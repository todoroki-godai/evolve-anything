"""test_memory_gating.py — memory_gating.py のユニットテスト。

TDD-first: 実装前にテストを書く原則に従い、全テストは LLM 呼び出しなし。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

# scripts/lib を sys.path に追加
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
# hooks/ も追加（auto_memory_runner のテスト用）
_HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from lib.memory_gating import GatingScore, score_correction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_correction(
    message: str = "テスト修正メッセージ",
    ctype: str = "correction",
    pattern: str | None = None,
) -> dict:
    """テスト用 correction dict を生成する。"""
    c: dict = {"message": message, "type": ctype}
    if pattern is not None:
        c["pattern"] = pattern
    return c


# ---------------------------------------------------------------------------
# test_score_high_recurrence
# ---------------------------------------------------------------------------

class TestRecurrenceScore:
    def test_score_high_recurrence(self) -> None:
        """同じ pattern が 3 回出現すると recurrence_score が高くなること。"""
        pattern = "git-diff-over-status"
        target = _make_correction(pattern=pattern)
        # 同一パターンを 3 件用意
        all_corrections = [
            _make_correction(pattern=pattern),
            _make_correction(pattern=pattern),
            _make_correction(pattern=pattern),
        ]
        result = score_correction(target, [], all_corrections)
        # 3 回出現 → recurrence_score = min(1.0, (3-1)/2) = 1.0
        assert result.recurrence_score == pytest.approx(1.0)
        assert isinstance(result, GatingScore)

    def test_score_single_occurrence_zero_recurrence(self) -> None:
        """1 件のみ出現の場合 recurrence_score = 0.0 であること。"""
        target = _make_correction(pattern="unique-pattern-xyz")
        all_corrections = [target]
        result = score_correction(target, [], all_corrections)
        assert result.recurrence_score == pytest.approx(0.0)

    def test_score_two_occurrences_half_recurrence(self) -> None:
        """2 件出現の場合 recurrence_score = 0.5 であること。"""
        pattern = "double-pattern"
        target = _make_correction(pattern=pattern)
        all_corrections = [
            _make_correction(pattern=pattern),
            _make_correction(pattern=pattern),
        ]
        result = score_correction(target, [], all_corrections)
        assert result.recurrence_score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# test_score_low_novelty
# ---------------------------------------------------------------------------

class TestNoveltyScore:
    def test_score_low_novelty(self) -> None:
        """既存メモリと高い Jaccard 類似度がある場合 novelty_score が低くなること。"""
        # correction と existing_memory に同一単語を多数含める
        message = "git diff で変更内容を確認してから commit すること"
        existing = "git diff で変更内容を確認してから commit すること"
        result = score_correction(_make_correction(message=message), [existing])
        # Jaccard 類似度 ≒ 1.0 → novelty_score ≒ 0.0
        assert result.novelty_score < 0.2

    def test_score_high_novelty_no_existing(self) -> None:
        """既存メモリがない場合 novelty_score = 1.0 であること。"""
        result = score_correction(_make_correction(), [])
        assert result.novelty_score == pytest.approx(1.0)

    def test_score_high_novelty_unrelated_memory(self) -> None:
        """既存メモリと無関係な correction は novelty_score が高いこと。"""
        message = "pytest fixture でテストデータを分離する"
        existing = ["AWS CDK インフラ変更時は動作確認後に ship する"]
        result = score_correction(_make_correction(message=message), existing)
        # 単語の重複が少ない → novelty_score が高い
        assert result.novelty_score > 0.5


# ---------------------------------------------------------------------------
# test_score_below_threshold_skipped
# ---------------------------------------------------------------------------

class TestCompositeAndThreshold:
    def test_score_below_threshold_should_store_false(self) -> None:
        """composite < 0.5 の場合 should_store=False であること。

        recurrence=0.0、novelty が低い（類似メモリあり）、severity=0.3（非correction/feedback）の組み合わせ。
        """
        message = "同じ内容のメモリ"
        existing = ["同じ内容のメモリ"]  # Jaccard ≒ 1.0 → novelty ≒ 0.0
        correction = _make_correction(message=message, ctype="other")
        result = score_correction(correction, existing)
        # composite ≒ 0.0*0.4 + 0.0*0.4 + 0.3*0.2 = 0.06
        assert result.composite < 0.5
        assert result.should_store is False

    def test_score_above_threshold_should_store_true(self) -> None:
        """composite >= 0.5 の場合 should_store=True であること。"""
        message = "全く新規のパターン abc xyz 123 foo bar baz"
        correction = _make_correction(message=message, ctype="correction")
        result = score_correction(correction, [])
        # recurrence=0.0, novelty=1.0, severity=0.8
        # composite = 0.0*0.4 + 1.0*0.4 + 0.8*0.2 = 0.56
        assert result.composite >= 0.5
        assert result.should_store is True

    def test_custom_threshold(self) -> None:
        """threshold パラメータが機能すること。"""
        message = "テスト"
        correction = _make_correction(message=message, ctype="correction")
        result_low = score_correction(correction, [], threshold=0.1)
        result_high = score_correction(correction, [], threshold=0.9)
        assert result_low.should_store is True
        assert result_high.should_store is False


# ---------------------------------------------------------------------------
# test_severity_score
# ---------------------------------------------------------------------------

class TestSeverityScore:
    def test_correction_type_severity(self) -> None:
        """type='correction' の場合 severity_score=0.8 であること。"""
        result = score_correction(_make_correction(ctype="correction"), [])
        assert result.severity_score == pytest.approx(0.8)

    def test_feedback_type_severity(self) -> None:
        """type='feedback' の場合 severity_score=0.5 であること。"""
        result = score_correction(_make_correction(ctype="feedback"), [])
        assert result.severity_score == pytest.approx(0.5)

    def test_other_type_severity(self) -> None:
        """type が correction/feedback 以外の場合 severity_score=0.3 であること。"""
        result = score_correction(_make_correction(ctype="stop"), [])
        assert result.severity_score == pytest.approx(0.3)

    def test_correction_type_via_correction_type_key(self) -> None:
        """correction_type キーも認識されること（後方互換）。"""
        c = {"message": "test", "correction_type": "correction"}
        result = score_correction(c, [])
        assert result.severity_score == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# test_gating_disabled_env
# ---------------------------------------------------------------------------

class TestGatingDisabledEnv:
    def test_is_gating_enabled_respects_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RL_GATING_DISABLED=1 のとき _is_gating_enabled() が False を返すこと。"""
        import auto_memory_runner as amr
        monkeypatch.setenv("RL_GATING_DISABLED", "1")
        assert amr._is_gating_enabled() is False

    def test_is_gating_enabled_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RL_GATING_DISABLED 未設定のとき _is_gating_enabled() が True を返すこと。"""
        import auto_memory_runner as amr
        monkeypatch.delenv("RL_GATING_DISABLED", raising=False)
        assert amr._is_gating_enabled() is True

    def test_gating_disabled_skips_gating(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """RL_GATING_DISABLED=1 でゲーティングが無効化され、キューに enqueue されること。

        [ADR-037] Phase 2: hook は LLM を呼ばず enqueue するだけになったため、
        旧来の「_call_llm が呼ばれる」は「キューに record が積まれる」へ置換する。
        """
        # 環境変数でゲーティング無効化
        monkeypatch.setenv("RL_GATING_DISABLED", "1")

        # corrections.jsonl に低スコアな correction を用意（ゲーティングがあれば弾かれるはず）
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        corrections_path = data_dir / "corrections.jsonl"
        import json
        # type="stop" (severity=0.3)、既存メモリと類似（novelty低）、recurrence=0
        correction_data = {
            "message": "同じ内容のメモリ",
            "type": "stop",
        }
        corrections_path.write_text(json.dumps(correction_data) + "\n")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("# Memory\n")

        import auto_memory_runner as amr
        import auto_memory_broker as amb
        # RL_GATING_DISABLED=1 → ゲーティング無効（実行時評価を monkeypatch でシミュレート）
        with mock.patch.object(amr, "_is_gating_enabled", return_value=False):
            amr.run(
                memory_dir=memory_dir,
                memory_md_path=memory_md,
                data_dir=data_dir,
                slug="testslug",
            )
        # ゲーティング無効 → enqueue が実行されること
        assert len(amb.read_queue("testslug", data_dir)) == 1

    def test_gating_disabled_via_env_var_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RL_GATING_DISABLED=1 を実際に環境変数にセットすると _is_gating_enabled() が
        False を返し、ゲーティングロジックがスキップされて enqueue されること。

        _is_gating_enabled はモックせず、env var → 関数評価 → run() の経路を通す。
        """
        import json
        import auto_memory_runner as amr
        import auto_memory_broker as amb

        monkeypatch.setenv("RL_GATING_DISABLED", "1")

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # 低スコア correction（ゲーティングが有効なら弾かれるはず）
        correction_data = {"message": "同じ内容のメモリ", "type": "stop"}
        (data_dir / "corrections.jsonl").write_text(json.dumps(correction_data) + "\n")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("# Memory\n")

        # env var から _is_gating_enabled() → False の経路を実際に通す
        with mock.patch.object(amr, "_HAS_MEMORY_GATING", True):
            amr.run(
                memory_dir=memory_dir,
                memory_md_path=memory_md,
                data_dir=data_dir,
                slug="testslug",
            )
        # RL_GATING_DISABLED=1 → ゲーティングスキップ → enqueue されること
        assert len(amb.read_queue("testslug", data_dir)) == 1

    def test_gating_enabled_low_score_skips_llm(self, tmp_path: Path) -> None:
        """ゲーティング有効かつ低スコア correction では enqueue されないこと。

        should_store=False → enqueue スキップ。
        """
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        corrections_path = data_dir / "corrections.jsonl"
        import json

        # 低スコアになる条件:
        # - type="stop" → severity=0.3
        # - recurrence=0 (1件のみ)
        # - novelty は別途コントロールできないが、既存メモリなしで novelty=1.0
        # → composite = 0.0*0.4 + 1.0*0.4 + 0.3*0.2 = 0.46 (< 0.5)
        correction_data = {
            "message": "unique message abc def ghi jkl",
            "type": "stop",
        }
        corrections_path.write_text(json.dumps(correction_data) + "\n")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("# Memory\n")

        import auto_memory_runner as amr
        import auto_memory_broker as amb
        with mock.patch.object(amr, "_is_gating_enabled", return_value=True), \
             mock.patch.object(amr, "_HAS_MEMORY_GATING", True):
            amr.run(
                memory_dir=memory_dir,
                memory_md_path=memory_md,
                data_dir=data_dir,
                slug="testslug",
            )
        # ゲーティングでスキップ → enqueue なし
        assert amb.read_queue("testslug", data_dir) == []


