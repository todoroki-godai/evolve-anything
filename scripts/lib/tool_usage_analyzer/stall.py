"""停滞→リカバリパターン検出モジュール (Phase 6 / Slice 1)。

セッション横断で Long→Investigation→Recovery→Long シーケンスを検出し、
pitfall candidate に変換するヘルパを提供する。
"""
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional


def _classify_stall_step(command: str) -> Optional[str]:
    """コマンドを long/investigation/recovery に分類する。"""
    # _get_command_head は Slice 2 で classify.py に移動予定。現状は __init__ に残る。
    from . import (
        INVESTIGATION_COMMANDS,
        LONG_COMMAND_PATTERNS,
        RECOVERY_COMMANDS,
        _get_command_head,
    )
    head = _get_command_head(command)
    if head in RECOVERY_COMMANDS:
        return "recovery"
    if head in INVESTIGATION_COMMANDS:
        return "investigation"
    for pattern in LONG_COMMAND_PATTERNS:
        if re.search(pattern, command):
            return "long"
    return None


def _detect_stall_in_session(commands: List[str]) -> Optional[Dict[str, Any]]:
    """単一セッション内で Long→Investigation→Recovery→Long パターンを検出する。

    Returns:
        検出パターン dict or None。
    """
    from . import _get_command_head, _get_command_key
    # 分類済みステップ列を作成
    classified = [(cmd, _classify_stall_step(cmd)) for cmd in commands]

    # Long→Investigation→Recovery→Long の部分シーケンスを探す
    i = 0
    n = len(classified)
    while i < n:
        cmd_i, cls_i = classified[i]
        if cls_i != "long":
            i += 1
            continue

        # Long found at i — look for Investigation after i
        j = i + 1
        has_investigation = False
        has_recovery = False
        recovery_actions = set()

        while j < n:
            _, cls_j = classified[j]
            if cls_j == "investigation":
                has_investigation = True
            elif cls_j == "recovery":
                if has_investigation:
                    has_recovery = True
                    recovery_actions.add(_get_command_head(classified[j][0]))
            elif cls_j == "long":
                if has_investigation and has_recovery:
                    # Long コマンドパターンの正規化
                    cmd_pattern = _get_command_key(cmd_i)
                    return {
                        "command_pattern": cmd_pattern,
                        "recovery_actions": sorted(recovery_actions),
                    }
                # この Long を新たな起点にする
                break
            j += 1

        i = j if j > i + 1 else i + 1

    return None


def detect_stall_recovery_patterns(
    session_commands: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    """セッション横断で停滞→リカバリパターンを検出する。

    Args:
        session_commands: {session_id: [command_strings]}

    Returns:
        検出パターンリスト。各パターンに command_pattern, session_count,
        recovery_actions, confidence を含む。
    """
    from . import STALL_RECOVERY_MIN_SESSIONS

    if not session_commands:
        return []

    # セッションごとに検出
    pattern_sessions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for sid, commands in session_commands.items():
        detected = _detect_stall_in_session(commands)
        if detected:
            pattern_sessions[detected["command_pattern"]].append({
                "session_id": sid,
                "recovery_actions": detected["recovery_actions"],
            })

    # 閾値フィルタ + confidence 算出
    results = []
    for cmd_pattern, sessions in pattern_sessions.items():
        session_count = len(sessions)
        if session_count < STALL_RECOVERY_MIN_SESSIONS:
            continue
        # recovery_actions を全セッションから集約
        all_actions = set()
        for s in sessions:
            all_actions.update(s["recovery_actions"])
        confidence = min(0.5 + session_count * 0.1, 0.95)
        results.append({
            "command_pattern": cmd_pattern,
            "session_count": session_count,
            "recovery_actions": sorted(all_actions),
            "confidence": confidence,
        })

    # confidence 降順でソート
    results.sort(key=lambda x: -x["confidence"])
    return results


def stall_pattern_to_pitfall_candidate(
    pattern: Dict[str, Any],
    existing_candidates: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """停滞パターンを pitfall candidate に変換する。

    既存候補と Jaccard 重複排除し、重複なら既存の Occurrence-count を更新して None を返す。
    """
    cmd = pattern.get("command_pattern", "")
    session_count = pattern.get("session_count", 0)
    root_cause = f"stall_recovery — {cmd}: {session_count} sessions"

    if existing_candidates:
        try:
            from pitfall_manager import find_matching_candidate
            match_idx = find_matching_candidate(existing_candidates, root_cause)
            if match_idx is not None:
                existing_candidates[match_idx]["fields"]["Occurrence-count"] = str(session_count)
                return None
        except ImportError:
            pass

    return {
        "root_cause": root_cause,
        "fields": {
            "Occurrence-count": str(session_count),
            "Status": "Candidate",
            "Source": "stall_recovery_detection",
        },
    }
