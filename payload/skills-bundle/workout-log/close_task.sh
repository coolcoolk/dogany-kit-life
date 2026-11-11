#!/bin/bash
# Session-engine handoff and settlement of its explicitly ensured task.
# The exact sid link plus a durable close is evidence; titles are never matched.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
LIFEKIT_DB="$PROJ_ROOT/database/kit_life.db"
DAY="${1:-$(TZ=Asia/Seoul date +%F)}"
[[ "$DAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || exit 1
settle_closed_tasks() {
  if [[ -f "$PROJ_ROOT/database/kit_life.sh" ]]; then
    bash "$PROJ_ROOT/database/kit_life.sh" workout-retro-reconcile "$DAY" >/dev/null
  fi
}
settle_closed_tasks
BIN_DIR="$PROJ_ROOT/service/health"
STATE_DIR="${LIFEKIT_HEALTH_STATE_DIR:-$PROJ_ROOT/files/program}"
ACTIVE_MARKER="$STATE_DIR/.active-session"
# Preserve the handoff's stdout contract; settlement has no owner-visible copy.
echo "NONE"
[[ -z "${LIFEKIT_SESSION_CLOSE_CHAIN:-}" ]] || exit 0
[[ -x "$BIN_DIR/session-closeout" || -x "$BIN_DIR/session-cursor" ]] || exit 0
CLOSE_SID=""
CODE=""
# Existing session-engine category contract, used only for its handoff.
# It never selects a task or provides completion proof.
STRENGTH_CATEGORY=$'\xea\xb7\xbc\xeb\xa0\xa5'
# Planned or void v2 rows are not performed work. Old schemas keep their table.
HISTORY_SETS=workout_sets
KIT_UV=0
if [[ -r "$LIFEKIT_DB" ]] && command -v sqlite3 >/dev/null 2>&1; then
  KIT_UV="$(sqlite3 "$LIFEKIT_DB" 'PRAGMA query_only=ON; PRAGMA user_version;' 2>/dev/null || echo 0)"
  if [[ "$KIT_UV" =~ ^[0-9]+$ ]] && (( KIT_UV >= 78 )); then
    HISTORY_SETS=v2_logged_workout_sets
  fi
  ROW="$(sqlite3 -separator '|' "$LIFEKIT_DB" "PRAGMA query_only=ON;
    SELECT w.id, CASE WHEN EXISTS
      (SELECT 1 FROM ${HISTORY_SETS} ws WHERE ws.session_id=w.id
       AND ws.set_kind IN ('working','hold')) THEN COALESCE(st.code, '')
      ELSE '' END AS performed_code FROM workouts w
    LEFT JOIN session_type st ON st.id=w.session_type_id
    WHERE w.date='$DAY' AND (
      EXISTS (SELECT 1 FROM ${HISTORY_SETS} ws WHERE ws.session_id=w.id
              AND ws.set_kind IN ('working','hold'))
      OR (w.session_type_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM ${HISTORY_SETS} ws WHERE ws.session_id=w.id)
          AND EXISTS (SELECT 1 FROM workout_classifications wc
                      JOIN workout_types wt ON wt.id=wc.type_id
                      WHERE wc.workout_id=w.id AND wt.category='$STRENGTH_CATEGORY')))
    ORDER BY (performed_code != '') DESC, w.id DESC LIMIT 1;" 2>/dev/null || true)"
  # A prep-only or cardio-only day is not the session engine's end signal.
  [[ -n "$ROW" ]] || exit 0
  CLOSE_SID="${ROW%%|*}"
  [[ "$ROW" != *'|'* ]] || CODE="${ROW#*|}"
fi
if [[ -z "$CLOSE_SID" && -r "$ACTIVE_MARKER" ]]; then
  CLOSE_SID="$(tr -dc '0-9' < "$ACTIVE_MARKER")"
fi
[[ -n "$CLOSE_SID" ]] || exit 0
# The legacy engine cannot close canonical v2 sessions, including marker targets.
if [[ "$KIT_UV" =~ ^[0-9]+$ ]] && (( KIT_UV >= 78 )); then
  CLOSE_VERSION="$(sqlite3 "$LIFEKIT_DB" "PRAGMA query_only=ON;
    SELECT session_version FROM workouts WHERE id=${CLOSE_SID};" 2>/dev/null || true)"
  [[ "$CLOSE_VERSION" == "1" ]] || exit 0
fi
if [[ -n "$CODE" && -x "$BIN_DIR/session-closeout" ]]; then
  rc=0
  LIFEKIT_SESSION_CLOSE_CHAIN=1 "$BIN_DIR/session-closeout" "$CLOSE_SID" "$CODE" >&2 || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    echo "[close_task] session-closeout REFUSED (exit $rc) sid=$CLOSE_SID" >&2
  fi
elif [[ -x "$BIN_DIR/session-cursor" ]]; then
  "$BIN_DIR/session-cursor" clear --marker "$ACTIVE_MARKER" >&2 || true
fi
# When this script entered first, the chain guard prevents the engine from
# calling us back. Its newly durable close must be settled on this edge too.
settle_closed_tasks
