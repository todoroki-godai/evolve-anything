#!/usr/bin/env python3
"""UserPromptSubmit hook: 直近メッセージの context window 占有率を監視する。

最新 message の input_tokens + cache_read + cache_creation を model window で割った
占有率を計算し、閾値超過で compaction を促す（context rot 防止）。
API 課金累計（rate limit / コスト軸）は Claude Code 公式の /usage と statusline で
カバーされているため、本 hook では扱わない。

設計原則:
- 末尾から逆走して最初の usage エントリだけ読む（差分キャッシュは不要）
- 再警告クールダウン 5分（last_warned_at で管理）
- session_id 未取得 / 警告閾値 0 / window 0 は silent exit
- ファイル読み書き失敗は silent fallback
"""
import json
import os
import sys
import time
from pathlib import Path

_DEFAULT_WARN_PERCENT = 20
_DEFAULT_WINDOW_TOKENS = 1_000_000
_COOLDOWN_SECONDS = 300


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    try:
        val = int(raw)
        return val if val >= 0 else default
    except ValueError:
        return default


def get_warn_percent() -> int:
    return _int_env("CLAUDE_PLUGIN_OPTION_ctx_warn_percent", _DEFAULT_WARN_PERCENT)


def get_window_tokens() -> int:
    val = _int_env("CLAUDE_PLUGIN_OPTION_ctx_window_tokens", _DEFAULT_WINDOW_TOKENS)
    return val if val > 0 else _DEFAULT_WINDOW_TOKENS


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
    return {"last_warned_at": 0.0}


def _save_cache(cache_file: Path, data: dict) -> None:
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
    except Exception:
        pass


def _latest_usage(session_file: Path) -> dict | None:
    """末尾から逆走して最新の usage を持つエントリを返す。"""
    try:
        lines = session_file.read_bytes().splitlines()
    except Exception:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line.decode("utf-8", errors="ignore"))
            usage = entry.get("message", {}).get("usage")
            if usage:
                return usage
        except Exception:
            continue
    return None


def _ctx_size(usage: dict) -> int:
    return (
        usage.get("input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
    )


def check_ctx_usage(
    session_file: Path,
    cache_file: Path,
    warn_percent: int,
    window_tokens: int,
) -> bool:
    """直近 usage の ctx 占有率をチェックして警告を出す。"""
    if warn_percent <= 0 or window_tokens <= 0:
        return False

    usage = _latest_usage(session_file)
    if not usage:
        return False

    ctx = _ctx_size(usage)
    if ctx <= 0:
        return False

    pct = ctx * 100 / window_tokens
    if pct < warn_percent:
        return False

    cache = _load_cache(cache_file)
    now = time.time()
    if now - cache.get("last_warned_at", 0.0) < _COOLDOWN_SECONDS:
        return False

    print(
        f"[rl-anything:ctx_guard] ⚠ context 占有率 {pct:.1f}% "
        f"({ctx:,} / {window_tokens:,} tokens, 閾値 {warn_percent}%)\n"
        f"  compaction が走る前にできること:\n"
        f"  - /compact で手動圧縮\n"
        f"  - /handover で次セッションに引き継ぎ\n"
        f"  - 大きな Read/Bash 出力を避け、Grep + offset/limit に切り替え",
        flush=True,
    )

    cache["last_warned_at"] = now
    _save_cache(cache_file, cache)
    return True


def run(event: dict) -> None:
    session_id = event.get("session_id", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not session_id:
        return
    session_file = _session_jsonl_path(session_id, project_dir)
    if session_file is None or not session_file.exists():
        return
    cache_file = Path(f"/tmp/rl-ctx-guard-{session_id}.json")
    check_ctx_usage(
        session_file=session_file,
        cache_file=cache_file,
        warn_percent=get_warn_percent(),
        window_tokens=get_window_tokens(),
    )


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except Exception:
        return
    run(event)


if __name__ == "__main__":
    main()
