"""Per-card automation: is the owner's Shortcut handing card SMS to the
Dogany app, and which SMS senders may go into the ledger? Pack-owned state
`files/ledger/automation.json`: dates, counts, sender keys and receiver event
ids. Never SMS text (that stays in the receiver DB).

Sender list (`sms_senders`):
  registered  {sender key: issuer or null}  the owner said register; one issuer
                                            may own several keys
  refused     [sender key]                  the owner said do-not-register; never
                                            asked again, rows not kept
  held        {sender key: {sender, issuer, events[], count, last}}
                                            first-seen sender: rows wait for
                                            the owner's one answer
  unusable    {app_old|no_sender: {events[], count, last}}
                                            rows that carry no usable sender:
                                            app below 0.4, or the Shortcut's
                                            Sender field empty / holding the
                                            message body. Held, never ingested

The collector reports what it saw (`seen`, `problem`, `hold_*`); `lines()`
turns that into owner-facing Korean lines, each said once:
  ok       the first SMS of an issuer arrived            -> "it is coming in fine"
  sender   a first-seen sender (the one question)        -> "register it?" (deungrok-halkkayo)
  app      app below 0.4                                 -> update the app
  problem  the Shortcut hands over no sender             -> what to fix
  nudge    3 days after setup and nothing arrived at all -> checklist
"""
import datetime
import json

from . import paths
from .smsparse import sender_key

STATE = 'automation.json'
NUDGE_AFTER_DAYS = 3
MIN_APP_KO = '0.4'


def _path():
    return paths.state_dir() / STATE


