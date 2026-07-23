"""自己汚染ハルシネーション指紋の検出コア（純関数・ゼロ LLM・read-only）。

背景: opus 1M の長大セッションで、assistant が **tool_result 原文に存在しない偽指示を自己生成**
し、それを「外部からの汚染／注入」と誤認してラッチする現象が実測で増加した（operational
自己汚染 23→49・生タグ漏出 10→43／直近週）。この現象は 3 系統の指紋を持つ:

- **Family A（生タグ漏出）**: assistant の text ブロックに生の invoke / function_calls /
  antml タグが漏出する。FP 対策として (i) code fence(```)内は除外 (ii) タグの **構造完全性**
  （invoke の開き ＋ 対応する parameter か閉じ）を要求する。この PJ のルール文（tool-call-hygiene.md
  等）はタグ名を散文で含むので、単純な部分一致では誤検出する。構造完全性で散文言及を落とす。
- **Family B（偽 system-reminder）**: 本来 harness が user ターンへ注入するはずの
  ``<system-reminder>`` ブロックを assistant が自分の text 側に出力する。
- **Family C-lite（汚染宣言 × 原文非在）**: assistant が汚染語彙（injection/corrupted/汚染/
  ignore previous 等）を含み **かつ** 鉤括弧/quote 内の引用リテラル（>=12 文字）を持ち **かつ**
  その文字列が直前 K 個の tool_result 原文に **空白正規化 byte 照合で非在**。3 条件 AND のみ発火＝
  高精度・低再現（paraphrase 型の言い換え汚染宣言は取りこぼす＝仕様）。
- **ドメイン語彙 FP 除外（#203）**: Family C の汚染語彙は PJ 非依存の汎用リストだが、特定ドメイン
  PJ（例: Whisper 文字起こし校正 PJ・slug に ``bots`` を含む）では「ハルシネーション」等が正常業務
  語彙として頻出し FP を量産する。PJ slug × 語彙のペア（``_DOMAIN_VOCAB_FP_MARKERS``）でその語彙
  **のみ** が根拠の Family C 候補を ``domain_vocab_fp`` という別バケットへ振り分ける（ハード除外
  でなく別集計・``is_topic_pj`` と同型。他の汚染語彙が近傍にあれば通常どおり Family C として発火
  する）。除外件数は audit で常時 surface する（silence≠evaluated）。

**tool_result 原文（role=user の tool_result / 先頭 toolUseResult）と assistant の text/thinking を
厳密分離**して照合する（混同すると全て FP になる）。これが検出の核心。

この module は決定論・純関数のみで LLM を呼ばない（no-llm-in-tests は自明に満たす）。audit の
Layer 2 advisory（``sections_self_contamination``）が実行時に既存 transcript を走査して使う。
hook / store は新設しない（read-only）。
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

# ------------------------------------------------------------------
# 定数（検出パターン）
# ------------------------------------------------------------------
# Family A: 生タグの構造要素。antml: プレフィックス有無どちらも許容。
_INVOKE_OPEN = re.compile(r"<\s*(?:antml:)?invoke\s+name\s*=", re.IGNORECASE)
_PARAM_OPEN = re.compile(r"<\s*(?:antml:)?parameter\s+name\s*=", re.IGNORECASE)
_INVOKE_CLOSE = re.compile(r"</\s*(?:antml:)?invoke\s*>", re.IGNORECASE)
_FUNCTION_CALLS_OPEN = re.compile(r"<\s*(?:antml:)?function_calls\s*>", re.IGNORECASE)

# Family B: assistant text 側に出た system-reminder 開きタグ。
_SYSTEM_REMINDER_OPEN = re.compile(r"<\s*system-reminder\s*>", re.IGNORECASE)

# Family C-lite: 汚染 **主張** 語彙（assistant が「今の文脈／tool 出力が汚染された・偽だ」と
# メタに主張する言い回し）に限定する。``injection`` / ``corrupted`` / bare ``混入`` 等の
# 汎用技術語は normal な code 議論（prompt_injection / dependency injection 等）に頻出し、
# 近接窓内でも FP を量産するため **意図的に除外** する（実 corpus 較正で確認）。
_CONTAM_VOCAB = (
    "自己汚染",
    "汚染されて",
    "汚染された",
    "汚染だ",
    "が汚染",
    "文脈が汚染",
    "コンテキストが汚染",
    "出力が汚染",
    "作話",
    "ハルシネーション",
    "偽の system",
    "偽のsystem",
    "偽物の system",
    # 「偽の指示（文）を注入された／自分が書いていない」= 実測 figma の真の自己汚染宣言の言い回し。
    "偽の指示",
    "偽指示",
    "私が書いていない",
    "書いていない指示",
    "出力汚染",
    "ツール出力汚染",
    "思考が混入",
    "思考文体",
    "が混入して",
    "が混入しま",
    "tool_result に混入",
    "tool 結果に混入",
    "注入された",
    "注入され",
    "ignore previous instructions",
    "ignore all previous",
    "disregard previous",
    "これまでの指示を無視",
    "以前の指示を無視",
    "上記の指示を無視",
)

# 引用リテラルを囲う対（開き, 閉じ）。
_QUOTE_PAIRS = (("「", "」"), ('"', '"'), ("“", "”"), ("'", "'"), ("`", "`"))

# 話題 PJ（この現象を扱う PJ＝Family C の FP 源）。operational と分離集計するため。
_TOPIC_PJ_MARKERS = ("evolve-anything", "rl-anything")

# ドメイン語彙 FP 除外（#203）: PJ slug マーカー → その PJ で正常業務語彙として頻出し Family C
# 誤検出源になる汚染語彙のマッピング。初期エントリ: Whisper 文字起こし校正 PJ（slug に bots を
# 含む）で「ハルシネーション」が業務語彙として頻出する FP。ハード除外でなく別バケット集計に使う。
_DOMAIN_VOCAB_FP_MARKERS: Dict[str, Tuple[str, ...]] = {
    "bots": ("ハルシネーション",),
}

_DEFAULT_K = 5
_DEFAULT_MIN_LITERAL = 12
_DEFAULT_MAX_FILES = 400
# 汚染語彙（主張）と引用リテラルの近接窓（文字）。これを超えると別文脈とみなし発火しない。
# 実 corpus 較正: 真の figma positive は語彙→引用が ~16 文字なので 50 で安全に取れ、
# 無関係な近傍引用（120 だと FP を量産）を切れる。
_DEFAULT_PROXIMITY = 50


# ------------------------------------------------------------------
# データモデル
# ------------------------------------------------------------------
@dataclass
class Hit:
    """1 件の指紋検出。confab_text=作話側、reference_text=対比する直前 tool_result 原文。"""

    family: str  # "A" | "B" | "C"
    line: int
    block: str  # "text" | "thinking"
    confab_text: str
    reference_text: str = ""
    session_id: str = ""


@dataclass
class ScanReport:
    """1 transcript（または複数の集約）の検出結果。

    ``domain_vocab_fp`` は #203 のドメイン語彙 FP 除外バケット。Family C 候補のうち根拠が
    PJ 固有のドメイン語彙のみだったものをここへ振り分ける（ハード除外でなく別集計）。
    ``total`` / operational 集計には含めない。
    """

    family_a: List[Hit] = field(default_factory=list)
    family_b: List[Hit] = field(default_factory=list)
    family_c: List[Hit] = field(default_factory=list)
    domain_vocab_fp: List[Hit] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.family_a) + len(self.family_b) + len(self.family_c)

    def counts(self) -> Dict[str, int]:
        return {"A": len(self.family_a), "B": len(self.family_b), "C": len(self.family_c)}

    def extend(self, other: "ScanReport") -> None:
        self.family_a.extend(other.family_a)
        self.family_b.extend(other.family_b)
        self.family_c.extend(other.family_c)
        self.domain_vocab_fp.extend(other.domain_vocab_fp)


@dataclass
class ProjectScanReport:
    """PJ 1 個の transcript 群を走査した集約（period-over-period 付き）。"""

    report: ScanReport
    recent_counts: Dict[str, int]
    baseline_counts: Dict[str, int]
    files_scanned: int
    is_topic: bool


# ------------------------------------------------------------------
# 低レベルヘルパ: content ブロック分解 / 分離
# ------------------------------------------------------------------
def iter_content_blocks(content: Any) -> Iterable[Tuple[str, str]]:
    """message.content（str / list）を (kind, text) で列挙する。"""
    if isinstance(content, str):
        yield ("str", content)
        return
    if not isinstance(content, list):
        return
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "text":
            yield ("text", b.get("text", "") or "")
        elif bt == "thinking":
            yield ("thinking", b.get("thinking", "") or "")
        elif bt == "tool_result":
            c = b.get("content", "")
            if isinstance(c, list):
                parts = [
                    (x.get("text", "") or "") if isinstance(x, dict) else str(x) for x in c
                ]
                yield ("tool_result", "\n".join(parts))
            else:
                yield ("tool_result", c if isinstance(c, str) else str(c))


def tool_result_texts(record: dict) -> List[str]:
    """record から外部（tool_result / 先頭 toolUseResult）テキストのみを返す。

    assistant record からは決して拾わない（内部生成と外部原文の分離が核心）。
    """
    out: List[str] = []
    tur = record.get("toolUseResult")
    if tur is not None:
        out.append(tur if isinstance(tur, str) else json.dumps(tur, ensure_ascii=False))
    if record.get("type") == "user":
        msg = record.get("message", {})
        content = msg.get("content") if isinstance(msg, dict) else None
        for kind, text in iter_content_blocks(content):
            if kind == "tool_result" and text:
                out.append(text)
    return out


def assistant_text_blocks(record: dict) -> List[Tuple[str, str]]:
    """assistant record の text/thinking ブロックのみを返す（tool_use/tool_result は除外）。"""
    if record.get("type") != "assistant":
        return []
    msg = record.get("message", {})
    content = msg.get("content") if isinstance(msg, dict) else None
    return [(k, t) for k, t in iter_content_blocks(content) if k in ("text", "thinking") and t]


# ------------------------------------------------------------------
# テキスト正規化
# ------------------------------------------------------------------
def strip_code_fences(text: str) -> str:
    """```...``` の fenced code block を除去する（例示タグを漏出と誤判定しないため）。"""
    # 対になる ``` を貪欲でなく除去。閉じない fence は行末まで無視で安全側。
    return re.sub(r"```.*?```", " ", text, flags=re.DOTALL)


def normalize_ws(text: str) -> str:
    """空白（改行含む）を全除去して byte 照合を空白差に頑健にする。"""
    return re.sub(r"\s+", "", text)


# ------------------------------------------------------------------
# Family A: 生タグ漏出
# ------------------------------------------------------------------
def detect_raw_tag_leak(text: str) -> bool:
    """assistant text に生タグが **構造完全** に漏出しているか。

    FP 対策:
    - code fence 内は除外
    - invoke 開きタグ ＋ （parameter か invoke 閉じ）の対で「構造」を要求。
      散文の ``invoke`` 言及や ``<invoke>`` 単独では発火しない。
    - もしくは function_calls 開き ＋ invoke 開きの対。
    """
    stripped = strip_code_fences(text)
    has_invoke = bool(_INVOKE_OPEN.search(stripped))
    has_param = bool(_PARAM_OPEN.search(stripped))
    has_close = bool(_INVOKE_CLOSE.search(stripped))
    has_fc = bool(_FUNCTION_CALLS_OPEN.search(stripped))
    if has_invoke and (has_param or has_close):
        return True
    if has_fc and has_invoke:
        return True
    return False


# ------------------------------------------------------------------
# Family B: 偽 system-reminder
# ------------------------------------------------------------------
def detect_fake_system_reminder(text: str) -> bool:
    """assistant text 側に system-reminder の実体タグが出ているか（code fence 除外）。"""
    stripped = strip_code_fences(text)
    return bool(_SYSTEM_REMINDER_OPEN.search(stripped))


# ------------------------------------------------------------------
# Family C-lite: 汚染宣言 × 引用リテラル原文非在
# ------------------------------------------------------------------
def has_contamination_vocab(text: str) -> bool:
    low = text.lower()
    for kw in _CONTAM_VOCAB:
        if kw.isascii():
            if kw.lower() in low:
                return True
        elif kw in text:
            return True
    return False


# 引用リテラルが「注入された指示/文」でなく path/command/code/ファイル名 断片である兆候。
# これらは Family C の「引用された偽指示」対象から外す（実 corpus で最頻の FP 源）。
_PATH_LIKE = re.compile(r"^[~./][\w./\-]+$|/[\w.\-]+/[\w.\-]+")  # 絶対/相対パス
_CODE_LIKE = re.compile(r"[=;{}]|\(\s*\)|=>|\breturn\b|\bimport\b|\bdef\b|`|</|/>")  # コード/タグ記号
_FILENAME_LIKE = re.compile(r"\.[a-z]{1,5}(:\d+)?$")  # foo.md / bar.ts:40 等
# 汚染衛生ルール（tool-call-hygiene.md）の引用に特有の語（真の注入内容にはまず現れない）。
_RULE_CITATION = ("粘らず", "の入口", "10行以内", "1つずつ正しく呼ぶ")


def _looks_like_path_or_code(lit: str) -> bool:
    """path / command / code / filename / rule 引用っぽい（注入指示の主張でない）か。"""
    s = lit.strip()
    if _PATH_LIKE.search(s) or _CODE_LIKE.search(s) or _FILENAME_LIKE.search(s):
        return True
    # 単一トークンの ascii（空白なし）＝ファイル名/識別子/コマンド語。注入された「指示文」は
    # 文（空白を含む）なので、空白なし ascii は主張対象から外す。
    if s.isascii() and " " not in s:
        return True
    # 衛生ルールの引用（この現象への対処法を assistant が引用している＝メタ議論・live confab でない）。
    if any(marker in s for marker in _RULE_CITATION):
        return True
    return False


def _quoted_literal_spans(
    text: str, min_len: int = _DEFAULT_MIN_LITERAL
) -> List[Tuple[str, int, int]]:
    """鉤括弧 / quote 内の引用リテラル（min_len 文字以上）を (literal, start, end) で返す。

    start/end は括弧を含む span（proximity 計算で「引用の外側」を判定するため）。
    path/command/code 断片は「注入された指示の引用」でないため除外する（FP 抑制）。
    """
    out: List[Tuple[str, int, int]] = []
    for open_q, close_q in _QUOTE_PAIRS:
        if open_q == close_q:
            pattern = re.escape(open_q) + r"([^" + re.escape(open_q) + r"]+?)" + re.escape(close_q)
        else:
            pattern = re.escape(open_q) + r"([^" + re.escape(close_q) + r"]+?)" + re.escape(close_q)
        for m in re.finditer(pattern, text):
            lit = m.group(1).strip()
            if len(lit) >= min_len and not _looks_like_path_or_code(lit):
                out.append((lit, m.start(), m.end()))
    return out


def extract_quoted_literals(text: str, min_len: int = _DEFAULT_MIN_LITERAL) -> List[str]:
    """鉤括弧 / quote 内の引用リテラル（min_len 文字以上）を抽出する。"""
    return [lit for lit, _s, _e in _quoted_literal_spans(text, min_len)]


def _vocab_positions_with_kw(text: str) -> List[Tuple[int, str]]:
    """汚染語彙の出現開始位置（文字 index）を、マッチした語彙そのものと対で全て返す。"""
    low = text.lower()
    positions: List[Tuple[int, str]] = []
    for kw in _CONTAM_VOCAB:
        hay, needle = (low, kw.lower()) if kw.isascii() else (text, kw)
        start = 0
        while True:
            idx = hay.find(needle, start)
            if idx < 0:
                break
            positions.append((idx, kw))
            start = idx + 1
    return positions


def _vocab_positions(text: str) -> List[int]:
    """汚染語彙の出現開始位置（文字 index）を全て返す。"""
    return [pos for pos, _kw in _vocab_positions_with_kw(text)]


def domain_vocab_fp_words(name: str) -> Tuple[str, ...]:
    """PJ 名（encoded dir 名 or basename）がドメイン語彙 FP 除外対象なら、除外する汚染語彙を返す。

    ``is_topic_pj`` と同型の PJ slug マッチング（部分一致）。ハード除外ではなく、呼び出し側
    （``detect_confab_claim`` / ``scan_records``）が該当語彙のみを根拠にした Family C 候補を
    ``domain_vocab_fp`` バケットへ振り分けるための情報を返す（#203）。非該当 PJ は空 tuple。
    """
    if not name:
        return ()
    words: List[str] = []
    seen = set()
    for marker, vocab in _DOMAIN_VOCAB_FP_MARKERS.items():
        if marker in name:
            for w in vocab:
                if w not in seen:
                    seen.add(w)
                    words.append(w)
    return tuple(words)


def _confab_evidence(
    text: str,
    recent_tool_results: Iterable[str],
    min_len: int = _DEFAULT_MIN_LITERAL,
    proximity: int = _DEFAULT_PROXIMITY,
    excluded_vocab: Iterable[str] = (),
) -> Tuple[Optional[str], Optional[str]]:
    """Family C-lite の判定本体。``(genuine_literal, domain_fp_literal)`` を返す（排他的）。

    ``excluded_vocab`` に含まれる語彙**のみ**が根拠の候補は ``domain_fp_literal`` 側へ回し、
    それ以外（除外対象でない汚染語彙が近傍にある）は従来どおり ``genuine_literal`` として返す。
    """
    literals = _quoted_literal_spans(text, min_len=min_len)
    if not literals:
        return None, None
    vocab_hits = _vocab_positions_with_kw(text)
    if not vocab_hits:
        return None, None
    excluded = set(excluded_vocab)
    corpus = normalize_ws("\n".join(recent_tool_results))
    domain_fp_lit: Optional[str] = None
    for lit, start, end in literals:
        if normalize_ws(lit) in corpus:
            continue  # 原文に実在 → 誤認でない
        fp_found = False
        for vp, kw in vocab_hits:
            if start <= vp < end:
                continue  # 語彙が引用の内側（ルール文引用等）→ 主張でない
            dist = (start - vp) if vp < start else (vp - end)
            if 0 <= dist <= proximity:
                if kw in excluded:
                    fp_found = True
                    continue  # 除外対象語彙のみでは genuine 扱いにしない
                return lit, None  # 除外対象でない語彙が根拠 → genuine hit
        if fp_found and domain_fp_lit is None:
            domain_fp_lit = lit
    return None, domain_fp_lit


def detect_confab_claim(
    text: str,
    recent_tool_results: Iterable[str],
    min_len: int = _DEFAULT_MIN_LITERAL,
    proximity: int = _DEFAULT_PROXIMITY,
    excluded_vocab: Iterable[str] = (),
) -> Optional[str]:
    """Family C-lite（高精度・低再現）。条件を全て満たす引用リテラルを 1 件返す。

    発火条件（全 AND）:
    1. 引用リテラル（>=min_len）が存在する（＝assistant が具体的な「注入された文字列」を引用）
    2. そのリテラルが直近 tool_result 原文に空白正規化 byte 照合で **非在**
    3. 汚染語彙（claim）がリテラルの **外側** かつ proximity 文字以内の散文に存在する

    3 の「外側」制約が重要: ルール文/引用そのものが汚染語彙を含むケース（例: この PJ の
    tool-call-hygiene.md を assistant がそのまま引用）を落とす。語彙は「これは汚染だ」という
    **主張**として引用の近傍散文に出ていなければならない。paraphrase 型の言い換え宣言は取り
    こぼす（＝仕様）。

    ``excluded_vocab``（#203）を渡すと、その語彙のみが根拠の候補は genuine hit として返さない
    （呼び出し元が ``domain_vocab_fp`` バケットへ振り分けたい場合は ``_confab_evidence`` を使う）。
    """
    genuine, _domain_fp = _confab_evidence(
        text, recent_tool_results, min_len=min_len, proximity=proximity, excluded_vocab=excluded_vocab
    )
    return genuine


# ------------------------------------------------------------------
# transcript 走査
# ------------------------------------------------------------------
def scan_records(
    records: Iterable[dict],
    *,
    k: int = _DEFAULT_K,
    session_id: str = "",
    min_literal: int = _DEFAULT_MIN_LITERAL,
    excluded_vocab: Iterable[str] = (),
) -> ScanReport:
    """1 transcript（record 列）を走査し 3 系統の指紋を集める。

    Family C は「直前 K 個の tool_result 原文」を running window で保持して照合する。
    ``excluded_vocab``（#203）に含まれる語彙のみが根拠の Family C 候補は ``family_c`` でなく
    ``domain_vocab_fp`` バケットへ振り分ける（ハード除外でなく別集計）。
    """
    report = ScanReport()
    recent: Deque[str] = deque(maxlen=max(1, k))
    for ln, record in enumerate(records, 1):
        if not isinstance(record, dict):
            continue
        # 外部原文を先に window へ積む（この assistant ターンの「直前」に間に合わせる）。
        for tr in tool_result_texts(record):
            if tr:
                recent.append(tr)
        for block, text in assistant_text_blocks(record):
            if detect_raw_tag_leak(text):
                report.family_a.append(
                    Hit("A", ln, block, _snippet(text), session_id=session_id)
                )
            if detect_fake_system_reminder(text):
                report.family_b.append(
                    Hit("B", ln, block, _snippet(text), session_id=session_id)
                )
            genuine, domain_fp = _confab_evidence(
                text, recent, min_len=min_literal, excluded_vocab=excluded_vocab
            )
            if genuine is not None:
                report.family_c.append(
                    Hit(
                        "C",
                        ln,
                        block,
                        genuine,
                        reference_text=_snippet("\n".join(recent), width=160),
                        session_id=session_id,
                    )
                )
            elif domain_fp is not None:
                report.domain_vocab_fp.append(
                    Hit(
                        "C",
                        ln,
                        block,
                        domain_fp,
                        reference_text=_snippet("\n".join(recent), width=160),
                        session_id=session_id,
                    )
                )
    return report


def _snippet(text: str, width: int = 200) -> str:
    s = " ".join(text.split())
    return s if len(s) <= width else s[:width] + "…"


def scan_file(
    path: Path, *, k: int = _DEFAULT_K, excluded_vocab: Iterable[str] = ()
) -> ScanReport:
    """1 jsonl transcript ファイルを走査する。session_id はファイル stem。"""
    path = Path(path)
    session_id = path.stem
    records: List[dict] = []
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return ScanReport()
    return scan_records(records, k=k, session_id=session_id, excluded_vocab=excluded_vocab)


def is_topic_pj(name: str) -> bool:
    """PJ 名（encoded dir 名 or basename）が話題 PJ（Family C の FP 源）か。"""
    if not name:
        return False
    return any(marker in name for marker in _TOPIC_PJ_MARKERS)


def resolve_cc_transcript_dir(project_dir) -> Path:
    """``~/.claude/projects/<cwd-encoded>`` を返す（memory でなく transcript 本体）。

    CC は projects dir を cwd 絶対パスの ``/`` → ``-`` 置換で持つ。存在する candidate
    （先頭 ``-`` 有無）を優先し、無ければ primary candidate（非存在 Path）を返す。
    resolve_cc_memory_dir と同じエンコード規約（#18/#19）。
    """
    base = Path.home() / ".claude" / "projects"
    target = Path(project_dir)
    encoded = str(target).replace("/", "-")
    for candidate in (encoded, encoded.lstrip("-")):
        d = base / candidate
        if d.is_dir():
            return d
    return base / encoded


def scan_project_transcripts(
    transcript_dir,
    *,
    recent_days: int = 7,
    baseline_days: int = 14,
    k: int = _DEFAULT_K,
    max_files: int = _DEFAULT_MAX_FILES,
    now: Optional[float] = None,
) -> Optional[ProjectScanReport]:
    """transcript ディレクトリ（本体 + subagents の jsonl 群）を mtime 窓で走査し period 集計する。

    引数は ``~/.claude/projects/<encoded>`` の **実ディレクトリ**（section 側で
    ``resolve_cc_transcript_dir`` により解決したもの）。core を CC エンコードから切り離し
    テスト可能にするため、ここでは encode を行わない。

    - recent 窓: mtime >= now - recent_days
    - baseline 窓: now - baseline_days <= mtime < now - recent_days
    - 窓外（古い）ファイルは走査しない（暴走防止・O(全量) を避ける）
    transcript dir が無ければ None（沈黙）。
    """
    tdir = Path(transcript_dir)
    if not tdir.is_dir():
        return None
    now = time.time() if now is None else now
    recent_cutoff = now - recent_days * 86400
    baseline_cutoff = now - baseline_days * 86400

    is_topic = is_topic_pj(tdir.name)
    excluded_vocab = domain_vocab_fp_words(tdir.name)
    report = ScanReport()
    recent_counts = {"A": 0, "B": 0, "C": 0}
    baseline_counts = {"A": 0, "B": 0, "C": 0}
    files_scanned = 0

    files = sorted(tdir.rglob("*.jsonl"), key=_safe_mtime, reverse=True)
    for f in files:
        if files_scanned >= max_files:
            break
        mt = _safe_mtime(f)
        if mt < baseline_cutoff:
            continue  # 窓より古い → skip
        file_report = scan_file(f, k=k, excluded_vocab=excluded_vocab)
        files_scanned += 1
        if file_report.total == 0 and not file_report.domain_vocab_fp:
            continue
        report.extend(file_report)
        bucket = recent_counts if mt >= recent_cutoff else baseline_counts
        for fam, n in file_report.counts().items():
            bucket[fam] += n
    return ProjectScanReport(
        report=report,
        recent_counts=recent_counts,
        baseline_counts=baseline_counts,
        files_scanned=files_scanned,
        is_topic=is_topic,
    )


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
