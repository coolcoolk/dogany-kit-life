"""Shared closing-card and weekly reconciliation preferences."""
import datetime
import html
import json
import os
import plistlib
import re
from pathlib import Path

from . import paths, sources, text

DAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')
PREFIX = 'ledger.reconcile.'


def t(key, **kw):
    return text.t(PREFIX + key, **kw)


def _schedule(value):
    day, at = value.lower().split()
    if day not in DAYS or not re.fullmatch(r'[0-2][0-9]:[0-5][0-9]', at):
        raise ValueError('invalid weekly clock')
    datetime.time.fromisoformat(at)
    return day, at


def review_clock():
    """Read the installed weekly clock, saved clock, then the kit default."""
    root = paths.root()
    conf = {}
    paths._read_conf(root / '.instance.conf', conf)
    agent = conf.get('DOGANY_AGENT_NAME', root.name).strip('\"\'')
    label = 'com.telegram-skill-bot.%s.weekly-review' % agent
    la = Path(os.environ.get('ROUTINE_CTL_LA_DIR', str(Path.home() / 'Library/LaunchAgents')))
    units = Path(os.environ.get('ROUTINE_CTL_UNIT_DIR', str(Path.home() / '.config/systemd/user')))
    for path in (la / (label + '.plist'), root / 'routines' / (label + '.plist'),
                 units / ('dogany-%s-weekly-review.timer' % agent),
                 root / 'routines' / (label + '.timer')):
        if not path.is_file():
            continue
        if path.suffix == '.plist':
            with path.open('rb') as stream:
                clock = plistlib.load(stream)['StartCalendarInterval']
            if isinstance(clock, list):
                if len(clock) != 1:
                    raise ValueError('expected one weekly review clock')
                clock = clock[0]
            return _schedule('%s %02d:%02d' % (DAYS[(int(clock['Weekday']) - 1) % 7],
                                              clock['Hour'], clock['Minute']))
        match = re.search(r'^OnCalendar=(Sun|Mon|Tue|Wed|Thu|Fri|Sat)\s+\*-\*-\*\s+(\d{2}:\d{2}):00\s*$',
                          path.read_text(), re.M | re.I)
        if match:
            return _schedule('%s %s' % match.groups())
        raise ValueError('cannot read weekly review clock')
    saved = root / '.telegram_bot/kit-scheduler-clocks.json'
    if saved.is_file():
        value = json.loads(saved.read_text()).get('weekly-review')
        if value:
            return _schedule(value)
    for path in (root / 'service/kit_life/bundle.conf', paths.SERVICE_DIR.parent / 'bundle.conf'):
        if path.is_file():
            for line in path.read_text().splitlines():
                fields = line.split('\t')
                if fields[0] == 'weekly-review' and fields[1] == 'routine':
                    return _schedule(fields[2])
    raise ValueError('weekly review clock missing')


def completion_time(today=None):
    now = datetime.datetime.now()
    return datetime.datetime.combine(today or now.date(), now.time())


def time_label(at):
    hour, minute = map(int, at.split(':'))
    period = ('morning' if hour < 12 else 'afternoon' if hour < 18
              else 'evening' if hour < 21 else 'night')
    return t('clock.minute' if minute else 'clock.hour',
             period=t('period.' + period), hour=hour % 12 or 12, minute=minute)


def closing(state, completed=None):
    completed = completed or completion_time()
    sweep = state.get('sweep') or {}
    day, at = ((sweep['dow'], sweep['at']) if sweep.get('mode') == 'time' else review_clock())
    first = datetime.datetime.combine(completed.date(), datetime.time.fromisoformat(at))
    first += datetime.timedelta(days=(DAYS.index(day) - completed.weekday()) % 7)
    if first <= completed:
        first += datetime.timedelta(days=7)
    label = t('schedule.time' if sweep.get('mode') == 'time' else 'schedule.retro',
              day=text.dow(day), time=time_label(at))
    return text.t('ledger.onboarding.ready') + '\n' + t(
        'closing', schedule=label, date=t('date', month=first.month, day=first.day, dow=t('short.' + day)))


def say(message, today=None, *, spend_app=None, sweep=None):
    """Apply typed source/time preferences; message is evidence, never intent."""
    from . import onboarding as O
    if spend_app is None and sweep is None:
        return None
    if spend_app in sources.all_apps() and spend_app != 'other':
        spend_app = text.t('ledger.app.' + spend_app)
    # Preserve existing automation when changing just a reconciliation preference.
    state = O.load_state()
    if not state.get('completed_at') or state.get('flow'):
        raise ValueError('finish onboarding before changing reconciliation preferences')
    return O.run_apply(spend_app=spend_app, sweep=sweep, auto=state.get('auto') or [], today=today)
