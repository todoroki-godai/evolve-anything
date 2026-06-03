"""evolve_introspect のテスト（決定論・LLM 非依存 / #299）。

evolve 実行後に evolve の result dict を自己解析し、
1) 自己検出（提案の質） 2) 実行時エラー/誤検出 3) 改善余地
の3カテゴリで GitHub issue 候補を生成する。検出はすべて決定論で、
0 件でも「評価したが該当なし ✓」の summary_line を必ず返す（silence != evaluated）。
起票経路は gh 依存のため本テストでは扱わず、dedup・候補生成のみを検証する。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib import evolve_introspect as ei


# ── 共通フィクスチャ ────────────────────────────────


def _clean_result() -> dict:
    """エラー・矛盾・改善余地のない健全な evolve result。"""
    return {
        "timestamp": "2026-06-03T00:00:00+00:00",
        "dry_run": False,
        "env_tier": "mature",
        "phases": {
            "observe": {"sufficient": True},
            "discover": {"missed_skill_opportunities": []},
            "remediation": {
                "total_issues": 0,
                "classified": {"auto_fixable": [], "proposable": [], "manual_required": []},
            },
            "reorganize": {"skipped": True, "split_candidates": []},
            "prune": {"zero_invocations": [], "retirement_candidates": [], "decay_candidates": []},
            "self_evolution": {
                "skipped": False,
                "false_positives": {"high_confidence_count": 0, "systematic_flags": {}},
                "regression": {"has_regression": False, "regressions": {}},
            },
        },
        "observability": {"glossary_drift": ["✓ 構造 drift なし"]},
    }


# ── 構造契約: 3カテゴリ常在 + summary_line 常在 ───────


def test_analyze_returns_three_categories_always():
    analysis = ei.analyze_evolve_result(_clean_result())
    assert set(["self_detection", "runtime_errors", "improvement_opportunities"]).issubset(analysis)
    for key in ("self_detection", "runtime_errors", "improvement_opportunities"):
        section = analysis[key]
        assert "candidates" in section
        assert "summary_line" in section
        # clean なので 0 件 + ✓ を必ず残す（沈黙禁止）
        assert section["candidates"] == []
        assert "✓" in section["summary_line"]


def test_total_candidates_zero_on_clean():
    analysis = ei.analyze_evolve_result(_clean_result())
    assert analysis["total_candidates"] == 0


def test_empty_result_does_not_crash():
    analysis = ei.analyze_evolve_result({})
    assert analysis["total_candidates"] == 0
    for key in ("self_detection", "runtime_errors", "improvement_opportunities"):
        assert "✓" in analysis[key]["summary_line"]


# ── カテゴリ2: 実行時エラー / 誤検出 ─────────────────


def test_detect_phase_exception():
    result = _clean_result()
    result["phases"]["discover"] = {"error": "KeyError: 'missed_skill_opportunities'"}
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["runtime_errors"]["candidates"]
    assert len(cands) == 1
    c = cands[0]
    assert c["category"] == "runtime_error"
    assert "discover" in c["title"]
    assert c["suggested_label"] == "bug"
    assert c["severity"] == "high"
    assert c["dedup_key"].startswith("runtime_error:discover:")


def test_detect_observability_error():
    result = _clean_result()
    result["observability"] = {"error": "collect_observability failed: ImportError"}
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["runtime_errors"]["candidates"]
    assert any("observability" in c["dedup_key"] for c in cands)


def test_skipped_phase_without_error_is_not_runtime_error():
    result = _clean_result()
    result["phases"]["fitness_evolution"] = {"status": "insufficient_data", "skipped": True}
    analysis = ei.analyze_evolve_result(result)
    assert analysis["runtime_errors"]["candidates"] == []


def test_runtime_error_signature_is_path_and_digit_stable():
    """同じ root cause（行番号/パスだけ違う）は同一 dedup_key になる。"""
    r1 = _clean_result()
    r1["phases"]["audit"] = {"error": "FileNotFoundError: /tmp/abc123/x.py line 42"}
    r2 = _clean_result()
    r2["phases"]["audit"] = {"error": "FileNotFoundError: /tmp/zzz999/x.py line 7"}
    k1 = ei.analyze_evolve_result(r1)["runtime_errors"]["candidates"][0]["dedup_key"]
    k2 = ei.analyze_evolve_result(r2)["runtime_errors"]["candidates"][0]["dedup_key"]
    assert k1 == k2


# ── カテゴリ1: 自己検出（提案の質） ──────────────────


def test_detect_split_archive_contradiction():
    result = _clean_result()
    result["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill_name": "big-skill"}],
    }
    result["phases"]["prune"] = {
        "zero_invocations": ["big-skill"],
        "retirement_candidates": [],
        "decay_candidates": [],
    }
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["self_detection"]["candidates"]
    assert any("big-skill" in c["dedup_key"] and "split_archive" in c["dedup_key"] for c in cands)
    assert all(c["suggested_label"] == "bug" for c in cands)


def test_detect_line_budget_conflict():
    """line-limit 超過ファイルに content 追加系の fix を提案 = budget 悪化誘発。"""
    result = _clean_result()
    result["phases"]["remediation"] = {
        "total_issues": 2,
        "classified": {
            "auto_fixable": [
                {"type": "claudemd_missing_section", "file": "scripts/lib/big.py"},
            ],
            "proposable": [
                {"type": "line_limit_violation", "file": "scripts/lib/big.py"},
            ],
            "manual_required": [],
        },
    }
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["self_detection"]["candidates"]
    assert any("line_budget_conflict" in c["dedup_key"] and "big.py" in c["dedup_key"] for c in cands)


def test_no_self_issue_when_split_and_archive_disjoint():
    result = _clean_result()
    result["phases"]["reorganize"] = {"skipped": False, "split_candidates": [{"skill_name": "a"}]}
    result["phases"]["prune"] = {"zero_invocations": ["b"], "retirement_candidates": [], "decay_candidates": []}
    analysis = ei.analyze_evolve_result(result)
    assert analysis["self_detection"]["candidates"] == []
    assert "✓" in analysis["self_detection"]["summary_line"]


# ── カテゴリ3: 改善余地 ─────────────────────────────


def test_detect_systematic_rejection():
    result = _clean_result()
    result["phases"]["self_evolution"]["false_positives"]["systematic_flags"] = {
        "stale_ref": 7,
    }
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["improvement_opportunities"]["candidates"]
    assert any("systematic_rejection" in c["dedup_key"] and "stale_ref" in c["dedup_key"] for c in cands)
    assert all(c["suggested_label"] == "enhancement" for c in cands)


def test_detect_calibration_regression():
    result = _clean_result()
    result["phases"]["self_evolution"]["regression"] = {
        "has_regression": True,
        "regressions": {"orphan_rule": {"delta": -0.2}},
    }
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["improvement_opportunities"]["candidates"]
    assert any("calibration_regression" in c["dedup_key"] and "orphan_rule" in c["dedup_key"] for c in cands)


def test_no_improvement_when_self_evolution_skipped():
    result = _clean_result()
    result["phases"]["self_evolution"] = {"skipped": True, "reason": "insufficient"}
    analysis = ei.analyze_evolve_result(result)
    assert analysis["improvement_opportunities"]["candidates"] == []
    assert "✓" in analysis["improvement_opportunities"]["summary_line"]


# ── dedup ────────────────────────────────────────────


def _candidate(dedup_key="runtime_error:discover:keyerror", title="[evolve introspect] discover フェーズで例外"):
    return {
        "category": "runtime_error",
        "title": title,
        "body": "本文",
        "suggested_label": "bug",
        "dedup_key": dedup_key,
        "severity": "high",
    }


def test_render_body_embeds_marker_roundtrip():
    c = _candidate()
    body = ei.render_issue_body(c)
    assert ei.extract_marker(body) == c["dedup_key"]


def test_dedup_by_marker_exact_match():
    c = _candidate()
    existing = [{"number": 10, "title": "別タイトル", "body": ei.render_issue_body(c)}]
    out = ei.filter_duplicates([c], existing)
    assert out["unique"] == []
    assert len(out["duplicates"]) == 1
    assert out["duplicates"][0]["existing_number"] == 10


def test_dedup_by_title_similarity():
    c = _candidate(title="[evolve introspect] `discover` フェーズで例外: KeyError")
    existing = [{
        "number": 5,
        "title": "[evolve introspect] `discover` フェーズで例外: KeyError missed",
        "body": "marker なしで手動起票された既存 issue",
    }]
    out = ei.filter_duplicates([c], existing)
    assert out["unique"] == []
    assert len(out["duplicates"]) == 1


def test_unique_passes_through():
    c = _candidate()
    existing = [{"number": 1, "title": "全く無関係なバグ報告", "body": "別の話"}]
    out = ei.filter_duplicates([c], existing)
    assert len(out["unique"]) == 1
    assert out["duplicates"] == []


def test_dedup_empty_existing():
    c = _candidate()
    out = ei.filter_duplicates([c], [])
    assert len(out["unique"]) == 1


# ── summary_lines（surface 用） ──────────────────────


def test_flatten_candidates_collects_all_three_categories():
    result = _clean_result()
    result["phases"]["discover"] = {"error": "boom"}
    result["phases"]["reorganize"] = {"skipped": False, "split_candidates": [{"skill_name": "x"}]}
    result["phases"]["prune"] = {"zero_invocations": ["x"], "retirement_candidates": [], "decay_candidates": []}
    result["phases"]["self_evolution"]["false_positives"]["systematic_flags"] = {"orphan_rule": 4}
    analysis = ei.analyze_evolve_result(result)
    flat = ei.flatten_candidates(analysis)
    cats = {c["category"] for c in flat}
    assert cats == {"runtime_error", "self_detection", "improvement"}
    assert len(flat) == analysis["total_candidates"]


def test_flatten_candidates_empty_on_clean():
    assert ei.flatten_candidates(ei.analyze_evolve_result(_clean_result())) == []


def test_summary_lines_lists_all_categories():
    lines = ei.summary_lines(ei.analyze_evolve_result(_clean_result()))
    text = "\n".join(lines)
    assert "自己検出" in text or "self" in text.lower()
    assert "実行時エラー" in text or "runtime" in text.lower()
    assert "改善余地" in text or "improvement" in text.lower()
