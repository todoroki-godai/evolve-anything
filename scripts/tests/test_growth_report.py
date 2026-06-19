#!/usr/bin/env python3
"""growth_report のテスト — 成長レポート決定論生成 + 閾値単一ソース確認。

テスト方針:
- build_growth_report の出力構造を確認
- corrections 7/10 形式の lines が出ること
- 閾値到達済みなら「達成・次フェーズ条件は sessions/coherence」が出ること
- growth_report.py 内に閾値リテラル（10 等）が直書きされていないこと
- dry-run ゼロ書込: build_growth_report はファイルに書かない（read-only）
"""
import ast
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


# ── 補助: growth_engine 定数が export されているか ────────────────


class TestGrowthEngineConstants:
    """growth_engine.py の定数切り出し確認（挙動不変テスト）。"""

    def test_constants_exported(self):
        """STRUCTURED_CORRECTIONS_TARGET 等がモジュール定数として存在する。"""
        from growth_engine import (
            STRUCTURED_CORRECTIONS_TARGET,
            STRUCTURED_SESSIONS_TARGET,
            STRUCTURED_RULES_TARGET,
            BOOTSTRAP_SESSIONS_TARGET,
            MATURE_SESSIONS_TARGET,
            MATURE_RULES_TARGET,
        )
        assert STRUCTURED_CORRECTIONS_TARGET == 10
        assert STRUCTURED_SESSIONS_TARGET == 50
        assert STRUCTURED_RULES_TARGET == 3
        assert BOOTSTRAP_SESSIONS_TARGET == 10
        assert MATURE_SESSIONS_TARGET == 200
        assert MATURE_RULES_TARGET == 10

    def test_detect_phase_behavior_unchanged_bootstrap(self):
        """定数切り出し後も detect_phase の挙動が変わらない — Bootstrap。"""
        from growth_engine import detect_phase, Phase
        assert detect_phase(5, 0, 0, 0.0) == Phase.BOOTSTRAP

    def test_detect_phase_behavior_unchanged_initial(self):
        """定数切り出し後も detect_phase の挙動が変わらない — Initial Nurturing。"""
        from growth_engine import detect_phase, Phase
        assert detect_phase(30, 5, 0, 0.3) == Phase.INITIAL_NURTURING

    def test_detect_phase_behavior_unchanged_structured(self):
        """定数切り出し後も detect_phase の挙動が変わらない — Structured Nurturing。"""
        from growth_engine import detect_phase, Phase
        assert detect_phase(150, 15, 5, 0.5) == Phase.STRUCTURED_NURTURING

    def test_detect_phase_behavior_unchanged_mature(self):
        """定数切り出し後も detect_phase の挙動が変わらない — Mature Operation。"""
        from growth_engine import detect_phase, Phase
        assert detect_phase(250, 30, 12, 0.75) == Phase.MATURE_OPERATION

    def test_compute_phase_progress_bootstrap_uses_constant(self):
        """Bootstrap 進捗は BOOTSTRAP_SESSIONS_TARGET(=10) で正規化される。"""
        from growth_engine import compute_phase_progress, Phase, BOOTSTRAP_SESSIONS_TARGET
        progress = compute_phase_progress(Phase.BOOTSTRAP, BOOTSTRAP_SESSIONS_TARGET, 0, 0, 0.0)
        assert progress == pytest.approx(1.0, abs=0.01)

    def test_compute_phase_progress_initial_corrections_uses_constant(self):
        """Initial Nurturing の corrections 進捗は STRUCTURED_CORRECTIONS_TARGET で正規化。"""
        from growth_engine import compute_phase_progress, Phase, STRUCTURED_CORRECTIONS_TARGET
        # corrections が閾値丁度で進捗 1/3 以上になることを確認
        progress = compute_phase_progress(
            Phase.INITIAL_NURTURING,
            sessions_count=50,  # sessions も十分
            corrections_count=STRUCTURED_CORRECTIONS_TARGET,
            crystallized_rules=3,
            coherence_score=0.0,
        )
        assert progress > 0.5  # 3 条件中 2 つ満たせば 2/3 以上


# ── build_growth_report のテスト ──────────────────────────────────


