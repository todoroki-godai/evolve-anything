#!/usr/bin/env python3
"""Stop hook — 最終アシスタント応答を冗長性候補として記録する（ゼロ LLM・非ブロッキング・#75）。

設計（standalone ~/.claude/hooks/record-verbosity.py の移植 + PJ スコープ化）:
- 判定はしない。長さは「足切りゲート」にのみ使う（小さい応答は記録しない）。
- 冗長かどうかの判断は後段の scripts/lib/verbosity/judge.py が Haiku でまとめて行う。
- **何があっても exit 0**。ユーザーのセッションを絶対に止めない（非ブロッキング）。

standalone との差分:
- 出力先を standalone（~/.claude/verbosity/ 配下）ではなく **store_write barrier**（ADR-049）
  経由で verbosity_candidates.jsonl へ（canonical DATA_DIR/<name> を内部解決）。
- record に **pj_slug** を付与（cwd から pj_slug_fast・read 側照合の強制）。
"""
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

# hooks/ → plugin_root/ → scripts/lib/ を sys.path（既存 hook の解決を踏襲）。
_LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# 足切りゲート（判定ではない）。これ未満の応答はそもそも記録しない。env で調整可。
GATE_CHARS = int(os.environ.get("VERBOSITY_GATE_CHARS", "800"))

STORE_NAME = "verbosity_candidates.jsonl"


def _last_assistant_text(transcript_path: str) -> str:
    """transcript JSONL の末尾から最後の assistant 応答の text を連結して返す。"""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if ev.get("type") != "assistant":
            continue
        msg = ev.get("message") or {}
        content = msg.get("content") or []
        parts = []
        if isinstance(content, str):
            parts.append(content)
        else:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
        text = "\n".join(p for p in parts if p).strip()
        if text:
            return text
        # text 無し（tool_use のみ）の assistant イベントは飛ばして更に前を見る。
    return ""


def _resolve_slug(cwd: str):
    """cwd から worktree 安全 slug を導出する（hot path: subprocess なしの pj_slug_fast）。"""
    try:
        from pj_slug import pj_slug_fast

        try:
            from rl_common import DATA_DIR
            return pj_slug_fast(cwd, data_dir=DATA_DIR) or Path(cwd).name or None
        except ImportError:
            return pj_slug_fast(cwd) or Path(cwd).name or None
    except ImportError:
        return Path(cwd).name or None


def build_record(data: dict):
    """Stop イベント payload から冗長性候補レコードを組み立てる（足切り未満は None）。"""
    transcript_path = data.get("transcript_path") or ""
    session_id = data.get("session_id") or ""
    cwd = data.get("cwd") or os.getcwd()

    text = _last_assistant_text(transcript_path)
    if not text:
        return None

    char_len = len(text)
    if char_len < GATE_CHARS:  # 足切り: 小さい応答は学習対象にしない。
        return None

    line_count = text.count("\n") + 1
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    project = os.path.basename(cwd.rstrip("/")) or cwd

    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session_id,
        "pj_slug": _resolve_slug(cwd),
        "project": project,
        "cwd": cwd,
        "char_len": char_len,
        "line_count": line_count,
        "hash": digest,
        "text": text,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, ValueError):
        return 0

    rec = build_record(data)
    if rec is None:
        return 0

    try:
        from rl_common.store_write import store_write

        store_write(STORE_NAME, rec)
    except Exception:  # noqa: BLE001 - 記録失敗でセッションを止めない（非ブロッキング）。
        return 0
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - 何があっても exit 0。
        sys.exit(0)
