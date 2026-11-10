#!/bin/bash
# day-card-ctl.sh -- idempotent (un)scheduler for the WAKING-HOURS day-card
# refresh (cadence leg 3 of 3).
#
# The existing day-card unit wakes every five minutes, like mirror-poll.
# day-card.sh --tick enforces the waking window and gates publication to row
# boundaries, date changes and hourly TTL renewal. No extra unit is created.
#
# Usage:
#   day-card-ctl.sh enable  [<from_hour> [<to_hour>]]   default 7..23
#   day-card-ctl.sh disable
#   day-card-ctl.sh status            exit 0 = scheduled, 3 = not scheduled
#   day-card-ctl.sh refresh-enabled   upgrade an existing unit, keep window
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
OS="${ROUTINE_CTL_OS:-$(uname -s)}"

CMD="${1:-}"
FROM_H="${2:-7}"
TO_H="${3:-23}"
[ -n "$CMD" ] || { echo "usage: day-card-ctl.sh enable [from to] | disable | status | refresh-enabled" >&2; exit 1; }
case "$CMD" in enable|disable|status|refresh-enabled) ;; *) echo "[day-card-ctl] unknown command: $CMD" >&2; exit 1 ;; esac
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
  local dest="$LA_DIR/${LABEL}.plist" script_abs="$AGENT_DIR/$SCRIPT_REL"
  [ -f "$TPL" ] || { echo "[day-card-ctl] template not found: $TPL" >&2; exit 1; }
  [ -f "$script_abs" ] || { echo "[day-card-ctl] script not found: $script_abs" >&2; exit 1; }
  mkdir -p "$LA_DIR" "$AGENT_DIR/.telegram_bot/logs"
  sed -e "s#__LABEL__#${LABEL}#g" \
      -e "s#__SCRIPT__#${script_abs}#g" \
      -e "s#__ROOT__#${AGENT_DIR}#g" \
      -e "s#__HOMEDIR__#${HOME}#g" \
      -e "s#__LOGNAME__#${NAME}#g" \
      -e "s#__FROM_H__#${FROM_H}#g" \
      -e "s#__TO_H__#${TO_H}#g" \
      "$TPL" > "$dest"
  if command -v plutil >/dev/null 2>&1; then
    plutil -lint "$dest" >/dev/null || { echo "[day-card-ctl] plist lint failed: $dest" >&2; exit 2; }
  fi
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] && { echo "[day-card-ctl] rendered (no-load): $dest"; return 0; }
  _dc_launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
  _dc_launchctl bootstrap "gui/$UID_N" "$dest" 2>/dev/null \
    || _dc_launchctl load "$dest" 2>/dev/null || true
  mac_status || { echo "[day-card-ctl] could not verify scheduled: $LABEL" >&2; exit 2; }
  echo "[day-card-ctl] scheduled: $LABEL (every 5min, waking hours ${FROM_H}..${TO_H})"
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
ExecStart=/bin/bash ${script_abs} --tick --from-hour ${FROM_H} --to-hour ${TO_H}
WorkingDirectory=${AGENT_DIR}
Environment=HOME=${HOME}
EOF
  cat > "$UNIT_DIR/${UNIT}.timer" <<EOF
[Unit]
Description=dogany routine ${NAME} timer (${AGENT_NAME})

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] && { echo "[day-card-ctl] rendered (no-load): $UNIT_DIR/${UNIT}.timer"; return 0; }
  systemctl --user daemon-reload
  systemctl --user enable --now "${UNIT}.timer"
  lin_status || { echo "[day-card-ctl] could not verify scheduled: ${UNIT}.timer" >&2; exit 2; }
  echo "[day-card-ctl] scheduled: ${UNIT}.timer (every 5min, waking hours ${FROM_H}..${TO_H})"
}

