"""#400 codex レビュー是正（round1〜5）: SKILL.md / references/*.md 全体の project_dir 貫通手順が
文字列だけでなく「対象 PJ を正しく指す形」になっていることを検査する契約テスト。

背景（round2〜round5 の指摘の積み重ね）:
  - round1: `--project-dir` という文字列の存在しか見ない検査では `--project-dir "$(pwd)"` の
    ような誤った形でも緑になる。
  - round2: `--project-dir "$(pwd)"` / `project_root=Path.cwd()` を「足すだけ」では、単一 cwd
    から他 PJ の project_dir を渡すバッチ経路（#400 本体）で実行元 cwd が再選択され修理が
    形だけに終わる。→ `$PJ` 変数への統一に変更。
  - round3: SKILL.md 側だけを直し references/ 配下の `$(pwd)` 直書きを見落とした。
    → references/ 配下も全ファイル対象にする。
  - round4: `$PJ` はシェルプロセスをまたがない（bash は Bash tool 呼び出しごとに独立プロセス）
    ため、束縛行の無いブロックで `$PJ` を参照すると空文字になる。さらに suppression ledger
    等の**書込経路**で無引数 `resolve_slug()` / `Path.cwd()` / 空文字 fallback の
    `CLAUDE_PROJECT_DIR` が残っていると、対象 PJ でなく実行元 PJ に書き込む。
  - round5: round4 の契約テストにも5つの盲点があった:
    1. fenced ```python ブロックしか検査せず、bash ブロック内の `python3 -c "..."` を見ていない
       （SKILL.md Step 0.5 / world-context.md / report-narration.md の `resolve_slug()` 呼び出しは
       すべて `python3 -c` の中にあり、素通りしていた）
    2. `$PJ` の束縛しか見ておらず、`$SLUG` の束縛を検査していない（別ブロックで `$SLUG` を前提に
       すると空文字になる同型バグ）
    3. 旧形 `PJ="$(pwd)"`（`:-` 無し・env 上書き不可）も束縛として合格させていた
    4. 束縛が使用より前にあることを検査していない（束縛行がブロック末尾にあっても緑になっていた）
    5. `git rev-parse --show-toplevel` による slug 再導出（ADR-031 の `--git-common-dir` と異なり
       worktree で本体と食い違う既知バグ）を検出しない

本テストは 5 点すべてをカバーする。
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

# PJ="${PJ:-$(pwd)}"（推奨形のみ）の束縛行。旧形 PJ="$(pwd)"（:- 無し）は env の PJ を
# 上書きしてしまうため round5 で非推奨・非合格化した（round4 の盲点3）。
_PJ_BINDING_RE = re.compile(r'^PJ="\$\{PJ:-\$\(pwd\)\}"')

# 旧形（:- 無し）の検出用（非推奨形が紛れ込んでいないかの検査に使う）。
_PJ_BINDING_OLD_FORM_RE = re.compile(r'^PJ="\$\(pwd\)"$')

# SLUG="..." 形の束縛行（導出方法は resolve_slug 経由なら形が一定しないため緩く判定する）。
_SLUG_BINDING_RE = re.compile(r'^SLUG="')

# fenced code block 抽出（```lang\n...\n```）。リスト項目内でインデントされたブロック
# （開始・終了フェンスが同じ字下げ幅）にも対応するため、字下げをグループ化し閉じフェンスで
# 同じ字下げをバックリファレンスで要求する（さもないと閉じフェンスを見失い、後方の別ブロックの
# 閉じフェンスまで誤って呑み込む）。
_FENCED_BLOCK_RE = re.compile(r"^([ \t]*)```(\w*)\n(.*?)\n\1```", re.DOTALL | re.MULTILINE)

# bash ブロック内に埋め込まれた python3 -c "..." / python3 -c '...' の中身を抽出する
# （round5 盲点1: fenced ```python ブロックしか見ないと python3 -c 埋め込みを見落とす）。
_PYTHON_DASH_C_RE = re.compile(r"""python3\s+-c\s+"((?:[^"\\]|\\.)*)"|python3\s+-c\s+'((?:[^'\\]|\\.)*)'""", re.DOTALL)

