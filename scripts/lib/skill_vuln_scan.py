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

# 走査から除外するディレクトリ名（testpaths_coverage の _EXCLUDE_DIRS と同等の慣習）。
_EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".claude",
    "tests",
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
    """skills_dir 配下の対象拡張子ファイルを除外ディレクトリを除いて列挙する。"""
    out: List[Path] = []
    for p in skills_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in _SCAN_EXTENSIONS:
            continue
        if any(part in _EXCLUDE_DIRS for part in p.parts):
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
# ============================================================================

# fetch 系ネットワーク取得コマンド（gh api を含む）。
_FLOW_FETCH_CMD = re.compile(r"(?i)(\b(?:curl|wget|fetch)\b|\bgh\s+api\b)")

# コマンド置換の存在（$( ... ) or `...`）。
_FLOW_CMD_SUBST = re.compile(r"\$\(|`")

# VAR=... 代入（先頭の変数名を捕捉。export 許容）。
_FLOW_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

# fetch のダウンロード先ファイルを捕捉（-o/-O/--output/>/>>）。
_FLOW_FETCH_TO_FILE = re.compile(
    r"(?i)\b(?:curl|wget|fetch)\b[^\n]*?"
    r"(?:-o|-O|--output|>>?)\s*['\"]?([^\s'\"|;&><`]+)"
)

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
        fm = _FLOW_FETCH_TO_FILE.search(text)
        if fm:
            fpath = fm.group(1)
            if fpath and fpath not in _FLOW_FILE_IGNORE:
                fetch_files.setdefault(fpath, (lineno, _snippet(text)))

    return found


def _iter_scopes(path: Path, text: str) -> List[List[Tuple[int, str]]]:
    """フロー解析のスコープを列挙する。

    .sh/.bash はファイル全体を 1 スコープ。SKILL.md 等 .md は fenced code block
    （``` フェンス）ごとに独立スコープ（prose の跨ぎを排除）。行番号は原文基準で保持。
    """
    lines = text.splitlines()
    if path.suffix in {".sh", ".bash"}:
        return [list(enumerate(lines, start=1))]

    scopes: List[List[Tuple[int, str]]] = []
    cur: List[Tuple[int, str]] = []
    in_block = False
    for idx, line in enumerate(lines, start=1):
        if re.match(r"^\s*```", line):
            if in_block:
                if cur:
                    scopes.append(cur)
                cur = []
                in_block = False
            else:
                in_block = True
            continue
        if in_block:
            cur.append((idx, line))
    if in_block and cur:  # 未閉じフェンスも 1 スコープとして扱う
        scopes.append(cur)
    return scopes


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
