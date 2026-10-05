#!/bin/bash
# close_task.sh -- auto-close today's workout session task after a workout log.
#
# The kit's workout-log skill calls this after recording a workout to complete
# that day's session task. Session tasks are timeblock kind='task' titled
# $SESSION_TITLE (generic) or "$SESSION_TITLE - <split>" (once a split is
# known). kit_life-backed (SQL lane) only.
#
# Behavior (rename + complete):
#   1. Find today's OPEN session task -- title == $SESSION_TITLE or
#      "$SESSION_TITLE - <...>" (task-find + a date/status awk filter).
#   2. Resolve today's performed split: the most-recent workout of DAY that
#      carries a session_type_id -> session_type.code. Read-only SELECT against
#      the same DB the kit_life CLI uses (never a raw write). A day with no
#      tagged row (cardio only, or no health pack) resolves no code.
#   3. If a code resolved AND the title is not already "$SESSION_TITLE - <code>":
#      kit_life.sh task-retitle <id> "$SESSION_TITLE - <code>". No code -> the
#      title is kept.
#   4. kit_life.sh task-done <id>.
#
# It degrades gracefully: any failure / no CLI / no match -> NONE and exit 0.
# The workout-log skill depends on the CLOSED/NONE/MULTI contract.
#
# EXTERNAL SURFACE (declared, probed, never required). After the task close
# this script MAY hand the session end to a session engine if one is installed
# at $PROJECT_ROOT/service/health/{session-closeout,session-cursor}. Those are
# not kit files. Each call is behind an [[ -x ]] probe; absent -> nothing runs
# and nothing is said. The kit works identically without them. See SKILL.md
# "external surface".
#
# Environment (neutral names; a session engine that drives this script must
# use the same ones):
#   LIFEKIT_HEALTH_STATE_DIR     runtime marker root (default
#                                $PROJECT_ROOT/files/program). A test harness
#                                redirects it to a temp dir.
#   LIFEKIT_SESSION_CLOSE_CHAIN  re-entrancy flag shared with the session
#                                engine's close-out. Set = a close-out is
#                                already in flight; do not drive another.
#
# Usage:
#   close_task.sh [YYYY-MM-DD]   date defaults to today (KST)
#
# stdout:
#   CLOSED<TAB><final_title>   exactly one matching open task, renamed+completed
#   NONE                       no matching open task (or CLI unavailable) -- silent
#   MULTI                      2+ candidates -- not auto-closed; candidate lines follow:
#     <id><TAB><title>
#
# Exit codes: 0 success (CLOSED/NONE/MULTI) / 1 config/arg error.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The session task title this script owns (data, matched verbatim).
SESSION_TITLE="헬스"
# The workout_types category whose rows are session (set-based) workouts.
STRENGTH_CATEGORY="근력"

# Resolve kit_life.sh path-independently: prefer $PROJECT_ROOT, else walk up from
# the skill dir (.claude/skills-bundle/workout-log -> three levels up = PROJECT_ROOT).
if [[ -n "${PROJECT_ROOT:-}" && -x "$PROJECT_ROOT/database/kit_life.sh" ]]; then
  LIFEKIT="$PROJECT_ROOT/database/kit_life.sh"
else
  ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
  LIFEKIT="$ROOT/database/kit_life.sh"
fi
# The DB the kit_life CLI reads is <kit_life dir>/kit_life.db (a symlink to the
# shared crews DB). Resolve it the same way kit_life.py does.
LIFEKIT_DB="$(cd "$(dirname "$LIFEKIT")" && pwd)/kit_life.db"

DAY="${1:-$(TZ=Asia/Seoul date +%F)}"

# CLI not available / task verbs missing -> graceful no-op. Probe task-find.
if [[ ! -x "$LIFEKIT" ]] || ! "$LIFEKIT" task-find "$SESSION_TITLE" >/dev/null 2>&1; then
  echo "NONE"
  exit 0
fi

# task-find -> id<TAB>title<TAB>due<TAB>state<TAB>place (one row per match).
# Keep only today's OPEN (todo) rows whose title is exactly the session title
# or "<session title> - ...".
MATCHES="$("$LIFEKIT" task-find "$SESSION_TITLE" 2>/dev/null \
  | awk -F'\t' -v d="$DAY" -v t="$SESSION_TITLE" '
      $3==d && $4=="todo" && ($2==t || index($2, t" -")==1) {print $1"\t"$2}')"

COUNT="$(printf '%s\n' "$MATCHES" | grep -c . || true)"

