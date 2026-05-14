#!/usr/bin/env python3
"""環境の健康診断スクリプト。

全 skills / rules / memory の棚卸し + 行数チェック + 使用状況集計を行い、
1画面レポートを出力する。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from rl_common import DATA_DIR  # noqa: F401 — re-exported for backward compat (audit.DATA_DIR / bloat_control / test patches)
from reflect_utils import read_all_memory_entries, read_auto_memory, split_memory_sections
from hardcoded_detector import detect_hardcoded_values
from frontmatter import count_content_lines
from path_extractor import extract_paths_outside_codeblocks as _extract_paths_outside_codeblocks, KNOWN_DIR_PREFIXES
from skill_origin import (
    classify_skill_origin as _so_classify_skill_origin,
    get_plugin_skill_map as _so_get_plugin_skill_map,
    get_plugin_skill_names as _so_get_plugin_skill_names,
    build_plugin_prefixes as _so_build_plugin_prefixes,
    classify_usage_skill as _so_classify_usage_skill,
    invalidate_cache as _so_invalidate_cache,
)

# 行数制限 — line_limit.py を Single Source of Truth として参照
from line_limit import (
    MAX_PROJECT_RULE_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
    NEAR_LIMIT_RATIO,
)

# LIMITS / _STOPWORDS は audit/_constants.py に集約済み（後方互換のため再エクスポート）
from ._constants import LIMITS, _STOPWORDS  # noqa: F401

# DATA_DIR は rl_common.DATA_DIR を再エクスポート（L19 の import 参照）
# - CLAUDE_PLUGIN_DATA env var サポート（cross-project / fleet 用途）
# - 真の源を rl_common.py に一本化、bloat_control.py と test patch の互換維持
#   詳細: docs/decisions/022-data-dir-unification.md（予定）

# KNOWN_DIR_PREFIXES は path_extractor から import 済み

# キャッシュ: skill_origin.py に委譲（後方互換ラッパー）
# テスト後方互換: audit._plugin_skill_map_cache を直接セットするテスト向け
_plugin_skill_map_cache: Optional[Dict[str, str]] = None


def _load_plugin_skill_map() -> Dict[str, str]:
    """installed_plugins.json → {skill_name: plugin_name} マッピングを構築。

    skill_origin.py に委譲。後方互換のためラッパーとして残す。
    テストが _plugin_skill_map_cache を直接セットした場合はそちらを優先。
    """
    if _plugin_skill_map_cache is not None:
        _build_plugin_prefixes(_plugin_skill_map_cache)
        return _plugin_skill_map_cache
    mapping = _so_get_plugin_skill_map()
    # prefix も構築（classify_usage_skill の後方互換用）
    _build_plugin_prefixes(mapping)
    return mapping


# プラグイン名 → prefix パターンのキャッシュ
_plugin_prefix_cache: Optional[Dict[str, List[str]]] = None


def _build_plugin_prefixes(mapping: Dict[str, str]) -> None:
    """skill_origin.py に委譲。後方互換ラッパー。"""
    global _plugin_prefix_cache
    _plugin_prefix_cache = _so_build_plugin_prefixes(mapping)


def classify_usage_skill(skill_name: str) -> Optional[str]:
    """usage レコードのスキル名をプラグインに分類する。

    skill_origin.py に委譲。後方互換ラッパー。
    """
    # prefix キャッシュ初期化を保証
    _load_plugin_skill_map()
    return _so_classify_usage_skill(skill_name)


def _load_plugin_skill_names() -> frozenset:
    """後方互換ラッパー。テストが _plugin_skill_map_cache を直接セットした場合はそちらを優先。"""
    if _plugin_skill_map_cache is not None:
        return frozenset(_plugin_skill_map_cache.keys())
    return frozenset(_load_plugin_skill_map().keys())


def classify_artifact_origin(path: Path) -> str:
    """スキル/ルールの出自を分類する。skill_origin.py に委譲。

    テストが _plugin_skill_map_cache を直接セットした場合は、
    そのキャッシュを使ってインライン判定する（後方互換）。

    Returns:
        "plugin" — プラグイン由来
        "global" — ~/.claude/skills/ 配下
        "custom" — その他（プロジェクトローカル等）
    """
    if _plugin_skill_map_cache is not None:
        # テスト後方互換: ローカルキャッシュが設定されている場合はインライン判定
        resolved = path.expanduser().resolve()
        resolved_str = str(resolved)

        plugins_dir = os.environ.get("CLAUDE_PLUGINS_DIR")
        if plugins_dir:
            plugins_path = str(Path(plugins_dir).resolve())
        else:
            plugins_path = str(Path.home() / ".claude" / "plugins" / "cache")

        if resolved_str.startswith(plugins_path):
            return "plugin"

        global_skills_path = str(Path.home() / ".claude" / "skills")
        if resolved_str.startswith(global_skills_path):
            return "global"

        if "/.claude/skills/" in resolved_str:
            parts = resolved.parts
            try:
                skills_idx = len(parts) - 1 - list(reversed(parts)).index("skills")
                if skills_idx + 1 < len(parts):
                    skill_dir_name = parts[skills_idx + 1]
                    if skill_dir_name in _plugin_skill_map_cache:
                        return "plugin"
            except ValueError:
                pass

        return "custom"

    return _so_classify_skill_origin(path)


def find_artifacts(project_dir: Path) -> Dict[str, List[Path]]:
    """プロジェクト内のアーティファクトを一覧する。"""
    result: Dict[str, List[Path]] = {
        "skills": [],
        "rules": [],
        "memory": [],
        "claude_md": [],
    }

    claude_dir = project_dir / ".claude"

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        result["claude_md"].append(claude_md)

    # Skills
    skills_dir = claude_dir / "skills"
    if skills_dir.exists():
        for skill_md in skills_dir.rglob("SKILL.md"):
            result["skills"].append(skill_md)

    # Rules
    rules_dir = claude_dir / "rules"
    if rules_dir.exists():
        for rule_file in rules_dir.glob("*.md"):
            result["rules"].append(rule_file)

    # Memory
    memory_dir = claude_dir / "memory"
    if memory_dir.exists():
        for mem_file in memory_dir.glob("*.md"):
            result["memory"].append(mem_file)

    # Global artifacts
    global_claude = Path.home() / ".claude"
    global_skills = global_claude / "skills"
    if global_skills.exists():
        for skill_md in global_skills.rglob("SKILL.md"):
            result["skills"].append(skill_md)

    global_rules = global_claude / "rules"
    if global_rules.exists():
        for rule_file in global_rules.glob("*.md"):
            result["rules"].append(rule_file)

    return result


def check_line_limits(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """行数制限の超過を検出する。

    CLAUDE.md は violation ではなく warning のみ（collect_issues で除外）。
    """
    violations = []

    for path in artifacts.get("claude_md", []):
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines > LIMITS["CLAUDE.md"]:
            violations.append({"file": str(path), "lines": lines, "limit": LIMITS["CLAUDE.md"], "warning_only": True})

    for path in artifacts.get("rules", []):
        content = path.read_text(encoding="utf-8")
        lines = count_content_lines(content)
        # グローバル/プロジェクトルールで制限値を分ける
        home_str = str(Path.home())
        is_global = str(path).startswith(home_str) and ".claude/rules/" in str(path)
        limit = LIMITS["rules"] if is_global else LIMITS["project_rules"]
        if lines > limit:
            violations.append({"file": str(path), "lines": lines, "limit": limit})

    for path in artifacts.get("skills", []):
        # custom (プロジェクトローカル) のみ行数制限対象。plugin / global は
        # ダウンロード品なので除外する（ユーザーが管理するファイルではない）。
        if classify_artifact_origin(path) != "custom":
            continue
        lines = path.read_text(encoding="utf-8").count("\n") + 1
        if lines > LIMITS["SKILL.md"]:
            violations.append({"file": str(path), "lines": lines, "limit": LIMITS["SKILL.md"]})

    for path in artifacts.get("memory", []):
        content = path.read_text(encoding="utf-8")
        lines = content.count("\n") + 1
        limit = LIMITS["MEMORY.md"] if path.name == "MEMORY.md" else LIMITS["memory"]
        if lines > limit:
            violations.append({"file": str(path), "lines": lines, "limit": limit})
        # MEMORY.md のみバイトサイズチェック（CC v2.1.83 で 25KB 切り詰め追加）
        if path.name == "MEMORY.md":
            from lib.line_limit import MEMORY_MAX_BYTES, MEMORY_NEAR_LIMIT_BYTES

            byte_size = len(content.encode("utf-8"))
            if byte_size > MEMORY_MAX_BYTES:
                violations.append({"file": str(path), "bytes": byte_size, "bytes_limit": MEMORY_MAX_BYTES})
            elif byte_size > MEMORY_NEAR_LIMIT_BYTES:
                violations.append({"file": str(path), "bytes": byte_size, "bytes_limit": MEMORY_MAX_BYTES, "near_limit": True, "warning_only": True})

    return violations


# Memory verification functions are extracted to audit/memory.py
# 後方互換のため audit パッケージから直接 import 可能にする
from .memory import (  # noqa: F401, E402
    _extract_section_keywords,
    _find_archive_mentions,
    _is_project_specific_section,
    build_memory_verification_context,
    build_memory_health_section,
    build_temporal_memory_warnings,
)


def load_usage_data(
    days: int = 30,
    *,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """usage.jsonl から直近N日のデータを読み込む。

    Args:
        days: 直近何日分のデータを読み込むか。
        project_root: 指定時は該当プロジェクトのレコードのみ返す。
    """
    from telemetry_query import query_usage

    project_name = project_root.name if project_root else None
    # project_root 未指定時は全レコードを対象（グローバル集計）
    include_unknown = project_root is None
    records = query_usage(
        project=project_name,
        include_unknown=include_unknown,
        usage_file=DATA_DIR / "usage.jsonl",
    )

    # 日数フィルタ
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r for r in records if r.get("ts", r.get("timestamp", "")) >= cutoff]


from agent_classifier import BUILTIN_AGENT_NAMES

_BUILTIN_TOOLS = {f"Agent:{n}" for n in BUILTIN_AGENT_NAMES} | {"commit"}


def _is_openspec_skill(skill_name: str) -> bool:
    """スキル名が OpenSpec 関連（レガシー）かどうかを判定する。"""
    if not skill_name:
        return False
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return "openspec" in base or base.startswith("opsx:")


def _is_plugin_skill(skill_name: str) -> bool:
    """スキル名がプラグイン由来かどうかを判定する。

    classify_usage_skill（完全一致 + prefix マッチ）、_is_gstack_skill、
    _is_openspec_skill（レガシー）を併用。
    """
    if classify_usage_skill(skill_name) is not None:
        return True
    if _is_gstack_skill(skill_name):
        return True
    if _is_openspec_skill(skill_name):
        return True
    return False


def aggregate_usage(
    records: List[Dict[str, Any]],
    exclude_plugins: bool = False,
) -> Dict[str, int]:
    """スキル使用回数を集計する。基本ツールはノイズのため除外。

    Args:
        records: usage レコードのリスト
        exclude_plugins: True の場合、プラグインスキルを除外して PJ 固有のみ返す
    """
    counts: Dict[str, int] = {}
    for rec in records:
        skill = rec.get("skill_name", "unknown")
        if skill in _BUILTIN_TOOLS:
            continue
        if exclude_plugins and _is_plugin_skill(skill):
            continue
        counts[skill] = counts.get(skill, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def aggregate_plugin_usage(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """プラグイン別の使用回数を集計する。

    classify_usage_skill でプラグイン名が判定できるものはプラグイン名で集計。
    gstack スキルは "gstack" として、OpenSpec レガシーは "openspec(legacy)" として集計。

    Returns:
        {plugin_name: total_count} の辞書（降順ソート）
    """
    plugin_counts: Dict[str, int] = {}
    for rec in records:
        skill = rec.get("skill_name", "unknown")
        if skill in _BUILTIN_TOOLS:
            continue
        plugin_name = classify_usage_skill(skill)
        if plugin_name:
            plugin_counts[plugin_name] = plugin_counts.get(plugin_name, 0) + 1
        elif _is_gstack_skill(skill):
            key = "gstack"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
        elif _is_openspec_skill(skill):
            key = "openspec(legacy)"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
    return dict(sorted(plugin_counts.items(), key=lambda x: x[1], reverse=True))


def detect_duplicates_simple(artifacts: Dict[str, List[Path]]) -> List[Dict[str, Any]]:
    """簡易的な重複検出（ファイル名ベース）。LLM ベースの意味的類似度判定は別途実行。"""
    seen: Dict[str, List[str]] = {}
    duplicates = []

    for category in ["skills", "rules"]:
        for path in artifacts.get(category, []):
            name = path.stem if category == "rules" else path.parent.name
            key = name.lower().replace("-", "").replace("_", "")
            if key not in seen:
                seen[key] = []
            seen[key].append(str(path))

    for key, paths in seen.items():
        if len(paths) > 1:
            duplicates.append({"name": key, "paths": paths})

    return duplicates


def semantic_similarity_check(
    artifacts: Dict[str, List[Path]], threshold: float = 0.80
) -> List[Dict[str, Any]]:
    """TF-IDF + コサイン類似度による意味的類似度判定。閾値は 80%。

    audit-report spec の Single Source of Truth。
    prune はこの関数の結果を利用する。

    Returns:
        [{"path_a": str, "path_b": str, "similarity": float}, ...]
    """
    from similarity import compute_pairwise_similarity

    # artifacts から skills と rules のパスを辞書に変換（プラグイン由来は除外）
    path_dict: Dict[str, str] = {}
    for path in artifacts.get("skills", []):
        if classify_artifact_origin(path) == "plugin":
            continue
        skill_name = path.parent.name
        path_dict[skill_name] = str(path)

    for path in artifacts.get("rules", []):
        if classify_artifact_origin(path) == "plugin":
            continue
        rule_stem = path.stem
        path_dict[rule_stem] = str(path)

    return compute_pairwise_similarity(path_dict, threshold)


def load_usage_registry() -> Dict[str, List[Dict[str, Any]]]:
    """Usage Registry からデータを読み込む。"""
    registry_file = DATA_DIR / "usage-registry.jsonl"
    if not registry_file.exists():
        return {}

    result: Dict[str, List[Dict[str, Any]]] = {}
    for line in registry_file.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            skill = rec.get("skill_name", "")
            if skill not in result:
                result[skill] = []
            result[skill].append(rec)
        except json.JSONDecodeError:
            continue
    return result


def scope_advisory(registry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Scope Advisory: global スキルの使用PJ数と推奨アクションを生成。"""
    advisories = []
    for skill, records in registry.items():
        projects = set(r.get("project_path", "") for r in records)
        latest = max((r.get("timestamp", "") for r in records), default="")
        advisory = {
            "skill": skill,
            "project_count": len(projects),
            "projects": list(projects),
            "last_used": latest,
            "recommendation": "keep global" if len(projects) > 1 else "consider project-scope",
        }
        advisories.append(advisory)
    return advisories


