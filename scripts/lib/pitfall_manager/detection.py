"""Pitfall 検出系: Root-cause キーワード抽出 / 統合済み判定 /
corrections・errors からの自動抽出 / TTL ベースのアーカイブ判定 / 削除実行。
"""
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from similarity import jaccard_coefficient, tokenize
from skill_evolve import (
    CANDIDATE_PROMOTION_COUNT,
    ERROR_FREQUENCY_THRESHOLD,
    GRADUATED_TTL_DAYS,
    INTEGRATION_JACCARD_THRESHOLD,
    ROOT_CAUSE_JACCARD_THRESHOLD,
    STALE_ESCALATION_MONTHS,
    STALE_KNOWLEDGE_MONTHS,
)

from .parser import parse_pitfalls, render_pitfalls
from .recording import _safe_read, find_matching_candidate

# --- Root-cause キーワード抽出 ---

# ストップワード（日本語助詞・英語冠詞等）
_STOP_WORDS = frozenset({
    "の", "は", "が", "を", "に", "で", "と", "も", "や", "へ", "から", "まで",
    "より", "など", "か", "て", "た", "だ", "な", "する", "ある", "いる", "れる",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "and", "or", "but", "not", "no", "it", "its", "this", "that",
})


def extract_root_cause_keywords(root_cause: str) -> Set[str]:
    """Root-cause 文字列からキーワード集合を抽出する。

    D2.1: 「—」（em dash）で分割し後半部分を単語分割、ストップワード除外。
    """
    parts = root_cause.split("—")
    text = parts[-1] if len(parts) >= 2 else root_cause
    tokens = tokenize(text)
    return tokens - _STOP_WORDS


def _split_sections_from_content(content: str) -> List[str]:
    """Markdown テキストを ## 見出し単位でセクション分割する。

    D2.2: YAML frontmatter を除外。
    """
    # frontmatter 除外
    stripped = content
    if stripped.startswith("---"):
        end = stripped.find("---", 3)
        if end != -1:
            stripped = stripped[end + 3:]

    sections: List[str] = []
    current: List[str] = []
    for line in stripped.splitlines():
        if re.match(r"^##\s+", line):
            if current:
                sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


# --- 統合済み判定 ---


def detect_integration(
    pitfall: Dict[str, Any],
    skill_dir: Path,
) -> Dict[str, Any]:
    """Active pitfall の SKILL.md / references/ との統合済み判定。

    Args:
        pitfall: parse_pitfalls() の要素
        skill_dir: スキルディレクトリ

    Returns:
        {"integration_detected": bool, "integration_target": str|None, "confidence": float}
    """
    root_cause = pitfall["fields"].get("Root-cause", "")
    keywords = extract_root_cause_keywords(root_cause)
    if not keywords:
        return {"integration_detected": False, "integration_target": None, "confidence": 0.0}

    # D2.2: SKILL.md 突合
    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        sections = _split_sections_from_content(content)
        for section_text in sections:
            section_tokens = tokenize(section_text) - _STOP_WORDS
            score = jaccard_coefficient(keywords, section_tokens)
            if score >= INTEGRATION_JACCARD_THRESHOLD:
                return {
                    "integration_detected": True,
                    "integration_target": "SKILL.md",
                    "confidence": round(score, 3),
                }

    # D2.3: references/ 突合（pitfalls.md 除外）
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        for ref_file in sorted(refs_dir.glob("*.md")):
            if ref_file.name == "pitfalls.md":
                continue
            ref_content = ref_file.read_text(encoding="utf-8")
            ref_sections = _split_sections_from_content(ref_content)
            for section_text in ref_sections:
                section_tokens = tokenize(section_text) - _STOP_WORDS
                score = jaccard_coefficient(keywords, section_tokens)
                if score >= INTEGRATION_JACCARD_THRESHOLD:
                    return {
                        "integration_detected": True,
                        "integration_target": f"references/{ref_file.name}",
                        "confidence": round(score, 3),
                    }

    return {"integration_detected": False, "integration_target": None, "confidence": 0.0}


# --- 自動検出 ---