def load():
    try:
        d = json.loads(_path().read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    p = _path()
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    tmp.replace(p)


def start(issuers, today):
    """Onboarding apply: these issuers are now waiting for their first SMS.
    Issuers no longer chosen are dropped; an already verified one stays ok."""
    d = load()
    cur = d.get('issuers', {})
    d['issuers'] = {i: cur.get(i) or {'state': 'waiting', 'since': today.isoformat(), 'first_seen': None}
                    for i in issuers}
    d.setdefault('announced', [])
    save(d)
    return d


def seen(d, issuer, at):
    """An SMS from a registered sender arrived (issuer None = not recognised)."""
    at = at.isoformat() if at else None
    if at and (d.get('forward_seen') is None or at < d['forward_seen']):
        d['forward_seen'] = at
    rec = d.get('issuers', {}).get(issuer)
    if rec and rec['state'] == 'waiting':
        rec.update(state='ok', first_seen=at)


def problem(d, kind, at, **info):
    p = d.setdefault('problems', {}).setdefault(kind, {'count': 0})
    p['count'] += 1
    p['last'] = at.isoformat() if at else None
    p.update(info)


# ---- sender list ------------------------------------------------------------

def _senders(d):
    s = d.setdefault('sms_senders', {})
    s.setdefault('registered', {})
    s.setdefault('refused', [])
    s.setdefault('held', {})
    s.setdefault('unusable', {})
    return s


def sender_status(d, key):
    """'registered' | 'refused' | 'unknown' for a canonical sender key."""
    s = d.get('sms_senders') or {}
    if key in (s.get('registered') or {}):
        return 'registered'
    if key in (s.get('refused') or []):
        return 'refused'
    return 'unknown'


def _add_event(rec, event_id, at):
    if event_id not in rec.setdefault('events', []):
        rec['events'].append(event_id)
        rec['count'] = rec.get('count', 0) + 1
    rec['last'] = at.isoformat() if at else None


def hold_sender(d, key, raw, issuer, event_id, at):
    """A first-seen sender: keep the receiver event id (not the text) until
    the owner answers the one question for this key."""
    rec = _senders(d)['held'].setdefault(key, {'sender': raw.strip(), 'issuer': issuer})
    if issuer and not rec.get('issuer'):
        rec['issuer'] = issuer
    _add_event(rec, event_id, at)


def hold_unusable(d, why, event_id, at):
    """why: app_old (app below 0.4) | no_sender (empty, or not a sender)."""
    _add_event(_senders(d)['unusable'].setdefault(why, {}), event_id, at)


def decide_sender(raw, approved, issuer=None):
    """Persist the owner's explicit answer for one sender. Returns
    (key, issuer, held event ids). register -> registered under the parsed issuer
    (the caller's, else the one the held rows named); do-not-register -> refused
    for good and the held ids dropped (counts only survive, in the log)."""
    key = sender_key(raw)
    if key is None:
        raise ValueError('not a sender: %r' % raw)
    d = load()
    s = _senders(d)
    held = s['held'].pop(key, {})
    ids = held.get('events', [])
    if approved:
        issuer = issuer or held.get('issuer')
        s['registered'][key] = issuer
        s['refused'] = [k for k in s['refused'] if k != key]
    else:
        s['registered'].pop(key, None)
        s['refused'] = sorted(set(s['refused']) | {key})
    save(d)
    return key, issuer, ids


def known_sender_issuer(key, docs):
    """The issuer whose owner-verified sender numbers include this key."""
    for iss, doc in sorted(docs.items()):
        rec = doc['guide']['sender_numbers']
        if rec['status'] == 'verified' and any(sender_key(n) == key for n in rec['value'] or []):
            return iss
    return None


# ---- owner-facing lines ------------------------------------------------------

# Last digit as spoken (0 yeong ... 9 gu): which ones end in a final consonant
# other than rieul, i.e. take the particle -euro.
_EURO_DIGITS = set('036')


def with_ro(word):
    """'1577-6200' -> '1577-6200' + euro, 'Hyundai Card' (in Hangul) -> + ro:
    the particle after a final consonant other than rieul is -euro, else -ro."""
    last = word.rstrip()[-1:] if word else ''
    if last.isdigit():
        return word + ('\uc73c\ub85c' if last in _EURO_DIGITS else '\ub85c')
    if '\uac00' <= last <= '\ud7a3':
        jong = (ord(last) - 0xAC00) % 28
        return word + ('\uc73c\ub85c' if jong not in (0, 8) else '\ub85c')
    return word + '(\uc73c)\ub85c'


def sender_question(raw, hint_issuer_name=None):
    hint = ('%s \uacb0\uc81c \ubb38\uc790 \ubc88\ud638\ub85c \uc54c\ub824\uc9c4 \ubc88\ud638\uc608\uc694. ' % hint_issuer_name) if hint_issuer_name else ''
    return '%s \uce74\ub4dc \uacb0\uc81c \ubb38\uc790\uac00 \uc654\uc5b4\uc694. %s\ub9de\uc73c\uba74 \ub4f1\ub85d\ud560\uac8c\uc694. \uc774 \ubc88\ud638\ub97c \ub4f1\ub85d\ud560\uae4c\uc694?' % (with_ro(raw.strip()), hint)


APP_UPDATE_LINE = ('\uce74\ub4dc \uacb0\uc81c \ubb38\uc790\uac00 \uc608\uc804 \ub3c4\uac00\ub2c8 \uc571\uc5d0\uc11c \uc640\uc11c \ubcf4\ub0b8 \ubc88\ud638\ub97c \ud655\uc778\ud560 \uc218 \uc5c6\uc5b4\uc694. '
                   '\ub3c4\uac00\ub2c8 \uc571\uc744 %s \ubc84\uc804 \uc774\uc0c1\uc73c\ub85c \uc5c5\ub370\uc774\ud2b8\ud574\uc8fc\uc138\uc694. \uadf8\ub3d9\uc548 \uc628 \uacb0\uc81c\ub294 \ub9e4\uc8fc \uc815\ub9ac \ub54c \ucc59\uae38\uac8c\uc694.' % MIN_APP_KO)
NO_SENDER_LINE = ("\ub2e8\ucd95\uc5b4\uac00 \uce74\ub4dc \ubb38\uc790\ub97c \ubcf4\ub0bc \ub54c \ubcf4\ub0b8 \uc0ac\ub78c\uc774 \ube60\uc838 \uc788\uc5b4\uc694. \ub2e8\ucd95\uc5b4\uc758 [\ub3c4\uac00\ub2c8\ub85c \ubcf4\ub0b4\uae30] \ub3d9\uc791\uc5d0\uc11c "
                  "[\ubcf4\ub0b8 \uc0ac\ub78c] \uce78\uc5d0 [\ub2e8\ucd95\uc5b4 \uc785\ub825]\uc758 [\ubcf4\ub0b8 \uc0ac\ub78c]\uc744 \ub123\uc5b4\uc8fc\uc138\uc694. \ubb38\uc790 \ubcf8\ubb38\uc744 \ub123\uc73c\uba74 \uc548 \ub3fc\uc694.")


def _mmdd(iso):
    return '%d/%d' % (int(iso[5:7]), int(iso[8:10]))


def lines(today, names=None, docs=None, d=None):
    """[(key, text)] not yet announced. names: issuer -> display_ko."""
    d = d if d is not None else load()
    names = names or {}
    said = set(d.get('announced', []))
    out = []
    recs = d.get('issuers', {})
    if not recs:
        return out
    for iss, rec in sorted(recs.items()):
        if rec['state'] == 'ok' and rec.get('first_seen'):
            out.append(('ok:' + iss, '%s \uacb0\uc81c \ubb38\uc790\uac00 \uac00\uacc4\ubd80\ub85c \uc798 \ub4e4\uc5b4\uc624\uace0 \uc788\uc5b4\uc694 (\uccab \ubb38\uc790 %s). \uc774\uc81c \uc774 \uce74\ub4dc\ub294 \ub530\ub85c \ub9d0 \uc548 \ud558\uc154\ub3c4 \ub3fc\uc694.'
                        % (names.get(iss, iss), _mmdd(rec['first_seen']))))
    s = d.get('sms_senders') or {}
    for key, rec in sorted((s.get('held') or {}).items()):
        hint = known_sender_issuer(key, docs) if docs else None
        out.append(('sender:' + key, sender_question(rec.get('sender') or key, names.get(hint) if hint else None)))
    unusable = s.get('unusable') or {}
    if unusable.get('app_old'):
        out.append(('app_update', APP_UPDATE_LINE))
    ns = unusable.get('no_sender')
    if ns:
        out.append(('problem:no_sender:%s' % ns.get('last'), NO_SENDER_LINE))
    waiting = [i for i, r in sorted(recs.items()) if r['state'] == 'waiting']
    if waiting and not d.get('forward_seen') and not s.get('held') and not unusable:
        since = min(datetime.date.fromisoformat(recs[i]['since']) for i in waiting)
        if (today - since).days >= NUDGE_AFTER_DAYS:
            out.append(('nudge:' + ','.join(waiting),
                        '%s \uacb0\uc81c \ubb38\uc790\uac00 \uc544\uc9c1 \ud55c \ubc88\ub3c4 \uc548 \uc654\uc5b4\uc694. \uadf8\ub3d9\uc548 \uc774 \uce74\ub4dc\ub97c \uc548 \uc4f0\uc168\ub2e4\uba74 \uad1c\ucc2e\uc544\uc694. \uc4f0\uc168\ub2e4\uba74 '
                        '\u2460 \uce74\ub4dc \uacb0\uc81c \ubb38\uc790 \uc54c\ub9bc \uc2e0\uccad \u2461 \ub2e8\ucd95\uc5b4 \uc790\ub3d9\ud654\uac00 [\uc989\uc2dc \uc2e4\ud589]\uc778\uc9c0 \u2462 \ub3c4\uac00\ub2c8 \uc571\uc774 \uc5f0\uacb0\ub3fc \uc788\ub294\uc9c0 '
                        '\ud655\uc778\ud574\uc8fc\uc138\uc694.' % ', '.join(names.get(i, i) for i in waiting)))
    return [(k, t) for k, t in out if k not in said]


def mark_announced(keys):
    d = load()
    d['announced'] = sorted(set(d.get('announced', [])) | set(keys))
    save(d)
