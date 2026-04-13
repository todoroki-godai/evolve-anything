#!/usr/bin/env python3
"""quality_monitor.py のユニットテスト。"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quality_monitor import (
    DEGRADATION_THRESHOLD,
    HIGH_FREQ_DAYS,
    HIGH_FREQ_THRESHOLD,
    MAX_RECORDS_PER_SKILL,
    RESCORE_DAYS_THRESHOLD,
    RESCORE_USAGE_THRESHOLD,
    _parse_cot_response,
    append_record,
    compute_baseline_score,
    compute_moving_average,
    detect_degradation,
    evaluate_skill,
    find_high_freq_skills,
    get_skill_records,
    load_baselines,
    needs_rescore,
    resolve_skill_path,
    save_baselines,
)


# ── 定数テスト ──────────────────────────────────────────


def test_constants():
    """定数が規定値であることを確認。"""
    assert RESCORE_USAGE_THRESHOLD == 50
    assert RESCORE_DAYS_THRESHOLD == 7
    assert DEGRADATION_THRESHOLD == 0.10
    assert HIGH_FREQ_THRESHOLD == 10
    assert HIGH_FREQ_DAYS == 30
    assert MAX_RECORDS_PER_SKILL == 100


# ── ベースライン I/O ──────────────────────────────────────


def test_load_baselines_empty(tmp_path):
    """ファイルが無い場合は空リスト。"""
    with patch("quality_monitor.BASELINES_FILE", tmp_path / "nonexistent.jsonl"):
        assert load_baselines() == []


def test_load_save_baselines(tmp_path):
    """保存→読み込みの往復テスト。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"
    records = [
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01T00:00:00+00:00"},
        {"skill_name": "commit", "score": 0.80, "timestamp": "2025-01-08T00:00:00+00:00"},
    ]
    with patch("quality_monitor.BASELINES_FILE", baselines_file):
        save_baselines(records)
        loaded = load_baselines()
    assert len(loaded) == 2
    assert loaded[0]["score"] == 0.85
    assert loaded[1]["score"] == 0.80


def test_get_skill_records():
    """指定スキルのレコードのみ抽出、タイムスタンプ順。"""
    records = [
        {"skill_name": "commit", "score": 0.80, "timestamp": "2025-01-08"},
        {"skill_name": "other", "score": 0.90, "timestamp": "2025-01-01"},
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01"},
    ]
    result = get_skill_records(records, "commit")
    assert len(result) == 2
    assert result[0]["timestamp"] == "2025-01-01"
    assert result[1]["timestamp"] == "2025-01-08"


