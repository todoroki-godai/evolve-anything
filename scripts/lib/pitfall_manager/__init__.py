#!/usr/bin/env python3
"""Pitfall 品質ゲート & ライフサイクル管理。

Candidate→New 2段階昇格、3層コンテキスト管理、
状態機械（Candidate→New→Active→Graduated→Pruned）、
回避回数ベース卒業判定を提供する。
"""
import json
import re
import shutil
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import sys

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from similarity import jaccard_coefficient, tokenize

# Cold 層自動アーカイブ定数
CAP_EXCEEDED_CONFIDENCE = 0.90
PREFLIGHT_MATURITY_RATIO = 0.50
# スクリプト化可能なカテゴリ
SCRIPTIFIABLE_CATEGORIES = frozenset({"action", "tool_use", "output"})

from skill_evolve import (
    ACTIVE_PITFALL_CAP,
    CANDIDATE_PROMOTION_COUNT,
    ERROR_FREQUENCY_THRESHOLD,
    GRADUATED_TTL_DAYS,
    GRADUATION_THRESHOLDS,
    HOT_TIER_MAX_ITEMS,
    INTEGRATION_JACCARD_THRESHOLD,
    PITFALL_MAX_LINES,
    RATIONALIZATION_MIN_CORRECTIONS,
    RATIONALIZATION_OUTCOME_WINDOW_DAYS,
    RATIONALIZATION_SKIP_KEYWORDS,
    ROOT_CAUSE_JACCARD_THRESHOLD,
    STALE_ESCALATION_MONTHS,
    STALE_KNOWLEDGE_MONTHS,
)

# --- Pitfall パース / 3層コンテキスト (parser.py へ分離) ---
from .parser import (  # noqa: E402,F401
    _FIELD_RE,
    _PITFALL_HEADER_RE,
    _flush_item,
    get_cold_tier,
    get_hot_tier,
    get_warm_tier,
    parse_pitfalls,
    render_pitfalls,
)


# --- 品質ゲート / 状態機械 (recording.py へ分離) ---
from .recording import (  # noqa: E402,F401
    _make_pitfall_entry,
    _safe_read,
    _write_empty_template,
    find_matching_candidate,
    graduate_pitfall,
    promote_to_active,
    record_pitfall,
)


# --- Root-cause / 統合済み判定 / 自動検出 / TTL アーカイブ (detection.py へ分離) ---
from .detection import (  # noqa: E402,F401
    _STOP_WORDS,
    _split_sections_from_content,
    detect_archive_candidates,
    detect_integration,
    execute_archive,
    extract_pitfall_candidates,
    extract_root_cause_keywords,
)


# --- 行数ガード ---


def _compute_line_guard(
    sections: Dict[str, List[Dict[str, Any]]],
    content: str,
) -> Dict[str, Any]:
    """行数ガード: PITFALL_MAX_LINES 超過時に Cold 層から削除候補を生成。

    Returns:
        {"line_count": int, "line_guard_candidates": [...], "warning": str|None}
    """
    line_count = len(content.splitlines())
    if line_count <= PITFALL_MAX_LINES:
        return {"line_count": line_count, "line_guard_candidates": [], "warning": None}

    excess = line_count - PITFALL_MAX_LINES

    # Cold 層を古い順にソート
    cold_items: List[Dict[str, Any]] = []

    # Graduated: Graduated-date 古い順
    for item in sections.get("graduated", []):
        cold_items.append({
            "title": item["title"],
            "sort_key": item["fields"].get("Graduated-date", "9999-99-99"),
            "category": "graduated",
            "raw_lines": len(item.get("raw", "").splitlines()),
        })

    # Candidate: First-seen 古い順
    for item in sections.get("candidate", []):
        cold_items.append({
            "title": item["title"],
            "sort_key": item["fields"].get("First-seen", "9999-99-99"),
            "category": "candidate",
            "raw_lines": len(item.get("raw", "").splitlines()),
        })

    cold_items.sort(key=lambda x: x["sort_key"])

    candidates: List[Dict[str, Any]] = []
    removed_lines = 0
    for ci in cold_items:
        if removed_lines >= excess:
            break
        candidates.append({
            "title": ci["title"],
            "category": ci["category"],
            "lines": ci["raw_lines"],
        })
        removed_lines += ci["raw_lines"]

    warning = None
    if removed_lines < excess:
        warning = "Active/New 項目の手動レビューが必要"

    return {
        "line_count": line_count,
        "line_guard_candidates": candidates,
        "warning": warning,
    }


