"""skill_vuln_scan.py — 取り込みスキルの静的脆弱性スキャン（SkillSpector 型・#13）。

LLM 非依存・決定論・読み取りのみ（ファイル/store 書込なし）。背景:

外部由来のスキル（SKILL.md プロンプト + 同梱シェルスクリプト）を取り込むと、悪意ある
パターン（リモート取得 → shell 実行 / 秘密ファイルのネットワーク exfil / 破壊的コマンド /
SKILL.md に埋め込まれた prompt injection / frontmatter の全ツール付与）を見落とす穴がある。
本モジュールは `root/skills/` 配下を行単位で静的スキャンし、危険パターンを Finding として
列挙する。SkillSpector（取り込みスキルの脆弱性静的検査）の発想に倣う。

FP 較正の方針（このリポジトリの鉄則 = 偽陽性に極めて厳格）:
- **combo 必須・bare 単体は検出しない**。例: `curl https://...`（単独）は正当な取得なので非検出、
  `curl http://... | sh` のように shell へ流す combo のみ remote_exec とする。
- `gh api repos/x/contents/... -q .content | base64 -d`（GitHub content デコード）は実在の
  正当 FP。`base64 -d` 単体は検出せず、`base64 -d ... | sh` の combo のみ検出する。
- `rm -rf ./build` のような相対パス削除は非検出。`/`・`~`・`$HOME`・`*` を消す場合のみ destructive。
- secret_exfil は「秘密ソース」と「ネット sink」が**同一行に共起**したときのみ。片方だけは非検出。

対象拡張子は `.md` / `.sh` / `.bash` のみ（`.py` は FP 抑制のため本 PR 対象外。follow-up）。
配線先は audit observability の "Skill Vulnerability" section（`audit/sections_skill_vuln.py`）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Pattern, Tuple

# 走査対象拡張子（.py は本 PR 対象外＝FP 抑制。follow-up で別途）。
_SCAN_EXTENSIONS = {".md", ".sh", ".bash"}

# 走査から除外するディレクトリ名（skills_dir 相対で判定。#415 是正: 旧実装は
# root からの絶対パス全体で判定しており ~/.claude/ 配下を渡すと ".claude" が
# 常に一致して全件除外されていた＝skills 実在パスを1件も走査していなかった）。
# 判定は _iter_target_files 側で `p.relative_to(skills_dir).parts` に対して行う。
#
# 各項目は「除外してよい根拠」を実測込みで書く。根拠を示せない項目は除外しない
# （~/.claude/rules/verify-checks-by-breaking.md: allowlist/除外リストは検査を
# 骨抜きにする。迷ったら除外せず検査対象に倒す）。旧リストにあった "tests" は
# 実コーパスで実際のスキル同梱テスト文書（fetch/exec を含みうる本物の skill
# 内容）を除外していたため根拠不十分と判断し外した
# （実測: ~/.claude/skills/turnstile-spin/tests/validation.md）。
_EXCLUDE_DIRS = {
    # git 内部のオブジェクト/参照ストア。構造上 .md/.sh/.bash 拡張子のファイルを
    # 含まない（skill 側が意図的に作者するコンテンツではなく git 自身の管理領域）。
    ".git",
    # vendored Python virtualenv。activate スクリプトは拡張子無し/.csh/.fish が
    # 標準で対象拡張子に一致しないため走査上は実質無害（node_modules と同種の
    # 「インストール成果物であり skill が直接著作したコンテンツでない」区分）。
    ".venv",
    "venv",
    # vendored npm 依存ツリー。実測 488 件はサードパーティ製パッケージの
    # CHANGELOG.md 等で skill 作者のコンテンツではない
    # （例: ~/.claude/skills/gstack/node_modules/pkce-challenge/CHANGELOG.md）。
    # ただし postinstall script 等の依存チェーン攻撃は本スキャナのスコープ外
    # （.py 同様 follow-up）であり silent gap として残る。
    "node_modules",
    # Python バイトコードキャッシュ。.pyc のみを含み対象拡張子に一致しない
    # （構造上マッチしえない＝実害なし）。
    "__pycache__",
    # pytest の自動生成キャッシュ。実測 2 件は pytest 自身が生成する定型
    # README.md（攻撃者が編集できる skill 内容ではない）。
    ".pytest_cache",
    # mypy の自動生成キャッシュ。.pytest_cache と同種の自動生成物
    # （実コーパスに該当ファイル無し。構造的類推による判断）。
    ".mypy_cache",
    # ネストした Claude Code 実行時/開発状態ディレクトリ（worktree・plugin
    # キャッシュの入れ子等）。実測 1690 件は本プラグイン自身のキャッシュ内に
    # 混入した stray worktree アーティファクトで、skill として配布される
    # コンテンツではなかった（例: ~/.claude/plugins/cache/evolve-anything/
    # evolve-anything/1.125.0/.claude/worktrees/version-up/ 配下の丸ごとの
    # 開発ツリー2重）。root からの絶対パスでなく skills_dir 相対で判定する
    # ため、root 自身が ~/.claude 配下でも誤って全件除外はされない。
    ".claude",
}

# snippet の最大長（マッチ行を strip して truncate）。
_SNIPPET_MAX = 120

# --- pattern catalog（FP 較正済み・combo 必須） --------------------------------
# 各エントリ: (pattern_id, category, severity, compiled regex)
# 同一行に対し regex.search でマッチ判定する（行単位スキャン）。

_PATTERNS: List[Tuple[str, str, str, Pattern[str]]] = [
    # remote_exec / HIGH — リモート取得を shell にパイプ／ダウンロードして即実行。
    (
        "remote_exec.curl_pipe_sh",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\b(curl|wget|fetch)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|k|d|a)?sh\b"),
    ),
    (
        # base64 -d 単体は非検出。shell へパイプする combo のみ。
        "remote_exec.base64_pipe_sh",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\bbase64\s+(--decode|-d|-D)\b[^\n|]*\|\s*(ba)?sh\b"),
    ),
    (
        "remote_exec.download_and_run",
        "remote_exec",
        "HIGH",
        re.compile(r"(?i)\b(curl|wget)\b[^\n]*\b-o\b[^\n]*&&[^\n]*\b(ba)?sh\b"),
    ),
    # destructive / MEDIUM
    (
        "destructive.rm_rf_root",
        "destructive",
        "MEDIUM",
        re.compile(r"(?i)\brm\s+-[rf]*\s+(/|~|\$HOME|\*)(\s|$)"),
    ),
    (
        "destructive.chmod_777",
        "destructive",
        "MEDIUM",
        re.compile(r"chmod\s+-?R?\s*777\b"),
    ),
    (
        "destructive.fork_bomb",
        "destructive",
        "MEDIUM",
        re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:"),
    ),
    # prompt_injection / MEDIUM — SKILL.md 等に埋め込まれた注入ペイロード。
    (
        "prompt_injection.ignore_previous",
        "prompt_injection",
        "MEDIUM",
        re.compile(
            r"(?i)ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above)\s+"
            r"(instructions?|prompts?|rules?)"
        ),
    ),
    (
        "prompt_injection.disregard",
        "prompt_injection",
        "MEDIUM",
        re.compile(r"(?i)disregard\s+(the\s+|all\s+|previous\s+|above\s+)"),
    ),
    (
        "prompt_injection.do_not_tell_user",
        "prompt_injection",
        "MEDIUM",
        re.compile(r"(?i)do\s+not\s+(tell|inform|reveal\s+to|notify)\s+the\s+user"),
    ),
    (
        "prompt_injection.ja_ignore",
        "prompt_injection",
        "MEDIUM",
        re.compile(
            r"これまでの指示を無視|以前の指示を無視|システムプロンプトを無視|"
            r"ユーザーには(言わない|伝えない|秘密)"
        ),
    ),
    # overbroad_tools / LOW — frontmatter の全ツール付与。
    (
        "overbroad_tools.wildcard",
        "overbroad_tools",
        "LOW",
        re.compile(r"(?im)^\s*(tools|allowed-tools)\s*:\s*[\"']?\*"),
    ),
]

# secret_exfil は「秘密ソース」+「ネット sink」の同一行共起でのみ検出する（特殊判定）。
_SECRET_SOURCE = re.compile(
    r"(?i)(~/\.ssh/id_|\.aws/credentials|id_rsa|\.env\b|printenv\b|\benv\b\s*\|)"
)
_NET_SINK = re.compile(r"(?i)(\bcurl\b|\bwget\b|\bnc\b|https?://)")


@dataclass(frozen=True)
class Finding:
    """1 件の脆弱性ヒット。

    rel_path:   root からの POSIX 相対パス
    line:       1 始まりの行番号
    category:   remote_exec / secret_exfil / destructive / prompt_injection / overbroad_tools
    severity:   HIGH / MEDIUM / LOW
    pattern_id: マッチした pattern の識別子
    snippet:    マッチ行を strip し最大 120 字に truncate したもの
    """

    rel_path: str
    line: int
    category: str
    severity: str
    pattern_id: str
    snippet: str


@dataclass(frozen=True)
class FlowFinding:
    """静的フロー解析（マルチステップ攻撃系列・#123）が検出した 1 件の順序ペア。

    行単位の Finding と違い、各行単体では benign だが「fetch→exec」「read→exfil」の
    順序で組み合わさると悪性になる系列を表す。producer（fetch/read 行）→ consumer
    （exec/送信行）を 2 つの行番号で示す。

    rel_path:          root からの POSIX 相対パス
    producer_line:     fetch/read 行（1 始まり）
    consumer_line:     exec/送信行（1 始まり・producer より後）
    category:          remote_exec_flow / secret_exfil_flow
    severity:          HIGH（系列注入は高リスク）
    pattern_id:        マッチした系列 pattern の識別子
    var:               producer と consumer を繋ぐキー（変数名 or ダウンロード先ファイル）
    producer_snippet:  producer 行の strip 済み snippet
    consumer_snippet:  consumer 行の strip 済み snippet
    """

    rel_path: str
    producer_line: int
    consumer_line: int
    category: str
    severity: str
    pattern_id: str
    var: str
    producer_snippet: str
    consumer_snippet: str


@dataclass
class SkillVulnReport:
    """スキャン結果。

    applicable:    root/skills/ が存在したか（無ければ False＝非該当・沈黙）
    scanned_files: 走査した対象拡張子ファイル数
    findings:      検出した Finding（(rel_path, line, pattern_id) で安定ソート済み）
    flow_findings: 検出した FlowFinding（マルチステップ系列・#123。行単位 findings とは別枠）
    """

    applicable: bool = False
    scanned_files: int = 0
    findings: List[Finding] = field(default_factory=list)
    flow_findings: List[FlowFinding] = field(default_factory=list)


def _snippet(line_text: str) -> str:
    s = line_text.strip()
    if len(s) > _SNIPPET_MAX:
        return s[:_SNIPPET_MAX]
    return s


def _iter_target_files(skills_dir: Path) -> List[Path]:
    """skills_dir 配下の対象拡張子ファイルを除外ディレクトリを除いて列挙する。

    除外判定は skills_dir **相対**の parts に対して行う（#415 是正: 絶対パス全体を
    見ると skills_dir 自身の祖先パスに含まれる名前 — 例えば ~/.claude/skills を渡した
    ときの ".claude" — が常に一致し全件除外されるバグがあった）。
    """
    out: List[Path] = []
    for p in skills_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _SCAN_EXTENSIONS:
            continue
        rel_parts = p.relative_to(skills_dir).parts
        if any(part in _EXCLUDE_DIRS for part in rel_parts):
            continue
        out.append(p)
    return out


def _scan_line(rel_path: str, lineno: int, text: str) -> List[Finding]:
    found: List[Finding] = []
    for pattern_id, category, severity, regex in _PATTERNS:
        if regex.search(text):
            found.append(
                Finding(
                    rel_path=rel_path,
                    line=lineno,
                    category=category,
                    severity=severity,
                    pattern_id=pattern_id,
                    snippet=_snippet(text),
                )
            )
    # secret_exfil: 秘密ソース + ネット sink の同一行共起。
    if _SECRET_SOURCE.search(text) and _NET_SINK.search(text):
        found.append(
            Finding(
                rel_path=rel_path,
                line=lineno,
                category="secret_exfil",
                severity="HIGH",
                pattern_id="secret_exfil.source_and_sink",
                snippet=_snippet(text),
            )
        )
    return found


# ============================================================================
# 静的フロー解析（マルチステップ攻撃系列の順序ペア検出・#123）— 追加のみ
# ----------------------------------------------------------------------------
# 行単位スキャン（_scan_line）はステートレスで「行 A（fetch）→ 行 B（exec）」の
# 系列を追えない。ここでは同一スコープ（.sh/.bash は 1 ファイル全体、SKILL.md は
# 同一 fenced code block）内で、fetch 系がバインドした名前（変数 or ダウンロード先
# ファイル）が後続行の exec/送信ポジションで参照される順序ペアを決定論検出する。
# 完全なデータフロー解析はせず、同名の代入→参照・コマンド置換・-o/> のファイル
# 受け渡しのみ最小限に追う（combo 必須方針の系列版）。
#
# FP 抑制の要:
# - producer は「fetch/read をコマンド置換で変数に束ねる」or「-o/> でファイルに
#   落とす」のみ登録する（bare な取得は非登録）。
# - consumer は変数を **コードとして** 実行する形（eval / -c / <<< / `| sh`）だけ拾い、
#   引数渡し（`bash local.sh "$V"`）は除外する。ダウンロードファイルは interpreter
#   直後（`bash FILE` / `./FILE` / `source FILE` / `chmod +x FILE`）のみ。
# - producer は consumer より前の行に限る（同一行 self-loop は登録を後回しにして排除）。
# - スコープはフェンス内外を問わず全行（#415 型再発の是正: 4スペース字下げ / `~~~` /
#   `<details>` / フェンス無し本文が非検出だった）。行番号距離の上限は設けない
#   （#415 追補: 距離キャップは「上限を超える行数だけ離せば検出を回避できる」新たな
#   迂回経路そのものであり、allowlist/除外リストと同じ骨抜き構造になる。導入時に
#   検出された FP の真因は _FLOW_FETCH_TO_FILE 側の `>` 誤認識バグであり、そちらを
#   修正した結果、距離キャップ無しでも 67 roots 実コーパスで新規誤検出は0件だった）。
# ============================================================================

# fetch 系ネットワーク取得コマンド（gh api を含む）。
_FLOW_FETCH_CMD = re.compile(r"(?i)(\b(?:curl|wget|fetch)\b|\bgh\s+api\b)")

# コマンド置換の存在（$( ... ) or `...`）。
_FLOW_CMD_SUBST = re.compile(r"\$\(|`")

# VAR=... 代入（先頭の変数名を捕捉。export 許容）。
_FLOW_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

# fetch のダウンロード先ファイルを捕捉（-o/-O/--output/>/>>）。
# `<account_id>` のような山括弧プレースホルダ全体を先に同じ長さの `#` へマスクしてから
# マッチさせる（#415 追補2: 直前空白必須の lookbehind で誤認識を抑えた版は、
# `curl url>file` や stderr redirect `curl url 2>file` のような、bash として正当かつ
# 直前が空白でない redirect 記法を検出できなくする回帰を生んでいた実測がある — 変異
# 試験で両方とも flow_findings=[] のまま素通りすることを確認済み。プレースホルダの
# 識別は「直前が空白でないこと」ではなく「`<...>` に囲まれた語であること」で行う方が
# 誤認識の真因に近く、redirect 記法の空白有無に依存しない）。
_PLACEHOLDER_TOKEN = re.compile(r"<[A-Za-z0-9_.-]+>")

_FLOW_FETCH_TO_FILE = re.compile(
    r"(?i)\b(?:curl|wget|fetch)\b[^\n]*?"
    r"(?:-o|-O|--output|>>?)\s*['\"]?([^\s'\"|;&><`]+)"
)


def _mask_placeholder_tokens(text: str) -> str:
    """`<account_id>` 等の山括弧プレースホルダを同じ長さの `#` でマスクする。

    `_FLOW_FETCH_TO_FILE` がプレースホルダ内の `>` を redirect と誤認しないための
    前処理。キャプチャ位置・行文字列の長さを変えず、マスク後の文字列でのみ検索する
    （元の `text` はスニペット表示や他 regex に引き続き使う）。
    """
    return _PLACEHOLDER_TOKEN.sub(lambda m: "#" * len(m.group(0)), text)

# ダウンロード先として登録しない sink（/dev 系・stdout 記法）。
_FLOW_FILE_IGNORE = {"-", "/dev/null", "/dev/stdout", "/dev/stderr"}

# 変数を「コードとして」実行する形（引数渡しは除外＝FP 抑制）。{ref} に変数参照を埋める。
_EXEC_VAR_FORM_TEMPLATES = [
    r"(?i)\beval\b[^\n]*{ref}",  # eval "$V"
    # bash -c "$V" / sh -c / python -c / node -e / perl -e / ruby -e
    r"(?i)\b(?:(?:ba|z|k|d|a)?sh|python3?|node|perl|ruby)\b[^\n]*?\s-(?:c|e)\b[^\n]*{ref}",
    # bash <<< "$V" / python3 <<< "$V"
    r"(?i)\b(?:(?:ba|z|k|d|a)?sh|python3?)\b[^\n]*?<<<[^\n]*{ref}",
    # echo "$V" | sh   （変数参照が pipe より前、shell が後）
    r"(?i){ref}[^\n]*\|\s*(?:sudo\s+)?(?:ba|z|k|d|a)?sh\b",
]


def _var_ref_pattern(var: str) -> str:
    """$VAR / ${VAR} の参照を表す regex 断片（後続が識別子文字でないこと）。"""
    return r"\$\{?" + re.escape(var) + r"(?![A-Za-z0-9_])"


def _exec_var_regexes(var: str) -> List[Pattern[str]]:
    """変数 var を「コードとして」実行する consumer 行を判定する regex 群。"""
    ref = _var_ref_pattern(var)
    return [re.compile(t.format(ref=ref)) for t in _EXEC_VAR_FORM_TEMPLATES]


def _exec_file_regexes(fpath: str) -> List[Pattern[str]]:
    """ダウンロード済みファイル fpath を実行する consumer 行を判定する regex 群。"""
    ref = re.escape(fpath)
    base = re.escape(fpath.rsplit("/", 1)[-1])
    return [
        # bash FILE / sh FILE / source FILE / python FILE（flag を挟んでも可、直後の位置）
        re.compile(
            r"(?i)\b(?:(?:ba|z|k|d|a)?sh|source|python3?|node|perl|ruby)\s+"
            r"(?:-\S+\s+)*['\"]?" + ref
        ),
        re.compile(r"(?i)(?:^|;|&&|\|\|)\s*\.\s+['\"]?" + ref),  # . FILE（source 短縮）
        # ./FILE（basename 実行）— コマンド境界（^ / ; / & / | / ( / `）直後のみ。
        # 引数位置（`rm -rf ./x.deb` / `hdiutil attach ./x.dmg`）は非検出＝FP 抑制。
        re.compile(r"(?i)(?:^|[;&|(`])\s*\./" + base + r"\b"),
        re.compile(r"(?i)\bchmod\s+\+x\b[^\n]*" + ref),  # chmod +x FILE（実行準備）
    ]


def _detect_flows_in_scope(
    rel_path: str, scope_lines: List[Tuple[int, str]]
) -> List[FlowFinding]:
    """1 スコープ内の fetch→exec / read→exfil 順序ペアを検出する（決定論）。"""
    found: List[FlowFinding] = []
    fetch_vars: dict[str, Tuple[int, str]] = {}
    fetch_files: dict[str, Tuple[int, str]] = {}
    secret_vars: dict[str, Tuple[int, str]] = {}

    for lineno, text in scope_lines:
        # 1) consumer 判定は既登録 producer に対してのみ（＝producer 先行を強制）。
        for var, (pl, psnip) in fetch_vars.items():
            if any(rx.search(text) for rx in _exec_var_regexes(var)):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "remote_exec_flow", "HIGH",
                        "remote_exec_flow.fetch_var_to_exec", var, psnip, _snippet(text),
                    )
                )
        for fpath, (pl, psnip) in fetch_files.items():
            if any(rx.search(text) for rx in _exec_file_regexes(fpath)):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "remote_exec_flow", "HIGH",
                        "remote_exec_flow.fetch_file_to_exec", fpath, psnip, _snippet(text),
                    )
                )
        for var, (pl, psnip) in secret_vars.items():
            if re.search(_var_ref_pattern(var), text) and _NET_SINK.search(text):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "secret_exfil_flow", "HIGH",
                        "secret_exfil_flow.read_var_to_net", var, psnip, _snippet(text),
                    )
                )

        # 2) producer 登録は consumer 判定の後（同一行 self-loop を防ぐ）。
        m = _FLOW_ASSIGN.match(text)
        if m:
            var, rhs = m.group(1), m.group(2)
            if _FLOW_CMD_SUBST.search(rhs):
                if _FLOW_FETCH_CMD.search(rhs):
                    fetch_vars.setdefault(var, (lineno, _snippet(text)))
                if _SECRET_SOURCE.search(rhs):
                    secret_vars.setdefault(var, (lineno, _snippet(text)))
        fm = _FLOW_FETCH_TO_FILE.search(_mask_placeholder_tokens(text))
        if fm:
            fpath = fm.group(1)
            if fpath and fpath not in _FLOW_FILE_IGNORE:
                fetch_files.setdefault(fpath, (lineno, _snippet(text)))

    return found


def _iter_scopes(path: Path, text: str) -> List[List[Tuple[int, str]]]:
    """フロー解析のスコープを列挙する。

    拡張子・フェンス記法（``` / ~~~ / 4スペース字下げ / `<details>` / フェンス無し
    本文）を問わずファイル全体を 1 スコープとする（#415: フェンス限定 scope は
    4スペース字下げ・`~~~`・`<details>`・素の本文の combo を素通りさせていた）。
    行番号距離の上限は設けない（#415 追補: 距離キャップ自体が「超えれば回避できる」
    迂回経路になるため撤廃。producer/consumer 誤連鎖の真因は _FLOW_FETCH_TO_FILE
    側の regex を修正して解消した）。行番号は原文基準で保持。
    """
    lines = text.splitlines()
    return [list(enumerate(lines, start=1))]


def scan_skills(root: Path) -> SkillVulnReport:
    """root/skills/ 配下の取り込みスキルを静的スキャンして脆弱性 Finding を返す（決定論）。"""
    root = Path(root)
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return SkillVulnReport(applicable=False, scanned_files=0, findings=[])

    findings: List[Finding] = []
    flow_findings: List[FlowFinding] = []
    scanned = 0
    for path in _iter_target_files(skills_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        rel = path.relative_to(root).as_posix()
        for idx, line in enumerate(text.splitlines(), start=1):
            findings.extend(_scan_line(rel, idx, line))
        for scope in _iter_scopes(path, text):
            flow_findings.extend(_detect_flows_in_scope(rel, scope))

    findings.sort(key=lambda f: (f.rel_path, f.line, f.pattern_id))
    flow_findings.sort(
        key=lambda f: (f.rel_path, f.producer_line, f.consumer_line, f.pattern_id)
    )
    return SkillVulnReport(
        applicable=True,
        scanned_files=scanned,
        findings=findings,
        flow_findings=flow_findings,
    )
