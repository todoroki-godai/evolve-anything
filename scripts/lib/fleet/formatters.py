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
from .codex_usage import CODEX_STATUS_LOCKED, CODEX_STATUS_OK

if TYPE_CHECKING:
    from .codex_usage import CodexUsageResult

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


def _append_skipped_dead(lines: list, result: dict) -> None:
    """dead な tracked PJ（実 dir 不在で queue から外したもの）を footer 下に透明化表示する。

    silent truncation 禁止: queue から消えた理由をユーザーに見せる。slug をカンマ区切りで
    最大 5 個まで、超過は ``…`` で省略する（#79）。``skipped_dead`` が空なら何もしない。
    """
    skipped = result.get("skipped_dead") or []
    if not skipped:
        return
    slugs = [str(d.get("pj_slug", "")) for d in skipped]
    shown = slugs[:5]
    suffix = ", …" if len(slugs) > 5 else ""
    lines.append(f"（skipped {len(slugs)} dead: {', '.join(shown)}{suffix}）")


def _append_untracked(lines: list, result: dict) -> None:
    """material（weak/corr）を持つが tracked 母集団に居ない PJ を advisory 表示する（#86 O2）。

    queue 母集団は fleet-config.json の tracked_projects 限定だが material 母集団の方が広い。
    その差集合（untracked だが学習素材あり）を、待ちにも skipped_dead にも出ず沈黙させない
    ため 1 行で surface する。``untracked_with_material`` が空なら何もしない。slug と
    material_count を上位 5 件まで、超過は ``…`` で省略する。
    """
    untracked = result.get("untracked_with_material") or []
    if not untracked:
        return
    shown = untracked[:5]
    parts = [
        f"{u.get('pj_slug', '')} (material {u.get('material_count', 0)})" for u in shown
    ]
    suffix = ", …" if len(untracked) > 5 else ""
    lines.append(
        f"（未追跡だが学習素材あり: {', '.join(parts)}{suffix}"
        f" — tracked 追加を検討: evolve-fleet discover）"
    )


def _append_skipped_phantom(lines: list, result: dict) -> None:
    """閾値以上 material を持つが実 dir に解決できず除外した untracked slug を透明化表示する（#88）。

    ``skipped_dead`` / ``untracked_with_material`` は footer に出るのに、phantom（temp slug 等で
    実 dir 不在）だけが完全沈黙する非対称を是正する。slug と material_count を上位 5 件まで、
    超過は ``…`` で省略する。``skipped_phantom`` が空なら何もしない（temp slug が無いのが通常）。
    """
    phantom = result.get("skipped_phantom") or []
    if not phantom:
        return
    shown = phantom[:5]
    parts = [
        f"{p.get('pj_slug', '')} (material {p.get('material_count', 0)})" for p in shown
    ]
    suffix = ", …" if len(phantom) > 5 else ""
    lines.append(
        f"（skipped {len(phantom)} phantom: {', '.join(parts)}{suffix}"
        f" — 実 dir 未解決ゆえ除外）"
    )


