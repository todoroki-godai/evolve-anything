"""Pitfall ライフサイクル記録 (Candidate→New→Active→Graduated)。

`pitfalls.md` 上での状態遷移と品質ゲート (Jaccard マッチによる
Candidate→New 昇格) を担う I/O 層。
"""
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from similarity import jaccard_coefficient, tokenize
from skill_evolve import ROOT_CAUSE_JACCARD_THRESHOLD

from .parser import _FIELD_RE, parse_pitfalls, render_pitfalls


def find_matching_candidate(
    candidates: List[Dict[str, Any]],
    root_cause: str,
) -> Optional[int]:
    """既存 Candidate から同一根本原因を Jaccard 類似度で検索する。

    Returns:
        マッチした Candidate のインデックス、またはNone
    """
    new_tokens = tokenize(root_cause)
    for i, candidate in enumerate(candidates):
        existing_cause = candidate["fields"].get("Root-cause", "")
        existing_tokens = tokenize(existing_cause)
        if jaccard_coefficient(new_tokens, existing_tokens) >= ROOT_CAUSE_JACCARD_THRESHOLD:
            return i
    return None


def record_pitfall(
    pitfalls_path: Path,
    title: str,
    root_cause: str,
    *,
    is_user_correction: bool = False,
) -> Dict[str, Any]:
    """新しい pitfall を記録する（品質ゲート適用）。

    Args:
        pitfalls_path: references/pitfalls.md のパス
        title: pitfall のタイトル
        root_cause: 根本原因（カテゴリ — 説明）
        is_user_correction: ユーザー訂正の場合 True（ゲートスキップ）

    Returns:
        {"action": str, "status": str, "title": str}
    """
    content = _safe_read(pitfalls_path)
    sections = parse_pitfalls(content)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if is_user_correction:
        # ゲートスキップ → 即 Active
        new_item = _make_pitfall_entry(title, root_cause, "Active", today)
        sections["active"].append(new_item)
        pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")
        return {"action": "created_active", "status": "Active", "title": title}

    # 既存 Candidate とのマッチング
    match_idx = find_matching_candidate(sections["candidate"], root_cause)

    if match_idx is not None:
        # 同一根本原因2回目 → New に昇格
        candidate = sections["candidate"].pop(match_idx)
        promoted = _make_pitfall_entry(
            candidate["title"], root_cause, "New", today
        )
        sections["active"].append(promoted)  # New は active セクションに配置
        pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")
        return {"action": "promoted_to_new", "status": "New", "title": candidate["title"]}

    # 初回 → Candidate
    new_item = _make_pitfall_entry(title, root_cause, "Candidate", today)
    new_item["fields"]["Occurrence-count"] = "1"
    sections["candidate"].append(new_item)
    pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")
    return {"action": "created_candidate", "status": "Candidate", "title": title}


def promote_to_active(
    pitfalls_path: Path,
    title: str,
) -> bool:
    """New pitfall を Active に昇格する。"""
    content = _safe_read(pitfalls_path)
    sections = parse_pitfalls(content)

    for item in sections["active"]:
        if item["title"] == title and item["fields"].get("Status") == "New":
            item["fields"]["Status"] = "Active"
            item["raw"] = re.sub(
                r"\*\*Status\*\*:\s*New",
                "**Status**: Active",
                item["raw"],
            )
            pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")
            return True
    return False


def graduate_pitfall(
    pitfalls_path: Path,
    title: str,
    integration_target: str = "",
) -> bool:
    """Active pitfall を Graduated に移行する。"""
    content = _safe_read(pitfalls_path)
    sections = parse_pitfalls(content)

    for i, item in enumerate(sections["active"]):
        if item["title"] == title and item["fields"].get("Status") == "Active":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            graduated = _make_pitfall_entry(
                title,
                item["fields"].get("Root-cause", ""),
                "Graduated",
                today,
            )
            graduated["fields"]["Graduated-date"] = today
            if integration_target:
                graduated["fields"]["統合先"] = integration_target
            sections["active"].pop(i)
            sections["graduated"].append(graduated)
            pitfalls_path.write_text(render_pitfalls(sections), encoding="utf-8")
            return True
    return False


def _make_pitfall_entry(
    title: str,
    root_cause: str,
    status: str,
    date: str,
) -> Dict[str, Any]:
    """pitfall エントリを生成する。"""
    if status == "Candidate":
        raw = (
            f"### {title}\n"
            f"- **Status**: Candidate\n"
            f"- **First-seen**: {date}\n"
            f"- **Root-cause**: {root_cause}\n"
            f"- **Occurrence-count**: 1"
        )
    elif status == "Graduated":
        raw = (
            f"### {title}\n"
            f"- **Status**: Graduated\n"
            f"- **Graduated-date**: {date}\n"
            f"- **Root-cause**: {root_cause}"
        )
    else:
        raw = (
            f"### {title}\n"
            f"- **Status**: {status}\n"
            f"- **Last-seen**: {date}\n"
            f"- **Root-cause**: {root_cause}\n"
            f"- **Pre-flight対応**: No\n"
            f"- **Avoidance-count**: 0"
        )
    fields = {}
    for m in _FIELD_RE.finditer(raw):
        fields[m.group(1)] = m.group(2).strip()
    return {"title": title, "fields": fields, "raw": raw}


def _safe_read(pitfalls_path: Path) -> str:
    """pitfalls.md を安全に読み込む。破損時はバックアップ+再作成。"""
    if not pitfalls_path.exists():
        pitfalls_path.parent.mkdir(parents=True, exist_ok=True)
        _write_empty_template(pitfalls_path)
        return pitfalls_path.read_text(encoding="utf-8")

    content = pitfalls_path.read_text(encoding="utf-8")

    if not content.strip():
        _write_empty_template(pitfalls_path)
        return pitfalls_path.read_text(encoding="utf-8")

    # 最低限のセクション構造チェック
    has_active = bool(re.search(r"##\s+Active\s+Pitfalls", content, re.IGNORECASE))
    has_candidate = bool(re.search(r"##\s+Candidate\s+Pitfalls", content, re.IGNORECASE))

    if not has_active or not has_candidate:
        # 破損 → バックアップして再作成
        backup = pitfalls_path.with_suffix(".md.bak")
        shutil.copy2(pitfalls_path, backup)
        _write_empty_template(pitfalls_path)
        print(
            f"  [warn] pitfalls.md が破損していたため再作成しました。バックアップ: {backup}",
            file=sys.stderr,
        )
        return pitfalls_path.read_text(encoding="utf-8")

    return content


def _write_empty_template(pitfalls_path: Path) -> None:
    """空テンプレートで pitfalls.md を初期化する。"""
    template = (
        "# Pitfalls\n\n"
        "## Active Pitfalls\n\n"
        "_まだ記録がありません。_\n\n"
        "## Candidate Pitfalls\n\n"
        "_まだ記録がありません。_\n\n"
        "## Graduated Pitfalls\n\n"
        "_まだ記録がありません。_\n"
    )
    pitfalls_path.write_text(template, encoding="utf-8")
