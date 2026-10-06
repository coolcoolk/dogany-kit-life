"""Retro extension point (2.28.0, ledger pack gap G7).

The daily/weekly retro is kit-owned; modules shipped by packs (the ledger's
pending intake review, say) need one line in the stage-1 send and one section
inside the conversation without editing the kit's retro. This is the seam.

REGISTRY. Any provider declares rows in `service/<ns>/retro-sections.tsv`:

    <module_key> <TAB> <command>

`module_key` is a token from the kit manifest's module roster (e.g.
module_ledger). `command` is split with shlex; its first word is a path
relative to that provider's `service/<ns>/` directory and must stay inside
it. Blank lines and `#` comments are ignored. An empty registry prints
nothing. The kit's own one row (service/kit_life: module_core -> axis-lapsed,
the lapsed-axis lines of database/axis_state.py) is weekly-only, so a daily
retro without pack rows is byte-identical to 2.27.

CONTRACT for a provider command, invoked as
    <command> --lane daily|weekly --mode line|section
(also exported as KIT_RETRO_LANE / KIT_RETRO_MODE):
    exit 0 + stdout  -> shown (mode line: the first nonempty line only;
                        mode section: the first 3 nonempty lines, blank
                        lines dropped, the rest cut with one stderr line)
    exit 3           -> nothing to show tonight
A section is a glance, not a report: at most 3 lines, of which at most one
is a decision (a proposal the user answers), and that decision goes last.
The kit enforces the line cap; the one-decision rule is the provider's.
    anything else, a timeout (10 s) or no stdout -> skipped; one stderr line
A provider never fails the retro (fail-open), and it must not write the kit
database -- it reports, the retro conversation acts through kit verbs.

READ `kit_life.sh retro-sections --lane daily|weekly [--mode line|section]
[--json]`: rows in registry order (providers sorted by <ns>, then file order).
Text output: mode line = one line per provider; mode section = blocks
separated by one blank line. --json: [{"module","text"}]. No database access.

LANES (2.38.0, domain brief/retro standard). A row may carry a THIRD column,
a comma list of the lanes it serves:

    <module_key> <TAB> <command> <TAB> <lanes>

    lanes  daily | weekly | morning | grid   (any comma-joined subset)

A row without the column serves `daily,weekly` -- the 2.28 contract, so every
row shipped before 2.38 is called exactly as before and is never called on a
lane it did not declare (an old provider's argparse would refuse `morning`).
  morning  the morning brief: invoked `--lane morning --mode line|section`.
  grid     the weekly flow grid: invoked `--lane weekly --mode grid`. Output
           is DATA, not prose: up to 7 rows `YYYY-MM-DD<TAB><bar number>
           <TAB><shown value><TAB><note>`, optionally preceded by one
           `unit<TAB><what the bar measures>` row. The kit draws the grid
           (routines/bundle/life_dashboard.py), never the provider. The first
           provider with rows wins; one grid per message.

ROLE (2.38.0). `--role domain` drops the rows the kit itself ships
(service/kit_life): the kit-native lines are life lines and run on the main
agent only, so a domain agent never repeats them. `--skip-module <key>`
(repeatable) drops rows of a module another crew member owns -- the composers
pass what `kit_life.sh scope-self` prints as skip_modules.
"""
import argparse
import glob
import json
import os
import shlex
import subprocess
import sys

TIMEOUT_SEC = 10
NOTHING = 3
SECTION_MAX_LINES = 3
GRID_MAX_ROWS = 7
LANES = ('daily', 'weekly', 'morning', 'grid')
LEGACY_LANES = ('daily', 'weekly')
KIT_NS = 'kit_life'


def service_dir(script_dir):
    return os.environ.get('KIT_LIFE_SERVICE_DIR') or os.path.join(
        os.path.dirname(script_dir), 'service')


