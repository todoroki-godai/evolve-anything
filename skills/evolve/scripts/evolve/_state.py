#!/usr/bin/env python3
"""state / データ十分性 / fitness 系 helper（#531 PR 4/8 で evolve/__init__.py から抽出）。

evolve 実行状態の load/save、観測量（セッション/観測/全観測）の集計、データ十分性判定、
プロジェクト固有 fitness 関数の有無チェックをまとめる。振る舞いはゼロ変更で、__init__.py が
全名前を re-export して `from evolve import X` の後方互換と setattr(evolve, ...) 束縛を保つ。

⚠️ DATA_DIR / EVOLVE_STATE_FILE の遅延参照（#517 契約）:
本 module は module-top で `from ._env import DATA_DIR / EVOLVE_STATE_FILE` してはいけない。
それらを掴むと _state は reimport されず frozen 化し、test_evolve_data_dir_env が
`del sys.modules["evolve"]` + reimport で CLAUDE_PLUGIN_DATA を再評価する契約が壊れる
（__init__ は再解決した最新値を package 属性に束縛するが、_state が frozen 値を握ると
反映されない）。よって load/save/count 系は呼び出し時に `import evolve as _ev` で
`_ev.DATA_DIR` / `_ev.EVOLVE_STATE_FILE` を遅延参照する（PR#1 の束縛フェンスと同型）。
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT


def load_evolve_state() -> Dict[str, Any]:
    """前回の evolve 実行状態を読み込む。"""
    import evolve as _ev
    if not _ev.EVOLVE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(_ev.EVOLVE_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_evolve_state(state: Dict[str, Any]) -> None:
    """evolve 実行状態を保存する。"""
    import evolve as _ev
    _ev.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ev.EVOLVE_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def count_new_sessions() -> int:
    """前回 evolve 実行以降のセッション数を数える。

    sessions テーブル（DuckDB）と usage.jsonl 両方からユニーク session_id を集計する。
    backfill データ（usage 経由）も含めてカウントできる。
    """
    import evolve as _ev
    state = load_evolve_state()
    last_run = state.get("last_run_timestamp", "")
    session_ids: set = set()

    # sessions テーブルから集計
    sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
    import session_store
    for rec in session_store.query(since=last_run):
        sid = rec.get("session_id", "")
        if sid:
            session_ids.add(sid)

    # usage.jsonl からもユニーク session_id を集計（backfill 対応）
    usage_file = _ev.DATA_DIR / "usage.jsonl"
    if usage_file.exists():
        for line in usage_file.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                ts = rec.get("timestamp", "")
                if ts > last_run:
                    sid = rec.get("session_id", "")
                    if sid:
                        session_ids.add(sid)
            except json.JSONDecodeError:
                continue

    return len(session_ids)


def count_new_observations() -> int:
    """前回 evolve 実行以降の観測数を数える。"""
    import evolve as _ev
    state = load_evolve_state()
    last_run = state.get("last_run_timestamp", "")

    usage_file = _ev.DATA_DIR / "usage.jsonl"
    if not usage_file.exists():
        return 0

    count = 0
    for line in usage_file.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            ts = rec.get("timestamp", "")
            if ts > last_run:
                count += 1
        except json.JSONDecodeError:
            continue
    return count


def _build_trigger_summary() -> Dict[str, Any]:
    """直近のトリガー発火回数・最終発火日時をまとめる。"""
    state = load_evolve_state()
    history = state.get("trigger_history", [])
    if not history:
        return {"total_fires": 0, "last_fired": None}
    return {
        "total_fires": len(history),
        "last_fired": history[-1].get("timestamp"),
        "recent_reasons": [h.get("reason") for h in history[-5:]],
    }


def compute_trend(
    current: Dict[str, Any],
    previous: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """前回 snapshot との差分を算出しトレンド情報を返す。

    Args:
        current: 今回の tool_usage_snapshot (builtin_replaceable, sleep_patterns, bash_ratio)
        previous: 前回の tool_usage_snapshot (None = 初回)

    Returns:
        各指標のトレンド情報を含む辞書
    """
    if previous is None:
        return {"has_previous": False}

    trends: Dict[str, Any] = {"has_previous": True}

    # 件数ベースの指標
    for key in ("builtin_replaceable", "sleep_patterns"):
        cur = current.get(key, 0)
        prev = previous.get(key, 0)
        diff = cur - prev
        if prev > 0:
            pct = diff / prev * 100
        else:
            pct = 0.0 if diff == 0 else 100.0

        if diff < 0:
            label = f"↓ {abs(diff)}件減少 ({pct:+.0f}%)"
        elif diff > 0:
            label = f"↑ {diff}件増加 ({pct:+.0f}%)"
        else:
            label = "→ 変化なし"

        trends[key] = {"current": cur, "previous": prev, "diff": diff, "pct": pct, "label": label}

    # ratio ベースの指標 (bash_ratio)
    cur_ratio = current.get("bash_ratio", 0.0)
    prev_ratio = previous.get("bash_ratio", 0.0)
    pp_diff = (cur_ratio - prev_ratio) * 100  # パーセントポイント差

    if abs(pp_diff) < 0.05:
        ratio_label = f"{cur_ratio * 100:.1f}% → 変化なし"
    elif pp_diff < 0:
        ratio_label = f"{prev_ratio * 100:.1f}% → {cur_ratio * 100:.1f}% (↓{abs(pp_diff):.1f}pp)"
    else:
        ratio_label = f"{prev_ratio * 100:.1f}% → {cur_ratio * 100:.1f}% (↑{pp_diff:.1f}pp)"

    trends["bash_ratio"] = {
        "current": cur_ratio,
        "previous": prev_ratio,
        "pp_diff": pp_diff,
        "label": ratio_label,
    }

    return trends


def check_data_sufficiency() -> Dict[str, Any]:
    """観測データの十分性をチェックする。

    判定基準: セッション3+かつ観測10+、
    または全観測が20+（backfill で大量データがある場合を考慮）。
    """
    # 内部 helper は package namespace 経由で呼ぶ（_ev.<name>）。分割前は同一 module
    # globals 解決だったため `mock.patch.object(evolve, "count_new_sessions", ...)` が
    # 効いた（test_evolve_backfill_suggestion）。_state へ移しても束縛先を evolve
    # package に集約してこの差し替え契約を保つ。
    import evolve as _ev
    sessions = _ev.count_new_sessions()
    observations = _ev.count_new_observations()

    # 全データ（last_run 以前も含む）の観測数もフォールバックで確認
    total_observations = _ev._count_total_observations()

    sufficient = (
        (sessions >= 3 and observations >= 10)
        or total_observations >= 20
    )

    # テレメトリが完全に空（未取得）= 「単なるデータ不足」と区別する。
    # 初回導入直後に observe hooks のデータがまだ無い状態。この場合は
    # backfill で既存セッション履歴を取り込むのが正しい初手なので提案する。
    telemetry_empty = total_observations == 0 and sessions == 0
    backfill_recommended = telemetry_empty

    # 前回 evolve 以降の新規観測がゼロ（過去データはある）= フルパイプラインを
    # 回しても結局すべて keep/評価のみの no-op になりやすい状態（#396）。
    # backfill 推奨（テレメトリ空）とは別物。SKILL.md はこのフラグを見て
    # 「軽量モード（observability surface のみで重い LLM フェーズ/batch_guard を
    # スキップ提案）」をユーザーに提示する。べき等性は保ちつつ操作コストを下げる。
    no_new_observations = (
        sessions == 0 and observations == 0 and total_observations > 0
    )

    if sufficient and no_new_observations:
        msg = (
            f"前回 evolve 以降の新規観測なし（0 セッション / 0 新規観測, 全{total_observations}）。"
            "過去データは十分ですが、フル実行は no-op になりやすいため軽量モードを検討してください。"
        )
    elif sufficient:
        msg = f"{sessions} セッション, {observations} 新規観測 (全{total_observations}) — データ十分"
    elif telemetry_empty:
        # #486: 旧 /evolve-anything:backfill スキルは #215（v1.65.1）で CLI 削除済みの幻。
        # 現行は observe hooks がセッションを進行形で観測し、evolve が batch ingest する。
        # 初回はしばらく通常運用してから /evolve-anything:evolve を回せばよい。
        msg = (
            "テレメトリが空です（観測データ未取得）。"
            "observe hooks が今後のセッションを自動記録します。"
            "数セッション利用してから /evolve-anything:evolve を実行してください。"
        )
    else:
        msg = f"前回 evolve 以降: {sessions} セッション, {observations} 観測 (全{total_observations})"

    return {
        "sessions": sessions,
        "observations": observations,
        "total_observations": total_observations,
        "sufficient": sufficient,
        "telemetry_empty": telemetry_empty,
        "backfill_recommended": backfill_recommended,
        "no_new_observations": no_new_observations,
        "message": msg,
    }


def _count_total_observations() -> int:
    """usage.jsonl の全レコード数を返す。"""
    import evolve as _ev
    usage_file = _ev.DATA_DIR / "usage.jsonl"
    if not usage_file.exists():
        return 0
    return sum(
        1 for line in usage_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def check_fitness_function(project_dir: Optional[str] = None) -> Dict[str, Any]:
    """プロジェクト固有の fitness 関数の有無をチェックする。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    fitness_dir = proj / "scripts" / "rl" / "fitness"
    criteria_file = proj / ".claude" / "fitness-criteria.md"

    fitness_files = []
    if fitness_dir.exists():
        fitness_files = [f.stem for f in fitness_dir.glob("*.py") if f.name != "__init__.py"]

    return {
        "has_fitness": len(fitness_files) > 0,
        "has_criteria": criteria_file.exists(),
        "fitness_functions": fitness_files,
        "fitness_dir": str(fitness_dir),
    }


