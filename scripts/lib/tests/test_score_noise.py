"""score_noise モジュールのテスト。"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from score_noise import (
    compute_stats,
    recommend_epsilon,
    aggregate_runs,
    _score_single_axis,
    compare_prompt_versions,
    parse_score,
    build_scoring_requests,
    aggregate_from_responses,
    FALLBACK_SCORE,
)
from scorer_prompts import DEFAULT_AXIS_WEIGHTS


def test_compute_stats_basic():
    scores = [0.70, 0.75, 0.72, 0.68, 0.73]
    stats = compute_stats(scores)
    assert stats["mean"] == pytest.approx(0.716, abs=0.001)
    assert stats["std"] == pytest.approx(0.025, abs=0.005)
    assert stats["min"] == 0.68
    assert stats["max"] == 0.75
    assert stats["n"] == 5


def test_compute_stats_single():
    stats = compute_stats([0.80])
    assert stats["mean"] == 0.80
    assert stats["std"] == 0.0
    assert stats["n"] == 1


def test_compute_stats_identical():
    scores = [0.75, 0.75, 0.75]
    stats = compute_stats(scores)
    assert stats["std"] == 0.0


def test_recommend_epsilon_low_noise():
    # σ = 0.02 → epsilon = 2σ = 0.04
    stats = {"std": 0.02}
    epsilon = recommend_epsilon(stats)
    assert epsilon == pytest.approx(0.04, abs=0.001)


def test_recommend_epsilon_minimum():
    # σ が極小でも epsilon の下限は 0.02
    stats = {"std": 0.005}
    epsilon = recommend_epsilon(stats)
    assert epsilon >= 0.02


def test_recommend_epsilon_high_noise():
    # σ = 0.08 → epsilon = 0.16 だが上限 0.15 にクリップ
    stats = {"std": 0.08}
    epsilon = recommend_epsilon(stats)
    assert epsilon <= 0.15


def test_aggregate_runs_shape():
    # 3 runs × 3 axes のデータを集約
    runs = [
        {"technical": 0.70, "domain": 0.65, "structure": 0.80, "integrated": 0.695},
        {"technical": 0.72, "domain": 0.68, "structure": 0.78, "integrated": 0.708},
        {"technical": 0.68, "domain": 0.70, "structure": 0.82, "integrated": 0.702},
    ]
    result = aggregate_runs(runs)
    assert set(result.keys()) == {"technical", "domain", "structure", "integrated"}
    for axis_stats in result.values():
        assert "mean" in axis_stats
        assert "std" in axis_stats
        assert "n" in axis_stats
        assert axis_stats["n"] == 3


def test_aggregate_runs_integrated_stats():
    runs = [
        {"technical": 0.70, "domain": 0.65, "structure": 0.80, "integrated": 0.695},
        {"technical": 0.72, "domain": 0.68, "structure": 0.78, "integrated": 0.708},
    ]
    result = aggregate_runs(runs)
    assert result["integrated"]["mean"] == pytest.approx(0.7015, abs=0.001)


# --- リトライ機構のテスト ---

def _make_proc(returncode: int, stdout: str) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    return m


@patch("score_noise.subprocess.run")
def test_score_single_axis_success_first_try(mock_run):
    mock_run.return_value = _make_proc(0, "0.75")
    score = _score_single_axis("technical", "some content")
    assert score == pytest.approx(0.75)
    assert mock_run.call_count == 1


@patch("score_noise.subprocess.run")
def test_score_single_axis_retry_on_nonzero(mock_run):
    """returncode != 0 の場合はリトライし、2回目で成功する。"""
    mock_run.side_effect = [
        _make_proc(1, ""),
        _make_proc(0, "0.80"),
    ]
    score = _score_single_axis("technical", "some content")
    assert score == pytest.approx(0.80)
    assert mock_run.call_count == 2


@patch("score_noise.subprocess.run")
def test_score_single_axis_retry_on_no_match(mock_run):
    """スコアが解析できない場合はリトライし、2回目で成功する。"""
    mock_run.side_effect = [
        _make_proc(0, "申し訳ありませんが評価できません"),
        _make_proc(0, "0.72"),
    ]
    score = _score_single_axis("domain", "some content")
    assert score == pytest.approx(0.72)
    assert mock_run.call_count == 2


@patch("score_noise.subprocess.run")
def test_score_single_axis_fallback_after_all_retries(mock_run):
    """全リトライ失敗時は FALLBACK_SCORE を返す。"""
    mock_run.return_value = _make_proc(1, "")
    score = _score_single_axis("structure", "some content", max_retries=2)
    assert score == 0.5
    assert mock_run.call_count == 3  # 初回 + 2リトライ


@patch("score_noise.subprocess.run")
def test_score_single_axis_timeout_then_success(mock_run):
    """タイムアウト後にリトライして成功する。"""
    mock_run.side_effect = [
        subprocess.TimeoutExpired(cmd="claude", timeout=60),
        _make_proc(0, "0.68"),
    ]
    score = _score_single_axis("technical", "some content")
    assert score == pytest.approx(0.68)
    assert mock_run.call_count == 2


@patch("score_noise.subprocess.run")
def test_score_single_axis_file_not_found_no_retry(mock_run):
    """claude が見つからない場合はリトライしない。"""
    mock_run.side_effect = FileNotFoundError
    score = _score_single_axis("technical", "some content")
    assert score == 0.5
    assert mock_run.call_count == 1


# --- compare_prompt_versions のテスト ---


def test_compare_prompt_versions_returns_diff():
    """A/B プロンプトの σ と mean drift を比較して結果を返す"""
    a_runs = [
        {"technical": 0.70, "domain": 0.70, "structure": 0.70, "integrated": 0.70},
        {"technical": 0.75, "domain": 0.75, "structure": 0.75, "integrated": 0.75},
        {"technical": 0.65, "domain": 0.65, "structure": 0.65, "integrated": 0.65},
    ]
    b_runs = [
        {"technical": 0.70, "domain": 0.70, "structure": 0.70, "integrated": 0.70},
        {"technical": 0.71, "domain": 0.71, "structure": 0.71, "integrated": 0.71},
        {"technical": 0.69, "domain": 0.69, "structure": 0.69, "integrated": 0.69},
    ]
    result = compare_prompt_versions(a_runs, b_runs)
    # B はノイズが小さい
    assert result["b"]["stats"]["integrated"]["std"] < result["a"]["stats"]["integrated"]["std"]
    # 平均は近い → recommended は B（ノイズ低減）
    assert result["recommended"] == "b"
    assert "mean_drift" in result
    assert "sigma_delta" in result


def test_compare_prompt_versions_warns_on_mean_drift():
    """B の平均がドリフトしている場合は警告フラグを立てる"""
    a_runs = [
        {"technical": 0.70, "domain": 0.70, "structure": 0.70, "integrated": 0.70},
        {"technical": 0.71, "domain": 0.71, "structure": 0.71, "integrated": 0.71},
    ]
    b_runs = [
        # B は ノイズは小さいが、平均が 0.40 まで下がっている（採点基準が変わった）
        {"technical": 0.40, "domain": 0.40, "structure": 0.40, "integrated": 0.40},
        {"technical": 0.41, "domain": 0.41, "structure": 0.41, "integrated": 0.41},
    ]
    result = compare_prompt_versions(a_runs, b_runs, drift_threshold=0.05)
    # 平均が 0.30 もドリフトしている → 警告
    assert result["mean_drift_warning"] is True
    # 推奨は A（ドリフトが大きすぎて B は採用不可）
    assert result["recommended"] == "a"


# --- PoC: claude -p 全廃のファイルベース2相パターン（LLM-free、mock 不要）---
# Phase A: build_scoring_requests（決定論の前処理）
# Phase B: assistant が Task/インラインで採点（テスト対象外）
# Phase C: aggregate_from_responses（決定論のゲート）


def test_parse_score_extracts_float():
    """LLM 出力テキストからスコア float を抽出する（旧 _run_claude_prompt の regex を単独化）"""
    assert parse_score("0.75") == 0.75
    assert parse_score("スコアは 0.82 です") == 0.82
    assert parse_score("1.0") == 1.0
    assert parse_score("評価: 0") == 0.0


def test_parse_score_fallback_on_no_match():
    """数値が無い出力は FALLBACK_SCORE を返す"""
    assert parse_score("評価できません") == FALLBACK_SCORE
    assert parse_score("") == FALLBACK_SCORE


def test_build_scoring_requests_shape():
    """runs × axes の採点リクエストを決定論で生成する（LLM 呼び出しなし）"""
    requests = build_scoring_requests("SKILL 本文", runs=3)
    n_axes = len(DEFAULT_AXIS_WEIGHTS)
    assert len(requests) == 3 * n_axes
    # 各 request は id/run/axis/prompt を持つ
    first = requests[0]
    assert set(first.keys()) == {"id", "run", "axis", "prompt"}
    # id は run:axis で一意
    ids = [r["id"] for r in requests]
    assert len(ids) == len(set(ids))
    # prompt に content が埋め込まれている
    assert "SKILL 本文" in first["prompt"]


def test_build_scoring_requests_covers_all_axes_each_run():
    """各 run で全軸がカバーされる"""
    requests = build_scoring_requests("x", runs=2)
    for run_idx in range(2):
        axes_in_run = {r["axis"] for r in requests if r["run"] == run_idx}
        assert axes_in_run == set(DEFAULT_AXIS_WEIGHTS.keys())


def test_aggregate_from_responses_roundtrip():
    """Phase B の採点結果(id→score)を集約して measure_noise 同形の結果を返す"""
    requests = build_scoring_requests("x", runs=2)
    # assistant が返したと想定する responses（id → 生テキスト or float 混在）
    responses = {}
    for r in requests:
        responses[r["id"]] = "0.80" if r["axis"] == "technical" else 0.60
    result = aggregate_from_responses(requests, responses, runs=2)
    assert result["runs"] == 2
    assert len(result["raw"]) == 2
    # integrated = 0.80*0.40 + 0.60*0.40 + 0.60*0.20 = 0.32+0.24+0.12 = 0.68
    assert result["raw"][0]["integrated"] == 0.68
    assert "integrated" in result["stats"]
    assert "recommended_epsilon" in result


def test_aggregate_from_responses_missing_id_uses_fallback():
    """responses に欠損 id があっても FALLBACK_SCORE で穴埋めして壊れない"""
    requests = build_scoring_requests("x", runs=1)
    result = aggregate_from_responses(requests, {}, runs=1)  # 全欠損
    # 全軸 FALLBACK → integrated == FALLBACK_SCORE
    assert result["raw"][0]["integrated"] == round(FALLBACK_SCORE, 4)
