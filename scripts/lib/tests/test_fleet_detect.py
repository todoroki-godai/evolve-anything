"""fleet.detect: 学習素材の検出を evolve 実行から切り離す（#304）。

根因（#304）: 決定論 weak_signals の永続化は ``evolve --drain`` の apply 境界にしか配線されて
いない（#484/#513）。daily runner は ``ingest`` → ``tokens`` → ``queue`` の3ステップのみで検出を
含まないため、evolve を回さない限り素材が 1 件も生まれず ``fleet queue`` は永久に
``queue_status=EMPTY`` を返す（鶏卵ループ）。一度も evolve していない PJ は素材が作られる機会
自体がなく queue から永久に発見されない。

``detect_all_projects`` は ``~/.claude/projects`` の実 transcript dir を母集団として全 PJ 分の
決定論検出を回す（ゼロ LLM・冪等）。slug は read 側（queue）と同じ ``resolve_pj_slug`` 系で
解決し、worktree transcript も本体 repo の slug に寄せる。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet.detect import detect_all_projects  # noqa: E402
from weak_signals.store import existing_signal_keys  # noqa: E402


def _write_transcript(pj_dir: Path, name: str, events: list[dict]) -> Path:
    pj_dir.mkdir(parents=True, exist_ok=True)
    p = pj_dir / name
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                 encoding="utf-8")
    return p


def _esc_events(session_id: str) -> list[dict]:
    """Esc 中断シグナル（content-poor だが決定論チャネルの1つ）を含む最小 transcript。"""
    return [
        {"type": "user", "sessionId": session_id,
         "message": {"role": "user", "content": [{"type": "text", "text": "実装して"}]}},
        {"type": "user", "sessionId": session_id,
         "message": {"role": "user", "content": [
             {"type": "text", "text": "[Request interrupted by user for tool use]"}]}},
    ]


def _fake_repo(root: Path, *parts: str) -> Path:
    """実在 dir を作る（pj_id → 実 path の貪欲復元が効くようにする）。"""
    p = root.joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_detects_and_persists_for_all_projects(tmp_path, monkeypatch):
    """evolve を一度も回していない PJ でも、検出が走り weak_signals が永続化される。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "updater-index")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-updater-index", "s1.jsonl",
                      _esc_events("sess-1"))

    store = tmp_path / "weak_signals.jsonl"
    res = detect_all_projects(
        projects_root=projects,
        store_path=store,
        errors_path=tmp_path / "errors.jsonl",   # 不在 = permission_deny なし
        fs_root=fs_root,
        utterances=[],                           # rephrase なし（DB 非依存）
        progress=False,
    )

    assert res["projects"] == 1
    assert res["written"] >= 1, res
    assert store.exists(), "非 dry-run では weak_signals が永続化されること"
    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert {r["pj_slug"] for r in rows} == {"updater-index"}, rows


def test_dry_run_writes_nothing(tmp_path):
    """dry-run はストアに一切触れない（pitfall_dryrun_stateful_store_write 準拠）。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "amamo")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-amamo", "s1.jsonl", _esc_events("sess-2"))

    store = tmp_path / "weak_signals.jsonl"
    res = detect_all_projects(
        projects_root=projects,
        store_path=store,
        errors_path=tmp_path / "errors.jsonl",
        fs_root=fs_root,
        utterances=[],
        dry_run=True,
        progress=False,
    )

    assert res["dry_run"] is True
    assert res["written"] >= 1, "書くはずだった件数は観測できること"
    assert not store.exists(), "dry-run はファイルを作らないこと"


def test_idempotent_second_run_writes_nothing_new(tmp_path):
    """同じ transcript を2回検出しても signal_key dedup で二重記録されない。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "amamo")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-amamo", "s1.jsonl", _esc_events("sess-3"))

    store = tmp_path / "weak_signals.jsonl"
    kw = dict(projects_root=projects, store_path=store,
              errors_path=tmp_path / "errors.jsonl", fs_root=fs_root,
              utterances=[], progress=False)

    first = detect_all_projects(**kw)
    keys_after_first = existing_signal_keys(store)
    second = detect_all_projects(**kw)

    assert first["written"] >= 1
    assert second["written"] == 0, "2回目は新規ゼロ"
    assert second["skipped_dup"] >= 1
    assert existing_signal_keys(store) == keys_after_first


