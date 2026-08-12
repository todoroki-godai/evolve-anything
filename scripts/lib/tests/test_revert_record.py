"""#402 PR-1: 採用パッチの revert 用「記録拡張」のユニットテスト。

設計は design_402_v6.md（codex round4 + tacchi round2 反映済み・確定版）。本ファイルは
PR-1 スコープ（emit→queue→drain の本文運搬 / entry フィールド / revert_schema_version /
emit 時 generation スナップショット / 決定8のロック規約 / 決定4の ID 互換規約）を検証する。
revert 本体（``bin/evolve-revert``・実際の復元・戦果ボード導線）は PR-2 で対象外。

すべて LLM-free・決定論。
"""
import base64
import json
import subprocess
import sys
import threading
import zlib
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decision_ids as ids  # noqa: E402
import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


# ─── 決定1/2: before 全文の圧縮（zlib+base64）───────────────────────────────


def test_compress_before_content_round_trips():
    text = "# my-skill\n\n日本語を含む本文。\n"
    b64 = ids._compress_before_content(text)
    assert ids._decompress_before_content(b64) == text


def test_compress_before_content_is_stdlib_decodable_without_project_code():
    """完了条件(a): CHANGELOG に書く手動 decode ワンライナーが実際に動くことの証拠。

    プロジェクトコードを import せず、標準ライブラリの zlib/base64 だけで
    decode できることを固定する（decode 手順の正しさの回帰防止）。
    """
    text = "# skill\n\n復旧できる。\n"
    b64 = ids._compress_before_content(text)
    restored = zlib.decompress(base64.b64decode(b64)).decode("utf-8")
    assert restored == text


def test_compress_before_for_revert_returns_body_under_cap():
    b64, reason = ids._compress_before_for_revert("short text")
    assert b64 is not None
    assert reason is None


def test_compress_before_for_revert_drops_body_over_cap():
    """決定2 Should3: 圧縮後サイズが上限を超えたら本文を落とし理由コードを返す。"""
    huge = "x" * (ids.REVERT_BEFORE_MAX_COMPRESSED_BYTES * 10)  # 高圧縮率でも上限超過させる
    b64, reason = ids._compress_before_for_revert(huge, max_bytes=16)
    assert b64 is None
    assert reason == ids.REVERT_REASON_BEFORE_TOO_LARGE


# ─── 決定5: path 契約（project / global / fallback）─────────────────────────


def test_path_scope_identity_project_scope_in_git_repo(tmp_path):
    repo = tmp_path / "my-project"
    _init_repo(repo)
    skill = repo / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("x", encoding="utf-8")

    identity = ids._path_scope_identity(str(skill))

    assert identity["scope"] == "project"
    assert identity["relative_path"] == "skills/my-skill/SKILL.md"
    assert identity["worktree_root"] == str(repo.resolve())
    assert identity["repo_id"]
    assert identity["resolved_path"] == str(skill.resolve())


