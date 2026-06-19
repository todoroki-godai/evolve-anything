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
from lib import evolve_reconcile as er  # #400: reconcile_skill_evolve_archive / batch_skip obs


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


# ── カテゴリ2 拡張: stderr 警告のキャプチャ（#341） ────


def test_detect_captured_warning():
    """result["warnings"] に記録された警告を runtime_errors が候補化する。

    scipy の RuntimeWarning(NaN) 等は phase 例外として throw されないが
    stderr に出る。evolve が warnings を result に記録し introspect が拾う。
    """
    result = _clean_result()
    result["warnings"] = [
        {
            "category": "RuntimeWarning",
            "message": "invalid value encountered in divide",
            "filename": "scripts/lib/reorganize.py",
            "lineno": 58,
        }
    ]
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["runtime_errors"]["candidates"]
    assert any(c["dedup_key"].startswith("runtime_warning:") for c in cands)
    warn_cand = next(c for c in cands if c["dedup_key"].startswith("runtime_warning:"))
    assert warn_cand["category"] == "runtime_error"
    assert "RuntimeWarning" in warn_cand["title"] or "RuntimeWarning" in warn_cand["body"]
    assert warn_cand["suggested_label"] == "bug"


def test_warnings_accept_plain_strings():
    """warnings が文字列リストでも受け付ける（後方互換 / 緩い記録経路）。"""
    result = _clean_result()
    result["warnings"] = ["RuntimeWarning: invalid value encountered in sqrt"]
    analysis = ei.analyze_evolve_result(result)
    assert any(
        c["dedup_key"].startswith("runtime_warning:")
        for c in analysis["runtime_errors"]["candidates"]
    )


def test_duplicate_warnings_collapse_to_one_candidate():
    """同じ root cause の警告（場所違い）は単一候補に潰れる。"""
    result = _clean_result()
    result["warnings"] = [
        {"category": "RuntimeWarning", "message": "invalid value encountered in divide", "filename": "a.py", "lineno": 1},
        {"category": "RuntimeWarning", "message": "invalid value encountered in divide", "filename": "b.py", "lineno": 99},
    ]
    analysis = ei.analyze_evolve_result(result)
    warn_cands = [c for c in analysis["runtime_errors"]["candidates"] if c["dedup_key"].startswith("runtime_warning:")]
    assert len(warn_cands) == 1


def test_no_warning_candidates_when_warnings_empty():
    """warnings が空 / 欠落でも runtime_warning 候補は出ない（false alarm 防止）。"""
    result = _clean_result()
    result["warnings"] = []
    analysis = ei.analyze_evolve_result(result)
    assert not any(
        c["dedup_key"].startswith("runtime_warning:")
        for c in analysis["runtime_errors"]["candidates"]
    )
    # warnings キー自体が無い clean result でも 0 件 ✓
    analysis2 = ei.analyze_evolve_result(_clean_result())
    assert analysis2["runtime_errors"]["candidates"] == []
    assert "✓" in analysis2["runtime_errors"]["summary_line"]


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


# ── カテゴリ1 拡張: auto_fixable への FP landing（#341） ─────


def _result_with_auto_fixable(items):
    result = _clean_result()
    result["phases"]["remediation"] = {
        "total_issues": len(items),
        "classified": {"auto_fixable": items, "proposable": [], "manual_required": []},
    }
    return result


def test_detect_fp_landing_in_auto_fixable_ssm_path():
    """confidence>=0.9 の auto_fixable に SSM 風論理パスの FP が入る → 矛盾候補。"""
    result = _result_with_auto_fixable([
        {"type": "stale_ref", "file": "CLAUDE.md", "confidence_score": 0.95,
         "detail": {"path": "/myapp/db/password"}},
    ])
    analysis = ei.analyze_evolve_result(result)
    cands = analysis["self_detection"]["candidates"]
    assert any(c["dedup_key"].startswith("self:fp_in_auto_fixable:") for c in cands)
    fp_cand = next(c for c in cands if c["dedup_key"].startswith("self:fp_in_auto_fixable:"))
    assert fp_cand["suggested_label"] == "bug"
    assert "ssm_style_path" in fp_cand["dedup_key"]


