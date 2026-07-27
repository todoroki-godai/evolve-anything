"""fleet.queue_verify — verify 待ちの read-time 導出 + queue 全体状態ラベル（Epic #267 Sprint 1）。

背景（#267 issue コメント2の実装順4）: queue の REASON は material 件数のみで、直前 run で
accept した提案がまだ効果検証されていない（verify 待ち）ことも、queue が空である理由が
「本当に素材が無い」のか「素材はあるが処理できていない」のかも表示上区別できなかった。

新しいストアは作らない（#267 freeze 方針）。verify 待ちは既存2レーンの accept 記録
（``advisory_decision_log`` / ``optimize_history_store``）から **read 時に純粋導出**する。

verify 待ちの定義:
  直近 evolve run で accept された提案のうち、まだ効果を検証していないもの。
  「直近 run」は両レーンの accept 記録のうち recorded_at/timestamp が最も新しいものが
  属する run_id（run_id を持たない旧 schema レコードは対象外＝黙って混ぜない）。
  exposure（前回 evolve 以降の sessions 数）が 0 なら「適用したがまだ実タスクが走っていない」
  ＝ awaiting_exposure、1 以上なら verifiable。

決定論・LLM 非依存。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

STATUS_NONE = "none"
STATUS_AWAITING_EXPOSURE = "awaiting_exposure"
STATUS_VERIFIABLE = "verifiable"

QUEUE_STATUS_READY = "READY"
QUEUE_STATUS_SETUP_REQUIRED = "SETUP_REQUIRED"
QUEUE_STATUS_EMPTY = "EMPTY"


def _parse_iso(s: Any) -> Optional[datetime]:
    """ISO8601 文字列を tz-aware datetime にする。``Z`` / ``+00:00`` 終端を吸収。

    同一 instant の辞書順比較が壊れる既知の罠（advisory_decision_log._recorded_at /
    fleet.queue._parse_iso と同じ流儀）。パース不能・非文字列は None。

    naive（tz 無し）は **ローカル時刻**として解釈する（#267 C3）。``fleet.queue._parse_iso``
    は naive を UTC とみなすが、こちらが読む2レーンの一部 writer
    （``skills/evolve-fitness/scripts/fitness_evolution.py`` 修正前や
    ``run_loop.py`` / ``genetic-prompt-optimizer/scripts/optimize.py`` 等）は
    ``datetime.now().isoformat()``（tz 無し・実行機のローカル時刻＝この開発環境では JST）で
    書く。これを UTC 扱いすると JST で 9 時間の instant ずれが生じ、最新 run 判定や TTL 判定を
    誤らせる。``datetime.astimezone()`` は naive datetime をシステムローカル時刻として解釈し
    tzinfo を付与する（値は変えず解釈だけ変える）ため、実行機のローカル時刻で書かれた既存
    naive レコードを正しく aware 化できる。
    """
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


# --- verify_pending 純関数 ----------------------------------------------------


def compute_verify_pending(
    *,
    advisory_records: List[Dict[str, Any]],
    optimize_records: List[Dict[str, Any]],
    exposure_sessions: int,
) -> Dict[str, Any]:
    """2レーンの accept 記録から verify 待ちを算出する（純関数・store I/O なし）。

    advisory_records は ``advisory_decision_log.read_advisory_decisions`` の形
    （``decision`` / ``run_id`` / ``recorded_at``）、optimize_records は
    ``optimize_history_store.load_history`` の形（``human_accepted`` / ``run_id`` /
    ``timestamp``）を想定する。

    accept 記録（advisory は ``decision == "accept"``、optimize は
    ``human_accepted is True``）のうち run_id を持つものだけを対象に、記録時刻が最も新しい
    ものが属する run_id を「直近 run」とする。その run に属する accept 件数を ``accepted``
    とし、``exposure_sessions`` と合わせて status を決める。

    Returns:
        ``{"run_id": str|None, "accepted": int, "exposure_sessions": int, "status": str}``
    """
    accepts: List[tuple] = []  # (timestamp, run_id)
    for rec in advisory_records:
        if rec.get("decision") != "accept":
            continue
        rid = rec.get("run_id")
        if not rid:
            continue
        ts = _parse_iso(rec.get("recorded_at"))
        if ts is None:
            continue
        accepts.append((ts, rid))
    for rec in optimize_records:
        if rec.get("human_accepted") is not True:
            continue
        rid = rec.get("run_id")
        if not rid:
            continue
        ts = _parse_iso(rec.get("timestamp"))
        if ts is None:
            continue
        accepts.append((ts, rid))

    if not accepts:
        return {
            "run_id": None,
            "accepted": 0,
            "exposure_sessions": exposure_sessions,
            "status": STATUS_NONE,
        }

    _latest_ts, latest_run = max(accepts, key=lambda pair: pair[0])
    accepted = sum(1 for _ts, rid in accepts if rid == latest_run)

    status = STATUS_AWAITING_EXPOSURE if exposure_sessions == 0 else STATUS_VERIFIABLE

    return {
        "run_id": latest_run,
        "accepted": accepted,
        "exposure_sessions": exposure_sessions,
        "status": status,
    }


def verify_pending_by_pj(
    pj_slug: str,
    *,
    exposure_sessions: int,
    advisory_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """pj_slug の verify 待ちを実ストア（advisory_decisions.jsonl / optimize_history）から読む。

    store I/O を行う reader（``compute_verify_pending`` の薄い呼び出し口）。
    ``select_evolve_queue`` は純関数のまま保つため、この関数は ``build_queue_result`` からのみ
    呼ぶ（select_evolve_queue には計算済み dict を material 経由で渡す）。

    ``advisory_records``（``read_advisory_decisions`` の形）を渡すと advisory ストアの
    再読込を省略する（#267 I3: ``build_queue_result`` が全 PJ 分を ``read_advisory_decisions(None)``
    で1回だけ読み、pj_slug で group by した結果を渡す経路用。PJ 数 × ストア全件走査だった
    O(PJ数 × ログ全体) を解消する）。未指定なら従来通り自前で
    ``read_advisory_decisions(pj_slug)`` を読む（後方互換・単体利用時の薄い呼び出し口）。
    """
    from optimize_history_store import load_history

    if advisory_records is None:
        from advisory_decision_log import read_advisory_decisions
        advisory_records = read_advisory_decisions(pj_slug)

    return compute_verify_pending(
        advisory_records=advisory_records,
        optimize_records=load_history(pj_slug),
        exposure_sessions=exposure_sessions,
    )


def attach_verify_pending(
    materials: List[Dict[str, Any]],
    *,
    canonicalize: Callable[[str], str],
) -> None:
    """``materials``（in place）に ``verify_pending`` を付与する（#267 I3）。

    ``advisory_decisions.jsonl`` を PJ ごとに ``read_advisory_decisions(slug)`` で
    再読込すると O(PJ数 × ログ全体) になる。``read_advisory_decisions(None)`` で全 PJ 分を
    1 回だけ読み、canonical slug（rename fold 後）で group by してから per-PJ に配る。

    ``canonicalize`` は呼び側（``fleet.queue._canonical_slug``）の rename fold 関数を注入する
    （queue_verify → queue への逆依存を避けるための関数注入。``fleet.queue`` は既にこの
    モジュールを import しているため、モジュールレベルの逆 import は循環になる）。
    """
    from advisory_decision_log import read_advisory_decisions

    advisory_by_pj: Dict[str, List[Dict[str, Any]]] = {}
    for rec in read_advisory_decisions(None):
        rec_slug = canonicalize(str(rec.get("pj_slug") or ""))
        if not rec_slug:
            continue
        advisory_by_pj.setdefault(rec_slug, []).append(rec)

    for m in materials:
        sessions = int((m.get("activity_since") or {}).get("sessions", 0) or 0)
        slug_canon = canonicalize(m["pj_slug"])
        m["verify_pending"] = verify_pending_by_pj(
            m["pj_slug"],
            exposure_sessions=sessions,
            advisory_records=advisory_by_pj.get(slug_canon, []),
        )


def format_verify_pending_suffix(vp: Optional[Dict[str, Any]]) -> str:
    """verify_pending dict を REASON 文字列への追記断片にする（無ければ空文字列）。

    ``accepted == 0``（status="none"）の PJ は追記しない — 既存 REASON 文字列の
    後方互換を保つため（verify 待ちが無い PJ では従来通りの文字列のまま）。
    """
    if not vp:
        return ""
    accepted = vp.get("accepted", 0)
    if not accepted:
        return ""
    status = vp.get("status")
    if status == STATUS_VERIFIABLE:
        return f" / verify 待ち {accepted} 件（前回 accept・検証可能）"
    if status == STATUS_AWAITING_EXPOSURE:
        return f" / verify 待ち {accepted} 件（前回 accept・露出セッションなし）"
    return ""


# --- queue 全体状態ラベル ------------------------------------------------------


def compute_queue_status(
    *,
    queue: List[Dict[str, Any]],
    untracked_with_material: List[Dict[str, Any]],
    skipped_dead: List[Dict[str, Any]],
    skipped_phantom: List[Dict[str, Any]],
    unattributed_total: int,
) -> Dict[str, str]:
    """queue result 全体の状態ラベルを決定する（純関数）。

    優先順位:
      1. queue が1件以上 → READY
      2. queue 空だが「素材はあるのに処理できない」ものが存在 → SETUP_REQUIRED
         （untracked_with_material / skipped_dead / skipped_phantom / unattributed_total のいずれか非空）
      3. それ以外（本当に閾値未満・素材なし）→ EMPTY

    ``queue_status_reason`` は常に非空の1行で根拠を添える（EMPTY と SETUP_REQUIRED が
    表示だけで見分けられない現状を直すのが目的）。

    ``skipped_dead`` は ``material_count > 0`` のものだけを「処理できない学習素材」として
    数える（#267 C4）。``build_queue_result`` の dead PJ 分岐は material がゼロでも
    ``skipped_dead`` に append するため、無条件で数えると素材ゼロの dead PJ だけで
    SETUP_REQUIRED が誤発火する（本当は EMPTY であるべきケース）。
    """
    if queue:
        return {
            "queue_status": QUEUE_STATUS_READY,
            "queue_status_reason": f"待ち PJ {len(queue)} 件",
        }

    blocking_dead = [
        d for d in skipped_dead if int(d.get("material_count") or 0) > 0
    ]

    blocked: List[str] = []
    if untracked_with_material:
        blocked.append(f"untracked material {len(untracked_with_material)} 件")
    if blocking_dead:
        blocked.append(f"skipped_dead {len(blocking_dead)} 件")
    if skipped_phantom:
        blocked.append(f"skipped_phantom {len(skipped_phantom)} 件")
    if unattributed_total:
        blocked.append(f"未帰属 corrections {unattributed_total} 件")

    if blocked:
        return {
            "queue_status": QUEUE_STATUS_SETUP_REQUIRED,
            "queue_status_reason": (
                "待ち PJ は0件ですが処理できない学習素材があります: " + " / ".join(blocked)
            ),
        }

    return {
        "queue_status": QUEUE_STATUS_EMPTY,
        "queue_status_reason": "待ち PJ 0件・処理できない学習素材もありません（閾値未満か素材なし）",
    }
