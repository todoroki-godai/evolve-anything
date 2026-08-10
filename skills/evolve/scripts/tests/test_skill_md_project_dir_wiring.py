"""#400 codex レビュー是正（round1〜4）: SKILL.md / references/*.md 全体の project_dir 貫通手順が
文字列だけでなく「対象 PJ を正しく指す形」になっていることを検査する契約テスト。

背景（round2/round3/round4 の指摘の積み重ね）:
  - round1: `--project-dir` という文字列の存在しか見ない検査では `--project-dir "$(pwd)"` の
    ような誤った形でも緑になる。
  - round2: `--project-dir "$(pwd)"` / `project_root=Path.cwd()` を「足すだけ」では、単一 cwd
    から他 PJ の project_dir を渡すバッチ経路（#400 本体）で実行元 cwd が再選択され修理が
    形だけに終わる。→ `$PJ` 変数への統一に変更。
  - round3: SKILL.md 側だけを直し references/ 配下の `$(pwd)` 直書きを見落とした。
    → references/ 配下も全ファイル対象にする（司令塔がスコープ判断を撤回）。
  - round4: `$PJ` はシェルプロセスをまたがない（bash は Bash tool 呼び出しごとに独立プロセス）
    ため、束縛行の無いブロックで `$PJ` を参照すると空文字になる。さらに suppression ledger
    等の**書込経路**で無引数 `resolve_slug()` / `Path.cwd()` / 空文字 fallback の
    `CLAUDE_PROJECT_DIR` が残っていると、対象 PJ でなく実行元 PJ に書き込む。
    → ブロック単位で束縛行の有無を検査し、書込経路の無引数呼び出しも検出する。

本テストは SKILL.md と references/ 配下の**全 .md ファイル**を対象に、
(1) fenced ```bash ブロック単位で「$PJ を使うなら同じブロック内に束縛行がある」こと、
(2) 単発の呼び出し文言（コードブロック外の inline mention）は同じ行に束縛が同居しているか、
    出力フィールドの説明（新規実行を伴わない）であること、
(3) references/ の python スニペットに無引数 `resolve_slug()` / 素の `Path.cwd()` /
    空文字 fallback の `CLAUDE_PROJECT_DIR` 読み取りが残っていないこと、
を assert する。
"""
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT / "scripts" / "lib") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "lib"))

SKILL_MD = _REPO_ROOT / "skills" / "evolve" / "SKILL.md"
REFERENCES_DIR = _REPO_ROOT / "skills" / "evolve" / "references"

ALL_TARGET_MD_FILES = [SKILL_MD] + sorted(REFERENCES_DIR.glob("*.md"))

# --project-dir "$PJ" の厳密な渡し方。
_PROJECT_DIR_PJ_RE = re.compile(r'--project-dir "\$PJ"')

# $(pwd) が --project-dir の値として直書きされている誤り（round2 指摘の再発検出）。
_PROJECT_DIR_POPEN_PWD_RE = re.compile(r'--project-dir "\$\(pwd\)"')

# PJ="${PJ:-$(pwd)}"（推奨形） または PJ="$(pwd)"（旧形）の束縛行。
_PJ_BINDING_RE = re.compile(r'^PJ="(\$\{PJ:-\$\(pwd\)\}|\$\(pwd\))"')

# fenced code block 抽出（```lang\n...\n```）。リスト項目内でインデントされたブロック
# （開始・終了フェンスが同じ字下げ幅）にも対応するため、字下げをグループ化し閉じフェンスで
# 同じ字下げをバックリファレンスで要求する（さもないと閉じフェンスを見失い、後方の別ブロックの
# 閉じフェンスまで誤って呑み込む）。
_FENCED_BLOCK_RE = re.compile(r"^([ \t]*)```(\w*)\n(.*?)\n\1```", re.DOTALL | re.MULTILINE)

# 出力フィールドの説明（新規実行を指示しない）を示す近傍語。
_OUTPUT_REFERENCE_MARKERS = ("出力の", "の出力")


def _fenced_blocks(text: str):
    """(lang, block_text) のリストを返す（lang は空文字列のこともある）。"""
    return [(m.group(2), m.group(3)) for m in _FENCED_BLOCK_RE.finditer(text)]


def _strip_fenced_blocks(text: str) -> str:
    """fenced code block を取り除いた残りのテキスト（inline 検査用）。"""
    return _FENCED_BLOCK_RE.sub("", text)