def test_path_scope_identity_global_scope_under_claude_skills(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    global_skill = tmp_path / ".claude" / "skills" / "my-skill" / "SKILL.md"
    global_skill.parent.mkdir(parents=True)
    global_skill.write_text("x", encoding="utf-8")

    identity = ids._path_scope_identity(str(global_skill))

    assert identity["scope"] == "global"
    assert identity["repo_id"] is None
    assert identity["relative_path"] == "my-skill/SKILL.md"
    assert identity["worktree_root"] is None
    assert identity["resolved_path"] == str(global_skill.resolve())


def test_path_scope_identity_falls_back_when_neither_project_nor_global(tmp_path):
    plain = tmp_path / "not-a-repo" / "SKILL.md"
    plain.parent.mkdir(parents=True)
    plain.write_text("x", encoding="utf-8")

    identity = ids._path_scope_identity(str(plain))

    assert identity["scope"] is None
    assert identity["repo_id"] is None
    assert identity["worktree_root"] is None


# ─── 決定4: revert_generation の取得契約 ────────────────────────────────────


def test_revert_generation_for_target_defaults_to_zero_without_revert_events():
    assert ids._revert_generation_for_target([], "project", "r1", "skills/x/SKILL.md") == 0


def test_revert_generation_for_target_reads_matching_revert_event():
    history = [
        {"event_type": "revert", "scope": "project", "repo_id": "r1",
         "relative_path": "skills/x/SKILL.md", "revert_generation": 2},
        {"event_type": "revert", "scope": "project", "repo_id": "r1",
         "relative_path": "skills/other/SKILL.md", "revert_generation": 9},
    ]
    assert ids._revert_generation_for_target(history, "project", "r1", "skills/x/SKILL.md") == 2


def test_revert_generation_for_target_ignores_non_revert_events():
    history = [{"event_type": "accept", "repo_id": "r1", "relative_path": "skills/x/SKILL.md"}]
    assert ids._revert_generation_for_target(history, "project", "r1", "skills/x/SKILL.md") == 0


# ─── 決定4 Must2: ID バージョン互換規約 ─────────────────────────────────────


def test_decision_event_id_generation_zero_is_bit_identical_to_legacy_call():
    """拡張前 pending（revert_generation 未設定）を拡張後コードで drain しても ID が変わらない。"""
    legacy_id = ids._decision_event_id("evdiff_x", "accept", "after content")
    extended_default = ids._decision_event_id("evdiff_x", "accept", "after content", 0)
    assert legacy_id == extended_default


def test_decision_event_id_generation_one_or_more_differs():
    base_id = ids._decision_event_id("evdiff_x", "accept", "after content", 0)
    gen1_id = ids._decision_event_id("evdiff_x", "accept", "after content", 1)
    gen2_id = ids._decision_event_id("evdiff_x", "accept", "after content", 2)
    assert len({base_id, gen1_id, gen2_id}) == 3


# ─── 決定8 round4: monotonic supersede ガード ───────────────────────────────


def test_filter_monotonic_pending_keeps_higher_or_equal_generation():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    kept, discarded = ids._filter_monotonic_pending(existing, pending)
    assert kept == pending
    assert discarded == 0


def test_filter_monotonic_pending_discards_lower_generation():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md", "revert_generation": 0}]
    kept, discarded = ids._filter_monotonic_pending(existing, pending)
    assert kept == []
    assert discarded == 1


def test_filter_monotonic_pending_unrelated_path_is_unaffected():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 5}]
    pending = [{"skill_path": "/b/SKILL.md", "revert_generation": 0}]
    kept, discarded = ids._filter_monotonic_pending(existing, pending)
    assert kept == pending
    assert discarded == 0


def test_filter_monotonic_pending_treats_missing_generation_as_zero():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md"}]  # revert_generation 未設定 = 0
    kept, discarded = ids._filter_monotonic_pending(existing, pending)
    assert kept == []
    assert discarded == 1


# ══════════════════════════════════════════════════════════════════════════
# emit_decisions 統合: entry フィールドの純加算
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def project_repo(tmp_path):
    repo = tmp_path / "proj"
    _init_repo(repo)
    skill_dir = repo / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    skill = skill_dir / "SKILL.md"
    skill.write_text("# my-skill\n\n旧内容。\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add skill")
    return repo, skill


def test_emit_attaches_revert_fields_for_project_scope(project_repo, monkeypatch, tmp_path):
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }

    out = ed.emit_decisions(result, dry_run=False, slug="proj")

    entry = out["pending"][0]
    assert entry["revert_schema_version"] == ids.REVERT_SCHEMA_VERSION
    assert entry["revert_encoding"] == ids.REVERT_ENCODING
    assert entry["revert_generation"] == 0  # revert 未経験
    assert entry["scope"] == "project"
    assert entry["repo_id"]
    assert entry["relative_path"] == "skills/my-skill/SKILL.md"
    assert entry["resolved_path"] == str(skill.resolve())
    assert zlib.decompress(base64.b64decode(entry["revert_before_b64"])).decode("utf-8") == (
        "# my-skill\n\n旧内容。\n"
    )
    # queue にもマーカーにも同じフィールドが乗る
    queued = ed.read_queue("proj")[0]
    assert queued["revert_before_b64"] == entry["revert_before_b64"]
    marker = ed.read_pending_marker("proj")
    assert marker["pending"][0]["revert_before_b64"] == entry["revert_before_b64"]


def test_emit_dry_run_result_pending_also_carries_revert_fields(project_repo, monkeypatch, tmp_path):
    """決定2: --dry-run 経路の result 同梱 pending にも同じフィールドを載せる。"""
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }

    out = ed.emit_decisions(result, dry_run=True, slug="proj")

    assert ed.read_queue("proj") == []  # dry-run はストアに書かない（契約は不変）
    entry = out["pending"][0]
    assert entry["revert_before_b64"]
    assert entry["revert_schema_version"] == ids.REVERT_SCHEMA_VERSION


