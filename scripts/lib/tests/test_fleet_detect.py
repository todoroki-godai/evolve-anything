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

import pytest  # noqa: E402

import fleet.detect as fleet_detect  # noqa: E402
from fleet.detect import detect_all_projects, detect_exit_code  # noqa: E402
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


# ── 未帰属レコードの誤帰属（#312）──────────────────────────────────

def _deny_row(project: str | None = None, session: str = "sd") -> dict:
    row = {
        "type": "permission_denied", "tool_name": "Bash",
        "tool_input_summary": "git push", "denial_reason": "unknown",
        "timestamp": "2026-04-22T04:43:09.279230+00:00", "session_id": session,
    }
    if project is not None:
        row["project"] = project
    return row


def _write_errors(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return path


def _two_pj_fixture(tmp_path) -> tuple[Path, Path]:
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "updater", "amamo")
    _fake_repo(fs_root, "Users", "u", "updater", "docs-platform")
    projects = tmp_path / "projects"
    _write_transcript(projects / "-Users-u-updater-amamo", "s1.jsonl", _esc_events("sess-a"))
    _write_transcript(projects / "-Users-u-updater-docs-platform", "s1.jsonl",
                      _esc_events("sess-b"))
    return fs_root, projects


def test_unattributed_deny_is_not_attributed_to_any_project(tmp_path):
    """未帰属 deny を辞書順先頭の PJ に誤帰属させない（fan-out は strict 判定・#312）。

    ``record_project_match`` の寛容判定は単一 PJ 文脈では正しいが、全 PJ へ fan-out すると
    未帰属レコードが全 slug でマッチし、dedup 後に最初の slug（辞書順先頭）だけへ
    決定論的に誤帰属する。
    """
    fs_root, projects = _two_pj_fixture(tmp_path)
    errors = _write_errors(tmp_path / "errors.jsonl", [_deny_row(None)])

    store = tmp_path / "weak_signals.jsonl"
    res = detect_all_projects(
        projects_root=projects, store_path=store, errors_path=errors,
        fs_root=fs_root, utterances=[], progress=False)

    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    assert [r for r in rows if r["channel"] == "permission_deny"] == [], rows
    assert res["unattributed_deny"] == 1, "除外件数を surface する（silence != evaluated）"


def test_attributed_deny_is_still_detected(tmp_path):
    """帰属のある deny は従来どおり当該 PJ にだけ入る（strict 化で取りこぼさない）。"""
    fs_root, projects = _two_pj_fixture(tmp_path)
    errors = _write_errors(tmp_path / "errors.jsonl",
                           [_deny_row("amamo"), _deny_row(None)])

    store = tmp_path / "weak_signals.jsonl"
    res = detect_all_projects(
        projects_root=projects, store_path=store, errors_path=errors,
        fs_root=fs_root, utterances=[], progress=False)

    rows = [json.loads(l) for l in store.read_text().splitlines() if l.strip()]
    deny = [r for r in rows if r["channel"] == "permission_deny"]
    assert len(deny) == 1 and deny[0]["pj_slug"] == "amamo", rows
    assert res["unattributed_deny"] == 1


# ── 検出失敗の可視化と終了コード（#313）─────────────────────────────

def test_failed_dir_is_surfaced_and_not_counted_as_success(tmp_path, monkeypatch):
    """collect_signals が落ちた PJ を成功件数に加算せず、理由つきで結果に載せる。"""
    fs_root, projects = _two_pj_fixture(tmp_path)

    def _boom(slug, **kw):
        raise RuntimeError("permission denied on transcript")

    monkeypatch.setattr(fleet_detect, "collect_signals", _boom)
    res = detect_all_projects(
        projects_root=projects, store_path=tmp_path / "ws.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root, utterances=[],
        progress=False)

    assert res["projects"] == 0, "失敗した PJ を成功件数に数えない"
    assert sorted(f["pj_slug"] for f in res["failed_projects"]) == ["amamo", "docs-platform"]
    assert res["failed_dirs"], res
    assert "permission denied" in res["failed_dirs"][0]["error"]