def _append_unattributed(lines: list, result: dict) -> None:
    """PJ 帰属不能な corrections（project_path 欠落）を件数 + source 内訳で透明化表示する（#91）。

    ``_correction_slug`` が空に落ちるレコードはどの PJ の material にも数えられず queue から
    構造的に不可視。``skipped_dead`` / ``skipped_phantom`` と同じ「無音で落とさない」方針で、
    今後 hook 不具合等で欠落が増えたときに気づけるよう source 内訳まで出す。total 0 / キー
    欠落なら何もしない。source は件数降順（同数は名前昇順）で並べる。
    """
    ua = result.get("unattributed_corrections") or {}
    total = ua.get("total", 0)
    if not total:
        return
    by_source = ua.get("by_source") or {}
    parts = [
        f"{src}={cnt}"
        for src, cnt in sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    breakdown = f" — {', '.join(parts)}" if parts else ""
    lines.append(
        f"（PJ 未帰属 corrections: {total} 件{breakdown}"
        f" — project_path 欠落で queue 不可視）"
    )


def _append_coldstart_notice(lines: list, result: dict) -> None:
    """純 cold-start（全待ち PJ が未 evolve）時のみ material の意味を警告する（A）。

    cold-start では ``new_corrections`` が「前回 evolve 以降の増分」でなく **全履歴 backlog**
    の全件計上になる（``new_corrections_by_pj`` の ``last_evolve_at=None`` 分岐）。よって
    ``material_count`` は velocity（最近の勢い）でなく累積量を表し、ランキングも累積順になる。
    初回 ``/evolve`` 適用（drain）後は増分のみになり数値が大きく下がる。この非互換を知らずに
    上位 PJ を選ぶと stale な backlog を掴むため、純 cold-start 時だけ surface する。一部でも
    drained なら混在ノイズになるので出さない（純 cold-start は自己消滅する一過性状態）。
    """
    queue = result.get("queue") or []
    if not queue:
        return
    if not all(item.get("last_evolve_at") is None for item in queue):
        return
    lines.append(
        "（初回表示: corr は全履歴を計上＝累積量順で velocity ではありません。"
        "evolve 適用後は増分のみになり数値が下がります）"
    )


def _append_bootstrap_consumed(lines: list, result: dict) -> None:
    """bootstrap で消化済み（破棄/TTL 任せと判断済み）として待ちから除外した weak を透明化（#94）。

    silent truncation 禁止: 人間が bootstrap で判断済みの素材を queue から落とした事実を脚注に
    出す。除外しないと TTL（45日）まで material を膨らませ誤読を招くが、黙って除外すると「なぜ
    その PJ が待ちから消えたか」が分からなくなる。``bootstrap_consumed`` が空なら何も出さない。
    """
    bc = result.get("bootstrap_consumed") or []
    if not bc:
        return
    parts = [f"{e.get('pj_slug')} {e.get('consumed')}件" for e in bc]
    lines.append(
        f"（bootstrap 消化済みを待ちから除外: {', '.join(parts)}"
        f" — 破棄/TTL 任せと判断済み・TTL 失効まで再カウントしません）"
    )


def _append_weak_content_poor(lines: list, result: dict) -> None:
    """content-poor channel（昇格不能）で material から除外した weak を透明化表示する（#113）。

    silent truncation 禁止: esc_interrupt / manual_edit_after_ai 等の content-poor channel は
    y/n 確認から除外され promote しても signal_text が空で昇格不能な死荷重ゆえ material_count に
    載せないが、黙って落とすと「なぜ WEAK が生検出より少ないか」が不明になる。除外した PJ と
    件数を脚注に出す。``weak_content_poor`` が空なら何も出さない。
    """
    wcp = result.get("weak_content_poor") or []
    if not wcp:
        return
    parts = [f"{e.get('pj_slug')} {e.get('content_poor')}件" for e in wcp]
    lines.append(
        f"（content-poor channel を material から除外: {', '.join(parts)}"
        f" — esc/手編集等は昇格手段が無く material に数えません）"
    )


def _append_weak_semantics(lines: list, result: dict) -> None:
    """WEAK 列が「content-rich 未処理のみ」である意味を明示し、生検出数との乖離の誤読を防ぐ（②/#113）。

    WEAK は ``weak_unprocessed``（promoted=False・未 TTL 失効・content-rich channel）から
    bootstrap 消化分を除いた実残数で、検出された weak の生総数とは一致しない（promoted 昇格済み・
    TTL 失効・bootstrap 破棄・content-poor channel 除外が差分）。生数しか知らないと「なぜ table の
    数が生検出より少ないのか」と誤読する（実機 dogfood で amamo 生 64 → WEAK 16 のギャップが不可解
    に見えた）。待ち PJ があるとき列の意味を脚注で明示する。集計でなく定数注記（footer ノイズと
    算出コストを抑える）。
    """
    if not result.get("queue"):
        return
    lines.append(
        "（WEAK は content-rich 未処理のみ＝promoted 昇格済み・TTL 失効・bootstrap 消化済み・"
        "content-poor channel（昇格不能）を除いた実残数。検出された weak の生総数とは一致しません）"
    )


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
        lines.append(f"（0 projects waiting / {tracked} tracked (config)）")
        _append_bootstrap_consumed(lines, result)
        _append_weak_content_poor(lines, result)
        _append_skipped_dead(lines, result)
        _append_untracked(lines, result)
        _append_skipped_phantom(lines, result)
        _append_unattributed(lines, result)
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
    lines.append(f"（{len(queue)} projects waiting / {tracked} tracked (config)）")
    _append_coldstart_notice(lines, result)
    _append_weak_semantics(lines, result)
    _append_bootstrap_consumed(lines, result)
    _append_weak_content_poor(lines, result)
    _append_skipped_dead(lines, result)
    _append_untracked(lines, result)
    _append_skipped_phantom(lines, result)
    _append_unattributed(lines, result)
    return "\n".join(lines) + "\n"


# --- codex CLI 利用状況 advisory セクション（#245） --------------------------


def format_codex_usage_section(result: "CodexUsageResult", now: datetime | None = None) -> str:
    """codex CLI (`~/.codex/state_5.sqlite`) 利用状況を advisory 表示する（#245）。

    - status=missing（codex 未導入）/ schema_mismatch（スキーマ相違）→ 空文字列（無音）
    - status=ok かつ by_project 空（sessions 0）→ 空文字列（無音、既存表示を壊さない）
    - status=locked（open/query 失敗）→ 警告 1 行のみ（fail-open）
    - status=ok かつ by_project あり → PJ 別サマリ + CC 側 token_usage と合算していない旨の注記
    """
    if result.status == CODEX_STATUS_LOCKED:
        return f"[fleet] codex 利用状況の取得に失敗しました（{result.error}）— スキップします\n"
    if result.status != CODEX_STATUS_OK or not result.by_project:
        return ""

    now = now or datetime.now(timezone.utc)
    lines = ["", "## Codex CLI 利用状況（直近30日）"]
    for slug, entry in sorted(
        result.by_project.items(), key=lambda kv: -kv[1].get("tokens_used", 0)
    ):
        last = entry.get("last_used")
        last_str = _format_relative(datetime.fromisoformat(last), now) if last else "—"
        lines.append(
            f"  {slug}\t{entry.get('sessions', 0)} sessions"
            f"\t{_format_short_int(entry.get('tokens_used', 0))} tokens"
            f"\tlast: {last_str}"
        )
    lines.append("（CC 側トークン集計とは合算していません — 累積値/単位が異なるため）")
    return "\n".join(lines) + "\n"
