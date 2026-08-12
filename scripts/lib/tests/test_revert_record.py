"""#402 PR-1: 採用パッチの revert 用「記録拡張」のユニットテスト。

設計は design_402_v6.md（codex round4 + tacchi round2 反映済み・確定版）。本ファイルは
PR-1 スコープ（emit→queue→drain の本文運搬 / entry フィールド / revert_schema_version /
emit 時 generation スナップショット / 決定8のロック規約 / 決定4の ID 互換規約）を検証する。
revert 本体（``bin/evolve-revert``・実際の復元・戦果ボード導線）は PR-2 で対象外。

すべて LLM-free・決定論。
"""
import base64
import hashlib
import json
import re
import subprocess
import sys
import textwrap
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
    b64 = ids.compress_before_content(text)
    assert ids.decompress_before_content(b64) == text


def test_compress_before_content_is_stdlib_decodable_without_project_code():
    """完了条件(a): CHANGELOG に書く手動 decode ワンライナーが実際に動くことの証拠。

    プロジェクトコードを import せず、標準ライブラリの zlib/base64 だけで
    decode できることを固定する（decode 手順の正しさの回帰防止）。
    """
    text = "# skill\n\n復旧できる。\n"
    b64 = ids.compress_before_content(text)
    restored = zlib.decompress(base64.b64decode(b64)).decode("utf-8")
    assert restored == text


def test_compress_before_for_revert_returns_body_under_cap():
    b64, reason = ids.compress_before_for_revert("short text")
    assert b64 is not None
    assert reason is None


def test_compress_before_for_revert_drops_body_over_cap():
    """決定2 Should3: 圧縮後サイズが上限を超えたら本文を落とし理由コードを返す。"""
    huge = "x" * (ids.REVERT_BEFORE_MAX_COMPRESSED_BYTES * 10)  # 高圧縮率でも上限超過させる
    b64, reason = ids.compress_before_for_revert(huge, max_bytes=16)
    assert b64 is None
    assert reason == ids.REVERT_REASON_BEFORE_TOO_LARGE


def test_compress_before_for_revert_compares_zlib_bytes_not_base64_length():
    """round2 codex レビュー Should: 比較は zlib 圧縮直後のバイト長（base64 化前）で行う。

    base64 は 4/3 倍に膨張するため、base64 化後の文字数で比較すると実効の zlib 上限が
    名目より縮む食い違いが生じていた（実測: `~/.claude/skills/*/SKILL.md` 106件中
    base64 後最大 63,272 bytes で当時の上限に対し余裕 3.5%）。``max_bytes`` を実際の
    zlib 圧縮バイト長ちょうどに設定すれば通過し（base64 化後の文字数は必ずそれより
    大きいため、旧実装ならここで誤って弾かれていた）、1 byte でも小さければ弾かれる
    ことを固定する。
    """
    text = "abcdefghij" * 2000  # base64 膨張で誤判定が発生しうる程度の量
    compressed_len = len(zlib.compress(text.encode("utf-8")))
    assert compressed_len < len(base64.b64encode(zlib.compress(text.encode("utf-8"))))

    b64_at_boundary, reason_at_boundary = ids.compress_before_for_revert(
        text, max_bytes=compressed_len
    )
    assert b64_at_boundary is not None
    assert reason_at_boundary is None

    b64_over, reason_over = ids.compress_before_for_revert(
        text, max_bytes=compressed_len - 1
    )
    assert b64_over is None
    assert reason_over == ids.REVERT_REASON_BEFORE_TOO_LARGE


# ─── 決定5: path 契約（project / global / fallback）─────────────────────────


def test_path_scope_identity_project_scope_in_git_repo(tmp_path):
    repo = tmp_path / "my-project"
    _init_repo(repo)
    skill = repo / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("x", encoding="utf-8")

    identity = ids.path_scope_identity(str(skill))

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

    identity = ids.path_scope_identity(str(global_skill))

    assert identity["scope"] == "global"
    assert identity["repo_id"] is None
    assert identity["relative_path"] == "my-skill/SKILL.md"
    assert identity["worktree_root"] is None
    assert identity["resolved_path"] == str(global_skill.resolve())


