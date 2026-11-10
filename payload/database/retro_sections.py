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
                        mode section: up to 3 nonempty prose lines plus
                        12 quoted content lines; blank lines outside code dropped,
                        overflow cut with one stderr line)
    exit 3           -> nothing to show tonight
A section is a glance, not a report: at most 3 prose lines, of which at
most one is a decision (a proposal the user answers), and it goes last.
Markdown > rows and HTML <blockquote> content share a separate 12-line
allowance. HTML items and fenced cards are atomic: an item crossing the allowance is
dropped whole, never cut inside its tags. Wrapper-only lines cost nothing.
Providers emit one HTML block per item, joined without blank lines.
Fenced cards retain their internal blank lines and ignore HTML tags in code.
The kit enforces the line cap; the one-decision rule is the provider's.
    anything else, a timeout (10 s) or no stdout -> skipped; one stderr line
A provider never fails the retro (fail-open), and it must not write the kit
database -- it reports, the retro conversation acts through kit verbs.

READ `kit_life.sh retro-sections --lane daily|weekly [--mode line|section]
[--json]`: rows in registry order (providers sorted by <ns>, then file order).
Text output: both modes join providers with one newline, no blank separator.
--json: [{"module","text"}]. No database access.

IDENTITY-BEARING DAILY LINES. A line provider may emit one JSON object:
    {"items": [{"id": "15855", "kind": "task", "text": "...",
                "signal": "Waiting plan step", "age_days": 10,
                "age_clock": "step.updated_at/local-date"}]}
Kinds are task/event/module. task/event ids are block ids, not titles. `text`
is one display line per item; `signal` is age-free merge copy. --json retains
items with module provenance; plain output joins all explicit item texts.
The daily composer merges matching (kind,id) into displayed card rows and
keeps the card row's age; it emits every unmatched item in the ordered walk.
Legacy providers keep their original first-line behavior and receive a stable
module/text-derived walk identity. Never guess their block identity by title.

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
           provider with rows wins. An optional diet<TAB>JSON row adds the
           weekly diet grid below that provider's first prose section. Its
           schema is validated by _diet_grid; diet alone can activate the
           card. Legacy grids without this row keep their existing shape.

SECTION LIMIT. An optional FOURTH column sets the maximum number of
nonempty section lines for that row, an integer from 1 through 40:

    module_health <TAB> session-retro --part meal <TAB> daily <TAB> 40

Missing or empty limits keep the default 3 prose + 12 quote allowance;
explicit limits count all nonempty lines, retaining atomic HTML items.
Invalid limits skip the row with one stderr line. Line mode still shows only
the first line, and the grid limit stays 7 rows. Lanes may be empty to retain daily,weekly. A pre-limit reader
includes the fourth column in its lane value, rejects it as an unknown lane
and skips the row safely, rather than showing a truncated extended section.

ROLE (2.38.0). `--role domain` drops the rows the kit itself ships
(service/kit_life): the kit-native lines are life lines and run on the main
agent only, so a domain agent never repeats them. `--skip-module <key>`
(repeatable) drops rows of a module another crew member owns -- the composers
pass what `kit_life.sh scope-self` prints as skip_modules.

CHILD RATING (retro batch, owner 2026-10-09 21:37). A module that rates its
own things (dining / place taste) may add ONE inline button row to a nested
retro item, in the same message as the item's one question -- never a turn
of its own. Registry `service/<ns>/retro-child-rating.tsv`:

    <module_key> <TAB> <command>

invoked as `<command> --lane daily --mode child-rating` with the item's
children as a JSON list on stdin ({id, ulid, kind, title, name, place_id,
place_name, source}). It prints at most one line

    <child id> <TAB> <label>|<label>|<label> [<TAB> <record command>]

(2-4 labels) for a child it owns, or exits 3. The optional record command
is what the conversation runs when a label is tapped, `{label}` standing
for the tapped label (e.g. `kit_life.sh dining-rate 31 {label}`). The first provider with a
valid line wins; one row per item. Fail-open exactly like a section.

ATTACH (kit_life 2.46.0-dev.10, owner 2026-10-10 21:14). A row whose lanes
column carries the token `attach` is an ATTACHMENT part of the card, not a
line of it:

    <module_key> <TAB> <command> <TAB> daily,attach