# git rev-parse --show-toplevel による slug 再導出（ADR-031 は --git-common-dir 親を正とする。
# --show-toplevel は worktree で本体 PJ と食い違う既知の別バグ・round5 盲点5）。
_SHOW_TOPLEVEL_RE = re.compile(r"git rev-parse --show-toplevel")

# 出力フィールドの説明（新規実行を指示しない）を示す近傍語。
_OUTPUT_REFERENCE_MARKERS = ("出力の", "の出力")


def _fenced_blocks(text: str):
    """(lang, block_text) のリストを返す（lang は空文字列のこともある）。"""
    return [(m.group(2), m.group(3)) for m in _FENCED_BLOCK_RE.finditer(text)]


def _strip_fenced_blocks(text: str) -> str:
    """fenced code block を取り除いた残りのテキスト（inline 検査用）。"""
    return _FENCED_BLOCK_RE.sub("", text)


def _block_has_binding(block_text: str, binding_re: "re.Pattern") -> bool:
    return any(binding_re.match(line.strip()) for line in block_text.splitlines())


def _block_uses_var(block_text: str, token: str) -> bool:
    return token in block_text


def _embedded_python_snippets(text: str):
    """テキスト中の python3 -c "..." / '...' の中身を抽出する（bash ブロック内でも検出可能）。"""
    out = []
    for m in _PYTHON_DASH_C_RE.finditer(text):
        snippet = m.group(1) if m.group(1) is not None else m.group(2)
        out.append(snippet)
    return out


def _binding_and_first_usage_idx(block_text: str, binding_re: "re.Pattern", token: str):
    """束縛行のインデックスと、束縛行を除いた最初の使用行のインデックスを返す（順序検査用）。"""
    lines = block_text.splitlines()
    binding_idx = None
    first_usage_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if binding_idx is None and binding_re.match(stripped):
            binding_idx = i
            continue
        # コメント（行頭 # または コード部の # 以降）は「使用」に数えない。解説コメントが
        # 束縛行より前に変数名へ言及しているだけの偽陽性を避ける。
        code_part = line.split("#", 1)[0]
        if first_usage_idx is None and token in code_part:
            first_usage_idx = i
    return binding_idx, first_usage_idx


