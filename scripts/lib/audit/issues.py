"""issue 統一フォーマット収集ロジック。

audit パッケージから切り出された Issues collection モジュール。
- _is_user_invocable_heuristic: スキル内容からユーザー呼び出し型を推定
- detect_untagged_reference_candidates: reference 未設定スキル検出
- collect_issues: violations / stale_refs / near_limits / duplicates /
  hardcoded_values / layer 診断 / missing_effort / untagged_reference を
  統一 dict フォーマットで収集
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from reflect_utils import read_auto_memory
from path_extractor import extract_paths_outside_codeblocks as _extract_paths_outside_codeblocks, KNOWN_DIR_PREFIXES
from hardcoded_detector import detect_hardcoded_values
from line_limit import NEAR_LIMIT_RATIO
from memory_temporal import parse_memory_temporal

from ._constants import LIMITS
from .classification import classify_artifact_origin


def collect_hardcoded_value_issues(
    artifacts: Dict[str, List[Path]],
) -> List[Dict[str, Any]]:
    """skills / rules から hardcoded value issue を収集する共通関数（#419）。

    issues.py の collect_issues と orchestrator.py の run_audit はかつて同型の
    検出ループを二重実装しており、issues.py だけが global/plugin origin 除外を
    持っていた（orchestrator 側は除外なし＝外部管理スキルの散文まで走査して
    大量 FP の温床になっていた）。検出ループを本関数 1 箇所に集約し、両 call site
    がこれを呼ぶことで origin 除外の divergence を構造的に根治する。

    global / plugin origin（ユーザーが管理しない外部品）は除外する。
    """
    issues: List[Dict[str, Any]] = []
    for category in ("skills", "rules"):
        for path in artifacts.get(category, []):
            # global/plugin スキルは外部管理のため除外
            if classify_artifact_origin(path) in ("global", "plugin"):
                continue
            detections = detect_hardcoded_values(str(path))
            for det in detections:
                issues.append({
                    "type": "hardcoded_value",
                    "file": str(path),
                    "detail": det,
                    "source": "detect_hardcoded_values",
                })
    return issues

# memory_heavy_update 警告の複合閾値（#353）。
# 根拠: arXiv:2605.12978 "Useful Memories Become Faulty When Continuously Updated by LLMs" は
# 複数ラウンドの再要約で誤りが指数的に増幅することを示す (docs/research/faulty-updated-memories.md)。
# 更新回数単独では「活発に正しく更新した健全なメモリ」を誤検知するため、
# 行数（= 内容の肥大化）との複合条件にする（#353）。
MEMORY_HEAVY_UPDATE_THRESHOLD = 10
# 行数がこの値未満なら update_count が閾値を超えても警告しない。
# memory ファイルの上限 (LIMITS["memory"] = 120 行) の約 67% 相当。#104 で 30→80 に引き上げた:
# 旧 30 は amamo の簡潔メモリ（update 55 / 40 行）を誤検知した。80 に上げ「churn + 肥大化」の
# 劣化シグナルに純化することで、簡潔だが活発なメモリ（＝正常運用）を除外する。
MEMORY_HEAVY_UPDATE_LINE_THRESHOLD = 80


def claude_md_unparseable(project_dir: Optional[Path]) -> bool:
    """CLAUDE.md は在るが Skills セクションから trigger を 0 件しか抽出できない状態か (#295)。

    True のとき「CLAUDE.md 記載スキルは除外」ロジックが空集合で効かず、
    ユーザー呼び出し型スキルを untagged_reference / missed_skill 等として
    誤検出する。呼び出し側はこの状態を検出側のスキップ + 明示 surface に使う。

    CLAUDE.md がそもそも存在しない場合は False（= 環境解決失敗ではなく、
    CLAUDE.md を持たない正規プロジェクト。従来どおり検出を走らせてよい）。
    """
    if project_dir is None:
        return False
    from skill_triggers import resolve_claude_md_path, extract_skill_triggers

    resolved = resolve_claude_md_path(project_root=project_dir)
    if resolved is None:
        return False
    return not extract_skill_triggers(claude_md_path=resolved)


def _is_user_invocable_heuristic(content: str) -> bool:
    """スキル内容からユーザー呼び出し型かどうかを推定する (#47)。

    トリガーワード、使用タイミング等のアクション指標が
    リファレンス指標を上回ればユーザー呼び出し型と判定。
    """
    lower = content.lower()
    action_signals = [
        "trigger:", "トリガー", "使用タイミング",
        "steps", "手順", "実行", "execute",
        "run ", "deploy", "create", "generate",
        "```",         # コードブロックがあれば action 型とみなす
        "## usage", "## step", "## preamble", "## how",
        "check", "install", "setup", "update",
    ]
    reference_signals = [
        "ガイド", "guide", "仕様", "specification",
        "デザインシステム", "design system", "リファレンス", "reference",
        "評価基準", "criteria", "ルールブック", "rulebook",
        "type: reference",
    ]
    act_score = sum(1 for sig in action_signals if sig in lower)
    ref_score = sum(1 for sig in reference_signals if sig in lower)
    # 同スコア（両ゼロ含む）の場合は安全側として action 型とみなす
    return act_score >= ref_score


def detect_untagged_reference_candidates(
    artifacts: Dict[str, List[Path]],
    usage: Dict[str, int],
    *,
    project_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """ゼロ呼び出しだが reference 未設定のスキルを検出する。

    frontmatter に type フィールドがなく、usage もゼロのスキルを警告候補として返す。
    以下は除外:
    - プラグインスキル（プラグイン側で管理すべきため）
    - CLAUDE.md Skills セクションに記載されたスキル (#47)
    - コンテンツのヒューリスティックでユーザー呼び出し型と判定されたスキル (#47)
    """
    from frontmatter import parse_frontmatter
    from . import classify_artifact_origin  # 遅延 import: __init__ の関数

    # CLAUDE.md Skills セクションに記載のスキル名を収集
    claudemd_skills: set = set()
    if project_dir:
        from skill_triggers import extract_skill_triggers

        triggers = extract_skill_triggers(project_root=project_dir)
        for entry in triggers:
            claudemd_skills.add(entry["skill"])

    candidates = []
    for path in artifacts.get("skills", []):
        skill_name = path.parent.name
        if classify_artifact_origin(path) == "plugin":
            continue
        if skill_name in usage and usage[skill_name] > 0:
            continue
        # CLAUDE.md に記載済みなら除外 (#47)
        if skill_name in claudemd_skills:
            continue
        # frontmatter に type がないスキルのみ
        fm = parse_frontmatter(path)
        if fm.get("type"):
            continue
        # ヒューリスティックでユーザー呼び出し型なら除外 (#47)
        try:
            content = path.read_text(encoding="utf-8")
            if _is_user_invocable_heuristic(content):
                continue
        except (OSError, UnicodeDecodeError):
            pass
        candidates.append({
            "skill_name": skill_name,
            "file": str(path),
        })
    return candidates


def collect_issues(project_dir: Path) -> List[Dict[str, Any]]:
    """既存の検出関数の結果を統一フォーマットの issue リストとして返す。

    各 issue は {"type": str, "file": str, "detail": dict, "source": str} 形式。
    generate_report() には影響しない。
    """
    # 遅延 import: audit/__init__.py の関数群（循環 import 回避）
    from . import (
        aggregate_usage,
        check_line_limits,
        detect_duplicates_simple,
        find_artifacts,
        load_usage_data,
    )

    artifacts = find_artifacts(project_dir)
    issues: List[Dict[str, Any]] = []

    # violations（行数超過）— CLAUDE.md は warning のみ（violation として扱わない）
    violations = check_line_limits(artifacts)
    for v in violations:
        if v.get("warning_only"):
            continue
        issues.append({
            "type": "line_limit_violation",
            "file": v["file"],
            "detail": {"lines": v["lines"], "limit": v["limit"]},
            "source": "check_line_limits",
        })

    # stale_refs（陳腐化参照）と near_limits（肥大化警告）
    memory_files: List[Tuple[Path, str]] = []
    for path in artifacts.get("memory", []):
        try:
            content = path.read_text(encoding="utf-8")
            memory_files.append((path, content))
        except (OSError, UnicodeDecodeError):
            continue
    for entry in read_auto_memory(str(project_dir)):
        entry_path = Path(entry["path"])
        if not any(p == entry_path for p, _ in memory_files):
            memory_files.append((entry_path, entry["content"]))

    for path, content in memory_files:
        extracted = _extract_paths_outside_codeblocks(content)
        for line_num, ref_path in extracted:
            if ref_path.startswith("/"):
                check_path = Path(ref_path)
            else:
                check_path = project_dir / ref_path
            if not check_path.exists():
                # ファイル位置基準の相対パス解決（参照元ファイルの親ディレクトリ基準）
                if not ref_path.startswith("/"):
                    file_relative = path.parent / ref_path
                    if file_relative.exists():
                        continue
                # トップレベルディレクトリがプロジェクトルートに存在しない場合は除外
                if not ref_path.startswith("/"):
                    top_dir = ref_path.split("/")[0]
                    if top_dir not in KNOWN_DIR_PREFIXES and not (project_dir / top_dir).exists():
                        continue
                issues.append({
                    "type": "stale_ref",
                    "file": str(path),
                    "detail": {"line": line_num, "path": ref_path},
                    "source": "build_memory_health_section",
                })

        line_count = content.count("\n") + 1
        limit = LIMITS["MEMORY.md"] if path.name == "MEMORY.md" else LIMITS["memory"]
        threshold = int(limit * NEAR_LIMIT_RATIO)
        if line_count >= threshold:
            pct = int(line_count / limit * 100)
            issues.append({
                "type": "near_limit",
                "file": str(path),
                "detail": {"lines": line_count, "limit": limit, "pct": pct},
                "source": "build_memory_health_section",
            })

        # memory_heavy_update: LLM 自己更新が閾値超え (Issue #97 / arXiv:2605.12978)
        # 更新回数単独では正常な活発更新を誤検知するため、行数（= 内容の肥大化）との複合条件にする（#353）。
        # #104 再設計: 「churn + 肥大化」の劣化シグナルに純化する。update_count は memory_capability の
        # use_read 軸で「活性（良）」として加点される指標（＝正の信号）なので、低い閾値で heavy_update を
        # 発火させると同一 run で「活性（良）」と「要対応（悪）」に二重分類する矛盾を生む。閾値を
        # update>=10 / 行数>=80 に引き上げ、簡潔だが活発なメモリ（amamo: update 55 / 40 行）を誤検知しない。
        # 旧 #104 は maintain 軸（freshness）の健全性ゲートで除外したが、temporal メタデータの無い通常
        # メモリでは detector がほぼ発火せず near-inert になったため撤去した（健全/非健全を問わず肥大化で発火）。
        try:
            temporal = parse_memory_temporal(path)
            update_count = temporal.get("update_count", 0)
            if (
                update_count >= MEMORY_HEAVY_UPDATE_THRESHOLD
                and line_count >= MEMORY_HEAVY_UPDATE_LINE_THRESHOLD
            ):
                issues.append({
                    "type": "memory_heavy_update",
                    "file": str(path),
                    "detail": {
                        "update_count": update_count,
                        "threshold": MEMORY_HEAVY_UPDATE_THRESHOLD,
                        "line_count": line_count,
                        "line_threshold": MEMORY_HEAVY_UPDATE_LINE_THRESHOLD,
                    },
                    "source": "build_memory_health_section",
                })
        except Exception:
            pass  # frontmatter 不正は既存挙動を壊さない

    # duplicates（重複候補）
    duplicates = detect_duplicates_simple(artifacts)
    for d in duplicates:
        issues.append({
            "type": "duplicate",
            "file": d["paths"][0] if d["paths"] else "",
            "detail": {"name": d["name"], "paths": d["paths"]},
            "source": "detect_duplicates_simple",
        })

    # hardcoded values（ハードコード値検出）— orchestrator と共通の検出関数を使う（#419）
    issues.extend(collect_hardcoded_value_issues(artifacts))

    # レイヤー別診断（Rules / Memory / Hooks / CLAUDE.md）
    try:
        from layer_diagnose import diagnose_all_layers
        existing_stale_refs = [i for i in issues if i["type"] == "stale_ref"]
        layer_results = diagnose_all_layers(
            project_dir,
            existing_stale_refs=existing_stale_refs,
        )
        for layer_issues in layer_results.values():
            issues.extend(layer_issues)
    except Exception:
        pass  # レイヤー診断のエラーは既存機能に影響しない

    # missing_effort（effort frontmatter 未設定スキル）
    try:
        from effort_detector import detect_missing_effort_frontmatter
        effort_result = detect_missing_effort_frontmatter(project_dir)
        if effort_result["applicable"]:
            for ev in effort_result["evidence"]:
                issues.append({
                    "type": "missing_effort",
                    "file": ev["skill_path"],
                    "detail": {
                        "skill_name": ev["skill_name"],
                        "proposed_effort": ev["proposed_effort"],
                        "confidence": ev["confidence"],
                        "reason": ev.get("reason", ""),
                    },
                    "source": "detect_missing_effort_frontmatter",
                })
    except Exception:
        pass  # effort 検出のエラーは既存機能に影響しない

    # untagged_reference_candidates（reference type 未設定スキル）
    try:
        usage_records = load_usage_data(project_root=project_dir)
        usage = aggregate_usage(usage_records, exclude_plugins=True)
        # CLAUDE.md は在るが trigger 抽出 0 のときは除外ロジックが効かず誤検知になるため
        # untagged を構造化 issue に積まない（環境解決失敗の誤検出を confident に出さない, #295）。
        untagged = (
            [] if claude_md_unparseable(project_dir)
            else detect_untagged_reference_candidates(artifacts, usage, project_dir=project_dir)
        )
        for candidate in untagged:
            issues.append({
                "type": "untagged_reference_candidates",
                "file": candidate["file"],
                "detail": {"skill_name": candidate["skill_name"]},
                "source": "detect_untagged_reference_candidates",
            })
    except Exception:
        pass  # untagged 検出のエラーは既存機能に影響しない

    return issues
