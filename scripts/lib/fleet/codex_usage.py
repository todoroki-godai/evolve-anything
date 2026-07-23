"""codex CLI (`~/.codex/state_5.sqlite`) の利用状況を read-only 集計する（#245）。

CC 側 `token_usage_store` とは別枠（codex 側は累積 `tokens_used`・per-turn 粒度と
単位が異なる）。**合算はしない**。3 つの degrade ケースを必ず fail-open で扱い、
fleet 本体（status/tokens）の既存表示を壊さない:

- DB 不在（codex 未導入）→ 無音 skip
- open/query 失敗（ロック中等）→ 呼び出し側で警告 1 行・処理継続
- スキーマ相違（threads テーブル/列が無い）→ 無音 skip
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

_DEFAULT_CODEX_STATE_DB = Path.home() / ".codex" / "state_5.sqlite"

CODEX_STATUS_OK = "ok"
CODEX_STATUS_MISSING = "missing"  # ① DB 不在
CODEX_STATUS_LOCKED = "locked"  # ② open/query 失敗（ロック等）
CODEX_STATUS_SCHEMA_MISMATCH = "schema_mismatch"  # ③ threads テーブル/列相違

_SCHEMA_MISMATCH_MARKERS = ("no such table", "no such column")


@dataclass
class CodexUsageResult:
    """codex 利用状況の集計結果。

    ``by_project``: ``{pj_slug: {"sessions": int, "tokens_used": int, "last_used": str|None}}``
    """

    by_project: dict[str, dict] = field(default_factory=dict)
    status: str = CODEX_STATUS_OK
    error: str | None = None


def collect_codex_usage(
    db_path: Path | None = None,
    *,
    window_days: int = 30,
    now: datetime | None = None,
) -> CodexUsageResult:
    """`threads` テーブルを read-only で読み PJ (cwd) 別に集計する。

    Args:
        db_path: 明示指定時はこのファイルのみ読む（テスト注入用）。未指定時は
            ``~/.codex/state_5.sqlite``。
        window_days: `updated_at`（最終更新）が何日以内のスレッドを対象にするか。
        now: 基準時刻（テスト注入用）。

    書込は一切行わない（``file:...?mode=ro`` URI で open）。例外は握り潰し、
    ``CodexUsageResult.status`` で呼び出し側に degrade 理由を伝える。予期しない
    例外（行の型不正・環境差異等）で fleet CLI 全体を落とさないよう、集計本体を
    broad except で包み ``CODEX_STATUS_LOCKED`` に fail-open する（保険の二段防御、
    #245 レビュー指摘）。
    """
    db_path = db_path or _DEFAULT_CODEX_STATE_DB
    if not db_path.is_file():
        return CodexUsageResult(status=CODEX_STATUS_MISSING)

    try:
        return _collect_codex_usage_impl(db_path, window_days=window_days, now=now)
    except Exception as e:  # noqa: BLE001 - advisory 経路: 未知の例外で fleet 全体を落とさない保険
        return CodexUsageResult(status=CODEX_STATUS_LOCKED, error=str(e))


def _collect_codex_usage_impl(
    db_path: Path,
    *,
    window_days: int,
    now: datetime | None,
) -> CodexUsageResult:
    now = now or datetime.now(timezone.utc)
    cutoff_ms = int((now - timedelta(days=window_days)).timestamp() * 1000)

    # パスに `?`/`#` 等 URI 予約文字が含まれると mode=ro クエリの解釈が壊れるため
    # quote() でエンコードしてから file: URI を組み立てる（#245 レビュー指摘）。
    uri = f"file:{quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)

    try:
        try:
            cur = conn.execute(
                "SELECT cwd, tokens_used, COALESCE(updated_at_ms, updated_at * 1000) AS ts_ms "
                "FROM threads WHERE COALESCE(updated_at_ms, updated_at * 1000) >= ?",
                (cutoff_ms,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if any(marker in msg for marker in _SCHEMA_MISMATCH_MARKERS):
                return CodexUsageResult(status=CODEX_STATUS_SCHEMA_MISMATCH, error=str(e))
            raise  # 呼び出し元 collect_codex_usage の broad except で LOCKED に fail-open
    finally:
        conn.close()

    try:
        from audit.outcome_metrics import _normalize_pj
    except ImportError:  # pragma: no cover - パス未解決時のフォールバック
        def _normalize_pj(v):
            return Path(str(v)).name if v else None

    by_project: dict[str, dict] = {}
    last_ms: dict[str, int] = {}
    for cwd, tokens_used, ts_ms in rows:
        # SQLite は INTEGER 宣言列にも任意型を保存できる（動的型付け）ため、
        # 1 行の型不正（tokens_used が文字列・ts_ms が巨大値等）が集計全体を
        # 落とさないよう行単位で防御する（#245 レビュー指摘）。値の変換を先に
        # 全て済ませてから state を更新する（例外発生時に部分更新を残さない）。
        try:
            if not cwd:
                continue
            slug = _normalize_pj(cwd)
            if not slug:
                continue
            session_tokens = int(tokens_used or 0)
            ts = int(ts_ms or 0)
            last_used_str = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat() if ts else None
            )
        except Exception:  # noqa: BLE001 - 型不正行のみ skip、他行の集計は継続
            continue

        entry = by_project.setdefault(slug, {"sessions": 0, "tokens_used": 0, "last_used": None})
        entry["sessions"] += 1
        entry["tokens_used"] += session_tokens
        if ts > last_ms.get(slug, 0):
            last_ms[slug] = ts
            entry["last_used"] = last_used_str

    return CodexUsageResult(by_project=by_project)