def extract_pitfall_candidates(
    corrections: List[Dict[str, Any]],
    errors: Optional[List[Dict[str, Any]]] = None,
    skill_name: Optional[str] = None,
    existing_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """corrections/errors から pitfall Candidate を自動抽出する。

    Args:
        corrections: corrections.jsonl のレコード群
        errors: errors.jsonl のレコード群（省略可）
        skill_name: フィルタ対象スキル名（省略時は全スキル）
        existing_candidates: 既存 Candidate リスト（重複排除用）

    Returns:
        {"candidates": [...], "occurrence_increments": [...], "skipped": int}
    """
    candidates: List[Dict[str, Any]] = []
    occurrence_increments: List[Dict[str, Any]] = []
    skipped = 0
    existing = existing_candidates or []

    # corrections からの抽出
    for rec in corrections:
        try:
            if not isinstance(rec, dict):
                skipped += 1
                continue
            ct = rec.get("correction_type", "")
            if ct not in ("stop", "iya"):
                continue
            last_skill = rec.get("last_skill")
            if not last_skill:  # null or empty string
                continue
            if skill_name and last_skill != skill_name:
                continue
            message = rec.get("message", "")
            if not message:
                continue

            # Root-cause 生成: correction_type — message の要約
            root_cause = f"{ct} — {message[:100]}"

            # D6: 既存 Candidate との重複チェック
            match_idx = find_matching_candidate(existing, root_cause)
            if match_idx is not None:
                # Occurrence-count += 1
                count = int(existing[match_idx]["fields"].get("Occurrence-count", "1") or "1")
                existing[match_idx]["fields"]["Occurrence-count"] = str(count + 1)
                occurrence_increments.append({
                    "title": existing[match_idx]["title"],
                    "new_count": count + 1,
                    "auto_promote": (count + 1) >= CANDIDATE_PROMOTION_COUNT,
                })
                continue

            # 新規候補との重複チェック
            dup = False
            for c in candidates:
                c_tokens = tokenize(c["root_cause"])
                new_tokens = tokenize(root_cause)
                if jaccard_coefficient(c_tokens, new_tokens) >= ROOT_CAUSE_JACCARD_THRESHOLD:
                    dup = True
                    break
            if dup:
                continue

            candidates.append({
                "title": f"Auto: {last_skill} — {ct}",
                "root_cause": root_cause,
                "skill_name": last_skill,
                "source": "corrections",
            })
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue

    # errors からの頻出パターン検出
    if errors:
        error_counter: Counter = Counter()
        error_samples: Dict[str, str] = {}
        for rec in errors:
            try:
                err_skill = rec.get("skill_name")
                if not err_skill:
                    continue
                if skill_name and err_skill != skill_name:
                    continue
                err_msg = rec.get("error_message", "")
                if not err_msg:
                    continue
                # 正規化: 先頭80文字をキーに
                key = f"{err_skill}:{err_msg[:80]}"
                error_counter[key] += 1
                error_samples[key] = err_msg
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue

        for key, count in error_counter.items():
            if count >= ERROR_FREQUENCY_THRESHOLD:
                parts = key.split(":", 1)
                s_name = parts[0]
                root_cause = f"error — {error_samples[key][:100]}"

                # 既存 Candidate・新規候補との重複チェック
                match_idx = find_matching_candidate(existing, root_cause)
                if match_idx is not None:
                    continue
                dup = False
                for c in candidates:
                    c_tokens = tokenize(c["root_cause"])
                    new_tokens = tokenize(root_cause)
                    if jaccard_coefficient(c_tokens, new_tokens) >= ROOT_CAUSE_JACCARD_THRESHOLD:
                        dup = True
                        break
                if dup:
                    continue

                candidates.append({
                    "title": f"Auto: {s_name} — frequent error",
                    "root_cause": root_cause,
                    "skill_name": s_name,
                    "source": "errors",
                    "error_count": count,
                })

    return {
        "candidates": candidates,
        "occurrence_increments": occurrence_increments,
        "skipped": skipped,
    }


# --- TTL アーカイブ ---


def detect_archive_candidates(
    sections: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """TTL ベースのアーカイブ候補を検出する。

    - Graduated: GRADUATED_TTL_DAYS 超過で候補
    - Active/New: STALE_KNOWLEDGE_MONTHS + STALE_ESCALATION_MONTHS (9ヶ月) で候補
    """
    candidates: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    # Graduated TTL
    for item in sections.get("graduated", []):
        grad_date = item["fields"].get("Graduated-date", "")
        if not grad_date:
            continue
        try:
            grad_dt = datetime.strptime(grad_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_since = (now - grad_dt).days
            if days_since > GRADUATED_TTL_DAYS:
                candidates.append({
                    "title": item["title"],
                    "reason": f"卒業から{days_since}日経過",
                    "days_since": days_since,
                    "category": "graduated_ttl",
                })
        except ValueError:
            pass

    # Active/New stale escalation (9ヶ月)
    escalation_months = STALE_KNOWLEDGE_MONTHS + STALE_ESCALATION_MONTHS
    for item in sections.get("active", []):
        status = item["fields"].get("Status", "")
        if status not in ("Active", "New"):
            continue
        last_seen = item["fields"].get("Last-seen", "")
        if not last_seen:
            continue
        try:
            last_dt = datetime.strptime(last_seen, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            months_since = (now - last_dt).days / 30
            if months_since >= escalation_months:
                candidates.append({
                    "title": item["title"],
                    "reason": f"{round(months_since, 1)}ヶ月未更新 — 現在も有効か検証を推奨",
                    "months_since": round(months_since, 1),
                    "category": "stale_escalation",
                })
        except ValueError:
            pass

    return candidates


def execute_archive(
    pitfalls_path: Path,
    titles: List[str],
) -> Dict[str, Any]:
    """指定タイトルの pitfall を pitfalls.md から削除する。

    Returns:
        {"removed": [str], "not_found": [str]}
    """
    content = _safe_read(pitfalls_path)
    sections = parse_pitfalls(content)
    titles_set = set(titles)
    removed: List[str] = []
    not_found: List[str] = []

    for title in titles:
        found = False
        for section_key in ("graduated", "candidate", "active"):
            for i, item in enumerate(sections[section_key]):
                if item["title"] == title:
                    sections[section_key].pop(i)
                    removed.append(title)
                    found = True
                    break
            if found:
                break
        if not found:
            not_found.append(title)

    if removed:
        pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")

    return {"removed": removed, "not_found": not_found}