def test_path_scope_identity_global_scope_symlink_to_non_git_target(tmp_path, monkeypatch):
    """round2 codex レビュー Must: global root 配下の symlink（実体が git 管理外の別
    ディレクトリを指す。実環境 ``~/.claude/skills`` 配下に7件実在）を symlink 側の
    パスで ``scope="global"`` 判定する。

    ``resolve()`` 後の実体パスで判定すると、実体（この例では git 管理外の
    ``~/.agents/skills/agent-browser``）側の判定に落ちて ``scope=None``・
    ``relative_path`` に絶対パスが入る誤りが実測されていた。判定は symlink を辿らない
    字句的な絶対パスで行い、``resolved_path`` だけが symlink 実体を指す。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    target_dir = tmp_path / ".agents" / "skills" / "agent-browser"
    target_dir.mkdir(parents=True)
    target_skill = target_dir / "SKILL.md"
    target_skill.write_text("x", encoding="utf-8")

    link_dir = tmp_path / ".claude" / "skills" / "agent-browser"
    link_dir.parent.mkdir(parents=True, exist_ok=True)
    link_dir.symlink_to(target_dir, target_is_directory=True)

    identity = ids.path_scope_identity(str(link_dir / "SKILL.md"))

    assert identity["scope"] == "global"
    assert identity["repo_id"] is None
    assert identity["relative_path"] == "agent-browser/SKILL.md"
    assert identity["worktree_root"] is None
    assert identity["resolved_path"] == str(target_skill.resolve())


def test_path_scope_identity_rejects_lexical_dotdot_escape_from_global_root(tmp_path, monkeypatch):
    """``..`` で global root の外へ抜けようとする経路は、正規化後の絶対パスが root 配下に
    無ければ global 対象外として拒否する（symlink を辿らない字句的な正規化のみで判定）。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    outside = tmp_path / "outside" / "SKILL.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("x", encoding="utf-8")
    escaping_path = str(tmp_path / ".claude" / "skills" / ".." / ".." / "outside" / "SKILL.md")

    identity = ids.path_scope_identity(escaping_path)

    assert identity["scope"] != "global"


def test_path_scope_identity_falls_back_when_neither_project_nor_global(tmp_path):
    plain = tmp_path / "not-a-repo" / "SKILL.md"
    plain.parent.mkdir(parents=True)
    plain.write_text("x", encoding="utf-8")

    identity = ids.path_scope_identity(str(plain))

    assert identity["scope"] is None
    assert identity["repo_id"] is None
    assert identity["worktree_root"] is None


# ─── 決定4: revert_generation の取得契約 ────────────────────────────────────


def test_revert_generation_for_target_defaults_to_zero_without_revert_events():
    assert ids.revert_generation_for_target([], "project", "r1", "skills/x/SKILL.md") == 0


def test_revert_generation_for_target_reads_matching_revert_event():
    history = [
        {"event_type": "revert", "scope": "project", "repo_id": "r1",
         "relative_path": "skills/x/SKILL.md", "revert_generation": 2},
        {"event_type": "revert", "scope": "project", "repo_id": "r1",
         "relative_path": "skills/other/SKILL.md", "revert_generation": 9},
    ]
    assert ids.revert_generation_for_target(history, "project", "r1", "skills/x/SKILL.md") == 2


def test_revert_generation_for_target_ignores_non_revert_events():
    history = [{"event_type": "accept", "repo_id": "r1", "relative_path": "skills/x/SKILL.md"}]
    assert ids.revert_generation_for_target(history, "project", "r1", "skills/x/SKILL.md") == 0


# ─── 決定4 Must2: ID バージョン互換規約 ─────────────────────────────────────


def test_decision_event_id_generation_zero_is_bit_identical_to_legacy_call():
    """拡張前 pending（revert_generation 未設定）を拡張後コードで drain しても ID が変わらない。

    期待値は ``decision_event_id`` を呼ばず、拡張前の実装
    （``f"{proposal_id}_{kind}_{sha256(after)[:12]}"``）を標準ライブラリの hashlib だけで
    テスト内に独立して再現する（round2 codex レビュー Should: 関数を呼んで期待値を作ると
    将来 gen=0 の式ごと壊れてもテストが追従して緑のままになる循環を防ぐ）。
    """
    proposal_id, kind, after_content = "evdiff_x", "accept", "after content"
    digest12 = hashlib.sha256(after_content.encode("utf-8")).hexdigest()[:12]
    pre_extension_id = f"{proposal_id}_{kind}_{digest12}"

    assert ids.decision_event_id(proposal_id, kind, after_content) == pre_extension_id
    assert ids.decision_event_id(proposal_id, kind, after_content, 0) == pre_extension_id


