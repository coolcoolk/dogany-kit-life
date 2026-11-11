"""Read-only HealthKit link entry, shared by every kit agent.

Uses ingest-status's live reader, never config.health_link, cached counts,
program state or memory. Link intent starts instructions, not consent.
"""
import datetime
import json
import re
from pathlib import Path

import ingest_ledger as ledger
from locale_store import read_json


def root():
    return Path(__file__).absolute().parents[1]


def copy_bundle():
    import agent_conf
    code = agent_conf.conf_str('AGENT_LANG', 'ko').lower().split('_')[0].split('-')[0]
    base = root() / 'service/kit_life/i18n'
    fallback = read_json(base / 'en.json')
    if code != 'en' and (base / (code + '.json')).is_file():
        fallback.update(read_json(base / (code + '.json')))
    return fallback


def owner_tz():
    """The declared offset (AGENT_TZ_OFFSET_HOURS), else the kit display zone."""
    import agent_conf
    off = agent_conf.conf_int('AGENT_TZ_OFFSET_HOURS', None)
    if off is not None and -12 <= off <= 14:
        return datetime.timezone(datetime.timedelta(hours=off))
    from zoneinfo import ZoneInfo
    return ZoneInfo('Asia/Seoul')


def local_date(stamp, tz, copy):
    """UTC stamp -> owner-local 'M month D day' copy; None when unparseable (never raw)."""
    try:
        dt = datetime.datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    dt = dt.astimezone(tz)
    return copy['ingest.link.date'].format(m=dt.month, d=dt.day)


# (with final consonant, without) pairs; copy writes them as 'A(B)'.
JOSA = (('\uc740', '\ub294'), ('\uacfc', '\uc640'))


def josa(text):
    """Resolve Korean particle pairs in copy from the preceding syllable."""
    for with_final, without in JOSA:
        def pick(m, with_final=with_final, without=without):
            batchim = (ord(m.group(1)) - 0xAC00) % 28
            return m.group(1) + (with_final if batchim else without)
        text = re.sub('([\uac00-\ud7a3])' + with_final + '\\(' + without + '\\)', pick, text)
    return text


def join_names(names, copy):
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return copy['ingest.link.list.two'].format(a=names[0], b=names[1])
    return copy['ingest.link.list.last'].format(head=', '.join(names[:-1]), last=names[-1])


def status_line(status, tz, copy):
    label = lambda k: copy.get('ingest.link.kind.' + k, k)
    groups = {}
    for row in status:
        at = local_date(row['revoked_at'], tz, copy) if row['state'] == 'off' else None
        groups.setdefault((row['state'], at), []).append(label(row['kind']))
    if len(groups) == 1:
        (state, at), _ = next(iter(groups.items()))
        if state == 'off' and at is None:
            return copy['ingest.link.all.off_nodate'] + '.'
        return copy['ingest.link.all.' + state].format(at=at) + '.'
    items = list(groups.items())
    clauses = []
    for i, ((state, at), names) in enumerate(items):
        form = 'end' if i == len(items) - 1 else 'mid'
        key = 'ingest.link.%s.%s' % (state, form) + ('_nodate' if state == 'off' and at is None else '')
        clauses.append(copy[key].format(kinds=join_names(names, copy), at=at))
    return josa(copy['ingest.link.status'].format(clauses=copy['ingest.link.sep'].join(clauses)))


def describe(records, policy, copy, tz=None):
    by_kind = {r['kind']: r for r in records}
    # Missing rows mean no approval, not off since a fabricated timestamp.
    kinds = list(policy['kinds']) + sorted(set(by_kind) - set(policy['kinds']))
    status = [by_kind.get(k, dict(source=policy['source'], kind=k, state='none',
                                approved_at=None, revoked_at=None)) for k in kinds]
    label = lambda k: copy.get('ingest.link.kind.' + k, k)
    pending = [k for k in policy['kinds'] if by_kind.get(k, {}).get('state') != 'on']
    return dict(source=policy['source'], status=status,
                status_line=status_line(status, tz or owner_tz(), copy),
                pending_kinds=pending, step=1,
                first_step=copy['ingest.link.first' if pending else 'ingest.link.connected'].format(
                    kinds=', '.join(label(k) for k in pending)),
                reference=policy['reference'],
                consent_command=policy['consent_command'],
                pack_consent_command=policy['pack_consent_command'],
                writes=False)


def cmd_link(ctx, argv):
    p = ledger._parser('ingest-link')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)
    policy = json.loads((root() / 'service/kit_life/ingest-link.json').read_text())
    conn = ledger._open(ctx)
    try:
        conn.execute('PRAGMA query_only=ON')
        records = ledger.status_records(conn, policy['source'])
    finally:
        conn.close()
    result = describe(records, policy, copy_bundle())
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result['status_line'])
        print(result['first_step'])
        print('REFERENCE ' + result['reference'])
    return 0
