"""Where the owner's weekly spend list comes from, by region (sources.json).

Region: env DOGANY_COUNTRY / AGENT_COUNTRY, else config/agent.conf
AGENT_COUNTRY, else AGENT_TZ, else AGENT_LANG -- the first one set decides,
matched against each region's `match` lists. Nothing set, or no region
matches -> `default`: no app is named, the sweep question asks which app or
bank shows the spending, and the answer is kept as LEDGER_SPEND_APP.
"""
import functools
import json
import os

from . import paths, text

SPEND_APP_KEY = 'LEDGER_SPEND_APP'
SPEND_APP_MAX = 40


@functools.lru_cache(maxsize=1)
def load():
    return json.loads((paths.SERVICE_DIR / 'sources.json').read_text(encoding='utf-8'))


def all_apps():
    """Every screenshot app key any region names, in file order, then 'other'."""
    d = load()
    out = []
    for r in list(d['regions'].values()) + [d['default']]:
        for k in r['screenshot']:
            if k not in out:
                out.append(k)
    return tuple(out) + ('other',)


def region():
    """-> region key (e.g. 'KR') or None (unknown / not listed)."""
    regions = load()['regions']
    for field, val in (('country', os.environ.get('DOGANY_COUNTRY') or os.environ.get('AGENT_COUNTRY')
                        or paths.agent_conf_value('AGENT_COUNTRY', env=False)),
                       ('tz', paths.agent_conf_value('AGENT_TZ')),
                       ('lang', paths.agent_conf_value('AGENT_LANG'))):
        if not val:
            continue
        val = val.strip()
        for key, r in regions.items():
            wanted = r.get('match', {}).get(field, [])
            if field == 'country' and val.upper() in wanted:
                return key
            if field == 'tz' and val in wanted:
                return key
            if field == 'lang' and val.lower().split('_')[0].split('-')[0] in wanted:
                return key
        return None
    return None


def current():
    """The region's source data plus the owner-named app (if any)."""
    key = region()
    d = load()
    r = dict(d['regions'][key] if key else d['default'])
    r['region'] = key
    r['spend_app'] = spend_app()
    return r


def spend_app():
    return (paths.conf().get(SPEND_APP_KEY) or '').strip() or None


def clean_app(name):
    name = ' '.join(str(name or '').split())[:SPEND_APP_MAX]
    if not name or '=' in name or not any(ch.isalnum() for ch in name):
        raise ValueError('app must be 1..%d characters, no "="' % SPEND_APP_MAX)
    return name


def app_names(keys):
    return [text.t('ledger.app.' + k) for k in keys]


def screenshot_phrase(src):
    """The joined app names (ko: Toss, Banksalad, ..., card app with middle dots) for a region with named apps, else None."""
    return text.join(app_names(src['screenshot'])) if src['screenshot'] else None


def csv_phrase(src):
    return text.join(app_names(src['csv'])) if src['csv'] else None


def weekly_source():
    """The persisted app wins over regional suggestions for weekly reminders."""
    src = current()
    app = src['spend_app']
    key = next((key for key in all_apps() if key != 'other' and app and app.casefold() in
                (key.casefold(), text.t('ledger.app.' + key).casefold())), None)
    regions = list(load()['regions'].values()) + [load()['default']]
    return dict(app=text.t('ledger.app.' + key) if key else app,
                export=bool(key and any(key in region['csv'] for region in regions)))
