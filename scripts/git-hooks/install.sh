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

# managed pre-push hook が無い場合は、追跡済み local hook を呼ぶ最小wrapperを作る。
# 既存 hook は絶対に上書きしない。
_managed="$(git rev-parse --git-path hooks/pre-push)"
if [[ ! -e "${_managed}" ]]; then
  cat >"${_managed}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
_local="$(git rev-parse --git-path hooks/pre-push.local)"
[[ ! -x "${_local}" ]] || exec "${_local}" "$@"
EOF
  chmod +x "${_managed}"
  echo "installed: ${_managed}"
elif ! grep -q "pre-push.local" "${_managed}" 2>/dev/null; then
  echo "warn: ${_managed} が pre-push.local を chain しません。" >&2
  echo "      既存 hook は上書きしていません。pre-push.local を手動で chain してください。" >&2
fi
