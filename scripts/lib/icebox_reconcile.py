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

import math
import re
from datetime import datetime, timedelta, timezone
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

# source/metric に許す文字種（#352 B4）。issue 本文は untrusted 入力のため、audit や
# systemMessage の reason にそのまま埋め込んでよいのはこのパターンに適合する値だけに限る
# （制御文字・改行・空白・過長文字列は reopen-when ブロックごと無効化＝レーン2「観測器不在」
# の汎用理由へ合流させ、untrusted な生値を一切 echo しない）。
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# icebox 棚卸しは evolve-anything 自身の backlog に対してのみ動く（daily runner の
# ICEBOX_REPO と同じスコープ）。#352 B6: reopen-when ブロックの `pj_slug` extra キーによる
# 上書きは廃止（issue 本文は untrusted 入力のため、これを許すと他 PJ のデータを覗く経路に
# なる＝クロス PJ 情報開示。systemMessage/audit でユーザーに結果値が出るため影響が大きい）。
# 評価器は常にこの定数を使う。
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


def _valid_blocks(body: Optional[str]) -> List[Dict[str, Any]]:
    """issue 本文の `## 再開条件` 配下から有効な reopen-when ブロックを出現順に**すべて**返す。

    `extract_reopen_when`（先頭優先の後方互換 API）と `classify_issue`（ambiguous 判定に
    件数が要る・#352 P4）が共有する内部実装。以下はいずれもブロックとして採用しない
    （区別せずレーン2「観測器不在」に合流させるため）:
    - body が空 / `## 再開条件` 見出しが無い
    - 見出し配下に fenced YAML が無い / パース不能
    - トップレベルに `reopen-when` キーが無い、または値が dict でない
    - source/metric/op/threshold のいずれか欠落
    - source/metric/op が str でない（#352 B1: YAML はどの値も list/dict/数値になりうる。
      非 str のまま `dict` の key 照合に使うと unhashable な list/dict で TypeError が飛ぶ）
    - source/metric が `_TOKEN_RE` に適合しない（制御文字・改行・空白・65文字以上・#352 B4:
      issue 本文は untrusted 入力なので、reason に埋め込んでよい形へブロック単位で絞る）
    - op が未知の演算子

    自由文併記や `agent_type` 等の追加キーはそのまま保持して返す（`pj_slug` は #352 B6 で
    評価器側が無視するため、ここで拒否する必要はない）。
    """
    if not isinstance(body, str) or not body:
        return []
    section = _section_after_heading(body, REOPEN_HEADING)
    if section is None:
        return []
    out: List[Dict[str, Any]] = []
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
        if not all(isinstance(block.get(k), str) for k in ("source", "metric", "op")):
            continue
        if not (_TOKEN_RE.fullmatch(block["source"]) and _TOKEN_RE.fullmatch(block["metric"])):
            continue
        if block["op"] not in _OPS:
            continue
        out.append(block)
    return out


def extract_reopen_when(body: Optional[str]) -> Optional[Dict[str, Any]]:
    """issue 本文の `## 再開条件` 配下から reopen-when ブロック（dict）を抽出する。

    複数の有効なブロックが見つかった場合は先頭を返す（ambiguous 判定は `classify_issue`
    が `_valid_blocks` を直接使って行う・#352 P4）。無効化条件は `_valid_blocks` 参照。
    """
    blocks = _valid_blocks(body)
    return blocks[0] if blocks else None


# ─────────────────────────────────────────────────────────────────
# evaluator registry（最小セット・#352）
# ─────────────────────────────────────────────────────────────────
def _eval_weak_signals_unprocessed_count(
    data_dir: Path, extra: Dict[str, Any]
) -> Optional[float]:
    """SELF_PJ_SLUG（当 PJ 固定・#352 B6）の未昇格・非失効 weak_signal 件数。

    #352 B9: `weak_signals.store.read_signals` に明示 path を渡す従来実装は canonical
    dir しか読まず、DATA_DIR 分裂（既知 pitfall_datadir_hook_tool_split）時に
    legacy/plugins-data ストアを取りこぼして未処理件数を過少カウントし、`<=` 条件を
    誤って成立させうる。weak_signals の他 reader（daily_review 等）が使う union read
    （`rl_common.iter_read_data_dirs`）と同じ流儀に合わせ、`data_dir` を canonical 起点に
    union 解決してから各 dir を読む（`data_dir` がテスト isolation の tmp_path でも、
    legacy 候補は存在しなければ自動的に候補から外れるので既存の単一 dir テストは無改修で通る）。

    #405 round4 [Must]3 是正: この値は `classify_issue` の `<=` 閾値判定に直接使われる
    （`lane="met"` の成立条件）ため、判断済み（bootstrap で「破棄」「TTL 任せ」と人間が
    選んだ）weak_signal を未処理として数えると、判断済み項目だけで再開条件が誤って成立し
    うる。promoted / TTL 失効に加え bootstrap 消化除外（#94）を、全 actionable reader の
    単一 predicate（`correction_semantic.promote.filter_actionable`）経由で適用する。
    reviewed（既読）は他 reader と違いこの評価器には既読ストアの axis が無かったため
    従来挙動を保つ（exclude_reviewed=False・スコープ外の変更をしない）。marker 探索は
    `data_dir`（テスト isolation 起点）に anchor する。
    """
    try:
        from weak_signals.store import STORE_NAME, _read_one
    except ImportError:
        return None
    try:
        from store_read_union import pj_slug_match as _pj_slug_match
    except ImportError:
        def _pj_slug_match(a, b):  # type: ignore
            return a == b
    try:
        from correction_semantic.promote import filter_actionable
    except ImportError:
        return None

    try:
        import rl_common
        dirs = rl_common.iter_read_data_dirs(canonical=Path(data_dir))
    except Exception:
        dirs = [Path(data_dir)]

    records: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for d in dirs:
        for r in _read_one(Path(d) / STORE_NAME):
            k = r.get("signal_key")
            if k:
                if k in seen_keys:
                    continue
                seen_keys.add(k)
            if not _pj_slug_match(r.get("pj_slug"), SELF_PJ_SLUG):
                continue
            records.append(r)

    actionable = filter_actionable(
        records, SELF_PJ_SLUG, exclude_reviewed=False, marker_base=Path(data_dir)
    )
    return float(len(actionable))