def test_decision_event_id_generation_none_and_zero_are_equivalent_to_omission():
    """revert_generation=None（旧 entry の ``.get()`` フォールバック相当）も gen=0 と同じ扱い。"""
    proposal_id, kind, after_content = "evdiff_x", "accept", "after content"
    digest12 = hashlib.sha256(after_content.encode("utf-8")).hexdigest()[:12]
    pre_extension_id = f"{proposal_id}_{kind}_{digest12}"

    assert ids.decision_event_id(proposal_id, kind, after_content, 0) == pre_extension_id


def test_decision_event_id_generation_one_or_more_differs():
    base_id = ids.decision_event_id("evdiff_x", "accept", "after content", 0)
    gen1_id = ids.decision_event_id("evdiff_x", "accept", "after content", 1)
    gen2_id = ids.decision_event_id("evdiff_x", "accept", "after content", 2)
    assert len({base_id, gen1_id, gen2_id}) == 3


# ─── 決定8 round4: monotonic supersede ガード ───────────────────────────────


def test_filter_monotonic_pending_keeps_higher_or_equal_generation():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    kept, discarded = ids.filter_monotonic_pending(existing, pending)
    assert kept == pending
    assert discarded == 0


def test_filter_monotonic_pending_discards_lower_generation():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md", "revert_generation": 0}]
    kept, discarded = ids.filter_monotonic_pending(existing, pending)
    assert kept == []
    assert discarded == 1


def test_filter_monotonic_pending_unrelated_path_is_unaffected():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 5}]
    pending = [{"skill_path": "/b/SKILL.md", "revert_generation": 0}]
    kept, discarded = ids.filter_monotonic_pending(existing, pending)
    assert kept == pending
    assert discarded == 0


def test_filter_monotonic_pending_treats_missing_generation_as_zero():
    existing = [{"skill_path": "/a/SKILL.md", "revert_generation": 1}]
    pending = [{"skill_path": "/a/SKILL.md"}]  # revert_generation 未設定 = 0
    kept, discarded = ids.filter_monotonic_pending(existing, pending)
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


def test_ingest_accept_carries_after_sha_into_history(project_repo, monkeypatch, tmp_path):
    """#402 段階3: apply engine の3分岐判定（== after_sha / == before_sha / conflict）に
    ``after_sha`` が要る。PR-1 は ``before_sha``（decompress 可能）しか運ばず accept
    entry に after 内容の sha を永続化していなかった schema gap を埋める（段階3追加）。
    """
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
    after_content = "# my-skill\n\n改善。\n"
    skill.write_text(after_content, encoding="utf-8")

    hist = tmp_path / "hist.jsonl"
    ed.ingest_decisions("proj", accepted={pid}, dry_run=False, history_file=hist)

    recs = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1
    assert recs[0]["after_sha"] == ids.sha256(after_content)


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
    before_sha = ids.sha256(skill.read_text(encoding="utf-8"))
    pid = ids.proposal_id(str(skill), before_sha)
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
    # 期待値は decision_event_id を呼ばず、拡張前の実装を hashlib だけで独立に再現する
    # （round2 codex レビュー Should: 循環テスト防止）。
    digest12 = hashlib.sha256("# my-skill\n\n改善。\n".encode("utf-8")).hexdigest()[:12]
    expected_id = f"{pid}_accept_{digest12}"
    assert recs[0]["id"] == expected_id


