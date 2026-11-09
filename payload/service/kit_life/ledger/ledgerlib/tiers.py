"""Tiered / usage subscriptions in the monthly settlement (owner answer
2026-10-10 19:38: Claude Pro / Max 5x / Max 20x switching + API usage under
one merchant).

The kit tags every charge (`sub-tiers`, bands in
database/subscription_tiers.json); this module only words it:

  change_lines(month)   a tier change in that month, ONCE per service and
                        month (state files/ledger/tiers.json), e.g.
                        "Claude subscription: Max 5x -> Pro (month, amount)"
  usage_lines(month)    the month's usage (API) charges per service, said as
                        regular spending; business-scope usage kept apart

Callers: `month-close YYYY-MM` (the ledger-session monthly settlement) and
the weekly retro (the previous month's change, once, if the settlement has
not said it yet).
"""
import datetime
import json

from . import kit, paths, text

STATE = 'tiers.json'


def _path():
    return paths.state_dir() / STATE


def _load():
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


def prev_month(today):
    first = today.replace(day=1) - datetime.timedelta(days=1)
    return first.isoformat()[:7]


def change_lines(month, mark=True):
    try:
        data = kit.sub_tiers(month)
    except kit.KitError:
        return []
    st = _load()
    said = set(st.get('announced') or [])
    lines, new = [], []
    for sv in data.get('services', []):
        by = {}
        for ch in sv.get('changes', []):
            by.setdefault(ch['month'], []).append(ch)
        for m, chs in sorted(by.items()):
            tag = '%s:%s' % (sv['key'], m)
            if tag in said:
                continue
            # one line per service and month: the chain of switches
            chain = text.t('ledger.tiers.arrow').join([chs[0]['from']] + [c['to'] for c in chs])
            lines.append(text.t('ledger.tiers.change', name=sv.get('name') or sv['key'], chain=chain,
                                m=int(m[5:7]), v=text.money(chs[-1]['plan'])))
            new.append(tag)
    if mark and new:
        st['announced'] = sorted(said | set(new))
        _save(st)
    return lines


def usage_lines(month):
    try:
        data = kit.sub_tiers(month)
    except kit.KitError:
        return []
    out = []
    for sv in data.get('services', []):
        for m in sv.get('months', []):
            personal = m['usage'] - m['usage_business']
            if personal > 0:
                out.append(text.t('ledger.tiers.usage', name=sv.get('usage_name') or sv['key'],
                                  v=text.money(personal)))
            if m['usage_business'] > 0:
                out.append(text.t('ledger.tiers.usage_business', name=sv.get('usage_name') or sv['key'],
                                  v=text.money(m['usage_business'])))
    return out
