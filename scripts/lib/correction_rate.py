#!/usr/bin/env python3
"""correction_rate — ADR-054 §7.2.1 柱3(a)「指摘率」の週次集計（read 時 3ストア join）。

設計正典: docs/decisions/drafts/054-c-a-numerator.md（codex 2巡 + tacchi 1巡・全 [Must] 反映済み）。

#466 分母修正: 従来の分母（週内の全 dialogue 発話）は判定器（judge_runner）の判定母集団
（tracked PJ + 90日 cutoff に絞る）と非対称だった。tracked 外 PJ の発話は分母に入るのに
judge が永久に判定しないため、カバレッジが 100% に到達せず表示ゲートが開かなかった。
本モジュールは分母算出に judge_runner と**同一の**絞り込み述語（``_apply_population_filters`` /
``_resolve_tracked_slugs``）を import して使う（両側にコピーを作らない・単一ソース）。
加えて、ホームディレクトリ起動セッション（PJ の実体を持たない・fleet_config が discover
候補からも除外する規約と同型）の発話を分母から除外する。除外は 3 種別（tracked外 /
90日超 / ホーム起動）を diagnostics に集計し、呼び出し側（results_board）が常に件数を
表示する契約（silence != evaluated）。

3ストアを read 時に join する（新ストアを作らない・#379 新設凍結）:
  - ``utterances.db``（分母）— dialogue 発話の物理キー・timestamp・ingested_at
  - ``correction_judged.jsonl``（判定進捗）— judge が判定した物理キーと judged_at
  - ``weak_signals.jsonl``（分子）— channel=llm_judge の TP 記録（raw・promoted/TTL 不問）

指標: 「指摘率」= その週の発話のうち judge が判定した件数を分母、そのうち TP と判定された
件数を分子とする割合。**カバレッジ 100%（未判定 0 件）の確定週のみ**値を出す。

#400 ADR-054 A5: 上記の TP を ``weak_signal.provenance.category``（対象軸 8値 enum）で
内訳集計する（設計正典 docs/decisions/drafts/054-a5-correction-category.md §2.6）。母集団は
指摘率の分子と完全に同一。同一 physical key に複数 category が付いたら黙って多数決・
最新値を採らず、その週の内訳を未測定にする（`correction_type` 自体は変更しない）。

freeze（§2.2）: 週 W の cutoff = 週終了 + ``FREEZE_DELAY_DAYS`` 日。3ストア全てにこの
cutoff を課す（``ingested_at`` / ``judged_at`` / ``detected_at`` がいずれも cutoff 以前の
レコードのみ採用）。確定後に対象行を更新・削除しない契約（backlog drain や migration の
再判定で過去週が黙って動かないようにする・#376 再発防止）。この契約は producer 側の運用
規約であり、本モジュールは「削除された過去レコード」自体を検出する手段を持たない
（read 時点のスナップショットしか見えないため。新ストアを作らない制約と両立しない検出は
実装しない — 削除検知は運用契約として store docstring 側に明記する）。

決定論・LLM 非依存・read-only。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# #400 A5: enum は単一ソース（correction_semantic.prompt が語彙表・優先順の正典）。
# 純関数のみを import する（I/O なし・collect_raw_data の遅延 import 方針とは無関係）。
from correction_semantic.prompt import CATEGORY_ENUM  # noqa: E402

# #466: judge の母集団フィルタ（tracked PJ + 90日 cutoff）を単一ソースとして共有する。
# 新規実装しない（両側にコピーを作ると再発する drift の典型・pitfall_copied_parse_convention）。
from correction_semantic.judge_runner import (  # noqa: E402
    DEFAULT_JUDGE_UTTERANCE_MAX_AGE_DAYS as _JUDGE_MAX_AGE_DAYS_DEFAULT,
    _apply_population_filters,
    _resolve_tracked_slugs,
)
from pj_slug import canonical_pj_slug as _canonical_pj_slug  # noqa: E402
from pj_slug import pj_slug_fast as _pj_slug_fast  # noqa: E402
from measurement_result import MeasuredDict, metadata as _measurement_metadata  # noqa: E402

# D の値（§2.2）: 実測（週最大1,566件 > 週上限1,400件）から初期値として設定した**仮の運用値**。
# 100%表示ゲートがあるため D の誤差は誤った率でなく「未測定週の増加」として現れる（安全側）。
FREEZE_DELAY_DAYS = 3

# 表示開始ゲート（§2.9）: 全量判定（カバレッジ100%）の確定週が k 週連続で揃うまで系列を表示しない。
GATE_CONSECUTIVE_WEEKS = 4

# PJ 別内訳の rate 表示 floor（§2.7）: 「1桁分母」= judged が 10 未満なら rate を隠す
# （件数evidenceは常に出す。Simpson のパラドックス対策）。
MIN_PJ_RATE_DENOM = 10

LLM_JUDGE_CHANNEL = "llm_judge"


# ─────────────────────────────────────────────────────────────────
# 週境界ヘルパー（ISO 週・UTC 固定）
# ─────────────────────────────────────────────────────────────────
def week_id_for(dt: datetime) -> str:
    """UTC aware datetime から ISO 週 ID（``"YYYY-Www"``）を返す。"""
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def week_bounds(week_id: str) -> "tuple[datetime, datetime]":
    """週 ID から [開始（月曜 00:00 UTC）, 終了（次週月曜 00:00 UTC・排他的）) を返す。"""
    start = datetime.strptime(f"{week_id}-1", "%G-W%V-%u").replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=7)


def _next_week_id(week_id: str) -> str:
    start, _ = week_bounds(week_id)
    return week_id_for(start + timedelta(days=7))


# ─────────────────────────────────────────────────────────────────
# ISO8601 パース（辞書順比較禁止 pitfall 対策・results_board._parse_timestamp と同型）
# ─────────────────────────────────────────────────────────────────
def _parse_iso(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ─────────────────────────────────────────────────────────────────
# 生データ収集（production 既定は3ストアを read-only で読む）
# ─────────────────────────────────────────────────────────────────
def collect_raw_data() -> Dict[str, Any]:
    """3ストアを read-only で読む（production 既定経路）。

    utterances は dialogue のみ・sidechain（``/subagents/``）除外済み
    （``utterance_archive.query.query_utterances_all_projects`` の既定契約）。
    DuckDB は ``read_only=True`` 接続（pitfall_duckdb_read_opens_readwrite 対策・#65）。
    """
    from correction_semantic.store import read_judged_records
    from utterance_archive.query import query_utterances_all_projects
    from weak_signals.store import read_signals

    utterances = query_utterances_all_projects(source_kinds=("dialogue",))
    judged = read_judged_records()
    weak_signals = read_signals()
    return {
        "utterances": utterances,
        "judged": judged,
        "weak_signals": weak_signals,
        "_measurement_health": {
            "utterances": _measurement_metadata(utterances),
            "judged": _measurement_metadata(judged),
            "weak_signals": _measurement_metadata(weak_signals),
        },
    }


# ─────────────────────────────────────────────────────────────────
# ホームディレクトリ起動セッションの除外（#466・2026-08-16 ユーザー決定）
# ─────────────────────────────────────────────────────────────────
def _home_pj_slug() -> Optional[str]:
    """ホームディレクトリ起動セッションの pj_slug を返す（writer 側と同一規約で導出）。

    ``pj_slug_fast`` は worktree マーカー無し・cache 未指定のとき basename にフォールバックする
    ため、``Path.home()`` に対しては home dir の basename（例: ``matsukaze-takashi``）を返す。
    独自実装しない（#466 委譲メモの決定: PJ slug が ``matsukaze-takashi`` の発話を分母から外す
    ＝この基準そのものを固定文字列で書かず、writer 規約から動的に導出する）。
    """
    return _canonical_pj_slug(_pj_slug_fast(str(Path.home())))


def _split_home_dir_utterances(
    utterances: List[Dict[str, Any]],
) -> "tuple[List[Dict[str, Any]], int]":
    """ホームディレクトリ起動セッションの発話を分母から除外する（#466）。

    PJ の実体を持たないセッション（``fleet_config.filter_valid_projects`` が discover 候補
    からも除外する規約と同型）であり、tracked 化されることも判定対象になることもない。
    tracked フィルタより**先に**独立して適用する（tracked 化状態が将来変わっても除外が
    tracked 判定の副作用として消えないようにするため。診断内訳も tracked外と混ぜない）。
    """
    home_slug = _home_pj_slug()
    if not home_slug:
        return utterances, 0
    kept: List[Dict[str, Any]] = []
    excluded = 0
    for u in utterances:
        slug = _canonical_pj_slug(u.get("pj_slug"))
        if slug == home_slug:
            excluded += 1
        else:
            kept.append(u)
    return kept, excluded


def _physical_key(source_path: Any, line_no: Any) -> str:
    """utterances.db の PK と同型の物理キーを構成する（既存 utterance_key と同一規則）。

    key 文字列を末尾の ``:`` で split して逆算しない（source_path にコロンが入り得る・
    設計 §2.4）。構成は常にこの1関数を通す。
    """
    return f"{source_path or ''}:{line_no if line_no is not None else ''}"


def _coverage_gap_reason(
    *,
    population_keys: List[str],
    judged_key_set: Set[str],
    judged_at_by_key: Dict[str, datetime],
    judged_record_keys: Set[str],
    cutoff: datetime,
    expected_gap_count: int,
    judged_source_measured: bool,
) -> Dict[str, Any]:
    """カバレッジ不足を締切超過・未判定・分類不能へ排他的に分ける。

    ``unclassified_count`` は判定レコード自体は存在するものの ``judged_at`` を解釈
    できない件数。内訳合計が呼び出し側の ``母集団 − 判定済`` と違えば、数値を正常値
    として扱わず明示的に評価不能へ倒す。
    """
    if not judged_source_measured:
        return {
            "measured": False,
            "deadline_exceeded_count": None,
            "unjudged_count": None,
            "unclassified_count": None,
            "reason": "判定記録を取得できません",
        }

    unresolved_keys = set(population_keys) - judged_key_set
    deadline_exceeded_count = sum(
        1 for key in unresolved_keys
        if (judged_at_by_key.get(key) is not None and judged_at_by_key[key] > cutoff)
    )
    unjudged_count = sum(1 for key in unresolved_keys if key not in judged_record_keys)
    unclassified_count = sum(
        1 for key in unresolved_keys
        if key in judged_record_keys and key not in judged_at_by_key
    )
    classified_total = deadline_exceeded_count + unjudged_count + unclassified_count
    measured = classified_total == expected_gap_count
    return {
        "measured": measured,
        "deadline_exceeded_count": deadline_exceeded_count,
        "unjudged_count": unjudged_count,
        "unclassified_count": unclassified_count,
        "reason": (
            None if measured
            else f"内訳合計が母集団と一致しません（{classified_total}/{expected_gap_count} 件）"
        ),
    }


# ─────────────────────────────────────────────────────────────────
# 週次集計本体
# ─────────────────────────────────────────────────────────────────
def compute_weekly_correction_rate(
    *,
    now: Optional[datetime] = None,
    raw: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    tracked_projects: Optional[List[str]] = None,
    judge_utterance_max_age_days: int = _JUDGE_MAX_AGE_DAYS_DEFAULT,
) -> Dict[str, Any]:
    """3ストアを read 時 join し、週ごとの指摘率を決定論算出する。

    Args:
        tracked_projects: DI 用（``judge_runner._resolve_tracked_slugs`` と同型）。None なら
                    production 既定として ``fleet_config.load_config()`` を読む。
        judge_utterance_max_age_days: judge の 90日 cutoff と同一の値（#466）。既定は
                    ``judge_runner.DEFAULT_JUDGE_UTTERANCE_MAX_AGE_DAYS`` を単一ソースとして使う。

    Returns:
        {"weeks": [週ごとの dict（下記）], "diagnostics": {...}, "generated_at": iso}

    週 dict のキー: week_id / week_start / week_end / cutoff / total_population /
    judged_count / tp_count / coverage / measured / rate（未測定なら None） /
    failure_reasons / pj_breakdown / top3_examples。

    **進行中の週（cutoff 未到達）は候補にすら含めない**（§2.1）。cutoff 到達済みの週は
    カバレッジ100%未満・検証失敗でも "weeks" に含める（measured=False。表示ゲート判定・
    最新カバレッジ evidence の材料になるため）。

    **#466 分母フィルタ**: 母集団（``raw["utterances"]``）に対し、判定器と同一の絞り込み
    （tracked PJ + 90日 cutoff）とホームディレクトリ起動セッション除外を、週次集計より
    **前**に一括で適用する。除外件数は ``diagnostics`` に必ず記録する（silence != evaluated）。
    """
    _now = now or datetime.now(timezone.utc)
    raw = raw if raw is not None else collect_raw_data()
    source_health = raw.get("_measurement_health", {}) or {}
    source_failure_reasons = [
        f"{name}: {health.get('reason') or '読取失敗'}"
        for name, health in source_health.items()
        if not (health or {}).get("measured", True)
    ]

    raw_utterances = raw.get("utterances", []) or []
    utterances_no_home, excluded_home_dir_total = _split_home_dir_utterances(raw_utterances)
    tracked_slugs: Set[str] = _resolve_tracked_slugs(tracked_projects)
    cutoff_for_population: Optional[datetime] = None
    if judge_utterance_max_age_days is not None:
        cutoff_for_population = _now - timedelta(days=judge_utterance_max_age_days)
    (
        filtered_utterances,
        excluded_untracked_total,
        excluded_untracked_by_pj,
        excluded_before_cutoff_total,
    ) = _apply_population_filters(utterances_no_home, tracked_slugs, cutoff_for_population)
    raw = dict(raw)
    raw["utterances"] = filtered_utterances

    diagnostics: Dict[str, Any] = {
        "measurement_source_health": source_health,
        # #466: 分母から除外した件数（judge の母集団と揃えるため・silence != evaluated）。
        "excluded_home_dir_total": excluded_home_dir_total,
        "excluded_untracked_total": excluded_untracked_total,
        "excluded_untracked_by_pj": excluded_untracked_by_pj,
        "excluded_before_cutoff_total": excluded_before_cutoff_total,
        "excluded_total": (
            excluded_home_dir_total + excluded_untracked_total + excluded_before_cutoff_total
        ),
        "judged_missing_key": 0,
        "judged_unparseable_judged_at": 0,
        "judged_duplicate_keys": 0,
        "weak_missing_provenance": 0,
        "weak_unparseable_detected_at": 0,
        "orphan_tp_no_utterance": 0,
        "utterance_unparseable_timestamp": 0,
        "utterance_unparseable_ingested_at": 0,
        "conflict_keys": 0,
        # #400 A5: 同一 physical key に複数 category が付いた件数（週横断の合計）。
        "category_conflict_keys": 0,
    }

    # ── judged_at_by_key（最古の有効判定を採用・§2.2 競合解決） ──────
    judged_at_by_key: Dict[str, datetime] = {}
    judged_record_keys: Set[str] = set()
    for rec in raw.get("judged", []) or []:
        key = rec.get("key")
        if key is None:
            diagnostics["judged_missing_key"] += 1
            continue
        judged_record_keys.add(key)
        jat = _parse_iso(rec.get("judged_at"))
        if jat is None:
            diagnostics["judged_unparseable_judged_at"] += 1
            continue
        existing = judged_at_by_key.get(key)
        if existing is None:
            judged_at_by_key[key] = jat
        else:
            diagnostics["judged_duplicate_keys"] += 1
            if jat < existing:
                judged_at_by_key[key] = jat

    # ── TP 記録を物理キーごとにグルーピング（channel=llm_judge の raw 記録） ──
    tp_records_by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in raw.get("weak_signals", []) or []:
        if rec.get("channel") != LLM_JUDGE_CHANNEL:
            continue
        prov = rec.get("provenance") or {}
        source_path = prov.get("source_path")
        line_no = prov.get("line_no")
        if not source_path or line_no in (None, ""):
            diagnostics["weak_missing_provenance"] += 1
            continue
        dat = _parse_iso(rec.get("detected_at"))
        if dat is None:
            diagnostics["weak_unparseable_detected_at"] += 1
            continue
        key = _physical_key(source_path, line_no)
        tp_records_by_key[key].append({
            "detected_at": dat,
            "provenance": prov,
            "session_id": rec.get("session_id"),
            "pj_slug": rec.get("pj_slug"),
        })

    # ── 相反 TP 記録の検出（§2.2: 同一物理キーで session_id が食い違う＝集計失敗） ──
    conflict_keys: set = set()
    for key, recs in tp_records_by_key.items():
        if len({r.get("session_id") for r in recs}) > 1:
            conflict_keys.add(key)
    diagnostics["conflict_keys"] = len(conflict_keys)

    # ── utterances を物理キー・週でインデックス ──────────────────
    utterances_by_key: Dict[str, Dict[str, Any]] = {}
    weeks_map: Dict[str, List[str]] = defaultdict(list)
    for u in raw.get("utterances", []) or []:
        key = _physical_key(u.get("source_path"), u.get("line_no"))
        ts = _parse_iso(u.get("timestamp"))
        if ts is None:
            diagnostics["utterance_unparseable_timestamp"] += 1
            continue
        if _parse_iso(u.get("ingested_at")) is None:
            diagnostics["utterance_unparseable_ingested_at"] += 1
            # ingested_at が無ければ freeze cutoff を評価できないため母集団に入れない
            # （安全側 — population にも weeks_map にも登録しない）
            continue
        utterances_by_key[key] = u
        weeks_map[week_id_for(ts)].append(key)

    # orphan TP（母集団のどの発話にも属さない物理キー）を全体で surface する。
    known_keys = set(utterances_by_key)
    for key in tp_records_by_key:
        if key not in known_keys:
            diagnostics["orphan_tp_no_utterance"] += 1

    # ── 週ごとに集計（暦週昇順） ────────────────────────────────
    weeks_out: List[Dict[str, Any]] = []
    for week_id in sorted(weeks_map):
        week_start, week_end = week_bounds(week_id)
        cutoff = week_end + timedelta(days=FREEZE_DELAY_DAYS)
        if _now < cutoff:
            continue  # 進行中の週は候補にすら含めない（§2.1）

        population_keys: List[str] = []
        for key in weeks_map[week_id]:
            u = utterances_by_key[key]
            ingested_at = _parse_iso(u.get("ingested_at"))
            if ingested_at is not None and ingested_at <= cutoff:
                population_keys.append(key)
        total_population = len(population_keys)

        judged_keys: List[str] = []
        for key in population_keys:
            jat = judged_at_by_key.get(key)
            if jat is not None and jat <= cutoff:
                judged_keys.append(key)
        judged_count = len(judged_keys)
        judged_key_set = set(judged_keys)
        coverage_gap_reason = _coverage_gap_reason(
            population_keys=population_keys,
            judged_key_set=judged_key_set,
            judged_at_by_key=judged_at_by_key,
            judged_record_keys=judged_record_keys,
            cutoff=cutoff,
            expected_gap_count=total_population - judged_count,
            judged_source_measured=(source_health.get("judged") or {}).get("measured", True),
        )

        failure_reasons: List[str] = list(source_failure_reasons)
        tp_keys: List[str] = []
        top3_source: List[Dict[str, Any]] = []
        for key in population_keys:
            recs = tp_records_by_key.get(key)
            if not recs:
                continue
            if key not in judged_at_by_key:
                # 分子 ⊆ 分母 違反（TP はあるのに判定記録が無い）
                failure_reasons.append("tp_without_judged_record")
                continue
            if key in conflict_keys:
                failure_reasons.append("tp_conflict")
                continue
            if key not in judged_key_set:
                # judged_at が cutoff 外（未判定扱い）なら TP も未確定として扱う
                continue
            valid_recs = [r for r in recs if r["detected_at"] <= cutoff]
            if not valid_recs:
                continue
            tp_keys.append(key)
            latest = max(valid_recs, key=lambda r: r["detected_at"])
            top3_source.append({"detected_at": latest["detected_at"], "record": latest})
        tp_count = len(tp_keys)

        coverage = (judged_count / total_population) if total_population > 0 else 0.0
        measured = total_population > 0 and coverage == 1.0 and not failure_reasons
        rate = (tp_count / judged_count) if (measured and judged_count > 0) else (
            0.0 if measured else None
        )

        pj_breakdown = _pj_breakdown(population_keys, judged_key_set, set(tp_keys), utterances_by_key)
        category_breakdown = _category_breakdown(tp_keys, tp_records_by_key, cutoff)
        diagnostics["category_conflict_keys"] += category_breakdown["conflict_keys"]

        top3_source.sort(key=lambda t: t["detected_at"], reverse=True)
        top3_examples = [
            {
                "text": t["record"]["provenance"].get("text", ""),
                "reason": t["record"]["provenance"].get("reason", ""),
                "idiom": t["record"]["provenance"].get("idiom", ""),
                "pj_slug": t["record"].get("pj_slug"),
            }
            for t in top3_source[:3]
        ]

        weeks_out.append({
            "week_id": week_id,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "cutoff": cutoff.isoformat(),
            "total_population": total_population,
            "judged_count": judged_count,
            "tp_count": tp_count,
            "coverage": coverage,
            "measured": measured,
            "rate": rate,
            "failure_reasons": sorted(set(failure_reasons)),
            "coverage_gap_reason": coverage_gap_reason,
            "pj_breakdown": pj_breakdown,
            "top3_examples": top3_examples,
            "category_breakdown": category_breakdown,
        })

    return {
        "weeks": weeks_out,
        "diagnostics": diagnostics,
        "generated_at": _now.isoformat(),
    }


def _pj_breakdown(
    population_keys: List[str],
    judged_key_set: set,
    tp_key_set: set,
    utterances_by_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """PJ 別の判定済件数・TP数・カバレッジ・rate を集計する（§2.7 Simpson 防御 evidence）。

    rate は judged が ``MIN_PJ_RATE_DENOM`` 未満（1桁）なら None にする（件数は常に出す）。
    """
    totals: Dict[str, int] = defaultdict(int)
    judged: Dict[str, int] = defaultdict(int)
    tp: Dict[str, int] = defaultdict(int)
    for key in population_keys:
        slug = utterances_by_key[key].get("pj_slug") or "(unknown)"
        totals[slug] += 1
        if key in judged_key_set:
            judged[slug] += 1
        if key in tp_key_set:
            tp[slug] += 1

    out: Dict[str, Dict[str, Any]] = {}
    for slug in totals:
        j = judged[slug]
        out[slug] = {
            "total": totals[slug],
            "judged": j,
            "tp": tp[slug],
            "coverage": (j / totals[slug]) if totals[slug] > 0 else 0.0,
            "rate": (tp[slug] / j) if j >= MIN_PJ_RATE_DENOM else None,
        }
    return out


# ─────────────────────────────────────────────────────────────────
# カテゴリ内訳（#400 ADR-054 A5・設計正典 drafts/054-a5-correction-category.md §2.6）
# ─────────────────────────────────────────────────────────────────
def _category_breakdown(
    tp_keys: List[str],
    tp_records_by_key: Dict[str, List[Dict[str, Any]]],
    cutoff: datetime,
) -> Dict[str, Any]:
    """その週の TP を category（対象軸 8値 enum）で内訳集計する（§2.6）。

    母集団は指摘率の分子と完全に同一 —— 呼び出し元が既に確定した ``tp_keys``
    （physical key 単位・重複や session_id 競合を除去済み）をそのまま使う。

    - **物理キー単位で数える**（同一 key に同一 category の重複記録があっても 1 件）
    - **同一物理キーに複数の異なる category が付いたら黙って多数決・最新値を採らず、
      その週のカテゴリ内訳を丸ごと未測定にする**（§2.4/§2.6）。分母（指摘率本体）には
      影響しない —— 内訳固有の追加制約
    - category を持たない TP（A5 以前の legacy・schema 不整合）は ``unclassified_count``
      に計上し、conflict とは区別する（黙って多数決に混ぜない・エラーにもしない）
    """
    counts: Dict[str, int] = defaultdict(int)
    unclassified_count = 0
    conflict_key_count = 0
    examples_by_category: Dict[str, Dict[str, Any]] = {}

    for key in tp_keys:
        recs = tp_records_by_key.get(key, [])
        valid_recs = [r for r in recs if r["detected_at"] <= cutoff]
        categories = {
            r["provenance"].get("category")
            for r in valid_recs
            if r["provenance"].get("category") is not None
        }
        if len(categories) > 1:
            conflict_key_count += 1
            continue
        if not categories:
            unclassified_count += 1
            continue
        cat = next(iter(categories))
        if cat not in CATEGORY_ENUM:
            # producer が既知の enum を書く契約（prompt._validate_verdict）だが、
            # 過去データ・別 producer の混入に備えて防御的に unclassified 扱いにする。
            unclassified_count += 1
            continue
        counts[cat] += 1
        # 代表例は同一カテゴリ内で detected_at が最も新しい記録を採用（top3_examples と同方針）。
        latest = max((r for r in valid_recs if r["provenance"].get("category") == cat),
                     key=lambda r: r["detected_at"])
        existing = examples_by_category.get(cat)
        if existing is None or latest["detected_at"] > existing["detected_at"]:
            examples_by_category[cat] = latest

    measured = conflict_key_count == 0

    top_category: Optional[str] = None
    top_example: Optional[Dict[str, Any]] = None
    if measured and counts:
        top_category = max(
            counts.items(),
            key=lambda kv: (kv[1], -CATEGORY_ENUM.index(kv[0])),
        )[0]
        rec = examples_by_category.get(top_category)
        if rec is not None:
            prov = rec["provenance"]
            top_example = {
                "text": prov.get("text", ""),
                "reason": prov.get("reason", ""),
                "idiom": prov.get("idiom", ""),
                "pj_slug": rec.get("pj_slug"),
            }

    return {
        "measured": measured,
        "counts": dict(counts) if measured else {},
        "unclassified_count": unclassified_count if measured else 0,
        "conflict_keys": conflict_key_count,
        "top_category": top_category,
        "top_category_example": top_example,
    }


# ─────────────────────────────────────────────────────────────────
# 表示開始ゲート（§2.9: 全量判定の確定週が k 週連続で揃うまで表示しない）
# ─────────────────────────────────────────────────────────────────
def compute_display_gate(
    weeks: List[Dict[str, Any]], k: int = GATE_CONSECUTIVE_WEEKS,
) -> Dict[str, Any]:
    """``weeks``（week_id 昇順の finalized 週一覧）から表示ゲートの状態を決定論判定する。

    連続性は **暦週として隣接しているか**（week_id が1週分進んでいるか）で判定する。
    途中に候補週が存在しない（population=0）ギャップも「連続」を断ち切る。

    #508: 系列ゲート（``gate_open``）とは独立に、点表示専用の追加フィールドを返す。
    **``gate_open`` の判定式（``best_run >= k``）はこの拡張で一切変更しない**（round2 [Must]）。
    """
    best_run: List[Dict[str, Any]] = []
    current_run: List[Dict[str, Any]] = []
    for w in weeks:
        if current_run and _next_week_id(current_run[-1]["week_id"]) != w["week_id"]:
            current_run = []
        if w.get("measured"):
            current_run.append(w)
        else:
            current_run = []
        if len(current_run) > len(best_run):
            best_run = list(current_run)

    gate_open = len(best_run) >= k

    # #508 §2-1: point_week（点表示対象週）と current_run_length（点表示の進捗専用）。
    # 呼出契約は昇順を仮定するだけで内部ソートしない既存関数だが、この2フィールドは
    # §2-1-(c) のとおり明示的に week_id でソートしてから算出する（非ソート入力でも
    # 正しい値になることをテストで固定する）。gate_open の判定には使わない。
    sorted_weeks = sorted(weeks, key=lambda w: w["week_id"])
    point_week: Optional[Dict[str, Any]] = None
    current_run_length = 0
    point_run: List[Dict[str, Any]] = []
    for w in sorted_weeks:
        if point_run and _next_week_id(point_run[-1]["week_id"]) != w["week_id"]:
            point_run = []
        if w.get("measured"):
            point_run.append(w)
            # I9: weeks 中で week_id が最大の measured=True 週を採用し続ける
            # （最後まで走査した時点で最新の measured 週に確定する）。
            point_week = w
            # I8: current_run_length は point_week（最新の measured 週）で終わる
            # 連続 run の長さ。point_week 確定と同時に更新するため、point_week より
            # 後ろに未測定週が続いても current_run_length は動かない（表示専用）。
            current_run_length = len(point_run)
        else:
            point_run = []

    return {
        "gate_open": gate_open,
        "display_start_week": best_run[0]["week_id"] if gate_open else None,
        "required": k,
        "best_run_length": len(best_run),
        "point_week": point_week,
        "current_run_length": current_run_length,
    }


# ─────────────────────────────────────────────────────────────────
# 表示用集約（results_board から呼ぶエントリポイント）
# ─────────────────────────────────────────────────────────────────
def build_correction_rate_summary(
    *,
    now: Optional[datetime] = None,
    raw: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    tracked_projects: Optional[List[str]] = None,
    judge_utterance_max_age_days: int = _JUDGE_MAX_AGE_DAYS_DEFAULT,
) -> Dict[str, Any]:
    """戦果ボード向けに指摘率を集約する（gate 判定 + 悪化週フラグ付き）。

    gate が閉じている間は ``displayed_weeks=[]`` で、直近の確定週のカバレッジのみ返す
    （§2.7「測定不能なら 指摘率: 未測定（判定カバレッジ X/Y）」の材料）。
    """
    result = compute_weekly_correction_rate(
        now=now,
        raw=raw,
        tracked_projects=tracked_projects,
        judge_utterance_max_age_days=judge_utterance_max_age_days,
    )
    weeks = result["weeks"]
    gate = compute_display_gate(weeks)

    displayed: List[Dict[str, Any]] = []
    if gate["gate_open"]:
        prev_rate: Optional[float] = None
        for w in weeks:
            if not w["measured"] or w["week_id"] < gate["display_start_week"]:
                continue
            entry = dict(w)
            is_worsening = prev_rate is not None and w["rate"] is not None and w["rate"] > prev_rate
            entry["is_worsening"] = is_worsening
            if not is_worsening:
                entry["top3_examples"] = []
            prev_rate = w["rate"]
            displayed.append(entry)

    latest = weeks[-1] if weeks else None
    latest_coverage = (
        {
            "week_id": latest["week_id"],
            "judged": latest["judged_count"],
            "total": latest["total_population"],
            # #508 I6: 点表示（状態(ii)）が「最新候補週の未測定理由」を出せるよう、
            # coverage<1.0 以外の未測定理由（分子⊆分母違反等）も一緒に運ぶ。
            "failure_reasons": latest.get("failure_reasons", []),
        }
        if latest else None
    )

    utterances_health = (result["diagnostics"].get("measurement_source_health", {}) or {}).get(
        "utterances"
    ) or {}
    coverage_gaps = None if not utterances_health.get("measured", True) else [
        {
            "week_id": week["week_id"],
            "judged": week["judged_count"],
            "total": week["total_population"],
            "reason": week["coverage_gap_reason"],
        }
        for week in weeks
        if week["coverage"] < 1.0
    ]

    summary = {
        "gate": gate,
        "displayed_weeks": displayed,
        "latest_coverage": latest_coverage,
        "coverage_gaps": coverage_gaps,
        "diagnostics": result["diagnostics"],
        "generated_at": result["generated_at"],
    }
    source_health = result["diagnostics"].get("measurement_source_health", {}) or {}
    failures = [
        f"{name}: {health.get('reason') or '読取失敗'}"
        for name, health in source_health.items()
        if not (health or {}).get("measured", True)
    ]
    return MeasuredDict(
        summary,
        measured=not failures,
        reason="; ".join(failures) or None,
        dropped_lines=sum(
            int((health or {}).get("dropped_lines", 0))
            for health in source_health.values()
        ),
    )
