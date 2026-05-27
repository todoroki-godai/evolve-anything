#!/usr/bin/env python3
"""MessageDisplay hook — アシスタント応答のテレメトリ記録 (CC v2.1.152+)。

メッセージ内容を変換せず passthrough しつつ、応答の長さ・コードブロック数・
pitfall キーワード検出数を message_display.jsonl に記録する。
将来の応答フィルタリング・アノテーション基盤として設計。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

# pitfall キーワードはプロセス起動時に1回だけロードしてキャッシュする
_PITFALL_KW_CACHE: list[str] | None = None

# ファイルサイズ上限: 1MB を超えたらローテーション
_MAX_LOG_BYTES = 1 * 1024 * 1024


def _count_code_blocks(text: str) -> int:
    return len(re.findall(r"```", text)) // 2


def _load_pitfall_keywords() -> list[str]:
    """プラグインルートの pitfalls.md からキーワードを収集する（キャッシュ）。"""
    global _PITFALL_KW_CACHE
    if _PITFALL_KW_CACHE is not None:
        return _PITFALL_KW_CACHE
    keywords: list[str] = []
    plugin_root = Path(__file__).resolve().parent.parent
    for p in plugin_root.glob("**/pitfalls.md"):
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if line.startswith("- ") and len(line) > 5:
                    kw = line[2:40].strip()
                    if kw:
                        keywords.append(kw[:30])
            if keywords:
                break
        except OSError:
            pass
    _PITFALL_KW_CACHE = keywords
    return keywords


def _detect_pitfall_keywords(text: str) -> list[str]:
    """キャッシュ済みキーワードから text 内の出現を返す。上位5件。"""
    all_kw = _load_pitfall_keywords()
    text_lower = text.lower()
    return [kw for kw in all_kw if kw.lower() in text_lower][:5]


def _rotate_if_needed(log_path: Path) -> None:
    """ログファイルが上限を超えたら .1 にローテーションする。"""
    try:
        if log_path.exists() and log_path.stat().st_size > _MAX_LOG_BYTES:
            log_path.replace(log_path.with_suffix(".jsonl.1"))
    except OSError:
        pass


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    message_text: str = event.get("message", "") or ""
    if not message_text:
        return

    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    data_dir = Path(plugin_data) if plugin_data else Path.home() / ".claude" / "rl-anything"
    data_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": event.get("session_id", ""),
        "char_count": len(message_text),
        "code_blocks": _count_code_blocks(message_text),
        "pitfall_hits": _detect_pitfall_keywords(message_text),
    }
    log_path = data_dir / "message_display.jsonl"
    _rotate_if_needed(log_path)
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass

    # passthrough — メッセージを変換しない
    # 将来: return {"content": modified_text} でアノテーション可能


if __name__ == "__main__":
    main()
