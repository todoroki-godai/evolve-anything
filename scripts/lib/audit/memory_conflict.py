"""Memory Conflict — 同一 PJ 内の非両立 fact ペアを決定論で検出する（#83）。

背景: fleet recall は keyword 一致で memory fact を引くが、同一 PJ × 同一 entity に対し
**非両立な値**（肯定 / 否定）を持つ fact ペアが共存していると、recall が矛盾する2記憶を
両方掴んでも気づかず提案を汚染する。本モジュールはその矛盾候補ペアを決定論で検出し
advisory に surface する（memory_temporal=時間降格 / memory_contagion=評価源偏り とも
直交する第3の記憶健全性軸）。

**完全に決定論・LLM 非依存。** 矛盾の定義は捏造閾値を置かず「明確な対立のみ」に限定し
FP を避ける（保守側）:

  矛盾ペア = 同一 PJ の active な 2 fact が、**同じ specific key（backtick code span /
  識別子トークン）** を持ち、一方が肯定極性・他方が否定極性で言及している組。

極性判定は SOV/SVO の語順非対称を利用する（決定論）:
  - 日本語の否定は object の **後** に来る（「`gstack` を使わない」）ため forward 窓で見る。
  - 英語の否定は object の **前** に来る（「don't use X」「use B not A」）ため backward 窓で見る。
この非対称性により「A ではなく B」型（A=否定 / B=肯定）が正しく割り当たり、文内に肯定・否定が
同居しても誤って自己矛盾扱いしない。

スコープ（全PJ共通 DATA_DIR pitfall の再来防止）: 当 PJ の slug が解決した
``~/.claude/projects/<path-encoded>/memory/`` の **1 ディレクトリのみ** を対象とし、絶対に
全 PJ を走査しない。memory dir は ``pj_slug.resolve_cc_memory_dir``（CC パスエンコード単一
ソース）で解決する（``resolve_pj_slug`` の repo-basename slug は名前空間が別物で #19 で沈黙
バグを踏んだため使わない）。集計対象は ``*.md`` のうち ``MEMORY.md``（索引であって memory
実体でない）を除き、かつ superseded（時間降格済み＝解決済み矛盾）を除いた active fact。

floor: active fact が ``FLOOR`` 件未満なら矛盾評価しない（小コーパスノイズ回避）。
矛盾ゼロは ``✓ no conflicts (N facts scanned)``（silence != evaluated）。
重み非干渉の advisory（dry-run 安全・新ストアを作らない＝読み取り専用）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pj_slug import resolve_cc_memory_dir

# 索引ファイル（memory 実体ではないので集計対象外）。
_INDEX_FILENAME = "MEMORY.md"

# active fact がこれ未満なら矛盾評価しない（小コーパスノイズ回避・保守側）。
FLOOR = 3

# key の最小正規化長（これ未満は specific でないとして除外）。
MIN_KEY_LEN = 3

# 文 / 節の境界。極性判定の forward/backward 窓をこの内側に限定する。
_BOUNDARY_RE = re.compile(r"[。\n;；]")

# 否定マーカー（決定論）。日本語は object の後（SOV）→ forward 窓で見る。
# 英語は object の前（SVO・「use B not A」）→ backward 窓で見る。この非対称が
# 「A ではなく B」型を A=否定 / B=肯定 に正しく割り当てる肝。
_JP_NEG: Tuple[str, ...] = (
    "禁止", "ではなく", "でなく", "じゃなく", "しない", "せず", "するな",
    "不可", "避け", "ません", "ない", "なく",
)
_EN_NEG: Tuple[str, ...] = (
    "not ", "n't", "never", "avoid", "don't", "do not", "no ",
    "instead of", "rather than", "without",
)

# specific key の抽出パターン。backtick code span と識別子トークン
# （区切り入り snake/kebab/dotted/path、または CamelCase）。汎用語は拾わない。
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_IDENT_SEP_RE = re.compile(r"[A-Za-z0-9]+(?:[._/\-][A-Za-z0-9]+)+")
_CAMEL_RE = re.compile(r"[A-Z][a-z]+(?:[A-Z][a-z0-9]*)+")

# 汎用すぎて key にしない語（lowercased）。
_STOPWORDS = {"true", "false", "none", "null", "todo", "n/a"}


@dataclass
class ConflictPair:
    """非両立な記憶ペア 1 件（決定論）。

    key: 両 fact が共有する specific key（normalize 済み）。
    pos_path / pos_value: 肯定極性側の fact のパスと根拠文（対立値）。
    neg_path / neg_value: 否定極性側の fact のパスと根拠文（対立値）。
    """

    key: str
    pos_path: Path
    pos_value: str
    neg_path: Path
    neg_value: str


@dataclass
class ConflictReport:
    """矛盾検出の集計結果（決定論）。

    applicable: 評価対象が成立したか（active fact >= FLOOR）。
    total_facts: 走査した active fact 件数（evidence 用）。
    conflicts: 検出した矛盾ペア（保守的・明確な対立のみ）。
    """

    applicable: bool
    total_facts: int
    conflicts: List[ConflictPair] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────
# ローダ（PJ slug スコープ・1 ディレクトリのみ）
# ─────────────────────────────────────────────────────────────────
def _resolve_memory_dir(project_dir: Path) -> Path:
    """当 PJ の memory ディレクトリを返す（CC パスエンコード単一ソース・#19/#18 と共有）。"""
    return resolve_cc_memory_dir(Path(project_dir))


