"""#376: usage_count==0 のスキルを medium に昇格させないガードのテスト。

自己進化（pitfalls.md 蓄積）は実際のミスが溜まったスキルに効く仕組み。一度も使われて
いないスキルを「変換可能(medium)」と勧めるのは本末転倒なので insufficient_usage に降格する。
検証系バイパス（テレメトリ不足でも進化推奨）と rejected は降格しない。

ロジックは `_finalize_suitability` に集約され、バッチ(skill_evolve_assessment)と
単体(assess_single_skill)の両経路が共有する（DRY）。
"""
import sys
from pathlib import Path
from unittest import mock

_LIB = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(_LIB))

from skill_evolve.assessment import _finalize_suitability  # noqa: E402
from skill_evolve import assess_single_skill  # noqa: E402


def _tel(usage_count, error_count=0, categories=None):
    return {
        "frequency": 1, "diversity": 1, "evaluability": 1,
        "usage_count": usage_count, "error_count": error_count,
        "error_categories": categories or [],
    }


# ── _finalize_suitability の単体（ガードロジック） ──────────────


def test_finalize_demotes_zero_usage_to_insufficient(tmp_path):
    """usage_count=0 かつ非検証 → medium 判定でも insufficient_usage に降格。"""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n")
    with mock.patch("skill_evolve.is_verification_skill", return_value=False):
        suit, rec, _, bypass = _finalize_suitability(
            {"frequency": 1, "diversity": 1, "evaluability": 1,
             "external_dependency": 2, "judgment_complexity": 3, "error_count": 0},
            "medium", "s", skill_dir, _tel(0),
        )
    assert suit == "insufficient_usage"
    assert bypass is False
    assert "使用実績待ち" in rec
    # #478: usage 記録経路修正日の advisory を含める（pre-fix データ欠損の緩和）
    assert "#478" in rec


def test_finalize_verification_skill_keeps_medium_even_at_zero_usage(tmp_path):
    """検証系スキルは usage=0 でも medium 維持（バイパス優先・既存契約 v1.13.0）。"""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n")
    with mock.patch("skill_evolve.is_verification_skill", return_value=True):
        suit, rec, _, bypass = _finalize_suitability(
            {"frequency": 1, "diversity": 1, "evaluability": 1,
             "external_dependency": 1, "judgment_complexity": 1, "error_count": 0},
            "low", "verify-thing", skill_dir, _tel(0),
        )
    assert suit == "medium"
    assert bypass is True


def test_finalize_zero_usage_does_not_override_rejected(tmp_path):
    """rejected（アンチパターン）は usage=0 でも rejected のまま。"""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n")
    refs = skill_dir / "references"
    refs.mkdir()
    from skill_evolve import BAND_AID_THRESHOLD
    (refs / "t.md").write_text("# T\n\n" + "\n".join(f"- i{i}" for i in range(BAND_AID_THRESHOLD + 1)))
    # Noise Collector(diversity=1, error>0) + Band-Aid で 2 件 → rejected
    suit, rec, _, bypass = _finalize_suitability(
        {"frequency": 3, "diversity": 1, "evaluability": 3,
         "external_dependency": 1, "judgment_complexity": 1, "error_count": 5},
        "low", "s", skill_dir, _tel(0, error_count=5, categories=["a"]),
    )
    assert suit == "rejected"


def test_finalize_nonzero_usage_unchanged(tmp_path):
    """usage_count>0 は従来どおり（medium のまま）。"""
    skill_dir = tmp_path / "s"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# S\n")
    with mock.patch("skill_evolve.is_verification_skill", return_value=False):
        suit, _, _, _ = _finalize_suitability(
            {"frequency": 2, "diversity": 1, "evaluability": 1,
             "external_dependency": 2, "judgment_complexity": 3, "error_count": 0},
            "medium", "s", skill_dir, _tel(10),
        )
    assert suit == "medium"


# ── 単体経路 assess_single_skill での E2E（配線確認） ───────────


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_zero_usage_is_insufficient(mock_tel, mock_llm, tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")
    # usage=0 で外部依存/判断が高く、本来 medium に乗るケース
    mock_tel.return_value = _tel(0)
    mock_llm.return_value = {"external_dependency": 3, "judgment_complexity": 3, "cached": True}
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "insufficient_usage"
    assert result["telemetry_detail"]["usage_count"] == 0


@mock.patch("skill_evolve.compute_llm_scores")
@mock.patch("skill_evolve.compute_telemetry_scores")
def test_assess_single_nonzero_usage_still_medium(mock_tel, mock_llm, tmp_path):
    """既存挙動の回帰チェック: usage>0 の medium はそのまま。"""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# My Skill\n")
    mock_tel.return_value = _tel(10)
    mock_tel.return_value.update({"frequency": 2, "diversity": 2})
    mock_llm.return_value = {"external_dependency": 1, "judgment_complexity": 2, "cached": True}
    result = assess_single_skill("my-skill", skill_dir)
    assert result["suitability"] == "medium"