def load_quality_baselines() -> List[Dict[str, Any]]:
    """quality-baselines.jsonl から全レコードを読み込む。"""
    baselines_file = DATA_DIR / "quality-baselines.jsonl"
    if not baselines_file.exists():
        return []
    records = []
    for line in baselines_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def generate_sparkline(scores: List[float]) -> str:
    """スコアリストからスパークライン文字列を生成する。"""
    if not scores:
        return ""
    blocks = " ▁▂▃▄▅▆▇"
    min_s = min(scores)
    max_s = max(scores)
    span = max_s - min_s if max_s > min_s else 1.0
    result = ""
    for s in scores:
        idx = int((s - min_s) / span * (len(blocks) - 1))
        idx = max(0, min(len(blocks) - 1, idx))
        result += blocks[idx]
    return result


def build_quality_trends_section(
    baselines: List[Dict[str, Any]],
    usage: Dict[str, int],
) -> List[str]:
    """品質推移セクションの行リストを生成する。"""
    if not baselines:
        return []

    # 遅延 import（循環参照回避）
    _scripts_dir = PLUGIN_ROOT / "scripts"
    if str(_scripts_dir) not in sys.path:
        sys.path.insert(0, str(_scripts_dir))
    from quality_monitor import (
        DEGRADATION_THRESHOLD,
        RESCORE_DAYS_THRESHOLD,
        RESCORE_USAGE_THRESHOLD,
        compute_baseline_score,
        compute_moving_average,
        get_skill_records,
        needs_rescore,
    )

    # スキル名を収集
    skill_names = sorted(set(r.get("skill_name", "") for r in baselines if r.get("skill_name")))
    if not skill_names:
        return []

    lines = ["## Skill Quality Trends", ""]

    for skill_name in skill_names:
        skill_recs = get_skill_records(baselines, skill_name)
        if not skill_recs:
            continue

        scores = [r.get("score", 0.0) for r in skill_recs]
        latest_score = scores[-1] if scores else 0.0

        # スパークライン（2件以上必要）
        if len(scores) >= 2:
            sparkline = generate_sparkline(scores)
        else:
            sparkline = ""

        # 劣化判定
        degraded = False
        if len(skill_recs) >= 2:
            baseline = compute_baseline_score(skill_recs)
            avg = compute_moving_average(skill_recs)
            if baseline > 0:
                decline_rate = (baseline - avg) / baseline
                degraded = decline_rate >= DEGRADATION_THRESHOLD

        # 再スコアリング判定
        current_usage = usage.get(skill_name, 0)
        rescore_needed = needs_rescore(skill_name, current_usage, baselines)

        # 行を組み立て
        parts = [f"- {skill_name}"]
        if sparkline:
            parts.append(f" {sparkline}")
        parts.append(f" {latest_score:.2f}")
        if degraded:
            parts.append(f" DEGRADED → /optimize {skill_name}")
        elif rescore_needed:
            parts.append(" RESCORE NEEDED")
        lines.append("".join(parts))

    lines.append("")
    return lines


