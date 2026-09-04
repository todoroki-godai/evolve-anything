"""推奨 artifact の「同じものが既に無いか」を実物で確認する（#622 の実測から）。

`detect_recommended_artifacts` は決め打ちパスの `exists()` だけで導入状態を判定して
いたため、**同じ内容が別名・別ディレクトリにあっても「未導入」として提案していた**。

実測（2026-09-05・実 rules 2ディレクトリ）: 12 件の提案のうち 10 件が既存 rules に
書かれており、朝の y/n に「すでにあるルールを作りませんか」と出る状態だった。

判定に使う識別は ①ファイル名の一致 ②本文の文字列一致 の 2 つで、いずれも
**既知の言い回しのみ検出でき、書き換えられれば当たらない**。ゆえに提案を消さず
`covered_by`（根拠 file:line）を付けて下げるだけにし、下げた件数と内訳は
呼び出し側が必ず surface する（`.claude/rules/no-denylist-checks.md`）。
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


class TestExistingArtifactIsFound:
    def test_same_name_in_project_rules_counts_as_covered(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """決め打ちパスに無くても、PJ 側に同名ファイルがあれば covered。"""
        home = tmp_path / "home"
        (home / ".claude" / "rules").mkdir(parents=True)
        proj = tmp_path / "proj"
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / ".claude" / "rules" / "commit-version.md").write_text("本文", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="commit-version", path=home / ".claude" / "rules" / "commit-version.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "commit-version")
        assert got is not None
        assert got["covered_by"].endswith("commit-version.md")

    def test_marker_in_differently_named_file_counts_as_covered(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """ファイル名が違っても、本文の特徴語が見つかれば covered。"""
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "testing.md").write_text(
            "- 複数ステップのコードは正常系E2Eテストを最初に書く\n", encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="test-happy-path-first",
            path=rules / "test-happy-path-first.md",
            equivalent_markers=["正常系E2Eテスト"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "test-happy-path-first")
        assert got is not None
        assert "testing.md:1" in got["covered_by"]

    def test_marker_is_searched_recursively_including_refs(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """refs/ のような下位ディレクトリも探す（外出し先を見落とさない）。"""
        home = tmp_path / "home"
        refs = home / ".claude" / "rules" / "refs"
        refs.mkdir(parents=True)
        (refs / "workflow.md").write_text(
            "- gstack フローチェーン: `~/.gstack/flow-chain.json`\n", encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="gstack-flow-chain",
            path=home / ".claude" / "rules" / "gstack-flow-chain.md",
            equivalent_markers=["flow-chain.json"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "gstack-flow-chain")
        assert got is not None
        assert got["covered_by"]


class TestGenuinelyMissingIsStillProposed:
    """陽性対照: 本当に無いものを取り下げない（誤って提案を止めない）。"""

    def test_no_marker_and_no_file_stays_proposed(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = tmp_path / "home"
        (home / ".claude" / "rules").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(id="deploy-lock", path=home / ".claude" / "rules" / "deploy-lock.md")
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "deploy-lock")
        assert got is not None
        assert "covered_by" not in got

    def test_marker_that_does_not_match_stays_proposed(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "other.md").write_text("- 無関係な本文\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="process-stall-guard",
            path=rules / "process-stall-guard.md",
            equivalent_markers=["この語はどこにもない"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "process-stall-guard")
        assert got is not None
        assert "covered_by" not in got

    def test_installed_artifact_is_not_reported_at_all(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """決め打ちパスに実在するものは、そもそも一覧に出ない（従来どおり）。"""
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        target = rules / "already.md"
        target.write_text("本文\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(id="already", path=target)
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        assert detect_recommended_artifacts() == []


class TestHookIsNotCoveredByProse:
    """hook は本文の記述では代替できない（実体が要る）。"""

    def test_marker_does_not_mark_missing_hook_as_installed(
        self, monkeypatch, tmp_path, no_suppression
    ):
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (home / ".claude" / "hooks").mkdir(parents=True)
        (rules / "doc.md").write_text("- deploy-lock を使う\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

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

    def test_rule_covered_by_prose_does_not_install_the_hook(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """rule 側が本文でカバーされていても、hook の実体が無ければ hook は残る。

        rule と hook の両方が未導入の artifact で、rule だけが特徴語に当たる場合に
        hook まで導入済み扱いになると、機械で止める仕掛けが黙って消える。
        """
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (home / ".claude" / "hooks").mkdir(parents=True)
        (rules / "prose.md").write_text(
            "- 並行編集する worker が2体以上なら worktree で隔離する\n", encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="worktree-parallel-work",
            path=rules / "worktree-parallel-work.md",
            hook_path=home / ".claude" / "hooks" / "worktree-guard.py",
            equivalent_markers=["worktree で隔離する"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "worktree-parallel-work")
        assert got is not None, "hook が未導入なら一覧から消えてはならない"
        assert "hook" in [m["type"] for m in got["missing"]]
        assert got.get("covered_by"), "rule 側のカバー根拠は残る"


class TestNameCollisionDoesNotHideMissingArtifacts:
    """外部レビュー巡1 [Must]: 総称的なファイル名で別物を根拠にしない。"""

    def test_unrelated_skill_md_is_not_a_cover(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """skill の実体は常に `SKILL.md`。basename 一致だと別 skill に当たる。"""
        home = tmp_path / "home"
        skills = home / ".claude" / "skills"
        (skills / "unrelated").mkdir(parents=True)
        (skills / "unrelated" / "SKILL.md").write_text("別物\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="commit-skill", type="skill", path=skills / "commit" / "SKILL.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "commit-skill")
        assert got is not None, "本当に無い skill が提示から消えてはならない"
        assert "covered_by" not in got

    def test_same_skill_name_in_project_is_a_cover(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """陽性対照: 同じ skill 名なら PJ 側にあっても covered。"""
        home = tmp_path / "home"
        (home / ".claude" / "skills").mkdir(parents=True)
        proj = tmp_path / "proj"
        (proj / ".claude" / "skills" / "commit").mkdir(parents=True)
        (proj / ".claude" / "skills" / "commit" / "SKILL.md").write_text(
            "本文\n", encoding="utf-8"
        )
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="commit-skill",
            type="skill",
            path=home / ".claude" / "skills" / "commit" / "SKILL.md",
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "commit-skill")
        assert got is not None
        assert got["covered_by"].endswith("commit/SKILL.md")

    def test_same_basename_under_a_different_parent_is_not_a_cover(
        self, monkeypatch, tmp_path, no_suppression
    ):
        """rules 直下を探す artifact が、別ディレクトリの同名ファイルに当たらない。"""
        home = tmp_path / "home"
        refs = home / ".claude" / "rules" / "refs"
        refs.mkdir(parents=True)
        (refs / "deploy-lock.md").write_text("外出しした記録\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="deploy-lock", path=home / ".claude" / "rules" / "deploy-lock.md"
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "deploy-lock")
        assert got is not None
        assert "covered_by" not in got


class TestUnreadableFileDoesNotBreakTheScan:
    def test_non_utf8_markdown_is_skipped(self, monkeypatch, tmp_path, no_suppression):
        """非 UTF-8 の *.md が1件あっても、探索は落ちず次のファイルへ進む。"""
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "aaa_broken.md").write_bytes(b"\xff\xfe not utf-8 \x00\x9c")
        (rules / "zzz_good.md").write_text("- 正常系E2Eテストを最初に書く\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(
            id="test-happy-path-first",
            path=rules / "test-happy-path-first.md",
            equivalent_markers=["正常系E2Eテスト"],
        )
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "test-happy-path-first")
        assert got is not None
        assert "zzz_good.md" in got["covered_by"]


class TestCoveredByFormat:
    def test_marker_hit_carries_a_line_number(self, monkeypatch, tmp_path, no_suppression):
        home = tmp_path / "home"
        rules = home / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "x.md").write_text("1行目\n- 目印\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(id="a", path=rules / "a.md", equivalent_markers=["目印"])
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(), "a")
        assert got["covered_by"].endswith("x.md:2")

    def test_same_name_hit_has_no_line_number(self, monkeypatch, tmp_path, no_suppression):
        """契約は `file[:line]`。同名一致はファイル全体が根拠なので位置を持たない。"""
        home = tmp_path / "home"
        (home / ".claude" / "rules").mkdir(parents=True)
        proj = tmp_path / "proj"
        (proj / ".claude" / "rules").mkdir(parents=True)
        (proj / ".claude" / "rules" / "a.md").write_text("本文\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        art = _artifact(id="a", path=home / ".claude" / "rules" / "a.md")
        monkeypatch.setattr(discover, "RECOMMENDED_ARTIFACTS", [art])
        got = _by_id(detect_recommended_artifacts(project_root=proj), "a")
        assert got["covered_by"].endswith("a.md")
        assert not got["covered_by"].split("/")[-1].count(":")
