#!/usr/bin/env bash
# Independent review of a task branch by a local Ollama model.
# Usage: REVIEW_MODEL=<ollama model> factory/review.sh <slug> [base=main]
# The reviewer must be a different model family from the writer (Gemini). Gemma is Google's,
# so it counts as the same family and is refused, as are Gemini and Claude names.
# OLLAMA_HOST works as usual if the model runs on another machine (e.g. your Mac over Tailscale).
set -euo pipefail

slug="${1:?usage: factory/review.sh <slug> [base-branch]}"
base="${2:-main}"
model="${REVIEW_MODEL:?set REVIEW_MODEL to an Ollama model you have pulled}"
max_chars="${REVIEW_MAX_CHARS:-40000}"

shopt -s nocasematch
if [[ "$model" =~ (gemma|gemini|claude) ]]; then
  echo "REFUSED: '$model' is the same family as the writer (Gemini) or the QA model (Claude)." >&2
  exit 3
fi
shopt -u nocasematch

fdir="${FACTORY_DIR:-$HOME/.factory/arda}"
wt="$fdir/wt/$slug"
[ -d "$wt" ] || { echo "no worktree at $wt" >&2; exit 2; }

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
diff="$(git -C "$wt" diff "origin/$base...HEAD")"
[ -n "$diff" ] || { echo "empty diff: nothing to review" >&2; exit 2; }
if [ "${#diff}" -gt "$max_chars" ]; then
  echo "REFUSED: diff is ${#diff} chars (limit $max_chars). A reviewer that sees half the change proves nothing. Split the task." >&2
  exit 4
fi

command -v ollama >/dev/null || { echo "ollama is not on PATH" >&2; exit 2; }

out="$fdir/REVIEW-$slug.md"
{
  cat "$repo_root/factory/prompts/reviewer.md"
  echo; echo "## STATUS"; cat "$fdir/STATUS-$slug.md" 2>/dev/null || true
  echo; echo "## DIFF (origin/$base...HEAD)"; echo '```diff'; echo "$diff"; echo '```'
} | ollama run "$model" > "$out"

# A reviewer that returns nothing, or prose with no verdict, must not read as an approval.
if ! /usr/bin/grep -qE '^NO FINDINGS$|BLOCKER|MAJOR|MINOR' "$out"; then
  echo "REFUSED: $out has no verdict (neither NO FINDINGS nor a BLOCKER/MAJOR/MINOR finding). Treat the review as not done." >&2
  exit 5
fi

echo "review written to $out"
tail -n 5 "$out"