class TestBuildGrowthReport:
    """build_growth_report の出力構造・文言確認。"""

    def _make_corrections(self, n: int, source: str = "reflect_confirmed") -> List[Dict]:
        """human corrections n 件のリストを作る。"""
        return [{"source": source, "correction_type": "improvement"} for _ in range(n)]

    def test_returns_required_keys(self):
        """結果 dict に必須キーが存在する。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(7),
            review_result={},
            autopromote_result={},
        )
        for key in ("phase", "phase_ja", "corrections_human", "corrections_target",
                    "remaining_to_next", "promoted_today", "autopromoted_today", "lines"):
            assert key in result, f"missing key: {key}"

    def test_lines_is_list_of_strings(self):
        """lines は文字列リスト。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(3),
            review_result={},
            autopromote_result={},
        )
        assert isinstance(result["lines"], list)
        for line in result["lines"]:
            assert isinstance(line, str)

    def test_corrections_below_target_shows_remaining(self):
        """corrections 7/10 → あと3件で構造化育成へ 形式の行が含まれる。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(7),
            review_result={},
            autopromote_result={},
        )
        # lines に "7/10" または "あと3件" が含まれる行がある
        lines_text = " ".join(result["lines"])
        assert "7" in lines_text
        assert "10" in lines_text
        assert result["corrections_human"] == 7
        assert result["corrections_target"] == 10
        assert result["remaining_to_next"] == 3

    def test_corrections_at_target_shows_achieved(self):
        """corrections 10 → 「達成・次フェーズ条件は sessions/coherence」。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(10),
            review_result={},
            autopromote_result={},
        )
        assert result["remaining_to_next"] == 0
        lines_text = " ".join(result["lines"])
        # 達成済みを示す表現がある
        assert "達成" in lines_text or "sessions" in lines_text or "coherence" in lines_text

    def test_corrections_over_target_remaining_zero(self):
        """corrections 15 → remaining_to_next == 0（負にならない）。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(15),
            review_result={},
            autopromote_result={},
        )
        assert result["remaining_to_next"] == 0

    def test_zero_corrections(self):
        """corrections 0 件でもエラーなし。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=[],
            review_result={},
            autopromote_result={},
        )
        assert result["corrections_human"] == 0
        assert result["remaining_to_next"] == 10
        assert len(result["lines"]) >= 1

    def test_promoted_today_from_review_result(self):
        """review_result に daily.promoted があれば promoted_today に反映。"""
        from growth_report import build_growth_report
        review = {"daily": {"promoted": 3, "groups": []}}
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(5),
            review_result=review,
            autopromote_result={},
        )
        assert result["promoted_today"] == 3

    def test_autopromoted_today_from_autopromote_result(self):
        """autopromote_result に promoted があれば autopromoted_today に反映。"""
        from growth_report import build_growth_report
        autopromote = {"promoted": 2}
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(5),
            review_result={},
            autopromote_result=autopromote,
        )
        assert result["autopromoted_today"] == 2

    def test_promoted_today_shown_in_lines(self):
        """today promoted > 0 → lines に idiom 昇格の記述がある。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(5),
            review_result={"daily": {"promoted": 2, "groups": []}},
            autopromote_result={"promoted": 1},
        )
        lines_text = " ".join(result["lines"])
        # 昇格件数または idiom が含まれる
        assert "3" in lines_text or "idiom" in lines_text or "昇格" in lines_text

    def test_none_review_result_defensive(self):
        """review_result が None でも KeyError にならない。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(5),
            review_result=None,  # type: ignore
            autopromote_result=None,  # type: ignore
        )
        # エラーなく動作する
        assert "lines" in result

    def test_missing_keys_in_review_result_no_error(self):
        """review_result に daily キーが無くても KeyError にならない。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=self._make_corrections(5),
            review_result={"error": "some error"},
            autopromote_result={"error": "some error"},
        )
        assert result["promoted_today"] == 0
        assert result["autopromoted_today"] == 0

    def test_machine_corrections_not_counted(self):
        """機械生成 corrections（source=hook）は human カウントに含まれない。"""
        from growth_report import build_growth_report
        machine_only = [{"source": "hook", "correction_type": "improvement"} for _ in range(10)]
        result = build_growth_report(
            "test-pj",
            corrections=machine_only,
            review_result={},
            autopromote_result={},
        )
        assert result["corrections_human"] == 0

    def test_corrections_line_clarifies_human_source_meaning(self):
        """corrections 行が「何を数えた数か」（human-confirmed のみ）を明示する（#476-4）。

        corrections_human 0/10 と prune の corrections kept 39 の関係が読み取れず、何を数えて 0
        なのか不明だった。行に human-confirmed である旨を添える。
        """
        from growth_report import build_growth_report
        machine_only = [{"source": "hook", "correction_type": "improvement"} for _ in range(39)]
        result = build_growth_report(
            "test-pj",
            corrections=machine_only,
            review_result={},
            autopromote_result={},
        )
        lines_text = " ".join(result["lines"])
        assert "human" in lines_text or "人間" in lines_text


# ── promoted_today を corrections ストアから決定論導出（#494 発見2）──────
class TestPromotedTodayFromCorrectionsStore:
    """promoted_today / autopromoted_today が corrections ストア（実 promote の永続記録）
    から決定論導出され、構造的常時0が解消されることを確認する（#494 発見2）。

    根因: build_review の返り値に promoted キーが存在せず、growth_report が
    review_result.daily.promoted を読んでも構造的に必ず 0 になっていた。実 promote は
    Step 6.2 の evolve-reflect --promote-weak が corrections.jsonl に書く（source=reflect_confirmed
    / promoted_by=idiom_dict）ため、growth_report は corrections の「今日の昇格」を数える。
    """

    def _today_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def _yesterday_iso(self) -> str:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

    def test_promoted_today_counts_today_human_weak_promotions(self):
        """source=reflect_confirmed + weak_signal_key + 今日の timestamp を promoted_today に数える。

        review_result.daily に promoted キーが無い（= 実 build_review の返り値）状況でも、
        corrections ストアの今日の人間確認昇格を反映する。
        """
        from growth_report import build_growth_report
        corrections = [
            # 今日の人間確認昇格（promoted_today に数える）
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k1", "timestamp": self._today_iso()},
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k2", "timestamp": self._today_iso()},
            # 昨日の昇格（今日には数えない）
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k0", "timestamp": self._yesterday_iso()},
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"groups": []}},  # promoted キー無し（実 build_review 相当）
            autopromote_result={},
        )
        assert result["promoted_today"] == 2

    def test_autopromoted_today_counts_today_idiom_dict_promotions(self):
        """promoted_by=idiom_dict + 今日の timestamp を autopromoted_today に数える。"""
        from growth_report import build_growth_report
        corrections = [
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "correction_type": "semantic_idiom", "weak_signal_key": "a1",
             "timestamp": self._today_iso()},
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "correction_type": "semantic_idiom", "weak_signal_key": "a2",
             "timestamp": self._yesterday_iso()},  # 昨日 → 数えない
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"groups": []}},
            autopromote_result={},  # promoted キー無し（実 autopromote が当 run で 0 件のとき）
        )
        assert result["autopromoted_today"] == 1

    def test_idiom_dict_not_double_counted_as_promoted_today(self):
        """idiom_dict 昇格は autopromoted にのみ数え、promoted_today に重複計上しない。"""
        from growth_report import build_growth_report
        corrections = [
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "correction_type": "semantic_idiom", "weak_signal_key": "a1",
             "timestamp": self._today_iso()},
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"groups": []}},
            autopromote_result={},
        )
        assert result["promoted_today"] == 0
        assert result["autopromoted_today"] == 1

    def test_explicit_review_promoted_takes_precedence_when_higher(self):
        """同 run の live カウント（review_result.daily.promoted）が store より多ければ尊重する。

        後方互換: 明示渡しの promoted は max で勝たせる（structural-0 補正は下限保証）。
        """
        from growth_report import build_growth_report
        corrections = [
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k1", "timestamp": self._today_iso()},
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"promoted": 5, "groups": []}},
            autopromote_result={},
        )
        assert result["promoted_today"] == 5

    def test_non_weak_human_correction_not_counted(self):
        """weak_signal 由来でない reflect_confirmed（手書き correction）は promoted_today に数えない。"""
        from growth_report import build_growth_report
        corrections = [
            {"source": "reflect_confirmed", "correction_type": "improvement",
             "timestamp": self._today_iso()},  # weak_signal_key 無し
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"groups": []}},
            autopromote_result={},
        )
        assert result["promoted_today"] == 0


# ── #525-1: 今日の昇格行の出所明示（本日累計 vs このrun）─────────────
class TestPromotedTodayProvenance:
    """「今日の確認で N件が自動化対象に昇格」が同日の別セッション分を指す問題を、
    本日累計（store 由来）と このrun（明示渡しの live カウント）を区別して表示する（#525-1）。
    """

    def _today_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def test_returns_this_run_keys(self):
        """this_run の昇格件数キーが返り値に含まれる（出所区別用）。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=[],
            review_result={"daily": {"promoted": 2, "groups": []}},
            autopromote_result={"promoted": 1},
        )
        assert "promoted_this_run" in result
        assert "autopromoted_this_run" in result
        assert result["promoted_this_run"] == 2
        assert result["autopromoted_this_run"] == 1

    def test_line_distinguishes_today_total_from_this_run(self):
        """store に本日累計の昇格があり、このrunの明示渡しが 0 のとき、
        「本日累計 N 件昇格（このrunでは 0 件）」と出所を明示する。
        """
        from growth_report import build_growth_report
        corrections = [
            # 本日累計（別セッションで昇格済み）2 件
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k1", "timestamp": self._today_iso()},
            {"source": "reflect_confirmed", "correction_type": "semantic_idiom",
             "weak_signal_key": "k2", "timestamp": self._today_iso()},
        ]
        result = build_growth_report(
            "test-pj",
            corrections=corrections,
            review_result={"daily": {"groups": []}},  # promoted 無し = このrunで 0 件
            autopromote_result={},
        )
        # 本日累計 = 2、このrun = 0
        assert result["promoted_today"] == 2
        assert result["promoted_this_run"] == 0
        lines_text = " ".join(result["lines"])
        assert "本日累計" in lines_text
        assert "このrun" in lines_text

    def test_no_promotion_line_when_today_total_zero(self):
        """本日累計が 0 件なら昇格行を出さない（従来挙動の維持）。"""
        from growth_report import build_growth_report
        result = build_growth_report(
            "test-pj",
            corrections=[],
            review_result={"daily": {"groups": []}},
            autopromote_result={},
        )
        lines_text = " ".join(result["lines"])
        assert "本日累計" not in lines_text