def test_all_projects_failed_returns_nonzero_exit_code(tmp_path, monkeypatch):
    """全 PJ 失敗は非 zero（daily runner の沈黙モード再発を検知できるようにする）。"""
    fs_root, projects = _two_pj_fixture(tmp_path)
    monkeypatch.setattr(fleet_detect, "collect_signals",
                        lambda slug, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    res = detect_all_projects(
        projects_root=projects, store_path=tmp_path / "ws.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root, utterances=[],
        progress=False)
    assert detect_exit_code(res) != 0


def test_partial_failure_keeps_zero_exit_code(tmp_path, monkeypatch):
    """1 PJ の失敗では止めない（fail-open 方針は維持・観測だけ足す）。"""
    fs_root, projects = _two_pj_fixture(tmp_path)
    real = fleet_detect.collect_signals

    def _flaky(slug, **kw):
        if slug == "amamo":
            raise RuntimeError("x")
        return real(slug, **kw)

    monkeypatch.setattr(fleet_detect, "collect_signals", _flaky)
    res = detect_all_projects(
        projects_root=projects, store_path=tmp_path / "ws.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root, utterances=[],
        progress=False)

    assert res["projects"] == 1
    assert [f["pj_slug"] for f in res["failed_projects"]] == ["amamo"]
    assert detect_exit_code(res) == 0


def test_clean_run_has_empty_failure_fields(tmp_path):
    """成功時も失敗フィールドは必ず存在する（キー欠落で reader が壊れない）。"""
    fs_root, projects = _two_pj_fixture(tmp_path)
    res = detect_all_projects(
        projects_root=projects, store_path=tmp_path / "ws.jsonl",
        errors_path=tmp_path / "errors.jsonl", fs_root=fs_root, utterances=[],
        progress=False)
    assert res["failed_dirs"] == [] and res["failed_projects"] == []
    assert res["source_errors"] == []
    assert detect_exit_code(res) == 0


# ── max_transcripts の適用単位（#314）───────────────────────────────

def _multi_dir_fixture(tmp_path) -> tuple[Path, Path]:
    """1 slug に 2 dir（本体 + 撤去済み worktree）、各 2 transcript = 計 4 件。"""
    fs_root = tmp_path / "fs"
    _fake_repo(fs_root, "Users", "u", "utils", "rl-anything")
    projects = tmp_path / "projects"
    main = projects / "-Users-u-utils-rl-anything"
    wt = projects / "-Users-u-utils-rl-anything--claude-worktrees-wt"
    for i, d in enumerate((main, wt)):
        for j in range(2):
            _write_transcript(d, f"s{i}{j}.jsonl", _esc_events(f"sess-{i}{j}"))
    return fs_root, projects


def test_max_transcripts_is_per_project_not_per_dir(tmp_path):
    """上限は PJ 単位（slug 内の全 dir を合算してから適用）。

    dir ごとに適用すると実効上限が ``max_transcripts × dir 数`` になり、worktree を多く持つ
    PJ で daily の走査量が事実上無制限化する。
    """
    fs_root, projects = _multi_dir_fixture(tmp_path)
    kw = dict(projects_root=projects, errors_path=tmp_path / "errors.jsonl",
              fs_root=fs_root, utterances=[], progress=False)

    capped = detect_all_projects(store_path=tmp_path / "a.jsonl",
                                 max_transcripts=2, **kw)
    assert capped["total"] == 2, capped

    uncapped = detect_all_projects(store_path=tmp_path / "b.jsonl",
                                   max_transcripts=4, **kw)
    assert uncapped["total"] == 4, uncapped


def test_non_positive_max_transcripts_scans_nothing(tmp_path):
    """0 / 負数は「上限なし」でも ``files[:-1]`` でもなく走査ゼロに畳む（CLI では拒否）。"""
    fs_root, projects = _multi_dir_fixture(tmp_path)
    kw = dict(projects_root=projects, errors_path=tmp_path / "errors.jsonl",
              fs_root=fs_root, utterances=[], progress=False)
    assert detect_all_projects(store_path=tmp_path / "z.jsonl",
                               max_transcripts=0, **kw)["total"] == 0
    assert detect_all_projects(store_path=tmp_path / "n.jsonl",
                               max_transcripts=-1, **kw)["total"] == 0


# ── CLI 配線（#313 / #314）──────────────────────────────────────────

def test_cli_rejects_non_positive_max_transcripts():
    """--max-transcripts の 0 / 負数を argparse で拒否する。"""
    from fleet import cli as fcli

    for bad in ("0", "-1"):
        with pytest.raises(SystemExit) as excinfo:
            fcli.main(["detect", "--max-transcripts", bad])
        assert excinfo.value.code == 2


def _fake_detect_result(**over) -> dict:
    base = {
        "projects": 0, "written": 0, "skipped_dup": 0, "total": 0, "dry_run": False,
        "per_pj": [], "failed_dirs": [], "failed_projects": [], "source_errors": [],
        "unattributed_deny": 0,
    }
    base.update(over)
    return base


def test_cli_returns_nonzero_when_all_projects_failed(monkeypatch, capsys):
    from fleet import cli as fcli

    res = _fake_detect_result(
        failed_projects=[{"pj_slug": "amamo", "errors": ["RuntimeError: x"]}],
        failed_dirs=[{"pj_slug": "amamo", "dir": "d", "error": "RuntimeError: x"}],
    )
    monkeypatch.setattr(fleet_detect, "detect_all_projects", lambda **kw: res)
    assert fcli.main(["detect", "--quiet"]) != 0


def test_cli_summary_reports_failures_even_when_quiet(monkeypatch, capsys):
    """--quiet でも最終サマリに失敗・未帰属の件数を出す（daily ログの唯一の手がかり）。"""
    from fleet import cli as fcli

    res = _fake_detect_result(
        projects=1,
        failed_projects=[{"pj_slug": "amamo", "errors": ["RuntimeError: x"]}],
        failed_dirs=[{"pj_slug": "amamo", "dir": "d", "error": "RuntimeError: x"}],
        unattributed_deny=3,
    )
    monkeypatch.setattr(fleet_detect, "detect_all_projects", lambda **kw: res)
    fcli.main(["detect", "--quiet"])
    out = capsys.readouterr().out
    assert "失敗" in out and "amamo" in out
    assert "未帰属" in out
