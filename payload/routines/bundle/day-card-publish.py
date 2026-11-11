#!/usr/bin/env python3
"""Kit-owned day-card publication gate; no framework state access.

Every producer holds the same lock from render through successful publication.
Only scheduled ticks are suppressed. Writes and morning --fresh always publish.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def read_state(path):
    try:
        state = json.loads(path.read_text())
        if (isinstance(state, dict) and isinstance(state.get('date'), str)
                and isinstance(state.get('boundary'), str)
                and isinstance(state.get('published_at'), (int, float))):
            return state
    except (OSError, ValueError):
        pass
    return {}


def due(state, snapshot, stamp):
    # Keep the existing HH:00 renewal in addition to a 60-minute recovery
    # floor. A boundary at 14:30 must not cancel the scheduled 15:00 renewal.
    previous = state.get('published_at', 0)
    return (state.get('date') != snapshot['date']
            or snapshot['boundary'] > state.get('boundary', '')
            or stamp - previous >= 3600
            or stamp // 3600 > previous // 3600)


def save_state(path, snapshot, stamp):
    state = dict(date=snapshot['date'], boundary=snapshot['boundary'],
                 published_at=stamp)
    fd, temp = tempfile.mkstemp(prefix='.day-card-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def run(args, state_path):
    footer = Path(args.agent_dir) / 'routines/status-footer.py'
    if args.disabled:
        if not args.dry:
            result = subprocess.run([sys.executable, str(footer), '--pin-clear', 'lifekit-day'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode == 0 and state_path.exists():
                state_path.unlink()
        return 2 if args.fresh else 0
    now = (dt.datetime.strptime(args.now, '%Y-%m-%dT%H:%M:%SZ')
           .replace(tzinfo=dt.timezone.utc) if args.now else dt.datetime.now(dt.timezone.utc))
    local = now.astimezone(dt.timezone(dt.timedelta(hours=float(args.tz_offset))))
    if args.tick and not args.fresh and not args.from_hour <= local.hour <= args.to_hour:
        return 0
    date = args.date or local.strftime('%Y-%m-%d')
    life = str(Path(args.agent_dir) / 'database/kit_life.sh')
    common = ['--date', date, '--tz-offset', args.tz_offset,
              '--exclude-prefix', args.exclude_prefix]
    rendered = subprocess.run([life, 'day-card', '--pin-meta', '--now',
                               now.strftime('%Y-%m-%dT%H:%M:%SZ')] + common,
                              capture_output=True, text=True)
    if rendered.returncode:
        print('[day-card] rendering failed; previous pin preserved', file=sys.stderr)
        return 1
    snapshot = json.loads(rendered.stdout)
    card = snapshot['text']
    if args.print_card and card:
        print(card)
    if args.dry:
        if not args.print_card:
            print(card or '(no rows today -- the pin would be cleared)')
        return 0
    if not footer.is_file():
        print('[day-card] no section store at %s; card not pinned' % footer, file=sys.stderr)
        return 3 if args.fresh else 0
    if args.tick and not args.fresh and not due(read_state(state_path), snapshot, now.timestamp()):
        return 0
    buttons = subprocess.run([life, 'day-pin-buttons'] + common,
                             capture_output=True, text=True)
    if buttons.returncode:
        print('[day-card] action rendering failed; previous pin preserved', file=sys.stderr)
        return 1
    if not card:
        command = ['--pin-clear', 'lifekit-day']
    else:
        command = ['--pin-set', 'lifekit-day', '--ttl', args.ttl]
        capabilities = footer.read_text()
        # Ask --pin-set itself: the footer file names --buttons-json for its session pins
        # (framework DGN-2262), which --pin-set rejects, so a file-wide sniff failed every publish.
        grammar = subprocess.run([sys.executable, str(footer), '--pin-set'],
                                 capture_output=True, text=True).stderr
        if '--buttons-json' in grammar:
            command += ['--buttons-json', buttons.stdout.strip()]
        else:
            print('[day-card] framework lacks domain pin actions; text refreshed without buttons', file=sys.stderr)
        if args.fresh and '"--fresh"' in capabilities:
            command += ['--fresh', '--wait', args.wait]
    result = subprocess.run([sys.executable, str(footer)] + command,
                            input=card + '\n', text=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        save_state(state_path, snapshot, now.timestamp())
    else:
        print('[day-card] pin publication failed or fresh pin not confirmed; tick will retry', file=sys.stderr)
    if args.fresh:
        if not card:
            return 2
        if '--fresh' not in command:
            return 3
        return 0 if result.returncode == 0 else 2
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-dir', required=True)
    parser.add_argument('--tz-offset', default='0')
    parser.add_argument('--ttl', default='7200')
    parser.add_argument('--exclude-prefix', default='')
    parser.add_argument('--date')
    parser.add_argument('--now')
    parser.add_argument('--tick', action='store_true')
    parser.add_argument('--disabled', action='store_true')
    parser.add_argument('--from-hour', type=int, default=7)
    parser.add_argument('--to-hour', type=int, default=23)
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--print', dest='print_card', action='store_true')
    parser.add_argument('--fresh', action='store_true')
    parser.add_argument('--wait', default='90')
    args = parser.parse_args()
    # Instance-local module state, outside versioned .gen and shared crew DB.
    state = Path(args.agent_dir) / 'files/kit_life/day-card-publish.json'
    if args.dry:
        return run(args, state)
    state.parent.mkdir(parents=True, exist_ok=True)
    with (state.parent / 'day-card-publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return run(args, state)


if __name__ == '__main__':
    sys.exit(main())
