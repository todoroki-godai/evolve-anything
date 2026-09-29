"""issue #26 — recommended_artifacts のクールダウン/suppression。

`detect_recommended_artifacts` は従来「ディスク上に存在しない artifact」を毎回
全件再提示しており、ユーザーが「導入しない」と判断した artifact も提案され続けた。
本テストは type:"artifact" の suppression エントリ（TTL + 再発エスカレーション）を
導入し、クールダウン窓内は再提示しないことを回帰として封じる。

設計は既存の merge suppression（type:"merge"）と triage_ledger の TTL/再発昇格を踏襲。
artifact は home-global（RECOMMENDED_ARTIFACTS は ~/.claude 配下を見る）なので
suppression も slug 非依存・DATA_DIR グローバルストアに置く。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402
from discover.suppression import (  # noqa: E402
    add_artifact_suppression,
    load_artifact_suppression,
    is_artifact_suppressed,
)


@pytest.fixture()
def supp_file(tmp_path, monkeypatch):
    """SUPPRESSION_FILE を tmp に差し替える（既存テストと同じ package 属性 patch）。"""
    f = tmp_path / "discover-suppression.jsonl"
    monkeypatch.setattr(discover, "SUPPRESSION_FILE", f)
    monkeypatch.setattr(discover, "DATA_DIR", tmp_path)
    return f


# ---------------------------------------------------------------------------
# ストア層: add / load / TTL 判定
# ---------------------------------------------------------------------------

class TestArtifactSuppressionStore:
    def test_add_then_load_contains_id(self, supp_file):
        add_artifact_suppression("commit-version")
        assert "commit-version" in load_artifact_suppression()

    def test_load_empty_when_no_file(self, supp_file):
        assert load_artifact_suppression() == set()

    def test_merge_and_pattern_entries_not_treated_as_artifact(self, supp_file):
        """type:"merge" / type 未指定（pattern reject）は artifact suppression に混ざらない。"""
        discover.add_merge_suppression("a", "b")
        discover.add_to_suppression_list("some-pattern")
        add_artifact_suppression("deploy-lock")
        artifacts = load_artifact_suppression()
        assert artifacts == {"deploy-lock"}

    def test_within_cooldown_is_suppressed(self, supp_file):
        add_artifact_suppression("kill-guard", now=1_000_000.0)
        # クールダウン窓内（1日後）はまだ抑制
        assert is_artifact_suppressed(
            "kill-guard", now=1_000_000.0 + 86400.0,
        )

    def test_after_ttl_is_not_suppressed(self, supp_file):
        add_artifact_suppression("kill-guard", now=1_000_000.0)
        # TTL（既定45日）経過後は抑制解除＝1回再提示
        assert not is_artifact_suppressed(
            "kill-guard", now=1_000_000.0 + 46 * 86400.0,
        )

    def test_unknown_id_not_suppressed(self, supp_file):
        add_artifact_suppression("deploy-lock", now=1_000_000.0)
        assert not is_artifact_suppressed("kill-guard", now=1_000_000.0)

    def test_unknown_catalog_id_rejected(self, supp_file):
        """RECOMMENDED_ARTIFACTS に無い id は ValueError で拒否され、何も書かれない（レビュー指摘）。"""
        with pytest.raises(ValueError):
            add_artifact_suppression("this-id-does-not-exist-in-catalog")
        assert not supp_file.exists()
        assert load_artifact_suppression() == set()


# ---------------------------------------------------------------------------
# artifact suppression の pattern と usage skill_name の衝突防止
#
# RECOMMENDED_ARTIFACTS の id には "ship" / "spec-keeper" のように実スキル名と
# 同じ文字列が使われうる。artifact 見送り記録が discover.load_suppression_list()
# （ad-hoc スキル候補の抑制判定）に混入すると、無関係なスキル候補が誤って
# 抑制されたままになる（レビュー指摘 Should→今回直す）。
# ---------------------------------------------------------------------------


class TestArtifactSuppressionDoesNotLeakIntoPatternSuppression:
    def test_artifact_entry_excluded_from_skill_pattern_suppression(self, supp_file):
        # "ship" / "spec-keeper" は RECOMMENDED_ARTIFACTS の実 id（スキル名と衝突しうる）。
        add_artifact_suppression("ship")
        add_artifact_suppression("spec-keeper")
        discover.add_to_suppression_list("real-error-pattern")

        pattern_suppressed = discover.load_suppression_list()
        assert "ship" not in pattern_suppressed
        assert "spec-keeper" not in pattern_suppressed
        assert "real-error-pattern" in pattern_suppressed

        # artifact suppression 自体は引き続き有効（読み先が違うだけ）。
        assert load_artifact_suppression() == {"ship", "spec-keeper"}


# ---------------------------------------------------------------------------
# 統合: detect_recommended_artifacts がクールダウン中の artifact を畳む
# ---------------------------------------------------------------------------

def _patched_artifacts(tmp_path):
    """全 artifact を missing（ディスク不在）にして RECOMMENDED_ARTIFACTS を返す。"""
    patched = []
    for art in discover.RECOMMENDED_ARTIFACTS:
        new_art = {**art, "path": tmp_path / f"nope_{art['id']}.md"}
        if "hook_path" in art:
            new_art["hook_path"] = tmp_path / f"nope_{art['id']}.py"
        patched.append(new_art)
    return patched


class TestDetectRespectsSuppression:
    def test_suppressed_artifact_not_resurfaced(self, supp_file, tmp_path, monkeypatch):
        patched = _patched_artifacts(tmp_path)
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", patched)

        # まず全件 missing として上がることを確認
        before = discover.detect_recommended_artifacts()
        before_ids = {e["id"] for e in before}
        assert before_ids, "前提: missing artifact が1件以上ある"

        target = next(iter(before_ids))
        add_artifact_suppression(target)

        after = discover.detect_recommended_artifacts()
        after_ids = {e["id"] for e in after}
        assert target not in after_ids, (
            f"'{target}' は suppression 済みなので再提示されてはいけない (#26)"
        )
        # 他の artifact は引き続き提示される
        assert after_ids == before_ids - {target}

    def test_expired_suppression_resurfaces_once(self, supp_file, tmp_path, monkeypatch):
        patched = _patched_artifacts(tmp_path)
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", patched)
        before = discover.detect_recommended_artifacts()
        target = before[0]["id"]

        add_artifact_suppression(target, now=1_000_000.0)
        # TTL 経過後の now を渡して再提示されることを確認
        after = discover.detect_recommended_artifacts(
            now=1_000_000.0 + 46 * 86400.0,
        )
        assert target in {e["id"] for e in after}, (
            f"TTL 切れ後は '{target}' を1回再提示するべき (#26)"
        )
