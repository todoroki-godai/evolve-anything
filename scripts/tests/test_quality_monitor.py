#!/usr/bin/env python3
"""quality_monitor.py のユニットテスト。

claude -p 全廃（[ADR-037]）後はファイルベース2相のため LLM を一切呼ばない（mock 不要）。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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
    build_cot_prompt,
    compute_baseline_score,
    compute_moving_average,
    detect_degradation,
    emit_rescore_requests,
    find_high_freq_skills,
    get_skill_records,
    ingest_responses,
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


def test_resolve_skill_path_none_returns_none():
    """skill_name が None/空でも例外を投げず None を返す（implement の skill フィールド由来）。"""
    assert resolve_skill_path(None) is None
    assert resolve_skill_path("") is None


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


# ── ファイルベース2相（Phase A: emit / Phase C: ingest）──────────────────────


def test_build_cot_prompt_embeds_content():
    """CoT プロンプトにスキル本文と評価基準が埋め込まれる（LLM ゼロ）。"""
    prompt = build_cot_prompt("# my skill body")
    assert "# my skill body" in prompt
    assert "clarity" in prompt and "total" in prompt


_COT_RESPONSE = json.dumps({
    "clarity": {"score": 0.85, "reason": "clear"},
    "completeness": {"score": 0.80, "reason": "ok"},
    "structure": {"score": 0.90, "reason": "good"},
    "practicality": {"score": 0.75, "reason": "fine"},
    "total": 0.825,
})


def _patch_high_freq(skill_dir: Path, skill_name: str = "commit", count: int = 60):
    """find_high_freq_skills と resolve_skill_path を patch するコンテキストを返す。"""
    skill_md = skill_dir / skill_name / "SKILL.md"
    skill_md.parent.mkdir(parents=True, exist_ok=True)
    skill_md.write_text("# commit skill body", encoding="utf-8")
    return patch.multiple(
        "quality_monitor",
        find_high_freq_skills=lambda: {skill_name: count},
        resolve_skill_path=lambda n: skill_md if n == skill_name else None,
    ), skill_md


def test_emit_rescore_requests_shape(tmp_path):
    """Phase A: 再スコア対象の request を生成（id/prompt/meta、LLM ゼロ）。"""
    ctx, skill_md = _patch_high_freq(tmp_path / "skills")
    baselines_file = tmp_path / "quality-baselines.jsonl"
    with ctx, patch("quality_monitor.BASELINES_FILE", baselines_file):
        emitted = emit_rescore_requests()
    assert len(emitted["requests"]) == 1
    req = emitted["requests"][0]
    assert req["id"] == "commit"
    assert "# commit skill body" in req["prompt"]
    assert req["meta"]["skill_path"] == str(skill_md)
    assert req["meta"]["usage_count"] == 60
    # _content は meta に残さない（responses JSON 肥大化防止）
    assert "_content" not in req["meta"]


def test_emit_rescore_requests_skips_below_threshold(tmp_path):
    """既存 baseline が新しく閾値未満なら skip（request に含めない）。"""
    ctx, skill_md = _patch_high_freq(tmp_path / "skills", count=60)
    baselines_file = tmp_path / "quality-baselines.jsonl"
    recent = {
        "skill_name": "commit", "score": 0.85,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "usage_count_at_measure": 50,  # 差分 10 < 50
    }
    with ctx, patch("quality_monitor.BASELINES_FILE", baselines_file):
        save_baselines([recent])
        emitted = emit_rescore_requests()
    assert emitted["requests"] == []
    assert emitted["skipped"][0]["reason"] == "below threshold"


def test_ingest_responses_appends_and_returns_measured(tmp_path):
    """Phase C: 採点応答をパースし baselines 追記、measured を返す（LLM ゼロ）。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"
    requests = [{
        "id": "commit", "prompt": "...",
        "meta": {"skill_path": "/x/SKILL.md", "usage_count": 60},
    }]
    responses = {"commit": _COT_RESPONSE}
    with patch("quality_monitor.BASELINES_FILE", baselines_file):
        result = ingest_responses(requests, responses)
        loaded = load_baselines()
    assert len(result["measured"]) == 1
    assert result["measured"][0]["skill_name"] == "commit"
    assert result["measured"][0]["score"] == 0.825
    assert result["measured"][0]["usage_count_at_measure"] == 60
    assert len(loaded) == 1  # baselines に追記された


def test_ingest_responses_missing_response_skipped(tmp_path):
    """採点漏れ（応答欠損）は skip され壊れない。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"
    requests = [{"id": "commit", "prompt": "...", "meta": {}}]
    with patch("quality_monitor.BASELINES_FILE", baselines_file):
        result = ingest_responses(requests, {})  # 全欠損
    assert result["measured"] == []
    assert result["skipped"][0]["reason"] == "no response"


def test_ingest_responses_detects_degradation(tmp_path):
    """追記後に劣化が検知されると degraded に載る。"""
    baselines_file = tmp_path / "quality-baselines.jsonl"
    existing = [
        {"skill_name": "commit", "score": 0.85, "timestamp": "2025-01-01T00:00:00+00:00"},
        {"skill_name": "commit", "score": 0.80, "timestamp": "2025-01-02T00:00:00+00:00"},
    ]
    # 新たな採点が 0.60 → 移動平均 (0.85+0.80+0.60)/3=0.75、baseline 0.85 から -11.8%（>=10%）
    low_resp = json.dumps({"total": 0.60})
    requests = [{"id": "commit", "prompt": "...", "meta": {"usage_count": 60}}]
    with patch("quality_monitor.BASELINES_FILE", baselines_file):
        save_baselines(existing)
        result = ingest_responses(requests, {"commit": low_resp})
    assert result["degraded"]
    assert result["degraded"][0]["skill_name"] == "commit"


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
