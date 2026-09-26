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

from capture_recall import CaptureEvalIntegrityError, evaluate_capture_recall, load_capture_eval_set, load_capture_union
from optimize_history_store import load_effective_history, load_revert_events
from correction_rate import (
    build_correction_rate_summary,
    GATE_CONSECUTIVE_WEEKS,
)
from evolve_revert import REASON_LABELS, compute_revert_availability
from results_board_rate_render import _render_correction_rate
from pillar2_metrics import PILLAR2_NOT_MEASURED_TARGETS, count_applied_reflections
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
_CAPTURE_EVAL_FILENAME = "a0_eval_set.jsonl"
_CAPTURE_EVAL_PATH = Path(__file__).resolve().parents[1] / "bench" / _CAPTURE_EVAL_FILENAME
_JUDGE_RESULTS_PATH = Path(__file__).resolve().parents[2] / ".claude/hillclimb/correction-judge/baseline/results.jsonl"
_HOLDOUT_EVAL_SET = (
    "holdout682",
    "bench/holdout_682/holdout_682_eval_set.jsonl",
    ".claude/hillclimb/correction-judge/holdout682-after/results.jsonl",
)


def _capture_eval_candidates() -> List[Path]:
    """評価セットの探索順を返す（checkout 同梱 → git 管理外の共有 DATA_DIR）。

    評価セットは他PJの生発話を含むため git 管理外（`.gitignore:25`）で、共有 checkout に
    しか実体が無い。参照先を checkout 相対だけにすると worktree・他マシン・fresh clone から
    柱1が測定不能になるため、共有 DATA_DIR 配下も探す（#601）。

    **DATA_DIR は import 時に固定しない。** テストの HOME 隔離が DATA_DIR を tmp へ
    rebase するため、module import 時に解決すると隔離前の実パスを掴む。
    """
    import rl_common

    return [
        _CAPTURE_EVAL_PATH,
        Path(rl_common.DATA_DIR) / "bench" / _CAPTURE_EVAL_FILENAME,
    ]


def _capture_recall_from(path: Path) -> Dict[str, Any]:
    """1つの候補パスから捕捉率を算出する。使えなければ measured=False を返す。"""
    try:
        rows = load_capture_eval_set(path)
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


def _build_capture_recall() -> Dict[str, Any]:
    """実在する候補を順に試し、最初に測れたものを返す。

    **「最初に実在した候補」で打ち切らない**（#602 レビュー巡1 [Must]）。評価セットは
    git 管理外なので、checkout 側に更新前の古い実体や誤配置が残る状態は通常運用で
    到達しうる。1件目で確定すると、共有 DATA_DIR に正しい実体があっても壊れた側に
    shadow されて測定不能になる。
    """
    present = [c for c in _capture_eval_candidates() if c.exists()]
    if not present:
        return {"measured": False, "reason": "評価セットなし"}
    failure: Dict[str, Any] = {"measured": False, "reason": "評価セットなし"}
    for path in present:
        outcome = _capture_recall_from(path)
        if outcome["measured"]:
            return outcome
        failure = outcome
    return failure

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
    # None は「該当なし」ではなく取得不能。通常の評価済み・該当なしは [] で区別する。
    "coverage_gaps": None,
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
    judge_results_path: Path = _JUDGE_RESULTS_PATH,
) -> Dict[str, Any]:
    """戦果ボードを決定論生成する（read-only・LLM 非依存）。

    Args:
        slug: PJ slug。optimize_history_store.load_history と telemetry_query の
            project フィルタの両方に共通で使う。リポジトリ直下（worktree でない）呼び出しでは
            pj_slug.resolve_pj_slug の basename と telemetry_query の project-name（ディレクトリ
            basename）が一致する前提（既存コードの growth_report.py 等と同じ簡略化）。
        now: 基準時刻（省略時は現在の UTC）。テストの決定論性のため注入可能にする。
        project_root: 柱2の集計対象。sibling worktree から実行すると same-project の反映が
            脱落しうるが、その場合も現在は measured=True になる。

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
    try:
        capture_union = load_capture_union(_capture_eval_candidates(), judge_results_path)
    except Exception:
        capture_union = {"measured": False, "reason": "判定結果の読込失敗"}
    try:
        import rl_common
        holdout_name, eval_relative, results_relative = _HOLDOUT_EVAL_SET
        capture_holdout = load_capture_union(
            [Path(rl_common.DATA_DIR) / eval_relative],
            Path(__file__).resolve().parents[2] / results_relative,
            eval_set_name=holdout_name,
        )
    except Exception:
        capture_holdout = {"measured": False, "reason": "確認用セットの読込失敗"}

    pillar2_fallback = {
        "count": 0,
        "measured": False,
        "pre_scheme_excluded_count": None,
        "health": {"degraded": True},
        "not_measured": {
            target: {"reason": details["reason"]}
            for target, details in PILLAR2_NOT_MEASURED_TARGETS.items()
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
        "capture_union": capture_union,
        "capture_holdout": capture_holdout,
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


def render_results_board(board: Dict[str, Any]) -> List[str]:
    """戦果ボードの markdown ブロックを生成する（判定+行動を最上部・証拠は直下）。"""
    decisions = board["decisions"]

    lines = ["## 🏆 戦果ボード", ""]
    scopes = board.get("measurement_scopes") or pillar_scopes(board.get("slug", "(unknown)"))
    measurements = board.get("measurements") or {}

    holdout = board.get("capture_holdout") or {"measured": False, "reason": "確認用セットの測定値なし"}
    if holdout.get("measured") and holdout.get("display", True):
        low, high = holdout["recall_ci"]
        jst_time = "〜".join(datetime.fromisoformat(t).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST") for t in holdout["generated_at"].split("〜"))
        lines.append(f"**柱1 捕捉率（確認用セット・調整に不使用）: {holdout['caught']}/{holdout['positives']} = {holdout['recall']:.1%}** "
                     f"（Wilson 95% CI {low:.1%}–{high:.1%}・精度 {holdout['precision']:.1%}）柱1の主指標")
        lines.append(f"内訳: 正規表現 {holdout['regex_caught']}/{holdout['positives']}・AI 候補（朝の y/n 前・未保存）{holdout['judge_caught']}/{holdout['positives']} "
                     f"／ AI 判定の来歴: {jst_time}・版 {holdout['harness_sha'][:8]}・モデル別名 {holdout['model']}・バッチ設定 {holdout['batch_size']}")
    else:
        lines.append(f"**柱1 捕捉率（確認用セット・調整に不使用）: 測定不能（{holdout.get('reason', '来歴不明')}）**")
    union = board.get("capture_union") or {"measured": False, "reason": "a0 の測定値なし"}
    if union.get("measured"):
        jst_time = "〜".join(datetime.fromisoformat(t).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST") for t in union["generated_at"].split("〜"))
        lines.append(f"参考: 調整に使った評価セットでの値 {union['caught']}/{union['positives']} = {union['recall']:.1%}（上振れするため到達の根拠にしない）")
        lines.append(f"a0 の AI 判定の来歴: {jst_time}・版 {union['harness_sha'][:8]}・モデル別名 {union['model']}・バッチ設定 {union['batch_size']}")
    else:
        reason = union.get("reason", "来歴不明").split("。再測:", 1)[0].rstrip("。")
        lines.append(f"参考: 調整に使った評価セットでの値 測定不能（{reason}。到達の根拠にしない）")
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
        "pre_scheme_excluded_count": None,
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
