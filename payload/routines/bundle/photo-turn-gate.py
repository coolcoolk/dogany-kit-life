#!/usr/bin/env python3
"""photo-turn-gate.py -- UserPromptSubmit hook for "photo while today's
tasks are open (semantic evidence)" (declared in the kit's pack-manifest.json
hooks[], wired by pack_install.sh -- DGN-1496).

The owner's rule: any image evidence that clearly establishes TODAY's
completion of exactly one listed task settles it in the photo turn
(task-done-today --photo-evidence); two or more fulfilled rows need one done-button question; no match follows
the common activity-capture contract without mentioning unrelated tasks. The
kit's code gate
(kit_life.py PHOTO_GATE_KEY) holds that rule, but it is armed by
`task-open-today --photo`, a call the agent has to remember. In agent
rehearsal S3 (2026-10-11) the agent often never loaded schedule-log: it
listed today's tasks without --photo and settled with `task-done <id>`
(or an invented --said), so the gate never armed.

So this hook arms it. When the prompt is a bridge photo turn (it names an
image under .telegram_bot/images/) it runs the kit's own
`database/kit_life.sh task-open-today --photo`: today's open rows are armed
for THIS turn only (the gate records the turn's request_id; the owner's
later turns settle on the plain chat path -- incident 2026-10-13, a meal
photo held the workout task for 3h). The owner's own caption of the photo
(the bridge's "User caption: ..." line) rides --caption, so a --said in the
photo turn that quotes it and names the task passes; any other --said in
this turn is refused. When rows are open it prints one line naming
them, the evidence path and the one-tap path; today's already-done rows get
a second line (answer the activity, no already-done announcement). With neither, remind the agent
to capture a real activity at its evidence time. Copy: i18n
keys task.photo.hook.armed / task.photo.hook.done in
service/kit_life/i18n/<lang>.json ({rows}).

CONTRACT (fail-open, always): any error, timeout or missing kit prints
nothing and exits 0. Writes nothing itself; the kit verb writes gate arming
or an open session's photo-turn identity (no task is settled, moved or created).

Open workout sessions take precedence: the verb returns workout-session
routing without arming completion. Print only that handoff, even when the
day also has matching open/done tasks. Both legacy and v2 are read by the kit. Started task windows use the same
priority gate and take precedence over generic completion and meal capture.
"""
import json
import os
import re
import subprocess
import sys

KEY = 'task.photo.hook.armed'
DONE_KEY = 'task.photo.hook.done'
EMPTY_KEY = 'task.photo.hook.no_tasks'
SESSION_KEY = 'task.photo.hook.session_open'
TIMEOUT_S = 20
_PHOTO_RE = re.compile(r'[/\\]\.telegram_bot[/\\]images[/\\]\S+')
# The bridge's caption line (bridge i18n user_caption, en / ko).
_CAPTION_RE = re.compile('^(?:User caption|\uC0AC\uC6A9\uC790 \uCEA1\uC158): ',
                         re.MULTILINE)


def root():
    env = os.environ.get('DOGANY_INSTANCE_ROOT')
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lang(r):
    val = os.environ.get('AGENT_LANG')
    if not val:
        try:
            with open(os.path.join(r, 'config', 'agent.conf'), encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    if line.startswith('AGENT_LANG='):
                        val = line.split('=', 1)[1].strip().strip('"\'')
                        break
        except OSError:
            pass
    return (val or 'ko').lower().split('_')[0].split('-')[0] or 'ko'


def copy(r, key=KEY):
    for code in (lang(r), 'ko'):
        try:
            with open(os.path.join(r, 'service', 'kit_life', 'i18n', code + '.json'),
                      encoding='utf-8') as fh:
                s = json.load(fh).get(key)
            if s:
                return s
        except (OSError, ValueError):
            continue
    return None


def rows_text(rows):
    out = []
    for r in rows:
        hm = (r.get('start_local') or '')[11:16]
        out.append(' '.join(x for x in ('#%s' % r.get('id'), r.get('title') or '', hm) if x))
    return ', '.join(out)


def caption_of(prompt):
    """The owner's own caption of the photo (everything after the bridge's
    last caption marker), or None."""
    hits = list(_CAPTION_RE.finditer(prompt))
    text = prompt[hits[-1].end():].strip() if hits else ''
    return text or None


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = (payload.get('prompt') or '') if isinstance(payload, dict) else ''
    if not _PHOTO_RE.search(prompt):
        return 0
    r = root()
    kit = os.path.join(r, 'database', 'kit_life.sh')
    if not os.path.isfile(kit):
        return 0
    argv = [kit, 'task-open-today', '--photo']
    caption = caption_of(prompt)
    if caption:
        argv += ['--caption', caption]
    p = subprocess.run(argv, cwd=r, capture_output=True,
                       text=True, timeout=TIMEOUT_S,
                       env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    if p.returncode != 0:
        return 0
    out = json.loads(p.stdout.strip().splitlines()[-1])
    if out.get('route') == 'cooking-session':
        print('[cooking-session] ' + out['next'])
        return 0
    if out.get('route') == 'workout-session':
        print(copy(r, SESSION_KEY) or out['next'])
        return 0
    if out.get('route') == 'task-activity':
        line = copy(r, 'task.photo.hook.activity_open') or out['next']
        print(line.format(rows=rows_text(out.get('activities', []))))
        return 0
    for key, rows in ((KEY, out.get('open')), (DONE_KEY, out.get('done'))):
        line = copy(r, key) if rows else None
        if line:
            print(line.format(rows=rows_text(rows)))
    if not out.get('open') and not out.get('done'):
        line = copy(r, EMPTY_KEY)
        if line:
            print(line)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