class TestBashBlocksBindPjBeforeUse:
    """fenced ```bash ブロックが $PJ を使うなら、同じブロック内に束縛行（かつ使用より前）があること。"""

    def test_all_bash_blocks_using_pj_have_binding(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                if _block_uses_var(block, "$PJ") and not _block_has_binding(block, _PJ_BINDING_RE):
                    violations.append((md_file.name, block[:200]))
        assert not violations, (
            "$PJ を使う bash ブロックに束縛行（PJ=\"${PJ:-$(pwd)}\"）が無い"
            "（bash は呼び出しごとに独立プロセスのため $PJ が空文字になる・#400 round4）: "
            f"{violations!r}"
        )

    def test_pj_binding_precedes_first_usage(self):
        """round5 盲点4: 束縛行がブロック内にあっても、使用より後ろにあれば無意味（空文字参照）。"""
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                if not _block_uses_var(block, "$PJ"):
                    continue
                binding_idx, usage_idx = _binding_and_first_usage_idx(block, _PJ_BINDING_RE, "$PJ")
                if usage_idx is not None and (binding_idx is None or usage_idx < binding_idx):
                    violations.append((md_file.name, block[:200]))
        assert not violations, (
            f"$PJ の束縛行が最初の使用より後ろにある（#400 round5 盲点4）: {violations!r}"
        )

    def test_no_old_form_pj_binding(self):
        """round5 盲点3: 旧形 PJ="$(pwd)"（:- 無し）は env の PJ を上書きするため非合格化する。"""
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                if _PJ_BINDING_OLD_FORM_RE.match(line.strip()):
                    violations.append((md_file.name, line[:200]))
        assert not violations, (
            f'旧形の束縛 PJ="$(pwd)"（:- 無し）が残っている（env 上書き不可・#400 round5）: {violations!r}'
        )

    def test_at_least_one_bash_block_uses_pj(self):
        """検査自体が空振りでないことの自己チェック（clean pass の偽陰性防止）。"""
        found = False
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang in ("bash", "") and _block_uses_var(block, "$PJ"):
                    found = True
        assert found, "どの bash ブロックも $PJ を使っていない（検査対象が消失している疑い）"


class TestBashBlocksBindSlugBeforeUse:
    """round5 盲点2: $SLUG も $PJ と同型の空文字リスクを持つため同じ検査を適用する。"""

    def test_all_bash_blocks_using_slug_have_binding(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                if _block_uses_var(block, "$SLUG") and not _block_has_binding(block, _SLUG_BINDING_RE):
                    violations.append((md_file.name, block[:200]))
        assert not violations, (
            "$SLUG を使う bash ブロックに束縛行（SLUG=...）が無い（bash は呼び出しごとに独立"
            f"プロセスのため $SLUG が空文字になる・#400 round5 盲点2）: {violations!r}"
        )

    def test_slug_binding_precedes_first_usage(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                if not _block_uses_var(block, "$SLUG"):
                    continue
                binding_idx, usage_idx = _binding_and_first_usage_idx(block, _SLUG_BINDING_RE, "$SLUG")
                if usage_idx is not None and (binding_idx is None or usage_idx < binding_idx):
                    violations.append((md_file.name, block[:200]))
        assert not violations, (
            f"$SLUG の束縛行が最初の使用より後ろにある（#400 round5 盲点4）: {violations!r}"
        )

    def test_at_least_one_bash_block_uses_slug(self):
        found = False
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang in ("bash", "") and _block_uses_var(block, "$SLUG"):
                    found = True
        assert found, "どの bash ブロックも $SLUG を使っていない（検査対象が消失している疑い）"


class TestSlugDerivationUsesCanonicalForm:
    """round5c: slug 束縛の検査を「束縛が存在すること」（否定的検査）から「正しい導出形で
    あること」（肯定的要求）へ強化する。

    round5 までの `_SLUG_BINDING_RE`（`^SLUG="` があれば合格）は以下をすべて誤って
    合格させていた:
      - `SLUG="unknown"`（ハードコード）
      - `SLUG="$(basename "$PJ")"`（ADR-031 の resolve_slug と異なる別導出）
      - `PJ="$PJ"` の env assignment が欠落した resolve_slug 呼び出し

    `$SLUG` を使う全 bash ブロックの SLUG 束縛行に対し、
      1. `PJ="$PJ"`（env assignment）が同じ行に付いていること
      2. `resolve_slug(cwd=os.environ['PJ'])`（または `"PJ"` 変種）の形で呼んでいること
    の両方を positive に要求する。round5 の M1〜M3 と round5b が単一の契約で守られる。
    """

    _ENV_ASSIGNMENT_RE = re.compile(r'PJ="\$PJ"')
    _CANONICAL_RESOLVE_SLUG_RE = re.compile(r"""resolve_slug\(cwd=os\.environ\[('PJ'|"PJ")\]\)""")

    # SLUG="$SLUG" ... は「導出済みの値をそのまま次の python -c へ env 経由で渡す」
    # passthrough 行であり、新規導出（resolve_slug 呼び出し）ではないため対象外にする
    # （report-narration.md の save-from-response 相当ブロック）。
    _SLUG_SELF_PASSTHROUGH_RE = re.compile(r'^SLUG="\$SLUG"')

    def _slug_binding_lines(self, block_text: str):
        return [
            line for line in block_text.splitlines()
            if _SLUG_BINDING_RE.match(line.strip())
            and not self._SLUG_SELF_PASSTHROUGH_RE.match(line.strip())
        ]

    def test_slug_binding_lines_have_env_assignment_and_canonical_resolve_slug(self):
        violations = []
        checked_any = False
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                for line in self._slug_binding_lines(block):
                    checked_any = True
                    has_env_assignment = bool(self._ENV_ASSIGNMENT_RE.search(line))
                    snippets = _embedded_python_snippets(line)
                    has_canonical_call = any(
                        self._CANONICAL_RESOLVE_SLUG_RE.search(s) for s in snippets
                    )
                    if not (has_env_assignment and has_canonical_call):
                        violations.append((
                            md_file.name,
                            {
                                "line": line[:200],
                                "has_env_assignment": has_env_assignment,
                                "has_canonical_resolve_slug_call": has_canonical_call,
                            },
                        ))
        assert checked_any, "SLUG 束縛行が1件も見つからない（検査対象消失の疑い）"
        assert not violations, (
            "SLUG 束縛行が PJ=\"$PJ\"（env assignment）と resolve_slug(cwd=os.environ['PJ']) の"
            f"両方を満たしていない（#400 round5c）: {violations!r}"
        )


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


class TestNoShowToplevelSlugDerivation:
    """round5 盲点5: git rev-parse --show-toplevel による slug 再導出を検出する
    （ADR-031 は --git-common-dir 親を正とする。--show-toplevel は worktree で本体と食い違う）。
    """

    def test_no_show_toplevel_in_code(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for lang, block in _fenced_blocks(text):
                if lang not in ("bash", ""):
                    continue
                for line in block.splitlines():
                    code_part = line.split("#", 1)[0]
                    if _SHOW_TOPLEVEL_RE.search(code_part):
                        violations.append((md_file.name, line[:200]))
        assert not violations, (
            f"git rev-parse --show-toplevel による slug 再導出が残っている"
            f"（ADR-031 の --git-common-dir と異なり worktree で本体 PJ と食い違う・"
            f"#400 round5 盲点5）: {violations!r}"
        )


class TestPythonDashCHasNoDirectShellVarInterpolation:
    """round5b: python3 -c 本文にシェル変数（$PJ / $SLUG）を直接埋め込まないこと。

    PJ の絶対パスに `'` が含まれると python 文字列リテラルが壊れる（report-narration.md
    が自ら述べていた既存規約: 「slug は env 経由で渡す＝python -c へ直接埋め込むと repo 名に
    `'` を含む場合に壊れる」）。round5 で SLUG 導出を修正した際、この規約に反して
    `resolve_slug(cwd='$PJ')` のように $PJ を python -c へ直接埋め込んでしまっていた。
    正しい形は `VAR="$VAR" python3 -c "... os.environ['VAR'] ..."` の env 経由。

    `${CLAUDE_PLUGIN_ROOT}` はプラグインインストールパス（ユーザー入力ではない）で、
    このリポジトリ全体で python -c への直接埋め込みが確立された慣習のため対象外。
    """

    _SHELL_VAR_IN_PYTHON_RE = re.compile(r"\$PJ\b|\$SLUG\b")

    def test_no_direct_shell_var_interpolation_in_python_dash_c(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            for snippet in _embedded_python_snippets(text):
                if self._SHELL_VAR_IN_PYTHON_RE.search(snippet):
                    violations.append((md_file.name, snippet[:200]))
        assert not violations, (
            "python3 -c 本文に $PJ / $SLUG が直接埋め込まれている（'含む値で文字列リテラルが"
            f"壊れる。env 経由（os.environ[...]）に統一する・#400 round5b）: {violations!r}"
        )

    def test_env_passthrough_form_is_accepted(self):
        """検査自体が「env 経由の正しい形」まで拒否する過剰検知でないことの自己チェック。"""
        text = SKILL_MD.read_text(encoding="utf-8")
        snippets = [s for s in _embedded_python_snippets(text) if "resolve_slug" in s]
        assert snippets, "SKILL.md に resolve_slug を呼ぶ python3 -c スニペットが見つからない"
        for snippet in snippets:
            assert "os.environ['PJ']" in snippet or 'os.environ["PJ"]' in snippet, (
                f"env 経由の正しい形（os.environ['PJ']）になっていない: {snippet!r}"
            )
            assert not self._SHELL_VAR_IN_PYTHON_RE.search(snippet), (
                f"env 経由のはずが $PJ/$SLUG の直接埋め込みが残っている: {snippet!r}"
            )


class TestReferencesHaveNoUnattributedWritePaths:
    """python スニペット（fenced ```python ブロック **および** bash 内 python3 -c 埋め込みの
    両方・round5 盲点1）に無引数 resolve_slug() / 素の Path.cwd() / 空文字 fallback の
    CLAUDE_PROJECT_DIR 読み取りが残っていないこと（round4 Must 3, round5 盲点1）。
    """

    _BARE_RESOLVE_SLUG_RE = re.compile(r"(?<!def )resolve_slug\(\s*\)")
    _BARE_PATH_CWD_RE = re.compile(r"Path\.cwd\(\)")
    _EMPTY_FALLBACK_PROJECT_DIR_RE = re.compile(
        r'os\.environ\.get\(\s*["\']CLAUDE_PROJECT_DIR["\']\s*,\s*["\']["\']\s*\)'
    )

    def _code_lines(self, md_file: Path):
        """python fenced block の非コメント行 + bash 内 python3 -c 埋め込みの行を返す。"""
        text = md_file.read_text(encoding="utf-8")
        out = []
        for lang, block in _fenced_blocks(text):
            if lang == "python":
                for line in block.splitlines():
                    code_part = line.split("#", 1)[0]
                    out.append((line, code_part))
        # round5 盲点1: fenced ```python 以外に、bash ブロックの中に埋め込まれた
        # python3 -c "..." / python3 -c '...' も同じ検査対象にする。
        for snippet in _embedded_python_snippets(text):
            for line in snippet.splitlines():
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
            f"無引数 resolve_slug() が references/SKILL.md の python スニペット"
            f"（fenced ```python または python3 -c 埋め込み）に残っている"
            f"（書込経路が cwd 側 PJ に誤帰属する・#400 round4/round5）: {violations!r}"
        )

    def test_no_bare_path_cwd_calls(self):
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            for raw, code in self._code_lines(md_file):
                if self._BARE_PATH_CWD_RE.search(code):
                    violations.append((md_file.name, raw[:200]))
        assert not violations, (
            f"素の Path.cwd() が references/SKILL.md の python スニペットに残っている"
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

    def test_embedded_python_snippets_are_actually_detected(self):
        """検査自体が空振りでないことの自己チェック（round5 盲点1 の検出経路が生きていること）。"""
        found = False
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            if _embedded_python_snippets(text):
                found = True
        assert found, "python3 -c 埋め込みスニペットが1件も検出されていない（抽出ロジック破損の疑い）"


class TestGenerateProposalsUseResultProjectDir:
    """generate_proposals()/generate_auto_fix_summaries() の呼び出し・言及が全ファイルで
    Path.cwd() でなく解析対象 PJ の確定値（result["project_dir"]）を渡す形になっていることを
    検査する契約テスト（round3 Must 2 + round5 M4: remediation.md だけでなく
    proposal-protocol.md の言及も対象に含める）。
    """

    _CALL_RE = re.compile(r"generate_(?:proposals|auto_fix_summaries)\([^)]*\)")

    def test_all_generate_calls_pass_result_project_dir(self):
        checked_any = False
        violations = []
        for md_file in ALL_TARGET_MD_FILES:
            text = md_file.read_text(encoding="utf-8")
            calls = self._CALL_RE.findall(text)
            for call in calls:
                checked_any = True
                if 'result["project_dir"]' not in call:
                    violations.append((md_file.name, call))
                if "Path.cwd()" in call:
                    violations.append((md_file.name, call))
        assert checked_any, "generate_proposals/generate_auto_fix_summaries の呼び出しが1件も見つからない"
        assert not violations, (
            f"generate_proposals/generate_auto_fix_summaries の呼び出しが"
            f"project_root=Path(result[\"project_dir\"]) の形になっていない"
            f"（#400 round3 Must2 / round5 M4）: {violations!r}"
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
