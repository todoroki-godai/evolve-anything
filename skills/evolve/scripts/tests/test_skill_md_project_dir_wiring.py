"""#400 codex レビュー是正（round1〜3）: SKILL.md / references/*.md の project_dir 貫通手順が
文字列だけでなく「対象 PJ を正しく指す形」になっていることを検査する契約テスト。

背景: reflect.py 等に `--project-dir` を追加しても、手順書側の実際の呼び出し文言が
`--project-dir "$(pwd)"` のように**実行元 cwd を直書き**したままだと、単一 cwd から他 PJ の
project_dir を渡すバッチ経路（#400 本体）では実行元 PJ の cwd が渡ってしまい修理が形だけに
終わる（round2 codex レビュー指摘）。round3 の是正方針は「$(pwd) の直書きをやめ、対象 PJ を
1箇所（SKILL.md Step 1 の `PJ="$(pwd)"`）で束縛し、以降の全コマンドが `"$PJ"` を参照する」形へ
統一すること。バッチ経路ではこの束縛の代入元を queue の project_path に差し替えるだけで
対応できる。round1 の「`--project-dir` という文字列の存在しか見ない」検査では
`--project-dir "$(pwd)"` のような誤った形でも緑になってしまう（round2 codex レビュー指摘）ため、
本テストは `--project-dir "$PJ"` の厳密な形と `$(pwd)` 直書きの不在を assert する。
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

# --project-dir の厳密な渡し方（$PJ を参照しているか）。
_PROJECT_DIR_PJ_RE = re.compile(r'--project-dir "\$PJ"')

# $(pwd) が --project-dir の値として直書きされている誤り（round2 指摘の再発検出）。
_PROJECT_DIR_POPEN_PWD_RE = re.compile(r'--project-dir "\$\(pwd\)"')

# PJ="$(pwd)" の唯一許容される束縛行（bash プロセスは呼び出しごとに独立するため
# 複数ブロックに同一パターンが登場してよい。これは「対象 PJ を直書きしている」誤りではなく
# 「唯一の束縛点」を各ブロック冒頭で再束縛する意図的パターン）。
_PJ_BINDING_RE = re.compile(r'^PJ="\$\(pwd\)"')


def _promote_invocation_lines(text: str) -> list:
    return [line for line in text.splitlines() if _PROMOTE_INVOCATION_RE.search(line)]


def _non_binding_pwd_lines(text: str) -> list:
    """`$(pwd)` が登場する行のうち、許容された `PJ="$(pwd)"` 束縛行以外を返す。"""
    out = []
    for line in text.splitlines():
        if "$(pwd)" not in line:
            continue
        if _PJ_BINDING_RE.match(line.strip()):
            continue
        out.append(line)
    return out


class TestSkillMdPromoteWeakHasProjectDir:
    def test_skill_md_promote_invocation_uses_pj_variable(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "SKILL.md に evolve-reflect --promote-weak の呼び出し行が見つからない"
        for line in lines:
            assert _PROJECT_DIR_PJ_RE.search(line), (
                f"SKILL.md の evolve-reflect --promote-weak 呼び出しは --project-dir \"$PJ\" の"
                f"形でなければならない（$(pwd) 直書きはバッチ経路 #400 で実行元 cwd が誤って渡る）: {line!r}"
            )

    def test_correction_review_promote_invocation_uses_pj_variable(self):
        text = CORRECTION_REVIEW_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "correction-review.md に evolve-reflect --promote-weak の呼び出し行が見つからない"
        for line in lines:
            assert _PROJECT_DIR_PJ_RE.search(line), (
                f"correction-review.md の evolve-reflect --promote-weak 呼び出しは "
                f"--project-dir \"$PJ\" の形でなければならない: {line!r}"
            )

    def test_report_narration_promote_reference_uses_pj_variable(self):
        """出力フィールド説明の言及も含め、evolve-reflect --promote-weak の文言は全箇所で統一する。"""
        text = REPORT_NARRATION_MD.read_text(encoding="utf-8")
        lines = _promote_invocation_lines(text)
        assert lines, "report-narration.md に evolve-reflect --promote-weak の言及行が見つからない"
        for line in lines:
            assert _PROJECT_DIR_PJ_RE.search(line), (
                f"report-narration.md の evolve-reflect --promote-weak 言及は "
                f"--project-dir \"$PJ\" の形でなければならない（表記不統一）: {line!r}"
            )


class TestNoRawPwdProjectDir:
    """`--project-dir "$(pwd)"` の直書きが残っていないこと（round2 codex レビュー指摘の再発防止）。

    `$PJ="$(pwd)"` という唯一の束縛行（各 bash ブロック冒頭での再束縛を含む）は許容するが、
    `--project-dir` の値として `$(pwd)` を直接渡す形は禁止する。
    """

    def test_skill_md_has_no_raw_pwd_as_project_dir(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        bad = [line for line in text.splitlines() if _PROJECT_DIR_POPEN_PWD_RE.search(line)]
        assert not bad, f"SKILL.md に --project-dir \"$(pwd)\" の直書きが残っている: {bad!r}"

    def test_skill_md_pwd_only_appears_in_pj_binding(self):
        """SKILL.md 内で $(pwd) が登場するのは PJ="$(pwd)" 束縛行のみであること。"""
        text = SKILL_MD.read_text(encoding="utf-8")
        stray = _non_binding_pwd_lines(text)
        assert not stray, (
            f"SKILL.md に $(pwd) の直書きが PJ 束縛行以外に残っている"
            f"（$PJ 変数化が漏れている疑い・#400 round3）: {stray!r}"
        )

    def test_correction_review_has_no_raw_pwd_project_dir(self):
        text = CORRECTION_REVIEW_MD.read_text(encoding="utf-8")
        bad = [line for line in text.splitlines() if _PROJECT_DIR_POPEN_PWD_RE.search(line)]
        assert not bad, f"correction-review.md に --project-dir \"$(pwd)\" の直書きが残っている: {bad!r}"

    def test_report_narration_has_no_raw_pwd_project_dir(self):
        text = REPORT_NARRATION_MD.read_text(encoding="utf-8")
        bad = [line for line in text.splitlines() if _PROJECT_DIR_POPEN_PWD_RE.search(line)]
        assert not bad, f"report-narration.md に --project-dir \"$(pwd)\" の直書きが残っている: {bad!r}"

    def test_skill_md_pj_binding_exists(self):
        """SKILL.md Step 1 に唯一の束縛点 PJ="$(pwd)" が存在すること（変数化の起点）。"""
        text = SKILL_MD.read_text(encoding="utf-8")
        bindings = [line for line in text.splitlines() if _PJ_BINDING_RE.match(line.strip())]
        assert bindings, "SKILL.md に PJ=\"$(pwd)\" の束縛行が見つからない（#400 round3 Must 1）"


class TestRemediationMdGenerateCallsUseResultProjectDir:
    """remediation.md の generate_proposals()/generate_auto_fix_summaries() 呼び出し手順が
    Path.cwd() でなく解析対象 PJ の確定値（result["project_dir"]）を渡す形になっていることを
    検査する契約テスト（#400 codex レビュー round3 Must 2）。
    """

    _CALL_RE = re.compile(r"generate_(?:proposals|auto_fix_summaries)\([^)]*\)")

    def test_remediation_md_generate_calls_pass_result_project_dir(self):
        text = REMEDIATION_MD.read_text(encoding="utf-8")
        calls = self._CALL_RE.findall(text)
        assert calls, "remediation.md に generate_proposals/generate_auto_fix_summaries の呼び出しが見つからない"
        for call in calls:
            assert 'result["project_dir"]' in call, (
                f"remediation.md の呼び出しは project_root=Path(result[\"project_dir\"]) の形で"
                f"解析対象 PJ の確定値を渡さなければならない（Path.cwd() は単一 cwd から他 PJ の "
                f"issue を渡すバッチ経路で誤った PJ を指す・#400 round3）: {call!r}"
            )
            assert "Path.cwd()" not in call, (
                f"remediation.md の呼び出しに Path.cwd() フォールバックが残っている: {call!r}"
            )
