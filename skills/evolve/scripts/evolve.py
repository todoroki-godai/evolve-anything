#!/usr/bin/env python3
"""Evolve オーケストレーター。

Observe データ確認 → Discover → Enrich → Optimize → Reorganize → Prune(+Merge) →
Fitness Evolution → Report の全フェーズを1つのコマンドで実行する。
"""
import json
import re
import sys
import warnings as _warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"))

DATA_DIR = Path.home() / ".claude" / "rl-anything"
EVOLVE_STATE_FILE = DATA_DIR / "evolve-state.json"

# Module-level references for testability (populated on first call)
skill_evolve_assessment = None
collect_issues = None

ENV_TIER_THRESHOLDS = {"medium": 20, "large": 50}


def _resolve_evolve_slug(project_root: Path) -> str:
    """worktree 安全な PJ slug を返す（#408-C）。

    optimize_history_store.resolve_slug（git-common-dir 親で正規化、ADR-031）を
    再利用する。worktree から呼んでも本体 repo 名に正規化される。解決不能なら
    "_unattributed"。result metadata 用なので import 失敗時も例外を投げない。
    """
    try:
        from optimize_history_store import resolve_slug

        return resolve_slug(project_root)
    except Exception:
        return "_unattributed"


def _surface_constitutional_status(
    project_dir: Path,
    warning_sink: List[Dict[str, Any]],
    observability: Optional[Dict[str, Any]],
) -> Optional[str]:
    """constitutional の cache 状態を warnings/observability に昇格する（#408-D）。

    constitutional は [ADR-037] で LLM 全廃済み。cache 未生成/全 miss だと None を返すが、
    これは「評価失敗」ではなく「stale → refresh 必要」。従来は warnings にも observability にも
    乗らず、レポート本文だけが（誤って）「LLM 評価に失敗しました」と出して取り違えを招いていた。
    ここで cache-only 再集約（LLM 非依存・安価）して状態を昇格する。

    Returns: 追加した surface 行（None=正常算出で surface 不要、または import 失敗）。
    """
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "rl"))
        from fitness.constitutional import compute_constitutional_score

        con = compute_constitutional_score(project_dir)
    except Exception:
        # 状態 surface は best-effort。本流（audit/discover 等）には影響させない。
        return None

    if not (con is None or (isinstance(con, dict) and con.get("overall") is None)):
        return None  # 正常算出 — surface 不要

    skip_reason = con.get("skip_reason") if isinstance(con, dict) else None
    if skip_reason == "low_coverage":
        line = "Constitutional: coverage 不足でスキップ（評価対象が閾値未満）"
    else:
        line = (
            "Constitutional: cache stale/全 miss で未算出（失敗ではない）。"
            "audit Step 3.5 の 2 相 refresh で cache 再生成を推奨"
        )
    warning_sink.append({"category": "constitutional_cache", "message": line})
    if isinstance(observability, dict):
        observability["constitutional"] = [line]
    return line


def _count_env_artifacts(project_root: Path) -> Dict[str, int]:
    """tier 判定に使うスキル数・ルール数の内訳を返す（決定論）。

    env_tier の決定根拠（#408-E）を出力に含めるため、tier 計算と内訳算出を共有する。
    """
    skills_dir_count = 0
    skills_dir = project_root / ".claude" / "skills"
    if skills_dir.is_dir():
        skills_dir_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())

    claude_md_skills = 0
    claude_md = project_root / "CLAUDE.md"
    if claude_md.is_file():
        try:
            content = claude_md.read_text(encoding="utf-8")
            in_skills = False
            for line in content.splitlines():
                # Skills セクション開始
                if re.match(r"^#{1,3}\s+.*[Ss]kills?\b|^#{1,3}\s+.*スキル", line):
                    in_skills = True
                    continue
                # 別のセクション開始で終了
                if in_skills and re.match(r"^#{1,3}\s+", line):
                    in_skills = False
                    continue
                # リスト項目をカウント
                if in_skills and re.match(r"^\s*[-*]\s+", line):
                    claude_md_skills += 1
        except (OSError, UnicodeDecodeError):
            pass

    rules_count = 0
    rules_dir = project_root / ".claude" / "rules"
    if rules_dir.is_dir():
        rules_count = sum(1 for f in rules_dir.iterdir() if f.is_file())

    total = skills_dir_count + claude_md_skills + rules_count
    return {
        "skills_dir": skills_dir_count,
        "claude_md_skills": claude_md_skills,
        "rules": rules_count,
        "total": total,
    }


def _tier_from_count(count: int) -> str:
    if count >= ENV_TIER_THRESHOLDS["large"]:
        return "large"
    if count >= ENV_TIER_THRESHOLDS["medium"]:
        return "medium"
    return "small"


def _compute_env_tier(project_root: Path) -> str:
    """環境のスキル数+ルール数からtierを判定。

    スキル数: .claude/skills/ 配下のディレクトリ数 + CLAUDE.md の Skills セクション記載数
    ルール数: .claude/rules/ 配下のファイル数

    Returns: "small" | "medium" | "large"
    """
    return _tier_from_count(_count_env_artifacts(project_root)["total"])


