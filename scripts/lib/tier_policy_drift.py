"""tier_policy の stale-mention advisory（#193）。

正典が使わなくなったモデルエイリアス（例: opus 4.8 廃止後の「opus」残存言及）が
``advisory_scan`` ディレクトリ配下の散文（rules 等）や agent targets ファイルに
残っていないかを決定論・単語境界・case-insensitive で検出する。**書換は一切しない**
（散文の言及は人間が判断する設計確定事項。sync target と違い「本文の自由記述」は
機械編集の対象にしない）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

# 既知のモデルエイリアス（"inherit" はモデル名ではないので対象外）。
_ALL_ALIASES = ("opus", "sonnet", "haiku", "fable")


def _used_models(tiers: Dict[str, Dict[str, Any]]) -> set:
    return {
        str(policy.get("model")).strip().lower()
        for policy in tiers.values()
        if policy.get("model")
    }


def _collect_files(config: Dict[str, Any]) -> List[Path]:
    scan_dirs = [Path(p).expanduser() for p in (config.get("advisory_scan") or [])]
    agent_paths = [
        Path(p).expanduser() for p in ((config.get("targets") or {}).get("agents") or [])
    ]

    files: List[Path] = []
    seen = set()
    for d in scan_dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.md")):
            if f.is_file() and f not in seen:
                seen.add(f)
                files.append(f)
    for f in agent_paths:
        if f.is_file() and f not in seen:
            seen.add(f)
            files.append(f)
    return files


def scan_stale_mentions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """正典のどの tier の model にも使われていないエイリアス語の残存箇所を列挙する。

    Returns:
        ``[{"path", "line_no", "alias", "line"}, ...]``。stale なエイリアスが無ければ
        空 list（走査自体をスキップする）。
    """
    tiers = config.get("tiers") or {}
    used = _used_models(tiers)
    stale_aliases = [a for a in _ALL_ALIASES if a not in used]
    if not stale_aliases:
        return []

    patterns = {
        alias: re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
        for alias in stale_aliases
    }

    findings: List[Dict[str, Any]] = []
    for f in _collect_files(config):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for alias, pattern in patterns.items():
                if pattern.search(line):
                    findings.append(
                        {"path": str(f), "line_no": line_no, "alias": alias, "line": line}
                    )
    return findings
