#!/usr/bin/env python3
"""InstructionsLoaded hook — CLAUDE.md/rules ロードを sessions.jsonl に記録する。

セッション内で最初の 1 回のみ記録（flag file で dedup）。
stale flag（STALE_FLAG_TTL_HOURS 超過）は自動削除。
LLM 呼び出しは行わない（MUST NOT）。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import common

# memory_temporal は scripts/lib にある（sys.path 経由でアクセス）
_memory_temporal = None
try:
    _scripts_lib = str(Path(__file__).resolve().parent.parent / "scripts" / "lib")
    if _scripts_lib not in sys.path:
        sys.path.insert(0, _scripts_lib)
    from memory_temporal import parse_memory_temporal, is_stale, is_superseded
    _memory_temporal = True
except ImportError:
    pass


def _flag_path(session_id: str) -> Path:
    """dedup フラグファイルのパスを返す。"""
    tmp = common.DATA_DIR / "tmp"
    return tmp / f"{common.INSTRUCTIONS_LOADED_FLAG_PREFIX}{session_id}"


def _cleanup_stale_flags() -> None:
    """STALE_FLAG_TTL_HOURS 超過のフラグファイルを削除する。"""
    tmp = common.DATA_DIR / "tmp"
    if not tmp.exists():
        return
    ttl_seconds = common.STALE_FLAG_TTL_HOURS * 3600
    now = time.time()
    for f in tmp.glob(f"{common.INSTRUCTIONS_LOADED_FLAG_PREFIX}*"):
        try:
            if now - f.stat().st_mtime > ttl_seconds:
                f.unlink()
        except OSError:
            pass


def handle_instructions_loaded(event: dict) -> None:
    """InstructionsLoaded イベントを処理する。"""
    common.ensure_data_dir()
    session_id = event.get("session_id", "")
    if not session_id:
        return

    # stale flag cleanup
    _cleanup_stale_flags()

    # dedup: セッション内で 1 回のみ
    flag = _flag_path(session_id)
    if flag.exists():
        return

    # フラグディレクトリ作成 & フラグ書き込み
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(session_id, encoding="utf-8")

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    project = common.project_name_from_dir(project_dir) if project_dir else None

    record = {
        "type": "instructions_loaded",
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project,
    }
    common.append_jsonl(common.DATA_DIR / "sessions.jsonl", record)

    # ── stale memory 警告（ソフト指示方式） ──────────────────────
    if project_dir:
        proj_encoded = project_dir.replace("/", "-")
        for candidate in [proj_encoded, proj_encoded.lstrip("-")]:
            memory_dir = Path.home() / ".claude" / "projects" / candidate / "memory"
            if memory_dir.is_dir():
                _emit_stale_memory_warnings(memory_dir)
                break

    # ── NFD: Growth greeting 出力 ──────────────────────────────
    _emit_growth_greeting(project)


def _emit_stale_memory_warnings(memory_dir: Path) -> None:
    """superseded / stale な memory ファイルを stdout に出力する。

    Claude が受け取り「このエントリは無視してください」と判断するソフト指示方式。
    LLM 呼び出しなし。例外はサイレントに無視（hook の安定性優先）。

    # TODO(APEX-MEM-C): この静的フィルタは将来 Event-Centric Rewrite（Approach C）で
    # ReAct 型クエリ時解決エージェントに置き換える。参照: issue #13
    """
    if not _memory_temporal:
        return
    if not memory_dir.is_dir():
        return

    try:
        for md_file in sorted(memory_dir.glob("*.md")):
            try:
                temporal = parse_memory_temporal(md_file)
                if is_superseded(temporal) or is_stale(temporal):
                    print(f"STALE MEMORY: {md_file.name}")
            except Exception:
                pass  # ファイル単位のエラーはスキップ
    except Exception:
        pass  # memory_dir スキャン失敗はサイレント


def _emit_growth_greeting(project: str | None) -> None:
    """growth-state キャッシュを読み、成長データを stdout に出力。

    Claude が受け取ってユーザーに自然な形で表示する。
    LLM 呼び出しなし（ファイル読み取りのみ）。
    """
    if not project:
        return

    # userConfig チェック
    config = common.load_user_config()
    if not config.get("growth_display", True):
        return

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "lib"))
        from growth_engine import read_cache, PHASE_DISPLAY_NAMES, Phase

        cache = read_cache(project)
        if cache is None:
            return

        phase_str = cache.get("phase", "bootstrap")
        progress = cache.get("progress", 0.0)
        stale = cache.get("stale", False)
        progress_pct = int(progress * 100)

        # stdout にデータ出力（Claude が受け取る）
        # level がキャッシュにある場合はレベル表示、なければ旧フォーマット
        level = cache.get("level")
        title_en = cache.get("title_en", "")
        if level is not None:
            parts = [f"GROWTH: Lv.{level} {title_en} {phase_str} {progress_pct}%"]
        else:
            parts = [f"GROWTH: {phase_str} {progress_pct}%"]
        if stale:
            stale_days = cache.get("stale_days", 0)
            parts.append(f"(stale: {stale_days}d ago)")

        print(" ".join(parts))
    except Exception:
        pass  # greeting 失敗はサイレント（hook の安定性優先）


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return
        event = json.loads(raw)
        handle_instructions_loaded(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:instructions_loaded] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:instructions_loaded] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
