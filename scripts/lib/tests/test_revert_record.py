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

    b64_at_boundary, reason_at_boundary = ids._compress_before_for_revert(
        text, max_bytes=compressed_len
    )
    assert b64_at_boundary is not None
    assert reason_at_boundary is None

    b64_over, reason_over = ids._compress_before_for_revert(
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

    identity = ids._path_scope_identity(str(link_dir / "SKILL.md"))

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

    identity = ids._path_scope_identity(escaping_path)

    assert identity["scope"] != "global"


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
    """拡張前 pending（revert_generation 未設定）を拡張後コードで drain しても ID が変わらない。

    期待値は ``_decision_event_id`` を呼ばず、拡張前の実装
    （``f"{proposal_id}_{kind}_{sha256(after)[:12]}"``）を標準ライブラリの hashlib だけで
    テスト内に独立して再現する（round2 codex レビュー Should: 関数を呼んで期待値を作ると
    将来 gen=0 の式ごと壊れてもテストが追従して緑のままになる循環を防ぐ）。
    """
    proposal_id, kind, after_content = "evdiff_x", "accept", "after content"
    digest12 = hashlib.sha256(after_content.encode("utf-8")).hexdigest()[:12]
    pre_extension_id = f"{proposal_id}_{kind}_{digest12}"

    assert ids._decision_event_id(proposal_id, kind, after_content) == pre_extension_id
    assert ids._decision_event_id(proposal_id, kind, after_content, 0) == pre_extension_id


def test_decision_event_id_generation_none_and_zero_are_equivalent_to_omission():
    """revert_generation=None（旧 entry の ``.get()`` フォールバック相当）も gen=0 と同じ扱い。"""
    proposal_id, kind, after_content = "evdiff_x", "accept", "after content"
    digest12 = hashlib.sha256(after_content.encode("utf-8")).hexdigest()[:12]
    pre_extension_id = f"{proposal_id}_{kind}_{digest12}"

    assert ids._decision_event_id(proposal_id, kind, after_content, 0) == pre_extension_id


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
    # 期待値は _decision_event_id を呼ばず、拡張前の実装を hashlib だけで独立に再現する
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
    before_sha = ids._sha256(skill.read_text(encoding="utf-8"))
    pid = ids._proposal_id(str(skill), before_sha)
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
    違反の早期検知・round2 codex レビュー Should）。"""
    fe = _import_fitness_evolution()
    hist = tmp_path / "hist.jsonl"

    # REVERT_FIELD_KEYS のうち "scope" を既存 entry のキーであるかのように衝突させる
    # ため、entry 組立後に必ず存在するキー名 "id" を許可リストへ一時的に混ぜて検証する。
    import evolve_decision_ids as ids

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fe, "REVERT_FIELD_KEYS", ("id",))
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
        json.dumps({"id": "evolve_diff_xyz", "revert_before_b64": ids._compress_before_content(original_text)})
        + "\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "restored_SKILL.md"

    subprocess.run(
        [sys.executable, "-c", script, str(jsonl_path), "evolve_diff_xyz", str(out_path)],
        check=True, capture_output=True, text=True,
    )

    assert out_path.read_text(encoding="utf-8") == original_text
