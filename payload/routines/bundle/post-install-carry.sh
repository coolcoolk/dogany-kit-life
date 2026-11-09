#!/usr/bin/env bash
# Manifest post_install hook; cwd = instance root. Required reconciliation
# failures name the artifact in the log and leave the install incomplete.
set -uo pipefail
ROOT="$(pwd)"
LOG="$ROOT/logs/kit-post-install-carry.log"
mkdir -p "$ROOT/logs" || exit 1
failed=0

required() {
  local artifact="$1" out rc
  shift
  out="$("$@" 2>&1)"; rc=$?
  {
    printf '%s artifact=%s rc=%s root=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$artifact" "$rc" "$ROOT"
    [ -z "$out" ] || printf '%s\n' "$out"
  } >> "$LOG" || return 1
  [ -z "$out" ] || printf '[kit post_install] %s\n' "$out"
  if [ "$rc" -ne 0 ]; then
    echo "[kit post_install] $artifact failed rc=$rc (logged $LOG)" >&2
  fi
  return "$rc"
}

if ! required skill-alias "$ROOT/database/kit_life.sh" skill-alias carry --apply; then failed=1; fi
# Migration 097's optional mirror cleanup remains retryable. Its outcome is
# visible too; it is independent of required installed-artifact convergence.
if ! required meal-planned-marker-retire "$ROOT/database/kit_life.sh" meal-planned-marker-retire; then
  printf 'WARN optional meal-planned-marker-retire; retry on next install\n' >> "$LOG" || failed=1
fi
if ! required installed-artifacts python3 "$ROOT/routines/bundle/reconcile-installed.py" "$ROOT"; then failed=1; fi
exit "$failed"
