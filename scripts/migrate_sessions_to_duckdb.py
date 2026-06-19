#!/usr/bin/env python3
"""sessions.jsonl → sessions.db マイグレーション。

session_store.migrate_from_jsonl() を呼ぶ薄い CLI ラッパー。
べき等。既存データは尊重する。

使用例:
  python3 scripts/migrate_sessions_to_duckdb.py
  python3 scripts/migrate_sessions_to_duckdb.py --skip-if-db-has-data
  python3 scripts/migrate_sessions_to_duckdb.py --data-dir /path/to/data
"""
import argparse
import os
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_LIB))


def main() -> None:
    parser = argparse.ArgumentParser(description="sessions.jsonl を sessions.db (DuckDB) に取り込む")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("CLAUDE_PLUGIN_DATA"),
        help="データディレクトリ（デフォルト: $CLAUDE_PLUGIN_DATA or ~/.claude/evolve-anything）",
    )
    parser.add_argument(
        "--skip-if-db-has-data",
        action="store_true",
        help="DB に既存データがある場合スキップ（自動マイグレーション用）",
    )
    args = parser.parse_args()

    if args.data_dir:
        os.environ["CLAUDE_PLUGIN_DATA"] = args.data_dir

    # DATA_DIR を反映するため後 import
    import session_store

    if not session_store.HAS_DUCKDB:
        print("ERROR: duckdb がインストールされていません。`pip install duckdb`", file=sys.stderr)
        sys.exit(1)

    print(f"DATA_DIR: {session_store.DATA_DIR}")
    print(f"sessions.jsonl: {session_store.SESSIONS_JSONL} (exists={session_store.SESSIONS_JSONL.exists()})")
    print(f"sessions.db:    {session_store.SESSIONS_DB} (exists={session_store.SESSIONS_DB.exists()})")

    if not session_store.SESSIONS_JSONL.exists():
        print("sessions.jsonl が存在しません。マイグレーション不要。")
        return

    inserted = session_store.migrate_from_jsonl(skip_if_db_has_data=args.skip_if_db_has_data)
    print(f"取り込み完了: {inserted} 件挿入")

    total = session_store.count_unique_since("0000-00-00")
    print(f"DB 内ユニークセッション数: {total}")


if __name__ == "__main__":
    main()
