#!/usr/bin/env python3
"""correction_rate — ADR-054 §7.2.1 柱3(a)「指摘率」の週次集計（read 時 3ストア join）。

設計正典: docs/decisions/drafts/054-c-a-numerator.md（codex 2巡 + tacchi 1巡・全 [Must] 反映済み）。

3ストアを read 時に join する（新ストアを作らない・#379 新設凍結）:
  - ``utterances.db``（分母）— dialogue 発話の物理キー・timestamp・ingested_at
  - ``correction_judged.jsonl``（判定進捗）— judge が判定した物理キーと judged_at
  - ``weak_signals.jsonl``（分子）— channel=llm_judge の TP 記録（raw・promoted/TTL 不問）

指標: 「指摘率」= その週の発話のうち judge が判定した件数を分母、そのうち TP と判定された
件数を分子とする割合。**カバレッジ 100%（未判定 0 件）の確定週のみ**値を出す。

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
from typing import Any, Dict, List, Optional

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
def collect_raw_data() -> Dict[str, List[Dict[str, Any]]]:
    """3ストアを read-only で読む（production 既定経路）。

    utterances は dialogue のみ・sidechain（``/subagents/``）除外済み
    （``utterance_archive.query.query_utterances_all_projects`` の既定契約）。
    DuckDB は ``read_only=True`` 接続（pitfall_duckdb_read_opens_readwrite 対策・#65）。
    """
    from correction_semantic.store import read_judged_records
    from utterance_archive.query import query_utterances_all_projects
    from weak_signals.store import read_signals

    return {
        "utterances": query_utterances_all_projects(source_kinds=("dialogue",)),
        "judged": read_judged_records(),
        "weak_signals": read_signals(),
    }


def _physical_key(source_path: Any, line_no: Any) -> str:
    """utterances.db の PK と同型の物理キーを構成する（既存 utterance_key と同一規則）。

    key 文字列を末尾の ``:`` で split して逆算しない（source_path にコロンが入り得る・
    設計 §2.4）。構成は常にこの1関数を通す。
    """
    return f"{source_path or ''}:{line_no if line_no is not None else ''}"


# ─────────────────────────────────────────────────────────────────
# 週次集計本体
# ─────────────────────────────────────────────────────────────────
def compute_weekly_correction_rate(
    *, now: Optional[datetime] = None, raw: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """3ストアを read 時 join し、週ごとの指摘率を決定論算出する。

    Returns:
        {"weeks": [週ごとの dict（下記）], "diagnostics": {...}, "generated_at": iso}

    週 dict のキー: week_id / week_start / week_end / cutoff / total_population /
    judged_count / tp_count / coverage / measured / rate（未測定なら None） /
    failure_reasons / pj_breakdown / top3_examples。

    **進行中の週（cutoff 未到達）は候補にすら含めない**（§2.1）。cutoff 到達済みの週は
    カバレッジ100%未満・検証失敗でも "weeks" に含める（measured=False。表示ゲート判定・
    最新カバレッジ evidence の材料になるため）。
    """
    _now = now or datetime.now(timezone.utc)
    raw = raw if raw is not None else collect_raw_data()

    diagnostics: Dict[str, int] = {
        "judged_missing_key": 0,
        "judged_unparseable_judged_at": 0,
        "judged_duplicate_keys": 0,
        "weak_missing_provenance": 0,
        "weak_unparseable_detected_at": 0,
        "orphan_tp_no_utterance": 0,
        "utterance_unparseable_timestamp": 0,
        "utterance_unparseable_ingested_at": 0,
        "conflict_keys": 0,
    }

    # ── judged_at_by_key（最古の有効判定を採用・§2.2 競合解決） ──────
    judged_at_by_key: Dict[str, datetime] = {}
    for rec in raw.get("judged", []) or []:
        key = rec.get("key")
        if key is None:
            diagnostics["judged_missing_key"] += 1
            continue
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

        failure_reasons: List[str] = []
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
            "pj_breakdown": pj_breakdown,
            "top3_examples": top3_examples,
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
# 表示開始ゲート（§2.9: 全量判定の確定週が k 週連続で揃うまで表示しない）
# ─────────────────────────────────────────────────────────────────
def compute_display_gate(
    weeks: List[Dict[str, Any]], k: int = GATE_CONSECUTIVE_WEEKS,
) -> Dict[str, Any]:
    """``weeks``（week_id 昇順の finalized 週一覧）から表示ゲートの状態を決定論判定する。

    連続性は **暦週として隣接しているか**（week_id が1週分進んでいるか）で判定する。
    途中に候補週が存在しない（population=0）ギャップも「連続」を断ち切る。
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
    return {
        "gate_open": gate_open,
        "display_start_week": best_run[0]["week_id"] if gate_open else None,
        "required": k,
        "best_run_length": len(best_run),
    }


# ─────────────────────────────────────────────────────────────────
# 表示用集約（results_board から呼ぶエントリポイント）
# ─────────────────────────────────────────────────────────────────
def build_correction_rate_summary(
    *, now: Optional[datetime] = None, raw: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """戦果ボード向けに指摘率を集約する（gate 判定 + 悪化週フラグ付き）。

    gate が閉じている間は ``displayed_weeks=[]`` で、直近の確定週のカバレッジのみ返す
    （§2.7「測定不能なら 指摘率: 未測定（判定カバレッジ X/Y）」の材料）。
    """
    result = compute_weekly_correction_rate(now=now, raw=raw)
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
        {"week_id": latest["week_id"], "judged": latest["judged_count"], "total": latest["total_population"]}
        if latest else None
    )

    return {
        "gate": gate,
        "displayed_weeks": displayed,
        "latest_coverage": latest_coverage,
        "diagnostics": result["diagnostics"],
        "generated_at": result["generated_at"],
    }