case "$COUNT" in
  0)
    echo "NONE"
    ;;
  1)
    ID="$(printf '%s\n' "$MATCHES" | head -1 | cut -f1)"
    TITLE="$(printf '%s\n' "$MATCHES" | head -1 | cut -f2)"

    # Resolve today's performed split (read-only). Most-recent tagged workout
    # of DAY -> session_type.code. Untagged/NULL -> empty. Any error -> empty
    # (title simply kept). We never write DATA via raw sqlite: `PRAGMA
    # query_only=ON` hard-refuses any mutation while keeping normal WAL-mode
    # connection semantics. (A bare `-readonly`/`mode=ro` open fails on a WAL
    # DB because it cannot map the -shm sidecar.)
    CODE=""
    if [[ -r "$LIFEKIT_DB" ]] && command -v sqlite3 >/dev/null 2>&1; then
      CODE="$(sqlite3 "$LIFEKIT_DB" \
        "PRAGMA query_only=ON;
         SELECT st.code FROM workouts w
           JOIN session_type st ON st.id = w.session_type_id
          WHERE w.date = '${DAY}' AND w.session_type_id IS NOT NULL
          ORDER BY w.id DESC LIMIT 1;" 2>/dev/null || true)"
    fi

    FINAL_TITLE="$TITLE"
    if [[ -n "$CODE" ]]; then
      WANT="${SESSION_TITLE} - ${CODE}"
      if [[ "$TITLE" != "$WANT" ]]; then
        if "$LIFEKIT" task-retitle "$ID" "$WANT" >/dev/null 2>&1; then
          FINAL_TITLE="$WANT"
        fi
      else
        FINAL_TITLE="$WANT"
      fi
    fi

    "$LIFEKIT" task-done "$ID" >/dev/null 2>&1 || { echo "NONE"; exit 0; }

    # --- session-end hand-off (guarded probe; see EXTERNAL SURFACE above) ---
    # When a session engine is installed, this is its deterministic session-END
    # carrier (the mirror of its session-open at birth): drive its close-out so
    # the active-session marker is cleared and the session tag is validated at
    # session end. Best-effort + fully detached: a close-out failure NEVER
    # blocks the task close or the CLOSED contract. Engine absent -> skipped.
    #   - session id: the day's latest strength row, falling back to the
    #     active-session marker.
    #   - code: the split resolved above; if empty we only clear the marker via
    #     `session-cursor clear` so a stale gate never survives.
    if [[ -n "${PROJECT_ROOT:-}" ]]; then
      PROJ_ROOT="$PROJECT_ROOT"
    else
      PROJ_ROOT="$(cd "$(dirname "$LIFEKIT")/.." && pwd)"
    fi
    BIN_DIR="$PROJ_ROOT/service/health"
    STATE_DIR="${LIFEKIT_HEALTH_STATE_DIR:-$PROJ_ROOT/files/program}"
    ACTIVE_MARKER="$STATE_DIR/.active-session"
    if [[ -x "$BIN_DIR/session-closeout" || -x "$BIN_DIR/session-cursor" ]]; then
      # The day's latest strength row is the authoritative session id for THIS
      # day's close; the marker is only a hint and may be stale or phantom.
      CLOSE_SID=""
      if [[ -r "$LIFEKIT_DB" ]] && command -v sqlite3 >/dev/null 2>&1; then
        CLOSE_SID="$(sqlite3 "$LIFEKIT_DB" \
          "PRAGMA query_only=ON;
           SELECT w.id FROM workouts w
             JOIN workout_classifications wc ON wc.workout_id = w.id
             JOIN workout_types wt ON wt.id = wc.type_id
            WHERE w.date = '${DAY}' AND wt.category = '${STRENGTH_CATEGORY}'
            ORDER BY w.id DESC LIMIT 1;" 2>/dev/null || true)"
      fi
      if [[ -z "$CLOSE_SID" && -r "$ACTIVE_MARKER" ]]; then
        CLOSE_SID="$(tr -dc '0-9' < "$ACTIVE_MARKER" 2>/dev/null || true)"
      fi
      # RE-ENTRANCY GUARD. The engine's close-out drives THIS script at its own
      # end, so the two are a mutual pair and exactly one may drive the other
      # per close. LIFEKIT_SESSION_CLOSE_CHAIN set = a close-out is already in
      # flight; the task close above still happened, only the reverse edge is
      # skipped.
      if [[ -n "$CLOSE_SID" && -z "${LIFEKIT_SESSION_CLOSE_CHAIN:-}" ]]; then
        if [[ -n "$CODE" ]] && [[ -x "$BIN_DIR/session-closeout" ]]; then
          # nohup + & detaches (macOS has no setsid). exit 4 = close-out
          # REFUSED (nothing written, session stays open): keep its text where
          # a human and the bot log can see it instead of swallowing it.
          _CO_OUT="${TMPDIR:-/tmp}/session-closeout-${CLOSE_SID}.out"
          (
            # `|| _co_rc=$?`, not a bare `_co_rc=$?` on the next line: the
            # subshell inherits `set -e`, which would end it on the non-zero
            # exit before the refusal could be reported.
            _co_rc=0
            LIFEKIT_SESSION_CLOSE_CHAIN=1 \
            nohup "$BIN_DIR/session-closeout" "$CLOSE_SID" "$CODE" \
              >"$_CO_OUT" 2>&1 || _co_rc=$?
            if [[ "$_co_rc" -eq 4 ]]; then
              {
                echo "[close_task] session-closeout REFUSED (exit 4) sid=$CLOSE_SID"
                cat "$_CO_OUT" 2>/dev/null
              } >&2
            else
              rm -f "$_CO_OUT" 2>/dev/null || true
            fi
            exit 0
          ) &
        elif [[ -x "$BIN_DIR/session-cursor" ]]; then
          # no confident code -> just clear the gate marker.
          ( nohup "$BIN_DIR/session-cursor" clear \
              --marker "$ACTIVE_MARKER" >/dev/null 2>&1 || true ) &
        fi
      fi
    fi

    printf 'CLOSED\t%s\n' "$FINAL_TITLE"
    ;;
  *)
    echo "MULTI"
    printf '%s\n' "$MATCHES"
    ;;
esac
