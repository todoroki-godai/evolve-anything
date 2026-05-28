"""memory_trace — MemTrace 手法に倣った episodic memory 検索エラーの帰属診断。

3エラー類型:
  misretrieval  : query_relevant 返却スコアが score_threshold 未満なのに上位返却
  context_drift : memory_temporal の staleness（decay_days 超過）
  corruption    : 検索直後 post_retrieval_window_sec 以内に correction が発生

公開関数:
  attribute_errors(events, temporals, corrections, ...) -> list[dict]
  build_memory_trace_section(errors) -> list[str]

決定論的実装。LLM・外部 oracle 不使用。
DuckDB 未インストール時は空返し（guard パターンは episodic_store に準拠）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import duckdb as _duckdb  # noqa: F401 — 存在確認のみ
    HAS_DUCKDB = True
except ImportError:
    HAS_DUCKDB = False

# ─── デフォルト閾値 ────────────────────────────────────────────────────────────

DEFAULT_SCORE_THRESHOLD: float = 0.3
"""misretrieval 判定スコア閾値。score < この値 → misretrieval。"""

DEFAULT_STALENESS_DAYS: int = 30
"""context_drift 判定の陳腐化日数閾値。"""

DEFAULT_POST_RETRIEVAL_WINDOW_SEC: int = 300
"""corruption 判定の検索後ウィンドウ（秒）。"""


# ─── 公開 API ──────────────────────────────────────────────────────────────────


def attribute_errors(
    events: list[dict[str, Any]],
    temporals: dict[str, dict[str, Any]],
    corrections: list[dict[str, Any]],
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    staleness_days: int = DEFAULT_STALENESS_DAYS,
    post_retrieval_window_sec: int = DEFAULT_POST_RETRIEVAL_WINDOW_SEC,
) -> list[dict[str, Any]]:
    """episodic memory 検索エラーを類型化し発生源 event_id に帰属させる。

    Args:
        events: ``query_relevant`` の返り値形式の dict リスト。
            各 dict は ``{id, content, correction_type, timestamp, days_ago, score}``。
        temporals: ``{event_id: parse_memory_temporal(...) の返り値}`` の辞書。
            temporal frontmatter を持たないイベントはキーなしで OK。
        corrections: corrections.jsonl の各行 dict のリスト。
            各 dict は少なくとも ``{timestamp}`` を持つ（``session_id`` は任意）。
        score_threshold: misretrieval 判定スコア閾値（デフォルト 0.3）。
            ``event.score < score_threshold`` で misretrieval とみなす。
        staleness_days: context_drift 判定の陳腐化日数閾値（デフォルト 30）。
            ``decay_days`` が未設定の temporal は判定をスキップする。
        post_retrieval_window_sec: corruption 判定の検索後ウィンドウ（秒）。

    Returns:
        ``[{event_id, error_type, signal, detail}, ...]`` の list。
        エラーなし・events 空・HAS_DUCKDB=False のときは空リスト。
    """
    if not HAS_DUCKDB:
        return []
    if not events:
        return []

    # correction の timestamp をパース済みリストに変換（O(M) 一回）
    parsed_corrections = _parse_correction_timestamps(corrections)

    errors: list[dict[str, Any]] = []

    for event in events:
        event_id = event.get("id", "")
        score = event.get("score", 0.0)
        raw_ts = event.get("timestamp", "")

        # ── misretrieval ──────────────────────────────────────────────────────
        if score < score_threshold:
            errors.append({
                "event_id": event_id,
                "error_type": "misretrieval",
                "signal": "low_score",
                "detail": {"score": score, "threshold": score_threshold},
            })

        # ── context_drift ─────────────────────────────────────────────────────
        temporal = temporals.get(event_id)
        if temporal is not None:
            drift_detail = _check_context_drift(temporal, staleness_days)
            if drift_detail is not None:
                errors.append({
                    "event_id": event_id,
                    "error_type": "context_drift",
                    "signal": "stale_temporal",
                    "detail": drift_detail,
                })

        # ── corruption ────────────────────────────────────────────────────────
        event_dt = _parse_iso(raw_ts)
        if event_dt is not None and parsed_corrections:
            corr_detail = _check_corruption(
                event_id, event_dt, parsed_corrections, post_retrieval_window_sec
            )
            if corr_detail is not None:
                errors.append({
                    "event_id": event_id,
                    "error_type": "corruption",
                    "signal": "post_retrieval_correction",
                    "detail": corr_detail,
                })

    return errors


def build_memory_trace_section(errors: list[dict[str, Any]]) -> list[str]:
    """``attribute_errors`` の結果を audit レポート形式の行リストに変換する。

    既存の ``build_memory_health_section`` と同じ行リスト形式を採用。
    エラーがなければ空リストを返す。

    Args:
        errors: ``attribute_errors`` の返り値。

    Returns:
        Markdown 行リスト。問題なければ空リスト。
    """
    if not errors:
        return []

    # error_type 別にグループ化
    groups: dict[str, list[dict[str, Any]]] = {}
    for err in errors:
        etype = err.get("error_type", "unknown")
        groups.setdefault(etype, []).append(err)

    lines = ["## Memory Trace Diagnostics", ""]

    _TYPE_LABELS = {
        "misretrieval": "misretrieval — Low Score",
        "context_drift": "context_drift — Stale Temporal",
        "corruption": "corruption — Post-Retrieval Correction",
    }

    for etype in ("misretrieval", "context_drift", "corruption"):
        entries = groups.get(etype)
        if not entries:
            continue
        label = _TYPE_LABELS.get(etype, etype)
        lines.append(f"### {label} ({len(entries)})")
        for err in entries:
            eid = err.get("event_id", "?")
            detail = err.get("detail", {})
            detail_str = ", ".join(f"{k}={v}" for k, v in detail.items()) if detail else ""
            if detail_str:
                lines.append(f"- event_id={eid} [{detail_str}]")
            else:
                lines.append(f"- event_id={eid}")
        lines.append("")

    # その他の未知 error_type
    for etype, entries in groups.items():
        if etype in _TYPE_LABELS:
            continue
        lines.append(f"### {etype} ({len(entries)})")
        for err in entries:
            eid = err.get("event_id", "?")
            lines.append(f"- event_id={eid}")
        lines.append("")

    return lines


# ─── 内部ヘルパー ──────────────────────────────────────────────────────────────


def _parse_iso(ts: str) -> datetime | None:
    """ISO 8601 文字列を UTC datetime に変換する。パース失敗時は None。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_correction_timestamps(
    corrections: list[dict[str, Any]],
) -> list[tuple[datetime, dict[str, Any]]]:
    """corrections リストから timestamp をパースし、(datetime, original_dict) のリストを返す。

    timestamp が欠損・パース不能な行はスキップする。
    """
    result = []
    for corr in corrections:
        ts_str = corr.get("timestamp", "")
        dt = _parse_iso(ts_str)
        if dt is not None:
            result.append((dt, corr))
    return result


