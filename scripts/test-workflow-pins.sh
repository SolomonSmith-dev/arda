#!/usr/bin/env bash
# Fail if any workflow still has a floating action tag.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
wf_dir="$root/.github/workflows"

if [[ ! -d "$wf_dir" ]]; then
  echo "missing $wf_dir"
  exit 1
fi

unpinned="$(grep -E 'uses:' "$wf_dir"/*.yml | grep -vE 'uses: [-A-Za-z0-9_./]+@[0-9a-f]{40}' || true)"
if [[ -n "$unpinned" ]]; then
  echo "unpinned actions:"
  printf '%s\n' "$unpinned"
  exit 1
fi

if ! grep -Eq 'scripts/test-workflow-pins\.sh' "$wf_dir"/ci.yml; then
  echo "ci.yml does not invoke test-workflow-pins.sh"
  exit 1
fi

echo "workflow pins ok"