def test_emit_oversized_before_drops_body_with_reason(project_repo, monkeypatch, tmp_path):
    repo, skill = project_repo
    skill.write_text("x" * 100, encoding="utf-8")
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ids, "REVERT_BEFORE_MAX_COMPRESSED_BYTES", 8)
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }

    out = ed.emit_decisions(result, dry_run=False, slug="proj")

    entry = out["pending"][0]
    assert entry["revert_before_b64"] is None
    assert entry["revert_unavailable_reason"] == ids.REVERT_REASON_BEFORE_TOO_LARGE


# ─── accept entry への revert フィールド伝播（drain の本文運搬）─────────────


def test_ingest_accept_carries_revert_fields_into_history(project_repo, monkeypatch, tmp_path):
    """決定2: 恒久保存は accept された entry のみ。drain で queue→optimize_history へ運ぶ。"""
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    out = ed.emit_decisions(result, dry_run=False, slug="proj")
    pid = out["pending"][0]["id"]
    skill.write_text("# my-skill\n\n改善。\n", encoding="utf-8")

    hist = tmp_path / "hist.jsonl"
    ed.ingest_decisions("proj", accepted={pid}, dry_run=False, history_file=hist)

    recs = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1
    assert recs[0]["human_accepted"] is True
    assert recs[0]["revert_before_b64"]
    assert recs[0]["revert_schema_version"] == ids.REVERT_SCHEMA_VERSION
    assert recs[0]["scope"] == "project"
    assert recs[0]["relative_path"] == "skills/my-skill/SKILL.md"
    assert zlib.decompress(base64.b64decode(recs[0]["revert_before_b64"])).decode("utf-8") == (
        "# my-skill\n\n旧内容。\n"
    )


def test_ingest_reject_does_not_carry_revert_body(project_repo, monkeypatch, tmp_path):
    """決定2: reject/skip の本文は queue purge とともに捨てる（恒久保存しない）。"""
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    out = ed.emit_decisions(result, dry_run=False, slug="proj")
    pid = out["pending"][0]["id"]

    hist = tmp_path / "hist.jsonl"
    ed.ingest_decisions("proj", rejected={pid: "不一致"}, dry_run=False, history_file=hist)

    recs = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1
    assert recs[0]["human_accepted"] is False
    assert "revert_before_b64" not in recs[0] or recs[0]["revert_before_b64"] is None


def test_ingest_pre_extension_pending_produces_bit_identical_id(project_repo, monkeypatch, tmp_path):
    """決定4 Must2 の契約テスト: revert_generation を持たない旧 pending を新コードで drain
    しても ID が拡張前と bit 一致する（#279 の N 重記録が version 境界で再発しない）。"""
    repo, skill = project_repo
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    before_sha = ids._sha256(skill.read_text(encoding="utf-8"))
    pid = ids._proposal_id(str(skill), before_sha)
    # revert_generation キーを持たない旧 pending（拡張前に emit された marker residue を模す）。
    legacy_pending = [{
        "id": pid, "run_id": "evrun_legacy", "skill_name": "my-skill",
        "skill_path": str(skill), "before_sha": before_sha, "fitness_func": "skill_quality",
        "pattern": "p",
    }]
    skill.write_text("# my-skill\n\n改善。\n", encoding="utf-8")

    hist = tmp_path / "hist.jsonl"
    ed.ingest_decisions(
        "proj", accepted={pid}, dry_run=False, history_file=hist, pending=legacy_pending
    )

    recs = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1
    expected_id = ids._decision_event_id(pid, "accept", "# my-skill\n\n改善。\n")
    assert recs[0]["id"] == expected_id


# ─── 決定8: emit の history lock 規約 ───────────────────────────────────────


def _no_hang(fn, seconds: float = 30):
    box = {}
    thread = threading.Thread(target=lambda: box.update(value=fn()), daemon=True)
    thread.start()
    thread.join(timeout=seconds)
    assert not thread.is_alive(), f"{seconds}s 以内に完了しなかった（ロック待ちの疑い）"
    return box["value"]


