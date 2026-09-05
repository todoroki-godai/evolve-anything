"""推奨 artifact の「既に同じものが無いか」を場所一致だけで確認する（#624 巡3）。

`detect_recommended_artifacts` はかつて名前・本文の一致でも covered 扱いにしていたが、
`.claude/rules/no-denylist-checks.md`「名前・文字列・構文形・sink の種類で同一性を
判定する検査は blocking にしない」に反し、巡1・巡2 で名前族の [Must] が続いた
（unrelated skill の同名 basename に当たる／`refs/` 配下の同名ファイルに当たる等）。

3層方式（確定設計）:
  1. 場所一致（`_find_by_location`）— 宣言パスの `~/.claude` 相対部分を
     `~/.claude` と `<project_root>/.claude` の2 base に結合し `is_file()` だけ見る。
     **唯一の自動抑制**。missing から要素を外せるのはこれだけ。
  2. 言い回し一致（`_find_marker`）— `likely_covered_by` という印を付けるだけ。
     missing からは絶対に外さない（既知の言い回しのみ検出・迂回可能）。
  3. 人間の確定 — `discover-suppression.jsonl`（本テストの対象外）。

実測（2026-09-04・実 rules ディレクトリ 2 箇所, project_root=evolve-anything,
`( cd scripts/lib && python3 -c '...detect_recommended_artifacts(project_root=...)' )`):
12 件中 9 件が covered_by/likely_covered_by の対象だった内訳は、場所一致 2 件
（commit-version, evidence-before-claims）／言い回し一致 7 件（test-happy-path-first,
claude-md-style, suggest-implement-skill, gstack-flow-chain, living-spec-awareness,
continuation-check, worktree-parallel-work）。旧方式ではこの 9 件すべてが
`covered_by` で提示から下げられていたが、新方式では言い回し一致の 7 件は
fresh のまま `likely_covered_by` の印だけを持つ。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import discover  # noqa: E402
from discover.artifacts import detect_recommended_artifacts  # noqa: E402


@pytest.fixture()
def no_suppression(monkeypatch, tmp_path):
    """suppression を無効化して、判定だけを見る。"""
    monkeypatch.setattr(discover, "SUPPRESSION_FILE", tmp_path / "none.jsonl")
    monkeypatch.setattr(discover, "DATA_DIR", tmp_path)
    return tmp_path


def _artifact(**kw):
    base = {"id": "x", "type": "rule", "description": "d", "hook_path": None}
    base.update(kw)
    return base


def _by_id(entries, aid):
    return next((e for e in entries if e["id"] == aid), None)


def _mk_home(monkeypatch, tmp_path, name="home"):
    home = tmp_path / name
    (home / ".claude" / "rules").mkdir(parents=True)
    (home / ".claude" / "hooks").mkdir(parents=True)
    (home / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestLocationMatchIsTheOnlyAutoSuppression:
    """陽性対照: 宣言パスにファイルが実在すれば導入済み扱い（提示なし）。無ければ提示。"""

    def test_declared_path_exists_is_not_reported_at_all(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = _mk_home(monkeypatch, tmp_path)
        target = home / ".claude" / "rules" / "already.md"
        target.write_text("本文\n", encoding="utf-8")

        art = _artifact(id="already", path=target)
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        assert detect_recommended_artifacts() == []

    def test_declared_path_missing_and_no_alt_stays_fresh(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = _mk_home(monkeypatch, tmp_path)

        art = _artifact(id="deploy-lock", path=home / ".claude" / "rules" / "deploy-lock.md")
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "deploy-lock")
        assert got is not None
        assert got["missing"] == [{"type": "rule", "path": str(art["path"])}]
        assert "covered_by" not in got

    def test_same_basename_under_a_different_parent_is_not_a_location_match(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """`refs/` 配下の同名ファイルは、宣言パスの相対位置と異なるので当たらない。"""
        home = _mk_home(monkeypatch, tmp_path)
        refs = home / ".claude" / "rules" / "refs"
        refs.mkdir(parents=True)
        (refs / "deploy-lock.md").write_text("外出しした記録\n", encoding="utf-8")

        art = _artifact(id="deploy-lock", path=home / ".claude" / "rules" / "deploy-lock.md")
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "deploy-lock")
        assert got is not None
        assert "covered_by" not in got

    def test_unrelated_skill_with_same_basename_is_not_a_location_match(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """名前族の再発防止: 無関係な skill の同名 `SKILL.md` に当たらない。"""
        home = _mk_home(monkeypatch, tmp_path)
        unrelated_skill = home / ".claude" / "skills" / "unrelated"
        (unrelated_skill / "rules").mkdir(parents=True)
        (unrelated_skill / "rules" / "commit-version.md").write_text(
            "別物\n", encoding="utf-8"
        )
        (unrelated_skill / "SKILL.md").write_text("別物\n", encoding="utf-8")

        art = _artifact(
            id="commit-version", path=home / ".claude" / "rules" / "commit-version.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "commit-version")
        assert got is not None, "本当に無い artifact が提示から消えてはならない"
        assert "covered_by" not in got


class TestAltBaseIsALocationMatch:
    def test_same_relative_path_under_project_root_counts_as_covered(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """PJ 側の `.claude/rules/<同じ相対パス>` にあれば covered。"""
        home = _mk_home(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / ".claude" / "rules" / "commit-version.md").write_text(
            "本文", encoding="utf-8"
        )

        art = _artifact(
            id="commit-version", path=home / ".claude" / "rules" / "commit-version.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "commit-version")
        assert got is not None
        assert got["covered_by"] == str(proj / ".claude" / "rules" / "commit-version.md")
        assert got["missing"] == []

    def test_same_basename_under_different_parent_in_project_root_is_not_a_match(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """PJ 側でも、相対パスの一部（basename）だけの一致では当たらない。"""
        home = _mk_home(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        refs = proj / ".claude" / "rules" / "refs"
        refs.mkdir(parents=True)
        (refs / "commit-version.md").write_text("外出しした記録\n", encoding="utf-8")

        art = _artifact(
            id="commit-version", path=home / ".claude" / "rules" / "commit-version.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "commit-version")
        assert got is not None
        assert "covered_by" not in got

    def test_covered_by_has_no_line_number(self, monkeypatch, tmp_path, no_suppression):
        """契約: 場所一致の `covered_by` はファイル全体が根拠なので行番号を持たない。"""
        home = _mk_home(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / ".claude" / "rules" / "a.md").write_text("本文\n", encoding="utf-8")

        art = _artifact(id="a", path=home / ".claude" / "rules" / "a.md")
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "a")
        assert got["covered_by"].endswith("a.md")
        assert ":" not in got["covered_by"].split("/")[-1]

    def test_non_claude_relative_path_has_no_alt_base(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """`~/.claude` 配下として表現できない宣言パス（plugin skill 等）は alt base を持たない。"""
        home = _mk_home(monkeypatch, tmp_path)
        plugin_root = tmp_path / "plugin"
        (plugin_root / "skills" / "implement").mkdir(parents=True)
        proj = tmp_path / "proj"
        (proj / ".claude").mkdir(parents=True)

        art = _artifact(
            id="implement-skill",
            type="skill",
            path=plugin_root / "skills" / "implement" / "SKILL.md",
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "implement-skill")
        assert got is not None
        assert "covered_by" not in got


class TestElementWiseJudgement:
    """rule と hook は要素単位で判定する。"""

    def test_rule_location_match_and_hook_missing_stays_fresh_with_rule_covered_by(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = _mk_home(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / ".claude" / "rules" / "worktree-parallel-work.md").write_text(
            "本文", encoding="utf-8"
        )

        art = _artifact(
            id="worktree-parallel-work",
            path=home / ".claude" / "rules" / "worktree-parallel-work.md",
            hook_path=home / ".claude" / "hooks" / "check-worktree.py",
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "worktree-parallel-work")
        assert got is not None, "hook が未導入なら一覧から消えてはならない"
        assert [m["type"] for m in got["missing"]] == ["hook"]
        assert got["rule_covered_by"] == str(
            proj / ".claude" / "rules" / "worktree-parallel-work.md"
        )
        assert "covered_by" not in got

    def test_marker_match_alone_does_not_clear_missing_hook(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """言い回し一致は印だけ。hook の実体が無ければ hook は missing に残る。"""
        home = _mk_home(monkeypatch, tmp_path)
        (home / ".claude" / "rules" / "prose.md").write_text(
            "- deploy-lock を使う\n", encoding="utf-8"
        )

        art = _artifact(
            id="deploy-lock",
            path=None,
            hook_path=home / ".claude" / "hooks" / "deploy-lock.py",
            equivalent_markers=["deploy-lock を使う"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "deploy-lock")
        assert got is not None
        assert [m["type"] for m in got["missing"]] == ["hook"]
        assert "hook_covered_by" not in got
        assert "covered_by" not in got


class TestMarkerIsAMarkOnly:
    """言い回し一致 = 印だけ。covered にはしない。"""

    def test_marker_match_stays_fresh_with_likely_covered_by(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = _mk_home(monkeypatch, tmp_path)
        (home / ".claude" / "rules" / "testing.md").write_text(
            "1行目\n- 複数ステップのコードは正常系E2Eテストを最初に書く\n", encoding="utf-8"
        )

        art = _artifact(
            id="test-happy-path-first",
            path=home / ".claude" / "rules" / "test-happy-path-first.md",
            equivalent_markers=["正常系E2Eテスト"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "test-happy-path-first")
        assert got is not None, "言い回し一致だけで提示から消えてはならない"
        assert got["missing"] == [{"type": "rule", "path": str(art["path"])}]
        assert got["likely_covered_by"].endswith("testing.md:2")
        assert "covered_by" not in got

    def test_marker_inside_negation_still_stays_fresh(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """否定文中に語が現れても、印が付くだけで隠れない（隠れないことの固定）。"""
        home = _mk_home(monkeypatch, tmp_path)
        (home / ".claude" / "rules" / "other.md").write_text(
            "- 正常系E2Eテストを最初に書くルールはまだ無い\n", encoding="utf-8"
        )

        art = _artifact(
            id="test-happy-path-first",
            path=home / ".claude" / "rules" / "test-happy-path-first.md",
            equivalent_markers=["正常系E2Eテスト"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "test-happy-path-first")
        assert got is not None
        assert got["missing"] == [{"type": "rule", "path": str(art["path"])}]
        assert got.get("likely_covered_by")

    def test_marker_search_is_scoped_to_rules_only(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """走査 root は `rules/` のみ（skills/hooks は対象外）。"""
        home = _mk_home(monkeypatch, tmp_path)
        (home / ".claude" / "skills" / "x.md").write_text("- 目印\n", encoding="utf-8")

        art = _artifact(id="a", path=home / ".claude" / "rules" / "a.md", equivalent_markers=["目印"])
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "a")
        assert got is not None
        assert "likely_covered_by" not in got

    def test_marker_that_does_not_match_stays_fresh_without_mark(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = _mk_home(monkeypatch, tmp_path)
        (home / ".claude" / "rules" / "other.md").write_text("- 無関係な本文\n", encoding="utf-8")

        art = _artifact(
            id="process-stall-guard",
            path=home / ".claude" / "rules" / "process-stall-guard.md",
            equivalent_markers=["この語はどこにもない"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "process-stall-guard")
        assert got is not None
        assert "likely_covered_by" not in got
        assert "covered_by" not in got


class TestUnreadableFileDoesNotBreakTheScan:
    def test_non_utf8_markdown_is_skipped(self, monkeypatch, tmp_path, no_suppression):
        """非 UTF-8 の *.md が1件あっても、探索は落ちず次のファイルへ進む。"""
        home = _mk_home(monkeypatch, tmp_path)
        rules = home / ".claude" / "rules"
        (rules / "aaa_broken.md").write_bytes(b"\xff\xfe not utf-8 \x00\x9c")
        (rules / "zzz_good.md").write_text("- 正常系E2Eテストを最初に書く\n", encoding="utf-8")

        art = _artifact(
            id="test-happy-path-first",
            path=rules / "test-happy-path-first.md",
            equivalent_markers=["正常系E2Eテスト"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "test-happy-path-first")
        assert got is not None
        assert "zzz_good.md" in got["likely_covered_by"]
