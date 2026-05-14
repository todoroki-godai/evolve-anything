"""fleet status テーブル整形ロジック。

セル単位フォーマッタ + テーブル整形。FleetRow に依存するが副作用はなく、
fleet/__init__.py から re-export される（後方互換）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import FleetRow

from . import STATUS_ENABLED, STATUS_NOT_ENABLED

_TABLE_HEADERS = ["PJ", "STATUS", "SCORE", "LV", "PHASE", "LAST_AUDIT", "AUDIT", "ISSUES", "SUBAGENTS_30d", "TOKENS_30d", "CACHE_HIT"]


def _format_short_int(n: int) -> str:
    """1_234_567 → '1.2M', 12_345 → '12.3K'。負値は想定しない。"""
    if n is None:
        return "--"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def _format_cell_tokens(row: FleetRow) -> str:
    if row.tokens_30d is None:
        return "--"
    return _format_short_int(row.tokens_30d)


def _format_cell_cache_hit(row: FleetRow) -> str:
    if row.cache_hit_pct is None:
        return "--"
    return f"{row.cache_hit_pct:.0f}%"


def _format_relative(dt: datetime, now: datetime) -> str:
    """`1h ago` / `3d ago` のような短い相対時刻表記。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "future"
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days >= 1:
        return f"{days}d ago"
    if hours >= 1:
        return f"{hours}h ago"
    if minutes >= 1:
        return f"{minutes}m ago"
    return "just now"


def _format_cell_score(row: FleetRow) -> str:
    if row.status != STATUS_ENABLED:
        return "N/A"
    if row.env_score is None:
        return "—"
    return f"{row.env_score:.2f}"


def _format_cell_level(row: FleetRow) -> str:
    if row.growth_level is None:
        return "—"
    return f"Lv.{row.growth_level}"


def _format_cell_phase(row: FleetRow) -> str:
    return row.phase or "—"


def _format_cell_last_audit(row: FleetRow, now: datetime) -> str:
    if row.latest_audit is None:
        return "—"
    return _format_relative(row.latest_audit, now)


def _format_cell_audit(row: FleetRow) -> str:
    if row.status == STATUS_NOT_ENABLED:
        return "—"
    return row.audit_status


def _format_cell_issues(row: FleetRow) -> str:
    """ISSUES 列。旧 cache (issues_summary 欠落) → "—"、ある場合は合計を表示。

    内訳が必要なときは audit レポート / growth-state cache JSON を見るので
    ここでは 1 数字に集約して列幅を抑える。
    """
    if row.issues_summary is None:
        return "—"
    return str(row.issues_summary.total())


def _format_cell_subagents(row: FleetRow) -> str:
    return str(row.subagents_30d)


def format_status_table(rows: list[FleetRow], now: datetime | None = None) -> str:
    """fleet status 行を整列済みテキストテーブルに整形する。

    列: PJ / STATUS / SCORE / LV / PHASE / LAST_AUDIT / AUDIT
    列幅は各列の最大値に合わせ、各セルは左詰め（英数字のみ想定）。
    """
    now = now or datetime.now(timezone.utc)
    cells: list[list[str]] = [list(_TABLE_HEADERS)]
    for row in rows:
        cells.append([
            row.pj_name,
            row.status,
            _format_cell_score(row),
            _format_cell_level(row),
            _format_cell_phase(row),
            _format_cell_last_audit(row, now),
            _format_cell_audit(row),
            _format_cell_issues(row),
            _format_cell_subagents(row),
            _format_cell_tokens(row),
            _format_cell_cache_hit(row),
        ])
    widths = [max(len(c) for c in col) for col in zip(*cells)]
    lines = []
    for row_cells in cells:
        parts = [row_cells[i].ljust(widths[i]) for i in range(len(widths))]
        lines.append("  ".join(parts).rstrip())
    return "\n".join(lines) + "\n"
