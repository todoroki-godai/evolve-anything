"""#401: read-only weekly board notification."""
import datetime as _dt
import os
from pathlib import Path

from .model import NotificationItem


def _build_weekly_board_output(shared: "tuple | None" = None, *, now=None) -> "NotificationItem | None":
    """Read the daily snapshot only; health precedes the one-day display window (#401)."""
    def health(reason):
        reason = " ".join(str(reason).split())
        reason = reason if len(reason) <= 62 else reason[:61] + "…"
        text = f"戦果ボードの要約を読めません（{reason}）"
        return NotificationItem(label="weekly_board", tier=1, text=text, digest=text, commit=None)

    try:
        from . import collectors  # Shared resolver and optional dependencies stay patchable.
        data_dir, queue_data, file_state = shared if shared is not None else collectors._resolve_queue_data()
        if file_state == "corrupt":
            return None  # evolve_queue owns file corruption health.
        if data_dir is None:
            env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
            migration = collectors._data_dir_migration
            if not env or migration is None or not migration.is_cc_install_layout(Path(env)):
                return None  # Cannot establish CC layout: do not probe custom environments.
            import rl_common
            data_dir = rl_common.resolve_data_dir(env)
        if not data_dir or not (Path(data_dir) / "evolve-queue.json").exists():
            return None
        if not isinstance(queue_data, dict):
            return health("evolve-queue.json を読めません")
        if "weekly_board" not in queue_data:
            return None  # Old runner / not yet computed is not malformed.
        board = queue_data.get("weekly_board")
        if not isinstance(board, dict):
            return health("weekly_board の形式が不正です")
        if board.get("measured") is False:
            return health(board.get("reason") or "読取障害")
        if not isinstance(board.get("week_id"), str) or not isinstance(board.get("computed_on"), str):
            return health("week_id / computed_on がありません、または形式が不正です")
        now = now if now is not None else _dt.datetime.now().astimezone()
        if board["computed_on"] != now.date().isoformat():
            return None
        point = board["point_week"]
        rate = "データ蓄積中" if point is None else f"{point['rate']:.1%}（{point['week_id']}）"
        text = (f"戦果ボード: 実際に反映された改善（直近30日）{board['pillar2_count']}件／"
                f"指摘率 {rate}／戻せる採用 {board['pillar4_count']}件。"
                "柱2・4は本体／指摘率は全PJ")
        return NotificationItem(label="weekly_board", tier=1, text=text, digest=text, commit=None)
    except Exception as exc:
        return health(str(exc))


