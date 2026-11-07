#!/usr/bin/env bash
# One line per STATUS-*.md: slug, branch, minutes since last activity, now running.
# Activity is the later of STATUS `started:` and the worktree HEAD commit.
# STALE when that instant is 30 minutes old or older, or when neither is known.
# Usage: factory/status.sh
# Reads FACTORY_DIR (default ~/.factory/arda), the same directory as the other factory scripts.
set -euo pipefail

fdir="${FACTORY_DIR:-$HOME/.factory/arda}"
stale_after=30

field() {
  local file="$1" label="$2" line
  line="$(grep -m1 -F -- "- ${label}:" "$file" || true)"
  line="${line#"- ${label}:"}"
  line="${line# }"
  printf '%s' "$line"
}

# Parse STATUS started stamps from new-task.sh (UTC, minute precision).
epoch_of() {
  local raw="$1" out
  [ -n "$raw" ] || return 1
  out="$(date -u -d "$raw" +%s 2>/dev/null)" || return 1
  printf '%s' "$out"
}

shopt -s nullglob
files=("$fdir"/STATUS-*.md)
[ "${#files[@]}" -eq 0 ] && exit 0

now="$(date +%s)"
for file in "${files[@]}"; do
  base="$(basename "$file")"
  slug="${base#STATUS-}"
  slug="${slug%.md}"
  branch="$(field "$file" branch)"
  running="$(field "$file" "now running")"
  wt="$(field "$file" worktree)"
  [ -n "$wt" ] || wt="$fdir/wt/$slug"

  minutes="-"
  stale="STALE"
  ts=""
  started_ts="$(epoch_of "$(field "$file" started)" || true)"
  commit_ts="$(git -C "$wt" log -1 --format=%ct 2>/dev/null || true)"
  if [ -n "$started_ts" ]; then
    ts="$started_ts"
  fi
  if [ -n "$commit_ts" ]; then
    if [ -z "$ts" ] || [ "$commit_ts" -gt "$ts" ]; then
      ts="$commit_ts"
    fi
  fi
  if [ -n "$ts" ]; then
    minutes="$(( (now - ts) / 60 ))"
    if [ "$minutes" -lt "$stale_after" ]; then
      stale=""
    fi
  fi

  if [ -n "$stale" ]; then
    printf '%s | %s | %s | %s | %s\n' "$slug" "$branch" "$minutes" "$running" "$stale"
  else
    printf '%s | %s | %s | %s\n' "$slug" "$branch" "$minutes" "$running"
  fi
done