def test_ingest_pre_extension_pending_redrain_with_existing_history_stays_one_row(
    project_repo, monkeypatch, tmp_path
):
    """決定4 Must2: 同一 ID を持つ既存 history（拡張前に記録済みの accept 行を模す）に
    対し、拡張後コードで旧 pending を再 drain しても記録は増えず1行のまま（冪等・
    #279 の N 重記録が version 境界で再発しない）。"""
    repo, skill = project_repo
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    before_sha = ids.sha256(skill.read_text(encoding="utf-8"))
    pid = ids.proposal_id(str(skill), before_sha)
    legacy_pending = [{
        "id": pid, "run_id": "evrun_legacy", "skill_name": "my-skill",
        "skill_path": str(skill), "before_sha": before_sha, "fitness_func": "skill_quality",
        "pattern": "p",
    }]
    after_content = "# my-skill\n\n改善。\n"
    skill.write_text(after_content, encoding="utf-8")
    digest12 = hashlib.sha256(after_content.encode("utf-8")).hexdigest()[:12]
    pre_extension_id = f"{pid}_accept_{digest12}"

    hist = tmp_path / "hist.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    # 拡張前に既に記録済みだった accept 行を模して既存 history に直接書く。
    hist.write_text(
        json.dumps({"id": pre_extension_id, "human_accepted": True}) + "\n", encoding="utf-8"
    )

    ed.ingest_decisions(
        "proj", accepted={pid}, dry_run=False, history_file=hist, pending=legacy_pending
    )

    recs = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 1  # 二重記録されない


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

    旧テストは「queue lock を持っている間 emit が完走しない」ことしか見ておらず、
    **emit が history lock を保持したまま queue lock を待つ退行**でも通ってしまって
    いた（round2 codex レビュー Should）。``load_history``（history lock 内で呼ばれる）
    通過を Event で観測し、その直後（＝emit は history lock を解放し queue lock 待ちに
    入っているはず）に**別 thread が history lock を取得できる**ことまで固定する。
    """
    from rl_common.file_lock import try_file_lock

    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }

    history_loaded = threading.Event()
    real_load_history = ohs.load_history

    def _spy_load_history(slug):
        out = real_load_history(slug)
        history_loaded.set()
        return out

    monkeypatch.setattr(ohs, "load_history", _spy_load_history)

    done = threading.Event()

    def _emit():
        ed.emit_decisions(result, dry_run=False, slug="proj")
        done.set()

    history_file = ohs.history_path("proj")
    with ed._queue_lock("proj"):
        thread = threading.Thread(target=_emit, daemon=True)
        thread.start()
        assert history_loaded.wait(10), "history 読み取り（generation 計算）が完了しなかった"
        assert not done.wait(1.0), "queue ロックを持っている間に emit が完走した（history 保持中のはず）"
        assert ed.read_queue("proj") == []
        # history 読み取り完了後は emit が history lock を解放しているはず（決定8: history
        # と queue/marker を同時保持しない）。別 thread が非 blocking で取得できれば証明できる。
        with try_file_lock(history_file.with_name(history_file.name + ".lock")) as acquired:
            assert acquired, (
                "history 読み取り完了後も emit が history lock を保持していた"
                "（history と queue の同時保持の疑い）"
            )

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
    before_sha = ids.sha256(skill.read_text(encoding="utf-8"))
    newer_entry = {
        "id": ids.proposal_id(str(skill), before_sha), "run_id": "evrun_newer",
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
    before_sha = ids.sha256(skill.read_text(encoding="utf-8"))
    newer_entry = {
        "id": ids.proposal_id(str(skill), before_sha), "run_id": "evrun_newer",
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


# ─── round2 codex レビュー Should: record_evolve_diff_decision の revert_fields 契約 ──


def _import_fitness_evolution():
    fe_dir = _LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"
    if str(fe_dir) not in sys.path:
        sys.path.insert(0, str(fe_dir))
    import fitness_evolution as fe

    return fe


def test_record_evolve_diff_decision_filters_non_allowlisted_revert_fields(tmp_path):
    """許可リスト外のキーは無視される（callee 自身が純加算契約を保証する）。"""
    fe = _import_fitness_evolution()
    hist = tmp_path / "hist.jsonl"

    entry = fe.record_evolve_diff_decision(
        skill_name="s",
        after_content="# s\n\n本文\n",
        diff_summary="d",
        human_accepted=True,
        history_file=hist,
        entry_id="rf1",
        revert_fields={"revert_before_b64": "abc", "not_allowed_key": "x"},
    )

    assert entry["revert_before_b64"] == "abc"
    assert "not_allowed_key" not in entry


def test_record_evolve_diff_decision_rejects_revert_fields_colliding_with_entry_keys(tmp_path):
    """許可リストのキーが既存 entry キーと衝突したら ValueError で拒否する（純加算契約
    違反の早期検知・round2 codex レビュー Should）。

    #402-D PR1 で許可リストフィルタ+衝突検査は ``evolve_decision_ids.merge_revert_fields``
    へ抽出された（3 writer 共有の単一ソース）ため、許可リストの monkeypatch 対象も
    ``evolve_decision_ids.REVERT_FIELD_KEYS``（``fe.REVERT_FIELD_KEYS`` ではない）に
    変わっている。
    """
    fe = _import_fitness_evolution()
    hist = tmp_path / "hist.jsonl"

    # REVERT_FIELD_KEYS のうち "scope" を既存 entry のキーであるかのように衝突させる
    # ため、entry 組立後に必ず存在するキー名 "id" を許可リストへ一時的に混ぜて検証する。
    import evolve_decision_ids as ids

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ids, "REVERT_FIELD_KEYS", ("id",))
        with pytest.raises(ValueError):
            fe.record_evolve_diff_decision(
                skill_name="s",
                after_content="# s\n\n本文\n",
                diff_summary="d",
                human_accepted=True,
                history_file=hist,
                entry_id="rf2",
                revert_fields={"id": "hijacked"},
            )
    # 参照だけ使い lint の未使用 import を避ける（許可リスト単一ソースであることの明示）。
    assert ids.REVERT_FIELD_KEYS


def test_record_evolve_diff_decision_omits_none_valued_revert_fields(tmp_path):
    fe = _import_fitness_evolution()
    hist = tmp_path / "hist.jsonl"

    entry = fe.record_evolve_diff_decision(
        skill_name="s",
        after_content="# s\n\n本文\n",
        diff_summary="d",
        human_accepted=True,
        history_file=hist,
        entry_id="rf3",
        revert_fields={"revert_before_b64": None, "revert_generation": 0},
    )

    assert "revert_before_b64" not in entry
    assert entry["revert_generation"] == 0


# ─── #402-D PR1 §5.3 完了条件①: byte-equivalent entry の契約テスト ───────────


def test_record_evolve_diff_decision_persisted_entry_is_byte_equivalent_after_refactor(
    tmp_path,
):
    """A writer（``record_evolve_diff_decision``）を共有 helper 経由へリファクタしても、
    disk へ永続化される entry のキー集合・値が変わらないことを固定する（#402-D PR1
    §5.3 PR1 完了条件①）。base フィールド + revert_fields 全種 + provenance の3層を
    1本のテストで束ねて、リファクタ前の手書きロジック（許可リストフィルタ+純加算+
    ロック下 dedup append）と等価な出力になることを確認する。
    """
    fe = _import_fitness_evolution()
    hist = tmp_path / "hist.jsonl"

    revert_fields = {
        "revert_before_b64": "b64content",
        "revert_schema_version": 1,
        "revert_encoding": "zlib+base64",
        "revert_generation": 2,
        "revert_unavailable_reason": None,  # None は書かれない
        "repo_id": "/repo",
        "relative_path": "skills/x/SKILL.md",
        "scope": "project",
        "worktree_root": "/repo",
        "resolved_path": "/repo/skills/x/SKILL.md",
        "after_sha": "deadbeef",
    }

    entry = fe.record_evolve_diff_decision(
        skill_name="s",
        after_content="# s\n\n本文\n",
        diff_summary="d",
        human_accepted=True,
        rejection_reason=None,
        history_file=hist,
        entry_id="byte-eq-1",
        run_id="run-1",
        decision_source="explicit_accept",
        revert_fields=revert_fields,
    )

    # base フィールド（純加算対象外）
    assert entry["id"] == "byte-eq-1"
    assert entry["source"] == fe.EVOLVE_DIFF_SOURCE
    assert entry["skill_name"] == "s"
    assert entry["diff_summary"] == "d"
    assert entry["fitness_func"] == fe.EVOLVE_DIFF_FITNESS_FUNC
    assert entry["human_accepted"] is True
    assert entry["rejection_reason"] is None
    assert entry["run_id"] == "run-1"
    assert entry["decision_source"] == "explicit_accept"
    assert isinstance(entry["timestamp"], str)
    assert isinstance(entry["best_fitness"], (float, type(None)))

    # revert フィールド（None 以外が純加算される）
    for k, v in revert_fields.items():
        if v is None:
            assert k not in entry
        else:
            assert entry[k] == v

    # provenance（決定論・deterministic kind）
    assert entry["provenance"]["evaluation_kind"] == "deterministic"

    # 実際にディスクへ永続化された1行と、戻り値の entry が完全一致する
    # （書込直前に何かが追加/欠落していないことの固定）。
    persisted = json.loads(hist.read_text(encoding="utf-8").splitlines()[0])
    assert persisted == entry


# ─── round2 codex レビュー Should: CHANGELOG の decode 導線が drift しないことを固定 ──

_CHANGELOG_PATH = _LIB.parent.parent / "CHANGELOG.md"


def _extract_changelog_dump_before_script() -> str:
    """CHANGELOG.md の完了条件(a) decode ワンライナー（`python3 -c "..."` 本体）を
    抽出する。フェンスドコードブロックはリスト継続の2スペースインデント付きなので
    dedent してから ``python3 -c "..."`` の中身だけ取り出す。
    """
    changelog = _CHANGELOG_PATH.read_text(encoding="utf-8")
    fence_match = re.search(
        r'```\n(\s*python3 -c "\n.*?\n\s*".*?)\n\s*```', changelog, re.DOTALL
    )
    assert fence_match, "CHANGELOG.md から decode スクリプトのコードブロックを抽出できなかった"
    block = textwrap.dedent(fence_match.group(1))
    script_match = re.search(r'python3 -c "\n(.*?)\n"', block, re.DOTALL)
    assert script_match, "python3 -c のスクリプト本体を抽出できなかった"
    return script_match.group(1)


def test_changelog_dump_before_recipe_restores_fixture_jsonl(tmp_path):
    """完了条件(a): CHANGELOG に明記した decode ワンライナーを実際に fixture jsonl に対して
    実行し、元テキストへ復元できることを固定する（文面 drift の検出・round2 codex
    レビュー Should）。"""
    script = _extract_changelog_dump_before_script()
    original_text = "# my-skill\n\n復旧できるはず。\n"
    jsonl_path = tmp_path / "optimize_history" / "proj.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    jsonl_path.write_text(
        json.dumps({"id": "evolve_diff_xyz", "revert_before_b64": ids.compress_before_content(original_text)})
        + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "restored_SKILL.md"

    subprocess.run(
        [sys.executable, "-c", script, str(jsonl_path), "evolve_diff_xyz", str(out_path)],
        check=True, capture_output=True, text=True,
    )

    assert out_path.read_text(encoding="utf-8") == original_text


# ══════════════════════════════════════════════════════════════════════════
# #402 PR-2 段階1（lock protocol）: design_402_pr2_v2.md §0 の契約テスト10本
# ══════════════════════════════════════════════════════════════════════════
#
# §0.1（read_only_file_lock の inode/内容不変・不在時の非作成・ENOTSUP/ENOLCK 例外・
# 割込み時の fd 解放）は rl_common/tests/test_file_lock.py（契約テスト1/2/8/9）で
# カバーする。ここでは emit の seqlock check-after（§0.2）と単調性異常検出（§0.3）を
# 統合レベルで固定する（契約テスト3/4/5/6/7/10）。


def test_dry_run_emit_writes_zero_bytes_except_marker(project_repo, monkeypatch, tmp_path):
    """契約テスト3: dry-run emit は対象ファイル/history lock sidecar/temp/history に
    書込ゼロ。pending marker だけ意図された dry-run 書込（#505→#513）として対象外。
    """
    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    history_dir = tmp_path / "optimize_history"
    monkeypatch.setattr(ohs, "HISTORY_ROOT", history_dir)
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    history_file = ohs.history_path("proj")
    lock_path = history_file.with_name(history_file.name + ".lock")
    skill_before = skill.read_bytes()
    assert not history_file.exists()
    assert not lock_path.exists()

    out = ed.emit_decisions(result, dry_run=True, slug="proj")

    assert out["count"] == 1
    assert skill.read_bytes() == skill_before  # 対象ファイル: 書込ゼロ
    assert not history_file.exists()  # history: 書込ゼロ
    assert not lock_path.exists()  # history lock sidecar: 書込ゼロ
    # temp（sibling tmp 等）も含めゼロ。history_dir 自体すら作られない想定。
    assert not history_dir.exists() or list(history_dir.rglob("*")) == []
    assert ed.marker_path("proj").exists()  # marker は対象外（意図された dry-run 書込）


def test_dry_run_emit_blocks_while_history_lock_sidecar_held(project_repo, monkeypatch, tmp_path):
    """契約テスト4: revert（想定）が sidecar を保持中は dry-run emit の disk/generation
    読みが進めない（ロック保持中に相手が進めないことの確認 + daemon thread で hang→fail
    変換・learning_concurrency_test_by_lock_holding）。
    """
    from rl_common.file_lock import file_lock

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
    lock_path = hist_file.with_name(hist_file.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("", encoding="utf-8")  # revert writer が保持する sidecar を実在させる

    done = threading.Event()

    def _emit():
        ed.emit_decisions(result, dry_run=True, slug="proj")
        done.set()

    with file_lock(lock_path):
        thread = threading.Thread(target=_emit, daemon=True)
        thread.start()
        assert not done.wait(1.0), "sidecar を保持している間に dry-run emit が完走した"

    assert done.wait(30), "ロック解放後も完走しなかった"


def test_dry_run_emit_discards_snapshot_when_sidecar_appears_mid_read(
    project_repo, monkeypatch, tmp_path
):
    """契約テスト5: sidecar 不在判定の直後に writer が作成・保持した場合（first-writer
    race）、check-after が暫定 snapshot を破棄して locked 経路で読み直す。
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
    history_file = ohs.history_path("proj")
    lock_path = history_file.with_name(history_file.name + ".lock")
    path_identity = ids.path_scope_identity(str(skill))

    fresh_history = [{
        "event_type": "revert",
        "scope": path_identity["scope"],
        "repo_id": path_identity["repo_id"],
        "relative_path": path_identity["relative_path"],
        "revert_generation": 3,
    }]

    calls = {"n": 0}

    def fake_load_history(slug):
        calls["n"] += 1
        if calls["n"] == 1:
            # 不在判定直後に writer が sidecar を作成・保持したのを模す。
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_path.write_text("", encoding="utf-8")
            return []  # 暫定 snapshot（破棄されるべき）
        return fresh_history

    monkeypatch.setattr(ohs, "load_history", fake_load_history)

    out = ed.emit_decisions(result, dry_run=True, slug="proj")

    assert calls["n"] == 2  # 1回破棄して読み直した
    assert out["pending"][0]["revert_generation"] == 3


