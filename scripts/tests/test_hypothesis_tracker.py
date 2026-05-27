"""hypothesis_tracker.py のユニットテスト。

- LLM 呼び出しなし
- ファイル IO は tmp_path fixture で差し替え
"""
import sys
from pathlib import Path

import pytest

# importlib モード下では sys.path への追加が必要
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib.hypothesis_tracker import (  # noqa: E402
    Hypothesis,
    detect_contradiction,
    load_hypotheses,
    save_hypothesis,
    update_confidence,
)
import lib.hypothesis_tracker as ht


# ── fixture ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def patch_data_dir(tmp_path, monkeypatch):
    """DATA_DIR を tmp_path に差し替えて実ファイルを汚染しない。"""
    monkeypatch.setattr(ht, "DATA_DIR", tmp_path)


def _make_hypothesis(
    hypothesis_id: str = "h1",
    statement: str = "テスト仮説",
    confidence: float = 0.5,
    status: str = "active",
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hypothesis_id,
        statement=statement,
        confidence=confidence,
        status=status,
    )


# ── テスト ────────────────────────────────────────────

class TestSaveAndLoad:
    def test_save_and_load_hypothesis(self):
        """save → load のラウンドトリップ。"""
        h = _make_hypothesis("h1", "問題はパスの正規化", 0.6, "active")
        ht.save_hypothesis("sess1", h)

        loaded = ht.load_hypotheses("sess1")
        assert len(loaded) == 1
        assert loaded[0].hypothesis_id == "h1"
        assert loaded[0].statement == "問題はパスの正規化"
        assert loaded[0].confidence == 0.6
        assert loaded[0].status == "active"

    def test_timestamps_set_on_save(self):
        """save 時に created_at / updated_at が設定される。"""
        h = _make_hypothesis()
        ht.save_hypothesis("sess1", h)

        loaded = ht.load_hypotheses("sess1")[0]
        assert loaded.created_at != ""
        assert loaded.updated_at != ""

    def test_overwrite_same_id(self):
        """同一 hypothesis_id は上書きされる。"""
        h1 = _make_hypothesis("h1", "最初の仮説")
        ht.save_hypothesis("sess1", h1)

        h2 = _make_hypothesis("h1", "修正した仮説")
        ht.save_hypothesis("sess1", h2)

        loaded = ht.load_hypotheses("sess1")
        assert len(loaded) == 1
        assert loaded[0].statement == "修正した仮説"

    def test_multiple_hypotheses(self):
        """複数仮説が全件保存・ロードされる。"""
        for i in range(1, 4):
            ht.save_hypothesis("sess1", _make_hypothesis(f"h{i}", f"仮説{i}"))

        loaded = ht.load_hypotheses("sess1")
        assert len(loaded) == 3
        ids = [h.hypothesis_id for h in loaded]
        assert "h1" in ids and "h2" in ids and "h3" in ids


class TestUpdateConfidence:
    def test_update_confidence_supporting(self):
        """supporting 証拠で confidence が +0.1 上がる。"""
        h = _make_hypothesis(confidence=0.5)
        ht.save_hypothesis("sess1", h)

        updated = ht.update_confidence("sess1", "h1", "新証拠A", is_supporting=True)
        assert abs(updated.confidence - 0.6) < 1e-9
        assert "新証拠A" in updated.evidence_for

    def test_update_confidence_against(self):
        """against 証拠で confidence が -0.15 下がる。"""
        h = _make_hypothesis(confidence=0.5)
        ht.save_hypothesis("sess1", h)

        updated = ht.update_confidence("sess1", "h1", "反証B", is_supporting=False)
        assert abs(updated.confidence - 0.35) < 1e-9
        assert "反証B" in updated.evidence_against

    def test_confidence_clamped_upper(self):
        """confidence は 1.0 を超えない。"""
        h = _make_hypothesis(confidence=0.95)
        ht.save_hypothesis("sess1", h)

        updated = ht.update_confidence("sess1", "h1", "追加証拠", is_supporting=True)
        assert updated.confidence == 1.0

    def test_confidence_clamped_lower(self):
        """confidence は 0.0 未満にならない。"""
        h = _make_hypothesis(confidence=0.05)
        ht.save_hypothesis("sess1", h)

        updated = ht.update_confidence("sess1", "h1", "強い反証", is_supporting=False)
        assert updated.confidence == 0.0

    def test_confidence_clamped_0_1(self):
        """confidence が 0.0-1.0 でクランプされる（上下両端の統合テスト）。"""
        # 上限クランプ
        h_high = _make_hypothesis("h_high", confidence=1.0)
        ht.save_hypothesis("sess1", h_high)
        updated_high = ht.update_confidence("sess1", "h_high", "任意証拠", is_supporting=True)
        assert 0.0 <= updated_high.confidence <= 1.0

        # 下限クランプ
        h_low = _make_hypothesis("h_low", confidence=0.0)
        ht.save_hypothesis("sess1", h_low)
        updated_low = ht.update_confidence("sess1", "h_low", "任意反証", is_supporting=False)
        assert 0.0 <= updated_low.confidence <= 1.0

    def test_update_persists_to_file(self):
        """update_confidence の変更がファイルに永続化される。"""
        h = _make_hypothesis(confidence=0.5)
        ht.save_hypothesis("sess1", h)

        ht.update_confidence("sess1", "h1", "証拠", is_supporting=True)

        # 再ロードして確認
        loaded = ht.load_hypotheses("sess1")
        assert abs(loaded[0].confidence - 0.6) < 1e-9

    def test_update_unknown_id_raises(self):
        """存在しない hypothesis_id は KeyError を上げる。"""
        with pytest.raises(KeyError):
            ht.update_confidence("sess1", "h999", "証拠", is_supporting=True)


