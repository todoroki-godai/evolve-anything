"""judge_eval.py のユニットテスト（TDD・LLM 非呼出）。

no-llm-in-tests 完全整合: LLM 呼び出しは ``correction_semantic.judge_runner.call_haiku``
の1点に集約されており、本テストは常にこれを mock（``call_haiku_fn`` DI）で差し替える。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import judge_eval as je  # noqa: E402
from correction_semantic import prompt as _prompt  # noqa: E402


# ─────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────

def _row(eval_id: str, label: str, text: str = "text", category: str = "correction") -> Dict[str, Any]:
    return {
        "eval_id": eval_id,
        "label": label,
        "category": category,
        "text": text,
        "pj_slug": "demo",
        "prior_assistant_text": None,
    }


TP_ROW = _row("tp-1", "TP", text="四国めたんじゃなくてつむぎにして")
NOT_TP_ROW = _row("nottp-1", "not_TP", text="続けて", category="continue")


# ─────────────────────────────────────────────────
# 決定論ユニット（grading / ラベル対応 / harness gate）
# ─────────────────────────────────────────────────

def test_expected_is_correction_maps_tp_and_not_tp():
    assert je.expected_is_correction({"label": "TP"}) is True
    assert je.expected_is_correction({"label": "not_TP"}) is False


def test_expected_is_correction_rejects_unknown_label():
    with pytest.raises(ValueError):
        je.expected_is_correction({"label": "maybe"})


def test_label_category_vocab_is_disjoint_from_judge_category_enum():
    """eval コーパスの category（発話種類）と judge の category enum（対象軸）は無関係な語彙。

    混同してグレーディングに使っていないことを固定する回帰テスト。
    """
    corpus_categories = {
        "new_task", "question", "machinery_command", "correction", "approval",
        "automated_prompt", "status_question", "continue", "guardrail", "ambiguous",
        "machinery", "status_update", "forwarded_question", "ambiguous_truncated",
    }
    assert corpus_categories.isdisjoint(set(_prompt.CATEGORY_ENUM))


@pytest.mark.parametrize(
    "expected,predicted,want",
    [
        (True, True, {"accuracy": 1, "precision_hit": 1, "recall_hit": 1}),
        (True, False, {"accuracy": 0, "recall_hit": 0}),
        (False, True, {"accuracy": 0, "precision_hit": 0, "specificity_hit": 0}),
        (False, False, {"accuracy": 1, "specificity_hit": 1}),
    ],
)
def test_grade_case_confusion_matrix_axes(expected, predicted, want):
    assert je.grade_case(expected, predicted) == want


def test_grade_case_none_predicted_is_ungraded():
    assert je.grade_case(True, None) is None


def test_chunk_splits_preserving_order():
    assert je.chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_row_to_utterance_does_not_map_prior_assistant_text_to_prev_action():
    row = {"text": "hi", "prior_assistant_text": "some long assistant text"}
    u = je.row_to_utterance(row)
    assert u["prev_action"] is None
    assert u["text"] == "hi"


# ─────────────────────────────────────────────────
# harness-integrity gate（設計/実装は本人・裁定は別人という no-denylist-checks の枠外だが
# 「壊して赤くする」検証は verify-checks-by-breaking.md に従う）
# ─────────────────────────────────────────────────

def test_harness_sha_changes_when_a_harness_file_content_changes(tmp_path: Path):
    base = tmp_path / "root"
    (base / "scripts" / "bench").mkdir(parents=True)
    (base / "scripts" / "lib" / "correction_semantic").mkdir(parents=True)
    paths = (
        "scripts/bench/judge_eval.py",
        "scripts/lib/correction_semantic/judge_runner.py",
    )
    (base / paths[0]).write_text("v1", encoding="utf-8")
    (base / paths[1]).write_text("v1", encoding="utf-8")
    sha_before = je.compute_harness_sha(paths, base_dir=base)

    # 陽性対照: 内容を変えずに再計算しても同じハッシュ（誤検出しない）。
    assert je.compute_harness_sha(paths, base_dir=base) == sha_before

    # 陰性試験: 1 ファイルだけ書き換えると必ず変わる（検査が実際に効くことを実測で示す）。
    (base / paths[1]).write_text("v2 — mutated", encoding="utf-8")
    sha_after = je.compute_harness_sha(paths, base_dir=base)
    assert sha_after != sha_before


def test_harness_paths_includes_correction_semantic_init():
    """tacchi レビュー Should-3: __init__.py（MAX_CHARS_PER_UTTERANCE/DEFAULT_BATCH_SIZE の
    定義元）が harness-integrity gate の対象に含まれていることを固定する。
    """
    assert "scripts/lib/correction_semantic/__init__.py" in je.HARNESS_PATHS


def test_harness_gate_blocks_when_unapproved():
    ok, _ = je.check_harness_approval({}, "abc123", approve=False)
    assert ok is False


def test_harness_gate_blocks_when_sha_mismatches_recorded():
    state = {"harness_paths": {"paths": [], "sha": "old-sha"}}
    ok, _ = je.check_harness_approval(state, "new-sha", approve=False)
    assert ok is False


def test_harness_gate_passes_when_sha_matches_recorded():
    state = {"harness_paths": {"paths": [], "sha": "same-sha"}}
    ok, _ = je.check_harness_approval(state, "same-sha", approve=False)
    assert ok is True


def test_harness_gate_approve_records_current_sha():
    ok, new_state = je.check_harness_approval({}, "fresh-sha", approve=True)
    assert ok is True
    assert new_state["harness_paths"]["sha"] == "fresh-sha"


# ─────────────────────────────────────────────────
# run_eval（LLM 呼び出しは全て mock）
# ─────────────────────────────────────────────────

def _cases() -> List[Dict[str, Any]]:
    return [TP_ROW, NOT_TP_ROW]


def test_run_eval_oracle_mock_scores_100_percent_accuracy(tmp_path: Path):
    """オラクル: 正解どおりの verdict を返す mock → accuracy 100%（陽性対照）。"""

    def oracle(_prompt_text: str, _model: str) -> str:
        # バッチ内の発話は index 0=TP_ROW, index 1=NOT_TP_ROW（cases() の順）。
        return json.dumps({"verdicts": [
            {"index": 0, "is_correction": True, "idiom": "つむぎにして", "category": "factual", "reason": "r"},
            {"index": 1, "is_correction": False, "idiom": None, "category": None, "reason": "r"},
        ]})

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    summary = je.run_eval(
        _cases(), cfg, flow_dir=tmp_path, call_haiku_fn=oracle, sleep_fn=lambda _s: None,
    )
    assert summary["errors"] == 0
    assert summary["graded"] == 2

    rows = [json.loads(line) for line in (tmp_path / "baseline" / "results.jsonl").read_text().splitlines()]
    by_id = {r["prompt_id"]: r for r in rows}
    assert by_id["tp-1"]["grade"]["accuracy"] == 1
    assert by_id["nottp-1"]["grade"]["accuracy"] == 1


def test_run_eval_null_mock_yields_zero_recall(tmp_path: Path):
    """ヌル: 全件 is_correction=false を返す mock → recall 0（TP を1件も拾えない）。"""

    def null_mock(_prompt_text: str, _model: str) -> str:
        return json.dumps({"verdicts": [
            {"index": 0, "is_correction": False, "idiom": None, "category": None, "reason": "r"},
            {"index": 1, "is_correction": False, "idiom": None, "category": None, "reason": "r"},
        ]})

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    summary = je.run_eval(
        _cases(), cfg, flow_dir=tmp_path, call_haiku_fn=null_mock, sleep_fn=lambda _s: None,
    )
    assert summary["errors"] == 0
    rows = [json.loads(line) for line in (tmp_path / "baseline" / "results.jsonl").read_text().splitlines()]
    by_id = {r["prompt_id"]: r for r in rows}
    assert by_id["tp-1"]["grade"]["recall_hit"] == 0
    assert by_id["nottp-1"]["grade"]["specificity_hit"] == 1


def test_run_eval_api_error_lands_in_errors_not_scored_zero(tmp_path: Path):
    """API エラー: 例外送出 → errors.jsonl に行き、results.jsonl には出ない（grade=0 にしない）。"""

    def failing(_prompt_text: str, _model: str) -> str:
        raise RuntimeError("simulated API error")

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    summary = je.run_eval(
        _cases(), cfg, flow_dir=tmp_path, call_haiku_fn=failing, sleep_fn=lambda _s: None,
    )
    assert summary["graded"] == 0
    assert summary["errors"] == 2
    assert not (tmp_path / "baseline" / "results.jsonl").exists()
    error_rows = [
        json.loads(line) for line in (tmp_path / "baseline" / "errors.jsonl").read_text().splitlines()
    ]
    assert {r["case_id"] for r in error_rows} == {"tp-1", "nottp-1"}
    assert all(r["failure_class"] == "api_error" for r in error_rows)


def test_run_eval_malformed_json_is_parse_failed_not_scored(tmp_path: Path):
    """スキーマ不正 JSON: parse 失敗クラスとして errors.jsonl へ行き、grade を作らない。"""

    def garbage(_prompt_text: str, _model: str) -> str:
        return "not json at all {{{"

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    summary = je.run_eval(
        _cases(), cfg, flow_dir=tmp_path, call_haiku_fn=garbage, sleep_fn=lambda _s: None,
    )
    assert summary["graded"] == 0
    assert summary["errors"] == 2
    error_rows = [
        json.loads(line) for line in (tmp_path / "baseline" / "errors.jsonl").read_text().splitlines()
    ]
    assert all(r["failure_class"] == "parse_failed" for r in error_rows)


def test_run_eval_resume_skips_already_graded_case_rep(tmp_path: Path):
    calls = {"n": 0}

    def oracle(_prompt_text: str, _model: str) -> str:
        calls["n"] += 1
        return json.dumps({"verdicts": [
            {"index": 0, "is_correction": True, "idiom": None, "category": "factual", "reason": "r"},
            {"index": 1, "is_correction": False, "idiom": None, "category": None, "reason": "r"},
        ]})

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    je.run_eval(_cases(), cfg, flow_dir=tmp_path, call_haiku_fn=oracle, sleep_fn=lambda _s: None)
    assert calls["n"] == 1

    summary2 = je.run_eval(_cases(), cfg, flow_dir=tmp_path, call_haiku_fn=oracle, sleep_fn=lambda _s: None)
    assert calls["n"] == 1  # 2回目は呼ばれない（resume 冪等）
    assert summary2["graded"] == 0
    assert summary2["batches_called"] == 0


def test_results_row_never_contains_raw_utterance_text(tmp_path: Path):
    """tacchi レビュー [Must]1 の回帰テスト: results.jsonl の prompt/meta に生発話本文・
    idiom・reason の引用が一切含まれないことを固定する（commit 対象ファイルのため）。
    """
    secret_text = "四国めたんじゃなくてつむぎにして"
    row = _row("secret-1", "TP", text=secret_text)

    def oracle(_prompt_text: str, _model: str) -> str:
        return json.dumps({"verdicts": [
            {"index": 0, "is_correction": True, "idiom": secret_text, "category": "factual",
             "reason": f"引用: {secret_text}"},
        ]})

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    je.run_eval([row], cfg, flow_dir=tmp_path, call_haiku_fn=oracle, sleep_fn=lambda _s: None)

    raw = (tmp_path / "baseline" / "results.jsonl").read_text(encoding="utf-8")
    assert secret_text not in raw
    parsed = json.loads(raw.strip())
    assert "judge_reason" not in parsed["meta"]
    assert "idiom" not in parsed["meta"]
    assert parsed["prompt"].startswith("[redacted")
    assert parsed["meta"]["prompt_sha256"] == hashlib.sha256(secret_text.encode("utf-8")).hexdigest()


def test_run_eval_never_creates_files_under_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """tacchi レビュー Should-4: CLAUDE_PLUGIN_DATA を tmp へ向けても DATA_DIR 配下に
    何も生成されないことを実測する陽性対照（import 宣言だけに頼らない）。
    """
    data_dir = tmp_path / "would_be_data_dir"
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))
    flow_dir = tmp_path / "flow"

    def oracle(_prompt_text: str, _model: str) -> str:
        return json.dumps({"verdicts": [
            {"index": 0, "is_correction": True, "idiom": None, "category": "factual", "reason": "r"},
            {"index": 1, "is_correction": False, "idiom": None, "category": None, "reason": "r"},
        ]})

    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    je.run_eval(_cases(), cfg, flow_dir=flow_dir, call_haiku_fn=oracle, sleep_fn=lambda _s: None)
    assert not data_dir.exists()


def test_classify_error_does_not_misfire_on_unrelated_words_containing_rate():
    """tacchi レビュー Should-6: "generate"/"accurate" 等の無関係語に "rate" で誤マッチしない。"""
    assert je._classify_error(RuntimeError("failed to generate output")) == "api_error"
    assert je._classify_error(RuntimeError("accurate enough")) == "api_error"
    assert je._classify_error(RuntimeError("429 too many requests")) == "rate_limited"
    assert je._classify_error(RuntimeError("rate limit exceeded")) == "rate_limited"
    assert je._classify_error(RuntimeError("service overloaded")) == "rate_limited"


def test_compute_confusion_summary_with_wilson_ci():
    rows = [
        {"status": "ok", "meta": {"expected": True, "predicted": True}},
        {"status": "ok", "meta": {"expected": True, "predicted": False}},
        {"status": "ok", "meta": {"expected": False, "predicted": True}},
        {"status": "ok", "meta": {"expected": False, "predicted": False}},
        {"status": "ok", "meta": {"expected": False, "predicted": False}},
    ]
    summary = je.compute_confusion_summary(rows)
    assert summary["tp"] == 1 and summary["fn"] == 1 and summary["fp"] == 1 and summary["tn"] == 2
    assert summary["recall"] == 0.5
    assert summary["precision"] == 0.5
    assert summary["specificity"] == pytest.approx(2 / 3)
    assert summary["recall_ci"][0] <= summary["recall"] <= summary["recall_ci"][1]


def test_compute_confusion_summary_empty_positives_returns_none_recall():
    rows = [{"status": "ok", "meta": {"expected": False, "predicted": False}}]
    summary = je.compute_confusion_summary(rows)
    assert summary["recall"] is None
    assert summary["recall_ci"] is None


def test_run_dry_does_not_write_any_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(je, "FLOW_DIR", tmp_path / "flow")
    cfg = je.RunConfig(variant="baseline", batch_size=30, reps=1)
    result = je.run_dry(_cases(), cfg)
    assert result["dry_run"] is True
    assert result["cases"] == 2
    assert result["positives"] == 1
    assert not (tmp_path / "flow").exists()
