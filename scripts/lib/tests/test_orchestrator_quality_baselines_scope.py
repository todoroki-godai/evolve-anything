"""#324 調査で判明した実測定バグの回帰テスト。

quality-baselines.jsonl は高頻度 global/plugin スキル（openspec-* / spec-keeper /
evolve-anything 自身のスキル等）のみを追跡し、PJ でスコープされていない
（quality_monitor.py の設計上の意図・quality-baselines.jsonl の project フィールドは常に
None）。それにも関わらず旧実装は監査対象 PJ を問わず同じ degraded count を
growth-state cache の issues_summary へ無条件注入しており、無関係な PJ 同士で
skill_quality_degraded_count が bit-exact に一致し measurement_bug の誤検知（#324）を
招いていた。

帰属は record 単位の出自で決める（リポジトリ単位の boolean にしない）:
  - 稼働中プラグイン本体のリポジトリ（manifest の `name` 一致）以外 → 一切載せない
  - 本体リポジトリ → `skill_path` の出自が plugin / plugin_self の record のみ載せる
    （`global` はどの PJ にも属さない環境グローバル成果物なので載せない）
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import audit.orchestrator as orch  # noqa: E402


def _write_manifest(root: Path, payload: str) -> None:
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(payload, encoding="utf-8")


class TestIsPluginSelfRepo:
    """manifest の存在だけでは判定にしない（他プラグイン repo への誤帰属を防ぐ）。"""

    def test_same_plugin_name_is_self(self, tmp_path):
        _write_manifest(tmp_path, json.dumps({"name": "my-plugin"}))
        with patch.object(orch, "PLUGIN_ROOT", tmp_path):
            assert orch._is_plugin_self_repo(tmp_path) is True

    def test_other_plugin_repo_is_not_self(self, tmp_path):
        self_root = tmp_path / "self"
        other = tmp_path / "other"
        _write_manifest(self_root, json.dumps({"name": "my-plugin"}))
        _write_manifest(other, json.dumps({"name": "another-plugin"}))
        with patch.object(orch, "PLUGIN_ROOT", self_root):
            assert orch._is_plugin_self_repo(other) is False

    def test_manifest_without_name_is_not_self(self, tmp_path):
        self_root = tmp_path / "self"
        target = tmp_path / "target"
        _write_manifest(self_root, json.dumps({"name": "my-plugin"}))
        _write_manifest(target, "{}")
        with patch.object(orch, "PLUGIN_ROOT", self_root):
            assert orch._is_plugin_self_repo(target) is False

    def test_malformed_manifest_is_not_self(self, tmp_path):
        self_root = tmp_path / "self"
        target = tmp_path / "target"
        _write_manifest(self_root, json.dumps({"name": "my-plugin"}))
        _write_manifest(target, "{not json")
        with patch.object(orch, "PLUGIN_ROOT", self_root):
            assert orch._is_plugin_self_repo(target) is False

    def test_ordinary_project_is_not_self(self, tmp_path):
        self_root = tmp_path / "self"
        _write_manifest(self_root, json.dumps({"name": "my-plugin"}))
        with patch.object(orch, "PLUGIN_ROOT", self_root):
            assert orch._is_plugin_self_repo(tmp_path / "plain") is False

    def test_unreadable_self_manifest_disables_attribution(self, tmp_path):
        """稼働中プラグインの manifest が読めなければ安全側に倒す（全 PJ で非帰属）。"""
        target = tmp_path / "target"
        _write_manifest(target, json.dumps({"name": "my-plugin"}))
        with patch.object(orch, "PLUGIN_ROOT", tmp_path / "missing"):
            assert orch._is_plugin_self_repo(target) is False


class TestScopeQualityBaselines:
    """record 単位の出自で絞る（global はどの PJ にも属さない）。"""

    _RECORDS = [
        {"skill_name": "plugin-skill", "skill_path": "/cache/plugin/SKILL.md"},
        {"skill_name": "global-skill", "skill_path": "/home/.claude/skills/g/SKILL.md"},
        {"skill_name": "no-path"},
    ]

    def _scope(self, tmp_path, *, is_self, origins=None):
        origins = origins or {
            "/cache/plugin/SKILL.md": "plugin",
            "/home/.claude/skills/g/SKILL.md": "global",
        }
        with patch.object(orch, "_is_plugin_self_repo", return_value=is_self), \
             patch("audit.classification.classify_artifact_origin",
                   side_effect=lambda p: origins.get(str(p), "custom")):
            return orch._scope_quality_baselines(list(self._RECORDS), tmp_path)

    def test_non_self_repo_gets_nothing(self, tmp_path):
        assert self._scope(tmp_path, is_self=False) is None

    def test_self_repo_keeps_only_plugin_origin(self, tmp_path):
        scoped = self._scope(tmp_path, is_self=True)
        assert [r["skill_name"] for r in scoped] == ["plugin-skill"]

    def test_plugin_self_origin_is_kept(self, tmp_path):
        scoped = self._scope(
            tmp_path,
            is_self=True,
            origins={"/cache/plugin/SKILL.md": "plugin_self",
                     "/home/.claude/skills/g/SKILL.md": "global"},
        )
        assert [r["skill_name"] for r in scoped] == ["plugin-skill"]

    def test_empty_input_passes_through(self, tmp_path):
        assert orch._scope_quality_baselines(None, tmp_path) is None
        assert orch._scope_quality_baselines([], tmp_path) == []


class TestRunAuditGrowthQualityScope:
    """growth-state cache への issues_summary 注入が PJ でゲートされることを E2E で確認。"""

    _DEGRADED_PLUGIN = [
        {"skill_name": "drop", "skill_path": "/cache/plugin/SKILL.md", "score": 0.9},
        {"skill_name": "drop", "skill_path": "/cache/plugin/SKILL.md", "score": 0.9},
        {"skill_name": "drop", "skill_path": "/cache/plugin/SKILL.md", "score": 0.6},
        {"skill_name": "drop", "skill_path": "/cache/plugin/SKILL.md", "score": 0.6},
    ]
    _DEGRADED_GLOBAL = [
        {**rec, "skill_path": "/home/.claude/skills/g/SKILL.md"} for rec in _DEGRADED_PLUGIN
    ]

    def _run_growth(self, tmp_path, *, is_plugin_self, baselines):
        captured = {}

        def _fake_build_growth_report(proj, *, skip_llm=False, issues_summary=None):
            captured["issues_summary"] = issues_summary
            return ["## \U0001f331 Growth Report (NFD)"]

        proj = tmp_path / "target-pj"
        proj.mkdir()
        self_root = tmp_path / "self-plugin"
        _write_manifest(self_root, json.dumps({"name": "my-plugin"}))
        if is_plugin_self:
            _write_manifest(proj, json.dumps({"name": "my-plugin"}))
        else:
            _write_manifest(proj, json.dumps({"name": "another-plugin"}))

        origins = {
            "/cache/plugin/SKILL.md": "plugin",
            "/home/.claude/skills/g/SKILL.md": "global",
        }
        with patch.object(orch, "PLUGIN_ROOT", self_root), \
             patch("audit.classification.classify_artifact_origin",
                   side_effect=lambda p: origins.get(str(p), "custom")), \
             patch.object(orch, "find_artifacts",
                           return_value={"skills": [], "rules": [], "memory": [], "claude_md": []}), \
             patch.object(orch, "check_line_limits", return_value=[]), \
             patch.object(orch, "check_python_source_budgets", return_value=[]), \
             patch.object(orch, "load_usage_data", return_value=[]), \
             patch.object(orch, "aggregate_usage", return_value={}), \
             patch.object(orch, "aggregate_plugin_usage", return_value={}), \
             patch.object(orch, "detect_duplicates_simple", return_value=[]), \
             patch.object(orch, "load_usage_registry", return_value={}), \
             patch.object(orch, "scope_advisory", return_value=[]), \
             patch.object(orch, "load_quality_baselines", return_value=baselines), \
             patch.object(orch, "_build_growth_report", side_effect=_fake_build_growth_report), \
             patch("telemetry_query.query_corrections", return_value=[]):
            orch.run_audit(project_dir=str(proj), skip_rescore=True, growth=True)
        return captured["issues_summary"]

    def test_other_plugin_repo_gets_zero_degraded_count(self, tmp_path):
        """別プラグインの repo を監査しても global/plugin baseline を持ち込まない。"""
        issues = self._run_growth(
            tmp_path, is_plugin_self=False, baselines=self._DEGRADED_PLUGIN
        )
        assert issues.skill_quality_degraded_count == 0

    def test_plugin_self_pj_keeps_plugin_origin_degraded(self, tmp_path):
        """本体リポジトリの監査では、自プラグイン由来 record の degraded が反映される。"""
        issues = self._run_growth(
            tmp_path, is_plugin_self=True, baselines=self._DEGRADED_PLUGIN
        )
        assert issues.skill_quality_degraded_count == 1

    def test_plugin_self_pj_drops_global_origin_degraded(self, tmp_path):
        """global スキルの劣化はどの PJ の growth-state にも載せない（実データ由来の 1 件）。"""
        issues = self._run_growth(
            tmp_path, is_plugin_self=True, baselines=self._DEGRADED_GLOBAL
        )
        assert issues.skill_quality_degraded_count == 0
