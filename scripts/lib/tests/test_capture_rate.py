"""correction capture 率の決定論算出テスト（#421）。

capture 率 = 「min_turns 以上のターン（usage.jsonl の同一 session_id レコード数を proxy）を
持つセッション」のうち「同一セッションで correction を 1 件以上検出した割合」。
スコア重みには入れない advisory メトリクス。LLM 非依存・決定論。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import capture_rate  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _write_usage(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _usage_rows(session_id: str, n: int, *, ts: str | None = None) -> list[dict]:
    ts = ts or _now_iso()
    return [{"session_id": session_id, "skill_name": "Bash", "ts": ts} for _ in range(n)]


def test_no_usage_file_is_not_applicable(tmp_path):
    result = capture_rate.compute_capture_rate(
        usage_file=tmp_path / "missing.jsonl",
        corrections_file=tmp_path / "missing_corr.jsonl",
    )
    assert result["applicable"] is False
    assert result["active_sessions"] == 0


def test_no_active_sessions_when_below_threshold(tmp_path):
    """min_turns 未満のセッションしかなければ分母 0 → not applicable。"""
    usage = tmp_path / "usage.jsonl"
    _write_usage(usage, _usage_rows("s1", 5))  # 5 < 20
    result = capture_rate.compute_capture_rate(
        usage_file=usage,
        corrections_file=tmp_path / "corr.jsonl",
        min_turns=20,
    )
    assert result["active_sessions"] == 0
    assert result["applicable"] is False


def test_capture_rate_half(tmp_path):
    """20+ ターンのセッション 2 件中 1 件に correction → 0.5。"""
    usage = tmp_path / "usage.jsonl"
    rows = _usage_rows("s1", 25) + _usage_rows("s2", 22)
    _write_usage(usage, rows)
    corr = tmp_path / "corrections.jsonl"
    corr.write_text(
        json.dumps({"session_id": "s1", "timestamp": _now_iso()}) + "\n",
        encoding="utf-8",
    )
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=corr, min_turns=20
    )
    assert result["applicable"] is True
    assert result["active_sessions"] == 2
    assert result["captured_sessions"] == 1
    assert result["capture_rate"] == 0.5


def test_capture_rate_full(tmp_path):
    usage = tmp_path / "usage.jsonl"
    _write_usage(usage, _usage_rows("s1", 30))
    corr = tmp_path / "corrections.jsonl"
    corr.write_text(
        json.dumps({"session_id": "s1", "timestamp": _now_iso()}) + "\n",
        encoding="utf-8",
    )
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=corr, min_turns=20
    )
    assert result["capture_rate"] == 1.0
    assert result["captured_sessions"] == 1


def test_capture_rate_zero_when_no_corrections(tmp_path):
    usage = tmp_path / "usage.jsonl"
    _write_usage(usage, _usage_rows("s1", 30) + _usage_rows("s2", 40))
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=tmp_path / "no_corr.jsonl", min_turns=20
    )
    assert result["applicable"] is True
    assert result["active_sessions"] == 2
    assert result["captured_sessions"] == 0
    assert result["capture_rate"] == 0.0


def test_correction_for_non_active_session_does_not_count(tmp_path):
    """correction が active でないセッションに属していても numerator に入らない。"""
    usage = tmp_path / "usage.jsonl"
    _write_usage(usage, _usage_rows("active", 25) + _usage_rows("short", 3))
    corr = tmp_path / "corrections.jsonl"
    corr.write_text(
        json.dumps({"session_id": "short", "timestamp": _now_iso()}) + "\n",
        encoding="utf-8",
    )
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=corr, min_turns=20
    )
    assert result["active_sessions"] == 1
    assert result["captured_sessions"] == 0
    assert result["capture_rate"] == 0.0


def test_days_window_excludes_old_records(tmp_path):
    """days 窓より古い usage レコードは分母に入らない。"""
    usage = tmp_path / "usage.jsonl"
    old = _usage_rows("old", 30, ts=_ts_days_ago(90))
    recent = _usage_rows("recent", 30, ts=_now_iso())
    _write_usage(usage, old + recent)
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=tmp_path / "c.jsonl", days=30, min_turns=20
    )
    assert result["active_sessions"] == 1  # old は窓外


# ── #489: project フィルタ（usage は project / corrections は project_path basename） ──

def test_project_filter_scopes_usage_and_corrections(tmp_path):
    """project 指定で当PJの usage / corrections のみ数える（#489）。

    usage.jsonl は ``project``（basename）、corrections.jsonl は ``project_path``
    （フルパス）で PJ を識別する。当PJ "mine" を渡すと他PJ "other" は除外される。
    """
    usage = tmp_path / "usage.jsonl"
    _write_usage(
        usage,
        [{"session_id": "m1", "skill_name": "Bash", "ts": _now_iso(), "project": "mine"} for _ in range(25)]
        + [{"session_id": "o1", "skill_name": "Bash", "ts": _now_iso(), "project": "other"} for _ in range(30)],
    )
    corr = tmp_path / "corrections.jsonl"
    corr.write_text(
        json.dumps({"session_id": "m1", "timestamp": _now_iso(), "project_path": "/work/mine"}) + "\n"
        + json.dumps({"session_id": "o1", "timestamp": _now_iso(), "project_path": "/work/other"}) + "\n",
        encoding="utf-8",
    )
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=corr, min_turns=20, project="mine"
    )
    assert result["active_sessions"] == 1  # other は除外
    assert result["captured_sessions"] == 1  # mine の correction のみ
    assert result["capture_rate"] == 1.0


def test_project_filter_normalizes_worktree_paths(tmp_path):
    """worktree セッションの record が本体 repo の当PJ slug にマッチする（#489 差し戻し）。

    corrections の project_path はフルパスなので /.claude/worktrees/ を切って本体名に正規化
    される。project=当PJ slug（capture_rate._normalize_pj 済み）で突合する。project_dir 自体が
    worktree パスでも本体 slug に正規化される。
    """
    usage = tmp_path / "usage.jsonl"
    _write_usage(
        usage,
        [{"session_id": "m1", "skill_name": "Bash", "ts": _now_iso(), "project": "evolve-anything"} for _ in range(25)]
        + [{"session_id": "o1", "skill_name": "Bash", "ts": _now_iso(), "project": "other"} for _ in range(30)],
    )
    corr = tmp_path / "corrections.jsonl"
    corr.write_text(
        # worktree セッションの correction（project_path に /.claude/worktrees/）
        json.dumps({"session_id": "m1", "timestamp": _now_iso(),
                    "project_path": "/x/evolve-anything/.claude/worktrees/feedback"}) + "\n",
        encoding="utf-8",
    )
    # project_dir 自体が worktree パスでも本体 slug に正規化される
    project = capture_rate._normalize_pj("/x/evolve-anything/.claude/worktrees/agent-foo")
    assert project == "evolve-anything"
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=corr, min_turns=20, project=project,
    )
    assert result["active_sessions"] == 1  # other 除外
    # worktree correction が本体 slug に正規化され当PJ captured に乗る
    assert result["captured_sessions"] == 1
    assert result["capture_rate"] == 1.0


def test_project_filter_includes_unattributed_records(tmp_path):
    """project / project_path の無いレコードは寛容に include する（unattributed 救済）。"""
    usage = tmp_path / "usage.jsonl"
    _write_usage(
        usage,
        [{"session_id": "m1", "skill_name": "Bash", "ts": _now_iso(), "project": "mine"} for _ in range(25)]
        + [{"session_id": "u1", "skill_name": "Bash", "ts": _now_iso()} for _ in range(25)],  # project 無し
    )
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=tmp_path / "c.jsonl", min_turns=20, project="mine"
    )
    assert result["active_sessions"] == 2  # mine + unattributed


def test_malformed_lines_skipped(tmp_path):
    usage = tmp_path / "usage.jsonl"
    usage.parent.mkdir(parents=True, exist_ok=True)
    good = "\n".join(
        json.dumps({"session_id": "s1", "skill_name": "Bash", "ts": _now_iso()})
        for _ in range(25)
    )
    usage.write_text(good + "\nNOT JSON\n{bad\n", encoding="utf-8")
    result = capture_rate.compute_capture_rate(
        usage_file=usage, corrections_file=tmp_path / "c.jsonl", min_turns=20
    )
    assert result["active_sessions"] == 1