lin_disable() {
  [ "${ROUTINE_CTL_NO_LOAD:-0}" = "1" ] || {
    systemctl --user disable --now "${UNIT}.timer" >/dev/null 2>&1 || true
    systemctl --user daemon-reload || true
  }
  rm -f "$UNIT_DIR/${UNIT}.timer" "$UNIT_DIR/${UNIT}.service"
  echo "[day-card-ctl] unscheduled: ${UNIT}.timer"
}

# Keep an approved window even if the generated files later disappear.
# A direct disable is an owner opt-out and must beat a stale BUNDLE flag.
remember_schedule() {
  python3 - "$AGENT_DIR/.telegram_bot/day-card-schedule.json" "$1" "$FROM_H" "$TO_H" <<'PY'
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
data = {'enabled': sys.argv[2] == 'on'}
if data['enabled']:
    data.update(from_hour=int(sys.argv[3]), to_hour=int(sys.argv[4]))
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_suffix('.tmp')
tmp.write_text(json.dumps(data, sort_keys=True) + '\n')
tmp.replace(path)
PY
}

# Pack updates must not enable a unit that the instance had switched off.
# Both generations keep their waking window in the unit itself: old hourly
# calendar entries, or the current --tick arguments. No live state is read.
refresh_enabled() {
  local dest service="" window from to
  case "$OS" in
    Darwin) dest="$LA_DIR/${LABEL}.plist" ;;
    *) dest="$UNIT_DIR/${UNIT}.timer"; service="$UNIT_DIR/${UNIT}.service" ;;
  esac
  [ -f "$dest" ] || return 0
  window="$(python3 - "$dest" "$service" "$AGENT_DIR/.telegram_bot/day-card-schedule.json" <<'PY'
import json
import pathlib
import plistlib
import re
import shlex
import sys


def window():
    unit, service, saved = sys.argv[1:]
    if not service:
        with open(unit, 'rb') as src:
            config = plistlib.load(src)
        args = config.get('ProgramArguments', [])
        if '--from-hour' in args and '--to-hour' in args:
            return (args[args.index('--from-hour') + 1],
                    args[args.index('--to-hour') + 1])
        entries = config.get('StartCalendarInterval', [])
        if isinstance(entries, dict):
            entries = [entries]
        hours = [int(entry['Hour']) for entry in entries if 'Hour' in entry]
        if hours:
            return min(hours), max(hours)
    else:
        path = pathlib.Path(service)
        body = path.read_text() if path.is_file() else ''
        for line in body.splitlines():
            if line.startswith('ExecStart='):
                args = shlex.split(line.split('=', 1)[1])
                if '--from-hour' in args and '--to-hour' in args:
                    return (args[args.index('--from-hour') + 1],
                            args[args.index('--to-hour') + 1])
        match = re.search(r'^OnCalendar=\*-\*-\*\s+(\d+)(?:\.\.(\d+))?:',
                          pathlib.Path(unit).read_text(), re.M)
        if match:
            return match[1], match[2] or match[1]
    # A current Linux timer does not carry its window. When only its service
    # has gone missing, recover the approved window from the enable record.
    path = pathlib.Path(saved)
    if path.is_file():
        data = json.loads(path.read_text())
        if data.get('enabled') is True:
            return data['from_hour'], data['to_hour']
    return 7, 23


try:
    start, end = map(int, window())
    if not 0 <= start <= end <= 23:
        start, end = 7, 23
except (OSError, ValueError, TypeError, KeyError, IndexError):
    start, end = 7, 23
print(start, end)
PY
)"
  read -r from to <<< "$window"
  /bin/bash "$SCRIPT_DIR/day-card-ctl.sh" enable "$from" "$to"
}

if [ "$CMD" = "refresh-enabled" ]; then
  refresh_enabled
  exit 0
fi

case "$OS" in
  Darwin) case "$CMD" in enable) mac_enable ;; disable) mac_disable ;; status) mac_status ;; esac ;;
  *)      case "$CMD" in enable) lin_enable ;; disable) lin_disable ;; status) lin_status ;; esac ;;
esac
case "$CMD" in enable) remember_schedule on ;; disable) remember_schedule off ;; esac