def test_worktree_transcripts_attribute_to_main_repo_slug(tmp_path):
    """worktree の transcript も本体 repo の slug に寄せる（read 側 queue と名前空間を揃える）。

    worktree dir 名をそのまま slug にすると ``daily-report`` 等の幻 slug ができ、queue が
    当該 PJ の素材として数えられない（pitfall_worktree_slug_show_toplevel と同型）。
    """
    import subprocess

    fs_root = tmp_path / "fs"
    repo = _fake_repo(fs_root, "Users", "u", "utils", "evolve-anything")
    # 実 git repo + 実 worktree で検証する（resolve_pj_slug は git-common-dir を引くため、
    # .git を手で捏造した合成 fixture では本番の解決経路を通らない = false confidence）。
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "HOME": str(tmp_path), "PATH": "/usr/bin:/bin:/usr/local/bin"}
    def _git(*args, cwd=repo):
        subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                       capture_output=True)
    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "t")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "README.md")
    _git("commit", "-qm", "init")
    _git("worktree", "add", "-q", ".claude-worktrees/daily-report", "-b", "wt")
    wt = repo / ".claude-worktrees" / "daily-report"
    assert wt.is_dir()

    projects = tmp_path / "projects"
    _write_transcript(
        projects / "-Users-u-utils-evolve-anything--claude-worktrees-daily-report",
        "s1.jsonl", _esc_events("sess-4"))

    store = tmp_path / "weak_signals.jsonl"
    detect_all_projects(
        projects_root=projects, store_path=store,
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root,
        utterances=[], progress=False)

    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert rows, "worktree transcript も検出対象であること"
    assert {r["pj_slug"] for r in rows} == {"evolve-anything"}, rows


def test_removed_worktree_falls_back_to_main_repo_slug(tmp_path):
    """撤去済み worktree の transcript も本体 repo の slug に寄せる（幻 slug を作らない）。

    worktree dir が既に消えていると実パス復元が効かず、dir 名そのままだと
    ``rl-anything--claude-worktrees-feedback`` のような当 PJ 以外の幻 slug が queue に出る。
    """
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "utils", "rl-anything")   # 本体だけ実在
    projects = tmp_path / "projects"
    _write_transcript(
        projects / "-Users-u-utils-rl-anything--claude-worktrees-feedback",
        "s1.jsonl", _esc_events("sess-8"))

    store = tmp_path / "weak_signals.jsonl"
    detect_all_projects(
        projects_root=projects, store_path=store,
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root,
        utterances=[], progress=False)

    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert {r["pj_slug"] for r in rows} == {"rl-anything"}, rows


def test_dry_run_count_matches_actual_write_with_multiple_dirs(tmp_path):
    """1 slug に複数 dir（本体 + worktree）があっても dry-run 件数が実書込と一致する。

    dir ごとに append すると dry-run は未書込ストアと突合するため同一シグナルを dir 数ぶん
    重複計上し、「803 件書く予定 → 実際は 531 件」と観測が嘘をつく。
    """
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "utils", "rl-anything")
    projects = tmp_path / "projects"
    # 本体 + 撤去済み worktree（どちらも同じ slug に畳まれる）
    _write_transcript(projects / "-Users-u-utils-rl-anything", "s1.jsonl",
                      _esc_events("sess-9"))
    _write_transcript(projects / "-Users-u-utils-rl-anything--claude-worktrees-wt",
                      "s2.jsonl", _esc_events("sess-10"))

    kw = dict(projects_root=projects, errors_path=tmp_path / "errors.jsonl",
              fs_root=fs_root, utterances=[], progress=False)
    planned = detect_all_projects(store_path=tmp_path / "a.jsonl", dry_run=True, **kw)
    actual = detect_all_projects(store_path=tmp_path / "b.jsonl", **kw)

    assert planned["written"] == actual["written"], (planned, actual)


def test_only_filter_limits_to_one_project(tmp_path):
    """--pj で対象 PJ を絞れる（1 PJ だけ再検出したいとき）。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "amamo")
    _fake_repo(fs_root, "Users", "u", "updater", "docs-platform")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-amamo", "s1.jsonl", _esc_events("sess-5"))
    _write_transcript(projects / "-Users-u-updater-docs-platform", "s1.jsonl",
                      _esc_events("sess-6"))

    store = tmp_path / "weak_signals.jsonl"
    res = detect_all_projects(
        projects_root=projects, store_path=store,
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root,
        utterances=[], only=["amamo"], progress=False)

    assert res["projects"] == 1
    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert {r["pj_slug"] for r in rows} == {"amamo"}


def test_per_pj_breakdown_is_returned(tmp_path):
    """PJ 別の内訳を返す（CLI 表示と daily ログの単一ソース）。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "amamo")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-amamo", "s1.jsonl", _esc_events("sess-7"))

    res = detect_all_projects(
        projects_root=projects, store_path=tmp_path / "weak_signals.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root,
        utterances=[], progress=False)

    assert isinstance(res["per_pj"], list)
    entry = next(e for e in res["per_pj"] if e["pj_slug"] == "amamo")
    assert entry["written"] >= 1
    assert isinstance(entry["detected"], dict)


def test_empty_projects_root_is_noop(tmp_path):
    """transcript が無い環境でも例外を投げず 0 件で返る（daily runner の fail-open 前提）。"""
    res = detect_all_projects(
        projects_root=tmp_path / "nonexistent", store_path=tmp_path / "ws.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=tmp_path, utterances=[],
        progress=False)
    assert res["projects"] == 0
    assert res["written"] == 0