# ---------------------------------------------------------------------------
# test_auto_memory_runner_gating_integration
# ---------------------------------------------------------------------------

class TestAutoMemoryRunnerGatingIntegration:
    def test_run_skips_llm_when_gate_false(self, tmp_path: Path) -> None:
        """score_correction が False を返す場合 enqueue されないこと。

        [ADR-037] Phase 2: hook は LLM を呼ばず enqueue するだけ。gate False → キュー空。
        """
        import json
        import auto_memory_runner as amr
        import auto_memory_broker as amb

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        correction_data = {
            "message": "test message",
            "type": "stop",
        }
        (data_dir / "corrections.jsonl").write_text(json.dumps(correction_data) + "\n")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # score_correction を mock して should_store=False を返す
        fake_score = GatingScore(
            recurrence_score=0.0,
            novelty_score=0.0,
            severity_score=0.3,
            composite=0.06,
            should_store=False,
        )
        with mock.patch.object(amr, "_HAS_MEMORY_GATING", True), \
             mock.patch.object(amr, "_is_gating_enabled", return_value=True), \
             mock.patch("memory_gating.score_correction", return_value=fake_score), \
             mock.patch.object(amr, "_score_correction", return_value=fake_score):
            amr.run(
                memory_dir=memory_dir,
                memory_md_path=tmp_path / "MEMORY.md",
                data_dir=data_dir,
                slug="testslug",
            )
        assert amb.read_queue("testslug", data_dir) == []

    def test_run_calls_llm_when_gate_true(self, tmp_path: Path) -> None:
        """score_correction が True を返す場合 enqueue されること。

        [ADR-037] Phase 2: gate True → キューに record 1件。
        """
        import json
        import auto_memory_runner as amr
        import auto_memory_broker as amb

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        # type="correction" + unique message → composite 高め
        correction_data = {
            "message": "全く新規のパターン unique xyz",
            "type": "correction",
        }
        (data_dir / "corrections.jsonl").write_text(json.dumps(correction_data) + "\n")

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text("# Memory\n")

        fake_score = GatingScore(
            recurrence_score=0.0,
            novelty_score=1.0,
            severity_score=0.8,
            composite=0.56,
            should_store=True,
        )
        with mock.patch.object(amr, "_HAS_MEMORY_GATING", True), \
             mock.patch.object(amr, "_is_gating_enabled", return_value=True), \
             mock.patch.object(amr, "_score_correction", return_value=fake_score):
            amr.run(
                memory_dir=memory_dir,
                memory_md_path=memory_md,
                data_dir=data_dir,
                slug="testslug",
            )
        # should_store=True → enqueue されること
        assert len(amb.read_queue("testslug", data_dir)) == 1