class TestDetectContradiction:
    def test_detect_contradiction_threshold(self):
        """evidence_against が 3 件 → 矛盾検知される。"""
        h1 = _make_hypothesis("h1", "仮説A", status="active")
        h1.evidence_against = ["反証1", "反証2", "反証3"]

        h2 = _make_hypothesis("h2", "仮説B", status="active")
        h2.evidence_against = ["反証X", "反証Y", "反証Z"]

        pairs = ht.detect_contradiction([h1, h2])
        assert ("h1", "h2") in pairs

    def test_no_contradiction_below_threshold(self):
        """evidence_against が 2 件以下 → 矛盾検知されない。"""
        h1 = _make_hypothesis("h1")
        h1.evidence_against = ["反証1", "反証2"]

        h2 = _make_hypothesis("h2")
        h2.evidence_against = ["反証X", "反証Y"]

        pairs = ht.detect_contradiction([h1, h2])
        assert len(pairs) == 0

    def test_non_active_not_detected(self):
        """active でない仮説は矛盾検知から除外される。"""
        h1 = _make_hypothesis("h1", status="confirmed")
        h1.evidence_against = ["反証1", "反証2", "反証3"]

        h2 = _make_hypothesis("h2", status="active")
        h2.evidence_against = ["反証A", "反証B", "反証C"]

        pairs = ht.detect_contradiction([h1, h2])
        # h1 は confirmed なので候補外 → h2 単独では対を作れない
        assert len(pairs) == 0

    def test_single_hypothesis_no_pairs(self):
        """仮説が1件のみ → ペアなし。"""
        h1 = _make_hypothesis("h1")
        h1.evidence_against = ["反証1", "反証2", "反証3"]
        pairs = ht.detect_contradiction([h1])
        assert len(pairs) == 0

    def test_empty_list(self):
        """空リスト → ペアなし。"""
        assert ht.detect_contradiction([]) == []

    def test_asymmetric_contradiction(self):
        """h1=3件、h2=0件 → 一方だけ threshold 以上でも矛盾検知される（Option B 仕様）。"""
        h1 = _make_hypothesis("h1", "仮説A", status="active")
        h1.evidence_against = ["反証1", "反証2", "反証3"]

        h2 = _make_hypothesis("h2", "仮説B", status="active")
        # h2 は evidence_against なし

        pairs = ht.detect_contradiction([h1, h2])
        assert ("h1", "h2") in pairs

    def test_no_contradiction_neither_reaches_threshold(self):
        """h1=2件、h2=2件 → いずれも threshold 未満ならペアなし。"""
        h1 = _make_hypothesis("h1", "仮説A", status="active")
        h1.evidence_against = ["反証1", "反証2"]

        h2 = _make_hypothesis("h2", "仮説B", status="active")
        h2.evidence_against = ["反証X", "反証Y"]

        pairs = ht.detect_contradiction([h1, h2])
        assert len(pairs) == 0


class TestSessionIsolation:
    def test_session_isolation(self):
        """異なる session_id のデータが混在しない。"""
        h_a = _make_hypothesis("h1", "セッションAの仮説")
        ht.save_hypothesis("sess_a", h_a)

        h_b = _make_hypothesis("h1", "セッションBの仮説")
        ht.save_hypothesis("sess_b", h_b)

        loaded_a = ht.load_hypotheses("sess_a")
        loaded_b = ht.load_hypotheses("sess_b")

        assert len(loaded_a) == 1
        assert loaded_a[0].statement == "セッションAの仮説"

        assert len(loaded_b) == 1
        assert loaded_b[0].statement == "セッションBの仮説"

    def test_empty_session_returns_empty_list(self):
        """存在しないセッション ID → 空リスト。"""
        result = ht.load_hypotheses("nonexistent_session")
        assert result == []

    def test_update_in_one_session_does_not_affect_another(self):
        """一方のセッションの更新が他方に影響しない。"""
        h = _make_hypothesis(confidence=0.5)
        ht.save_hypothesis("sess_a", h)
        ht.save_hypothesis("sess_b", Hypothesis(
            hypothesis_id="h1", statement="Bの仮説", confidence=0.5, status="active"
        ))

        ht.update_confidence("sess_a", "h1", "証拠", is_supporting=True)

        loaded_b = ht.load_hypotheses("sess_b")
        assert abs(loaded_b[0].confidence - 0.5) < 1e-9