def test_dry_run_emit_warns_without_short_circuiting_when_sidecar_missing_after_revert(
    project_repo, monkeypatch, tmp_path
):
    """契約テスト6: history に revert イベントがあるのに sidecar が外部要因で不在の状態
    でも generation=0 に短絡せず history の実 generation で続行し、警告 + 回復手順を
    surface する（fail させない。data dir 移送・バックアップ復元という良性シナリオで
    起き、dry-run は daily runner の無人経路でもあるため）。
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
    path_identity = ids.path_scope_identity(str(skill))
    history_with_revert = [{
        "event_type": "revert",
        "scope": path_identity["scope"],
        "repo_id": path_identity["repo_id"],
        "relative_path": path_identity["relative_path"],
        "revert_generation": 5,
    }]
    monkeypatch.setattr(ohs, "load_history", lambda slug: history_with_revert)

    history_file = ohs.history_path("proj")
    lock_path = history_file.with_name(history_file.name + ".lock")
    assert not lock_path.exists()  # 外部削除された状態を模す（作らない）

    out = ed.emit_decisions(result, dry_run=True, slug="proj")

    assert out["pending"][0]["revert_generation"] == 5  # 0 に短絡していない
    warning = out["dry_run_snapshot_warning"]
    assert warning is not None
    assert "revert" in warning
    assert "file_lock" in warning  # 回復手順（次の正規書込で sidecar が再作成される旨）


def test_dry_run_emit_raises_and_publishes_nothing_when_snapshot_retries_exhausted(
    project_repo, monkeypatch, tmp_path
):
    """§0.2 リトライ上限超過時の契約: emit 全体を失敗させ、新しい pending を
    queue/marker/result のいずれにも公開しない。既存 pending も変更しない。10本の契約
    テストには含まれないが、§0.2 末尾の契約（値そのものより公開しない順序が重要）を
    固定する補足テスト。
    """
    from contextlib import contextmanager

    import evolve_decisions._emit as _emit_mod

    repo, skill = project_repo
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    result = {
        "phases": {"discover": {"matched_skills": [
            {"matched_skill": "my-skill", "skill_path": str(skill), "pattern": "p"}
        ]}}
    }
    existing_entry = {"id": "pre_existing", "skill_path": str(skill), "revert_generation": 0}
    ed.write_pending_marker("proj", [existing_entry], run_id="evrun_pre")

    calls = {"n": 0}

    @contextmanager
    def always_reappearing_lock(target_lock_path):
        calls["n"] += 1
        yield False
        # check-after（lock_path.exists()）が常に「出現していた」を観測する状態を模す
        # （単調性違反 / path 不安定化のシミュレーション。本物の production writer は
        # こんな動きをしない — それが§0.3の契約）。
        target_lock_path.parent.mkdir(parents=True, exist_ok=True)
        target_lock_path.write_text("", encoding="utf-8")

    monkeypatch.setattr(_emit_mod, "read_only_file_lock", always_reappearing_lock)

    with pytest.raises(_emit_mod.EmitSnapshotRetriesExhausted):
        ed.emit_decisions(result, dry_run=True, slug="proj")

    assert calls["n"] == _emit_mod._DRY_RUN_SNAPSHOT_MAX_RETRIES

    marker = ed.read_pending_marker("proj")
    assert marker is not None
    assert [e["id"] for e in marker["pending"]] == ["pre_existing"]  # 既存 pending は不変
    assert ed.read_queue("proj") == []  # 新しい pending は queue にも公開されない


def test_record_evolve_diff_decision_creates_sidecar_via_file_lock_before_history_write(
    tmp_path, monkeypatch
):
    """契約テスト10: revert writer が必ず sidecar を作る経路（通常の file_lock）を通る
    ―― history へ書く前に sidecar が存在することを assert する。段階3 で revert writer
    本体が入るまでは、現時点で唯一 file_lock 経由で history に書く writer である
    ``record_evolve_diff_decision`` で検証する。

    #402-D PR1: 既存確認→append は
    ``optimize_history_store._append_history_entry_deduped_locked``（3 writer 共有）
    へ抽出された。この関数が内部で読む ``_read_jsonl`` をプローブして、file_lock 下で
    呼ばれる時点で既に sidecar が存在することを確認する（探索対象が
    ``fe.load_history`` から移った点以外は元の契約と同じ）。
    """
    fe = _import_fitness_evolution()
    hist = tmp_path / "history.jsonl"
    lock_path = hist.with_name(hist.name + ".lock")

    probe_result = {}
    real_read_jsonl = fe._history_store._read_jsonl

    def probe(path):
        # record_evolve_diff_decision は file_lock 下で
        # _append_history_entry_deduped_locked（既存 id 確認・#287-2）を呼んでから
        # append する。この時点で sidecar が既に存在すれば、sidecar 作成（file_lock の
        # read-modify-write open）が history への書込より前に起きている証拠になる。
        probe_result["sidecar_exists_before_history_write"] = lock_path.exists()
        return real_read_jsonl(path)

    monkeypatch.setattr(fe._history_store, "_read_jsonl", probe)

    fe.record_evolve_diff_decision(
        skill_name="s",
        after_content="# s\n\n本文\n",
        diff_summary="d",
        human_accepted=True,
        history_file=hist,
        entry_id="sidecar_order_check",
    )

    assert probe_result["sidecar_exists_before_history_write"] is True
    assert lock_path.exists()  # 通常の file_lock 経由で作成されたまま残る（削除しない）


def _iter_production_python_files():
    """production コード（tests/ 配下・test_*.py・conftest.py を除く）を列挙する。"""
    repo_root = _LIB.parent.parent
    for path in repo_root.rglob("*.py"):
        rel = path.relative_to(repo_root)
        parts = rel.parts
        if any(part.startswith(".") or part in ("__pycache__", "node_modules") for part in parts):
            continue
        if any(part == "tests" for part in parts):
            continue
        if rel.name.startswith("test_") or rel.name == "conftest.py":
            continue
        yield path


def test_no_production_code_unlinks_lock_path_sidecar():
    """契約テスト7: production コードに sidecar 削除経路が存在しないことの静的検査。

    §0.3 の単調性契約（sidecar は一度作られたら削除されない）は、production コードが
    lock sidecar パス（このリポジトリの規約で変数名 ``lock_path``。
    ``<target>.with_name(<target>.name + ".lock")`` で構築 — ``_emit.py`` /
    ``judge_runner.py`` / ``restore_state.py`` と揃える）に対して ``unlink()`` /
    ``shutil.rmtree()`` を呼ぶ箇所が無いことで固定する。本文で「unlink→再作成は対応
    保証外」と非対応を決めているので、テスト名も「検出 or 非対応化」の二択にしない
    （検出したらこの test を赤くする）。
    """
    import ast

    offenders = []
    for path in _iter_production_python_files():
        text = path.read_text(encoding="utf-8")
        if "lock_path" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "unlink":
                if isinstance(func.value, ast.Name) and func.value.id == "lock_path":
                    offenders.append(f"{path}:{node.lineno} lock_path.unlink(...)")
                continue
            target_name = None
            if isinstance(func, ast.Attribute) and func.attr == "rmtree":
                target_name = "rmtree"
            elif isinstance(func, ast.Name) and func.id == "rmtree":
                target_name = "rmtree"
            if target_name:
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id == "lock_path":
                        offenders.append(f"{path}:{node.lineno} rmtree(lock_path)")

    assert offenders == [], f"lock_path sidecar を削除しうる production コード: {offenders}"