# ---------- gstack ワークフロー分析 ----------

_FLOW_CHAIN_FILE = Path.home() / ".gstack" / "flow-chain.json"

# fallback 値（flow-chain.json がない場合に使用）
_FALLBACK_GSTACK_LIFECYCLE = ["plan", "ship", "document", "spec", "retro"]
_FALLBACK_GSTACK_SKILL_PHASE_MAP: Dict[str, str] = {
    "office-hours": "plan",
    "plan-eng-review": "plan",
    "plan-ceo-review": "plan",
    "plan-design-review": "plan",
    "ship": "ship",
    "document-release": "document",
    "spec-keeper": "spec",
    "retro": "retro",
}


def _load_flow_chain_phases(
    path: Optional[Path] = None,
) -> tuple:
    """flow-chain.json から lifecycle と phase_map を構築する。

    Returns:
        (lifecycle, phase_map) — ファイル不在・不正時は fallback 値
    """
    p = path or _FLOW_CHAIN_FILE
    try:
        if not p.exists():
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP
        data = json.loads(p.read_text(encoding="utf-8"))
        chain = data.get("chain")
        if not isinstance(chain, dict) or not chain:
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP

        phase_map: Dict[str, str] = {}
        seen_phases: list = []
        for skill_name, entry in chain.items():
            if not isinstance(entry, dict):
                continue
            phase = entry.get("phase")
            if not phase or not isinstance(phase, str):
                continue
            phase_map[skill_name] = phase
            if phase not in seen_phases:
                seen_phases.append(phase)

        if not phase_map:
            return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP

        return seen_phases, phase_map
    except (json.JSONDecodeError, OSError, KeyError):
        return _FALLBACK_GSTACK_LIFECYCLE, _FALLBACK_GSTACK_SKILL_PHASE_MAP


