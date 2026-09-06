"""evolve_revert_listing — `bin/evolve-revert --list` 用の read-only 一覧生成
（ADR-054 Phase D PR4/D2）。

戦果ボード（``results_board.py``）の「取り下げ候補」は verdict==REGRESSED に絞った表示だが、
`--list` は「戻せる可能性のある採用全体」を対象にする（revert 対象を REGRESSED に限定する
理由が無い——entry_id を探す導線としては全 accepted を対象にすべき）。

``evolve_revert`` パッケージには置かない——``results_board.classify_decision`` を使うため、
``evolve_revert`` パッケージ内に置くと results_board(→evolve_revert) との循環 import になる
（results_board は既に ``from evolve_revert import REASON_LABELS, compute_revert_availability``
している）。本モジュールは両方の上位に立つ薄い集約層として独立させる（``evolve_revert_cli.py``
と同じ「CLI 直下・パッケージ外」の置き方）。

出力契約（#376「黙って落とさない」）: 採用（accepted）でない entry（rejected/pending/
#376 で無効化された excluded）は revert の対象になりえないため一覧に出さない。一方、
**accepted の中で revert 不可な entry（記録拡張前・対象外 lane）は除外せず reason つきで
残す**——ADR-054 Phase D の裁定により optimize.py/run_loop.py 経由の採用は revert 対象外
だが、それを一覧から消すと「採用したのに存在しないことになっている」という #376 型の
不正直さを再生産する。

決定論・LLM 非依存・read-only（一切書き込まない）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from optimize_history_store import load_effective_history, resolve_slug
from evolve_revert import REASON_LABELS, compute_revert_availability, detect_subsequent_change
from results_board import classify_decision
from measurement_result import MeasuredList, read_measurement


def build_revert_listing(slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """slug の accepted entry を revert 可否つきで新しい順に列挙する。

    #475 §8.2: revert 可能（``revert_available=True``）な entry に限り、対象ファイルへの
    後続変更（採用直後の内容からも変更前の内容からも既に変わっている状態）を
    ``evolve_revert.detect_subsequent_change`` で read-only 判定する
    （``compute_revert_availability`` 自体は変更しない・別レイヤーとして追加）。

    Returns:
        各要素 ``{entry_id, skill_name, target, scope, timestamp, revert_available,
        revert_unavailable_reason, subsequent_change}``。``subsequent_change`` は
        ``revert_available=False`` の entry では判定対象外のため常に ``None``。
    """
    if slug is None:
        slug = resolve_slug()

    history, history_measurement = read_measurement(
        lambda: load_effective_history(slug), fallback=[]
    )
    if history is None:
        history = []

    items: List[Dict[str, Any]] = []
    for entry in history:
        if classify_decision(entry) != "accepted":
            continue
        available, reason = compute_revert_availability(entry)
        subsequent_change = detect_subsequent_change(entry) if available else None
        items.append({
            "entry_id": entry.get("id"),
            "skill_name": entry.get("skill_name") or entry.get("target") or "(unknown)",
            "target": entry.get("target"),
            "scope": entry.get("scope"),
            "timestamp": entry.get("timestamp"),
            "revert_available": available,
            "revert_unavailable_reason": reason,
            "subsequent_change": subsequent_change,
        })

    # timestamp 欠落は最古扱い（末尾）にする。新しい順（reverse=True）と組合わせ、
    # 欠落キー（空文字列）は辞書順で最小になるため sort 前に降順で末尾へ回る。
    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return MeasuredList(items, **history_measurement)


def render_revert_listing(items: List[Dict[str, Any]]) -> List[str]:
    """人間向けテキスト表示を生成する（``bin/evolve-revert --list`` の既定出力）。"""
    if not bool(getattr(items, "measured", True)):
        return [f"採用履歴: 測定不能（{getattr(items, 'reason', None) or '理由不明'}）"]
    if not items:
        return ["採用の記録はありません（0件）"]

    # 「戻せる」件数は後続変更ありの entry を除く（§8.2: 同ファイルへの後続変更が
    # あると conflict で戻せなくなるため、集計もそれを反映する）。
    revertible_count = sum(
        1 for it in items if it["revert_available"] and not it.get("subsequent_change")
    )
    unavailable_count = len(items) - revertible_count

    lines = [
        f"採用 {len(items)} 件（戻せる {revertible_count} 件 / 戻せない {unavailable_count} 件）",
        "",
    ]
    if getattr(items, "dropped_lines", 0):
        lines.insert(1, str(getattr(items, "reason", "")))
    for it in items:
        ts = (it.get("timestamp") or "")[:10] or "(日時不明)"
        entry_id = it.get("entry_id") or "(id不明)"
        skill = it.get("skill_name")
        if it["revert_available"] and it.get("subsequent_change"):
            lines.append(
                f"[戻せません] {entry_id}  {ts}  {skill} — "
                "このファイルはその後さらに変更されたため後続変更ありで戻せません"
            )
        elif it["revert_available"]:
            lines.append(f"[戻せる] {entry_id}  {ts}  {skill}")
            lines.append(
                f"    bin/evolve-revert {entry_id}            # 何が起きるか確認（既定 dry-run）"
            )
        else:
            reason = it.get("revert_unavailable_reason")
            label = REASON_LABELS.get(reason, reason) if reason else "理由不明"
            lines.append(f"[対象外] {entry_id}  {ts}  {skill} — {label}")
    return lines