def rows(root):
    """[(module_key, argv, provider_dir, lanes)] in deterministic order."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, '*', 'retro-sections.tsv'))):
        base = os.path.realpath(os.path.dirname(path))
        try:
            with open(path, encoding='utf-8') as fh:
                lines = fh.read().splitlines()
        except OSError as e:
            print('[retro-sections] unreadable %s: %s' % (path, e), file=sys.stderr)
            continue
        for n, line in enumerate(lines, 1):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            parts = line.split('\t', 2)
            if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
                print('[retro-sections] %s:%d: expected <module>\\t<command>' % (path, n), file=sys.stderr)
                continue
            lanes = LEGACY_LANES
            if len(parts) == 3 and parts[2].strip():
                lanes = tuple(x.strip() for x in parts[2].split(',') if x.strip())
                bad = [x for x in lanes if x not in LANES]
                if bad or not lanes:
                    print('[retro-sections] %s:%d: unknown lane %s' % (path, n, ','.join(bad)),
                          file=sys.stderr)
                    continue
            try:
                argv = shlex.split(parts[1])
            except ValueError as e:
                print('[retro-sections] %s:%d: %s' % (path, n, e), file=sys.stderr)
                continue
            exe = os.path.realpath(os.path.join(base, argv[0]))
            if os.path.isabs(argv[0]) or not exe.startswith(base + os.sep):
                print('[retro-sections] %s:%d: command must stay inside %s' % (path, n, base), file=sys.stderr)
                continue
            out.append((parts[0].strip(), [exe] + argv[1:], base, lanes))
    return out


def registry(root):
    """[(module_key, argv, provider_dir)] in deterministic order (2.28 shape)."""
    return [(k, argv, base) for k, argv, base, _lanes in rows(root)]


def _selected(root, lane, role, skip):
    want = 'grid' if lane == 'grid' else lane
    for key, argv, base, lanes in rows(root):
        if want not in lanes:
            continue
        if role == 'domain' and os.path.basename(base) == KIT_NS:
            continue
        if key in skip:
            continue
        yield key, argv, base


def _call(key, argv, base, lane, mode):
    """(returncode, text) or None when skipped (already reported)."""
    env = dict(os.environ, KIT_RETRO_LANE=lane, KIT_RETRO_MODE=mode)
    try:
        p = subprocess.run(argv + ['--lane', lane, '--mode', mode], cwd=base, env=env,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=TIMEOUT_SEC)
    except (OSError, subprocess.TimeoutExpired) as e:
        print('[retro-sections] %s skipped: %s' % (key, e.__class__.__name__), file=sys.stderr)
        return None
    if p.returncode == NOTHING:
        return None
    text = p.stdout.decode('utf-8', 'replace').strip('\n')
    if p.returncode != 0 or not text.strip():
        print('[retro-sections] %s skipped: exit %d%s' % (
            key, p.returncode, '' if text.strip() else ', no output'), file=sys.stderr)
        return None
    return text


def run(root, lane, mode, role='main', skip=()):
    shown = []
    for key, argv, base in _selected(root, lane, role, skip):
        text = _call(key, argv, base, lane, mode)
        if text is None:
            continue
        body = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        if mode == 'line':
            text = body[0].strip()
        else:
            text = '\n'.join(body[:SECTION_MAX_LINES])
            if len(body) > SECTION_MAX_LINES:
                print('[retro-sections] %s: %d lines cut to %d' % (
                    key, len(body), SECTION_MAX_LINES), file=sys.stderr)
        shown.append({'module': key, 'text': text})
    return shown


def grid(root, role='main', skip=()):
    """The weekly flow grid: {'module', 'unit', 'rows': [[date, bar, shown,
    note]]} from the first `grid` provider with valid rows, else None."""
    for key, argv, base in _selected(root, 'grid', role, skip):
        text = _call(key, argv, base, 'weekly', 'grid')
        if text is None:
            continue
        unit, out = '', []
        for ln in text.splitlines():
            c = ln.rstrip('\n').split('\t')
            if c and c[0] == 'unit' and len(c) >= 2 and not out:
                unit = c[1].strip()
                continue
            if len(c) < 3 or len(c[0]) != 10:
                continue
            try:
                float(c[1])
            except ValueError:
                continue
            out.append([c[0], c[1], c[2], c[3] if len(c) > 3 else ''])
        if not out:
            print('[retro-sections] %s skipped: no grid rows' % key, file=sys.stderr)
            continue
        if len(out) > GRID_MAX_ROWS:
            print('[retro-sections] %s: %d grid rows cut to %d' % (
                key, len(out), GRID_MAX_ROWS), file=sys.stderr)
        return {'module': key, 'unit': unit, 'rows': out[:GRID_MAX_ROWS]}
    return None


def cli(script_dir, argv):
    p = argparse.ArgumentParser(prog='kit_life.sh retro-sections')
    p.add_argument('--lane', choices=('daily', 'weekly', 'morning'), required=True)
    p.add_argument('--mode', choices=('line', 'section', 'grid'), default='line')
    p.add_argument('--role', choices=('main', 'domain'), default='main')
    p.add_argument('--skip-module', action='append', default=[])
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    skip = tuple(x for v in a.skip_module for x in v.split(',') if x)
    root = service_dir(script_dir)
    if a.mode == 'grid':
        if a.lane != 'weekly':
            p.error('--mode grid is weekly only')
        g = grid(root, a.role, skip)
        if a.json:
            print(json.dumps(g, ensure_ascii=True))
        elif g:
            if g['unit']:
                print('unit\t%s' % g['unit'])
            for r in g['rows']:
                print('\t'.join(r))
        return
    shown = run(root, a.lane, a.mode, a.role, skip)
    if a.json:
        print(json.dumps(shown, ensure_ascii=True))
    elif shown:
        print(('\n' if a.mode == 'line' else '\n\n').join(s['text'] for s in shown))