def load_evolve_state() -> Dict[str, Any]:
    """前回の evolve 実行状態を読み込む。"""
    if not EVOLVE_STATE_FILE.exists():
        return {}
    try:
        return json.loads(EVOLVE_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_evolve_state(state: Dict[str, Any]) -> None:
    """evolve 実行状態を保存する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVOLVE_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def count_new_sessions() -> int:
    """前回 evolve 実行以降のセッション数を数える。

    sessions テーブル（DuckDB）と usage.jsonl 両方からユニーク session_id を集計する。
    backfill データ（usage 経由）も含めてカウントできる。
    """
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
    usage_file = DATA_DIR / "usage.jsonl"
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
    state = load_evolve_state()
    last_run = state.get("last_run_timestamp", "")

    usage_file = DATA_DIR / "usage.jsonl"
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
    sessions = count_new_sessions()
    observations = count_new_observations()

    # 全データ（last_run 以前も含む）の観測数もフォールバックで確認
    total_observations = _count_total_observations()

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
        msg = (
            "テレメトリが空です（観測データ未取得）。"
            "初回セットアップとして /rl-anything:backfill で既存セッション履歴を取り込んでください。"
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
    usage_file = DATA_DIR / "usage.jsonl"
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


@contextmanager
def _capture_warnings(sink: List[Dict[str, Any]]):
    """フェーズ実行中に出た警告（scipy RuntimeWarning(NaN) 等）を sink に記録する（#341）。

    phase が throw しない警告は phase.error に乗らず stderr に流れて消える。
    self_analysis（evolve_introspect）が `result["warnings"]` を読んで surface できるよう、
    ここで決定論的にシリアライズして溜める。LLM 非依存・副作用は sink への append のみ。
    """
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        try:
            yield
        finally:
            for w in caught:
                try:
                    cat = getattr(w.category, "__name__", str(w.category))
                    sink.append({
                        "category": cat,
                        "message": str(w.message),
                        "filename": str(getattr(w, "filename", "")),
                        "lineno": int(getattr(w, "lineno", 0) or 0),
                    })
                except Exception:
                    # 記録失敗で本流を壊さない（警告は best-effort 観測）。
                    continue


def run_evolve(
    project_dir: Optional[str] = None,
    dry_run: bool = False,
    skip_skills: Optional[set] = None,
    skip_llm_evolve: bool = False,
    confirmed_batch: bool = False,
    observe_first: bool = False,
) -> Dict[str, Any]:
    """全フェーズを実行する。

    Args:
        project_dir: プロジェクトディレクトリ
        dry_run: True の場合、レポートのみ出力し変更は行わない
        observe_first: True の場合、安価な observe + fitness ゲートだけ算出して
            重いフェーズ（discover/audit/skill_evolve/remediation/prune…）を回さず
            early-return する（#407）。SKILL Step 1 の lightweight/skip 分岐を
            「フル分析コストを払う前」に効かせるための pre-flight モード。

    Returns:
        各フェーズの結果を含む辞書
    """
    _generated_at = datetime.now(timezone.utc).isoformat()
    proj_root = Path(project_dir) if project_dir else Path.cwd()
    result: Dict[str, Any] = {
        "timestamp": _generated_at,
        # --- 結果の同一性 metadata（#408 A/B）: 読み手が「どの PJ・いつ・本実行か」を
        #     skill_name からの推測でなくトップレベルで機械検証できるようにする。---
        "generated_at": _generated_at,
        "slug": _resolve_evolve_slug(proj_root),
        "project_dir": str(proj_root.resolve()),
        "dry_run": dry_run,
        "phases": {},
    }
    # フェーズ実行中の警告（stderr に流れて消える scipy RuntimeWarning(NaN) 等）を
    # 溜める sink。self_analysis が result["warnings"] を読んで surface する（#341）。
    _warning_sink: List[Dict[str, Any]] = []

    # Tier 計算（各 Phase の深度制御に使用）。決定根拠（#408-E）も出力に含める。
    _tier_breakdown = _count_env_artifacts(proj_root)
    tier = _tier_from_count(_tier_breakdown["total"])
    result["env_tier"] = tier
    result["env_tier_reason"] = {
        "count": _tier_breakdown["total"],
        "breakdown": _tier_breakdown,
        "thresholds": dict(ENV_TIER_THRESHOLDS),
    }

    # Phase 1: Observe データ確認
    sufficiency = check_data_sufficiency()
    result["phases"]["observe"] = sufficiency

    if not sufficiency["sufficient"]:
        if sufficiency.get("backfill_recommended"):
            # テレメトリ未取得 = 初回導入直後。backfill を先に実行するよう提案する
            # （自動実行はせず、副作用が大きいためユーザー判断に委ねる）。
            result["phases"]["observe"]["action"] = "backfill_recommended"
        else:
            # スキップ推奨だがユーザー選択に委ねる
            result["phases"]["observe"]["action"] = "skip_recommended"
        _warn_insufficient_data(sufficiency)
    elif sufficiency.get("no_new_observations"):
        # データは十分だが前回 evolve 以降の新規観測がゼロ（#396）。フル実行は
        # no-op になりやすいので軽量モードを提案する（SKILL.md が surface）。
        # 自動スキップはしない — べき等性は保ちつつユーザーに選択させる。
        result["phases"]["observe"]["action"] = "lightweight_recommended"

    # Phase 1.5: Fitness 関数チェック
    fitness_check = check_fitness_function(project_dir)
    result["phases"]["fitness"] = fitness_check

    # Phase 1.6: observe-first pre-flight early-return（#407）
    # observe（新規観測の有無）と fitness はどちらもファイル走査だけで安価に算出できる。
    # observe_first 時はここで打ち切り、重いフェーズ（discover/audit/skill_evolve/
    # remediation/reorganize/prune…）を回さずに action だけ返す。SKILL Step 1 が action を
    # 見て「軽量/スキップ/フル」を選び、フルが必要なときだけ重い dry-run を別途走らせる。
    # これで lightweight_recommended の判定が「フル分析コストを払う前」に効く。
    if observe_first:
        result["observe_first"] = True
        result["skipped_heavy_phases"] = True
        return result

    # Phase 2: Discover
    try:
        from discover import run_discover
        project_root = Path(project_dir) if project_dir else None
        discover_result = run_discover(project_root=project_root, tool_usage=True)
        result["phases"]["discover"] = discover_result
    except Exception as e:
        result["phases"]["discover"] = {"error": str(e)}

    # Phase 2.5: Enrich（discover に統合済み — discover 出力から取得）
    discover_data = result["phases"].get("discover", {})
    result["phases"]["enrich"] = {
        "enrichments": discover_data.get("matched_skills", []),
        "unmatched_patterns": discover_data.get("unmatched_patterns", []),
        "total_enrichments": len(discover_data.get("matched_skills", [])),
        "total_unmatched": len(discover_data.get("unmatched_patterns", [])),
        "skipped_reason": "no_patterns_available" if not discover_data.get("matched_skills") and not discover_data.get("unmatched_patterns") else None,
    }

    # Phase 2.6: Skill Triage（trigger eval + CREATE/UPDATE/SPLIT/MERGE/OK 判定）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from skill_triage import triage_all_skills
        from telemetry_query import query_sessions, query_usage
        proj = Path(project_dir) if project_dir else Path.cwd()
        sessions = query_sessions(project=proj.name)
        usage_data = query_usage(project=proj.name)
        missed = discover_data.get("missed_skill_opportunities", [])
        triage_result = triage_all_skills(
            sessions=sessions,
            usage=usage_data,
            missed_skills=missed,
            project_root=proj,
            dry_run=dry_run,  # #308: --dry-run 時は triage_ledger に書き込まない
        )
        # #433 先行スコープ: corrections 非依存の2軸（一発成功率 / rework 率）を
        # スキル単位に分解し、triage 候補の順位に自動入力（advisory→閉ループ配線）。
        # in-memory の sessions/usage_data を渡すので DATA_DIR 再読込なし（dry-run 安全）。
        from audit.outcome_attribution import apply_outcome_ranking
        triage_result = apply_outcome_ranking(
            triage_result, usage=usage_data, sessions=sessions
        )
        result["phases"]["skill_triage"] = triage_result
    except Exception as e:
        result["phases"]["skill_triage"] = {"error": str(e), "skipped": True}

    # Phase 2.65: Skill Quality Pattern Detection（テレメトリ不要）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from instruction_patterns import detect_patterns, check_defaults_first, analyze_context_efficiency
        from quality_engine import recommend_patterns

        proj = Path(project_dir) if project_dir else Path.cwd()
        claude_md_path = proj / "CLAUDE.md"
        claude_md_content = claude_md_path.read_text(encoding="utf-8") if claude_md_path.is_file() else None

        quality_results = {}
        skills_dir = proj / ".claude" / "skills"
        if skills_dir.is_dir():
            for skill_dir in skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                content = skill_md.read_text(encoding="utf-8")
                patterns = detect_patterns(content)
                defaults = check_defaults_first(content)
                ctx_eff = analyze_context_efficiency(content, claude_md_content)
                recommendation = recommend_patterns(patterns, content)
                quality_results[skill_dir.name] = {
                    "patterns": patterns,
                    "defaults_first_score": defaults,
                    "context_efficiency": ctx_eff,
                    "recommendation": recommendation,
                }
        result["phases"]["quality_patterns"] = quality_results
    except Exception as e:
        result["phases"]["quality_patterns"] = {"error": str(e)}

    # Phase 2.7: Layer Diagnose（全レイヤー診断）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from layer_diagnose import diagnose_all_layers
        proj = Path(project_dir) if project_dir else Path.cwd()
        layer_result = diagnose_all_layers(proj)
        result["phases"]["layer_diagnose"] = layer_result
    except Exception as e:
        result["phases"]["layer_diagnose"] = {"error": str(e)}

    # Phase 3: Audit
    try:
        from audit import run_audit
        # MemTrace(#264) と constitutional(slop_detector #255 を 10% ブレンド) は opt-in だが、
        # evolve では既定で有効化し「evolve するだけで全機能が効く」状態にする。
        # MemTrace は決定論(LLM ゼロ)、constitutional は haiku×最大4 だがレイヤ単位キャッシュで
        # 通常 0〜1 コール（constitutional_cache.json）。
        audit_report = run_audit(
            project_dir, memory_trace=True, constitutional_score=True
        )
        result["phases"]["audit"] = {"report": audit_report}
    except Exception as e:
        result["phases"]["audit"] = {"error": str(e)}

    # Observability contract（#272 後続）: audit の 217KB markdown に埋もれて surface されない
    # observability 行（unmanaged_pitfalls / glossary_drift …）を構造化フィールドに昇格させ、
    # assistant が必ずサマリに出せるようにする。silence != evaluated 原則を契約として明文化。
    try:
        from audit import collect_observability

        _obs_proj = Path(project_dir) if project_dir else Path.cwd()
        result["observability"] = collect_observability(_obs_proj)
    except Exception as e:
        result["observability"] = {"error": str(e)}

    # Phase 3.2: Constitutional cache 状態の surface（#408-D）
    _surface_constitutional_status(
        Path(project_dir) if project_dir else Path.cwd(),
        _warning_sink,
        result.get("observability"),
    )

    # Phase 3.3: Skill Quality Trace Analysis（テレメトリ依存 — data_sufficiency 後）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from quality_engine import analyze_traces, compute_overall_score, record_quality_score

        proj = Path(project_dir) if project_dir else Path.cwd()
        quality_patterns = result["phases"].get("quality_patterns", {})
        trace_results = {}
        for skill_name, qr in quality_patterns.items():
            if isinstance(qr, dict) and "patterns" in qr:
                trace = analyze_traces(skill_name, project=proj.name)
                pattern_score = qr["patterns"].get("score", 0.0)
                confusion = trace.get("confusion_score") if trace else None
                ctx_eff = qr.get("context_efficiency", {}).get("efficiency_score", 0.5)
                defaults = qr.get("defaults_first_score", 1.0)
                overall = compute_overall_score(pattern_score, confusion, ctx_eff, defaults)
                trace_results[skill_name] = {
                    "confusion_score": confusion,
                    "overall_score": overall,
                }
                if not dry_run:
                    record_quality_score(skill_name, {
                        "pattern_score": pattern_score,
                        "confusion_score": confusion,
                        "context_efficiency": ctx_eff,
                        "defaults_first_score": defaults,
                        "overall": overall,
                    })
        result["phases"]["quality_traces"] = trace_results
    except Exception as e:
        result["phases"]["quality_traces"] = {"error": str(e)}

    # Phase 3.4: Skill Self-Evolution Assessment（適性判定 — remediation の前に実行）
    try:
        import evolve as _evolve_mod
        if _evolve_mod.skill_evolve_assessment is None:
            sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
            from skill_evolve import skill_evolve_assessment as _sea
            _evolve_mod.skill_evolve_assessment = _sea
        skill_evolve_assessment = _evolve_mod.skill_evolve_assessment
        proj = Path(project_dir) if project_dir else Path.cwd()
        se_assessment = skill_evolve_assessment(
            proj, project=proj.name,
            skip_skills=skip_skills,
            skip_llm_evolve=skip_llm_evolve,
            confirmed_batch=confirmed_batch,
        )
        # _meta エントリを分離
        _excluded_meta = next((a for a in se_assessment if a.get("_meta") == "excluded_globals"), {})
        _batch_guard = next((a for a in se_assessment if a.get("_meta") == "batch_guard_trigger"), None)
        _assessments = [a for a in se_assessment if not a.get("_meta")]
        result["phases"]["skill_evolve"] = {
            "assessments": _assessments,
            "total_skills": len(_assessments),
            "already_evolved": sum(1 for a in _assessments if a.get("already_evolved")),
            "high_suitability": sum(1 for a in _assessments if a.get("suitability") == "high"),
            "medium_suitability": sum(1 for a in _assessments if a.get("suitability") == "medium"),
            "insufficient_usage": sum(1 for a in _assessments if a.get("suitability") == "insufficient_usage"),
            "rejected": sum(1 for a in _assessments if a.get("suitability") == "rejected"),
            "excluded_global_count": _excluded_meta.get("excluded_global_count", 0),
            "excluded_global_hint": _excluded_meta.get("hint", ""),
            "batch_guard_trigger": _batch_guard,
        }
    except Exception as e:
        result["phases"]["skill_evolve"] = {"error": str(e)}

    # Phase 3.5: Remediation（audit + discover + skill_evolve の結果を統合）
    try:
        import evolve as _evolve_mod2
        if _evolve_mod2.collect_issues is None:
            from audit import collect_issues as _ci
            _evolve_mod2.collect_issues = _ci
        collect_issues = _evolve_mod2.collect_issues
        from remediation import classify_issues as classify_remediation_issues
        proj = Path(project_dir) if project_dir else Path.cwd()
        issues = collect_issues(proj)

        # --- discover の tool_usage 結果を issue に変換 ---
        from issue_schema import (
            make_rule_candidate_issue,
            make_hook_candidate_issue,
            make_skill_evolve_issue,
            make_skill_triage_issue,
            VERIFICATION_RULE_CANDIDATE,
            make_verification_rule_issue,
            make_workflow_checkpoint_issue,
            make_stall_recovery_issue,
            make_skill_quality_issue,
        )
        discover_data = result["phases"].get("discover", {})
        tool_usage = discover_data.get("tool_usage_patterns", {})
        if tool_usage:
            rule_candidates = tool_usage.get("rule_candidates", [])
            rules_dir_str = str(Path.home() / ".claude" / "rules")
            for rc in rule_candidates:
                issues.append(make_rule_candidate_issue(
                    rc, rules_dir_str=rules_dir_str,
                ))
            hook_candidate = tool_usage.get("hook_candidate")
            if hook_candidate and not tool_usage.get("hook_status", {}).get("installed"):
                total_count = sum(
                    item.get("count", 0)
                    for item in tool_usage.get("builtin_replaceable", [])
                )
                issues.append(make_hook_candidate_issue(
                    hook_candidate, total_count,
                ))

        # --- skill_evolve の適性判定結果を issue に変換 ---
        se_phase = result["phases"].get("skill_evolve", {})
        for assessment in se_phase.get("assessments", []):
            suitability = assessment.get("suitability", "low")
            if suitability in ("high", "medium"):
                skill_md_path = str(Path(assessment["skill_dir"]) / "SKILL.md")
                issues.append(make_skill_evolve_issue(
                    assessment, skill_md_path,
                ))

        # --- skill_triage の結果を issue に変換 ---
        triage_phase = result["phases"].get("skill_triage", {})
        if not triage_phase.get("skipped"):
            for action in ("CREATE", "UPDATE", "SPLIT", "MERGE"):
                for triage in triage_phase.get(action, []):
                    issue = make_skill_triage_issue(triage)
                    if issue:
                        issues.append(issue)

        # --- skill_quality_pattern_gap を issue に変換 ---
        quality_patterns = result["phases"].get("quality_patterns", {})
        quality_traces = result["phases"].get("quality_traces", {})
        for skill_name, qr in quality_patterns.items():
            if isinstance(qr, dict) and "recommendation" in qr:
                rec = qr["recommendation"]
                missing_req = rec.get("required_missing", [])
                missing_rec = rec.get("recommended_missing", [])
                if missing_req:  # required が欠けている場合のみ issue 化
                    trace_info = quality_traces.get(skill_name, {})
                    issues.append(make_skill_quality_issue({
                        "skill_name": skill_name,
                        "domain": rec.get("domain", "default"),
                        "missing_required": missing_req,
                        "missing_recommended": missing_rec,
                        "pattern_score": qr["patterns"].get("score", 0.0),
                        "overall_score": trace_info.get("overall_score", 0.0),
                        "confidence": 0.7 if missing_req else 0.4,
                    }))

        # --- verification_needs を issue に変換 ---
        verification_needs = discover_data.get("verification_needs", [])
        for vn in verification_needs:
            detection_result = vn.get("detection_result", {})
            issues.append(make_verification_rule_issue(
                vn, detection_result,
                project_dir_str=str(proj),
            ))

        # --- stall_recovery_patterns を issue に変換 ---
        stall_patterns = discover_data.get("stall_recovery_patterns", [])
        for sp in stall_patterns:
            issues.append(make_stall_recovery_issue(sp))

        # --- workflow_checkpoint_gaps を issue に変換 ---
        workflow_gaps = discover_data.get("workflow_checkpoint_gaps", [])
        for wg in workflow_gaps:
            skill_name = wg.get("skill_name", "")
            for gap in wg.get("gaps", []):
                issues.append(make_workflow_checkpoint_issue(
                    gap,
                    skill_name=skill_name,
                    skill_dir=str(proj / ".claude" / "skills" / skill_name),
                ))

        classified = classify_remediation_issues(issues)

        # proposable を custom/global スコープ別に集計（#183 false positive 可視化）
        from audit import classify_artifact_origin  # artifact_scope は re-export しないため audit から直接 import
        proposable_custom = []
        proposable_global = []
        for issue in classified["proposable"]:
            file_path = issue.get("file", "")
            origin = "custom"
            if file_path:
                try:
                    origin = classify_artifact_origin(Path(file_path))
                except Exception:
                    pass
            if origin == "global":
                proposable_global.append(issue)
            else:
                proposable_custom.append(issue)

        # classified にも split リストを追加し、トップレベルの count と整合させる。
        # 修正前は classified に proposable_custom キーがなかったため、
        # jq で classified.proposable_custom を参照すると null になり、
        # phases.remediation.proposable_custom（例: 5）と食い違っていた (#353⑪)。
        classified["proposable_custom"] = proposable_custom
        classified["proposable_global"] = proposable_global

        # proposable_custom を confidence しきい値で「個別承認」「まとめてスキップ」に分割
        # （#377-3）。低 confidence FP 群（conf 0.5 中心）で per-item 承認 MUST が質問攻めに
        # なるのを防ぐ。判定は決定論コードに置き、SKILL.md は count を消費するだけにする。
        from remediation import partition_proposable_by_confidence
        _partition = partition_proposable_by_confidence(proposable_custom)
        classified["proposable_custom_individual"] = _partition["individual"]
        classified["proposable_custom_batch_skip"] = _partition["batch_skip"]

        remediation_data = {
            "total_issues": len(issues),
            "auto_fixable": len(classified["auto_fixable"]),
            "proposable": len(classified["proposable"]),
            "proposable_custom": len(proposable_custom),
            "proposable_global": len(proposable_global),
            "proposable_custom_individual": len(_partition["individual"]),
            "proposable_custom_batch_skip": len(_partition["batch_skip"]),
            "manual_required": len(classified["manual_required"]),
            "classified": classified,
        }
        result["phases"]["remediation"] = remediation_data
    except Exception as e:
        result["phases"]["remediation"] = {"error": str(e)}

    # Phase 3.7: Reorganize（Prune の前）
    # scipy のクラスタリングが NaN を含む距離行列で RuntimeWarning を出す（#340）。
    # この警告は例外として throw されず phase.error に乗らないため、capture して
    # result["warnings"] に記録し self_analysis が surface できるようにする（#341）。
    try:
        from reorganize import run_reorganize
        with _capture_warnings(_warning_sink):
            reorganize_result = run_reorganize(project_dir)
        result["phases"]["reorganize"] = reorganize_result
    except Exception as e:
        result["phases"]["reorganize"] = {"error": str(e)}

    # Phase 4: Prune（dry-run 時は候補のみ）
    try:
        from prune import run_prune
        # Reorganize の merge_groups を Prune に渡す
        reorganize_data = result["phases"].get("reorganize", {})
        merge_groups = reorganize_data.get("merge_groups", []) if not reorganize_data.get("skipped") else []
        prune_result = run_prune(project_dir, reorganize_merge_groups=merge_groups)
        result["phases"]["prune"] = prune_result
    except Exception as e:
        result["phases"]["prune"] = {"error": str(e)}

    # Phase 4.1: split↔archive 相互排他 reconcile（#301 #302 root cause fix）
    # reorganize と prune が揃った後、archive 候補のスキルを split 候補から除外する
    # （消す対象を同じ run で分割提案する矛盾を本流で解消。決定論・LLM 非依存）。
    try:
        from evolve_introspect import reconcile_split_archive
        result["phases"]["split_archive_reconcile"] = reconcile_split_archive(result)
    except Exception as e:
        result["phases"]["split_archive_reconcile"] = {"error": str(e)}

    # Phase 4.2: skill_evolve↔archive 相互排他 reconcile（#400 バグ#2）
    # archive 候補のスキルを skill_evolve（自己進化提案）から除外する。消そうとする対象に
    # 自己進化を組み込めと提案する矛盾を本流で解消（決定論・LLM 非依存）。emit_decisions より
    # 前に降格させることで矛盾候補を fitness 母集団からも外す。
    try:
        from evolve_reconcile import reconcile_skill_evolve_archive
        result["phases"]["skill_evolve_archive_reconcile"] = reconcile_skill_evolve_archive(result)
    except Exception as e:
        result["phases"]["skill_evolve_archive_reconcile"] = {"error": str(e)}

    # Phase 4.3: remediation batch_skip を observability に強制昇格（#400 バグ#6）。
    # reconcile 後の最終 batch_skip 件数を result["observability"] に注入し、Step 3.8 が必ず
    # surface する構造化経路に乗せる（SKILL.md の surface MUST 依存をやめ silence != evaluated を担保）。
    try:
        from evolve_reconcile import build_remediation_batch_skip_observability
        _bs_line = build_remediation_batch_skip_observability(result)
        if _bs_line is not None:
            obs = result.get("observability")
            if not isinstance(obs, dict) or "error" in obs:
                obs = {} if not isinstance(obs, dict) else obs
                result["observability"] = obs
            obs["remediation_batch_skip"] = _bs_line
    except Exception:
        pass

    # Phase 4.5: Pitfall Hygiene（自己進化済みスキルの剪定）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from pitfall_manager import pitfall_hygiene as run_pitfall_hygiene
        # 適性判定から frequency_scores を取得
        se_phase = result["phases"].get("skill_evolve", {})
        freq_scores = {}
        for a in se_phase.get("assessments", []):
            if a.get("scores"):
                freq_scores[a["skill_name"]] = a["scores"].get("frequency", 1)
        proj = Path(project_dir) if project_dir else Path.cwd()
        hygiene_result = run_pitfall_hygiene(proj, frequency_scores=freq_scores)
        result["phases"]["pitfall_hygiene"] = hygiene_result
    except Exception as e:
        result["phases"]["pitfall_hygiene"] = {"error": str(e)}

    # Phase 4.6: Rationalization Table（合理化防止テーブル — pitfall_hygiene から取得）
    hygiene_data = result["phases"].get("pitfall_hygiene", {})
    rt = hygiene_data.get("rationalization_table", {})
    if rt and not rt.get("data_insufficient"):
        result["phases"]["rationalization_table"] = rt

    # 用語集 seed（CONTEXT.md 不在 + jargon ≥ 閾値）は #275 で独立 phase にしていたが、
    # #278 の observability contract に統合済み（build_glossary_drift_section が emit し
    # result["observability"]["glossary_drift"] に surface）。ここでの個別 emit は不要。

    # Phase 5: Fitness Evolution（評価関数の改善チェック）
    try:
        from fitness_evolution import run_fitness_evolution, fitness_next_action
        fitness_evo_result = run_fitness_evolution()
        # #400 バグ#5: insufficient_data の結論 1 行（next_action）を現 run の提案有無で確定する。
        # skill_evolve high/medium も discover matched_skills も 0 = 提案が構造的に出ない PJ →
        # 「fitness は使わない設計。対応不要」。1 つでも提案があれば「放置でOK（継続で貯まる）」。
        if fitness_evo_result.get("status") == "insufficient_data":
            _se = result["phases"].get("skill_evolve", {})
            _disc = result["phases"].get("discover", {})
            _proposals_available = (
                _se.get("high_suitability", 0) > 0
                or _se.get("medium_suitability", 0) > 0
                or len(_disc.get("matched_skills", []) or []) > 0
            )
            fitness_evo_result["next_action"] = fitness_next_action(_proposals_available)
        result["phases"]["fitness_evolution"] = fitness_evo_result
    except Exception as e:
        result["phases"]["fitness_evolution"] = {"error": str(e)}

    # Phase 6: Self-Evolution（パイプライン自己改善）
    try:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from pipeline_reflector import (
            analyze_trajectory,
            calibrate_confidence,
            check_calibration_regression,
            check_control_chart,
            detect_false_positives,
            generate_adjustment_proposals,
            load_self_evolution_config,
            record_proposal,
        )
        se_config = load_self_evolution_config()
        analysis = analyze_trajectory(config=se_config)

        if not analysis["sufficient"]:
            result["phases"]["self_evolution"] = {
                "skipped": True,
                "reason": analysis["diagnosis"],
                "total": analysis["total"],
                "min_required": analysis["min_required"],
            }
        else:
            # Trajectory analysis
            fp_result = detect_false_positives(
                analysis.get("_outcomes", []),  # fallback: empty
                se_config,
            )

            # Calibration
            cal_result = calibrate_confidence(config=se_config)
            calibrations = cal_result.get("calibrations", {})

            # Control chart + regression check
            cc_result = check_control_chart(calibrations) if calibrations else {}
            from pipeline_reflector import load_outcomes
            outcomes = load_outcomes(lookback_days=se_config.get("analysis_lookback_days", 30))
            reg_result = check_calibration_regression(calibrations, outcomes, se_config) if calibrations else {"has_regression": False, "regressions": {}}

            # Generate proposals
            proposals = generate_adjustment_proposals(calibrations, cc_result, reg_result, se_config) if calibrations else []

            # Record proposals (unless dry-run)
            recorded_proposals = []
            for p in proposals:
                rec = record_proposal(p, dry_run=dry_run)
                if rec:
                    recorded_proposals.append(rec)

            result["phases"]["self_evolution"] = {
                "skipped": False,
                "analysis": {
                    "total": analysis["total"],
                    "by_type": analysis["by_type"],
                    "diagnosis": analysis["diagnosis"],
                },
                "false_positives": {
                    "high_confidence_count": len(fp_result.get("high_confidence_rejections", [])),
                    "systematic_flags": fp_result.get("systematic_rejections", {}),
                },
                "calibrations": calibrations,
                "control_chart": cc_result,
                "regression": reg_result,
                "proposals": proposals,
                "proposals_recorded": len(recorded_proposals),
            }
    except Exception as e:
        result["phases"]["self_evolution"] = {"error": str(e)}

    # Trigger history summary for report
    result["trigger_summary"] = _build_trigger_summary()

    # キャプチャした警告を self_analysis が読めるよう result に確定する（#341）。
    # 必ず self_analysis の前に格納する（runtime_errors が警告を surface するため）。
    result["warnings"] = _warning_sink

    # Phase 7: Self-Analysis（#299 — evolve 自身の result を自己解析し issue 候補を生成）
    # 全フェーズが揃った後に実行する（phases の error / 提案矛盾 / 改善余地 / 警告を読む）。
    # 決定論・LLM 非依存。起票自体は SKILL が人間承認の後に行う（半自動）。
    try:
        from evolve_introspect import analyze_evolve_result
        result["self_analysis"] = analyze_evolve_result(result, project_dir)
    except Exception as e:
        result["self_analysis"] = {"error": str(e)}

    # State 更新（dry-run でない場合）
    if not dry_run:
        state = load_evolve_state()
        state.update({
            "last_run_timestamp": datetime.now(timezone.utc).isoformat(),
            "sessions_processed": sufficiency["sessions"],
            "observations_processed": sufficiency["observations"],
        })
        # Self-evolution state
        se_phase = result["phases"].get("self_evolution", {})
        if not se_phase.get("skipped") and not se_phase.get("error"):
            state["last_calibration_timestamp"] = datetime.now(timezone.utc).isoformat()
            history = state.get("calibration_history", [])
            history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "calibrations": se_phase.get("calibrations", {}),
                "proposals_count": len(se_phase.get("proposals", [])),
            })
            state["calibration_history"] = history
        # Tool usage snapshot for trend tracking
        discover_data = result["phases"].get("discover", {})
        tool_usage = discover_data.get("tool_usage_patterns", {})
        if tool_usage:
            state["tool_usage_snapshot"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "builtin_replaceable": sum(
                    item.get("count", 0)
                    for item in tool_usage.get("builtin_replaceable", [])
                ),
                "sleep_patterns": sum(
                    p.get("count", 0)
                    for p in tool_usage.get("repeating_patterns", [])
                    if "sleep" in p.get("pattern", "").lower()
                ),
                "bash_ratio": (
                    tool_usage.get("bash_calls", 0) / tool_usage.get("total_tool_calls", 1)
                    if tool_usage.get("total_tool_calls", 0) > 0
                    else 0.0
                ),
            }
        save_evolve_state(state)

        # スヌーズ解除（evolve 実行完了で自動クリア）
        try:
            from trigger_engine import clear_snooze
            clear_snooze()
        except ImportError:
            pass

    # ── sessions.jsonl → sessions.db の batch ingest（#415 Phase A）────────
    # hot path（hooks）は jsonl 追記のみで、db への取り込みはこの batch 文脈に同居させる。
    # dry-run 時は DATA_DIR 非書込の規約に従い ingest しない。
    if not dry_run:
        try:
            import session_store
            ingested = session_store.ingest()
            result["sessions_ingested"] = ingested
        except Exception as e:
            print(f"[rl-anything:evolve] session ingest warning: {e}", file=sys.stderr)
            result["sessions_ingested"] = {"error": str(e)}

    # ── NFD: 結晶化イベント emit + growth キャッシュ更新 ────────
    if not dry_run:
        try:
            _emit_growth_crystallization(result, project_dir)
        except Exception as e:
            print(f"[rl-anything:evolve] growth emit warning: {e}", file=sys.stderr)

    # ── evolve 提案 accept/reject の決定論キャプチャ（#360-A, ADR-041）────────
    # 候補スキルの before_sha をキューに emit。適用実績=accept / 明示却下=reject は
    # SKILL.md Step 7.8 の drain（ingest_decisions）が optimize_history に記録する。
    # dry_run 時は pending を計算するが書き込まない（emit_decisions が内部でガード）。
    try:
        from evolve_decisions import emit_decisions
        result["evolve_decisions"] = emit_decisions(result, project_dir, dry_run=dry_run)
    except Exception as e:
        result["evolve_decisions"] = {"error": str(e)}

    return result


def _emit_growth_crystallization(result: Dict[str, Any], project_dir: Optional[str]) -> None:
    """evolve 完了時に結晶化イベントを journal に記録する。

    キャッシュ (growth-state) は更新しない — audit が唯一の権威。
    journal の phase はキャッシュからフォールバック取得する。
    """
    sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
    from growth_journal import emit_crystallization
    from growth_engine import read_cache

    project_name = Path(project_dir).name if project_dir else "unknown"

    # remediation で変更されたファイルを targets として抽出
    remediation_data = result.get("phases", {}).get("remediation", {})
    classified = remediation_data.get("classified", {})
    targets: list[str] = []
    evidence_count = 0
    for category in ("auto_fixable", "proposable"):
        for issue in classified.get(category, []):
            # line-limit fix 等の非結晶化変更は除外
            issue_type = issue.get("type", "")
            if issue_type in ("line_limit_violation", "untagged_reference_candidates"):
                continue
            target = issue.get("target", issue.get("filename", ""))
            if target:
                targets.append(target)
                evidence_count += 1

    # phase をキャッシュからフォールバック取得（audit が正確な値を持つ）
    cache = read_cache(project_name)
    phase_str = cache.get("phase", "unknown") if cache else "unknown"

    emit_crystallization(
        project=project_name,
        targets=list(set(targets)),
        evidence_count=evidence_count,
        phase=phase_str,
        source="evolve",
    )


def _warn_insufficient_data(sufficiency: Dict[str, Any]) -> None:
    """データ未取得/不足の人間向けガイダンスを stderr に出す（#336）。

    stdout は result JSON 専用の契約。ここに「テレメトリ未取得」等の非 JSON 行を
    混ぜると利用側の `json.loads` が先頭行で失敗するため、ガイダンスは必ず stderr へ。
    """
    if sufficiency.get("backfill_recommended"):
        print(f"テレメトリ未取得: {sufficiency['message']}", file=sys.stderr)
        print(
            "→ /rl-anything:backfill を先に実行してから evolve を回してください。",
            file=sys.stderr,
        )
    else:
        print(f"データ不足: {sufficiency['message']}", file=sys.stderr)
        print("スキップ推奨。--force で強制実行可能。", file=sys.stderr)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Evolve オーケストレーター")
    parser.add_argument("--project-dir", default=None, help="プロジェクトディレクトリ")
    parser.add_argument("--dry-run", action="store_true", help="レポートのみ、変更なし")
    parser.add_argument("--skip-skills", default=None, help="評価をスキップするスキル名（カンマ区切り）")
    parser.add_argument("--skip-llm-evolve", action="store_true", help="skill_evolve の LLM 評価を全スキップ")
    parser.add_argument("--confirmed-batch", action="store_true", help="batch_guard_trigger 確認済み。件数が閾値を超えても LLM 評価を続行する")
    parser.add_argument(
        "--observe-first",
        action="store_true",
        help=(
            "安価な observe + fitness ゲートだけ算出して即返す pre-flight モード（#407）。"
            "重いフェーズ（discover/audit/skill_evolve/remediation/prune…）は回さない。"
            "SKILL Step 1 がまずこれで action（lightweight/skip/full）を判定し、"
            "フルが必要なときだけ --observe-first 無しの dry-run を別途走らせる。"
        ),
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help=(
            "evolve 本体を回さず、保留中の提案 accept/reject を optimize_history に drain する（#402）。"
            "apply 後の SKILL.md Step 7.8 で `rl-evolve --drain` を1コマンド実行する。"
            "pending は marker（emit が dry-run でも記録）か --result-json から取る。"
        ),
    )
    parser.add_argument(
        "--result-json",
        default=None,
        help="--drain 時の pending ソース result JSON（未指定なら marker を使う）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "指定すると result JSON 全体をこのパスに書き、stdout には1行サマリだけ出す。"
            "巨大 JSON の stdout 一発出力が head/Bash 出力上限で途中切断され invalid JSON 化する事故を防ぐ。"
            "未指定時は従来通り full JSON を stdout に出す（後方互換）"
        ),
    )

    args = parser.parse_args()

    # #402: drain モード — evolve 本体を回さず保留中の決定を optimize_history へ記録する。
    # CLI(=tool 文脈)で走るため reader と同一 DATA_DIR に書く＝#358(DATA_DIR split)を踏まない。
    if args.drain:
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from evolve_decisions import drain_pending

        summary = drain_pending(project_dir=args.project_dir, result_json=args.result_json)
        print(json.dumps(summary, ensure_ascii=False))
        return

    _skip_skills = {s.strip() for s in args.skip_skills.split(",") if s.strip()} if args.skip_skills else None

    result = run_evolve(
        project_dir=args.project_dir,
        dry_run=args.dry_run,
        skip_skills=_skip_skills,
        skip_llm_evolve=args.skip_llm_evolve,
        confirmed_batch=args.confirmed_batch,
        observe_first=args.observe_first,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(_summarize_result(result, out_path), ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _summarize_result(result: dict, output_path: Path) -> dict:
    """`--output` 時に stdout へ出す小さな1行サマリ。

    full result を stdout に混ぜず、保存先パス・実行フェーズ一覧・env_tier だけを
    surface する。Claude は `output` のファイルを Read で読んで各フェーズや env_score を
    参照する（巨大 JSON を stdout に出すと head/Bash 上限で途中切断され invalid JSON 化するため）。

    `phases` は実フェーズ名（`result["phases"]` 配下: observe/fitness/discover/...）を列挙する。
    env_score は result のトップレベルに存在しない（audit セクション配下にネストする）ため
    サマリには出さず、トップレベルに必ずある `env_tier`（small/medium/large 等）を surface する。
    """
    if not isinstance(result, dict):
        return {"output": str(output_path), "phases": []}
    phases_obj = result.get("phases")
    phase_names = sorted(phases_obj.keys()) if isinstance(phases_obj, dict) else sorted(result.keys())
    summary: dict = {"output": str(output_path), "phases": phase_names}
    # 同一性 metadata を 1 行サマリにも出す（#408）。読み手は stdout だけで
    # 「どの PJ・いつの・本実行か」を即検証でき、stale/別 PJ ファイルの誤読を防げる。
    for k in ("slug", "project_dir", "generated_at", "dry_run", "env_tier"):
        if k in result:
            summary[k] = result[k]
    if result.get("observe_first"):
        summary["observe_first"] = True
        observe = result.get("phases", {}).get("observe", {})
        if isinstance(observe, dict) and observe.get("action"):
            summary["observe_action"] = observe["action"]
    return summary


if __name__ == "__main__":
    main()
