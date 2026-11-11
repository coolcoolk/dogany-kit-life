"""Read the bridge's instance member roster without importing its runtime.

Bridge ownership precedence is allowed_user_ids, then owner.lock. Contacts,
crew agents, workout owners and process environment are never a member roster.
Only the selected membership value is retained in the evidence, never .env.
"""
import hashlib
import json
from pathlib import Path
import shlex


def read_instance_roster(root):
    root = Path(root).resolve()
    env = root / '.telegram_bot/.env'
    selected = {}
    if env.is_file():
        for line in env.read_text(encoding='utf-8').splitlines():
            key, sep, value = line.strip().removeprefix('export ').partition('=')
            key = key.strip().upper()
            if sep and key in ('ALLOWED_USER_IDS', 'BOT_DATA_DIR'):
                if key in selected:
                    raise ValueError('duplicate instance membership setting: ' + key)
                tokens = shlex.split(value, comments=True)
                selected[key] = ' '.join(tokens)
    value = selected.get('ALLOWED_USER_IDS', '')
    if value:
        ids = [int(x.strip()) for x in value.split(',') if x.strip()]
        source = '.telegram_bot/.env:ALLOWED_USER_IDS'
    else:
        custom = selected.get('BOT_DATA_DIR')
        custom_path = Path(custom).expanduser() if custom else root/'.telegram_bot'
        if not custom_path.is_absolute():
            custom_path = root/custom_path
        if custom_path.resolve() != (root/'.telegram_bot').resolve():
            raise ValueError('custom BOT_DATA_DIR requires an explicit roster capture')
        lock = root/'.telegram_bot/owner.lock'
        raw = lock.read_text(encoding='ascii').strip() if lock.is_file() else ''
        ids = [int(raw)] if raw else []
        source = '.telegram_bot/owner.lock'
    if any(i <= 0 for i in ids):
        raise ValueError('instance member IDs must be positive')
    members = sorted({str(i) for i in ids})
    evidence = dict(schema='instance_member_roster/1', members=members,
                    source_ref=source, method='bridge_ownership_precedence/1')
    evidence['source_digest'] = 'sha256:' + hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode('ascii')).hexdigest()
    return evidence


def single_member_binding(roster, owning_agent, legacy_session_ids):
    """Mechanical attribution evidence, without modifying a legacy workout."""
    from session_program import fingerprint
    members = roster['members']
    if len(members) != 1:
        raise ValueError('legacy attribution requires exactly one instance member; found ' + str(len(members)))
    member = members[0]
    source = dict(member_id=member, legacy_session_ids=sorted(legacy_session_ids),
                  owning_agent=owning_agent, provenance='from_v1',
                  method='single_instance_member', member_roster=members)
    return {member: dict(legacy_session_ids=source['legacy_session_ids'], owning_agent=owning_agent,
        attribution_evidence=['from_v1:single_instance_member:' + fingerprint(source)],
        attribution_digest=fingerprint(source), instance_roster=roster)}
