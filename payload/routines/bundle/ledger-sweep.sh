#!/bin/bash
# ledger-sweep.sh -- the weekly sweep at a time the owner picked (instead of
# the weekly retro). One message: the sweep ask (i18n ledger.sweep.ask.*),
# asking for this week's spend-list screenshot or export file.
#
# module_ledger (kit_life 2.46.0; shipped by pack_ledger before).
# SHIPS OFF. Scheduled only by the module onboarding when the owner chose a
# separate weekday/time (`onboarding apply --sweep <dow>@HH:MM`):
#   routines/lib/routine-ctl.sh enable ledger-sweep routines/bundle/ledger-sweep.sh <HH:MM> <dow>
# Choosing the weekly retro (the default) runs `routine-ctl.sh disable`.
# The reply (screenshot / file) is handled in the conversation by
# ledger-log (its import lane); this script only opens the topic. The text comes from the
# service (no Korean literal here).
set -uo pipefail
ROOT="${LEDGER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
PY="${LIFEKIT_PYTHON:-$(command -v python3 || echo /usr/bin/python3)}"
export PYTHONDONTWRITEBYTECODE=1
PUSH="$ROOT/routines/push.sh"
[[ -f "$PUSH" ]] || { echo "[ledger-sweep] push.sh not found" >&2; exit 1; }
MSG="$("$PY" "$ROOT/service/kit_life/ledger/retro-section" nudge)" || exit 0
[[ -n "$MSG" ]] || exit 0
[[ "${1:-}" == "--dry" ]] && { printf '%s\n' "$MSG"; exit 0; }
bash "$PUSH" --text "$MSG" --audience owner
