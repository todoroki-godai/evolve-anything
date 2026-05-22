"""importance_score heuristic のテスト。

calculate_importance_score() は純粋関数のため LLM 呼び出しなし。
"""
from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path

# reflect.py を import できるようにパス設定
_root = Path(__file__).resolve().parent.parent.parent
_reflect_path = _root / "skills" / "reflect" / "scripts"
_scripts_lib = _root / "scripts" / "lib"
sys.path.insert(0, str(_scripts_lib))   # plugin_root など依存モジュール
sys.path.insert(0, str(_reflect_path))
from reflect import calculate_importance_score


def test_high_confidence_fresh():
    # confidence=0.9, age≈0 → スコア ≈ 0.9
    correction = {
        "confidence": 0.9,
        "decay_days": 90,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    score = calculate_importance_score(correction)
    assert 0.85 <= score <= 1.0


def test_low_confidence_old():
    # confidence=0.3, age=90日 (decay_days=90) → score ≈ 0.0
    correction = {
        "confidence": 0.3,
        "decay_days": 90,
        "timestamp": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
    }
    score = calculate_importance_score(correction)
    assert score < 0.05  # ほぼ0


def test_decay_reduces_score():
    # 同じ confidence でも古いほどスコアが低い
    now = datetime.now(timezone.utc)
    fresh = {"confidence": 0.8, "decay_days": 90, "timestamp": now.isoformat()}
    old = {"confidence": 0.8, "decay_days": 90, "timestamp": (now - timedelta(days=45)).isoformat()}

    score_fresh = calculate_importance_score(fresh)
    score_old = calculate_importance_score(old)
    assert score_fresh > score_old


def test_clamp_to_1():
    # confidence > 1.0 は 1.0 に clamp される
    correction = {
        "confidence": 1.5,
        "decay_days": 90,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    score = calculate_importance_score(correction)
    assert score <= 1.0


def test_missing_timestamp_fallback():
    # timestamp なし → confidence をそのまま返す
    correction = {"confidence": 0.7, "decay_days": 90}
    score = calculate_importance_score(correction)
    assert abs(score - 0.7) < 0.01


def test_decay_days_zero_fallback():
    # decay_days=0 → confidence をそのまま返す（ゼロ除算防止）
    correction = {
        "confidence": 0.6,
        "decay_days": 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    score = calculate_importance_score(correction)
    assert abs(score - 0.6) < 0.01
