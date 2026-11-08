#!/bin/bash
# appt.sh -- THIN delegator to the appt-* verbs of kit_life.sh (schedule-log skill).
#
# DGN-180 cutover (04a): the Notion backend is gone -- kit_life.py (via
# kit_life.sh) OWNS kit_life.db. ZERO SQL, ZERO curl here; every verb forwards
# to the owner CLI (same model as task.sh). Kept as a belt for any stale
# caller -- the skill itself (SKILL.md) already instructs calling the kit verbs.
#
# Verb map (legacy -> kit_life.sh):
#   find <from> [to]         -> appt-find   (TSV: id, local time, title, location)
#   show <id>                -> appt-show   (id = event integer id; legacy
#                                            Notion page ids are NOT resolvable
#                                            here anymore -- use find first)
#   add <title> <start> [..] -> schedule-add --kind appointment
#       Legacy add means appointment; --kind task|auto overrides that judgment.
#       Missing ends stay unknown; matching tasks inherit routine structure.
#   upd <id> k=v ...         -> appt-upd
#   link <id> <person>       -> appt-person (person name or id)
#   person-find <query>      -> person-find (sqlite only)
#   person-appts             -> REMOVED (OQ-B4 ruling: no kit_life verb; no
#                               known caller. Loud error, exit 1.)
#
# Exit codes: passed through from the kit CLI unchanged.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${PROJECT_ROOT:-}" ]]; then
  ROOT="$PROJECT_ROOT"
else
  ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

LIFEKIT="$ROOT/database/kit_life.sh"
if [[ ! -x "$LIFEKIT" ]]; then
  echo "[appt] kit CLI not found or not executable: $LIFEKIT" >&2
  exit 1
fi

CMD="${1:-}"
case "$CMD" in
  find)        shift; exec "$LIFEKIT" appt-find "$@" ;;
  show)        shift; exec "$LIFEKIT" appt-show "$@" ;;
  add)
    shift
    # Existing add callers already selected an appointment. Preserve that intent;
    # a caller that judged self activity supplies its own typed kind.
    for arg in "$@"; do
      if [[ "$arg" == "--kind" ]]; then
        exec "$LIFEKIT" schedule-add "$@"
      fi
    done
    exec "$LIFEKIT" schedule-add "$@" --kind appointment
    ;;
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
