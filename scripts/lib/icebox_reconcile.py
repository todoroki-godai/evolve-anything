"""icebox_reconcile — icebox（凍結 issue）棚卸しの3レーン決定論分類（#352）。

#194/#351 の通知は件数と最古日数を告げるだけで、個々の issue が「再開条件を満たしたか」を
判定しない。2026-08-01 の一発監査（54件全件の再開条件を audit 出力と突合）で「条件文が自由文
だから判定できない」のでなく「条件が観測できる前提で書かれているのに観測器（audit
observability section 等）が動いていない」ことが真の障害と判明した。そこで判定を3レーンに
分ける:

- レーン1「成立」  : issue 本文 `## 再開条件` 配下の fenced YAML `reopen-when:` ブロック
  （source/metric/op/threshold）を実ストアと決定論突合し、閾値を満たせば成立。
- レーン2「観測器不在」: ブロックが無い、または `source`/`metric` が EVALUATORS に未実装
  → 「observer を作れば判定に乗る」候補として提示（**取りこぼし無し**の受け皿）。
- レーン3「失効」   : ブロックはあり判定可能だが、凍結から ARCHIVE_AGE_DAYS 日経過・未成立・
  closedAt 以降に本文が実質編集されていない → archive 候補として提示のみ（自動 close はしない）。

レーンは互いに排他（優先順位: met > observer_missing > archive_candidate > None）。
レーン2の判定は年齢に関わらず適用する（ブロック欠落・source 未実装は仕様上つねにレーン2。
#352 issue 本文「ブロックが無い/source が実在しない issue はレーン2へ自動分類」の通り）。

gh 呼び出しはこのモジュールの責務外（呼び出し側が issue の dict リストを渡す）。
本モジュールは純関数中心・決定論・LLM 非依存・ゼロ副作用（読み取りのみ）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

REOPEN_HEADING = "## 再開条件"

# 凍結からこの日数を超え、未成立・本文未編集ならレーン3（archive 候補）。
ARCHIVE_AGE_DAYS = 180

# closedAt → updatedAt の差がこれを超えたら「本文が編集された」とみなしレーン3から除外する
# （GitHub はラベル操作等の付随イベントでも updatedAt を進めるため、僅かな差は編集とみなさない）。
UPDATE_TOLERANCE_DAYS = 1

_REQUIRED_BLOCK_KEYS = ("source", "metric", "op", "threshold")

_OPS: Dict[str, Callable[[float, float], bool]] = {
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
}

# icebox 棚卸しは evolve-anything 自身の backlog に対してのみ動く（daily runner の
# ICEBOX_REPO と同じスコープ）。reopen-when ブロックが `pj_slug` を明示しなければこれを使う。
SELF_PJ_SLUG = "evolve-anything"


# ─────────────────────────────────────────────────────────────────
# reopen-when ブロック抽出
# ─────────────────────────────────────────────────────────────────
def _section_after_heading(body: str, heading: str) -> Optional[str]:
    """`heading` 直後から次の `## ` 見出し手前まで（無ければ末尾まで）を返す。"""
    idx = body.find(heading)
    if idx == -1:
        return None
    rest = body[idx + len(heading):]
    m = re.search(r"\n## ", rest)
    return rest[: m.start()] if m else rest


def _fenced_blocks(section_text: str) -> List[str]:
    """```yaml ... ``` / ``` ... ``` フェンス内テキストを出現順に全て返す。"""
    return re.findall(r"```(?:ya?ml)?\n(.*?)```", section_text, flags=re.DOTALL)


def extract_reopen_when(body: Optional[str]) -> Optional[Dict[str, Any]]:
    """issue 本文の `## 再開条件` 配下から reopen-when ブロック（dict）を抽出する。

    以下はいずれも None（区別せずレーン2「観測器不在」に合流させるため）:
    - body が空 / `## 再開条件` 見出しが無い
    - 見出し配下に fenced YAML が無い / パース不能
    - トップレベルに `reopen-when` キーが無い、または値が dict でない
    - source/metric/op/threshold のいずれか欠落
    - op が未知の演算子

    自由文併記や `agent_type`/`pj_slug` 等の追加キーはそのまま保持して返す。
    """
    if not isinstance(body, str) or not body:
        return None
    section = _section_after_heading(body, REOPEN_HEADING)
    if section is None:
        return None
    for raw in _fenced_blocks(section):
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError:
            continue
        if not isinstance(parsed, dict):
            continue
        block = parsed.get("reopen-when")
        if not isinstance(block, dict):
            continue
        if not all(k in block for k in _REQUIRED_BLOCK_KEYS):
            continue
        if block.get("op") not in _OPS:
            continue
        return block
    return None


# ─────────────────────────────────────────────────────────────────
# evaluator registry（最小セット・#352）
# ─────────────────────────────────────────────────────────────────
def _eval_weak_signals_unprocessed_count(
    data_dir: Path, extra: Dict[str, Any]
) -> Optional[float]:
    """当 PJ（既定 SELF_PJ_SLUG）の未昇格・非失効 weak_signal 件数。"""
    try:
        from weak_signals.store import read_signals
        from weak_signals.ttl import is_effectively_expired
    except ImportError:
        return None
    try:
        from store_read_union import pj_slug_match as _pj_slug_match
    except ImportError:
        def _pj_slug_match(a, b):  # type: ignore
            return a == b

    slug = extra.get("pj_slug") or SELF_PJ_SLUG
    path = Path(data_dir) / "weak_signals.jsonl"
    recs = read_signals(path)
    count = 0
    for r in recs:
        if not _pj_slug_match(r.get("pj_slug"), slug):
            continue
        if r.get("promoted"):
            continue
        if is_effectively_expired(r):
            continue
        count += 1
    return float(count)


def _eval_subagent_traces_first_try_success_rate(
    data_dir: Path, extra: Dict[str, Any]
) -> Optional[float]:
    """当 PJ の agent_type 別内部一発成功率。`agent_type` 指定が無ければ n 加重平均。"""
    try:
        from subagent_traces.query import per_agent_type_summary
    except ImportError:
        return None

    slug = extra.get("pj_slug") or SELF_PJ_SLUG
    summaries = per_agent_type_summary(slug, data_dir=Path(data_dir))
    if not summaries:
        return None

    agent_type = extra.get("agent_type")
    if agent_type:
        for s in summaries:
            if s.get("agent_type") == agent_type:
                return float(s["first_try_success_rate"])
        return None

    total_n = sum(int(s.get("n", 0)) for s in summaries)
    if not total_n:
        return None
    weighted = sum(
        s["first_try_success_rate"] * int(s.get("n", 0)) for s in summaries
    ) / total_n
    return float(weighted)


def _eval_token_usage_total_tokens(
    data_dir: Path, extra: Dict[str, Any]
) -> Optional[float]:
    """token_usage.db の全 PJ 合計トークン消費（input+output+cache_creation+cache_read）。

    token_usage_store の DATA_DIR/USAGE_DB はモジュール import 時に解決される固定属性
    （pitfall_module_level_datadir_import_copy）で `data_dir` 引数を受け取れないため、
    この評価器は暫定で `data_dir` を無視し token_usage_store が実際に解決した DB を読む
    （production では同一 DATA_DIR に一致する）。テストは `token_usage_store.USAGE_DB` を
    直接 monkeypatch する。
    """
    try:
        import token_usage_store as _store
    except ImportError:
        return None
    if not _store.HAS_DUCKDB or not _store.USAGE_DB.exists():
        return None
    try:
        rows = _store.query(
            "SELECT SUM(input_tokens + output_tokens + "
            "cache_creation_input_tokens + cache_read_input_tokens) FROM token_usage"
        )
    except Exception:
        return None
    if not rows or rows[0][0] is None:
        return 0.0
    return float(rows[0][0])


EVALUATORS: Dict[str, Dict[str, Callable[[Path, Dict[str, Any]], Optional[float]]]] = {
    "weak_signals": {"unprocessed_count": _eval_weak_signals_unprocessed_count},
    "subagent_traces": {
        "first_try_success_rate": _eval_subagent_traces_first_try_success_rate
    },
    "token_usage": {"total_tokens": _eval_token_usage_total_tokens},
}


def known_sources_summary(
    evaluators: Optional[Dict[str, Dict[str, Callable]]] = None,
) -> str:
    """observer_missing の reason に添える「対応済み source.metric」一覧（診断用）。"""
    evals = evaluators if evaluators is not None else EVALUATORS
    pairs = sorted(
        f"{source}.{metric}" for source, metrics in evals.items() for metric in metrics
    )
    return ", ".join(pairs) if pairs else "(未実装)"


# ─────────────────────────────────────────────────────────────────
# 3レーン分類
# ─────────────────────────────────────────────────────────────────
def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _edited_after_close(closed_dt: Optional[datetime], updated_dt: Optional[datetime]) -> bool:
    """closedAt 以降に本文が実質編集されたとみなせるか（UPDATE_TOLERANCE_DAYS 超の差）。"""
    if not closed_dt or not updated_dt:
        return False
    return (updated_dt - closed_dt).days > UPDATE_TOLERANCE_DAYS


def _verdict(
    number: Any,
    *,
    lane: Optional[str],
    reason: str,
    source: Optional[str] = None,
    metric: Optional[str] = None,
    op: Optional[str] = None,
    threshold: Optional[float] = None,
    value: Optional[float] = None,
    closed_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    days_since_closed: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "number": number,
        "lane": lane,
        "reason": reason,
        "source": source,
        "metric": metric,
        "op": op,
        "threshold": threshold,
        "value": value,
        "closed_at": closed_at,
        "updated_at": updated_at,
        "days_since_closed": days_since_closed,
    }


def classify_issue(
    issue: Dict[str, Any],
    *,
    now: datetime,
    data_dir: Path,
    evaluators: Optional[Dict[str, Dict[str, Callable]]] = None,
) -> Dict[str, Any]:
    """1 issue（gh `--json number,body,closedAt,updatedAt` 形）を3レーンに分類する。

    Returns: `_verdict` 形の dict。lane は "met" / "observer_missing" /
    "archive_candidate" / None（判定不能でも未成立でもなく単に「まだ何も言えない」）。
    """
    evals = evaluators if evaluators is not None else EVALUATORS
    number = issue.get("number")
    body = issue.get("body") or ""
    closed_at_raw = issue.get("closedAt")
    updated_at_raw = issue.get("updatedAt")
    closed_dt = _parse_iso(closed_at_raw)
    updated_dt = _parse_iso(updated_at_raw)
    days_since_closed = (now - closed_dt).days if closed_dt else None

    block = extract_reopen_when(body)
    if block is None:
        return _verdict(
            number,
            lane="observer_missing",
            reason=(
                f"`{REOPEN_HEADING}` の reopen-when ブロックが無い、または"
                "必須キー(source/metric/op/threshold)が欠落しています"
            ),
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )

    source = block["source"]
    metric = block["metric"]
    op = block["op"]
    try:
        threshold = float(block["threshold"])
    except (TypeError, ValueError):
        return _verdict(
            number,
            lane="observer_missing",
            reason=f"threshold が数値でない: {block['threshold']!r}",
            source=source,
            metric=metric,
            op=op,
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )

    metric_fn = evals.get(source, {}).get(metric)
    if metric_fn is None:
        return _verdict(
            number,
            lane="observer_missing",
            reason=(
                f"source='{source}' metric='{metric}' に対応する observer が未実装"
                f"（対応済み: {known_sources_summary(evals)}）"
            ),
            source=source,
            metric=metric,
            op=op,
            threshold=threshold,
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )

    value = metric_fn(data_dir, block)

    if value is not None and _OPS[op](value, threshold):
        return _verdict(
            number,
            lane="met",
            reason=f"{source}.{metric} = {value:g} {op} {threshold:g} を満たしました",
            source=source,
            metric=metric,
            op=op,
            threshold=threshold,
            value=value,
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )

    if (
        days_since_closed is not None
        and days_since_closed >= ARCHIVE_AGE_DAYS
        and not _edited_after_close(closed_dt, updated_dt)
    ):
        return _verdict(
            number,
            lane="archive_candidate",
            reason=(
                f"凍結から{days_since_closed}日経過・再開条件未成立・本文未更新"
                "（archive 候補・自動 close はしません）"
            ),
            source=source,
            metric=metric,
            op=op,
            threshold=threshold,
            value=value,
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )

    reason = (
        "再開条件は未成立です" if value is not None else "評価値を取得できませんでした（未計測）"
    )
    return _verdict(
        number,
        lane=None,
        reason=reason,
        source=source,
        metric=metric,
        op=op,
        threshold=threshold,
        value=value,
        closed_at=closed_at_raw,
        updated_at=updated_at_raw,
        days_since_closed=days_since_closed,
    )


def _default_data_dir() -> Path:
    import os

    import rl_common  # 遅延 import（hook/tool 文脈の patch 追従）

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    return Path(rl_common.resolve_data_dir(env))


def build_verdicts(
    issues: List[Any],
    *,
    now: Optional[datetime] = None,
    data_dir: Optional[Path] = None,
    evaluators: Optional[Dict[str, Dict[str, Callable]]] = None,
) -> Dict[str, Any]:
    """`gh issue list --json number,body,closedAt,updatedAt` の出力を全件分類する。

    非 dict の issue（gh の想定外形状）は静かに skip する（取りこぼしを警告したい場合は
    呼び出し側で len(issues) と len(verdicts) を比較する）。
    """
    now = now or datetime.now(timezone.utc)
    resolved_data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()
    verdicts: List[Dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        verdicts.append(
            classify_issue(
                issue, now=now, data_dir=resolved_data_dir, evaluators=evaluators
            )
        )
    return {"generated_at": now.isoformat(), "verdicts": verdicts}