def test_detect_fp_landing_in_auto_fixable_tmp_path():
    """/tmp パスの FP が auto_fixable に landing → 検出。"""
    result = _result_with_auto_fixable([
        {"type": "stale_ref", "file": "CLAUDE.md", "confidence_score": 0.95,
         "detail": {"path": "/tmp/scratch/out.json"}},
    ])
    analysis = ei.analyze_evolve_result(result)
    assert any(
        c["dedup_key"].startswith("self:fp_in_auto_fixable:") and "tmp_path" in c["dedup_key"]
        for c in analysis["self_detection"]["candidates"]
    )


def test_no_fp_landing_when_confidence_below_threshold():
    """confidence < 0.9 では auto_fixable へ自動適用されないため検出対象外。"""
    result = _result_with_auto_fixable([
        {"type": "stale_ref", "file": "CLAUDE.md", "confidence_score": 0.5,
         "detail": {"path": "/tmp/scratch/out.json"}},
    ])
    analysis = ei.analyze_evolve_result(result)
    assert not any(
        c["dedup_key"].startswith("self:fp_in_auto_fixable:")
        for c in analysis["self_detection"]["candidates"]
    )


def test_no_fp_landing_for_legit_high_confidence_item():
    """正当な実ファイル参照は high confidence でも FP landing として誤検出しない。"""
    result = _result_with_auto_fixable([
        {"type": "stale_ref", "file": "CLAUDE.md", "confidence_score": 0.95,
         "detail": {"path": "scripts/lib/real_module.py"}},
    ])
    analysis = ei.analyze_evolve_result(result)
    assert not any(
        c["dedup_key"].startswith("self:fp_in_auto_fixable:")
        for c in analysis["self_detection"]["candidates"]
    )
    assert "✓" in analysis["self_detection"]["summary_line"]


def test_no_self_issue_when_split_and_archive_disjoint():
    result = _clean_result()
    result["phases"]["reorganize"] = {"skipped": False, "split_candidates": [{"skill_name": "a"}]}
    result["phases"]["prune"] = {"zero_invocations": ["b"], "retirement_candidates": [], "decay_candidates": []}
    analysis = ei.analyze_evolve_result(result)
    assert analysis["self_detection"]["candidates"] == []
    assert "✓" in analysis["self_detection"]["summary_line"]


# ── consistency drift が improvement_opportunities に合流（#377-5） ──


def test_consistency_candidate_flows_into_improvement():
    """usage0×suitability 矛盾が improvement_opportunities に surface される。"""
    result = _clean_result()
    result["phases"]["skill_evolve"] = {
        "assessments": [
            {"skill_name": "ghost", "suitability": "high",
             "telemetry_detail": {"usage_count": 0}},
        ],
    }
    analysis = ei.analyze_evolve_result(result)
    keys = [c["dedup_key"] for c in analysis["improvement_opportunities"]["candidates"]]
    assert any("usage_suitability" in k for k in keys)


def test_clean_result_keeps_improvement_zero():
    """健全な result では consistency 合流後も improvement は 0 件（regression guard）。"""
    analysis = ei.analyze_evolve_result(_clean_result())
    assert analysis["improvement_opportunities"]["candidates"] == []


# ── reconcile: split↔archive 相互排他（root cause fix / #301 #302） ──


def test_reconcile_suppresses_split_for_archived_skill():
    """archive 候補のスキルは split 候補から除外される（archive 優先）。"""
    result = _clean_result()
    result["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill_name": "big-skill", "line_count": 400}],
        "issues": [{"type": "split_candidate", "detail": {"skill_name": "big-skill"}}],
        "total_split_candidates": 1,
    }
    result["phases"]["prune"] = {
        "zero_invocations": ["big-skill"],
        "retirement_candidates": [],
        "decay_candidates": [],
    }
    summary = ei.reconcile_split_archive(result)
    assert summary["suppressed"] == ["big-skill"]
    reorg = result["phases"]["reorganize"]
    assert reorg["split_candidates"] == []
    assert reorg["total_split_candidates"] == 0
    assert reorg["issues"] == []
    assert reorg["split_suppressed_by_archive"] == ["big-skill"]