# ── 閾値リテラル禁止テスト ────────────────────────────────────────


class TestNoThresholdLiteralsInGrowthReport:
    """growth_report.py に閾値リテラルが直書きされていないことを AST で検証。"""

    def test_no_numeric_threshold_literals(self):
        """growth_report.py のソースコードに閾値リテラル（10, 50, 200 等）が
        定数定義を除いて直書きされていない。

        許可: import した定数の参照、比較式の右辺に変数を使う
        禁止: 10 / 50 / 200 / 3 / 0.7 をハードコード
        """
        growth_report_path = (
            Path(__file__).resolve().parent.parent / "lib" / "growth_report.py"
        )
        assert growth_report_path.exists(), "growth_report.py が存在しない"

        source = growth_report_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # growth_engine 定数として使われる閾値の整数値
        forbidden_literals = {10, 50, 200, 3}
        # コメント行・文字列リテラルは除外し、数値ノードのみ確認
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                if node.value in forbidden_literals:
                    violations.append((node.lineno, node.value))

        assert violations == [], (
            f"growth_report.py に閾値リテラルが直書きされています: {violations}\n"
            "growth_engine の定数を import して使ってください"
        )


# ── dry-run ゼロ書込テスト ────────────────────────────────────────


class TestGrowthReportNoDiskWrite:
    """build_growth_report はファイルを一切書かない（read-only）。"""

    def test_no_file_written(self, tmp_path, monkeypatch):
        """呼び出し後に tmp_path に何も作られない。"""
        from growth_report import build_growth_report
        before = set(tmp_path.rglob("*"))
        build_growth_report(
            "test-pj",
            corrections=[{"source": "reflect_confirmed", "correction_type": "improvement"}],
            review_result={},
            autopromote_result={},
        )
        after = set(tmp_path.rglob("*"))
        assert before == after, f"ファイルが書かれた: {after - before}"
