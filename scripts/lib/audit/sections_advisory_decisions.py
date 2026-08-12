"""Advisory Decisions の observability セクション（#284 / #267 Sprint 1）。

advisory detector は長らく「表示するだけ」で、提案が accept されたか却下されたかを
一切追跡していなかった（#267 実測前提2: advisory は decision lane に1件も載っていない）。
#284 で emit→drain に載せた結果を、ここで detector 単位に surface する。

採用率の分母は「判断された数」（accept+reject）でなく ``surfaced`` でなければならない
（#267 実測: freeze 解除条件が「detector 別 surfaced/accepted 集計が3ヶ月分揃ったら」の
ため、surfaced を記録しない限りこの条件は構造的に成立しない）。surfaced/deferred は
``evolve_decisions.ingest_decisions`` が drain 到達時に記録する（#267 Sprint 1）。
**ただし surfaced は「提示された数」ではなく「drain 到達数」**（``ingest_decisions`` は
``not dry_run`` のときだけ記録するため、dry-run レポートに出たまま drain されず放置
された提案は分母に入らない。無視され続ける detector ほど採用率が上振れする逆バイアスが
ある。#381 tacchi レビュー）。

「どの detector の提案が実際に採用されているか」が見えて初めて、効かない detector を
淘汰できる（#267 Sprint 3）。書きっぱなしの advisory を1つ減らすための reader でもある。

observability contract 互換 ``(project_dir) -> Optional[List[str]]``。決定論・LLM 非依存。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

# ⚠ 淘汰候補フラグを出す最小「判断済み（cohort 内 accept + cohort 内 reject）」件数。
# 閾値未満で出すと (a) surfaced=0 の detector（採用率 "-" なのに淘汰候補と矛盾）と
# (b) surfaced>0 だが全件未判断（人間がまだ y/n していないだけ）に冤罪を出す
# （#381 tacchi レビュー: 旧条件は accept==0 単独判定だった）。
MIN_DECIDED_FOR_WARNING = 5


def _compute(project_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        from advisory_decision_log import (
            _recorded_at,
            read_advisory_decisions,
            summarize_by_detector,
        )
        from pj_slug import resolve_pj_slug
    except ImportError:
        return None

    # slug は optimize_history / advisory ストアの書込側と同じ単一ソースで解決する
    # （evolve_decisions.resolve_slug も同関数の thin wrapper・#492）。observability 供給
    # モジュールに ~/.claude 由来の module 定数を持ち込まないため直接 import する。
    records = read_advisory_decisions(slug=resolve_pj_slug(project_dir))
    return {
        "summary": summarize_by_detector(records),
        "total": len(records),
        "period": _record_period(records, _recorded_at),
    }


def _record_period(records: List[Dict[str, Any]], recorded_at_fn) -> Optional[Dict[str, Any]]:
    """記録の最古／最新 ``recorded_at`` を read 時導出する（新ストアは作らない・#381 D）。

    freeze 解除条件（「3ヶ月分揃ったら」）の判定材料として、表がどの期間の記録かを
    読者が判別できるようにする。``recorded_at`` を持たないレコードは無視する。
    """
    stamps = [recorded_at_fn(rec) for rec in records if rec.get("recorded_at")]
    if not stamps:
        return None
    earliest, latest = min(stamps).date(), max(stamps).date()
    return {
        "earliest": earliest.isoformat(),
        "latest": latest.isoformat(),
        "days": (latest - earliest).days,
    }


def _render(data: Dict[str, Any]) -> List[str]:
    summary: Dict[str, Dict[str, int]] = data["summary"]
    lines = [f"ℹ advisory 提案の記録を {data['total']} 件保持（detector 別）"]
    period = data.get("period")
    if period:
        lines.append(
            f"ℹ 記録期間: {period['earliest']} 〜 {period['latest']}"
            f"（{period['days']}日）"
        )
    lines += [
        "",
        "| detector | surfaced | accept | reject | 未判断 | ever deferred | 採用率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    legacy_accept_total = 0
    legacy_reject_total = 0
    warn = []
    for detector_id in sorted(summary):
        counts = summary[detector_id]
        surfaced = counts.get("surfaced", 0)
        accept = counts.get("accept", 0)
        reject = counts.get("reject", 0)
        deferred = counts.get("deferred", 0)
        open_count = counts.get("open", 0)
        # 採用率の分母は surfaced（drain 到達数）。分子は surfaced 記録がある提案の
        # accept だけ（accept_in_cohort）。surfaced 記録開始前の accept/reject を
        # 混ぜると採用率が 100% を超える（移行期間の嘘。#267/#381）。
        accept_in_cohort = counts.get("accept_in_cohort", accept)
        legacy_accept = counts.get("legacy_accept", 0)
        reject_in_cohort = counts.get("reject_in_cohort", reject)
        legacy_reject = counts.get("legacy_reject", 0)
        legacy_accept_total += legacy_accept
        legacy_reject_total += legacy_reject

        # accept/reject 列は cohort 内件数を主表示し、legacy（surfaced 記録前）分は
        # 行内に内訳として添える（隠さない・#381 C: 行内で検算が成立するように）。
        accept_display = (
            f"{accept_in_cohort} (+{legacy_accept} legacy)" if legacy_accept else str(accept_in_cohort)
        )
        reject_display = (
            f"{reject_in_cohort} (+{legacy_reject} legacy)" if legacy_reject else str(reject_in_cohort)
        )
        rate = f"{accept_in_cohort / surfaced:.0%}" if surfaced else "-"
        lines.append(
            f"| {detector_id} | {surfaced} | {accept_display} | {reject_display} | "
            f"{open_count} | {deferred} | {rate} |"
        )

        decided_in_cohort = accept_in_cohort + reject_in_cohort
        if decided_in_cohort >= MIN_DECIDED_FOR_WARNING and accept_in_cohort == 0:
            warn.append(detector_id)

    lines.append("")
    lines.append(
        "※ 採用率の分母（surfaced）は「提示された数」ではなく **drain 到達数**（dry-run "
        "のまま drain されず放置された提案は含まない）。無視され続けている detector ほど"
        "分母が小さくなり、採用率は**上振れ側**に偏る。"
    )
    lines.append(
        "※ `未判断` は surfaced 済かつ accept/reject 未記録の**現在**の件数。`ever deferred` "
        "はレーン健全性の参考指標（初回 drain で判断されなかった**ユニーク提案数**・回数では"
        "ない。accept と非排他）。"
    )
    if legacy_accept_total or legacy_reject_total:
        lines.append(
            f"※ surfaced 記録開始前の accept が {legacy_accept_total} 件、reject が "
            f"{legacy_reject_total} 件あり、採用率の分子・分母から除外している（列内の "
            "`(+N legacy)` が detector 別の内訳）。"
        )

    if warn:
        lines += [
            "",
            f"⚠ 判断済み（cohort 内 accept+reject）{MIN_DECIDED_FOR_WARNING}件以上で "
            f"accept 0 件の detector: {', '.join(sorted(warn))}"
            "（提案が採用されていない＝淘汰候補）。",
        ]
    return lines


def build_advisory_decisions_section(project_dir: Path) -> Optional[List[str]]:
    """detector 別の advisory 提案 surfaced/accept/reject/deferred を audit に surface する。

    観測可能性:
    - モジュール未解決 → None（沈黙）
    - 記録 0 件 → None（沈黙）。lane を1度も通していない PJ で「0 件」を出すと
      「評価対象が無い PJ では observability を空にする」既存契約
      （test_empty_when_no_observability_artifacts）を破るため、他の clean-時-沈黙
      section（invalid_frontmatter / subagent_noise）と同じ扱いにする
    - 記録あり → detector 別テーブル（surfaced/accept/reject/未判断/ever deferred/採用率）+
      判断済み（cohort 内 accept+reject）が ``MIN_DECIDED_FOR_WARNING`` 件以上で accept 0
      件の detector の ⚠
    """
    return build_advisory_section(
        project_dir,
        title="Advisory Decisions",
        compute=_compute,
        applicable=lambda data: data["total"] > 0,
        render=_render,
    )
