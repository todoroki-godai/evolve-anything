#!/usr/bin/env python3
"""過去セッションから preceding_tool_calls を抽出し failure_patterns を分析する。

Usage:
    python3 scripts/backfill_preceding_tool_calls.py [--days N] [--max-files N] [--persist]

Options:
    --days N       対象セッションの日数 (default: 7)
    --max-files N  最大処理ファイル数 (default: 200)
    --persist      corrections.jsonl に書き込む (default: 分析のみ)
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# プラグインルートを解決
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks"))

import common

# ~/.claude/projects/<slug>/ のパス
_PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-Users-todoroki-tools-evolve-anything"
_DATA_DIR = Path(os.environ.get("CLAUDE_PLUGIN_DATA", Path.home() / ".claude" / "evolve-anything"))

PRECEDING_N = 5  # 直近 N 件のツール呼び出しを取得


def extract_messages_from_session(session_file: Path) -> list[dict]:
    """セッションファイルから全メッセージを時系列順に返す。"""
    messages = []
    try:
        with open(session_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    messages.append(entry)
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError):
        pass
    return messages


def get_tool_calls_from_messages(messages: list[dict], up_to_idx: int) -> list[dict]:
    """messages[0..up_to_idx] の tool_use + tool_result から直近 N 件を返す。"""
    tool_calls = []
    for entry in messages[:up_to_idx]:
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue

        if role == "assistant":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append({
                        "tool": block.get("name", ""),
                        "tool_use_id": block.get("id", ""),
                        "success": True,  # 後で tool_result と照合
                    })
        elif role == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id", "")
                    is_error = block.get("is_error", False)
                    # 対応する tool_call の success を更新
                    for tc in reversed(tool_calls):
                        if tc.get("tool_use_id") == tool_use_id:
                            tc["success"] = not is_error
                            break

    # tool_use_id は内部IDなので除去して返す
    result = [{"tool": tc["tool"], "success": tc["success"]} for tc in tool_calls]
    return result[-PRECEDING_N:]


def get_user_text(entry: dict) -> str:
    """エントリからユーザーの発話テキストを抽出する。"""
    msg = entry.get("message", {})
    if not isinstance(msg, dict):
        return ""
    if msg.get("role") != "user":
        return ""
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text.strip():
                    return text
    return ""


def analyze_tool_call_patterns(corrections: list[dict]) -> dict:
    """preceding_tool_calls から failure_patterns と先行シーケンスを集計する。

    failure_patterns: ツール失敗（success=False）→次ツールのシーケンス（tool failure 起因）
    preceding_sequences: correction 前に頻出するツールシーケンス（success 問わず）
    """
    from collections import Counter

    tool_total: Counter = Counter()
    tool_fail: Counter = Counter()
    fail_seq_counter: Counter = Counter()   # 失敗→次ツール
    all_seq_counter: Counter = Counter()    # correction 直前の 2-gram（success 問わず）
    correction_type_counter: Counter = Counter()

    for c in corrections:
        calls = c.get("preceding_tool_calls", [])
        correction_type_counter[c.get("correction_type", "unknown")] += 1
        if not calls:
            continue
        for call in calls:
            tool = call.get("tool", "")
            tool_total[tool] += 1
            if not call.get("success", True):
                tool_fail[tool] += 1

        # 2-gram シーケンス（失敗→次のツール）
        for i in range(len(calls) - 1):
            if not calls[i].get("success", True):
                seq = f"{calls[i]['tool']}(fail) → {calls[i+1]['tool']}"
                fail_seq_counter[seq] += 1

        # 2-gram シーケンス（correction 直前の任意ペア）
        for i in range(len(calls) - 1):
            seq = f"{calls[i]['tool']} → {calls[i+1]['tool']}"
            all_seq_counter[seq] += 1

    failure_rate = {
        tool: round(tool_fail[tool] / total, 2)
        for tool, total in tool_total.items()
        if total > 0
    }

    return {
        "failure_patterns": [
            {"sequence": seq, "count": cnt}
            for seq, cnt in fail_seq_counter.most_common()
            if cnt >= 1
        ],
        "preceding_sequences": [
            {"sequence": seq, "count": cnt}
            for seq, cnt in all_seq_counter.most_common(10)
            if cnt >= 2
        ],
        "failure_rate_by_tool": failure_rate,
        "correction_type_breakdown": dict(correction_type_counter.most_common()),
        "total_corrections_with_tool_data": sum(1 for c in corrections if c.get("preceding_tool_calls")),
    }


def process_sessions(days: int, max_files: int) -> list[dict]:
    """セッションファイルをスキャンして correction + preceding_tool_calls を返す。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    session_files = sorted(
        _PROJECTS_DIR.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    results = []
    processed = 0
    skipped = 0

    for sf in session_files:
        if processed >= max_files:
            break
        try:
            mtime = datetime.fromtimestamp(sf.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            break  # 古すぎる（新しい順ソートなのでここで打ち切れる）

        session_id = sf.stem
        messages = extract_messages_from_session(sf)
        if not messages:
            skipped += 1
            continue

        found_in_session = 0
        for idx, entry in enumerate(messages):
            text = get_user_text(entry)
            if not text.strip():
                continue
            if not common.should_include_message(text):
                continue
            result = common.detect_correction(text)
            if result is None:
                continue

            correction_type, confidence = result
            preceding = get_tool_calls_from_messages(messages, idx)

            results.append({
                "correction_type": correction_type,
                "message": text[:200],
                "preceding_tool_calls": preceding,
                "confidence": confidence,
                "session_id": session_id,
                "source": "backfill",
                "reflect_status": "pending",
                "timestamp": entry.get("timestamp", ""),
            })
            found_in_session += 1

        processed += 1
        print(f"  [{processed}/{max_files}] {sf.name[:16]}… corrections={found_in_session}", flush=True)

    print(f"\n処理完了: {processed} files, {skipped} skipped, {len(results)} corrections found", flush=True)
    return results


def persist_to_corrections(corrections: list[dict]) -> int:
    """重複なしで corrections.jsonl に追記する。"""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_keys: set[str] = set()
    corrections_file = _DATA_DIR / "corrections.jsonl"

    if corrections_file.exists():
        with open(corrections_file, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    key = f"{d.get('session_id','')}-{d.get('timestamp','')}"
                    existing_keys.add(key)
                except json.JSONDecodeError:
                    pass

    new_count = 0
    with open(corrections_file, "a", encoding="utf-8") as f:
        for c in corrections:
            key = f"{c.get('session_id','')}-{c.get('timestamp','')}"
            if key not in existing_keys:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
                existing_keys.add(key)
                new_count += 1

    return new_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-files", type=int, default=200)
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args()

    print(f"セッションスキャン開始 (days={args.days}, max_files={args.max_files})", flush=True)
    t0 = time.time()

    corrections = process_sessions(args.days, args.max_files)

    if args.persist and corrections:
        added = persist_to_corrections(corrections)
        print(f"corrections.jsonl に {added} 件追記", flush=True)

    analysis = analyze_tool_call_patterns(corrections)
    elapsed = time.time() - t0

    print(f"\n=== failure_patterns (elapsed={elapsed:.1f}s) ===")
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
