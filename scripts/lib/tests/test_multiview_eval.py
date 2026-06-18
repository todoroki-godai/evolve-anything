"""多視点評価（multiview_eval）の決定論分類テスト（#564）。

evolve 提案の評価を「単一の accept/reject」から「多視点」へ拡張する薄い集約レイヤ。
既存3部品（chaos / outcome_attribution / negative_transfer）の結果を受け取り、各 evolve
対象スキルを4視点ラベルに **決定論的** に分類する純関数を検証する。LLM 非依存。

データ契約（各ソース関数を Read で確認済み）:
  - chaos.compute_chaos_score → importance_ranking[]: {name, layer, delta_score, criticality}
                                single_point_of_failure[]: {name, layer, delta_score}
  - outcome_attribution.attribute_outcomes → {skill: {first_try_success, rework,
                                              n_sessions, degraded}}
  - usage.compute_negative_transfer → [{skill_name, delta_score, negative_transfer,
                                        before_score, after_score}]

純関数のみ（store 再読込なし = dry-run 安全）。monkeypatch は使わず in-memory 入力で完結する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import multiview_eval as mv  # noqa: E402


# ---------- 単一スキルの分類 ----------

class TestClassifySkillMultiview:
    def test_no_signals_is_unknown(self):
        """3部品とも該当レコードが無ければ unknown（沈黙でなく「評価不能」を明示）。"""
        res = mv.classify_skill_multiview(
            skill="foo",
            chaos_entry=None,
            outcome_attr=None,
            neg_transfer=None,
        )
        assert res["skill"] == "foo"
        assert res["labels"] == ["unknown"]
        assert res["degraded"] is True

    def test_reusable_improvement_when_important_and_good_outcome(self):
        """chaos で効いている（important/critical）かつアウトカム良好 → 再利用可能な改善。"""
        res = mv.classify_skill_multiview(
            skill="foo",
            chaos_entry={"name": "foo", "layer": "skills",
                         "delta_score": 0.12, "criticality": "critical"},
            outcome_attr={"first_try_success": 0.9, "rework": 0.0,
                          "n_sessions": 8, "degraded": False},
            neg_transfer=None,
        )
        assert "reusable_improvement" in res["labels"]
        assert res["degraded"] is False

    def test_regression_risk_when_spof(self):
        """chaos の delta_score が SPOF 閾値以上（除去で大きく劣化）→ 退行リスク。"""
        res = mv.classify_skill_multiview(
            skill="bar",
            chaos_entry={"name": "bar", "layer": "skills",
                         "delta_score": 0.20, "criticality": "critical"},
            outcome_attr=None,
            neg_transfer=None,
        )
        assert "regression_risk" in res["labels"]

    def test_regression_risk_when_negative_transfer(self):
        """negative_transfer フラグが立っている → 退行リスク。"""
        res = mv.classify_skill_multiview(
            skill="baz",
            chaos_entry=None,
            outcome_attr=None,
            neg_transfer={"skill_name": "baz", "delta_score": -0.2,
                          "negative_transfer": True,
                          "before_score": 0.8, "after_score": 0.6},
        )
        assert "regression_risk" in res["labels"]

    def test_overfit_suspect_when_low_success_few_sessions(self):
        """少数セッションでアウトカムが悪い（first_try_success 低い）→ 過学習疑い。"""
        res = mv.classify_skill_multiview(
            skill="qux",
            chaos_entry=None,
            outcome_attr={"first_try_success": 0.2, "rework": 0.0,
                          "n_sessions": 2, "degraded": False},
            neg_transfer=None,
        )
        assert "overfit_suspect" in res["labels"]

    def test_no_overfit_when_low_success_but_many_sessions(self):
        """セッション数が十分あれば low success でも過学習疑いにしない（母集団十分）。"""
        res = mv.classify_skill_multiview(
            skill="qux",
            chaos_entry=None,
            outcome_attr={"first_try_success": 0.2, "rework": 0.0,
                          "n_sessions": 50, "degraded": False},
            neg_transfer=None,
        )
        assert "overfit_suspect" not in res["labels"]

    def test_cost_increase_when_high_rework(self):
        """rework 率が高い（手戻り多い）→ コスト増。"""
        res = mv.classify_skill_multiview(
            skill="quux",
            chaos_entry=None,
            outcome_attr={"first_try_success": 0.8, "rework": 0.6,
                          "n_sessions": 6, "degraded": False},
            neg_transfer=None,
        )
        assert "cost_increase" in res["labels"]

    def test_degraded_outcome_does_not_classify(self):
        """outcome_attr が degraded（テレメトリ不足）なら outcome 由来ラベルを出さない。"""
        res = mv.classify_skill_multiview(
            skill="foo",
            chaos_entry=None,
            outcome_attr={"first_try_success": None, "rework": None,
                          "n_sessions": 0, "degraded": True},
            neg_transfer=None,
        )
        # outcome 由来のラベル（overfit/cost）は出ない
        assert "overfit_suspect" not in res["labels"]
        assert "cost_increase" not in res["labels"]

    def test_multiple_labels_can_coexist(self):
        """複数視点が同時成立しうる（退行リスク + コスト増）。"""
        res = mv.classify_skill_multiview(
            skill="multi",
            chaos_entry={"name": "multi", "layer": "skills",
                         "delta_score": 0.20, "criticality": "critical"},
            outcome_attr={"first_try_success": 0.8, "rework": 0.7,
                          "n_sessions": 6, "degraded": False},
            neg_transfer=None,
        )
        assert "regression_risk" in res["labels"]
        assert "cost_increase" in res["labels"]

    def test_evidence_is_attached(self):
        """各ラベルの根拠 evidence が付与される（数字に意味を添える）。"""
        res = mv.classify_skill_multiview(
            skill="foo",
            chaos_entry={"name": "foo", "layer": "skills",
                         "delta_score": 0.12, "criticality": "critical"},
            outcome_attr={"first_try_success": 0.9, "rework": 0.0,
                          "n_sessions": 8, "degraded": False},
            neg_transfer=None,
        )
        ev = res["evidence"]
        assert ev["chaos_delta"] == 0.12
        assert ev["first_try_success"] == 0.9
        assert ev["n_sessions"] == 8


# ---------- 複数スキルの集約 ----------

class TestClassifyMultiview:
    def _chaos(self, entries):
        return {
            "importance_ranking": entries,
            "single_point_of_failure": [
                e for e in entries if e["delta_score"] >= 0.15
            ],
            "robustness_score": 0.5,
            "baseline_coherence": 0.8,
            "max_delta_score": 0.2,
            "elements_tested": len(entries),
        }

    def test_empty_targets_returns_empty(self):
        res = mv.classify_multiview(
            target_skills=[],
            chaos_result=self._chaos([]),
            outcome_attribution={},
            negative_transfer=[],
        )
        assert res == {}

    def test_joins_three_sources_by_skill(self):
        """3 部品を skill 名で join して各スキルを分類する。"""
        chaos = self._chaos([
            {"name": "alpha", "layer": "skills",
             "delta_score": 0.20, "criticality": "critical"},
            {"name": "beta", "layer": "skills",
             "delta_score": 0.01, "criticality": "low"},
        ])
        attribution = {
            "alpha": {"first_try_success": 0.9, "rework": 0.0,
                      "n_sessions": 8, "degraded": False},
            "beta": {"first_try_success": 0.2, "rework": 0.5,
                     "n_sessions": 2, "degraded": False},
        }
        neg = [{"skill_name": "alpha", "delta_score": -0.2,
                "negative_transfer": True, "before_score": 0.8,
                "after_score": 0.6}]
        res = mv.classify_multiview(
            target_skills=["alpha", "beta"],
            chaos_result=chaos,
            outcome_attribution=attribution,
            negative_transfer=neg,
        )
        assert set(res.keys()) == {"alpha", "beta"}
        # alpha: SPOF + negative_transfer → regression_risk
        assert "regression_risk" in res["alpha"]["labels"]
        # beta: low success + few sessions → overfit, high rework → cost_increase
        assert "overfit_suspect" in res["beta"]["labels"]
        assert "cost_increase" in res["beta"]["labels"]

    def test_target_not_in_any_source_is_unknown(self):
        """対象スキルがどの部品にも現れない → unknown。"""
        res = mv.classify_multiview(
            target_skills=["ghost"],
            chaos_result=self._chaos([]),
            outcome_attribution={},
            negative_transfer=[],
        )
        assert res["ghost"]["labels"] == ["unknown"]
        assert res["ghost"]["degraded"] is True

    def test_handles_missing_chaos_result_gracefully(self):
        """chaos_result が None でも落ちず、他部品の信号で分類する。"""
        res = mv.classify_multiview(
            target_skills=["alpha"],
            chaos_result=None,
            outcome_attribution={
                "alpha": {"first_try_success": 0.2, "rework": 0.0,
                          "n_sessions": 2, "degraded": False},
            },
            negative_transfer=[],
        )
        assert "overfit_suspect" in res["alpha"]["labels"]


# ---------- ラベルの定数契約 ----------

def test_label_constants_are_the_four_views():
    """4視点ラベルが定数として公開され、想定通り（仕様の単一ソース）。"""
    assert mv.LABEL_REUSABLE == "reusable_improvement"
    assert mv.LABEL_OVERFIT == "overfit_suspect"
    assert mv.LABEL_REGRESSION == "regression_risk"
    assert mv.LABEL_COST == "cost_increase"


def test_label_jp_descriptions_exist():
    """各ラベルに日本語説明があり、report で意味を添えられる。"""
    for label in (mv.LABEL_REUSABLE, mv.LABEL_OVERFIT,
                  mv.LABEL_REGRESSION, mv.LABEL_COST):
        assert label in mv.LABEL_DESCRIPTIONS
        assert mv.LABEL_DESCRIPTIONS[label]  # 非空


# ---------- chaos しきい値の複製 drift 検出ゲート ----------

def _load_fitness_config():
    """fitness/config.py を直接ロードする（既存 test_fitness_config.py の踏襲）。

    config.py を通常 import すると fitness パッケージのパス解決に依存するため、
    spec_from_file_location でファイルから直接ロードして CHAOS_THRESHOLDS を読む。
    """
    import importlib.util

    cfg_path = (
        Path(__file__).resolve().parents[3]
        / "scripts" / "rl" / "fitness" / "config.py"
    )
    spec = importlib.util.spec_from_file_location("fitness_config_mv", cfg_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_chaos_thresholds_match_fitness_config():
    """multiview_eval の複製しきい値が fitness/config.py の CHAOS_THRESHOLDS と一致する。

    multiview_eval は config.py を import せず値を複製している（パス解決の疎結合のため）。
    将来 config.py 側が変わると静かに drift するため、この契約テストが drift を検出する。
    """
    cfg = _load_fitness_config()
    thresholds = cfg.CHAOS_THRESHOLDS
    assert mv._SPOF_DELTA == thresholds["spof_delta"]
    assert mv._IMPORTANT_DELTA == thresholds["low_delta"]
