"""共通 regression gate ライブラリ。

optimize.py / evolve-loop で共有するゲートチェックを一元管理する。
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

FORBIDDEN_PATTERNS = ["TODO", "FIXME", "HACK", "XXX"]


@dataclass
class GateResult:
    """ゲートチェックの結果。スコアリングは呼び出し側の責務。"""

    passed: bool
    reason: Optional[str]


@dataclass
class PreCheckResult:
    """pre_check() の結果。warn-only なので passed は常に True。"""

    passed: bool
    warnings: List[str] = field(default_factory=list)


def check_gates(
    candidate: str,
    original: Optional[str] = None,
    *,
    max_lines: int,
    max_chars: Optional[int] = None,
    pitfall_patterns_path: Optional[str] = None,
) -> GateResult:
    """全ゲートチェックを実行し結果を返す。

    Args:
        candidate: パッチ候補テキスト
        original: 元のファイル内容（frontmatter 保持チェック用）
        max_lines: 行数上限（必須）。呼び出し側が line_limit.py の定数を参照して渡す
        max_chars: 文字数上限（任意・#120 GEPA ガードレール）。None なら char ゲートを
            適用しない（後方互換）。呼び出し側が ``line_limit.max_chars_for(max_lines)``
            を渡す。行数ゲートを通っても 1 行が異常に長い bloat を捕捉する。
        pitfall_patterns_path: pitfalls.md のパス。None ならスキップ
    """
    # 空コンテンツチェック
    if not candidate or not candidate.strip():
        return GateResult(passed=False, reason="empty_content")

    # 行数制限チェック
    lines = candidate.count("\n") + 1
    if lines > max_lines:
        return GateResult(
            passed=False, reason=f"line_limit_exceeded({lines}/{max_lines})"
        )

    # 文字数制限チェック（#120 GEPA: 行内 bloat を捕捉。max_chars=None ならスキップ）
    if max_chars is not None and len(candidate) > max_chars:
        return GateResult(
            passed=False, reason=f"char_limit_exceeded({len(candidate)}/{max_chars})"
        )

    # 禁止パターンチェック
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in candidate:
            return GateResult(passed=False, reason=f"forbidden_pattern({pattern})")

    # pitfall パターンチェック
    if pitfall_patterns_path is not None:
        for pp in _load_pitfall_patterns(pitfall_patterns_path):
            if pp in candidate:
                return GateResult(passed=False, reason=f"pitfall_pattern({pp})")

    # frontmatter 保持チェック
    if original and original.startswith("---"):
        if not candidate.startswith("---"):
            return GateResult(passed=False, reason="frontmatter_lost")

    return GateResult(passed=True, reason=None)


def pre_check(candidate: str, original: str) -> PreCheckResult:
    """候補テキストに対して warn-only のリスク評価を行う。

    passed は常に True（ブロックしない）。検出した問題は warnings に追加する。

    検出条件:
    1. API シグネチャ消失: original の def 関数名が candidate にない
    2. 行数 2x 超: candidate の行数が original の 2 倍を超える
    3. frontmatter 削除: original が "---" で始まるのに candidate が始まらない

    Args:
        candidate: パッチ候補テキスト
        original: 元のファイル内容

    Returns:
        PreCheckResult(passed=True, warnings=[...])
    """
    warnings: List[str] = []

    # 1. API シグネチャ消失チェック
    for line in original.splitlines():
        stripped = line.strip()
        if stripped.startswith("def "):
            m = re.match(r"def\s+(\w+)\s*\(", stripped)
            if m:
                func_name = m.group(1)
                if func_name not in candidate:
                    warnings.append(f"API signature lost: {func_name}")

    # 2. 行数 2x 超チェック
    candidate_lines = len(candidate.splitlines())
    original_lines = len(original.splitlines())
    if candidate_lines > original_lines * 2:
        warnings.append(
            f"Line count explosion: {candidate_lines} > {original_lines} * 2"
        )

    # 3. frontmatter 削除チェック
    if original.startswith("---") and not candidate.startswith("---"):
        warnings.append("Frontmatter deleted")

    return PreCheckResult(passed=True, warnings=warnings)


def _load_pitfall_patterns(pitfall_patterns_path: str) -> List[str]:
    """pitfalls.md からゲート不合格パターンを読み込む。"""
    path = Path(pitfall_patterns_path)
    if not path.exists():
        return []

    patterns = []
    content = path.read_text(encoding="utf-8")
    for line in content.strip().split("\n"):
        if not line.strip().startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4 and parts[1] == "gate":
            m = re.match(r"forbidden_pattern\((.+)\)", parts[2])
            if m:
                patterns.append(m.group(1))
    return patterns
