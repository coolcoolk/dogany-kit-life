#!/usr/bin/env python3
"""Refresh kit schedules from saved choices, then reconcile slots and criteria.

No onboarding is replayed. Explicit off/retirement wins over stale files or
flags. Unanswered legacy bundle flags may refresh an installed enabled unit,
but only an explicit on (or saved day-card enable) can restore a missing one.
The framework's supported renderer intentionally replaces old cron-guard
wrappers with its current direct bundle-script arguments.
"""
import json
import os
from pathlib import Path
import plistlib
import re
import shlex
import subprocess
import sys


DAYS = ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat')


def conf(path):
    result = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:]
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                result.setdefault(key.strip(), ' '.join(shlex.split(value, comments=True)))
    return result


def state(path):
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError('expected an object: %s' % path)
    return data


def schedule(value):
    parts = value.lower().split()
    if len(parts) == 1:
        clock, day = parts[0], ''
    elif len(parts) == 2 and parts[0] in DAYS:
        day, clock = parts
    else:
        raise ValueError('bad saved schedule: %r' % value)
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
        raise ValueError('bad saved clock: %r' % value)
    return clock, day


class Reconciler:
    def __init__(self, root):
        self.root = root
        self.env = dict(os.environ)
        self.no_load = self.env.get('ROUTINE_CTL_NO_LOAD') == '1'
        # brief-slot-ctl has a separate no-load seam. Never let a render-only
        # update call a live launchctl through the slot reconciliation path.
        if self.no_load:
            self.env['BRIEF_SLOT_NO_LOAD'] = '1'
        self.mac = self.env.get('ROUTINE_CTL_OS', os.uname().sysname) == 'Darwin'
        self.agent = conf(root / '.instance.conf').get('DOGANY_AGENT_NAME') or root.name
        self.kconf = conf(root / 'config/kit_life.conf')
        self.aconf = conf(root / 'config/agent.conf')
        self.la = Path(self.env.get('ROUTINE_CTL_LA_DIR', str(Path.home() / 'Library/LaunchAgents')))
        self.units = Path(self.env.get('ROUTINE_CTL_UNIT_DIR', str(Path.home() / '.config/systemd/user')))
        retired = root / '.telegram_bot/retired-routines.tsv'
        self.retired = {line.split('\t')[0] for line in retired.read_text().splitlines()} if retired.is_file() else set()
        self.clock_path = root / '.telegram_bot/kit-scheduler-clocks.json'
        self.clocks = state(self.clock_path)
        self.failed = False

    def label(self, name):
        return 'com.telegram-skill-bot.%s.%s' % (self.agent, name)

    def unit(self, name):
        if self.mac:
            return self.la / (self.label(name) + '.plist')
        return self.units / ('dogany-%s-%s.timer' % (self.agent, name))

    def run(self, *args):
        proc = subprocess.run([str(a) for a in args], cwd=self.root,
                              env=self.env, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=120)
        if proc.stdout:
            print(proc.stdout, end='' if proc.stdout.endswith('\n') else '\n', flush=True)
        # bundle-routine-ctl reports slot failures as WARN with exit 0.
        # That is not success for required post-install reconciliation.
        if proc.returncode or any(line.startswith('WARN\tbrief-slot-ctl\t')
                                  for line in proc.stdout.splitlines()):
            raise ValueError('command failed rc=%s: %s' % (proc.returncode, ' '.join(map(str, args))))

    def attempt(self, name, action):
        try:
            action()
            print('OK\t%s' % name, flush=True)
        except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
            self.failed = True
            print('ERROR\t%s\t%s' % (name, exc), flush=True)

    def enabled(self, name, flag):
        if flag == 'off' or self.label(name) in self.retired:
            return False
        if flag == 'on':
            return True
        if not self.unit(name).is_file():
            return False
        if self.mac or self.no_load:
            return True
        # A disabled systemd timer file remains on disk. File presence alone
        # must not turn it back on when there is no saved owner opt-in.
        proc = subprocess.run(['systemctl', '--user', 'is-enabled', self.unit(name).name],
                              env=self.env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30)
        if proc.returncode not in (0, 1):
            raise ValueError('cannot determine enabled state: %s' % self.unit(name))
        return proc.returncode == 0

    def installed_schedule(self, name):
        path = self.unit(name)
        if not path.is_file():
            return ''
        if self.mac:
            with path.open('rb') as src:
                entry = plistlib.load(src)['StartCalendarInterval']
            if isinstance(entry, list):
                if len(entry) != 1:
                    raise ValueError('expected one calendar interval: %s' % path)
                entry = entry[0]
            clock = '%02d:%02d' % (entry['Hour'], entry['Minute'])
            return (DAYS[int(entry['Weekday']) % 7] + ' ' if 'Weekday' in entry else '') + clock
        match = re.search(r'^OnCalendar=(?:(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+)?\*-\*-\*\s+(\d{2}:\d{2}):00\s*$', path.read_text(), re.M | re.I)
        if not match:
            raise ValueError('cannot read saved calendar: %s' % path)
        return ((match[1].lower() + ' ') if match[1] else '') + match[2]

    def render(self, name, value):
        clock, day = schedule(value)
        args = ['/bin/bash', self.root / 'routines/lib/routine-ctl.sh', 'enable',
                name, 'routines/bundle/%s.sh' % name, clock]
        if day:
            args.append(day)
        self.run(*args)
        # Preserve a legacy unit's only copy of its owner-selected clock,
        # so a later missing-file repair does not fall back to kit defaults.
        self.clocks[name] = (day + ' ' if day else '') + clock
        self.clock_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.clock_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.clocks, sort_keys=True) + '\n')
        tmp.replace(self.clock_path)

    def bundle_routine(self, name, default, slot):
        flag = self.kconf.get('BUNDLE_' + name.replace('-', '_').upper(), '')
        if name == 'day-card':
            saved = state(self.root / '.telegram_bot/day-card-schedule.json')
            if saved.get('enabled') is False:
                flag = 'off'
            elif saved.get('enabled') is True and flag != 'off':
                flag = 'on'
            if self.enabled(name, flag):
                ctl = self.root / 'routines/bundle/day-card-ctl.sh'
                if self.unit(name).is_file():
                    self.run('/bin/bash', ctl, 'refresh-enabled')
                else:
                    start, end = saved.get('from_hour', 7), saved.get('to_hour', 23)
                    # A partial Linux pair may still carry the owner's window
                    # in its service even when the timer/enable record is lost.
                    service = self.unit(name).with_suffix('.service')
                    if not self.mac and service.is_file():
                        for line in service.read_text().splitlines():
                            if line.startswith('ExecStart='):
                                args = shlex.split(line.split('=', 1)[1])
                                if '--from-hour' in args and '--to-hour' in args:
                                    start = args[args.index('--from-hour') + 1]
                                    end = args[args.index('--to-hour') + 1]
                    self.run('/bin/bash', ctl, 'enable', start, end)
        elif self.enabled(name, flag):
            clock = self.aconf.get('BRIEF_TIME_' + slot.upper(), '') if slot != '-' else ''
            self.render(name, clock or self.installed_schedule(name) or self.clocks.get(name) or default)

    def ledger(self, name):
        saved = state(self.root / 'files/ledger/onboarding.json')
        if self.label(name) in self.retired:
            return
        if name == 'ledger-sms-poll':
            if saved.get('auto'):
                # Same legacy-pack precedence as ledgerlib.paths.
                config = dict(self.kconf)
                config.update({k: v for k, v in conf(self.root / 'config/pack_ledger.conf').items() if v})
                self.render(name, saved.get('poll_at') or config.get('LEDGER_SMS_POLL_AT')
                            or self.installed_schedule(name) or '21:40')
        else:
            sweep = saved.get('sweep') or {}
            if sweep.get('mode') == 'time':
                self.render(name, '%s %s' % (sweep['dow'], sweep['at']))

    def sync_slot(self, name, slot):
        # Domain instances may have neither this kit routine nor a generic
        # framework fallback. There is no scheduler surface to swap there.
        generic = self.root / 'routines' / (self.label('generic-brief-' + slot) + '.plist')
        if self.mac and not self.unit(name).is_file() and not generic.is_file():
            print('SKIP\tslot-sync:' + name + '\tno installed slot owner or generic fallback', flush=True)
            return
        self.run('/bin/bash', self.root / 'routines/lib/bundle-routine-ctl.sh', 'slot-sync', name)

    def reconcile(self):
        rows = []
        def read_bundle():
            for line in (self.root / 'service/kit_life/bundle.conf').read_text().splitlines():
                if not line.strip() or line.lstrip().startswith(('#', '@')):
                    continue
                parts = line.split('\t')
                if len(parts) >= 3 and parts[1] == 'routine':
                    rows.append((parts[0], parts[2], parts[5] if len(parts) >= 6 else '-'))
        self.attempt('bundle.conf', read_bundle)
        for name, default, slot in rows:
            self.attempt(name, lambda n=name, d=default, s=slot: self.bundle_routine(n, d, s))
        for name in ('ledger-sms-poll', 'ledger-sweep'):
            self.attempt(name, lambda n=name: self.ledger(n))
        # Exactly one slot-sync per declared slot owner, after all renders.
        for name, _, slot in rows:
            if slot != '-':
                self.attempt('slot-sync:' + name, lambda n=name, s=slot: self.sync_slot(n, s))
        self.attempt('criterion-reconcile', lambda: self.run(
            self.root / 'database/kit_life.sh', 'criterion-reconcile'))
        return int(self.failed)


if __name__ == '__main__':
    try:
        sys.exit(Reconciler(Path(sys.argv[1]).resolve()).reconcile())
    except (OSError, ValueError, IndexError) as exc:
        print('ERROR\tinstalled-artifacts\t%s' % exc, flush=True)
        sys.exit(1)
