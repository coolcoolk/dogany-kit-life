#!/bin/bash
# appt.sh -- THIN delegator to kit_life.sh appt-* verbs (schedule-log skill).
#
# DGN-180 cutover (04a): the Notion backend is gone -- kit_life.py (via
# kit_life.sh) OWNS kit_life.db. ZERO SQL, ZERO curl here; every verb forwards
# to the owner CLI (same model as task.sh). Kept as a belt for any stale
# caller -- the skill itself (SKILL.md) already instructs kit_life.sh directly.
#
# Verb map (legacy -> kit_life.sh):
#   find <from> [to]         -> appt-find   (TSV: id, local time, title, location)
#   show <id>                -> appt-show   (id = event integer id; legacy
#                                            Notion page ids are NOT resolvable
#                                            here anymore -- use find first)
#   add <title> <start> [..] -> appt-add    (end omitted -> +3h default,
#                                            owner ruling 2026-09-30)
#   upd <id> k=v ...         -> appt-upd
#   link <id> <person>       -> appt-person (person name or id)
#   person-find <query>      -> person-find (sqlite only)
#   person-appts             -> REMOVED (OQ-B4 ruling: no kit_life verb; no
#                               known caller. Loud error, exit 1.)
#
# Exit codes: passed through from kit_life.sh unchanged.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${PROJECT_ROOT:-}" ]]; then
  ROOT="$PROJECT_ROOT"
else
  ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

LIFEKIT="$ROOT/database/kit_life.sh"
if [[ ! -x "$LIFEKIT" ]]; then
  echo "[appt] kit_life.sh not found or not executable: $LIFEKIT" >&2
  exit 1
fi

CMD="${1:-}"
case "$CMD" in
  find)        shift; exec "$LIFEKIT" appt-find "$@" ;;
  show)        shift; exec "$LIFEKIT" appt-show "$@" ;;
  add)         shift; exec "$LIFEKIT" appt-add "$@" ;;
  upd)         shift; exec "$LIFEKIT" appt-upd "$@" ;;
  link)        shift; exec "$LIFEKIT" appt-person "$@" ;;
  person-find) shift; exec "$LIFEKIT" person-find "$@" ;;
  person-appts)
    echo "[appt] person-appts는 폐기되었습니다 (DGN-180 OQ-B4). appt-find로 조회하세요." >&2
    exit 1
    ;;
  *)
    echo "usage: appt.sh find|show|add|upd|link|person-find ..." >&2
    exit 1
    ;;
esac
