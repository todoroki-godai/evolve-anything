"""#324 調査で判明した実測定バグの回帰テスト。

quality-baselines.jsonl は高頻度 global/plugin スキル（openspec-* / spec-keeper /
evolve-anything 自身のスキル等）のみを追跡し、PJ でスコープされていない
（quality_monitor.py の設計上の意図・quality-baselines.jsonl の project フィールドは常に
None）。それにも関わらず旧実装は監査対象 PJ を問わず同じ degraded count を
growth-state cache の issues_summary へ無条件注入しており、無関係な PJ 同士で
skill_quality_degraded_count が bit-exact に一致し measurement_bug の誤検知（#324）を
招いていた。growth-state cache へは、プラグイン本体（`.claude-plugin/plugin.json` を
持つ PJ = evolve-anything 自身または開発用 worktree）を監査している時のみ反映する。
"""
import sys
from pathlib import Path
from unittest.mock import patch

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import audit.orchestrator as orch  # noqa: E402


class TestQualityBaselinesApplyTo:
    def test_plugin_self_repo_applies(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
        assert orch._quality_baselines_apply_to(tmp_path) is True

    def test_ordinary_project_does_not_apply(self, tmp_path):
        assert orch._quality_baselines_apply_to(tmp_path) is False

    def test_plugin_json_dir_without_file_does_not_apply(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        assert orch._quality_baselines_apply_to(tmp_path) is False


class TestRunAuditGrowthQualityScope:
    """growth-state cache への issues_summary 注入が PJ でゲートされることを E2E で確認。"""

    _DEGRADED_BASELINES = [
        {"skill_name": "drop", "score": 0.9},
        {"skill_name": "drop", "score": 0.9},
        {"skill_name": "drop", "score": 0.6},
        {"skill_name": "drop", "score": 0.6},
    ]

    def _run_growth(self, tmp_path, *, is_plugin_self):
        captured = {}

        def _fake_build_growth_report(proj, *, skip_llm=False, issues_summary=None):
            captured["issues_summary"] = issues_summary
            return ["## \U0001f331 Growth Report (NFD)"]

        proj = tmp_path / "target-pj"
        proj.mkdir()
        if is_plugin_self:
            (proj / ".claude-plugin").mkdir()
            (proj / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")

        with patch.object(orch, "find_artifacts",
                           return_value={"skills": [], "rules": [], "memory": [], "claude_md": []}), \
             patch.object(orch, "check_line_limits", return_value=[]), \
             patch.object(orch, "check_python_source_budgets", return_value=[]), \
             patch.object(orch, "load_usage_data", return_value=[]), \
             patch.object(orch, "aggregate_usage", return_value={}), \
             patch.object(orch, "aggregate_plugin_usage", return_value={}), \
             patch.object(orch, "detect_duplicates_simple", return_value=[]), \
             patch.object(orch, "load_usage_registry", return_value={}), \
             patch.object(orch, "scope_advisory", return_value=[]), \
             patch.object(orch, "load_quality_baselines", return_value=self._DEGRADED_BASELINES), \
             patch.object(orch, "_build_growth_report", side_effect=_fake_build_growth_report), \
             patch("telemetry_query.query_corrections", return_value=[]):
            orch.run_audit(project_dir=str(proj), skip_rescore=True, growth=True)
        return captured["issues_summary"]

    def test_ordinary_pj_gets_zero_degraded_count(self, tmp_path):
        """他 PJ の監査では、無関係な global/plugin スキルの degraded count を持ち込まない。"""
        issues = self._run_growth(tmp_path, is_plugin_self=False)
        assert issues.skill_quality_degraded_count == 0

    def test_plugin_self_pj_keeps_degraded_count(self, tmp_path):
        """プラグイン本体の監査では、従来どおり degraded count が反映される。"""
        issues = self._run_growth(tmp_path, is_plugin_self=True)
        assert issues.skill_quality_degraded_count == 1