# #105: fitness 生成提案（SKILL.md Step 2）を fitness_evolution の structural 判定と整合させる。
_FITNESS_GENERATION_SKIP_NOTE = (
    "fitness_evolution が構造的スキップ（skill 提案が構造的に出ない PJ）と判定。"
    "fitness を生成しても calibration 母集団が貯まらず空振りになりやすい。"
    "デフォルトはスキップ推奨（生成は禁止しない）。"
)


def annotate_fitness_generation_advice(
    fitness_phase: Optional[Dict[str, Any]],
    fitness_evo_result: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """`phases.fitness` に `generation_advised` を back-annotate する（#105）。

    has_fitness=false の PJ で、fitness_evolution が構造的スキップ
    （skill_evolve_not_scored / bootstrap）と判定していれば `generation_advised=false` +
    `generation_note` を付与する。SKILL.md Step 2 はこれを見て「効果が薄い見込み」と注記し
    デフォルトを skip 寄りにする（「fitness 生成しろ」と「fitness は使わない設計」の同時提示矛盾を断つ）。
    生成自体は禁止しない。has_fitness=true / fitness_phase 不正時は無変更。副作用で dict を書き換える。
    """
    if not isinstance(fitness_phase, dict) or fitness_phase.get("has_fitness"):
        return fitness_phase
    try:
        from fitness_evolution import is_structural_skip
    except Exception:
        return fitness_phase
    if is_structural_skip(fitness_evo_result):
        fitness_phase["generation_advised"] = False
        fitness_phase["generation_note"] = _FITNESS_GENERATION_SKIP_NOTE
    else:
        fitness_phase["generation_advised"] = True
    return fitness_phase