def _check_context_drift(
    temporal: dict[str, Any],
    staleness_days: int,
) -> dict[str, Any] | None:
    """temporal dict が staleness_days を超過しているか判定する。

    超過していれば detail dict を返す。超過でなければ None。
    ``decay_days`` が None または 0 以下はスキップ（判定不能 → None）。
    """
    decay_days = temporal.get("decay_days")
    # is_stale と同じ判定ロジック: decay_days なしは False
    if not decay_days or not isinstance(decay_days, int):
        return None

    valid_from_str = temporal.get("valid_from")
    if not valid_from_str:
        return None

    event_dt = _parse_iso(str(valid_from_str))
    if event_dt is None:
        return None

    age_days = (datetime.now(timezone.utc) - event_dt).days
    if age_days > staleness_days:
        return {
            "age_days": age_days,
            "decay_days": decay_days,
            "staleness_threshold": staleness_days,
            "valid_from": valid_from_str,
        }
    return None


def _check_corruption(
    event_id: str,
    event_dt: datetime,
    parsed_corrections: list[tuple[datetime, dict[str, Any]]],
    window_sec: int,
) -> dict[str, Any] | None:
    """検索直後 window_sec 以内に correction があるか判定する。

    該当する correction が見つかれば detail dict を返す。なければ None。
    """
    from datetime import timedelta

    window_end = event_dt + timedelta(seconds=window_sec)
    for corr_dt, corr in parsed_corrections:
        if event_dt <= corr_dt <= window_end:
            return {
                "retrieval_ts": event_dt.isoformat(),
                "correction_ts": corr_dt.isoformat(),
                "elapsed_sec": int((corr_dt - event_dt).total_seconds()),
                "window_sec": window_sec,
                "correction_session_id": corr.get("session_id", ""),
            }
    return None
