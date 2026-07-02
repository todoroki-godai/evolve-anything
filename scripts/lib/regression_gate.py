"""共通 regression gate ライブラリ。

optimize.py / evolve-loop で共有するゲートチェックを一元管理する。
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# similarity モジュール（Jaccard 係数）
try:
    from similarity import jaccard_coefficient, tokenize
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from similarity import jaccard_coefficient, tokenize

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


@dataclass
class IntentionCheckResult:
    """intention_check() の結果。"""

    severity: str  # "block" | "warn" | "ok"
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


# Trigger 行を検出する正規表現
_TRIGGER_LINE_RE = re.compile(r"^\s*Trigger\s*:", re.MULTILINE | re.IGNORECASE)

# Usage 大見出しを検出する正規表現
_USAGE_HEADER_RE = re.compile(r"^#{1,3}\s+Usage\b", re.MULTILINE)

# Jaccard 類似度の warn 閾値
_JACCARD_WARN_THRESHOLD = 0.5

# Trigger 行削除率の block 閾値
_TRIGGER_DELETE_RATE_THRESHOLD = 0.3


def intention_check(candidate: str, original: str) -> IntentionCheckResult:
    """evolve 実行前の意図確認。候補テキストが元スキルの意図を逸脱していないか検証。

    severity 優先順位: block > warn > ok。最初の block 検出で即返す。

    BLOCK 検出ロジック:
    1. Trigger 行削除率 ≥ 30%
    2. description キー消失
    3. disable-model-invocation: true → false への変化
    4. Usage/実行セクション完全消失

    WARN 検出ロジック:
    1. effort 昇降 (low ↔ high)
    2. Jaccard 係数 < 0.5

    Args:
        candidate: パッチ候補テキスト
        original: 元のファイル内容

    Returns:
        IntentionCheckResult(severity, reason, details)
    """
    # ── BLOCK チェック ────────────────────────────────────────────────

    # 1. Trigger 行削除率チェック
    original_trigger_count = len(_TRIGGER_LINE_RE.findall(original))
    if original_trigger_count > 0:
        candidate_trigger_count = len(_TRIGGER_LINE_RE.findall(candidate))
        deleted = original_trigger_count - candidate_trigger_count
        delete_rate = deleted / original_trigger_count
        if delete_rate >= _TRIGGER_DELETE_RATE_THRESHOLD:
            return IntentionCheckResult(
                severity="block",
                reason=f"trigger_deletion_rate({delete_rate:.0%})",
                details={
                    "original_trigger_count": original_trigger_count,
                    "candidate_trigger_count": candidate_trigger_count,
                    "delete_rate": delete_rate,
                },
            )

    # 2. description キー消失チェック
    if re.search(r"^description\s*:", original, re.MULTILINE | re.IGNORECASE):
        if not re.search(r"^description\s*:", candidate, re.MULTILINE | re.IGNORECASE):
            return IntentionCheckResult(
                severity="block",
                reason="description_key_lost",
                details={},
            )

    # 3. disable-model-invocation: true → false チェック
    original_dmi = re.search(
        r"^disable-model-invocation\s*:\s*(true|false)", original, re.MULTILINE | re.IGNORECASE
    )
    candidate_dmi = re.search(
        r"^disable-model-invocation\s*:\s*(true|false)", candidate, re.MULTILINE | re.IGNORECASE
    )
    if original_dmi and original_dmi.group(1).lower() == "true":
        if not candidate_dmi or candidate_dmi.group(1).lower() != "true":
            return IntentionCheckResult(
                severity="block",
                reason="disable-model-invocation が削除または false に変更されました",
                details={
                    "original_dmi": original_dmi.group(0),
                    "candidate_dmi": str(candidate_dmi.group(0)) if candidate_dmi else "None",
                },
            )

    # 4. Usage/実行セクション完全消失チェック
    if _USAGE_HEADER_RE.search(original) and not _USAGE_HEADER_RE.search(candidate):
        return IntentionCheckResult(
            severity="block",
            reason="usage_section_lost",
            details={},
        )

    # ── WARN チェック ────────────────────────────────────────────────

    # 1. effort 昇降チェック
    original_effort = re.search(r"^effort\s*:\s*(\w+)", original, re.MULTILINE | re.IGNORECASE)
    candidate_effort = re.search(r"^effort\s*:\s*(\w+)", candidate, re.MULTILINE | re.IGNORECASE)
    if original_effort and candidate_effort:
        orig_val = original_effort.group(1).lower()
        cand_val = candidate_effort.group(1).lower()
        if orig_val != cand_val and {orig_val, cand_val} == {"low", "high"}:
            return IntentionCheckResult(
                severity="warn",
                reason=f"effort_changed({orig_val}→{cand_val})",
                details={"original_effort": orig_val, "candidate_effort": cand_val},
            )

    # 2. Jaccard 係数チェック
    original_tokens = tokenize(original)
    candidate_tokens = tokenize(candidate)
    jaccard = jaccard_coefficient(original_tokens, candidate_tokens)
    if jaccard < _JACCARD_WARN_THRESHOLD:
        return IntentionCheckResult(
            severity="warn",
            reason=f"jaccard_low({jaccard:.2f})",
            details={"jaccard": jaccard},
        )

    return IntentionCheckResult(severity="ok", reason="ok", details={})


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