It is never called in line / section / grid mode, and only it is called as
`<command> --lane <lane> --mode attach` (KIT_RETRO_MODE=attach), after the
text card is composed and only when the card goes out (an attachment never
makes a send on its own). `attach` needs at least one real lane beside it;
today only the daily domain card (routines/bundle/daily-retro.sh) asks. A
pre-dev.10 reader takes `attach` as an unknown lane and skips the row with
one stderr line, so a pack can ship the flag before the kit has it. Output
is DATA, one row per line:

    photo   <TAB> <absolute path> [<TAB> <caption>]
        an existing regular file, sent as a photo message AFTER the card;
        caption optional, cut to 200 chars. At most 3 photos per card.
    buttons <TAB> <json>
        inline-keyboard rows [[{"text", "callback_data"}, ...], ...] put ON
        the card message itself (push.sh --strict --buttons-json). Rows of
        every provider merge in registry order, at most 3 rows of at most 4
        buttons. callback_data must be `opt:<label>` (<= 64 bytes): the
        bridge's opt: route -- the same one the retro start button rides --
        replays the tapped label as the owner's next message into the agent
        that sent the card. On a domain card that agent owns the module
        (--role domain / --skip-module drop everyone else's rows), so the tap
        reaches the owning module's skill as the owner's words; the module's
        skill must recognise its own labels. No second callback route.
    ask     <TAB> <json>                                   (retro-walk card, owner 2026-10-30)
        ONE question the owner answers in its own message AFTER the card, with
        its own keyboard (the card itself carries no question line and no
        button for it). json = {"id": "<token>", "text": "<question>",
        "choices": [{"label", "cmd"?, "then"?}, ...]}. 1-4 choices, distinct
        labels (<= 60 bytes, no newline); `cmd` is run (provider dir as cwd,
        first word inside it) when that label is answered, `then` is
        `advance` (default: the next question follows) or `talk` (the owner
        still has to say something; the relaying agent finishes and calls
        retro-walk resume). A consumer that posts asks (today only the domain
        daily card, routines/bundle/daily-retro.sh) exports KIT_RETRO_ASK_SPLIT=1
        around its retro-sections calls, which the providers inherit; every
        other caller (main-role cards, weekly, morning) leaves it unset and
        sees the old inline output byte for byte: a provider that sees it keeps its question line (and
        any `[[OPTIONS: ...]]` marker) OUT of section text and out of the
        `buttons` row, and emits the question as an `ask` row instead. A
        pre-dev.73 kit ignores the `ask` row (unknown kind) and never sets
        the variable, so the provider keeps the old inline question.
        At most 8 asks per card, in registry order. A bad row is dropped
        alone with one stderr line. --json adds "asks" only when non-empty.
        ASK ROW CONTRACT v2 (owner 2026-10-31 06:13): an ask may carry
        `"recheck": "<provider command>"` (same cwd rules as `cmd`). The walk
        runs it right BEFORE posting that question, after every earlier answer
        has been recorded, so a question whose premise an earlier answer
        changed is re-evaluated, not posted from compose-time numbers. Its
        stdout: empty = the question stands as composed; `drop` = no longer
        needed, skipped (result `skipped`), the walk goes on; an
        `ask<TAB>json` row = post this recomputed text/choices instead (the id
        stays). A failing / silent / timed-out recheck leaves the question as
        composed (fail-open). A provider emits `recheck` only when the kit
        exports KIT_RETRO_ASK_RECHECK=1 (today only routines/bundle/
        daily-retro.sh, beside KIT_RETRO_ASK_SPLIT=1); a v1 kit rejects the
        unknown key and would drop the row, so it never sets the variable.
Isolation is per part: a missing file drops that photo, bad json drops that
buttons row, a failing / silent / timed-out command drops that row's parts;
the card always goes out. Unknown row kinds are ignored with one stderr line.
`kit_life.sh retro-sections --lane daily --mode attach [--json]` prints the
validated set: `photo<TAB>path<TAB>caption` lines, then ONE merged
`buttons<TAB><json>` line (--json: {"photos": [...], "buttons": [...]}).
"""
import argparse
import datetime
import glob
import json
import math
import os
import re
import shlex
import subprocess
import sys

TIMEOUT_SEC = 10
NOTHING = 3
SECTION_MAX_LINES = 3
SECTION_HARD_MAX_LINES = 40
QUOTE_MAX_LINES = 12
GRID_MAX_ROWS = 7
LANES = ('daily', 'weekly', 'morning', 'grid')
ATTACH = 'attach'
ASK_MAX = 8
ASK_CHOICES_MAX = 4
ASK_LABEL_MAX_BYTES = 60
ASK_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
PHOTO_MAX = 3
CAPTION_MAX = 200
BUTTON_ROWS_MAX = 3
BUTTONS_PER_ROW_MAX = 4
CALLBACK_PREFIX = 'opt:'
CALLBACK_MAX_BYTES = 64
LEGACY_LANES = ('daily', 'weekly')
KIT_NS = 'kit_life'


def service_dir(script_dir):
    return os.environ.get('KIT_LIFE_SERVICE_DIR') or os.path.join(
        os.path.dirname(script_dir), 'service')


RATING_FILE = 'retro-child-rating.tsv'
RATING_LABELS = (2, 4)


def _rows(root, fname='retro-sections.tsv'):
    """Registry rows with optional section limits, in deterministic order."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, '*', fname))):
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
            parts = line.split('\t')
            if not 2 <= len(parts) <= 4 or not parts[0].strip() or not parts[1].strip():
                print('[retro-sections] %s:%d: expected <module>\\t<command>' % (path, n), file=sys.stderr)
                continue
            lanes = LEGACY_LANES
            if len(parts) >= 3 and parts[2].strip():
                lanes = tuple(x.strip() for x in parts[2].split(',') if x.strip())
                bad = [x for x in lanes if x not in LANES + (ATTACH,)]
                if ATTACH in lanes and not bad and not [x for x in lanes if x in LANES[:3]]:
                    print('[retro-sections] %s:%d: attach needs a lane (daily|weekly|morning)'
                          % (path, n), file=sys.stderr)
                    continue
                if bad or not lanes:
                    print('[retro-sections] %s:%d: unknown lane %s' % (path, n, ','.join(bad)),
                          file=sys.stderr)
                    continue
            section_max = None
            if len(parts) == 4 and parts[3].strip():
                try:
                    section_max = int(parts[3].strip())
                    if not 1 <= section_max <= SECTION_HARD_MAX_LINES:
                        raise ValueError
                except ValueError:
                    print('[retro-sections] %s:%d: section limit must be an integer from 1 to %d' % (
                        path, n, SECTION_HARD_MAX_LINES), file=sys.stderr)
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
            out.append((parts[0].strip(), [exe] + argv[1:], base, lanes, section_max))
    return out


def rows(root, fname='retro-sections.tsv'):
    """[(module_key, argv, provider_dir, lanes)] (2.38 shape)."""
    return [(k, argv, base, lanes) for k, argv, base, lanes, _limit in _rows(root, fname)]


def registry(root):
    """[(module_key, argv, provider_dir)] in deterministic order (2.28 shape)."""
    return [(k, argv, base) for k, argv, base, _lanes in rows(root)]


def _selected(root, lane, role, skip, attach=False):
    want = 'grid' if lane == 'grid' else lane
    for key, argv, base, lanes, section_max in _rows(root):
        if want not in lanes or (ATTACH in lanes) != attach:
            continue
        if (role == 'domain' and os.path.basename(base) == KIT_NS
                and os.path.basename(argv[0]) != 'regimen-retro'):
            continue
        if key in skip:
            continue
        yield key, argv, base, section_max


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


def child_rating(root, children):
    """{'module', 'child_id', 'name', 'labels'} from the first rating
    provider that claims one of `children`, else None. Never raises."""
    if not children:
        return None
    by_id = {str(c.get('id')): c for c in children}
    payload = json.dumps(children, ensure_ascii=True).encode('utf-8')
    for key, argv, base, _lanes in rows(root, RATING_FILE):
        env = dict(os.environ, KIT_RETRO_LANE='daily', KIT_RETRO_MODE='child-rating')
        try:
            p = subprocess.run(argv + ['--lane', 'daily', '--mode', 'child-rating'],
                               cwd=base, env=env, input=payload,
                               capture_output=True, timeout=TIMEOUT_SEC)
        except (OSError, subprocess.TimeoutExpired) as e:
            print('[retro-rating] %s skipped: %s' % (key, e.__class__.__name__), file=sys.stderr)
            continue
        if p.returncode == NOTHING:
            continue
        if p.returncode != 0:
            print('[retro-rating] %s skipped: exit %d' % (key, p.returncode), file=sys.stderr)
            continue
        for ln in p.stdout.decode('utf-8', 'replace').splitlines():
            c = ln.split('\t')
            if len(c) < 2 or c[0].strip() not in by_id:
                continue
            labels = [x.strip() for x in c[1].split('|') if x.strip()]
            if not RATING_LABELS[0] <= len(labels) <= RATING_LABELS[1]:
                continue
            child = by_id[c[0].strip()]
            return {'module': key, 'child_id': child.get('id'),
                    'name': child.get('name') or child.get('title') or '',
                    'labels': labels,
                    'record': c[2].strip() if len(c) > 2 else ''}
        print('[retro-rating] %s skipped: no valid row' % key, file=sys.stderr)
    return None


# Recognize structural tags only; escaped/defused provider data is ordinary text.
_QUOTE_TAG = re.compile(r'<(/?)blockquote\b[^>]*>', re.IGNORECASE)


def _section_units(text):
    """Keep a fenced card verbatim and atomic, including its blank lines.

    Quote tags inside code are data. A section cap drops the whole card
    rather than delivering an open fence or half of a nutrition block.
    """
    prose, code = [], None
    for line in text.splitlines():
        fence = line.startswith('```')
        if code is not None:
            code.append(line)
            if fence:
                yield '\n'.join(code), max(1, sum(bool(ln.strip()) for ln in code[1:-1]))
                code = None
        elif fence:
            yield from _html_section_units('\n'.join(prose))
            prose = []
            code = [line]
        else:
            prose.append(line)
    if code is not None:
        yield '\n'.join(code), None
    else:
        yield from _html_section_units('\n'.join(prose))


def _html_section_units(text):
    """Yield (text, quote-line cost): prose=0, malformed=None.

    Buffer HTML until its closing tag so caps cannot leave dangling markup.
    Each nonempty content line costs one, regardless of wrapper placement;
    even an empty block costs one. Nested/unclosed blocks are not valid items.
    """
    def plain(fragment):
        for line in fragment.splitlines():
            if line.strip():
                yield line.rstrip(), int(line.lstrip().startswith('>'))

    cursor, start, depth, nested = 0, 0, 0, False
    for tag in _QUOTE_TAG.finditer(text):
        if not depth:
            yield from plain(text[cursor:tag.start()])
            start = tag.start()
            nested = False
        if tag.group(1):
            if not depth:
                yield tag.group(), None
                cursor = tag.end()
                continue
            depth -= 1
            if not depth:
                block = text[start:tag.end()]
                content = _QUOTE_TAG.sub('', block)
                cost = max(1, sum(bool(line.strip()) for line in content.splitlines()))
                yield block, None if nested else cost
                cursor = tag.end()
        else:
            nested = nested or depth > 0
            depth += 1
    if depth:
        yield text[start:], None
    else:
        yield from plain(text[cursor:])


def run(root, lane, mode, role='main', skip=()):
    shown = []
    for key, argv, base, section_max in _selected(root, lane, role, skip):
        text = _call(key, argv, base, lane, mode)
        if text is None:
            continue
        body = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        items = None
        if mode == 'line':
            text = body[0].strip()
            # Optional identity-bearing line protocol. Plain providers retain
            # their first-line contract; structured providers list every item.
            try:
                packet = json.loads(text)
            except ValueError:
                packet = None
            if isinstance(packet, dict) and 'items' in packet:
                items = packet['items']
                if not isinstance(items, list) or not items or any(
                        not isinstance(it, dict) or it.get('kind') not in ('task', 'event', 'module')
                        or not isinstance(it.get('id'), str) or not it['id']
                        or not isinstance(it.get('text'), str) or not it['text'].strip()
                        or any(c in it['text'] for c in '\r\n\t') for it in items):
                    print('[retro-sections] %s: invalid items skipped' % key, file=sys.stderr)
                    continue
                text = '\n'.join(it['text'] for it in items)
        else:
            kept, plain, quoted = [], 0, 0
            cut, input_lines, limited = False, 0, 0
            for unit, cost in _section_units(text):
                input_lines += len(unit.splitlines())
                if cost is None:  # malformed HTML quote: fail open, drop the item
                    cut = True
                    continue
                if section_max is not None:
                    # Explicit limits cover all nonempty lines. Keep HTML items
                    # atomic and do not let later items leapfrog an overflow.
                    limited += len(unit.splitlines())
                    if limited <= section_max:
                        kept.append(unit)
                    else:
                        cut = True
                elif cost:
                    quoted += cost
                    if quoted <= QUOTE_MAX_LINES:
                        kept.append(unit)
                    else:
                        cut = True
                else:
                    plain += 1
                    if plain <= SECTION_MAX_LINES:
                        kept.append(unit)
                    else:
                        cut = True
            text = '\n'.join(kept)
            if cut:
                print('[retro-sections] %s: %d lines cut to %d' % (
                    key, input_lines, len(text.splitlines())), file=sys.stderr)
        if not text:
            continue
        row = {'module': key, 'text': text}
        if items is not None:
            row['items'] = items
        shown.append(row)
    return shown


def _buttons(raw):
    """The rows of one `buttons` line, or None when it breaks the contract."""
    try:
        rows_ = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(rows_, list) or not rows_:
        return None
    for row in rows_:
        if not isinstance(row, list) or not 1 <= len(row) <= BUTTONS_PER_ROW_MAX:
            return None
        for b in row:
            if not isinstance(b, dict) or set(b) != {'text', 'callback_data'}:
                return None
            text, data = b['text'], b['callback_data']
            if not isinstance(text, str) or not text.strip() or '\n' in text:
                return None
            if not isinstance(data, str) or not data.startswith(CALLBACK_PREFIX) \
                    or len(data) <= len(CALLBACK_PREFIX) \
                    or len(data.encode('utf-8')) > CALLBACK_MAX_BYTES:
                return None
    return rows_


def ask_row(raw):
    """The validated ask dict of one `ask` line, or None when it breaks the contract."""
    try:
        got = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(got, dict) or not set(got) <= {'id', 'text', 'choices', 'recheck'}:
        return None
    ident, text, choices = got.get('id'), got.get('text'), got.get('choices')
    recheck = got.get('recheck')
    if recheck is not None and (not isinstance(recheck, str) or not recheck.strip()):
        return None
    if not isinstance(ident, str) or not ASK_ID.match(ident):
        return None
    if not isinstance(text, str) or not text.strip() or '\n' in text.strip():
        return None
    if not isinstance(choices, list) or not 1 <= len(choices) <= ASK_CHOICES_MAX:
        return None
    seen, out = set(), []
    for c in choices:
        if not isinstance(c, dict) or not set(c) <= {'label', 'cmd', 'then'}:
            return None
        label, cmd, then = c.get('label'), c.get('cmd'), c.get('then', 'advance')
        if not isinstance(label, str) or not label.strip() or '\n' in label or label in seen \
                or len(label.encode('utf-8')) > ASK_LABEL_MAX_BYTES:
            return None
        if cmd is not None and (not isinstance(cmd, str) or not cmd.strip()):
            return None
        if then not in ('advance', 'talk'):
            return None
        seen.add(label)
        row = {'label': label}
        if cmd is not None:
            row['cmd'] = cmd
        if then != 'advance':
            row['then'] = then
        out.append(row)
    row = {'id': ident, 'text': text.strip(), 'choices': out}
    if recheck is not None:
        row['recheck'] = recheck
    return row


def attach(root, lane, role='main', skip=()):
    """{'photos': [{'module', 'path', 'caption'}], 'buttons': rows} from the
    rows flagged `attach` on `lane`. Every part is checked on its own: a bad
    one is dropped with one stderr line and never takes the others along."""
    photos, buttons, asks = [], [], []
    for key, argv, base, _section_max in _selected(root, lane, role, skip, attach=True):
        text = _call(key, argv, base, lane, ATTACH)
        if text is None:
            continue
        for ln in text.splitlines():
            if not ln.strip():
                continue
            c = ln.split('\t')
            kind = c[0].strip()
            if kind == 'photo':
                path = c[1].strip() if len(c) > 1 else ''
                if not path or not os.path.isabs(path) or not os.path.isfile(path):
                    print('[retro-attach] %s photo skipped: not a file: %s' % (key, path or '-'),
                          file=sys.stderr)
                elif len(photos) >= PHOTO_MAX:
                    print('[retro-attach] %s photo skipped: %d per card' % (key, PHOTO_MAX),
                          file=sys.stderr)
                else:
                    cap = ' '.join(c[2].split()) if len(c) > 2 else ''
                    photos.append({'module': key, 'path': path, 'caption': cap[:CAPTION_MAX]})
            elif kind == 'buttons':
                got = _buttons(c[1] if len(c) > 1 else '')
                if got is None:
                    print('[retro-attach] %s buttons skipped: bad json or contract' % key,
                          file=sys.stderr)
                    continue
                room = BUTTON_ROWS_MAX - len(buttons)
                if len(got) > room:
                    print('[retro-attach] %s: %d button rows cut to %d' % (
                        key, len(got), max(room, 0)), file=sys.stderr)
                buttons += got[:max(room, 0)]
            elif kind == 'ask':
                got = ask_row(c[1] if len(c) > 1 else '')
                if got is None:
                    print('[retro-attach] %s ask skipped: bad json or contract' % key,
                          file=sys.stderr)
                elif got['id'] in [a['id'] for a in asks] or len(asks) >= ASK_MAX:
                    print('[retro-attach] %s ask skipped: %s' % (
                        key, 'duplicate id' if got['id'] in [a['id'] for a in asks]
                        else '%d per card' % ASK_MAX), file=sys.stderr)
                else:
                    asks.append(dict(got, module=key, base=base))
            else:
                print('[retro-attach] %s: unknown row kind %r ignored' % (key, kind[:20]),
                      file=sys.stderr)
    out = {'photos': photos, 'buttons': buttons}
    if asks:
        out['asks'] = asks
    return out


def _diet_grid(raw):
    """Validate the optional secondary diet grid; keep old grid rows intact.

    Wire row: diet<TAB>JSON. Pack owns head/notes/summary and all decisions;
    kit owns cells. At most 7 dated rows and 3 summary lines. Empty-week
    payload is {empty: <one line>} and works without any workout rows.
    """
    try:
        data = json.loads(raw)
        def line(value):
            return isinstance(value, str) and len(value) <= 240 and not any(
                c in value for c in '\n\r\t')
        if not isinstance(data, dict):
            return None
        if 'empty' in data:
            return {'empty': data['empty']} if line(data['empty']) and data['empty'] else None
        rows, summary = data['rows'], data['summary']
        if not line(data['head']) or not isinstance(rows, list) or not 1 <= len(rows) <= 7:
            return None
        if not isinstance(summary, list) or len(summary) > 3 or not all(map(line, summary)):
            return None
        seen = set()
        for row in rows:
            date = row['date']
            if datetime.date.fromisoformat(date).isoformat() != date or date in seen:
                return None
            seen.add(date)
            if not line(row['note']):
                return None
            for key in ('kcal', 'target'):
                n = row[key]
                if n is not None and (type(n) not in (int, float) or not math.isfinite(n)
                                      or n < 0 or (key == 'target' and n == 0)):
                    return None
        return data
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def grid(root, role='main', skip=()):
    """The weekly flow grid: {'module', 'unit', 'rows': [[date, bar, shown,
    note]]} from the first `grid` provider with valid rows, else None."""
    for key, argv, base, _section_max in _selected(root, 'grid', role, skip):
        text = _call(key, argv, base, 'weekly', 'grid')
        if text is None:
            continue
        unit, out, diet = '', [], None
        for ln in text.splitlines():
            c = ln.rstrip('\n').split('\t')
            if c and c[0] == 'diet':
                if diet is None and len(c) == 2:
                    diet = _diet_grid(c[1])
                continue
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
        if not out and diet is None:
            print('[retro-sections] %s skipped: no grid rows' % key, file=sys.stderr)
            continue
        if len(out) > GRID_MAX_ROWS:
            print('[retro-sections] %s: %d grid rows cut to %d' % (
                key, len(out), GRID_MAX_ROWS), file=sys.stderr)
        result = {'module': key, 'unit': unit, 'rows': out[:GRID_MAX_ROWS]}
        if diet is not None:
            result['diet'] = diet
        return result
    return None


def cli(script_dir, argv):
    p = argparse.ArgumentParser(prog='kit_life.sh retro-sections')
    p.add_argument('--lane', choices=('daily', 'weekly', 'morning'), required=True)
    p.add_argument('--mode', choices=('line', 'section', 'grid', ATTACH), default='line')
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
            if 'diet' in g:
                print('diet\t' + json.dumps(g['diet'], ensure_ascii=True))
        return
    if a.mode == ATTACH:
        got = attach(root, a.lane, a.role, skip)
        if a.json:
            print(json.dumps(got, ensure_ascii=True))
            return
        for ph in got['photos']:
            print('photo\t%s\t%s' % (ph['path'], ph['caption']))
        if got['buttons']:
            print('buttons\t%s' % json.dumps(got['buttons'], ensure_ascii=False,
                                               separators=(',', ':')))
        for q in got.get('asks', []):
            print('ask\t%s' % json.dumps(q, ensure_ascii=False, separators=(',', ':')))
        return
    shown = run(root, a.lane, a.mode, a.role, skip)
    if a.json:
        print(json.dumps(shown, ensure_ascii=True))
    elif shown:
        print('\n'.join(s['text'] for s in shown))