def test_reconcile_then_no_contradiction_detected():
    """reconcile 後は introspect が矛盾を検出しない（root cause が消える）。"""
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
    ei.reconcile_split_archive(result)
    analysis = ei.analyze_evolve_result(result)
    assert analysis["self_detection"]["candidates"] == []
    assert "✓" in analysis["self_detection"]["summary_line"]


def test_reconcile_keeps_split_when_not_archived():
    """archive 対象でない split 候補は維持される。"""
    result = _clean_result()
    result["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill_name": "alive"}, {"skill_name": "dead"}],
    }
    result["phases"]["prune"] = {
        "zero_invocations": ["dead"],
        "retirement_candidates": [],
        "decay_candidates": [],
    }
    summary = ei.reconcile_split_archive(result)
    assert summary["suppressed"] == ["dead"]
    names = [ei._skill_name(sc) for sc in result["phases"]["reorganize"]["split_candidates"]]
    assert names == ["alive"]


def test_reconcile_noop_when_reorganize_skipped():
    result = _clean_result()  # reorganize skipped by default
    summary = ei.reconcile_split_archive(result)
    assert summary["suppressed"] == []


def test_reconcile_noop_when_no_archive_candidates():
    result = _clean_result()
    result["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill_name": "big"}],
    }
    summary = ei.reconcile_split_archive(result)
    assert summary["suppressed"] == []
    assert summary["remaining_split"] == 1


def test_reconcile_handles_retirement_and_decay_keys():
    """retirement_candidates / decay_candidates も archive 寄りとして扱う。"""
    result = _clean_result()
    result["phases"]["reorganize"] = {
        "skipped": False,
        "split_candidates": [{"skill_name": "r"}, {"skill_name": "d"}],
    }
    result["phases"]["prune"] = {
        "zero_invocations": [],
        "retirement_candidates": [{"skill_name": "r"}],
        "decay_candidates": [{"skill_name": "d"}],
    }
    summary = ei.reconcile_split_archive(result)
    assert summary["suppressed"] == ["d", "r"]
    assert result["phases"]["reorganize"]["split_candidates"] == []


# ── reconcile: skill_evolve↔archive 相互排他（#400 バグ#2） ──


def _result_with_skill_evolve_and_archive():
    """ghost を high 適性で自己進化提案しつつ prune で archive 候補にもする矛盾 result。"""
    result = _clean_result()
    result["phases"]["skill_evolve"] = {
        "assessments": [
            {"skill_name": "ghost", "skill_dir": "/s/ghost", "suitability": "high"},
            {"skill_name": "alive", "skill_dir": "/s/alive", "suitability": "medium"},
        ],
        "high_suitability": 1,
        "medium_suitability": 1,
    }
    result["phases"]["prune"] = {
        "zero_invocations": ["ghost"],
        "retirement_candidates": [],
        "decay_candidates": [],
    }
    result["phases"]["remediation"] = {
        "classified": {
            "proposable_custom": [
                {"type": "skill_evolve_candidate", "detail": {"skill_name": "ghost"}},
                {"type": "skill_evolve_candidate", "detail": {"skill_name": "alive"}},
            ],
            "proposable_custom_individual": [
                {"type": "skill_evolve_candidate", "detail": {"skill_name": "ghost"}},
            ],
            "proposable_custom_batch_skip": [
                {"type": "skill_evolve_candidate", "detail": {"skill_name": "alive"}},
            ],
            "proposable": [
                {"type": "skill_evolve_candidate", "detail": {"skill_name": "ghost"}},
            ],
        },
        "proposable_custom": 2,
        "proposable_custom_individual": 1,
        "proposable_custom_batch_skip": 1,
        "proposable": 1,
    }
    return result