def _memory_files(memory_dir: Path) -> List[Path]:
    """集計対象の memory ファイル（MEMORY.md を除く *.md）を決定論順で返す。"""
    if not memory_dir.is_dir():
        return []
    return sorted(p for p in memory_dir.glob("*.md") if p.name != _INDEX_FILENAME)


def _is_superseded(path: Path) -> bool:
    """temporal frontmatter で supersede 済み（=解決済み矛盾）かを判定する。"""
    try:
        from memory_temporal import is_superseded, parse_memory_temporal

        return is_superseded(parse_memory_temporal(path))
    except Exception:
        return False


def _body_text(path: Path) -> str:
    """frontmatter を除いた本文を返す（YAML キーを key 抽出に混ぜない）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if text.startswith("---"):
        try:
            from frontmatter import find_frontmatter_close
        except ImportError:
            return text
        end = find_frontmatter_close(text)
        if end != -1:
            return text[end + 3:]
    return text


# ─────────────────────────────────────────────────────────────────
# key / 極性抽出（決定論）
# ─────────────────────────────────────────────────────────────────
def _normalize_key(raw: str) -> Optional[str]:
    """key を正規化する（lowercase + 空白畳み）。specific でなければ None。"""
    k = " ".join(raw.strip().lower().split())
    if len(k) < MIN_KEY_LEN:
        return None
    if k in _STOPWORDS:
        return None
    if not any(c.isalpha() for c in k):
        return None  # 純数値 / 記号のみは key にしない
    return k


def _split_sentences(text: str) -> List[str]:
    """本文を文 / 節境界で分割する（forward/backward 窓を文内に閉じる）。"""
    return [p.strip() for p in _BOUNDARY_RE.split(text) if p.strip()]


def _key_spans(sentence: str) -> List[Tuple[str, int, int]]:
    """文中の specific key を (normalize_key, start, end) で返す。"""
    spans: List[Tuple[str, int, int]] = []
    for m in _BACKTICK_RE.finditer(sentence):
        nk = _normalize_key(m.group(1))
        if nk:
            spans.append((nk, m.start(), m.end()))
    for rx in (_IDENT_SEP_RE, _CAMEL_RE):
        for m in rx.finditer(sentence):
            nk = _normalize_key(m.group(0))
            if nk:
                spans.append((nk, m.start(), m.end()))
    return spans


def _polarity(sentence: str, start: int, end: int) -> str:
    """key 出現の極性を返す（"pos" / "neg"）。

    日本語否定は forward 窓（key の後・次境界まで）、英語否定は backward 窓
    （key の前）で見る。文は既に境界分割済みなので窓は同一文内に閉じる。
    """
    forward = sentence[end:]
    bnd = _BOUNDARY_RE.search(forward)
    if bnd:
        forward = forward[: bnd.start()]
    if any(marker in forward for marker in _JP_NEG):
        return "neg"
    backward = sentence[:start].lower()
    if any(marker in backward for marker in _EN_NEG):
        return "neg"
    return "pos"


def _extract_claims(body: str) -> Dict[str, Tuple[str, str]]:
    """本文から key -> (polarity, 根拠文) を抽出する。

    同一ファイル内で同じ key が肯定・否定の両極性を持つ場合は ambiguous として
    drop する（内部不整合であって cross-file 矛盾ではない・FP 回避）。
    """
    polarities: Dict[str, set] = {}
    sentences: Dict[str, Dict[str, str]] = {}
    for sent in _split_sentences(body):
        for key, s, e in _key_spans(sent):
            pol = _polarity(sent, s, e)
            polarities.setdefault(key, set()).add(pol)
            sentences.setdefault(key, {}).setdefault(pol, sent)
    claims: Dict[str, Tuple[str, str]] = {}
    for key, pols in polarities.items():
        if len(pols) != 1:
            continue  # ambiguous（両極性同居）→ drop
        pol = next(iter(pols))
        claims[key] = (pol, sentences[key][pol])
    return claims


# ─────────────────────────────────────────────────────────────────
# 集計
# ─────────────────────────────────────────────────────────────────
def compute_conflicts(project_dir: Path) -> ConflictReport:
    """当 PJ の active memory fact から非両立ペアを検出する（決定論・LLM 非依存）。

    - memory dir 不在 / active fact < FLOOR → applicable=False（沈黙対象）。
    - active fact >= FLOOR → applicable=True、矛盾ペア（無ければ空）を返す。
    """
    memory_dir = _resolve_memory_dir(Path(project_dir))
    active = [p for p in _memory_files(memory_dir) if not _is_superseded(p)]
    total = len(active)
    if total < FLOOR:
        return ConflictReport(applicable=False, total_facts=total, conflicts=[])

    pos_map: Dict[str, List[Tuple[Path, str]]] = {}
    neg_map: Dict[str, List[Tuple[Path, str]]] = {}
    for path in active:
        for key, (pol, sent) in _extract_claims(_body_text(path)).items():
            target = pos_map if pol == "pos" else neg_map
            target.setdefault(key, []).append((path, sent))

    conflicts: List[ConflictPair] = []
    for key in sorted(set(pos_map) & set(neg_map)):
        pos_path, pos_val = sorted(pos_map[key], key=lambda t: str(t[0]))[0]
        neg_path, neg_val = sorted(neg_map[key], key=lambda t: str(t[0]))[0]
        conflicts.append(
            ConflictPair(
                key=key,
                pos_path=pos_path,
                pos_value=pos_val,
                neg_path=neg_path,
                neg_value=neg_val,
            )
        )

    return ConflictReport(applicable=True, total_facts=total, conflicts=conflicts)