# --- Pre-flight スクリプト提案 ---


_CATEGORY_TEMPLATE_MAP = {
    "action": "action.sh",
    "tool_use": "tool_use.sh",
    "output": "output.sh",
}


def suggest_preflight_script(
    pitfall: Dict[str, Any],
    templates_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Pre-flight 対応 pitfall にテンプレートパスを提案する。

    Returns:
        {"pitfall_title": str, "category": str, "template_path": str} or None
    """
    if pitfall["fields"].get("Pre-flight対応", "").lower() != "yes":
        return None
    if pitfall["fields"].get("Status") != "Active":
        return None

    root_cause = pitfall["fields"].get("Root-cause", "")
    category = root_cause.split("—")[0].strip().lower() if "—" in root_cause else "generic"

    tmpl_dir = templates_dir or (_plugin_root.parent / "skills" / "evolve" / "templates" / "preflight")
    template_name = _CATEGORY_TEMPLATE_MAP.get(category)
    if template_name is None:
        template_name = "generic.sh"
        category = "generic"

    template_path = tmpl_dir / template_name

    # フォールバック: ファイルが存在しない場合
    if not template_path.exists():
        template_path = tmpl_dir / "generic.sh"
        template_name = "generic.sh"
        category = "generic"

    return {
        "pitfall_title": pitfall["title"],
        "category": category,
        "template_path": str(template_path),
    }


# --- 合理化防止テーブル (superpowers-knowledge-integration) ---


def detect_rationalization_patterns(
    corrections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """corrections からスキップ/バイパスの合理化パターンを検出する。

    Returns:
        [{"excuse": str, "corrections": [dict], "sample_count": int}]
    """
    keyword_set = {kw.lower() for kw in RATIONALIZATION_SKIP_KEYWORDS}
    groups: Dict[str, List[Dict[str, Any]]] = {}

    for rec in corrections:
        if not isinstance(rec, dict):
            continue
        message = rec.get("message", "")
        if not message:
            continue
        msg_lower = message.lower()
        matched = [kw for kw in keyword_set if kw in msg_lower]
        if not matched:
            continue
        # グルーピングキー: 最初にマッチしたキーワード
        key = sorted(matched, key=lambda k: msg_lower.index(k))[0]
        excuse = message[:120]
        if excuse not in groups:
            groups[excuse] = []
        groups[excuse].append(rec)

    return [
        {"excuse": excuse, "corrections": recs, "sample_count": len(recs)}
        for excuse, recs in groups.items()
        if len(recs) >= 1
    ]


def generate_rationalization_table(
    corrections: List[Dict[str, Any]],
    usage: Optional[List[Dict[str, Any]]] = None,
    errors: Optional[List[Dict[str, Any]]] = None,
    *,
    existing_pitfalls: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """合理化防止テーブルを生成する。

    corrections のスキップパターンをテレメトリと突合し、
    「言い訳 vs 実際の結果」テーブルを生成する。

    Args:
        corrections: corrections.jsonl のレコード群
        usage: usage.jsonl のレコード群
        errors: errors.jsonl のレコード群
        existing_pitfalls: 既存 pitfall セクション（重複チェック用）

    Returns:
        {"data_insufficient": bool, "table": [...], "enriched_pitfalls": [...]}
    """
    patterns = detect_rationalization_patterns(corrections)
    total_skip_corrections = sum(p["sample_count"] for p in patterns)

    if total_skip_corrections < RATIONALIZATION_MIN_CORRECTIONS:
        return {"data_insufficient": True, "table": [], "enriched_pitfalls": []}

    error_list = errors or []
    table: List[Dict[str, Any]] = []
    enriched_pitfalls: List[Dict[str, Any]] = []

    for pattern in patterns:
        excuse = pattern["excuse"]
        sample_count = pattern["sample_count"]

        # テレメトリ突合: パターン発生時期の前後でエラー率を算出
        outcome_error_rate: Optional[float] = None
        telemetry_source = "corrections_only"

        if error_list and pattern["corrections"]:
            # corrections のタイムスタンプ前後 OUTCOME_WINDOW_DAYS のエラーを集計
            post_errors = 0
            for corr in pattern["corrections"]:
                corr_ts = corr.get("timestamp", "")
                if not corr_ts:
                    continue
                try:
                    corr_dt = datetime.fromisoformat(corr_ts.replace("Z", "+00:00"))
                    window_end = corr_dt + timedelta(days=RATIONALIZATION_OUTCOME_WINDOW_DAYS)
                    for err in error_list:
                        err_ts = err.get("timestamp", "")
                        if not err_ts:
                            continue
                        try:
                            err_dt = datetime.fromisoformat(err_ts.replace("Z", "+00:00"))
                            if corr_dt <= err_dt <= window_end:
                                post_errors += 1
                        except (ValueError, TypeError):
                            continue
                except (ValueError, TypeError):
                    continue

            if sample_count > 0:
                outcome_error_rate = round(post_errors / sample_count, 2)
                telemetry_source = "usage+errors"

        entry = {
            "excuse": excuse,
            "outcome_error_rate": outcome_error_rate,
            "sample_count": sample_count,
            "telemetry_source": telemetry_source,
        }
        table.append(entry)

        # 既存 pitfall との Jaccard 重複チェック → エンリッチ
        if existing_pitfalls:
            excuse_tokens = tokenize(excuse)
            for section_key in ("active", "candidate"):
                for pitfall in existing_pitfalls.get(section_key, []):
                    root_cause = pitfall["fields"].get("Root-cause", "")
                    pitfall_tokens = tokenize(root_cause)
                    if excuse_tokens and pitfall_tokens:
                        score = jaccard_coefficient(excuse_tokens, pitfall_tokens)
                        if score >= ROOT_CAUSE_JACCARD_THRESHOLD:
                            enriched_pitfalls.append({
                                "pitfall_title": pitfall["title"],
                                "matched_excuse": excuse,
                                "jaccard_score": round(score, 3),
                                "telemetry_data": entry,
                            })

    # sample_count で降順ソート
    table.sort(key=lambda x: x["sample_count"], reverse=True)

    return {
        "data_insufficient": False,
        "table": table,
        "enriched_pitfalls": enriched_pitfalls,
    }


# --- Pitfall 剪定 (pitfall_hygiene) ---


def pitfall_hygiene(
    project_dir: Optional[Path] = None,
    *,
    frequency_scores: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """自己進化済みスキルの pitfall を剪定する。

    Args:
        project_dir: プロジェクトディレクトリ
        frequency_scores: スキル名→実行頻度スコアのマップ（省略時はデフォルト閾値）

    Returns:
        {"skills_checked": int, "graduation_candidates": [...],
         "graduation_proposals": [...], "archive_candidates": [...],
         "codegen_proposals": [...], "line_count": int,
         "cap_exceeded": [...], "stale_warnings": [...],
         "cross_skill_analysis": {...}}
    """
    from skill_evolve import is_self_evolved_skill

    _audit_scripts = _plugin_root / "skills" / "audit" / "scripts"
    if str(_audit_scripts) not in sys.path:
        sys.path.insert(0, str(_audit_scripts))
    from audit import find_artifacts

    proj = project_dir or Path.cwd()
    artifacts = find_artifacts(proj)

    freq_scores = frequency_scores or {}
    graduation_candidates: List[Dict[str, Any]] = []
    graduation_proposals: List[Dict[str, Any]] = []
    all_archive_candidates: List[Dict[str, Any]] = []
    codegen_proposals: List[Dict[str, Any]] = []
    cap_exceeded: List[Dict[str, Any]] = []
    stale_warnings: List[Dict[str, Any]] = []
    all_root_causes: Dict[str, List[str]] = {}  # category → [skill_names]
    hygiene_issues: List[Dict[str, Any]] = []
    preflight_candidates: List[Dict[str, Any]] = []
    skills_checked = 0
    total_line_count = 0

    for skill_path in artifacts.get("skills", []):
        skill_dir = skill_path.parent
        skill_name = skill_dir.name

        if not is_self_evolved_skill(skill_dir):
            continue

        skills_checked += 1
        pitfalls_path = skill_dir / "references" / "pitfalls.md"
        if not pitfalls_path.exists():
            continue

        content = pitfalls_path.read_text(encoding="utf-8")
        sections = parse_pitfalls(content)

        # 卒業判定
        freq = freq_scores.get(skill_name, 1)
        threshold = GRADUATION_THRESHOLDS.get(freq, 3)

        for item in sections.get("active", []):
            if item["fields"].get("Status") != "Active":
                continue

            avoidance = int(item["fields"].get("Avoidance-count", "0") or "0")
            if avoidance >= threshold:
                graduation_candidates.append({
                    "skill_name": skill_name,
                    "pitfall_title": item["title"],
                    "avoidance_count": avoidance,
                    "threshold": threshold,
                })

            # 統合済み判定 → graduation_proposals
            integration = detect_integration(item, skill_dir)
            if integration["integration_detected"]:
                graduation_proposals.append({
                    "skill_name": skill_name,
                    "pitfall_title": item["title"],
                    "integration_target": integration["integration_target"],
                    "confidence": integration["confidence"],
                })

            # Stale Knowledge ガード
            last_seen = item["fields"].get("Last-seen", "")
            if last_seen:
                try:
                    last_dt = datetime.strptime(last_seen, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    months_ago = (datetime.now(timezone.utc) - last_dt).days / 30
                    if months_ago >= STALE_KNOWLEDGE_MONTHS:
                        stale_warnings.append({
                            "skill_name": skill_name,
                            "pitfall_title": item["title"],
                            "last_seen": last_seen,
                            "months_since": round(months_ago, 1),
                        })
                except ValueError:
                    pass

            # Pre-flight スクリプト提案
            proposal = suggest_preflight_script(item)
            if proposal:
                codegen_proposals.append(proposal)

        # Pre-flight スクリプト化候補検出
        for item in sections.get("active", []):
            if item["fields"].get("Status") != "Active":
                continue
            avoidance = int(item["fields"].get("Avoidance-count", "0") or "0")
            root_cause = item["fields"].get("Root-cause", "")
            category = root_cause.split("—")[0].strip().lower() if "—" in root_cause else ""
            if (
                category in SCRIPTIFIABLE_CATEGORIES
                and item["fields"].get("Pre-flight対応", "").lower().startswith("yes")
                and avoidance >= threshold * PREFLIGHT_MATURITY_RATIO
            ):
                template = suggest_preflight_script(item)
                preflight_candidates.append({
                    "pitfall_id": item["title"],
                    "skill_name": skill_name,
                    "category": category,
                    "avoidance_count": avoidance,
                    "template": template.get("template_path", "") if template else "",
                })

        # Active 上限チェック
        active_count = sum(
            1 for item in sections.get("active", [])
            if item["fields"].get("Status") == "Active"
        )
        cold_count = len(get_cold_tier(sections))
        if active_count > ACTIVE_PITFALL_CAP:
            cap_exceeded.append({
                "skill_name": skill_name,
                "active_count": active_count,
                "cap": ACTIVE_PITFALL_CAP,
                "cold_count": cold_count,
            })
            hygiene_issues.append({
                "type": "cap_exceeded",
                "file": str(pitfalls_path),
                "detail": {
                    "skill_name": skill_name,
                    "active_count": active_count,
                    "cap": ACTIVE_PITFALL_CAP,
                    "cold_count": cold_count,
                },
                "source": "pitfall_hygiene",
            })

        # TTL アーカイブ候補
        archive_cands = detect_archive_candidates(sections)
        for ac in archive_cands:
            ac["skill_name"] = skill_name
        all_archive_candidates.extend(archive_cands)

        # 行数ガード
        line_guard = _compute_line_guard(sections, content)
        total_line_count += line_guard["line_count"]
        if line_guard["line_guard_candidates"]:
            for lgc in line_guard["line_guard_candidates"]:
                lgc["skill_name"] = skill_name
            all_archive_candidates.extend(line_guard["line_guard_candidates"])
            hygiene_issues.append({
                "type": "line_guard",
                "file": str(pitfalls_path),
                "detail": {
                    "skill_name": skill_name,
                    "line_count": line_guard["line_count"],
                    "max_lines": PITFALL_MAX_LINES,
                    "cold_count": cold_count,
                },
                "source": "pitfall_hygiene",
            })

        # 横断分析: 根本原因カテゴリ集計
        for item in sections.get("active", []):
            cause = item["fields"].get("Root-cause", "")
            category = cause.split("—")[0].strip() if "—" in cause else cause.split("-")[0].strip()
            category = category.lower().strip()
            if category:
                if category not in all_root_causes:
                    all_root_causes[category] = []
                if skill_name not in all_root_causes[category]:
                    all_root_causes[category].append(skill_name)

    # 横断分析: 3スキル以上で同じカテゴリ
    cross_skill = {
        cat: skills
        for cat, skills in all_root_causes.items()
        if len(skills) >= 3
    }

    # preflight_candidates → issue 化
    for pc in preflight_candidates:
        hygiene_issues.append({
            "type": "preflight_scriptification",
            "file": "",  # pitfall 単位なのでファイル不要
            "detail": {
                "pitfall_title": pc["pitfall_id"],
                "skill_name": pc["skill_name"],
                "category": pc["category"],
                "avoidance_count": pc["avoidance_count"],
                "template_path": pc.get("template", ""),
            },
            "source": "pitfall_hygiene",
        })

    # 合理化防止テーブル生成 (superpowers-knowledge-integration)
    rationalization_table: Dict[str, Any] = {"data_insufficient": True, "table": [], "enriched_pitfalls": []}
    try:
        import telemetry_query
        corrections = telemetry_query.query_corrections(
            project=proj.name if proj else None,
        )
        errors_data = telemetry_query.query_errors(
            project=proj.name if proj else None,
        )
        # 全スキルの pitfall セクションを集約
        all_pitfall_sections: Dict[str, List[Dict[str, Any]]] = {"active": [], "candidate": [], "graduated": []}
        for skill_path in artifacts.get("skills", []):
            pf_path = skill_path.parent / "references" / "pitfalls.md"
            if pf_path.exists():
                pf_content = pf_path.read_text(encoding="utf-8")
                pf_sections = parse_pitfalls(pf_content)
                for key in all_pitfall_sections:
                    all_pitfall_sections[key].extend(pf_sections.get(key, []))

        rationalization_table = generate_rationalization_table(
            corrections,
            errors=errors_data,
            existing_pitfalls=all_pitfall_sections,
        )
    except Exception:
        pass  # テレメトリ取得失敗時は data_insufficient のまま

    return {
        "skills_checked": skills_checked,
        "graduation_candidates": sorted(
            graduation_candidates,
            key=lambda x: x["avoidance_count"],
            reverse=True,
        ),
        "graduation_proposals": graduation_proposals,
        "archive_candidates": all_archive_candidates,
        "codegen_proposals": codegen_proposals,
        "line_count": total_line_count,
        "cap_exceeded": cap_exceeded,
        "stale_warnings": stale_warnings,
        "cross_skill_analysis": cross_skill if cross_skill else {"status": "問題なし"},
        "issues": hygiene_issues,
        "preflight_candidates": preflight_candidates,
        "rationalization_table": rationalization_table,
    }