def _eval_subagent_traces_first_try_success_rate(
    data_dir: Path, extra: Dict[str, Any]
) -> Optional[float]:
    """SELF_PJ_SLUG（当 PJ 固定・#352 B6）の agent_type 別内部一発成功率。

    `agent_type` 指定が無ければ n 加重平均。
    """
    try:
        from subagent_traces.query import per_agent_type_summary
    except ImportError:
        return None

    summaries = per_agent_type_summary(SELF_PJ_SLUG, data_dir=Path(data_dir))
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
    """closedAt 以降に本文が実質編集されたとみなせるか（UPDATE_TOLERANCE_DAYS 超の差）。

    #352 P3: `timedelta.days` は端数を切り捨てる（25時間差でも `.days == 1`）ため、
    旧実装の `.days > UPDATE_TOLERANCE_DAYS`（=1）は「24時間超」でなく実質「48時間超」
    でしか True にならなかった。timedelta 同士を直接比較し docstring 通りの
    「UPDATE_TOLERANCE_DAYS 超」に一致させる。
    """
    if not closed_dt or not updated_dt:
        return False
    return (updated_dt - closed_dt) > timedelta(days=UPDATE_TOLERANCE_DAYS)


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

    #352 の一発監査（レビュー指摘 B1-B4/P4）を踏まえ、issue 本文は untrusted 入力である
    前提で全経路を固める:
    - ブロックが複数見つかった場合は一意に定まらないので observer_missing（P4）
    - evaluator 呼び出しは例外を投げても落ちない（B1）
    - threshold は非有限（NaN/inf）なら成立させない（B2）。数値変換に失敗した理由は
      issue 本文由来の生値を echo しない固定文言にする（B4）
    - 評価値が実測できていない（None）のに凍結年齢だけで archive 候補にしない（B3）
    """
    evals = evaluators if evaluators is not None else EVALUATORS
    number = issue.get("number")
    body = issue.get("body") or ""
    closed_at_raw = issue.get("closedAt")
    updated_at_raw = issue.get("updatedAt")
    closed_dt = _parse_iso(closed_at_raw)
    updated_dt = _parse_iso(updated_at_raw)
    days_since_closed = (now - closed_dt).days if closed_dt else None

    blocks = _valid_blocks(body)
    if len(blocks) > 1:
        return _verdict(
            number,
            lane="observer_missing",
            reason=(
                f"`{REOPEN_HEADING}` 配下に reopen-when ブロックが複数見つかりました"
                "（条件が一意に定まらないため判定できません。1つに統合してください）"
            ),
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )
    if not blocks:
        return _verdict(
            number,
            lane="observer_missing",
            reason=(
                f"`{REOPEN_HEADING}` の reopen-when ブロックが無い、または"
                "必須キー(source/metric/op/threshold)が欠落・不正な形式です"
            ),
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )
    block = blocks[0]

    source = block["source"]
    metric = block["metric"]
    op = block["op"]
    try:
        threshold = float(block["threshold"])
    except (TypeError, ValueError):
        return _verdict(
            number,
            lane="observer_missing",
            reason="threshold が数値ではありません（reopen-when ブロックを確認してください）",
            source=source,
            metric=metric,
            op=op,
            closed_at=closed_at_raw,
            updated_at=updated_at_raw,
            days_since_closed=days_since_closed,
        )
    if not math.isfinite(threshold):
        return _verdict(
            number,
            lane="observer_missing",
            reason="threshold が有限の数値ではありません（NaN/inf は指定できません）",
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

    try:
        value = metric_fn(data_dir, block)
    except Exception:
        value = None

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
        value is not None
        and days_since_closed is not None
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
