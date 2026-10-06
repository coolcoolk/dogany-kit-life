#!/bin/bash
# day-card-ctl.sh -- idempotent (un)scheduler for the WAKING-HOURS day-card
# refresh (cadence leg 3 of 3).
#
# WHY THIS EXISTS AND NOT routine-ctl.sh. routine-ctl.sh is the estate's
# routine scheduler and stays the scheduler for every bundle routine,
# day-card included: `routine-ctl.sh enable day-card routines/bundle/day-card.sh
# HH:MM` gives the once-a-day run and is what the bundle activation path uses.
# But it renders routine.plist.tpl, which carries exactly ONE calendar
# interval, and the pinned card's reason to exist is that it moves during the
# day (past rows spent, current position marked). One daily render would pin a
# card that is wrong from 07:05 onward while still looking live.
#
# So this helper does ONE thing routine-ctl cannot: render a MULTI-interval
# unit for this one routine. Same label grammar, same token set, same
# NO_LOAD/dir env seams, same exit codes -- deliberately, so folding this back
# into routine-ctl.sh (if it ever learns multi-interval scheduling) is a
# delete, not a rewrite.
#
# Template tokens (day-card.plist.tpl): __LABEL__ __SCRIPT__ __ROOT__
# __HOMEDIR__ __LOGNAME__ __INTERVALS__ (one <dict> per scheduled hour).
#
# Usage:
#   day-card-ctl.sh enable  [<from_hour> [<to_hour>]]   default 7..23
#   day-card-ctl.sh disable
#   day-card-ctl.sh status            exit 0 = scheduled, 3 = not scheduled
#
# Env seams (never needed in normal operation; same names routine-ctl uses):
#   ROUTINE_CTL_LA_DIR      override ~/Library/LaunchAgents (macOS)
#   ROUTINE_CTL_UNIT_DIR    override ~/.config/systemd/user (Linux)
#   ROUTINE_CTL_NO_LOAD=1   render + place files only, skip launchctl/systemctl
#   ROUTINE_CTL_LAUNCHCTL   override the launchctl binary (macOS)
#
# Exit: 0 ok / 1 usage or config error / 2 scheduler verification failed /
#       3 (status only) not scheduled
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TPL="$SCRIPT_DIR/day-card.plist.tpl"
NAME="day-card"
SCRIPT_REL="routines/bundle/day-card.sh"

AGENT_NAME="$(grep -E '^DOGANY_AGENT_NAME=' "$AGENT_DIR/.instance.conf" 2>/dev/null | head -1 | cut -d= -f2 || true)"
AGENT_NAME="${AGENT_NAME:-$(basename "$AGENT_DIR")}"
LABEL="com.telegram-skill-bot.${AGENT_NAME}.${NAME}"
UNIT="dogany-${AGENT_NAME}-${NAME}"
LA_DIR="${ROUTINE_CTL_LA_DIR:-$HOME/Library/LaunchAgents}"
UNIT_DIR="${ROUTINE_CTL_UNIT_DIR:-$HOME/.config/systemd/user}"
UID_N="$(id -u)"

