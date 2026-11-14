"""Session-provided current-help lookups for capture and export instructions.

Official help is requested first; results expire after sources.json ttl_days.
Dated instructions name their actual source; posts never receive an official label.
Failed or stale lookups use generic wording without a menu path.
The service never browses or ships a static app navigation path.
"""
import datetime
import json

from . import paths, sources

STATE = 'lookup.json'
NOTES = 'product-notes.jsonl'
WHATS = ('export', 'how', 'capture')
VERDICTS = {'export': ('available', 'unavailable', 'failed'), 'how': ('found', 'failed'), 'capture': ('found', 'failed')}
SOURCES = ('official', 'posts')
STEPS_MAX = 12
STEP_LEN = 200
NOTE_LEN = 500


def conf():
    return sources.load().get('lookup') or {}


def ttl_days():
    return int(conf().get('ttl_days') or 30)


def app_conf(app):
    return (conf().get('apps') or {}).get(app) or {}


def _path():
    return paths.state_dir() / STATE


def load():
    try:
        d = json.loads(_path().read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    p = _path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(p)


def get(app, what, today):
    """The cached verdict, or None when there is none or it is older than
    the TTL (then the agent looks again)."""
    e = (load().get(app) or {}).get(what)
    if not e or not e.get('checked'):
        return None
    try:
        age = (today - datetime.date.fromisoformat(e['checked'])).days
    except ValueError:
        return None
    return e if 0 <= age < ttl_days() else None


def put(app, what, verdict, today, source=None, url=None, steps=None):
    if what not in WHATS:
        raise ValueError('lookup: what is export|how|capture')
    if verdict not in VERDICTS[what]:
        raise ValueError('lookup %s verdict is %s' % (what, '|'.join(VERDICTS[what])))
    if source is not None and source not in SOURCES:
        raise ValueError('lookup source is official|posts')
    e = {'verdict': verdict, 'checked': today.isoformat()}
    if what in ('how', 'capture') and verdict == 'found':
        if source is None:
            raise ValueError('lookup how found needs --source official|posts')
        if not isinstance(steps, list) or not 1 <= len(steps) <= STEPS_MAX or \
                not all(isinstance(s, str) and 0 < len(s.strip()) <= STEP_LEN for s in steps):
            raise ValueError('lookup how found needs --steps-json: a JSON list of 1..%d step texts' % STEPS_MAX)
        e['steps'] = [s.strip() for s in steps]
    if what == 'export' and verdict == 'available' and source is None:
        raise ValueError('lookup export available needs --source official|posts')
    if source:
        e['source'] = source
    if url:
        e['url'] = str(url)[:300]
    d = load()
    d.setdefault(app, {})[what] = e
    _save(d)
    return dict(e, app=app, what=what)


def need(app, what, today):
    """What the agent has to look up now (None = the cache answers)."""
    if get(app, what, today):
        return None
    a = app_conf(app)
    return {'app': app, 'what': what, 'official': a.get('official') or [],
            'ttl_days': ttl_days(),
            'answer': ('onboarding lookup-set %s export available|unavailable|failed --source official|posts '
                       '[--url URL]' % app) if what == 'export' else
                      ('onboarding lookup-set %s %s found --source official|posts --steps-json JSON '
                       '[--url URL] | onboarding lookup-set %s %s failed' % (app, what, app, what)),
            'other_app': 'another app newly offering an export: onboarding product-note TEXT '
                         '(recorded for the steward, never offered to the owner)'}


def note(text, today):
    text = ' '.join(str(text or '').split())[:NOTE_LEN]
    if not text:
        raise ValueError('product-note needs a text')
    p = paths.state_dir() / NOTES
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps({'at': today.isoformat(), 'from': 'ledger-lookup', 'note': text},
                           ensure_ascii=False, sort_keys=True) + '\n')
    return {'noted': True, 'file': 'files/ledger/' + NOTES}