def test_reconcile_skill_evolve_suppresses_archived_skill():
    """archive 候補のスキルは skill_evolve 提案から除外される（archive 優先）。"""
    result = _result_with_skill_evolve_and_archive()
    summary = er.reconcile_skill_evolve_archive(result)
    assert summary["suppressed"] == ["ghost"]
    se = result["phases"]["skill_evolve"]
    # ghost は降格、alive は維持
    suit = {a["skill_name"]: a["suitability"] for a in se["assessments"]}
    assert suit["ghost"] == "suppressed_by_archive"
    assert suit["alive"] == "medium"
    assert se["high_suitability"] == 0  # ghost が抜けた
    assert se["medium_suitability"] == 1
    assert se["evolve_suppressed_by_archive"] == ["ghost"]


def test_reconcile_skill_evolve_removes_remediation_issues():
    """remediation の skill_evolve issue も archive 対象スキル分を除外し count を整合させる。"""
    result = _result_with_skill_evolve_and_archive()
    er.reconcile_skill_evolve_archive(result)
    cls = result["phases"]["remediation"]["classified"]
    names_in = lambda key: [i["detail"]["skill_name"] for i in cls[key]]
    assert names_in("proposable_custom") == ["alive"]
    assert names_in("proposable_custom_individual") == []
    assert names_in("proposable_custom_batch_skip") == ["alive"]
    assert names_in("proposable") == []
    rem = result["phases"]["remediation"]
    assert rem["proposable_custom"] == 1
    assert rem["proposable_custom_individual"] == 0
    assert rem["proposable_custom_batch_skip"] == 1
    assert rem["proposable"] == 0


def test_reconcile_skill_evolve_excluded_from_emit_after_reconcile():
    """reconcile 後の assessments を emit_decisions が拾わない（母集団に矛盾候補を入れない）。"""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "lib"))
    import evolve_decisions as ed
    result = _result_with_skill_evolve_and_archive()
    er.reconcile_skill_evolve_archive(result)
    cands = ed._extract_candidates(result)
    names = {c["skill_name"] for c in cands}
    assert "ghost" not in names  # archive 候補は emit 対象外
    assert "alive" in names


def test_reconcile_skill_evolve_noop_without_archive():
    result = _clean_result()
    result["phases"]["skill_evolve"] = {
        "assessments": [{"skill_name": "x", "suitability": "high"}],
        "high_suitability": 1, "medium_suitability": 0,
    }
    summary = er.reconcile_skill_evolve_archive(result)
    assert summary["suppressed"] == []
    assert result["phases"]["skill_evolve"]["assessments"][0]["suitability"] == "high"


def test_reconcile_skill_evolve_noop_when_phase_missing():
    summary = er.reconcile_skill_evolve_archive(_clean_result())
    assert summary["suppressed"] == []


# ── observability: remediation batch_skip 強制 surface（#400 バグ#6） ──


def test_batch_skip_observability_surfaces_count_when_nonzero():
    result = _clean_result()
    result["phases"]["remediation"] = {"proposable_custom_batch_skip": 7}
    lines = er.build_remediation_batch_skip_observability(result)
    assert lines is not None
    assert len(lines) == 1
    assert "7 件" in lines[0]
    assert "batch_skip" in lines[0]


def test_batch_skip_observability_zero_still_surfaces():
    """0 件でも ✓ を残す（silence != evaluated）。"""
    result = _clean_result()
    result["phases"]["remediation"] = {"proposable_custom_batch_skip": 0}
    lines = er.build_remediation_batch_skip_observability(result)
    assert lines == ["✓ remediation batch_skip: 0 件（まとめスキップ対象なし）"]


def test_batch_skip_observability_none_when_no_remediation():
    """remediation phase が無い / error は非該当（None）。"""
    result = _clean_result()
    result["phases"].pop("remediation", None)
    assert er.build_remediation_batch_skip_observability(result) is None
    result["phases"]["remediation"] = {"error": "boom"}
    assert er.build_remediation_batch_skip_observability(result) is None