# 動的読み込み（モジュールロード時に1回実行）
_GSTACK_LIFECYCLE, _GSTACK_SKILL_PHASE_MAP = _load_flow_chain_phases()

# gstack スキル名の集合（高速判定用）
_GSTACK_SKILL_NAMES = frozenset(_GSTACK_SKILL_PHASE_MAP.keys())


def _match_gstack_phase(skill_name: str) -> Optional[str]:
    """スキル名から gstack ライフサイクルフェーズを推定する。"""
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return _GSTACK_SKILL_PHASE_MAP.get(base)


def _is_gstack_skill(skill_name: str) -> bool:
    """スキル名が gstack 関連かどうかを判定する。"""
    if not skill_name:
        return False
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return base in _GSTACK_SKILL_NAMES


def build_gstack_analytics_section(
    records: List[Dict[str, Any]],
) -> List[str]:
    """gstack ワークフロー分析セクションを構築する。

    ファネル（plan→refine→ship→document→spec→retro の完走率）、
    フェーズ別効率、品質トレンド、最適化候補を表示。
    """
    # gstack レコードのみ抽出
    gstack_records = [r for r in records if _is_gstack_skill(r.get("skill_name", ""))]
    if not gstack_records:
        return []

    # フェーズ別集計
    phase_counts: Dict[str, int] = {}
    phase_records: Dict[str, List[Dict[str, Any]]] = {}
    for rec in gstack_records:
        phase = _match_gstack_phase(rec.get("skill_name", ""))
        if phase:
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
            if phase not in phase_records:
                phase_records[phase] = []
            phase_records[phase].append(rec)

    if not phase_counts:
        return []

    lines = ["## gstack Workflow Analytics", ""]

    # ファネル表示
    funnel_parts = []
    for phase in _GSTACK_LIFECYCLE:
        count = phase_counts.get(phase, 0)
        if count > 0:
            funnel_parts.append(f"{phase}({count})")
    if funnel_parts:
        lines.append(f"Funnel: {' → '.join(funnel_parts)}")

    # plan → retro 比率
    plan_count = phase_counts.get("plan", 0)
    retro_count = phase_counts.get("retro", 0)
    if plan_count > 0:
        ratio = retro_count / plan_count
        if ratio <= 1.0:
            lines.append(f"Completion rate: {int(ratio * 100)}% ({retro_count}/{plan_count})")
        else:
            lines.append(f"Plan→Retro ratio: {ratio:.1f}x ({retro_count}/{plan_count})")
    lines.append("")

    # フェーズ別効率テーブル
    lines.append("Phase efficiency:")
    for phase in _GSTACK_LIFECYCLE:
        recs = phase_records.get(phase, [])
        if not recs:
            continue
        count = len(recs)
        # セッション別グルーピングで平均ステップ数を推定
        sessions: Dict[str, int] = {}
        for r in recs:
            sid = r.get("session_id", "unknown")
            sessions[sid] = sessions.get(sid, 0) + 1
        avg_steps = sum(sessions.values()) / len(sessions) if sessions else 0
        # スキル名のばらつき（一貫性指標）
        skill_names = [r.get("skill_name", "") for r in recs]
        unique_ratio = len(set(skill_names)) / len(skill_names) if skill_names else 1.0
        consistency = 1.0 - unique_ratio  # 名前が統一されているほど高い
        warn = " LOW" if consistency < 0.5 and count >= 5 else ""
        lines.append(f"- {phase}: {count} runs, avg {avg_steps:.1f} steps/session, consistency {consistency:.2f}{warn}")

    lines.append("")

    # 品質トレンド（quality-baselines.jsonl から gstack スキルのみ）
    baselines = load_quality_baselines()
    if baselines:
        gstack_baselines = [b for b in baselines if _is_gstack_skill(b.get("skill_name", ""))]
        if gstack_baselines:
            lines.append("Quality trends:")
            skill_scores: Dict[str, float] = {}
            for b in gstack_baselines:
                skill_scores[b["skill_name"]] = b.get("score", 0.0)
            for name, score in sorted(skill_scores.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- {name}: {score:.2f}")
            lines.append("")

    # 最適化候補（一貫性が最も低いフェーズ）
    worst_phase = None
    worst_consistency = 1.0
    for phase in _GSTACK_LIFECYCLE:
        recs = phase_records.get(phase, [])
        if len(recs) < 5:
            continue
        skill_names = [r.get("skill_name", "") for r in recs]
        unique_ratio = len(set(skill_names)) / len(skill_names) if skill_names else 1.0
        consistency = 1.0 - unique_ratio
        if consistency < worst_consistency:
            worst_consistency = consistency
            worst_phase = phase
    if worst_phase and worst_consistency < 0.5:
        lines.append(f"Optimization candidate: {worst_phase} (consistency {worst_consistency:.2f})")
        lines.append("")

    return lines


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
    ]
    reference_signals = [
        "ガイド", "guide", "仕様", "specification",
        "デザインシステム", "design system", "リファレンス", "reference",
        "評価基準", "criteria", "ルールブック", "rulebook",
        "type: reference",
    ]
    act_score = sum(1 for sig in action_signals if sig in lower)
    ref_score = sum(1 for sig in reference_signals if sig in lower)
    return act_score > ref_score


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
    for category in ("skills", "rules"):
        for path in artifacts.get(category, []):
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


