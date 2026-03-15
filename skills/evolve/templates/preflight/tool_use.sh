#!/bin/bash
# Preflight check: tool_use category
# TODO: {pitfall_title} の検証ロジックを実装

# Root-cause: {root_cause}
# このスクリプトはツール使用前の検証を行います

if [ -z "${TOOL_NAME:-}" ]; then
  echo "ERROR: TOOL_NAME が設定されていません"
  exit 1
fi

# TODO: ツール使用固有のチェックロジックを追加
# 例: ツールの存在確認、入力形式の検証等

echo "OK: ツール使用の事前チェック完了"
exit 0