def test_append_record_respects_limit(tmp_path):
    """レコード上限を超えると古いレコードが削除される。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"

    # MAX_RECORDS_PER_SKILL 件のレコードを作成
    existing = []
    for i in range(MAX_RECORDS_PER_SKILL):
        existing.append({
            "skill_name": "commit",
            "score": 0.80,
            "timestamp": f"2025-01-{i+1:02d}T00:00:00+00:00",
        })

    with patch("quality_monitor.BASELINES_FILE", baselines_file):
        save_baselines(existing)
        # 1件追加 → 最古の1件が削除されるはず
        new_record = {
            "skill_name": "commit",
            "score": 0.90,
            "timestamp": "2025-05-01T00:00:00+00:00",
        }
        append_record(new_record)

        loaded = load_baselines()
        commit_recs = [r for r in loaded if r["skill_name"] == "commit"]
        assert len(commit_recs) == MAX_RECORDS_PER_SKILL
        # 最古のレコードが消えている
        timestamps = [r["timestamp"] for r in commit_recs]
        assert "2025-01-01T00:00:00+00:00" not in timestamps
        assert "2025-05-01T00:00:00+00:00" in timestamps


# ── スキル検出 ──────────────────────────────────────────


def test_resolve_skill_path_global(tmp_path):
    """~/.claude/skills/{name}/SKILL.md のパス解決。"""
    skill_dir = tmp_path / "skills" / "commit"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# commit skill")

    with patch("quality_monitor.Path.home", return_value=tmp_path / "fake_home"):
        # パスが存在しないので None
        assert resolve_skill_path("nonexistent") is None

    # 直接パスを差し替えてテスト
    with patch("quality_monitor.Path.home", return_value=tmp_path):
        result = resolve_skill_path("commit")
        # tmp_path/.claude/skills/commit/SKILL.md を期待
        # ただし tmp_path/skills/ にしか作ってないので None になる
        # ~/.claude/skills/ に合わせて作り直す
    claude_skills = tmp_path / ".claude" / "skills" / "commit"
    claude_skills.mkdir(parents=True)
    (claude_skills / "SKILL.md").write_text("# commit")
    with patch("quality_monitor.Path.home", return_value=tmp_path):
        result = resolve_skill_path("commit")
        assert result is not None
        assert result.name == "SKILL.md"


def test_find_high_freq_skills():
    """高頻度 global/plugin スキルの検出。"""
    usage_data = [
        {"skill_name": "commit", "ts": datetime.now(timezone.utc).isoformat()},
    ] * 15 + [
        {"skill_name": "rarely", "ts": datetime.now(timezone.utc).isoformat()},
    ] * 3

    mock_path = Path("/fake/.claude/skills/commit/SKILL.md")

    with patch("quality_monitor.load_usage_data", return_value=usage_data), \
         patch("quality_monitor.aggregate_usage", return_value={"commit": 15, "rarely": 3}), \
         patch("quality_monitor.resolve_skill_path", side_effect=lambda n: mock_path if n == "commit" else None), \
         patch("quality_monitor.classify_artifact_origin", return_value="global"):
        result = find_high_freq_skills()
        assert "commit" in result
        assert result["commit"] == 15
        assert "rarely" not in result


# ── LLM 品質評価 ──────────────────────────────────────────


def test_parse_cot_response_valid_json():
    """正常な JSON レスポンスのパース。"""
    response = json.dumps({
        "clarity": {"score": 0.85, "reason": "clear"},
        "completeness": {"score": 0.80, "reason": "complete"},
        "structure": {"score": 0.90, "reason": "well structured"},
        "practicality": {"score": 0.75, "reason": "practical"},
        "total": 0.825,
    })
    score, cot = _parse_cot_response(response)
    assert score == 0.825
    assert cot is not None
    assert cot["clarity"]["score"] == 0.85


def test_parse_cot_response_json_code_block():
    """```json ブロック内の JSON をパース。"""
    response = '```json\n{"clarity": {"score": 0.9, "reason": "x"}, "total": 0.9}\n```'
    score, cot = _parse_cot_response(response)
    assert score == 0.9


def test_parse_cot_response_no_total():
    """total が無い場合、各基準の平均を計算。"""
    response = json.dumps({
        "clarity": {"score": 0.80, "reason": "ok"},
        "completeness": {"score": 0.80, "reason": "ok"},
        "structure": {"score": 0.80, "reason": "ok"},
        "practicality": {"score": 0.80, "reason": "ok"},
    })
    score, cot = _parse_cot_response(response)
    assert abs(score - 0.80) < 0.01
    assert cot["total"] == 0.8


def test_parse_cot_response_fallback():
    """JSON パース失敗時の正規表現フォールバック。"""
    score, cot = _parse_cot_response("The score is 0.75 overall")
    assert score == 0.75
    assert cot is None


def test_evaluate_skill_timeout():
    """タイムアウト時は None を返す。"""
    import subprocess
    with patch("quality_monitor.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 60)):
        result = evaluate_skill("# test skill")
        assert result is None


def test_evaluate_skill_command_not_found():
    """claude コマンドが見つからない場合は None。"""
    with patch("quality_monitor.subprocess.run", side_effect=FileNotFoundError):
        result = evaluate_skill("# test skill")
        assert result is None


def test_evaluate_skill_nonzero_exit():
    """claude -p が非ゼロ終了の場合は None。"""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "error"
    with patch("quality_monitor.subprocess.run", return_value=mock_result):
        result = evaluate_skill("# test skill")
        assert result is None


def test_evaluate_skill_success():
    """正常な評価結果。"""
    response_json = json.dumps({
        "clarity": {"score": 0.85, "reason": "clear"},
        "completeness": {"score": 0.80, "reason": "ok"},
        "structure": {"score": 0.90, "reason": "good"},
        "practicality": {"score": 0.75, "reason": "fine"},
        "total": 0.825,
    })
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = response_json
    with patch("quality_monitor.subprocess.run", return_value=mock_result):
        result = evaluate_skill("# test skill")
        assert result is not None
        score, cot = result
        assert score == 0.825


# ── 再スコアリング判定 ──────────────────────────────────────


def test_needs_rescore_no_records():
    """レコードなし → 初回計測が必要。"""
    assert needs_rescore("commit", 100, baselines=[]) is True


def test_needs_rescore_usage_threshold():
    """使用回数閾値超過で再スコアリングが必要。"""
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": datetime.now(timezone.utc).isoformat(), "usage_count_at_measure": 100},
    ]
    # 差分 55 >= 50
    assert needs_rescore("commit", 155, baselines=baselines) is True
    # 差分 30 < 50
    assert needs_rescore("commit", 130, baselines=baselines) is False


def test_needs_rescore_days_threshold():
    """経過日数閾値超過で再スコアリングが必要。"""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": old_ts, "usage_count_at_measure": 100},
    ]
    # 10日 >= 7日
    assert needs_rescore("commit", 110, baselines=baselines) is True


def test_needs_rescore_both_below():
    """両方閾値未満 → 不要。"""
    recent_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": recent_ts, "usage_count_at_measure": 100},
    ]
    assert needs_rescore("commit", 120, baselines=baselines) is False


# ── 劣化検知 ──────────────────────────────────────────


def test_compute_baseline_score():
    """最高スコアを返す。"""
    recs = [
        {"score": 0.80},
        {"score": 0.85},
        {"score": 0.75},
    ]
    assert compute_baseline_score(recs) == 0.85


def test_compute_baseline_score_empty():
    """空リストなら 0.0。"""
    assert compute_baseline_score([]) == 0.0


def test_compute_moving_average():
    """直近3回の移動平均。"""
    recs = [
        {"score": 0.80, "timestamp": "2025-01-01"},
        {"score": 0.85, "timestamp": "2025-01-02"},
        {"score": 0.90, "timestamp": "2025-01-03"},
        {"score": 0.70, "timestamp": "2025-01-04"},
        {"score": 0.75, "timestamp": "2025-01-05"},
    ]
    avg = compute_moving_average(recs, window=3)
    # 直近3件: 0.90, 0.70, 0.75 → 平均 0.783...
    expected = (0.90 + 0.70 + 0.75) / 3
    assert abs(avg - expected) < 0.001


def test_compute_moving_average_less_than_window():
    """計測履歴が3回未満の場合、存在するレコードの平均を使用。"""
    recs = [
        {"score": 0.80, "timestamp": "2025-01-01"},
        {"score": 0.70, "timestamp": "2025-01-02"},
    ]
    avg = compute_moving_average(recs, window=3)
    expected = (0.80 + 0.70) / 2
    assert abs(avg - expected) < 0.001


def test_detect_degradation_degraded():
    """10%以上の低下で劣化を検知。"""
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01"},
        {"skill_name": "commit", "score": 0.80, "timestamp": "2025-01-02"},
        {"skill_name": "commit", "score": 0.74, "timestamp": "2025-01-03"},
        {"skill_name": "commit", "score": 0.72, "timestamp": "2025-01-04"},
    ]
    result = detect_degradation("commit", baselines=baselines)
    assert result is not None
    assert result["skill_name"] == "commit"
    assert result["baseline_score"] == 0.85
    assert result["recommended_command"] == "/optimize commit"
    assert result["decline_rate"] >= 10.0


def test_detect_degradation_not_degraded():
    """10%未満の低下では劣化なし。"""
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01"},
        {"skill_name": "commit", "score": 0.84, "timestamp": "2025-01-02"},
        {"skill_name": "commit", "score": 0.80, "timestamp": "2025-01-03"},
    ]
    result = detect_degradation("commit", baselines=baselines)
    assert result is None


def test_detect_degradation_single_record():
    """1件のみではベースライン記録のみ、劣化判定なし。"""
    baselines = [
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01"},
    ]
    result = detect_degradation("commit", baselines=baselines)
    assert result is None


def test_detect_degradation_no_records():
    """レコードなしなら None。"""
    result = detect_degradation("commit", baselines=[])
    assert result is None
