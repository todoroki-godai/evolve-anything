"""run_discover オーケストレータ + CLI エントリポイント (`main`)。

discover/__init__.py から re-export される（後方互換）。
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _project_transcript_dir(project_root: Path) -> Path:
    """project_root を CC のエンコード規則で ~/.claude/projects/<encoded> に変換する。

    CC は transcript ディレクトリ名を ``str(path)`` の ``/`` と ``.`` を ``-`` に置換して
    決定する。trajectory 採掘を discover と同じ project スコープに揃えることで、
    無関係な他 PJ の成功軌跡が混入する noise を防ぐ。
    """
    encoded = str(project_root).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / encoded


def _existing_skill_names(project_root: Path) -> set:
    """既存の project / global スキル名（bare 名）の集合を返す。

    ``<project>/.claude/skills/<name>/SKILL.md`` と ``~/.claude/skills/<name>/SKILL.md``
    の親ディレクトリ名を集める。``.archive`` / ``.gstack-backup`` などドット始まりの
    バックアップ・アーカイブディレクトリは除外する（実スキルではないため）。
    プラグインスキル（``:`` namespaced）は名前自体で判別できるので走査不要。
    """
    names: set = set()
    for base in (project_root / ".claude" / "skills",
                 Path.home() / ".claude" / "skills"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and not child.name.startswith(".") \
                    and (child / "SKILL.md").exists():
                names.add(child.name)
    return names


# CC 組み込みスラッシュコマンド（`<command-name>` に現れるが SKILL.md を持たない）。
# skill_extractor が採掘するため CREATE 候補から除外する。CC のバージョンアップで
# 組み込みが増えたらここに追記する。
_CC_BUILTIN_COMMANDS = frozenset({
    "loop", "model", "compact", "clear", "help", "cost", "init", "config",
    "doctor", "status", "resume", "memory", "permissions", "mcp", "agents",
    "fast", "vim", "login", "logout", "add-dir", "bug", "terminal-setup",
})


def _is_already_existing_skill(name: str, known_skills) -> bool:
    """候補スキル名が「既に存在するスキル/コマンド」かどうかを判定する。

    skill_extractor はセッション履歴の ``<command-name>`` ターンを採掘するため、
    候補は定義上すべて「過去に実行されたコマンド」= 既存である。CREATE 候補と
    して扱うべきでないものを除外する:

    - プラグイン namespaced（``plugin:skill`` のように ``:`` を含む）→ インストール済み
      プラグインスキル（例: ``evolve-anything:evolve``）。bare 名の project/global スキルに
      ``:`` は付かないため、``:`` の有無で確実に判別できる
    - ``known_skills`` に含まれる → 既存の project / global スキル（例: ``review``）
    - CC 組み込みコマンド（例: ``loop`` / ``model``）→ SKILL.md を持たないため
      known_skills では捕まらないので別途 denylist で除外する

    除外しないと「既存の loop/model/review/evolve-anything:* を新規作成せよ」という
    無意味な CREATE 提案が remediation に流れる（docs-platform evolve で 5 件検出）。
    """
    if not name:
        return True
    if ":" in name:
        return True
    if name in _CC_BUILTIN_COMMANDS:
        return True
    return name in known_skills


def _trajectory_candidates_to_missed(
    candidates: list,
    *,
    threshold: float,
    existing_skills=(),
    known_skills=(),
):
    """skill_extractor 候補を閾値フィルタし triage の missed_skills 形式へ変換する。

    Args:
        candidates: ``extract_skill_candidates`` の戻り値（skill_name / session_count /
            generalizability_score / sample_prompts / source / decomposition を持つ
            dict のリスト）。decomposition は Workflow-to-Skill の4軸分解（#381）。
        threshold: generalizability_score の下限（未満は除外）。
        existing_skills: 既に missed_skill_opportunities にある skill 名の集合。
            重複する候補は merge から除外する（surface には残す）。
        known_skills: 既存の project / global スキル名の集合。プラグイン namespaced
            （``:`` を含む）candidate と合わせて surface / merge の双方から除外する
            （既存スキルへの CREATE 提案は無意味なため）。

    Returns:
        ``(surfaced, merged)``。surfaced は閾値を満たした新規候補（decomposition 全4軸を
        保持）、merged は triage が消費する ``{"skill", "session_count",
        "triggers_matched", "routing", "attachments", ...}`` 形式のリスト。
    """
    existing = set(existing_skills)
    known = set(known_skills)
    surfaced = []
    merged = []
    for c in candidates:
        if c.get("generalizability_score", 0.0) < threshold:
            continue
        name = c.get("skill_name", "")
        # 既存スキル（プラグイン namespaced / 既存 project・global）は候補にしない
        if _is_already_existing_skill(name, known):
            continue
        surfaced.append(c)
        if name in existing:
            continue
        # Workflow-to-Skill の4軸分解から、採用判断に効く 2 軸（routing=発火文脈 /
        # attachments=anchor の広がり）を merged にも持ち上げ、triage/report で surface
        # する（#381）。候補テーブルに「どこで発火・どれだけ定着しているか」が出る
        # ようにする（attachments.session_count が単一 PJ scope でも定着度を弁別する）。
        decomposition = c.get("decomposition") or {}
        merged.append({
            "skill": name,
            "session_count": c.get("session_count", 0),
            "triggers_matched": c.get("sample_prompts", []),
            "source": c.get("source", "codeskill_extraction"),
            "generalizability_score": c.get("generalizability_score", 0.0),
            "routing": decomposition.get("routing", {}),
            "attachments": decomposition.get("attachments", {}),
            "failure_analysis": decomposition.get("failure_analysis", {}),
        })
    return surfaced, merged


def run_discover(
    *,
    project_root: Optional[Path] = None,
    include_unknown: bool = False,
    tool_usage: bool = False,
) -> Dict[str, Any]:
    """Discover を実行して候補を返す。enrich 統合済み。"""
    # 各検出関数は package 経由で参照することで `mock.patch.object(discover, "X", ...)`
    # 既存テストに追従する（同名属性の差し替えが効くようにする）
    from . import (  # noqa: PLC0415
        PLUGIN_ROOT,
        _enrich_patterns,
        detect_behavior_patterns,
        detect_error_patterns,
        detect_installed_artifacts,
        detect_missed_skills,
        detect_recommended_artifacts,
        detect_rejection_patterns,
        detect_repeated_correction_patterns,
        determine_scope,
        load_claude_reflect_data,
    )

    behavior = detect_behavior_patterns(
        project_root=project_root, include_unknown=include_unknown,
    )
    errors = detect_error_patterns(
        project_root=project_root, include_unknown=include_unknown,
    )
    rejections = detect_rejection_patterns()

    result: Dict[str, Any] = {
        "behavior_patterns": behavior,
        "error_patterns": errors,
        "rejection_patterns": rejections,
    }

    # reflect データ件数（下流 SKILL.md Step 6 / Step 10.1 が `>= 5` 比較で参照する。
    # 失敗時に欠落させると None 比較で TypeError になるため、必ずキーを残し
    # degraded sentinel -1 にフォールバックする。#526-3）
    # sentinel を int に保つのは CANONICAL 契約が同キーを kind=int と宣言しているため。
    # str sentinel だと runtime self-detect（evolve_consistency）が wrong_kind drift を
    # 誤検出し幻の「契約乖離 issue」を自作する（/review #530 で発見）。
    try:
        reflect_data = load_claude_reflect_data()
        result["reflect_data_count"] = len(reflect_data)
    except Exception as e:
        # 下流の `reflect_data_count >= 5` が None で未定義にならないよう明示値にする。
        # SKILL は数値比較の前に `< 0`（degraded）を先判定する。
        result["reflect_data_count"] = -1
        result["reflect_data_count_error"] = str(e)

    # missed skill 検出。detect_missed_skills が None / 想定キー欠落を返しても
    # try/except 外の subscript で run_discover 全体を落とさない（#521）。
    try:
        missed_result = detect_missed_skills(
            project_root=project_root,
            include_unknown=include_unknown,
        )
        if missed_result is None:
            # 契約違反（detect_missed_skills は常に dict を返す約束）。握り潰さず
            # 観測可能にする。下流の subscript 起因の `'NoneType' object is not
            # subscriptable` 全死を防ぐ（#521）。
            raise TypeError("detect_missed_skills returned None (expected dict)")
        # missed skill opportunities をレポートに含める（想定キー欠落にも耐える）
        if missed_result.get("missed"):
            result["missed_skill_opportunities"] = missed_result["missed"]
        if missed_result.get("message"):
            result["missed_skill_message"] = missed_result["message"]
    except Exception as e:
        result["missed_skill_opportunities_error"] = str(e)

    # 成功軌跡からのスキル採掘 (SIRI ①, issue #291)
    # discover は evolve が回す recurring ループなので、ここに配線することで
    # evolve のたびに自動発火する（手動 CLI 止まりにしない）。出力は triage の
    # missed_skills 形式へ変換して既存の合流ポイント (missed_skill_opportunities) に接続する。
    try:
        _se_lib = PLUGIN_ROOT / "scripts" / "lib"
        if str(_se_lib) not in sys.path:
            sys.path.insert(0, str(_se_lib))
        from skill_extractor import extract_skill_candidates
        from . import TRAJECTORY_SKILL_SCORE_THRESHOLD  # noqa: PLC0415

        # discover と同じ project スコープで採掘する（cross-PJ noise 防止）
        traj_root = _project_transcript_dir(project_root or Path.cwd())
        traj_candidates = extract_skill_candidates(projects_root=traj_root)
        existing_missed = {
            m.get("skill") for m in result.get("missed_skill_opportunities", [])
        }
        # 既存 project / global スキル名を集めて CREATE 候補から除外する
        known_skills = _existing_skill_names(project_root or Path.cwd())
        surfaced, merged = _trajectory_candidates_to_missed(
            traj_candidates,
            threshold=TRAJECTORY_SKILL_SCORE_THRESHOLD,
            existing_skills=existing_missed,
            known_skills=known_skills,
        )
        if surfaced:
            result["trajectory_skill_candidates"] = surfaced
        if merged:
            result.setdefault("missed_skill_opportunities", []).extend(merged)
    except Exception as e:
        result["trajectory_skill_candidates_error"] = str(e)

    # スコープ判断。determine_scope が例外でも握り潰さず error を残す（#521）。
    all_patterns = behavior + errors + rejections
    try:
        for p in all_patterns:
            p["scope"] = determine_scope(p)
    except Exception as e:
        result["scope_error"] = str(e)

    result["total_candidates"] = len(all_patterns)

    # enrich 統合: Jaccard 照合。_enrich_patterns が None / 想定キー欠落を返しても
    # try/except 外の subscript で run_discover を落とさない（#521）。
    active_patterns = errors + rejections if (errors or rejections) else behavior
    if active_patterns:
        try:
            enrich_result = _enrich_patterns(active_patterns, project_dir=project_root)
            if enrich_result is None:
                raise TypeError("_enrich_patterns returned None (expected dict)")
            result["matched_skills"] = enrich_result.get("matched_skills", [])
            result["unmatched_patterns"] = enrich_result.get("unmatched_patterns", [])
        except Exception as e:
            result["matched_skills_error"] = str(e)

    # 検証知見カタログの検出
    try:
        from verification_catalog import detect_verification_needs
        proj = project_root or Path.cwd()
        verification_needs = detect_verification_needs(proj)
        if verification_needs:
            result["verification_needs"] = verification_needs
    except Exception as e:
        result["verification_needs_error"] = str(e)

    tool_result = None
    if tool_usage:
        from tool_usage_analyzer import analyze_tool_usage
        tool_result = analyze_tool_usage(project_root=project_root)
        # analyze_tool_usage は常に dict を返す契約だが、None / キー欠落でも
        # subscript で落とさず .get() でガードする（#521 の一貫した防御）。
        if (tool_result or {}).get("total_tool_calls", 0) > 0:
            result["tool_usage_patterns"] = tool_result

        # rule_violation_observed レーン分離 (#522-3): 既存 rules で禁止済みのコマンドが
        # repeating_patterns で「スキル候補」提案されるのを防ぐ。rule installed != enforced
        # の違反観測は専用レーンに分離し、スキル候補から除外する。決定論・LLM 非依存。
        try:
            from rule_violation_lane import (
                default_rule_dirs,
                extract_prohibited_command_heads,
                partition_rule_violations,
            )
            _proj = project_root or Path.cwd()
            prohibited = extract_prohibited_command_heads(default_rule_dirs(_proj))
            partitioned = partition_rule_violations(
                tool_result.get("repeating_patterns", []),
                prohibited,
                project_root=_proj,
            )
            tool_result["repeating_patterns"] = partitioned["skill_candidates"]
            if partitioned["rule_violation_observed"]:
                result["rule_violation_observed"] = partitioned["rule_violation_observed"]
        except Exception as e:
            result["rule_violation_observed_error"] = str(e)

    # 推奨アーティファクト未導入チェック（tool_usage データを証拠として付加）
    recommended_missing = detect_recommended_artifacts(
        tool_usage_patterns=tool_result,
    )
    if recommended_missing:
        result["recommended_artifacts"] = recommended_missing

    # 導入済みアーティファクトの状態
    installed = detect_installed_artifacts(
        tool_usage_patterns=tool_result,
    )
    if installed:
        result["installed_artifacts"] = installed

    # pitfall 自動検出
    try:
        _lib_path = PLUGIN_ROOT / "scripts" / "lib"
        if str(_lib_path) not in sys.path:
            sys.path.insert(0, str(_lib_path))
        from pitfall_manager import extract_pitfall_candidates
        from telemetry_query import query_corrections, query_errors
        proj = project_root or Path.cwd()
        proj_name = proj.name
        corrections_data = query_corrections(project=proj_name)
        errors_data = query_errors(project=proj_name)
        pitfall_result = extract_pitfall_candidates(corrections_data, errors=errors_data)
        if pitfall_result["candidates"]:
            result["pitfall_candidates"] = pitfall_result["candidates"]

        # hook 候補検出: 同じ corrections パターンが N 回繰り返されたもの (#41)
        hook_candidates = detect_repeated_correction_patterns(corrections_data)
        if hook_candidates:
            result["hook_candidates"] = hook_candidates
    except Exception as e:
        result["pitfall_candidates_error"] = str(e)

    # instruction violation 検出 (issue #39)
    try:
        from critical_instruction_extractor import (
            extract_critical_lines,
            detect_instruction_violation,
        )
        from telemetry_query import query_corrections
        from issue_schema import make_instruction_violation_issue

        proj = project_root or Path.cwd()
        proj_name = proj.name
        corrections_data = query_corrections(project=proj_name)

        # last_skill が設定されている corrections のみ対象
        skill_corrections = [
            c for c in corrections_data if c.get("last_skill")
        ]

        # llm-batch-guard: 1件あたり最大 ~15 instruction × LLM呼び出しが発生するため上限を設ける
        # 最新 corrections を優先するため timestamp 降順でソートしてからスライス
        _MAX_CORRECTION_CHECKS = 20
        skill_corrections = sorted(
            skill_corrections,
            key=lambda c: c.get("timestamp", ""),
            reverse=True,
        )
        if len(skill_corrections) > _MAX_CORRECTION_CHECKS:
            print(
                f"[llm-batch-guard] instruction_violation: {len(skill_corrections)} corrections → "
                f"最新 {_MAX_CORRECTION_CHECKS} 件のみ検査 (推定LLM呼び出し: "
                f"{_MAX_CORRECTION_CHECKS * 15} 回)",
                file=sys.stderr,
            )
            skill_corrections = skill_corrections[:_MAX_CORRECTION_CHECKS]

        violations = []
        for corr in skill_corrections:
            skill_name = corr["last_skill"]
            # スキルの SKILL.md を探す
            skill_dirs = list(Path.home().glob(f".claude/skills/{skill_name}/SKILL.md"))
            pj_skill_dirs = list((proj / ".claude" / "skills" / skill_name / "SKILL.md").parent.glob("SKILL.md")) if (proj / ".claude" / "skills" / skill_name).exists() else []
            all_skill_mds = skill_dirs + [d for d in pj_skill_dirs if d not in skill_dirs]

            for skill_md in all_skill_mds:
                content = skill_md.read_text(encoding="utf-8")
                instructions = extract_critical_lines(content)
                if not instructions:
                    continue
                violation = detect_instruction_violation(corr, instructions)
                if violation:
                    violations.append(
                        make_instruction_violation_issue(
                            skill_name=skill_name,
                            skill_path=str(skill_md),
                            instruction_text=violation.instruction.original,
                            correction_message=violation.correction_message,
                            match_type=violation.match_type,
                            confidence=violation.confidence,
                            reason=violation.reason,
                            needs_review=violation.needs_review,
                        )
                    )
                break  # 最初にマッチしたスキルのみ

        if violations:
            result["instruction_violations"] = violations
    except Exception as e:
        result["instruction_violations_error"] = str(e)

    # constraint decay 検出 (arXiv 2605.06445)
    try:
        from .patterns import detect_constraint_decay  # noqa: PLC0415
        from . import DATA_DIR as _data_dir  # noqa: PLC0415
        decay_findings = detect_constraint_decay(
            sessions_path=_data_dir / "sessions.jsonl",
            corrections_path=_data_dir / "corrections.jsonl",
        )
        warnings = [f for f in decay_findings if f["severity"] == "WARNING"]
        if warnings:
            result["constraint_decay_warnings"] = warnings
        if decay_findings:
            result["constraint_decay_findings"] = decay_findings
    except Exception as e:
        result["constraint_decay_error"] = str(e)

    # 停滞→リカバリパターン検出
    try:
        from tool_usage_analyzer import (
            extract_tool_calls_by_session,
            detect_stall_recovery_patterns,
            STALL_RECOVERY_RECENCY_DAYS,
        )
        session_commands = extract_tool_calls_by_session(
            project_root,
            max_age_days=STALL_RECOVERY_RECENCY_DAYS,
        )
        stall_patterns = detect_stall_recovery_patterns(session_commands)
        result["stall_recovery_patterns"] = stall_patterns
    except Exception as e:
        result["stall_recovery_patterns"] = []
        result["stall_recovery_error"] = str(e)

    # ワークフローチェックポイントギャップ走査。
    # workflow skill 該当なし（skills_dir 不在等）でもキーを必ず残し `[]` にする。
    # 「評価したが該当なし ✓」と「そもそも評価していない（silence）」を
    # 下流 SKILL.md Step 10.4 が区別できるようにするため
    # — stall_recovery_patterns が常に出力されるのと同じ契約に揃える（#369）。
    workflow_gaps: list = []
    try:
        from workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
        proj = project_root or Path.cwd()
        skills_dir = proj / ".claude" / "skills"
        if skills_dir.is_dir():
            for skill_dir in sorted(skills_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                if not is_workflow_skill(skill_dir):
                    continue
                gaps = detect_checkpoint_gaps(skill_dir.name, skill_dir, proj)
                if gaps:
                    workflow_gaps.append({
                        "skill_name": skill_dir.name,
                        "gaps": gaps,
                    })
        result["workflow_checkpoint_gaps"] = workflow_gaps
    except Exception as e:
        result["workflow_checkpoint_gaps"] = workflow_gaps
        result["workflow_checkpoint_gaps_error"] = str(e)

    return result


def main() -> None:
    from . import run_discover as _run_discover  # noqa: PLC0415
    parser = argparse.ArgumentParser(description="パターン発見スクリプト")
    parser.add_argument(
        "--project-dir",
        default=None,
        help="プロジェクトディレクトリ（指定時はそのプロジェクトのレコードのみ集計）",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="project が null のレコードも集計に含める",
    )
    parser.add_argument(
        "--tool-usage",
        action="store_true",
        help="セッション JSONL からツール利用パターンを分析する",
    )
    args = parser.parse_args()

    project_root = Path(args.project_dir) if args.project_dir else None
    result = _run_discover(
        project_root=project_root,
        include_unknown=args.include_unknown,
        tool_usage=args.tool_usage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