def test_emit_holds_history_lock_while_reading_disk_and_generation(
    project_repo, monkeypatch, tmp_path
):
    """決定8: emit は同じ history lock 内で disk 内容読み + generation 読みを行う。

    history lock を外から保持している間は emit の候補構築が進まないことを固定する
    （ロック保持中に相手が進めないことの確認 + daemon thread で hang→fail 変換）。
    """
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    hist_file = ohs.history_path("proj")
    from rl_common.file_lock import file_lock

    done = threading.Event()

    def _emit():
        ed.emit_decisions(result, dry_run=False, slug="proj")
        done.set()

    with file_lock(hist_file.with_name(hist_file.name + ".lock")):
        thread = threading.Thread(target=_emit, daemon=True)
        thread.start()
        assert not done.wait(1.0), "history ロックを持っている間に emit が完走した"
        assert ed.read_queue("proj") == []

    assert done.wait(30), "ロック解放後も完走しなかった"
    assert len(ed.read_queue("proj")) == 1


def test_emit_does_not_hold_history_and_queue_locks_simultaneously(
    project_repo, monkeypatch, tmp_path
):
    """決定8: emit は history と queue を同時保持しない（deadlock 回避の契約）。

    queue ロックを外から保持していても、history 読み取り自体は進む（queue 書込の
    段になって初めてブロックされる）ことを確認する。
    """
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    done = threading.Event()

    def _emit():
        ed.emit_decisions(result, dry_run=False, slug="proj")
        done.set()

    with ed._queue_lock("proj"):
        thread = threading.Thread(target=_emit, daemon=True)
        thread.start()
        assert not done.wait(1.0), "queue ロックを持っている間に emit が完走した（history 保持中のはず）"
        assert ed.read_queue("proj") == []

    assert done.wait(30), "ロック解放後も完走しなかった"
    assert len(ed.read_queue("proj")) == 1


# ─── 決定8 round4: monotonic supersede ガードの queue/marker 統合 ───────────


def test_emit_queue_write_does_not_let_stale_generation_supersede_newer(
    project_repo, monkeypatch, tmp_path
):
    """decision8 round4 の実例: 既に世代1が公開済みの queue に、世代0（未設定=0）の
    新規 pending が遅れて来ても supersede させない。"""
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    before_sha = ids._sha256(skill.read_text(encoding="utf-8"))
    newer_entry = {
        "id": ids._proposal_id(str(skill), before_sha), "run_id": "evrun_newer",
        "skill_name": "my-skill", "skill_path": str(skill), "before_sha": before_sha,
        "fitness_func": "skill_quality", "pattern": "p", "revert_generation": 1,
    }
    ed._write_queue("proj", [newer_entry])

    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    out = ed.emit_decisions(result, dry_run=False, slug="proj")

    queued = ed.read_queue("proj")
    assert len(queued) == 1
    assert queued[0]["run_id"] == "evrun_newer"  # 世代1が生き残る
    assert queued[0]["revert_generation"] == 1
    assert out["revert_generation_discarded"] >= 1


def test_emit_marker_write_does_not_let_stale_generation_supersede_newer(
    project_repo, monkeypatch, tmp_path
):
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    before_sha = ids._sha256(skill.read_text(encoding="utf-8"))
    newer_entry = {
        "id": ids._proposal_id(str(skill), before_sha), "run_id": "evrun_newer",
        "skill_name": "my-skill", "skill_path": str(skill), "before_sha": before_sha,
        "fitness_func": "skill_quality", "pattern": "p", "revert_generation": 1,
    }
    ed.write_pending_marker("proj", [newer_entry], run_id="evrun_newer")

    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    ed.emit_decisions(result, dry_run=False, slug="proj")

    marker = ed.read_pending_marker("proj")
    assert marker is not None
    assert [run["run_id"] for run in marker["runs"]] == ["evrun_newer"]
    assert marker["pending"][0]["revert_generation"] == 1


def test_write_pending_marker_returns_discarded_count(project_repo, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    higher = {"id": "a", "skill_path": "/a/SKILL.md", "revert_generation": 3}
    ed.write_pending_marker("proj", [higher], run_id="r1")

    lower = {"id": "b", "skill_path": "/a/SKILL.md", "revert_generation": 0}
    discarded = ed.write_pending_marker("proj", [lower], run_id="r2")

    assert discarded == 1
    marker = ed.read_pending_marker("proj")
    assert [run["run_id"] for run in marker["runs"]] == ["r1"]
