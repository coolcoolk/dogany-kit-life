#!/usr/bin/env python3
"""Kit-owned five-minute schedule for the framework's routines/mirror-poll.sh.

enable | disable | status | reconcile (default, also the lifecycle hook).
Uses day-card-ctl's ROUTINE_CTL_OS/LA_DIR/UNIT_DIR/NO_LOAD/LAUNCHCTL seams.
The historical com.telegram-skill-bot.<agent>.mirror-poll name is adopted on
both platforms: no second job is installed alongside a framework-era unit.
"""
import fcntl
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys


def conf(path):
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            fields = shlex.split(line, comments=True)
            if fields[:1] == ['export']:
                fields = fields[1:]
            if len(fields) == 1 and '=' in fields[0]:
                key, value = fields[0].split('=', 1)
                values.setdefault(key, value)
    return values


class Scheduler:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.home = str(Path.home())
        agent = conf(self.root / '.instance.conf').get('DOGANY_AGENT_NAME', self.root.name)
        self.label = 'com.telegram-skill-bot.%s.mirror-poll' % agent
        self.mac = os.environ.get('ROUTINE_CTL_OS', os.uname().sysname) == 'Darwin'
        self.no_load = os.environ.get('ROUTINE_CTL_NO_LOAD') == '1'
        self.la = Path(os.environ.get('ROUTINE_CTL_LA_DIR', self.home + '/Library/LaunchAgents'))
        self.units = Path(os.environ.get('ROUTINE_CTL_UNIT_DIR', self.home + '/.config/systemd/user'))
        self.dest = (self.la / (self.label + '.plist') if self.mac else
                     self.units / (self.label + '.timer'))
        self.service = self.units / (self.label + '.service')
        self.legacy = self.root / 'routines' / self.dest.name
        self.engine = self.root / 'routines/mirror-poll.sh'
        self.domain = 'gui/%s' % os.getuid()
        self.transport_path = self.root / '.telegram_bot/mirror-poll-transport'
        self.transport = None

    def run(self, *args, check=True):
        return subprocess.run(args, check=check, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30).returncode

    def launchctl(self, *args, **kwargs):
        return self.run(os.environ.get('ROUTINE_CTL_LAUNCHCTL', 'launchctl'), *args, **kwargs)

    def status(self):
        if self.no_load:
            return self.dest.is_file()
        if self.mac:
            if self.launchctl('print', self.domain + '/' + self.label, check=False) == 0:
                return True
            result = subprocess.run([os.environ.get('ROUTINE_CTL_LAUNCHCTL', 'launchctl'), 'list'],
                                    capture_output=True, text=True, timeout=30)
            return any(line.split()[-1:] == [self.label] for line in result.stdout.splitlines())
        return self.run('systemctl', '--user', 'is-enabled', self.dest.name, check=False) == 0

    def artifacts(self):
        paths = [self.dest, self.legacy]
        if not self.mac:
            paths += [self.service, self.legacy.with_suffix('.service')]
        return paths

    def desired(self):
        pin = conf(self.root / 'config/agent.conf').get('LIFE_ENGINE')
        paths = [self.root / ('config/%s.conf' % stem)
                 for stem in ([pin] if pin else ['kit_life', 'lifekit'])]
        cfg = next((conf(path) for path in paths if path.is_file()), {})
        if cfg.get('LIFEKIT') != 'on' or cfg.get('MIRROR_MODULE') != 'on':
            return False
        # The legacy transport's disconnect switches MIRROR_MODULE off. Its
        # credentials are not in service.calendar; retain that supported path.
        mode = os.environ.get('CALENDAR_TRANSPORT', cfg.get('CALENDAR_TRANSPORT', 'auto'))
        if mode == 'gws-cli':
            self.transport = 'gws-cli'
            return True
        # Framework status reads local credentials only; no network or token
        # refresh. Do not let a machine-wide Google connect enable a kit whose
        # own mirror has never been enabled. Health pauses keep the timer so
        # recovery can run; an explicit credential disconnect removes it.
        sys.path[:0] = [str(self.root), str(self.root / '.dogany-consume')]
        from service import calendar
        status = calendar.status(str(self.root))
        if status.get('connected'):
            self.transport = 'compat'
            return True
        if (status.get('class') not in (None, 'needs_consent', 'not_connected') or
                status.get('health') not in (None, 'needs_consent')):
            raise ValueError('calendar status unavailable; keeping existing schedule')
        # Preserve auto's existing gws fallback for older installations. Once
        # this instance used compat, deleting its credentials is a disconnect,
        # not permission to silently start using another credential store.
        previous = self.transport_path.read_text().strip() if self.transport_path.is_file() else ''
        if mode == 'auto' and previous != 'compat':
            override = os.environ.get('GWS_BIN')
            candidates = ([override] if override else [shutil.which('gws') or '',
                          self.home + '/.npm-global/bin/gws', self.home + '/.local/bin/gws',
                          '/opt/homebrew/bin/gws', '/usr/local/bin/gws'])
            if any(path and (os.access(path, os.X_OK) or shutil.which(path)) for path in candidates):
                self.transport = 'gws-cli'
                return True
        return False

    def content(self):
        path = self.home + '/.local/bin:' + self.home + '/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
        if self.mac:
            return {self.dest: plistlib.dumps({
                'Label': self.label,
                'ProgramArguments': ['/bin/bash', str(self.engine)],
                'StartInterval': 300, 'RunAtLoad': False,
                'WorkingDirectory': str(self.root),
                'EnvironmentVariables': {'HOME': self.home, 'PATH': path},
                'StandardOutPath': str(self.root / '.telegram_bot/logs/mirror-poll.stdout.log'),
                'StandardErrorPath': str(self.root / '.telegram_bot/logs/mirror-poll.stderr.log'),
            })}
        # systemd's quoting differs from a shell. Quote paths and escape its
        # percent specifiers and ExecStart's environment expansion explicitly.
        def quote(value, exec_start=False):
            value = str(value).replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"')
            if exec_start:
                value = value.replace('$', '$$')
            return '"' + value + '"'
        service = ('[Unit]\nDescription=dogany mirror poll\n\n[Service]\nType=oneshot\n'
                   'ExecStart=/bin/bash %s\nWorkingDirectory=%s\nEnvironment=%s\nEnvironment=%s\n' % (
                       quote(self.engine, True), quote(self.root), quote('HOME=' + self.home), quote('PATH=' + path)))
        timer = ('[Unit]\nDescription=dogany mirror poll timer\n\n[Timer]\n'
                 'OnBootSec=1min\nOnUnitActiveSec=300s\nAccuracySec=1s\n\n[Install]\nWantedBy=timers.target\n')
        return {self.service: service.encode(), self.dest: timer.encode()}

    def enable(self):
        if not self.engine.is_file():
            raise ValueError('framework engine missing: %s' % self.engine)
        content = self.content()
        changed = any(not path.is_file() or path.read_bytes() != body for path, body in content.items())
        if not changed and self.status():
            return
        (self.root / '.telegram_bot/logs').mkdir(parents=True, exist_ok=True)
        for path, body in content.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + '.tmp')
            temp.write_bytes(body)
            temp.replace(path)
        if self.mac:
            if self.no_load:
                return
            self.launchctl('bootout', self.domain + '/' + self.label, check=False)
            # Older framework jobs were loaded from routines/, under this
            # exact label. Booting it out before bootstrap adopts that job.
            if self.launchctl('bootstrap', self.domain, str(self.dest), check=False):
                self.launchctl('load', str(self.dest), check=False)
        elif not self.no_load:
            self.run('systemctl', '--user', 'daemon-reload')
            self.run('systemctl', '--user', 'enable', '--now', self.dest.name)
        if not self.status():
            raise ValueError('could not verify scheduled: %s' % self.label)

    def disable(self):
        if not any(path.exists() for path in self.artifacts()):
            return
        if not self.no_load:
            if self.mac:
                self.launchctl('bootout', self.domain + '/' + self.label, check=False)
                # Compatibility with agents loaded using launchctl load.
                for path in (self.dest, self.legacy):
                    if path.exists():
                        self.launchctl('unload', str(path), check=False)
            else:
                self.run('systemctl', '--user', 'disable', '--now', self.dest.name)
            if self.status():
                raise ValueError('could not verify unscheduled: %s' % self.label)
        for path in self.artifacts():
            path.unlink(missing_ok=True)
        if not self.no_load:
            if not self.mac:
                self.run('systemctl', '--user', 'daemon-reload')

    def apply(self, command):
        if command == 'status':
            return 0 if self.status() else 3
        desired = self.desired() if command == 'reconcile' else command == 'enable'
        directory = self.root / '.telegram_bot'
        lock_path = directory / 'mirror-poll-schedule.lock'
        # If an enable already holds the lock, wait even when its unit has
        # not landed yet: an overlapping off must remove that pending unit.
        if not desired and not any(path.exists() for path in self.artifacts()) and not lock_path.exists():
            return 0
        directory.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            # Re-read after the lock: overlapping post-tool hooks must not
            # resurrect a schedule after a later off/disconnect.
            if command == 'reconcile':
                desired = self.desired()
            (self.enable if desired else self.disable)()
            if desired and self.transport:
                if not self.transport_path.is_file() or self.transport_path.read_text().strip() != self.transport:
                    temp = self.transport_path.with_suffix('.tmp')
                    temp.write_text(self.transport + '\n')
                    temp.replace(self.transport_path)
        return 0


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'reconcile'
    if command not in ('enable', 'disable', 'status', 'reconcile'):
        print('usage: mirror-poll-ctl.py enable|disable|status|reconcile', file=sys.stderr)
        return 1
    try:
        return Scheduler(Path(__file__).resolve().parents[2]).apply(command)
    except (OSError, ValueError, ImportError, subprocess.SubprocessError) as exc:
        print('[mirror-poll-ctl] %s' % exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
