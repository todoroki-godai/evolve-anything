"""belief_entropy: 生成後 memory 要約の retention/drift 決定論ゲート。

LLM 呼び出しなし。auto_memory_runner が _call_llm で生成した要約が、
元 corrections の情報を保持(retention)し、ソースに無い主張を過剰に
含まない(drift)かを similarity トークンの集合演算で近似評価する。

    retention = |src ∩ sum| / |src|   ソース用語の保持率（= recall）
    drift     = |sum \\ src| / |sum|   要約の非接地トークン率（= 1 - precision）
    should_store = retention >= RETENTION_THRESHOLD かつ drift <= DRIFT_THRESHOLD

位置づけ:
- memory_gating（生成前フィルタ: 保存に値するか）の **後段** に立つ安全網。
  memory_gating は「その correction を覚える価値があるか」を判定し、
  belief_entropy は「生成された要約がソースを忠実に表しているか」を判定する。
- 過剰ブロックを避け「明確な情報欠落・幻覚」のみを弾く保守的な閾値。

設計上の注意:
- 粗いトークン化（日本語など空白/記号で分割されにくいテキスト）では
  トークン信号が乏しく retention/drift が不正確になりやすい。十分なトークン数が
  無ければ should_store=True（ブロックしない）に倒す（low_signal ガード）。
- 要約は frontmatter（name/description/...）を含む生テキストを渡す前提。
  frontmatter の構造トークンは drift を僅かに押し上げるが、保守的な
  DRIFT_THRESHOLD がそれを吸収する（通常の要約は frontmatter だけでは超えない）。
- Belief Entropy 論文（arXiv:2605.30159）の厳密な不確実性推定ではなく、
  hot-hook（毎 Stop 発火）原則に沿った LLM ゼロの決定論プロキシ。

issue #285
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from similarity import jaccard_coefficient, tokenize  # noqa: F401  (再エクスポート用)
except ImportError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from similarity import jaccard_coefficient, tokenize  # noqa: F401

# 保守的な安全網閾値（memory_gating で worth 判定済みの後段に立つため緩め）
RETENTION_THRESHOLD = 0.25  # これ未満 = ソース用語の大半を落とした明確な情報欠落
DRIFT_THRESHOLD = 0.85      # これ超過 = 要約の大半がソース非接地（幻覚/脱線）

# 粗いトークン化（日本語等）で「信号が乏しい」と判定する最小トークン数
MIN_SIGNAL_TOKENS = 5


@dataclass
class BeliefScore:
    """生成後ゲートの評価結果。"""

    retention: float    # ソース用語の保持率 [0.0-1.0]
    drift: float        # 要約の非接地トークン率 [0.0-1.0]
    should_store: bool  # retention >= RETENTION かつ drift <= DRIFT
    low_signal: bool    # トークン不足で評価を保留した（= ブロックしない）


def _strip_frontmatter(text: str) -> str:
    """先頭の YAML frontmatter（--- ... ---）を取り除き body を返す。

    frontmatter の構造トークン（name/description/metadata/type/importance 等）は
    常に存在しソースに非接地なため、drift を不当に押し上げる。retention/drift は
    本文の忠実度を測るべきなので body のみを評価対象にする。frontmatter が無ければ
    元テキストをそのまま返す。
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    lines = stripped.splitlines()
    # 先頭 '---' の次から 2 本目の '---' を探す
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text  # 閉じ '---' が無い = frontmatter ではない


def _source_text(corrections: List[dict]) -> str:
    """corrections から比較元テキストを抽出する。

    各 correction の `message`（無ければ `pattern`）を連結する。
    """
    parts: List[str] = []
    for c in corrections:
        if not isinstance(c, dict):
            continue
        msg = c.get("message") or c.get("pattern") or ""
        if msg:
            parts.append(str(msg))
    return " ".join(parts)


def score_belief(
    summary: str,
    corrections: List[dict],
    retention_threshold: float = RETENTION_THRESHOLD,
    drift_threshold: float = DRIFT_THRESHOLD,
) -> BeliefScore:
    """要約 vs ソース corrections の retention/drift を評価する。

    Args:
        summary:           LLM が生成した memory 候補テキスト（frontmatter 込み生テキスト）。
        corrections:       生成元の correction レコード列（`message` を含む dict）。
        retention_threshold: should_store の retention 下限。
        drift_threshold:     should_store の drift 上限。

    Returns:
        BeliefScore。比較不能（ソース/要約が空）・信号不足の場合は
        should_store=True / low_signal=True を返す（安全側）。
    """
    src_tokens = tokenize(_source_text(corrections))
    sum_tokens = tokenize(_strip_frontmatter(summary or ""))

    # ソースまたは要約のトークンが無ければ比較不能 → 安全側で保存を許可
    if not src_tokens or not sum_tokens:
        return BeliefScore(retention=1.0, drift=0.0, should_store=True, low_signal=True)

    retention = len(src_tokens & sum_tokens) / len(src_tokens)
    drift = len(sum_tokens - src_tokens) / len(sum_tokens)

    # 粗いトークン化（日本語等）で信号が乏しい場合はブロックしない
    low_signal = len(src_tokens) < MIN_SIGNAL_TOKENS or len(sum_tokens) < MIN_SIGNAL_TOKENS
    if low_signal:
        return BeliefScore(
            retention=retention, drift=drift, should_store=True, low_signal=True
        )

    should_store = retention >= retention_threshold and drift <= drift_threshold
    return BeliefScore(
        retention=retention, drift=drift, should_store=should_store, low_signal=False
    )


# ── observability（#285 audit surface）─────────────────────────────────────

# belief ゲートの block ログ
BLOCKS_FILENAME = "belief_blocks.jsonl"


def summarize_blocks(
    data_dir: Path, days: int = 30
) -> Tuple[int, List[str]]:
    """belief_blocks.jsonl から直近 days 日の block を集計する。

    Args:
        data_dir: belief_blocks.jsonl を含むデータディレクトリ。
        days:     集計ウィンドウ（日数）。

    Returns:
        (件数, 直近 block の summary_head リスト最大3件)。
        ファイル不在・読み取りエラー時は (0, [])。
    """
    path = Path(data_dir) / BLOCKS_FILENAME
    if not path.exists():
        return 0, []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    heads: List[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(rec.get("ts"))
            if ts is not None and ts < cutoff:
                continue
            count += 1
            head = str(rec.get("summary_head", "")).strip()
            if head and len(heads) < 3:
                heads.append(head)
    except OSError:
        return 0, []
    return count, heads


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """ISO 8601 文字列を aware datetime に変換する。失敗時は None。"""
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