def _format_constitutional_report(result: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Constitutional Score をレポート用にフォーマットする。"""
    if result is None:
        return ["## Constitutional Score", "", "LLM 評価に失敗しました", ""]

    if result.get("overall") is None:
        skip_reason = result.get("skip_reason", "unknown")
        coverage = result.get("coverage_value", "N/A")
        return [
            "## Constitutional Score",
            "",
            f"Skipped: {skip_reason} (coverage={coverage})",
            "",
        ]

    lines = [f"## Constitutional Score: {result['overall']:.2f}", ""]

    # 原則別スコア
    per_principle = result.get("per_principle", [])
    if per_principle:
        lines.append("### Per-Principle Scores")
        for p in per_principle:
            score = p.get("score", 0.0)
            bar_filled = int(score * 20)
            bar_empty = 20 - bar_filled
            bar = "\u2588" * bar_filled + "\u2591" * bar_empty
            lines.append(f"  {p.get('id', '?'):30s} {score:.2f} {bar}")
        lines.append("")

    # コスト情報
    cost = result.get("estimated_cost_usd", 0)
    calls = result.get("llm_calls_count", 0)
    lines.append(f"LLM calls: {calls}, Estimated cost: ${cost:.4f}")
    lines.append("")

    return lines


def _short_int(n: int | None) -> str:
    if n is None:
        return "--"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def build_token_consumption_section(days: int = 30) -> List[str]:
    """Token Consumption セクションを生成する。

    データ無し → 1 行ヒントのみ返す。
    データあり → TOP 3 / Anomalies / Hints。
    """
    try:
        import token_usage_query as tuq  # type: ignore
        import token_usage_store as tus  # type: ignore
    except ImportError:
        return []

    db_empty = (not tus.HAS_DUCKDB) or (not tus.USAGE_DB.exists())
    if not db_empty:
        try:
            row = tus.query("SELECT COUNT(*) FROM token_usage")
            db_empty = (not row) or (row[0][0] == 0)
        except Exception:
            db_empty = True

    if db_empty:
        return [
            "## Token Consumption",
            "",
            "(Token tracking not initialized — run `rl-fleet tokens --backfill` to enable)",
            "",
        ]

    try:
        top = tuq.top_n_consumers(days=days, n=3)
        wow = tuq.wow_anomalies()
        cache = tuq.cache_hit_anomalies()
    except Exception:
        return []

    lines: List[str] = [f"## Token Consumption (last {days} days)", ""]
    if top:
        lines.append("TOP 3 consumers:")
        for i, c in enumerate(top, 1):
            hit = (
                f"  (cache hit {c['cache_hit_pct']:.0f}%)"
                if c.get("cache_hit_pct") is not None
                else ""
            )
            label = c.get("pj_slug") or c["pj_id"]
            lines.append(f"  {i}. {label}\t{_short_int(c['tokens'])}{hit}")
        lines.append("")
    if wow or cache:
        lines.append("Anomalies detected:")
        for a in wow:
            lines.append(
                f"  • {a['pj_id']}: WoW +{a['wow_pct']:.0f}% "
                f"({_short_int(a['last_week'])} → {_short_int(a['this_week'])})"
            )
        for a in cache:
            lines.append(
                f"  • {a['pj_id']}: cache hit {a['last_hit_pct']:.0f}% → "
                f"{a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)"
            )
        lines.append("")
    lines.append("Hints:")
    lines.append("  • Low cache hit (<40%) often means CLAUDE.md / system prompt changes per session")
    lines.append("  • WoW spikes often correlate with subagent loops — check SUBAGENTS_30d column")
    lines.append("")
    return lines


def _build_test_guard_section(project_dir: Path) -> Optional[List[str]]:
    """PJ が LLM SDK を使うのに no-llm-in-tests / pytest-no-llm が未導入なら勧める。"""
    try:
        import test_guard
    except ImportError:
        return None
    rows = test_guard.collect_test_guard_rows([project_dir])
    if not rows:
        return None
    r = rows[0]
    if not r.uses_llm:
        return None
    if not r.needs_attention and not r.preventive_candidate:
        return None
    lines = ["## Test Guard", ""]
    lines.append(f"このPJはLLM SDKを利用しています ({', '.join(sorted(r.languages))})。")
    if r.preventive_candidate:
        lines.append("現在テストフレームワーク未導入のため即時の事故リスクは低いですが、")
        lines.append("テスト追加時に備え以下のguardを予防的に導入することを推奨します:")
    else:
        lines.append("ユニットテストでLLMを誤って実呼び出ししないよう、以下のguardの導入を推奨します:")
    if not r.has_precommit_hook:
        lines.append("- pre-commit: `no-llm-in-tests` (静的検出、全言語)")
    if "python" in r.languages and not r.has_pytest_no_llm:
        lines.append("- pip: `pytest-no-llm` (実行時 guard、Python のみ)")
    lines.append("")
    lines.append("導入方法は ~/tools/no-llm-in-tests/README.md, ~/tools/pytest-no-llm/README.md を参照。")
    lines.append("")
    return lines


def generate_report(
    artifacts: Dict[str, List[Path]],
    violations: List[Dict[str, Any]],
    usage: Dict[str, int],
    duplicates: List[Dict[str, Any]],
    advisories: List[Dict[str, Any]],
    quality_baselines: Optional[List[Dict[str, Any]]] = None,
    project_dir: Optional[Path] = None,
    plugin_usage: Optional[Dict[str, int]] = None,
    gstack_analytics: Optional[List[str]] = None,
    untagged_reference_candidates: Optional[List[Dict[str, Any]]] = None,
    hardcoded_values: Optional[List[Dict[str, Any]]] = None,
    coherence_report: Optional[List[str]] = None,
    telemetry_report: Optional[List[str]] = None,
    constitutional_report: Optional[List[str]] = None,
    environment_report: Optional[List[str]] = None,
    pipeline_health_report: Optional[List[str]] = None,
    cross_project_report: Optional[List[str]] = None,
    growth_report: Optional[List[str]] = None,
) -> str:
    """1画面レポートを生成する。"""
    lines = ["# Environment Audit Report", ""]

    # Growth Report (NFD) — 最上部に表示
    if growth_report:
        lines.extend(growth_report)

    # セクション順序: Environment Fitness → Constitutional → Coherence → Telemetry → Pipeline Health
    if environment_report:
        lines.extend(environment_report)

    if constitutional_report:
        lines.extend(constitutional_report)

    if coherence_report:
        lines.extend(coherence_report)

    if telemetry_report:
        lines.extend(telemetry_report)

    # Pipeline Health（既存スコアセクションの後に配置）
    if pipeline_health_report:
        lines.extend(pipeline_health_report)

    # Cross-Project Summary
    if cross_project_report:
        lines.extend(cross_project_report)

    # サマリ
    total = sum(len(v) for v in artifacts.values())
    lines.append(f"## Summary: {total} artifacts found")
    for category, paths in artifacts.items():
        lines.append(f"- {category}: {len(paths)}")
    lines.append("")

    # 行数超過
    if violations:
        lines.append(f"## Line Limit Violations ({len(violations)})")
        for v in violations:
            lines.append(f"- {v['file']}: {v['lines']}/{v['limit']} lines")
        lines.append("")

    # Memory Health（Line Limit Violations の直後）
    if project_dir is not None:
        memory_health = build_memory_health_section(artifacts, project_dir)
        if memory_health:
            lines.extend(memory_health)

    # Test Guard 導入状況
    if project_dir is not None:
        tg_section = _build_test_guard_section(project_dir)
        if tg_section:
            lines.extend(tg_section)

    # 使用状況（PJ 固有のみ）
    if usage:
        lines.append("## Usage (last 30 days)")
        for skill, count in list(usage.items())[:15]:
            lines.append(f"- {skill}: {count} invocations")
        lines.append("")

    # プラグイン利用サマリ
    if plugin_usage:
        summary_parts = [f"{name}({count})" for name, count in plugin_usage.items()]
        lines.append(f"Plugin usage: {' / '.join(summary_parts)}")
        lines.append("")

    # 品質推移
    if quality_baselines is not None:
        trends = build_quality_trends_section(quality_baselines, usage)
        if trends:
            lines.extend(trends)

    # gstack ワークフロー分析
    if gstack_analytics:
        lines.extend(gstack_analytics)

    # Token Consumption (PJ別 LLM トークン消費)
    token_section = build_token_consumption_section(days=30)
    if token_section:
        lines.extend(token_section)

    # 重複候補
    if duplicates:
        lines.append(f"## Potential Duplicates ({len(duplicates)})")
        for d in duplicates:
            lines.append(f"- {d['name']}: {', '.join(d['paths'])}")
        lines.append("")

    # Hardcoded Values 警告
    if hardcoded_values:
        lines.append(f"## Hardcoded Values ({len(hardcoded_values)})")
        for hv in hardcoded_values:
            detail = hv.get("detail", {})
            lines.append(
                f"- {hv['file']}:{detail.get('line', '?')} "
                f"`{detail.get('matched', '?')}` ({detail.get('pattern_type', '?')}, "
                f"confidence={detail.get('confidence_score', 0):.2f})"
            )
        lines.append("")

    # Reference Type 未設定警告
    if untagged_reference_candidates:
        lines.append(f"## Reference Type Warning ({len(untagged_reference_candidates)})")
        lines.append("以下のスキルはゼロ呼び出しかつ `type` 未設定です。参照型スキルの場合は frontmatter に `type: reference` を追加してください。")
        for c in untagged_reference_candidates:
            lines.append(f"- {c['skill_name']}")
        lines.append("")

    # Scope Advisory
    if advisories:
        lines.append("## Scope Advisory")
        for a in advisories:
            lines.append(
                f"- {a['skill']}: {a['project_count']} projects, "
                f"last used {a['last_used'][:10] if a['last_used'] else 'never'} → {a['recommendation']}"
            )
        lines.append("")

    return "\n".join(lines)


_AUDIT_HISTORY_FILE = DATA_DIR / "audit-history.jsonl"
_MAX_AUDIT_HISTORY = 100
_DEGRADATION_THRESHOLD = 0.10  # 10% drop


def _record_audit_completion(
    coherence_report: Optional[List[str]] = None,
    telemetry_report: Optional[List[str]] = None,
    environment_report: Optional[List[str]] = None,
) -> None:
    """audit 完了時: last_audit_timestamp 更新 + audit-history.jsonl 記録 + 劣化検出。"""
    try:
        # Update last_audit_timestamp in evolve-state.json
        state_file = DATA_DIR / "evolve-state.json"
        state = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        now = datetime.now(timezone.utc).isoformat()
        state["last_audit_timestamp"] = now
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Record to audit-history.jsonl
        history_record: Dict[str, Any] = {"timestamp": now}
        # Extract scores from report lines if available
        for report_lines, key in [
            (coherence_report, "coherence_score"),
            (telemetry_report, "telemetry_score"),
            (environment_report, "environment_score"),
        ]:
            if report_lines:
                score = _extract_score_from_report(report_lines)
                if score is not None:
                    history_record[key] = score

        _append_audit_history(history_record)

        # Degradation detection
        _check_degradation(history_record)
    except Exception as e:
        print(f"[rl-anything:audit] history recording error: {e}", file=sys.stderr)


def _extract_score_from_report(lines: List[str]) -> Optional[float]:
    """レポート行からスコア値を抽出する。"""
    import re
    for line in lines:
        # Match patterns like "Score: 0.85" or "Overall: 0.72"
        m = re.search(r'(?:Score|Overall|Total)[:\s]+(\d+\.?\d*)', line)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _append_audit_history(record: Dict[str, Any]) -> None:
    """audit-history.jsonl にレコードを追記し、pruning する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    if _AUDIT_HISTORY_FILE.exists():
        lines = _AUDIT_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    lines.append(json.dumps(record, ensure_ascii=False))
    # Pruning
    if len(lines) > _MAX_AUDIT_HISTORY:
        lines = lines[-_MAX_AUDIT_HISTORY:]
    _AUDIT_HISTORY_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _check_degradation(current: Dict[str, Any]) -> None:
    """前回スコアとの比較で 10% 以上低下時に警告を出力する。"""
    if not _AUDIT_HISTORY_FILE.exists():
        return
    lines = _AUDIT_HISTORY_FILE.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        return
    try:
        prev = json.loads(lines[-2])
    except (json.JSONDecodeError, IndexError):
        return

    for key in ("coherence_score", "telemetry_score", "environment_score"):
        prev_val = prev.get(key)
        curr_val = current.get(key)
        if prev_val is not None and curr_val is not None and prev_val > 0:
            drop = (prev_val - curr_val) / prev_val
            if drop >= _DEGRADATION_THRESHOLD:
                label = key.replace("_", " ").title()
                print(
                    f"⚠ {label} が {drop:.0%} 低下しています "
                    f"({prev_val:.2f} → {curr_val:.2f})",
                    file=sys.stderr,
                )


def _load_global_retro(gstack_dir: Path = None) -> Optional[Dict[str, Any]]:
    """~/.gstack/retros/global-*.json から最新のグローバルretro結果を取得。

    Args:
        gstack_dir: gstack データディレクトリ（None の場合 ~/.gstack）

    Returns:
        parsed JSON dict or None
        スキーマ: {type, date, window, projects[], totals{}}
    """
    if gstack_dir is None:
        gstack_dir = Path.home() / ".gstack"
    retros_dir = gstack_dir / "retros"
    if not retros_dir.exists():
        return None
    try:
        files = sorted(retros_dir.glob("global-*.json"))
        if not files:
            return None
        # 最新ファイル（ソート順で最後）を読む
        latest_file = files[-1]
        return json.loads(latest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def run_audit(project_dir: Optional[str] = None, skip_rescore: bool = False, coherence_score: bool = False, telemetry_score: bool = False, constitutional_score: bool = False, pipeline_health: bool = False, cross_project: bool = False, growth: bool = False) -> str:
    """Audit を実行してレポートを返す。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)
    violations = check_line_limits(artifacts)
    usage_records = load_usage_data(project_root=proj)
    usage = aggregate_usage(usage_records, exclude_plugins=True)
    plugin_usage = aggregate_plugin_usage(usage_records)
    duplicates = detect_duplicates_simple(artifacts)
    registry = load_usage_registry()
    advisories = scope_advisory(registry)

    # 品質計測統合
    quality_baselines = None
    if not skip_rescore:
        try:
            _scripts_dir = PLUGIN_ROOT / "scripts"
            if str(_scripts_dir) not in sys.path:
                sys.path.insert(0, str(_scripts_dir))
            from quality_monitor import run_quality_monitor
            run_quality_monitor()
        except Exception as e:
            print(f"品質計測スキップ: {e}", file=sys.stderr)

    # ベースラインがあればレポートに含める
    baselines = load_quality_baselines()
    if baselines:
        quality_baselines = baselines

    # gstack ワークフロー分析
    gstack_analytics = build_gstack_analytics_section(usage_records)

    # Reference type 未設定警告
    untagged = detect_untagged_reference_candidates(artifacts, usage, project_dir=proj)

    # Hardcoded values 検出
    hardcoded_values = []
    for category in ("skills", "rules"):
        for path in artifacts.get(category, []):
            detections = detect_hardcoded_values(str(path))
            for det in detections:
                hardcoded_values.append({
                    "type": "hardcoded_value",
                    "file": str(path),
                    "detail": det,
                    "source": "detect_hardcoded_values",
                })

    # Coherence Score
    coherence_report_lines = None
    if coherence_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.coherence import compute_coherence_score, format_coherence_report
            result = compute_coherence_score(proj)
            coherence_report_lines = format_coherence_report(result)
        except Exception as e:
            print(f"Coherence Score スキップ: {e}", file=sys.stderr)

    # Telemetry Score
    telemetry_report_lines = None
    if telemetry_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.telemetry import compute_telemetry_score, format_telemetry_report
            tel_result = compute_telemetry_score(proj)
            telemetry_report_lines = format_telemetry_report(tel_result)
        except Exception as e:
            print(f"Telemetry Score スキップ: {e}", file=sys.stderr)

    # Constitutional Score
    constitutional_report_lines = None
    if constitutional_score:
        try:
            _fitness_dir = PLUGIN_ROOT / "scripts" / "rl"
            if str(_fitness_dir) not in sys.path:
                sys.path.insert(0, str(_fitness_dir))
            from fitness.constitutional import compute_constitutional_score
            from fitness.chaos import compute_chaos_score, format_chaos_report
            con_result = compute_constitutional_score(proj)
            constitutional_report_lines = _format_constitutional_report(con_result)
            # Chaos Testing
            try:
                chaos_result = compute_chaos_score(proj)
                chaos_lines = format_chaos_report(chaos_result)
                if constitutional_report_lines is not None:
                    constitutional_report_lines.extend(chaos_lines)
                else:
                    constitutional_report_lines = chaos_lines
            except Exception as e:
                print(f"Chaos Testing スキップ: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Constitutional Score スキップ: {e}", file=sys.stderr)

    # Environment Fitness（複数スコア指定時）
    environment_report_lines = None
    score_count = sum([coherence_score, telemetry_score, constitutional_score])
    if score_count >= 2:
        try:
            from fitness.environment import compute_environment_fitness, format_environment_report
            env_result = compute_environment_fitness(proj)
            environment_report_lines = format_environment_report(env_result)
        except Exception as e:
            print(f"Environment Fitness スキップ: {e}", file=sys.stderr)

    # Pipeline Health（LLM 不使用）
    pipeline_health_report_lines = None
    if pipeline_health:
        try:
            from pipeline_reflector import build_pipeline_health_section
            pipeline_health_report_lines = build_pipeline_health_section()
        except Exception as e:
            print(f"Pipeline Health スキップ: {e}", file=sys.stderr)

    # Cross-project summary from gstack retro global
    cross_project_report_lines = None
    if cross_project:
        retro = _load_global_retro()
        if retro is not None:
            cross_project_report_lines = ["## Cross-Project Summary (from /retro global)", ""]
            projects = retro.get("projects", [])
            totals = retro.get("totals", {})
            cross_project_report_lines.append(f"- Projects: {len(projects)}")
            if "sessions" in totals:
                cross_project_report_lines.append(f"- Total sessions: {totals['sessions']}")
            if "streak" in totals:
                cross_project_report_lines.append(f"- Streak: {totals['streak']}")
            cross_project_report_lines.append("")

    # Record audit completion: update last_audit_timestamp + audit-history.jsonl
    _record_audit_completion(
        coherence_report=coherence_report_lines,
        telemetry_report=telemetry_report_lines,
        environment_report=environment_report_lines,
    )

    # ── NFD Growth Report ──────────────────────────────────────
    growth_report_lines = None
    if growth:
        # skip_rescore=True のとき LLM eval（constitutional）をスキップして
        # fleet の 10s timeout 内で完了させる (#86)
        # issues_summary は audit run の検出結果から組み立てて growth-state に
        # 書き込む（fleet status の ISSUES 列で読まれる、#22）
        from issues_summary import compute_issues_summary
        from telemetry_query import query_corrections
        _project_name_for_issues = proj.resolve().name
        _corrections = query_corrections(project=_project_name_for_issues)
        _issues = compute_issues_summary(
            violations=violations,
            hardcoded_values=hardcoded_values,
            duplicates=duplicates,
            corrections=_corrections,
            quality_baselines=quality_baselines,
        )
        growth_report_lines = _build_growth_report(
            proj, skip_llm=skip_rescore, issues_summary=_issues,
        )

    return generate_report(
        artifacts, violations, usage, duplicates, advisories,
        quality_baselines, project_dir=proj,
        plugin_usage=plugin_usage if plugin_usage else None,
        gstack_analytics=gstack_analytics if gstack_analytics else None,
        untagged_reference_candidates=untagged if untagged else None,
        hardcoded_values=hardcoded_values if hardcoded_values else None,
        coherence_report=coherence_report_lines,
        telemetry_report=telemetry_report_lines,
        constitutional_report=constitutional_report_lines,
        environment_report=environment_report_lines,
        pipeline_health_report=pipeline_health_report_lines,
        cross_project_report=cross_project_report_lines,
        growth_report=growth_report_lines,
    )


def _build_growth_report(proj: Path, *, skip_llm: bool = False, issues_summary: Optional[Any] = None) -> List[str]:
    """NFD Growth Report セクションを生成する。

    Args:
        proj: プロジェクトディレクトリ
        skip_llm: True の場合、compute_environment_fitness に skip_llm=True を伝播し、
            LLM（constitutional）軸をスキップして軽量軸のみで env_score を算出する。
            rl-fleet status の 10s timeout 対応 (#86)。
        issues_summary: IssuesSummary instance — growth-state cache の `issues_summary`
            キーに dict として書き込む。fleet status (#22) が読み取る。None なら
            未書き込み（旧 cache 互換）。
    """
    lines = ["## 🌱 Growth Report (NFD)", ""]
    project_name = proj.resolve().name
    try:
        _scripts_lib = PLUGIN_ROOT / "scripts" / "lib"
        if str(_scripts_lib) not in sys.path:
            sys.path.insert(0, str(_scripts_lib))

        from growth_engine import read_cache, detect_phase, compute_phase_progress, update_cache, PHASE_DISPLAY_NAMES, Phase
        from growth_journal import query_crystallizations, count_crystallized_rules
        from growth_narrative import compute_profile, generate_story
        from growth_level import compute_level

        # テレメトリからフェーズ判定
        from telemetry_query import query_sessions, query_corrections
        sessions = query_sessions(project=project_name)
        corrections = query_corrections(project=project_name)
        crystallized = count_crystallized_rules(project=project_name)
        sessions_count = len(sessions) if sessions else 0
        corrections_count = len(corrections) if corrections else 0

        # env_score 計算（coherence 含む正確なスコア）
        _fitness_dir = PLUGIN_ROOT / "scripts" / "rl" / "fitness"
        if str(_fitness_dir) not in sys.path:
            sys.path.insert(0, str(_fitness_dir))
        env_score = 0.0
        coherence_score = 0.0
        try:
            from environment import compute_environment_fitness
            env_result = compute_environment_fitness(proj, skip_llm=skip_llm)
            env_score = env_result.get("overall", 0.0) if isinstance(env_result, dict) else 0.0
            coherence_score = env_result.get("axes", {}).get("coherence", {}).get("score", 0.0) if isinstance(env_result, dict) else 0.0
        except Exception:
            pass

        phase = detect_phase(sessions_count, corrections_count, crystallized, coherence_score)
        progress = compute_phase_progress(phase, sessions_count, corrections_count, crystallized, coherence_score)
        names = PHASE_DISPLAY_NAMES[phase]

        # Level 計算
        level_info = compute_level(env_score)

        # キャッシュ更新（env_score + level + issues_summary を含む）
        _cache_extra = {
            "sessions_count": sessions_count,
            "crystallizations_count": crystallized,
            "env_score": round(env_score, 4),
            "level": level_info.level,
            "title_en": level_info.title_en,
            "title_ja": level_info.title_ja,
        }
        if issues_summary is not None and hasattr(issues_summary, "to_dict"):
            _cache_extra["issues_summary"] = issues_summary.to_dict()
        update_cache(project_name, phase, progress, _cache_extra)

        progress_pct = int(progress * 100)
        bar_filled = int(progress * 20)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)

        lines.append(f"**Level:** Lv.{level_info.level} {level_info.title_en} ({level_info.title_ja})")
        lines.append(f"**Environment Score:** {env_score:.2f}")
        lines.append(f"**Phase:** {names['en']} ({names['ja']})")
        lines.append(f"**Progress:** [{bar}] {progress_pct}%")
        lines.append(f"**Sessions:** {sessions_count} | **Corrections:** {corrections_count} | **Crystallizations:** {crystallized}")
        lines.append("")

        # 結晶化ログ
        events = query_crystallizations(project=project_name)
        if events:
            lines.append("### Crystallization Log")
            for ev in events[-10:]:  # 最新10件
                ts = ev.get("ts", "")[:10]
                targets = ", ".join(ev.get("targets", [])[:3]) or "(no targets)"
                lines.append(f"- {ts}: {targets}")
            lines.append("")

        # Environment Profile
        profile = compute_profile(project_name)
        if profile.strengths or profile.personality_traits:
            lines.append("### Environment Profile")
            if profile.strengths:
                lines.append(f"**Strengths:** {', '.join(profile.strengths)}")
            if profile.personality_traits:
                lines.append(f"**Traits:** {', '.join(profile.personality_traits)}")
            lines.append(f"**Style:** {profile.crystallization_style}")
            lines.append("")

        # Growth Story
        story = generate_story(project_name)
        if story and "まだ" not in story:
            lines.append("### Growth Story")
            lines.append(story)
            lines.append("")

        # Next Milestone
        lines.append("### Next Milestone")
        if phase == Phase.MATURE_OPERATION:
            lines.append("最終フェーズに到達しています。")
        else:
            next_phases = {
                Phase.BOOTSTRAP: ("Initial Nurturing", "sessions >= 10"),
                Phase.INITIAL_NURTURING: ("Structured Nurturing", "sessions >= 50, corrections >= 10, crystallized_rules >= 3"),
                Phase.STRUCTURED_NURTURING: ("Mature Operation", "sessions > 200, crystallized_rules >= 10, coherence >= 0.7"),
            }
            next_name, next_req = next_phases.get(phase, ("?", "?"))
            lines.append(f"Next phase: **{next_name}** — requires: {next_req}")
        lines.append("")

    except Exception as e:
        lines.append(f"Growth Report の生成に失敗しました: {e}")
        lines.append("")

    return lines


def main() -> None:
    """bin/rl-audit エントリポイント。"""
    import argparse as _argparse

    _parser = _argparse.ArgumentParser(description="環境の健康診断")
    _parser.add_argument("project", nargs="?", default=None, help="プロジェクトディレクトリ")
    _parser.add_argument("--skip-rescore", action="store_true", help="品質計測をスキップ")
    _parser.add_argument("--memory-context", action="store_true", help="MEMORY 検証コンテキストを JSON 出力")
    _parser.add_argument("--coherence-score", action="store_true", help="Coherence Score セクションを表示")
    _parser.add_argument("--telemetry-score", action="store_true", help="Telemetry Score セクションを表示")
    _parser.add_argument("--constitutional-score", action="store_true", help="Constitutional Score セクションを表示")
    _parser.add_argument("--pipeline-health", action="store_true", help="Pipeline Health セクションを表示")
    _parser.add_argument("--growth", action="store_true", help="NFD Growth Report セクションを表示")
    _args = _parser.parse_args()
    if _args.memory_context:
        proj = Path(_args.project) if _args.project else Path.cwd()
        ctx = build_memory_verification_context(proj)
        print(json.dumps(ctx, ensure_ascii=False, indent=2))
    else:
        print(run_audit(_args.project, skip_rescore=_args.skip_rescore, coherence_score=_args.coherence_score, telemetry_score=_args.telemetry_score, constitutional_score=_args.constitutional_score, pipeline_health=_args.pipeline_health, growth=_args.growth))


if __name__ == "__main__":
    main()
