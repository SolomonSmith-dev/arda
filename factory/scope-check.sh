#!/usr/bin/env bash
# Fail when task/<slug> changes a file outside the paths in STATUS-<slug>.md.
# Usage: factory/scope-check.sh <slug>
# A listed path covers that file and files under it. src/ui covers src/ui/a.py
# and does not cover src/uix/a.py. An empty list covers nothing.
# Committed changes are the diff from origin/<base> (STATUS base, or main) to HEAD.
# A rename counts on both sides, so a move into scope still shows the old path.
# Uncommitted files count too.
# Reads FACTORY_DIR (default ~/.factory/arda), the same directory as the other factory scripts.
set -euo pipefail

slug="${1:?usage: factory/scope-check.sh <slug>}"
[[ "$slug" =~ ^[a-z0-9][a-z0-9-]*$ ]] || {
  echo "slug must be lowercase letters, digits, hyphens" >&2
  exit 2
}

fdir="${FACTORY_DIR:-$HOME/.factory/arda}"
status="$fdir/STATUS-$slug.md"
[ -f "$status" ] || { echo "no status at $status" >&2; exit 2; }

field() {
  local label="$1" line
  line="$(grep -m1 -F -- "- ${label}:" "$status" || true)"
  line="${line#"- ${label}:"}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  printf '%s' "$line"
}

normalize() {
  local raw="$1" part out=""
  raw="${raw#./}"
  while [[ "$raw" == */ ]]; do raw="${raw%/}"; done
  case "$raw" in
    /*|*\\*|"") return 1 ;;
  esac
  [ "$raw" = "." ] && return 1
  local -a parts=()
  IFS=/ read -r -a parts <<< "$raw"
  for part in "${parts[@]}"; do
    case "$part" in
      ""|.) continue ;;
      ..) return 1 ;;
      *) out="${out:+$out/}$part" ;;
    esac
  done
  [ -n "$out" ] || return 1
  printf '%s' "$out"
}

paths=()
while IFS= read -r line; do
  line="${line#"- paths:"}"
  line="${line#"${line%%[![:space:]]*}"}"
  [ -n "$line" ] || continue
  read -r -a tokens <<< "$line"
  for token in "${tokens[@]}"; do
    if ! norm="$(normalize "$token")"; then
      echo "bad path: $token" >&2
      exit 2
    fi
    paths+=("$norm")
  done
done < <(grep -F -- "- paths:" "$status" || true)

base="$(field base)"
[ -n "$base" ] || base="main"
wt="$(field worktree)"
[ -n "$wt" ] || wt="$fdir/wt/$slug"
[ -d "$wt" ] || { echo "no worktree at $wt" >&2; exit 2; }
git -C "$wt" rev-parse --verify --quiet "refs/remotes/origin/$base" >/dev/null \
  || { echo "no origin/$base in $wt" >&2; exit 2; }

covered() {
  local file="$1" scope
  [ "${#paths[@]}" -eq 0 ] && return 1
  for scope in "${paths[@]}"; do
    case "$file" in
      "$scope"|"$scope"/*) return 0 ;;
    esac
  done
  return 1
}

list="$(mktemp)"
trap 'rm -f "$list"' EXIT
collect() {
  local path
  while IFS= read -r -d '' path; do
    [ -n "$path" ] || continue
    printf '%s\n' "$path" >> "$list"
  done
}
collect < <(git -C "$wt" diff --name-only -z --no-renames "origin/$base...HEAD")
collect < <(git -C "$wt" diff --name-only -z --no-renames)
collect < <(git -C "$wt" diff --name-only -z --no-renames --cached)
collect < <(git -C "$wt" ls-files -z --others --exclude-standard)

fail=0
while IFS= read -r file; do
  [ -n "$file" ] || continue
  if ! covered "$file"; then
    echo "OUT OF SCOPE: $file" >&2
    fail=1
  fi
done < <(LC_ALL=C sort -u "$list")
exit "$fail"
