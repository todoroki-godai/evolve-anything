#!/usr/bin/env bash
# scripts/git-hooks/install.sh — 追跡フックソースを .git/hooks へ導入する。
#
# gstack-redact の managed pre-push hook が `pre-push.local` を chain するため、
# scripts/git-hooks/pre-push.local を .git/hooks/pre-push.local へコピーする。
# 共有 hooks（worktree 横断）なので 1 回の実行で全 worktree に効く。
set -euo pipefail

_src_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_dest="$(git rev-parse --git-path hooks/pre-push.local)"

cp "${_src_dir}/pre-push.local" "${_dest}"
chmod +x "${_dest}"
echo "installed: ${_dest}"

# managed pre-push hook（gstack-redact）が無い環境向けの注意喚起。
_managed="$(git rev-parse --git-path hooks/pre-push)"
if ! grep -q "pre-push.local" "${_managed}" 2>/dev/null; then
  echo "warn: ${_managed} が pre-push.local を chain しません。" >&2
  echo "      managed hook（gstack-redact）が無い場合は pre-push 本体から手動で呼び出してください。" >&2
fi