def _block_has_pj_binding(block_text: str) -> bool:
    return any(_PJ_BINDING_RE.match(line.strip()) for line in block_text.splitlines())


def _block_uses_pj(block_text: str) -> bool:
    return "$PJ" in block_text


class TestBashBlocksBindPjBeforeUse:
    """fenced ```bash ブロックが $PJ を使うなら、同じブロック内に束縛行があること（round4 Must 2）。"""

    def test_all_bash_blocks_using_pj_have_binding(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                if _block_uses_pj(block) and not _block_has_pj_binding(block):
                    violations.append((md_file.name, block[:200]))
        assert not violations, (
            "$PJ を使う bash ブロックに束縛行（PJ=\"${PJ:-$(pwd)}\" 相当）が無い"
            "（bash は呼び出しごとに独立プロセスのため $PJ が空文字になる・#400 round4）: "
            f"{violations!r}"
        )

    def test_at_least_one_bash_block_uses_pj(self):
        """検査自体が空振りでないことの自己チェック（clean pass の偽陰性防止）。"""
        found = False
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang == "bash" and _block_uses_pj(block):
                    found = True
        assert found, "どの bash ブロックも $PJ を使っていない（検査対象が消失している疑い）"


class TestInlineProjectDirMentionsAreSelfContained:
    """コードブロック外（inline backtick）の --project-dir "$PJ" 言及が、
    新規実行を指示する場合は同じ行に束縛を伴う、出力フィールド参照の場合は除外する。
    """

    def _is_output_reference(self, line: str, match_end: int) -> bool:
        tail = line[match_end:match_end + 20]
        return any(marker in tail for marker in _OUTPUT_REFERENCE_MARKERS)

    def test_inline_invocation_mentions_bind_pj_on_same_line(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = _strip_fenced_blocks(md_file.read_text(encoding="utf-8"))
            for line in text.splitlines():
                m = _PROJECT_DIR_PJ_RE.search(line)
                if not m:
                    continue
                if self._is_output_reference(line, m.end()):
                    continue  # 出力フィールドの説明。新規実行を伴わないため束縛不要。
                if "PJ=" not in line:
                    violations.append((md_file.name, line[:200]))
        assert not violations, (
            "コードブロック外で --project-dir \"$PJ\" を呼び出す inline 記述に、同じ行の"
            "束縛（PJ=...）が無い（bash は呼び出しごとに独立プロセスのため空文字になる・"
            f"#400 round4）: {violations!r}"
        )


class TestNoRawPwdOrUnboundProjectDir:
    """`--project-dir "$(pwd)"` の直書きが SKILL.md / references 配下のどこにも残っていないこと。"""

    def test_no_raw_pwd_project_dir_anywhere(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                if _PROJECT_DIR_POPEN_PWD_RE.search(line):
                    violations.append((md_file.name, line[:200]))
        assert not violations, (
            f"--project-dir \"$(pwd)\" の直書きが残っている: {violations!r}"
        )

    def test_pwd_only_appears_in_pj_binding_pattern(self):
        """`$(pwd)` が登場する行は、必ず `PJ="` 束縛（standalone 行 / inline `PJ="..." && ...` /
        束縛パターンを説明する散文中の言及のいずれか）を伴うこと。`--project-dir` の値として
        `$(pwd)` を直接渡す（PJ= を経由しない）誤りだけを検出する。
        """
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                if "$(pwd)" not in line:
                    continue
                if 'PJ="' in line:
                    continue
                violations.append((md_file.name, line[:200]))
        assert not violations, (
            f"$(pwd) の直書きが PJ 束縛（PJ=\"...\"）を伴わずに残っている（$PJ 変数化が漏れている疑い）: {violations!r}"
        )

    def test_skill_md_pj_binding_exists(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        bindings = [line for line in text.splitlines() if _PJ_BINDING_RE.match(line.strip())]
        assert bindings, "SKILL.md に PJ 束縛行が見つからない（#400 round3 Must 1）"


class TestReferencesHaveNoUnattributedWritePaths:
    """references/ の python スニペットに無引数 resolve_slug() / 素の Path.cwd() /
    空文字 fallback の CLAUDE_PROJECT_DIR 読み取りが残っていないこと（round4 Must 3）。
    """

    _BARE_RESOLVE_SLUG_RE = re.compile(r"(?<!def )resolve_slug\(\s*\)")
    _BARE_PATH_CWD_RE = re.compile(r"Path\.cwd\(\)")
    _EMPTY_FALLBACK_PROJECT_DIR_RE = re.compile(
        r'os\.environ\.get\(\s*["\']CLAUDE_PROJECT_DIR["\']\s*,\s*["\']["\']\s*\)'
    )

    def _code_lines(self, md_file: Path):
        """python fenced block の非コメント行を返す。"""
        text = md_file.read_text(encoding="utf-8")
        out = []
        for lang, block in _fenced_blocks(text):
            if lang != "python":
                continue
            for line in block.splitlines():
                code_part = line.split("#", 1)[0]
                out.append((line, code_part))
        return out

    def test_no_bare_resolve_slug_calls(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            for raw, code in self._code_lines(md_file):
                if self._BARE_RESOLVE_SLUG_RE.search(code):
                    violations.append((md_file.name, raw[:200]))
        assert not violations, (
            f"無引数 resolve_slug() が references の python スニペットに残っている"
            f"（書込経路が cwd 側 PJ に誤帰属する・#400 round4）: {violations!r}"
        )

    def test_no_bare_path_cwd_calls(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            for raw, code in self._code_lines(md_file):
                if self._BARE_PATH_CWD_RE.search(code):
                    violations.append((md_file.name, raw[:200]))
        assert not violations, (
            f"素の Path.cwd() が references の python スニペットに残っている"
            f"（単一 cwd から他 PJ を渡すバッチ経路で誤った PJ を指す・#400 round4）: {violations!r}"
        )

    def test_no_empty_fallback_claude_project_dir(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            for raw, code in self._code_lines(md_file):
                if self._EMPTY_FALLBACK_PROJECT_DIR_RE.search(code):
                    violations.append((md_file.name, raw[:200]))
        assert not violations, (
            f'os.environ.get("CLAUDE_PROJECT_DIR", "") の空文字 fallback が残っている'
            f"（未設定/バッチ経路で誤った PJ・空 slug になる・#400 round4）: {violations!r}"
        )


class TestRemediationMdGenerateCallsUseResultProjectDir:
    """remediation.md の generate_proposals()/generate_auto_fix_summaries() 呼び出し手順が
    Path.cwd() でなく解析対象 PJ の確定値（result["project_dir"]）を渡す形になっていることを
    検査する契約テスト（#400 codex レビュー round3 Must 2）。
    """

    REMEDIATION_MD = REFERENCES_DIR / "remediation.md"
    _CALL_RE = re.compile(r"generate_(?:proposals|auto_fix_summaries)\([^)]*\)")

    def test_remediation_md_generate_calls_pass_result_project_dir(self):
        text = self.REMEDIATION_MD.read_text(encoding="utf-8")
        calls = self._CALL_RE.findall(text)
        assert calls, "remediation.md に generate_proposals/generate_auto_fix_summaries の呼び出しが見つからない"
        for call in calls:
            assert 'result["project_dir"]' in call, (
                f"remediation.md の呼び出しは project_root=Path(result[\"project_dir\"]) の形で"
                f"解析対象 PJ の確定値を渡さなければならない: {call!r}"
            )
            assert "Path.cwd()" not in call, (
                f"remediation.md の呼び出しに Path.cwd() フォールバックが残っている: {call!r}"
            )


class TestPromoteWeakInvocationsUseProjectDirPj:
    """evolve-reflect --promote-weak の呼び出し・言及行が --project-dir "$PJ" の形であること。"""

    _PROMOTE_INVOCATION_RE = re.compile(r"evolve-reflect [^`]*--promote-weak")

    def _promote_lines(self, md_file: Path):
        text = md_file.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if self._PROMOTE_INVOCATION_RE.search(line)]

    def test_all_promote_weak_mentions_use_pj(self):
        checked_any = False
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            for line in self._promote_lines(md_file):
                checked_any = True
                if not _PROJECT_DIR_PJ_RE.search(line):
                    violations.append((md_file.name, line[:200]))
        assert checked_any, "evolve-reflect --promote-weak の言及行が見つからない（検査対象消失の疑い）"
        assert not violations, (
            f"evolve-reflect --promote-weak の言及が --project-dir \"$PJ\" の形になっていない: {violations!r}"
        )
