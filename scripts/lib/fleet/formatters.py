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

_TABLE_HEADERS = ["PJ", "STATUS", "SCORE", "LV", "PHASE", "LAST_AUDIT", "AUDIT", "ISSUES", "SUBAGENTS_30d", "TOKENS_30d", "CACHE_HIT", "REUSE"]


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


def _format_cell_cache_reuse(row: FleetRow) -> str:
    if row.cache_reuse_factor is None:
        return "--"
    return f"{row.cache_reuse_factor:.1f}x"


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
            _format_cell_cache_reuse(row),
        ])
    widths = [max(len(c) for c in col) for col in zip(*cells)]
    lines = []
    for row_cells in cells:
        parts = [row_cells[i].ljust(widths[i]) for i in range(len(widths))]
        lines.append("  ".join(parts).rstrip())
    return "\n".join(lines) + "\n"


def format_status_json(rows: list[FleetRow]) -> str:
    """fleet status 行を JSON で整形する（``--json`` 出力・#53）。

    `tokens` / `plugins` / `test-guard status` が既に持つ ``--json`` と一貫させ、
    複数 PJ の env_score / 導入状況を構造化データで提供する（HTML 化より優先＝
    主要消費者は Claude Code セッション内のアシスタント）。

    各行を ``asdict`` で dict 化し（``issues_summary`` などネストした dataclass も
    再帰展開）、JSON 非対応の ``latest_audit``（datetime）は ISO 8601 文字列へ、
    None はそのまま null にする。``default=str`` は将来 datetime 以外の非対応
    フィールドが増えても落ちないための保険。
    """
    import json as _json
    from dataclasses import asdict

    projects: list[dict] = []
    for row in rows:
        d = asdict(row)
        la = d.get("latest_audit")
        d["latest_audit"] = la.isoformat() if la is not None else None
        projects.append(d)
    return _json.dumps({"projects": projects}, ensure_ascii=False, indent=2, default=str) + "\n"


# --- queue サブコマンド（#79）表示 -------------------------------------------

_QUEUE_HEADERS = ["PROJECT", "MATERIAL", "WEAK", "CORR", "LAST_EVOLVE", "REASON"]


def format_queue_table(result: dict) -> str:
    """fleet queue の result dict を `PROJECT/MATERIAL/WEAK/CORR/LAST_EVOLVE/REASON`
    テーブルに整形する（末尾に `（N projects waiting / M tracked）` を添える）。

    待ち 0 件でも tracked 総数は表示する（沈黙させない）。LAST_EVOLVE は ISO 文字列の
    先頭 10 文字（日付）を出し、None は `never`（初回＝全件待ち）と表示する。
    """
    queue = result.get("queue", [])
    tracked = result.get("tracked_total", 0)

    lines: list[str] = []
    if not queue:
        lines.append(
            f"[fleet:queue] 待ち PJ はありません"
            f"（閾値 {result.get('threshold')} 以上の学習素材なし）。"
        )
        lines.append(f"（0 projects waiting / {tracked} tracked）")
        return "\n".join(lines) + "\n"

    rows: list[list[str]] = []
    for item in queue:
        last = item.get("last_evolve_at")
        last_str = (last[:10] if isinstance(last, str) and last else "never")
        rows.append([
            str(item.get("pj_slug", "")),
            str(item.get("material_count", 0)),
            str(item.get("weak_unprocessed", 0)),
            str(item.get("new_corrections", 0)),
            last_str,
            str(item.get("reason", "")),
        ])

    widths = [len(h) for h in _QUEUE_HEADERS]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))

    def _fmt_row(cells: list[str]) -> str:
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells))

    lines.append(_fmt_row(_QUEUE_HEADERS))
    lines.append(_fmt_row(["-" * w for w in widths]))
    for r in rows:
        lines.append(_fmt_row(r))
    lines.append("")
    lines.append(f"（{len(queue)} projects waiting / {tracked} tracked）")
    return "\n".join(lines) + "\n"
