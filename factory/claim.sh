#!/usr/bin/env bash
# Claim or release a hot shared file so two agents do not edit it at once.
# Usage: factory/claim.sh claim   <path> <slug>
#        factory/claim.sh release <path> <slug>
#        factory/claim.sh list
set -euo pipefail

fdir="${FACTORY_DIR:-$HOME/.factory/arda}"
claims="$fdir/CLAIMS.md"
mkdir -p "$fdir"; touch "$claims"

case "${1:-}" in
  list) cat "$claims" ;;
  claim)
    path="${2:?path}"; slug="${3:?slug}"
    holder="$(awk -F' \\| ' -v p="$path" '$1==p {print $2}' "$claims" | head -1)"
    if [ -n "$holder" ] && [ "$holder" != "$slug" ]; then
      echo "REFUSED: $path is claimed by $holder" >&2; exit 1
    fi
    [ -n "$holder" ] || echo "$path | $slug | $(date -u +%Y-%m-%dT%H:%MZ)" >> "$claims"
    echo "claimed $path for $slug" ;;
  release)
    path="${2:?path}"; slug="${3:?slug}"
    holder="$(awk -F' \\| ' -v p="$path" '$1==p {print $2}' "$claims" | head -1)"
    if [ "$holder" != "$slug" ]; then
      echo "nothing to release: $path is not claimed by $slug" >&2; exit 1
    fi
    tmp="$(mktemp)"
    awk -F' \\| ' -v p="$path" -v s="$slug" '!($1==p && $2==s)' "$claims" > "$tmp"
    mv "$tmp" "$claims"
    echo "released $path for $slug" ;;
  *) echo "usage: factory/claim.sh claim|release <path> <slug> | list" >&2; exit 2 ;;
esac
