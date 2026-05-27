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

# update_count >= 3 で memory_heavy_update 警告を発火。
# 根拠: arXiv:2605.12978 "Useful Memories Become Faulty When Continuously Updated by LLMs" は
# 複数ラウンドの再要約で誤りが指数的に増幅することを示す (docs/research/faulty-updated-memories.md)。
MEMORY_HEAVY_UPDATE_THRESHOLD = 3


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
        try:
            temporal = parse_memory_temporal(path)
            update_count = temporal.get("update_count", 0)
            if update_count >= MEMORY_HEAVY_UPDATE_THRESHOLD:
                issues.append({
                    "type": "memory_heavy_update",
                    "file": str(path),
                    "detail": {
                        "update_count": update_count,
                        "threshold": MEMORY_HEAVY_UPDATE_THRESHOLD,
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

    # hardcoded values（ハードコード値検出）
    from . import classify_artifact_origin as _classify_origin  # noqa: E402
    for category in ("skills", "rules"):
        for path in artifacts.get(category, []):
            # global/plugin スキルは外部管理のため除外
            if _classify_origin(path) in ("global", "plugin"):
                continue
            detections = detect_hardcoded_values(str(path))
            for det in detections:
                issues.append({
                    "type": "hardcoded_value",
                    "file": str(path),
                    "detail": det,
                    "source": "detect_hardcoded_values",
                })

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
        untagged = detect_untagged_reference_candidates(artifacts, usage, project_dir=project_dir)
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
