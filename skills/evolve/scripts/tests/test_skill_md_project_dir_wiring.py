"""#400 codex レビュー是正: SKILL.md / references/correction-review.md の
`evolve-reflect --promote-weak` 呼び出し手順が `--project-dir` を渡す形になっていることを
検査する契約テスト。

背景: reflect.py に `--project-dir` を追加しても（unit テストでフラグ単体しか検査しなければ）
手順書側の実際の呼び出し文言が更新されないまま残ると、単一 cwd から他 PJ のバッチ実行時に
実行元 PJ の cwd/env にフォールバックし、対象 PJ でなく実行元 PJ に correction が誤帰属する
（今回塞ぐはずだった silent 消失そのもの）。手順書の文言 drift をコードの unit テストでは
検出できないため、文言自体を静的に検査する。
"""
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT / "scripts" / "lib") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))

SKILL_MD = _REPO_ROOT / "skills" / "evolve" / "SKILL.md"
CORRECTION_REVIEW_MD = _REPO_ROOT / "skills" / "evolve" / "references" / "correction-review.md"
REPORT_NARRATION_MD = _REPO_ROOT / "skills" / "evolve" / "references" / "report-narration.md"
REMEDIATION_MD = _REPO_ROOT / "skills" / "evolve" / "references" / "remediation.md"

# 「昇格を実行する」呼び出し命令行（出力フィールド名の説明行は対象外・report-narration.md 等）。
_PROMOTE_INVOCATION_RE = re.compile(r"evolve-reflect [^`]*--promote-weak")


def _promote_invocation_lines(text: str) -> list:
    return [line for line in text.splitlines() if _PROMOTE_INVOCATION_RE.search(line)]


class TestSkillMdPromoteWeakHasProjectDir:
    def test_skill_md_promote_invocation_has_project_dir(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "SKILL.md に evolve-reflect --promote-weak の呼び出し行が見つからない"
        for line in lines:
            assert "--project-dir" in line, (
                f"SKILL.md の evolve-reflect --promote-weak 呼び出しに --project-dir が"
                f"欠落している（単一 cwd バッチ経路で誤帰属する・#400）: {line!r}"
            )

    def test_correction_review_promote_invocation_has_project_dir(self):
        text = CORRECTION_REVIEW_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "correction-review.md に evolve-reflect --promote-weak の呼び出し行が見つからない"
        for line in lines:
            assert "--project-dir" in line, (
                f"correction-review.md の evolve-reflect --promote-weak 呼び出しに "
                f"--project-dir が欠落している（単一 cwd バッチ経路で誤帰属する・#400）: {line!r}"
            )

    def test_report_narration_promote_reference_has_project_dir(self):
        """出力フィールド説明の言及も含め、evolve-reflect --promote-weak の文言は全箇所で統一する。"""
        text = REPORT_NARRATION_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "report-narration.md に evolve-reflect --promote-weak の言及行が見つからない"
        for line in lines:
            assert "--project-dir" in line, (
                f"report-narration.md の evolve-reflect --promote-weak 言及に --project-dir が"
                f"欠落している（表記不統一・#400）: {line!r}"
            )


class TestRemediationMdGenerateCallsHaveProjectRoot:
    """remediation.md の generate_proposals()/generate_auto_fix_summaries() 呼び出し手順が
    project_root= を渡す形になっていることを検査する契約テスト（#400 codex レビュー是正）。
    """

    _CALL_RE = re.compile(r"generate_(?:proposals|auto_fix_summaries)\([^)]*\)")

    def test_remediation_md_generate_calls_pass_project_root(self):
        text = REMEDIATION_MD.read_text(encoding="utf-8")
        calls = self._CALL_RE.findall(text)
        assert calls, "remediation.md に generate_proposals/generate_auto_fix_summaries の呼び出しが見つからない"
        for call in calls:
            assert "project_root" in call, (
                f"remediation.md の呼び出しに project_root= が欠落している"
                f"（paths_suggestion 生成が暗黙 Path.cwd() フォールバックに依存する・#400）: {call!r}"
            )
