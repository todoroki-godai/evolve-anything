#!/usr/bin/env python3
"""UserPromptSubmit hook: セッション累積トークン消費を監視して警告を差し込む (issue #34)。

設計原則:
- 現在セッションの .jsonl 1ファイルだけ読む（全 PJ スキャン禁止）
- byte-offset キャッシュで差分読み → < 50ms で完了
- DuckDB 書き込みなし（読み取り専用）
- session_id が取得できない場合は silent exit
- /tmp 書き込み失敗時は silent fallback（末尾 500 行のみ全読み）
- 再警告インターバル: 5分（last_warned_at で管理）
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_THRESHOLD = 50000
_COOLDOWN_SECONDS = 300  # 5分
_FALLBACK_TAIL_LINES = 500


def get_threshold() -> int:
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_token_warn_threshold", "")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return _DEFAULT_THRESHOLD


def _session_jsonl_path(session_id: str, project_dir: str) -> Path | None:
    if not session_id or not project_dir:
        return None
    slug = project_dir.replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def _load_cache(cache_file: Path) -> dict:
    try:
        if cache_file.exists():
            return json.loads(cache_file.read_text())
    except Exception:
        pass
    return {"total": 0, "byte_offset": 0, "last_warned_at": 0.0, "start_ts": None}


def _save_cache(cache_file: Path, data: dict) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
    except Exception:
        pass  # /tmp 書き込み失敗は silent fallback


def _parse_ts(ts_str: str | None) -> float | None:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


def _read_usage_from_file(session_file: Path, byte_offset: int) -> tuple[int, int, float | None]:
    """session_file を byte_offset から読んでトークン合計と新 offset を返す。
    戻り値: (追加トークン数, 新 byte_offset, 最初のエントリの timestamp or None)
    """
    added = 0
    first_ts = None
    new_offset = byte_offset
    try:
        with session_file.open("rb") as f:
            f.seek(byte_offset)
            for line in f:
                new_offset = f.tell()
                try:
                    entry = json.loads(line.decode("utf-8", errors="ignore"))
                    usage = entry.get("message", {}).get("usage")
                    if usage:
                        added += usage.get("input_tokens", 0)
                        added += usage.get("output_tokens", 0)
                        added += usage.get("cache_read_input_tokens", 0)
                        if first_ts is None:
                            first_ts = _parse_ts(entry.get("timestamp"))
                except Exception:
                    continue
    except Exception:
        pass
    return added, new_offset, first_ts


def _read_tail_usage(session_file: Path) -> tuple[int, float | None]:
    """キャッシュなしの fallback: 末尾 N 行だけ読む。"""
    added = 0
    first_ts = None
    try:
        lines = session_file.read_bytes().splitlines()
        for line in lines[-_FALLBACK_TAIL_LINES:]:
            try:
                entry = json.loads(line.decode("utf-8", errors="ignore"))
                usage = entry.get("message", {}).get("usage")
                if usage:
                    added += usage.get("input_tokens", 0)
                    added += usage.get("output_tokens", 0)
                    added += usage.get("cache_read_input_tokens", 0)
                    if first_ts is None:
                        first_ts = _parse_ts(entry.get("timestamp"))
            except Exception:
                continue
    except Exception:
        pass
    return added, first_ts


def _calc_rate(total: int, start_ts: float | None) -> str:
    if not start_ts:
        return "不明"
    elapsed_min = (time.time() - start_ts) / 60
    if elapsed_min < 0.1:
        return "不明"
    rate = int(total / elapsed_min)
    return f"{rate:,}"


def check_token_usage(
    session_file: Path,
    cache_file: Path,
    threshold: int,
) -> bool:
    """トークン消費をチェックして警告を出力する。

    Returns:
        True if warning was emitted, False otherwise.
    """
    cache = _load_cache(cache_file)
    byte_offset = cache.get("byte_offset", 0)
    total = cache.get("total", 0)
    last_warned_at = cache.get("last_warned_at", 0.0)
    start_ts = cache.get("start_ts")

    cache_writable = True
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # 書き込み可能性テスト
        if not cache_file.parent.exists():
            raise OSError
    except Exception:
        cache_writable = False

    if cache_writable:
        added, new_offset, entry_ts = _read_usage_from_file(session_file, byte_offset)
        total += added
        if start_ts is None and entry_ts:
            start_ts = entry_ts
        cache["total"] = total
        cache["byte_offset"] = new_offset
        cache["start_ts"] = start_ts
    else:
        # /tmp 書き込み不可: 末尾読み fallback
        tail_total, entry_ts = _read_tail_usage(session_file)
        total = tail_total
        if start_ts is None and entry_ts:
            start_ts = entry_ts

    if total <= threshold:
        _save_cache(cache_file, cache)
        return False

    now = time.time()
    if now - last_warned_at < _COOLDOWN_SECONDS:
        _save_cache(cache_file, cache)
        return False

    # 警告出力
    rate_str = _calc_rate(total, start_ts)
    print(
        f"[rl-anything:token_guard] ⚠ 累積 {total:,} tokens（閾値 {threshold:,} 超過）\n"
        f"  ペース: 約 {rate_str} tokens/分。このペースで継続すると rate limit に近づく可能性があります。\n"
        f"  代替案を検討してください:\n"
        f"  - バッチを 10件/回 に分割して都度確認を挟む\n"
        f"  - 高信頼度モデルへの自動エスカレーションを無効にし手動判断に切り替える\n"
        f"  - ローカル処理（OCR 等）で LLM 呼び出しを削減する\n"
        f"  続行する場合はそのまま指示してください。",
        flush=True,
    )

    cache["last_warned_at"] = now
    _save_cache(cache_file, cache)
    return True


def run(event: dict) -> None:
    """hook エントリポイント。session_id が取得できない場合は silent exit。"""
    session_id = event.get("session_id", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    if not session_id:
        return

    session_file = _session_jsonl_path(session_id, project_dir)
    if session_file is None or not session_file.exists():
        return

    cache_file = Path(f"/tmp/rl-token-guard-{session_id}.json")
    threshold = get_threshold()

    check_token_usage(session_file=session_file, cache_file=cache_file, threshold=threshold)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    run(event)


if __name__ == "__main__":
    main()
