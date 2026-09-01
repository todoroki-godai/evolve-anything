#!/usr/bin/env python3
"""results_board — 戦果ボード（#379 Step 4・ADR-054 §7.2.1 柱3(a)）。

optimize_history（accept/reject 決定ログ）・correction_rate（3ストア read 時 join の
「指摘率」・#379 #400）を直読みし、「指摘率」「採用した改善」「取り下げ候補」を決定論で
1画面表示する。

growth-journal harness（crystallization イベント記録・growth_narrative の成長ストーリー）
削除の置換成果物。「記録は全自動・判断は朝の30秒・効果は週1の数字で実感」の3本目
（週1の戦果を数字で見せる）を、新規 write-only ストアを作らず既存ストア
（optimize_history / correction_rate 経由の utterances.db・correction_judged.jsonl・
weak_signals.jsonl）の直読みだけで実現する（#379 の新設凍結方針にも整合）。

**旧「手直し件数」表示（``count_human_corrections`` ベース）は置換した。併存させない**
（「手直し」を名乗る数字が2つ並ぶのは #376 の再演になるため・設計正典 §2.7）。

決定論・LLM 非依存・read-only（ファイル書き込みなし）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from capture_recall import CaptureEvalIntegrityError, evaluate_capture_recall, load_capture_eval_set
from optimize_history_store import load_effective_history, load_revert_events
from correction_rate import build_correction_rate_summary, GATE_CONSECUTIVE_WEEKS
from correction_semantic.prompt import CATEGORY_ENUM, CATEGORY_LABELS_JA
from evolve_revert import REASON_LABELS, compute_revert_availability
from pillar2_metrics import count_applied_reflections
from measurement_result import (
    collect_board_measurements,
    pillar_scopes,
    read_measurement,
    render_decisions_health,
    render_pillar2_health,
    render_rate_health,
    render_revert_health,
    render_scope,
)
import rl_common.detection as correction_detection

_WINDOW_DAYS = 30
_CAPTURE_EVAL_PATH = Path(__file__).resolve().parents[1] / "bench" / "a0_eval_set.jsonl"


def _build_capture_recall() -> Dict[str, Any]:
    if not _CAPTURE_EVAL_PATH.exists():
        return {"measured": False, "reason": "評価セットなし"}
    try:
        rows = load_capture_eval_set(_CAPTURE_EVAL_PATH)
        result = evaluate_capture_recall(
            rows,
            lambda text: correction_detection._detect_correction(text, false_positive_hashes=()),
            correction_detection.should_include_message,
        )
    except CaptureEvalIntegrityError:
        return {"measured": False, "reason": "評価セット不一致"}
    except Exception as exc:
        return {"measured": False, "reason": f"算出失敗: {type(exc).__name__}"}
    if not rows:
        return {"measured": False, "reason": "評価セットが空"}
    if not result["positives"]:
        return {"measured": False, "reason": "TPラベルなし"}
    if not result["hits"]:
        return {"measured": False, "reason": "検出ヒットなし"}
    return {
        "measured": True,
        "pattern_version": correction_detection.CORRECTION_PATTERN_VERSION,
        **result,
    }

# ADR-054 §7.2.1 柱3(a): correction_rate.build_correction_rate_summary が返す schema と
# 同型のフォールバック（read 失敗時に render 側を壊さないための安全な既定値）。
_EMPTY_CORRECTION_RATE: Dict[str, Any] = {
    "gate": {
        "gate_open": False,
        "display_start_week": None,
        "required": GATE_CONSECUTIVE_WEEKS,
        "best_run_length": 0,
        # #508: 点表示（状態(ii)）専用フィールド。フォールバック時は点表示対象なし＝状態(i)。
        "point_week": None,
        "current_run_length": 0,
    },
    "displayed_weeks": [],
    "latest_coverage": None,
    "diagnostics": {},
    "generated_at": None,
}

# ADR-054 §2.6-7: excluded の理由（テスト汚染 / legacy 無効化）を画面に出す。
# classify_decision の判定優先順位（fitness_eligible=False → テスト汚染）と同じ順序。
_EXCLUSION_REASON_LABELS = {
    "fitness_ineligible": "legacy無効化",
    "test_polluted": "テスト汚染",
}

# #512: `record_rule_revert_entry` が作る entry の id prefix と scope。
# 単一ソースは writer 側（`skills/reflect/scripts/reflect.py`）だが、reader は writer を
# import できない（skills/ 配下・循環参照）ため定数を持つ。値を変えるときは両側を直す。
_LEGACY_RULE_APPLY_ID_PREFIX = "rule_apply_"
_RULE_SCOPES = ("global_rule", "project_rule")

# #379 Step 4 実データ較正（~/.claude/evolve-anything/optimize_history/evolve-anything.jsonl・
# 38件・2026-08-10 読み取り時点）: pytest 実行由来の一時パス汚染は "pytest-of-" だけでなく
# macOS tmpfile 規約（/T/tmp<random>/ 等）にも及ぶ。狭い "pytest-of-" 限定では 30 件中 13 件
# （tmp<random> パターン）を取り逃し、真の accepted 件数が 1 件のところ複数件を誤って
# accepted と数えてしまう。
#
# 当初は汎用正規表現 `/tmp[^/]*/` で拾っていたが、`/tmpl/`（"tmpl" で始まるディレクトリ名）
# のような正当な skill パスまで汚染扱いする false positive があった（頭レビュー指摘）。
# 既知の一時ディレクトリ・ルートに限定したリテラルマーカー方式へ変更する。実データの
# tmp<random> パスは全件 `/private/var/folders/.../T/tmp<random>/` または
# `/var/folders/.../T/tmp<random>/`（symlink 解決の有無で /private 有無が揺れる）の形で、
# いずれも "/T/tmp" を含むため単一マーカーで両方を拾える。`/private/var/folders/` と
# 素の `/tmp/` セグメントは将来の別由来汚染への保険として個別に持つ（`/tmpXXX/` のような
# 曖昧一致は含めない）。
_PYTEST_OF_MARKER = "pytest-of-"
_TMP_ROOT_MARKERS = ("/private/var/folders/", "/tmp/", "/T/tmp")


def _is_test_polluted(entry: Dict[str, Any]) -> bool:
    """entry の target/skill_name がテスト実行由来の一時パスかを判定する。"""
    for key in ("target", "skill_name"):
        value = str(entry.get(key) or "")
        if not value:
            continue
        if _PYTEST_OF_MARKER in value:
            return True
        if any(marker in value for marker in _TMP_ROOT_MARKERS):
            return True
    return False


def _is_legacy_rule_apply(entry: Dict[str, Any]) -> bool:
    """#512: `human_accepted` を書く前の rule 反映 entry（legacy shape）か判定する。

    4 番目の writer（`skills/reflect/scripts/reflect.py` の `record_rule_revert_entry`）は
    #512 の修正で `human_accepted: True` を書くようになったが、それ以前に書かれた entry は
    決定フラグを持たない。そのままだと `pending` に落ち `bin/evolve-revert --list`（entry_id を
    人間が知る唯一の導線）から脱落するため、**この writer の形に限って** accepted と見なす。

    条件は 3 つすべてを満たすときのみ（広げない）:
      1. `id` が `rule_apply_` 始まり — 当該 writer だけが作る id prefix
      2. `scope` が rule スコープ — 同 writer は他 scope を書かない
      3. `revert_schema_version` を持つ — revert 記録として完成している

    この writer は「利用者が 4 択で 1)/2) を選び、rule ファイルへの追記が実際に行われた後」に
    のみ append されるため、記録の存在そのものが人間の明示承認を意味する。

    **新規 entry はこの分岐を通らない**（`human_accepted` を持つため先に決着する）。
    将来この分岐が不要になったら削除してよい。

    store の in-place migration（`legacy_accept_migration.py` 同型）を採らなかった理由:
    同形の legacy entry が他マシンの store にどれだけあるかは**このマシンからは測定不能**で、
    migration は実行された環境でしか直らない。reader 側の狭い分岐なら配布した時点で全環境に
    効く。代償は「reader が writer の形を知る」ことだが、3 条件すべてを要求して
    `rule_apply_` 以外に波及しないようにし、`_apply.py` の revert イベント
    （`revert_schema_version` を持たない）が accepted に化けないことをテストで固定した。
    """
    if not str(entry.get("id") or "").startswith(_LEGACY_RULE_APPLY_ID_PREFIX):
        return False
    if entry.get("scope") not in _RULE_SCOPES:
        return False
    return entry.get("revert_schema_version") is not None


def classify_decision(entry: Dict[str, Any]) -> str:
    """optimize_history の1エントリを accepted/rejected/pending/excluded に正規化する。

    canonical writer 4種の実 emit 形を read して判定フィールドを確認した結果
    （#398 Must 1、4 番目は #512 で追補。store_registry.py の writer 列挙と整合させること）:
      - `fitness_evolution.record_evolve_diff_decision`（source="evolve_remediation"）:
        `human_accepted`（bool）を持つ
      - `run_loop.py`（evolve-loop）: `approved`（bool）を持つ。`source`/`human_accepted`
        キー自体が無い
      - `optimize.py` の `save_history_entry`: `human_accepted`（bool | None）を持つ。
        `source`/`approved` キー自体が無い
      - `skills/reflect/scripts/reflect.py` の `record_rule_revert_entry`（#475 §8.2 の
        rule 反映記録）: #512 以降は `human_accepted`（常に True）を持つ。それ以前に
        書かれた entry は決定フラグを持たない（→ `_is_legacy_rule_apply` で救済）。
        `best_fitness` を持たないため fitness 母集団には入らない（#512・上方汚染の防止）

    **旧実装の誤り**: 「source=None → approved で判定」という survey 段階の前提は
    optimize.py の存在を見落としていた。optimize.py も source=None を書くため、
    旧ロジックでは human_accepted=True/False の optimize.py レコードが常に
    approved（欠落＝None）を読み pending に落ちる構造的バグだった。

    **是正**: source 文字列でなく**フィールドの実在と bool 型を優先**して正規化する
    （`human_accepted` が bool ならそれを採用 → 次に `approved` が bool なら採用 →
    どちらも無ければ pending）。3 writer とも同じ規則で正しく判定できる。

    優先順位: fitness_eligible=False（#376 の誤帰属 accept 無効化フラグ）を最優先で
    excluded。次点でテスト汚染パス（#420 growth-journal 汚染と同系統）を excluded。
    """
    if entry.get("fitness_eligible") is False:
        return "excluded"
    if _is_test_polluted(entry):
        return "excluded"

    human_accepted = entry.get("human_accepted")
    approved = entry.get("approved")
    if isinstance(human_accepted, bool):
        decided: Optional[bool] = human_accepted
    elif isinstance(approved, bool):
        decided = approved
    elif _is_legacy_rule_apply(entry):
        decided = True
    else:
        decided = None

    if decided is True:
        return "accepted"
    if decided is False:
        return "rejected"
    return "pending"


def _exclusion_reason(entry: Dict[str, Any]) -> str:
    """excluded と分類された entry の理由を返す（ADR-054 §2.6-7）。

    classify_decision と同じ優先順位（fitness_eligible=False 最優先 → テスト汚染）で
    判定する。classify_decision が "excluded" を返さない entry への呼び出しは呼び出し側
    の契約違反だが、防御的に "unknown" を返す。
    """
    if entry.get("fitness_eligible") is False:
        return "fitness_ineligible"
    if _is_test_polluted(entry):
        return "test_polluted"
    return "unknown"


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    """timestamp を aware UTC datetime にパースする（growth_report._is_today と同型）。

    naive 文字列（tz 情報なし）は UTC として解釈する。パース不能・非文字列は None。
    """
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


def _in_window(
    record: Dict[str, Any], start: datetime, end: datetime, *, inclusive_end: bool = False
) -> bool:
    """[start, end) 判定。``inclusive_end=True`` なら [start, end]（``now`` ちょうどを含める）。

    直近 window の上限は呼び出し時点の ``now`` 自身であることが多く、排他的にすると
    「たった今」記録されたエントリが直近集計から漏れる。前 window との境界（window_start）は
    二重計上を避けるため排他のまま据え置く。
    """
    ts = _parse_timestamp(record.get("timestamp"))
    if ts is None:
        return False
    if inclusive_end:
        return start <= ts <= end
    return start <= ts < end


def build_results_board(
    slug: str,
    now: Optional[datetime] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """戦果ボードを決定論生成する（read-only・LLM 非依存）。

    Args:
        slug: PJ slug。optimize_history_store.load_history と telemetry_query の
            project フィルタの両方に共通で使う。リポジトリ直下（worktree でない）呼び出しでは
            pj_slug.resolve_pj_slug の basename と telemetry_query の project-name（ディレクトリ
            basename）が一致する前提（既存コードの growth_report.py 等と同じ簡略化）。
        now: 基準時刻（省略時は現在の UTC）。テストの決定論性のため注入可能にする。

    Returns:
        correction_rate（ADR-054 §7.2.1 柱3(a)「指摘率」の gate 状態 + 表示対象週 +
        直近確定週のカバレッジ + diagnostics）・decisions（accepted/rejected/
        pending/excluded の直近30日件数・excluded も常時 key を出す=silence≠evaluated）・
        accepted_list（直近30日 accepted の skill_name+日付、最大10件・新しい順）・
        withdrawal_candidates（accepted のうち verdict==REGRESSED のもの）。
    """
    _now = now or datetime.now(timezone.utc)
    window_start = _now - timedelta(days=_WINDOW_DAYS)
    prev_window_start = _now - timedelta(days=_WINDOW_DAYS * 2)

    # ── 指摘率（ADR-054 §7.2.1 柱3(a)）: 3ストア read 時 join の週次集計 ──────
    correction_rate, history, revert_events, scopes, measurements = collect_board_measurements(
        slug,
        correction_reader=lambda: build_correction_rate_summary(now=_now),
        history_reader=lambda: load_effective_history(slug),
        revert_reader=lambda: load_revert_events(slug),
        correction_fallback={**_EMPTY_CORRECTION_RATE, "generated_at": _now.isoformat()},
    )
    capture_recall = _build_capture_recall()

    pillar2_fallback = {
        "count": 0,
        "measured": False,
        "health": {"degraded": True},
        "not_measured": {
            "hook": {"reason": "no_store"},
            "pitfall_memory": {"reason": "mtime_collision"},
        },
    }
    if project_root is None:
        pillar2 = pillar2_fallback
        pillar2_health = {
            "measured": False,
            "reason": "project_root が指定されていません",
            "dropped_lines": 0,
        }
    else:
        pillar2, pillar2_health = read_measurement(
            lambda: count_applied_reflections(Path(project_root), now=_now),
            fallback=pillar2_fallback,
            reader_name="pillar2_metrics.count_applied_reflections",
        )
    measurements["pillar2"] = pillar2_health

    # ── 採用した改善: 直近30日の optimize_history ─────────────────
    # #402 段階4: revert 済み accept を判断母集団から除外した effective view を読む
    # （raw のままだと revert イベントが history[-10:] に混入し本物の decision を
    # 押し出す・S1）。
    # withdrawal candidate の「戻し済み」表示用（S4）。effective view は revert 済み
    # accept を既に除外しているため、このボードで reverted=True になることは構造上
    # 無いが、fold の内部実装に依存せず load_revert_events 経由で判定する契約にする
    # （results_board で individual fold 実装をしない・設計正典 §3）。
    reverted_ids = {
        e.get("reverted_entry_id")
        for e in revert_events
        if e.get("reverted_entry_id") is not None
    }

    recent_history = [
        h for h in history if _in_window(h, window_start, _now, inclusive_end=True)
    ]

    buckets: Dict[str, List[Dict[str, Any]]] = {
        "accepted": [], "rejected": [], "pending": [], "excluded": [],
    }
    for entry in recent_history:
        buckets[classify_decision(entry)].append(entry)

    # ADR-054 §2.6-7: excluded の理由（テスト汚染 / legacy 無効化）内訳。
    excluded_reasons = dict(Counter(_exclusion_reason(e) for e in buckets["excluded"]))

    def _label(entry: Dict[str, Any]) -> str:
        return entry.get("skill_name") or entry.get("target") or "(unknown)"

    def _sort_key(raw_timestamp: Any) -> datetime:
        # #398 Should 3: 生文字列の辞書順比較は tz 表記混在（"Z" vs "+00:00"）で誤順序に
        # なる既知 pitfall（ISO8601 辞書順比較）と同型。_parse_timestamp でパースした
        # aware datetime を比較する。パース不能/欠落は最古扱い（reverse=True で末尾に沈む）。
        parsed = _parse_timestamp(raw_timestamp)
        return parsed if parsed is not None else datetime.min.replace(tzinfo=timezone.utc)

    accepted_list = sorted(
        (
            {"skill_name": _label(e), "timestamp": e.get("timestamp")}
            for e in buckets["accepted"]
        ),
        key=lambda x: _sort_key(x["timestamp"]),
        reverse=True,
    )[:10]

    # ── 取り下げ候補: accepted のうち verdict == REGRESSED ──────────
    # #402 段階4 §3(S4): entry_id/revert_available/revert_unavailable_reason/reverted
    # を構造化結果まで運ぶ（PR-1 の敗因「導線ゼロ」の再演を防ぐ）。
    withdrawal_candidates = []
    for e in buckets["accepted"]:
        if e.get("verdict") != "REGRESSED":
            continue
        entry_id = e.get("id")
        available, reason = compute_revert_availability(e)
        withdrawal_candidates.append({
            "skill_name": _label(e),
            "timestamp": e.get("timestamp"),
            "verdict": e.get("verdict"),
            "entry_id": entry_id,
            "revert_available": available,
            "revert_unavailable_reason": reason,
            "reverted": entry_id is not None and entry_id in reverted_ids,
        })

    return {
        "slug": slug,
        "generated_at": _now.isoformat(),
        "correction_rate": correction_rate,
        "capture_recall": capture_recall,
        "pillar2": pillar2,
        "measurement_scopes": scopes,
        "measurements": measurements,
        "decisions": {
            "accepted": len(buckets["accepted"]),
            "rejected": len(buckets["rejected"]),
            "pending": len(buckets["pending"]),
            "excluded": len(buckets["excluded"]),
        },
        "excluded_reasons": excluded_reasons,
        "accepted_list": accepted_list,
        "withdrawal_candidates": withdrawal_candidates,
    }


def _category_breakdown_lines(category_breakdown: Optional[Dict[str, Any]]) -> List[str]:
    """1週分のカテゴリ内訳（#400 A5・設計 §2.6「表示の形」）を markdown 行にする。

    ``measured=False``（同一物理キーへの conflicting category 検出）・counts が空
    （legacy 週で category を持つ TP が1件も無い）のいずれでも空リストを返す
    （§3: 内訳は「あれば出す」optional・category を持たない週で壊れない）。
    週次 delta は雑音（設計 §2.6 実測）なので**比較は表示しない** — 今週の構成比 +
    最大カテゴリの実発話1件のみ。
    """
    cb = category_breakdown or {}
    counts: Dict[str, int] = cb.get("counts") or {}
    # 衝突（同一 physical key に複数 category）で内訳を落としたときは、**黙って消さない**。
    # 内訳行が痕跡なく消えると「カテゴリが無い週」と見分けが付かず、判定の重複記録という
    # 測定バグの手がかりを失う（P4: silence != evaluated）。内訳は出さないが理由は出す。
    conflict_keys = cb.get("conflict_keys") or 0
    if not cb.get("measured"):
        if conflict_keys:
            return [
                f"  カテゴリ内訳: 測定不能"
                f"（同一発話に複数カテゴリ {conflict_keys} 件 — 判定の重複記録が疑われます）"
            ]
        return []
    if not counts:
        return []

    total = sum(counts.values())
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], CATEGORY_ENUM.index(kv[0])))
    parts = []
    for cat, cnt in ordered:
        pct = (cnt / total * 100) if total else 0.0
        label = CATEGORY_LABELS_JA.get(cat, cat)
        parts.append(f"{label}（{cat}）{cnt}件（{pct:.0f}%）")
    unclassified = cb.get("unclassified_count") or 0
    if unclassified:
        parts.append(f"unclassified {unclassified}件")

    lines = [f"  カテゴリ構成: {', '.join(parts)}"]
    example = cb.get("top_category_example")
    top_category = cb.get("top_category")
    if example and top_category:
        top_label = CATEGORY_LABELS_JA.get(top_category, top_category)
        text = (example.get("text") or "").strip()
        reason = (example.get("reason") or "").strip()
        suffix = f"（{reason}）" if reason else ""
        lines.append(f"  最大カテゴリ（{top_label}）の実発話: {text}{suffix}")
    return lines


def _render_exclusion_diagnostics(diagnostics: Dict[str, Any]) -> List[str]:
    """指摘率の分母から除外した件数を表示する（#466・0件でも必ず表示・silence != evaluated）。

    3種別（tracked外 / 90日超 / ホーム起動）は ``correction_rate.py`` が排他的に集計する
    （ホーム起動セッションを先に除外してから tracked 判定するため、tracked外の件数と
    二重計上しない）。
    """
    untracked = diagnostics.get("excluded_untracked_total", 0)
    cutoff = diagnostics.get("excluded_before_cutoff_total", 0)
    home = diagnostics.get("excluded_home_dir_total", 0)
    total = diagnostics.get("excluded_total", untracked + cutoff + home)
    return [
        f"分母から除外: {total} 件（tracked外 {untracked} 件・"
        f"90日超 {cutoff} 件・ホーム起動 {home} 件）",
        "",
    ]


def _render_point_pj_breakdown(pj_breakdown: Dict[str, Any]) -> List[str]:
    """点表示（状態(ii)）専用の PJ 別内訳（#508 I7・全 PJ 列挙・floor 込み）。

    状態(iii) の PJ 行構築ロジック（既存・不変）とは意図的に別関数にする（既存分岐を
    一字も変えないため）。judged が ``MIN_PJ_RATE_DENOM`` 未満の PJ は件数のみ出す。
    """
    parts: List[str] = []
    for pj_slug, stats in sorted(pj_breakdown.items()):
        if stats.get("rate") is not None:
            parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（{stats['rate'] * 100:.1f}%）")
        else:
            parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（件数のみ・分母不足）")
    return parts


def _render_correction_rate_point(gate: Dict[str, Any], correction_rate: Dict[str, Any]) -> List[str]:
    """指摘率セクションの状態(ii)（点表示）ブロックを生成する（#508 §2-3 必須要素 (a)〜(f)）。

    呼び出し側（``_render_correction_rate``）が ``point_week`` の存在と PJ 別内訳の
    非空（I7(d)）を確認してから呼ぶ契約。
    """
    point_week = gate["point_week"]
    required = gate.get("required", GATE_CONSECUTIVE_WEEKS)
    n = gate.get("current_run_length", 0)

    week_id = point_week["week_id"]
    judged = point_week["judged_count"]
    tp = point_week["tp_count"]
    rate = point_week.get("rate")
    rate_label = f"{rate * 100:.1f}%" if rate is not None else "?"

    lines: List[str] = [
        # (a)(b): 対象週の week_id と分子/分母の実数。(c): 1週分の但し書き。
        f"**指摘率（{week_id}）: {rate_label}**（1週分。推移は {required} 週連続で表示）",
        f"判定 {judged} 件中 TP {tp} 件・カバレッジ100%",
        # (d): 連続 run の進捗。n は表示専用（I8）。
        f"連続 run の進捗: {n}/{required} 週連続",
    ]

    # (e)/I6: 点の対象週より新しい確定候補週が未測定なら、欠測を隠さず明示する。
    latest = correction_rate.get("latest_coverage")
    if latest and latest.get("week_id") != week_id:
        reasons = latest.get("failure_reasons") or []
        reason_note = f"・理由: {'・'.join(reasons)}" if reasons else ""
        lines.append(
            f"最新候補週 {latest['week_id']}: 判定カバレッジ {latest['judged']}/{latest['total']}"
            f"・未測定{reason_note}"
        )

    # (f)/I7: PJ 別内訳を必須 evidence として全件列挙する。
    pj_lines = _render_point_pj_breakdown(point_week.get("pj_breakdown") or {})
    lines.append(f"PJ別: {', '.join(pj_lines)}")

    lines.append("")
    return lines


def _render_correction_rate(
    correction_rate: Dict[str, Any],
    gate_health: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """指摘率セクション（ADR-054 §7.2.1 柱3(a)）の markdown ブロックを生成する。

    表示開始ゲート（§2.9・k=``GATE_CONSECUTIVE_WEEKS`` 週連続で全量判定確定週が揃うまで
    非表示）が閉じている間は「未測定（判定カバレッジ X/Y）」1行のみ。開いていれば
    表示対象週を新しい順に列挙し、PJ別内訳（Simpson 防御・必須 evidence）と、
    悪化週のみ TP 実発話 TOP3（朝レビューへの導線）を添える。

    #400 A5（設計 §2.6「表示の形」）: 各週の TP をカテゴリ（対象軸）で内訳分解した行も
    添える。**週次 delta の比較は表示しない**（週次 TP ≈10〜20件を8分割すると各セル
    0〜5件で雑音・PJ 構成比の変化＝task-mix 交絡が支配的なため）。表示は今週の構成比 +
    最大カテゴリの実発話1件 + task-mix 交絡の注記の3点に絞る。category を持つ週が
    1つも無ければ注記自体を出さない（silence でなく、内訳が単に無いだけ）。
    """
    gate = correction_rate.get("gate") or {}
    # #568 T3: gate_open を鵜呑みにせず、検算に通った場合だけ系列表示を許す。
    # 検算は `measurement_result.validate_correction_gate` が行い、summary 自体は
    # verbatim のまま（board["correction_rate"] の pass-through 契約を壊さない）。
    # gate_health が None の呼び出し（既存テスト等）は従来どおり gate_open に従う。
    gate_open_effective = (
        gate.get("gate_open") is True
        if gate_health is None
        else gate_health.get("gate_open_effective") is True
    )
    required = gate.get("required", GATE_CONSECUTIVE_WEEKS)
    lines: List[str] = []

    # #466: 分母から除外した件数は gate の開閉に関わらず常に表示する（silence != evaluated）。
    lines.extend(_render_exclusion_diagnostics(correction_rate.get("diagnostics") or {}))

    # #508 状態(ii): 系列ゲートが閉じていても、点表示できる確定週があれば1週分の点を出す。
    # I7(d): PJ 別内訳が空なら点表示そのものを行わない（状態(i)へフォールバック）。
    # 既存の閉ゲート分岐・開ゲート分岐はこの下で一字も変えない。
    point_week = gate.get("point_week")
    if not gate_open_effective and point_week and (point_week.get("pj_breakdown") or {}):
        lines.extend(_render_correction_rate_point(gate, correction_rate))
        return lines

    if not gate_open_effective:
        latest = correction_rate.get("latest_coverage")
        if latest:
            headline = (
                f"指摘率: 未測定（判定カバレッジ {latest['judged']}/{latest['total']}・"
                f"{latest['week_id']}）"
            )
        else:
            headline = "指摘率: 未測定（確定週データなし）"
        lines.append(f"**{headline}**")
        lines.append(f"全量判定の確定週が {required} 週連続で揃うまで系列は表示しません。")
        lines.append("")
        return lines

    displayed = correction_rate.get("displayed_weeks") or []
    lines.append("**指摘率**（判定カバレッジ100%の確定週のみ・新しい順）")
    lines.append(
        "分子は LLM judge の意味判定です（実測 precision 80% ＝ 分子の2割は誤りを含む前提で読んでください）。"
    )
    lines.append("")
    category_lines_by_week = {
        w["week_id"]: _category_breakdown_lines(w.get("category_breakdown"))
        for w in displayed
    }
    # task-mix 交絡の注記は**構成比を実際に表示する週がある場合だけ**出す。
    # 「測定不能」行しか無い週で注記だけ出すと、読者が存在しない構成比を探すことになる。
    if any(
        (w.get("category_breakdown") or {}).get("measured")
        and (w.get("category_breakdown") or {}).get("counts")
        for w in displayed
    ):
        lines.append(
            "カテゴリ構成は**その週に何をやったか**に強く依存します（task-mix 交絡）。"
            "週次の増減比較には使わず、今週の内訳として読んでください。"
        )
        lines.append("")
    for w in reversed(displayed):
        rate = w.get("rate")
        rate_label = f"{rate * 100:.1f}%" if rate is not None else "?"
        lines.append(
            f"- {w['week_id']}: {rate_label}"
            f"（判定 {w['judged_count']} 件中 TP {w['tp_count']} 件・カバレッジ100%）"
        )
        pj_parts = []
        for pj_slug, stats in sorted((w.get("pj_breakdown") or {}).items()):
            if stats.get("rate") is not None:
                pj_parts.append(
                    f"{pj_slug} {stats['tp']}/{stats['judged']}（{stats['rate'] * 100:.1f}%）"
                )
            else:
                pj_parts.append(f"{pj_slug} {stats['tp']}/{stats['judged']}（件数のみ・分母不足）")
        if pj_parts:
            lines.append(f"  PJ別: {', '.join(pj_parts)}")
        lines.extend(category_lines_by_week.get(w["week_id"], []))
        if w.get("is_worsening") and w.get("top3_examples"):
            lines.append("  悪化週です。気になった直近の指摘:")
            for ex in w["top3_examples"]:
                text = (ex.get("text") or "").strip()
                reason = (ex.get("reason") or "").strip()
                suffix = f"（{reason}）" if reason else ""
                lines.append(f"    - {text}{suffix}")
    lines.append("")
    return lines


def render_results_board(board: Dict[str, Any]) -> List[str]:
    """戦果ボードの markdown ブロックを生成する（判定+行動を最上部・証拠は直下）。"""
    decisions = board["decisions"]

    lines = ["## 🏆 戦果ボード", ""]
    scopes = board.get("measurement_scopes") or pillar_scopes(board.get("slug", "(unknown)"))
    measurements = board.get("measurements") or {}

    capture = board.get("capture_recall") or {"measured": False, "reason": "評価セットなし"}
    if capture.get("measured"):
        recall_low, recall_high = capture["recall_ci"]
        precision_low, precision_high = capture["precision_ci"]
        lines.append(
            f"**L1捕捉率: {capture['caught']}/{capture['positives']} = {capture['recall']:.1%}** "
            f"（Wilson 95% CI {recall_low:.1%}–{recall_high:.1%}・pattern v{capture['pattern_version']}）"
        )
        lines.append(
            f"精度: {capture['caught']}/{capture['hits']} = {capture['precision']:.1%} "
            f"（Wilson 95% CI {precision_low:.1%}–{precision_high:.1%}）"
        )
    else:
        lines.append(f"**L1捕捉率: 未測定（{capture.get('reason', '評価セットなし')}）**")
    lines.append(render_scope(scopes, "capture_recall"))
    lines.append("")

    pillar2 = board.get("pillar2") or {
        "count": 0,
        "measured": False,
        "health": {"degraded": True},
        "not_measured": {},
    }
    lines.extend(render_pillar2_health(pillar2, measurements))
    lines.append(render_scope(scopes, "pillar2"))
    lines.append("")

    lines.extend(render_rate_health(measurements))
    lines.append(render_scope(scopes, "correction_rate"))
    lines.extend(
        _render_correction_rate(
            board.get("correction_rate") or _EMPTY_CORRECTION_RATE,
            measurements.get("correction_rate_gate"),
        )
    )

    lines.extend(render_decisions_health(decisions, measurements))
    lines.append(render_scope(scopes, "accepted_improvements"))
    # ADR-054 §2.6-7: excluded の理由内訳を画面に出す（テスト汚染/legacy無効化が
    # どちらもどこにも見えない状態を解消する）。
    excluded_reasons = board.get("excluded_reasons") or {}
    if decisions["excluded"] and excluded_reasons:
        reason_parts = ", ".join(
            f"{_EXCLUSION_REASON_LABELS.get(k, k)} {v} 件"
            for k, v in sorted(excluded_reasons.items(), key=lambda kv: -kv[1])
        )
        lines.append(f"  excluded 内訳: {reason_parts}")
    lines.append("")

    if board["accepted_list"]:
        lines.append("### 直近の採用")
        for item in board["accepted_list"]:
            ts = (item.get("timestamp") or "")[:10]
            lines.append(f"- {ts}: {item['skill_name']}")
        lines.append("")

    if board["withdrawal_candidates"]:
        lines.append("### 取り下げ候補（採用後に REGRESSED 判定）")
        for item in board["withdrawal_candidates"]:
            ts = (item.get("timestamp") or "")[:10]
            lines.append(f"- {ts}: {item['skill_name']} — verdict={item['verdict']}")
            entry_id = item.get("entry_id")
            if item.get("reverted"):
                lines.append("  戻し済みです。")
            elif item.get("revert_available") and entry_id:
                # #402 段階4 §3(S4): revert_available=true の行には実行コマンド
                # そのものを印字する（既定 dry-run のため2段案内）。
                lines.append(f"  bin/evolve-revert {entry_id}            # 何が起きるか確認（既定 dry-run）")
                lines.append(f"  bin/evolve-revert {entry_id} --apply    # 実際に戻す")
            elif not item.get("revert_available"):
                # コード（機械用）でなく日本語1行（人間用）を表示する（§3 理由コード2層）。
                reason = item.get("revert_unavailable_reason")
                label = REASON_LABELS.get(reason, reason) if reason else None
                if label:
                    lines.append(f"  {label}")
        lines.append("")

    lines.extend(render_revert_health(measurements))
    lines.append(render_scope(scopes, "withdrawal_candidates"))
    lines.append("")

    return lines