CMD="${1:-}"
FROM_H="${2:-7}"
TO_H="${3:-23}"
[ -n "$CMD" ] || { echo "usage: day-card-ctl.sh enable [from to] | disable | status" >&2; exit 1; }
case "$CMD" in enable|disable|status) ;; *) echo "[day-card-ctl] unknown command: $CMD" >&2; exit 1 ;; esac
if [ "$CMD" = "enable" ]; then
  case "$FROM_H$TO_H" in (*[!0-9]*) echo "[day-card-ctl] hours must be integers" >&2; exit 1 ;; esac
  FROM_H=$((10#$FROM_H)); TO_H=$((10#$TO_H))
  { [ "$FROM_H" -ge 0 ] && [ "$TO_H" -le 23 ] && [ "$FROM_H" -le "$TO_H" ]; } \
    || { echo "[day-card-ctl] bad waking window: ${FROM_H}..${TO_H} (need 0 <= from <= to <= 23)" >&2; exit 1; }
fi

_dc_launchctl() { "${ROUTINE_CTL_LAUNCHCTL:-launchctl}" "$@"; }

mac_status() {
  _dc_launchctl print "gui/$UID_N/$LABEL" >/dev/null 2>&1 && return 0
  _dc_launchctl list 2>/dev/null | grep -q -- "$LABEL" && return 0
  return 3
}

mac_enable() {
  local dest="$LA_DIR/${LABEL}.plist" script_abs="$AGENT_DIR/$SCRIPT_REL" h intervals=""
  [ -f "$TPL" ] || { echo "[day-card-ctl] template not found: $TPL" >&2; exit 1; }
  [ -f "$script_abs" ] || { echo "[day-card-ctl] script not found: $script_abs" >&2; exit 1; }
  mkdir -p "$LA_DIR" "$AGENT_DIR/.telegram_bot/logs"
  h="$FROM_H"
  while [ "$h" -le "$TO_H" ]; do
    intervals="${intervals}<dict><key>Hour</key><integer>${h}</integer><key>Minute</key><integer>0</integer></dict>"
    h=$((h + 1))
  done
  sed -e "s#__LABEL__#${LABEL}#g" \
      -e "s#__SCRIPT__#${script_abs}#g" \
      -e "s#__ROOT__#${AGENT_DIR}#g" \
      -e "s#__HOMEDIR__#${HOME}#g" \
      -e "s#__LOGNAME__#${NAME}#g" \
      -e "s#__INTERVALS__#${intervals}#g" \
      "$TPL" > "$dest"
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$dest" >/dev/null || { echo "[day-card-ctl] plist lint failed: $dest" >&2; exit 2; }
  fi
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] && { echo "[day-card-ctl] rendered (no-load): $dest"; return 0; }
  _dc_launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
  _dc_launchctl bootstrap "gui/$UID_N" "$dest" 2>/dev/null \
    || _dc_launchctl load "$dest" 2>/dev/null || true
  mac_status || { echo "[day-card-ctl] could not verify scheduled: $LABEL" >&2; exit 2; }
  echo "[day-card-ctl] scheduled: $LABEL (hourly ${FROM_H}:00..${TO_H}:00)"
}

mac_disable() {
  local dest="$LA_DIR/${LABEL}.plist"
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] || _dc_launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
  [ -f "$dest" ] && rm -f "$dest"
  echo "[day-card-ctl] unscheduled: $LABEL"
}

lin_status() { systemctl --user is-enabled "${UNIT}.timer" >/dev/null 2>&1 && return 0; return 3; }

lin_enable() {
  local script_abs="$AGENT_DIR/$SCRIPT_REL"
  [ -f "$script_abs" ] || { echo "[day-card-ctl] script not found: $script_abs" >&2; exit 1; }
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/${UNIT}.service" <<EOF
[Unit]
Description=dogany routine ${NAME} (${AGENT_NAME})

[Service]
Type=oneshot
ExecStart=/bin/bash ${script_abs}
WorkingDirectory=${AGENT_DIR}
Environment=HOME=${HOME}
EOF
  cat > "$UNIT_DIR/${UNIT}.timer" <<EOF
[Unit]
Description=dogany routine ${NAME} timer (${AGENT_NAME})

[Timer]
OnCalendar=*-*-* ${FROM_H}..${TO_H}:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] && { echo "[day-card-ctl] rendered (no-load): $UNIT_DIR/${UNIT}.timer"; return 0; }
  systemctl --user daemon-reload
  systemctl --user enable --now "${UNIT}.timer"
  lin_status || { echo "[day-card-ctl] could not verify scheduled: ${UNIT}.timer" >&2; exit 2; }
  echo "[day-card-ctl] scheduled: ${UNIT}.timer (hourly ${FROM_H}:00..${TO_H}:00)"
}

lin_disable() {
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] || {
    systemctl --user disable --now "${UNIT}.timer" >/dev/null 2>&1 || true
    systemctl --user daemon-reload || true
  }
  rm -f "$UNIT_DIR/${UNIT}.timer" "$UNIT_DIR/${UNIT}.service"
  echo "[day-card-ctl] unscheduled: ${UNIT}.timer"
}

case "$(uname -s)" in
  Darwin) case "$CMD" in enable) mac_enable ;; disable) mac_disable ;; status) mac_status ;; esac ;;
  *)      case "$CMD" in enable) lin_enable ;; disable) lin_disable ;; status) lin_status ;; esac ;;
esac
