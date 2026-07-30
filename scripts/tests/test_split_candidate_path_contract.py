#!/usr/bin/env python3
"""split_candidate の file パス契約テスト（#306）。

reorganize が検出した SKILL.md の**実パス**が issue["file"] まで欠落なく届くことを担保する。
以前は `make_split_candidate_issue` が skill 名から `.claude/skills/<name>/SKILL.md` を
組み立て直していたため、repo 直下 `skills/<name>/SKILL.md` レイアウト（plugin_self origin,
#185）では実在しないパスになり、承認しても `fix_split_candidate` が
`SKILL.md not found` で早期 return する silent no-op になっていた。
"""
import sys
from pathlib import Path

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "reorganize" / "scripts"))

import reorganize  # noqa: E402
from issue_schema import make_split_candidate_issue  # noqa: E402
from remediation import FIX_DISPATCH  # noqa: E402


def _write_long_skill(skill_dir: Path, name: str, lines: int = 400) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        f"# {name}\n" + "\n".join(f"Line {i}" for i in range(lines)),
        encoding="utf-8",
    )
    return path


class TestSplitCandidateCarriesRealPath:
    def test_detect_carries_actual_path(self, tmp_path):
        """detect_split_candidates は検出元の実パスを候補に載せる。"""
        skill_md = _write_long_skill(tmp_path / "skills" / "long-skill", "long-skill")

        candidates = reorganize.detect_split_candidates({"skills": [skill_md], "rules": []})

        assert len(candidates) == 1
        assert candidates[0]["path"] == str(skill_md)

    def test_issue_file_exists_for_repo_root_layout(self, tmp_path):
        """repo 直下 skills/ レイアウト（plugin_self origin）でも issue["file"] が実在する。

        #306 の regression: 以前は `.claude/skills/<name>/SKILL.md` を組み立てていたため
        このレイアウトでは必ず不在パスになった。
        """
        skill_md = _write_long_skill(tmp_path / "skills" / "evolve", "evolve")

        candidates = reorganize.detect_split_candidates({"skills": [skill_md], "rules": []})
        issue = make_split_candidate_issue(candidates[0])

        assert Path(issue["file"]).exists(), issue["file"]
        assert Path(issue["file"]) == skill_md

    def test_fixer_succeeds_end_to_end(self, tmp_path):
        """検出 → issue 化 → fixer が fixed=True で通る（silent no-op しない）。"""
        skill_md = _write_long_skill(tmp_path / "skills" / "spec-keeper", "spec-keeper")

        candidates = reorganize.detect_split_candidates({"skills": [skill_md], "rules": []})
        issue = make_split_candidate_issue(candidates[0])
        results = FIX_DISPATCH["split_candidate"]([issue])

        assert len(results) == 1
        assert results[0]["fixed"] is True, results[0].get("error")
        assert results[0]["error"] is None

    def test_path_missing_falls_back_to_legacy_shape(self):
        """path キーが無い旧形式の候補でも従来のパス生成にフォールバックする。"""
        issue = make_split_candidate_issue({"skill_name": "legacy", "line_count": 400})

        assert issue["file"] == ".claude/skills/legacy/SKILL.md"


class TestUnresolvableFixTargets:
    """FIX_DISPATCH に載る issue の file 実在率を観測できること（#306 の3点目）。"""

    def test_detects_missing_target(self, tmp_path):
        from remediation import detect_unresolvable_fix_targets

        real = _write_long_skill(tmp_path / "skills" / "ok", "ok")
        classified = {
            "auto_fixable": [
                {"type": "split_candidate", "file": str(real)},
            ],
            "proposable": [
                {"type": "split_candidate", "file": str(tmp_path / "nope" / "SKILL.md")},
            ],
        }

        res = detect_unresolvable_fix_targets(classified)

        assert res["count"] == 1
        assert res["by_type"] == {"split_candidate": 1}
        assert res["files"] == [str(tmp_path / "nope" / "SKILL.md")]

    def test_clean_when_all_exist(self, tmp_path):
        from remediation import detect_unresolvable_fix_targets

        real = _write_long_skill(tmp_path / "skills" / "ok", "ok")
        res = detect_unresolvable_fix_targets(
            {"auto_fixable": [{"type": "split_candidate", "file": str(real)}]}
        )

        assert res["count"] == 0
        assert res["by_type"] == {}

    def test_ignores_types_without_fixer_and_empty_file(self):
        """FIX_DISPATCH に無い type と file 空文字は対象外（FP を作らない）。"""
        from remediation import detect_unresolvable_fix_targets

        res = detect_unresolvable_fix_targets(
            {
                "proposable": [
                    {"type": "no_such_fixer_type", "file": "/definitely/missing.md"},
                    {"type": "split_candidate", "file": ""},
                ]
            }
        )

        assert res["count"] == 0

    def test_proposal_only_types_are_not_flagged(self):
        """FIX_DISPATCH 経由でもファイルを読まない proposal 専用 type は対象外にしない。

        現状は「fixer が file を読む」type のみ対象。dispatch に載る type は
        すべて file 実在を前提にするため、ここでは dispatch キーで絞る。
        """
        from remediation import detect_unresolvable_fix_targets

        assert "split_candidate" in FIX_DISPATCH
        res = detect_unresolvable_fix_targets({})
        assert res == {"count": 0, "by_type": {}, "files": []}