def test_batch_skip_observability_handles_non_int():
    result = _clean_result()
    result["phases"]["remediation"] = {"proposable_custom_batch_skip": None}
    lines = er.build_remediation_batch_skip_observability(result)
    assert lines == ["✓ remediation batch_skip: 0 件（まとめスキップ対象なし）"]


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


# ── closed issue regression（#33） ───────────────────


def test_closed_marker_match_is_regression_not_duplicate():
    """close 済み issue にマーカーが一致したら、dup でなく unique（regression）として扱う。"""
    c = _candidate()
    existing = [{
        "number": 42,
        "state": "CLOSED",
        "title": "別タイトル",
        "body": ei.render_issue_body(c),
    }]
    out = ei.filter_duplicates([c], existing)
    # 再発なので新規起票（unique）に残る
    assert len(out["unique"]) == 1
    assert out["duplicates"] == []
    # regression として呼び出し側に surface される
    assert len(out["regressions"]) == 1
    assert out["regressions"][0]["existing_number"] == 42
    assert out["regressions"][0]["dedup_key"] == c["dedup_key"]


def test_open_marker_match_still_duplicate_when_also_closed_exists():
    """open と closed の両方に同一マーカーがあれば open 優先で dup（既存挙動を守る）。"""
    c = _candidate()
    existing = [
        {"number": 7, "state": "CLOSED", "title": "旧", "body": ei.render_issue_body(c)},
        {"number": 9, "state": "OPEN", "title": "現役", "body": ei.render_issue_body(c)},
    ]
    out = ei.filter_duplicates([c], existing)
    assert out["unique"] == []
    assert len(out["duplicates"]) == 1
    assert out["duplicates"][0]["existing_number"] == 9
    assert out["regressions"] == []


def test_missing_state_defaults_to_open_for_backward_compat():
    """state 欠落（旧 gh 出力）は open 扱いで従来通り dup（後方互換）。"""
    c = _candidate()
    existing = [{"number": 10, "title": "別タイトル", "body": ei.render_issue_body(c)}]
    out = ei.filter_duplicates([c], existing)
    assert out["unique"] == []
    assert len(out["duplicates"]) == 1
    assert out["regressions"] == []


def test_lowercase_state_closed_is_recognized():
    """gh の state 表記揺れ（小文字 'closed'）も regression として認識する。"""
    c = _candidate()
    existing = [{
        "number": 13,
        "state": "closed",
        "title": "別タイトル",
        "body": ei.render_issue_body(c),
    }]
    out = ei.filter_duplicates([c], existing)
    assert len(out["regressions"]) == 1
    assert out["regressions"][0]["existing_number"] == 13


def test_regression_body_prepends_backlink():
    """regression render は body 冒頭に前回 closed issue へのバックリンクを差し込む。"""
    c = _candidate()
    rendered = ei.render_regression_body(c, 42)
    # 冒頭に regression 文脈
    assert rendered.splitlines()[0].startswith("> ")
    assert "#42" in rendered.splitlines()[0]
    # 元本文も残る
    assert c["body"] in rendered
    # dedup マーカーも従来通り末尾に入る（再発も dedup 対象に保つ）
    assert ei.extract_marker(rendered) == c["dedup_key"]


def test_closed_title_similarity_is_not_regression():
    """マーカー無し closed の title 類似は regression 扱いしない（誤バックリンク防止）。"""
    c = _candidate(title="[evolve introspect] `discover` フェーズで例外: KeyError")
    existing = [{
        "number": 5,
        "state": "CLOSED",
        "title": "[evolve introspect] `discover` フェーズで例外: KeyError missed",
        "body": "marker なしで手動 close された既存 issue",
    }]
    out = ei.filter_duplicates([c], existing)
    # marker が無いので前歴に確実に紐づけられない → 通常の unique（誤リンクしない）
    assert len(out["unique"]) == 1
    assert out["regressions"] == []
    assert out["duplicates"] == []


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
