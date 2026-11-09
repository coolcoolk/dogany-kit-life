"""Agent-judged dish selection, retained until planning or local day end.

No prompt classification lives here. The agent supplies typed context; the
hook reads it on every turn without inspecting the owner's words.
"""
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

PREFIX = 'cooking.pending.'


def load(conn, agent, now=None):
    row = conn.execute('SELECT value FROM config WHERE key=?', (PREFIX + agent,)).fetchone()
    state = json.loads(row[0]) if row else None
    if state and state['expires_at'] > (time.time() if now is None else now):
        return state
    return None


def store(K, conn, dishes, *, eaters=None, target=None, now=None):
    import agent_conf
    if not dishes or any(not d.strip() or d.lstrip().startswith('--') for d in dishes):
        raise ValueError('cooking pending DISH [DISH ...] [--eaters owner,NAME] [--target ISO_WITH_OFFSET]')
    agent = K._pin_agent()
    now = time.time() if now is None else now
    previous = load(conn, agent, now) or {}
    members = eaters if eaters is not None else previous.get('eaters', ['owner'])
    if not members or any(not m.strip() for m in members) or len(set(members)) != len(members) or 'owner' not in members:
        raise ValueError('eaters must be unique names including owner')
    target = target if target is not None else previous.get('target_at')
    if target is not None:
        parsed = dt.datetime.fromisoformat(target.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('target must include its date, time and timezone offset')
        target = parsed.isoformat()
    zone = ZoneInfo(agent_conf.conf_str('AGENT_TIMEZONE', 'Asia/Seoul'))
    today = dt.datetime.fromtimestamp(now, zone).date()
    expires = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(), zone).timestamp()
    state = dict(dishes=dishes, eaters=members, target_at=target, expires_at=expires)
    K._config_put(conn, PREFIX + agent, json.dumps(state, ensure_ascii=False))
    return state


def consume(K, conn):
    conn.execute('DELETE FROM config WHERE key=?', (PREFIX + K._pin_agent(),))


def hook_context(conn, agent, now=None):
    state = load(conn, agent, now)
    if not state:
        return None
    context = {key: state[key] for key in ('dishes', 'eaters', 'target_at')}
    return ('[cooking-pending] Open cooking context: ' + json.dumps(context, ensure_ascii=True) +
            '. Read core-log/reference/cooking.md. Judge this turn in conversation context; '
            'any timing, prep or schedule answer for these dishes must use $LKIT day-now, '
            'cooking template -> PLAN.json -> cooking check PLAN.json until PLAN_OK -> cooking plan PLAN.json. '
            'Never task-add for cooking. Retain dishes, eaters and target; update cooking pending for later facts. '
            'Owner plus one companion = 2 eaters; size recipes for them. Read template appliance facts; '
            'never re-ask stored type or modes. With a target wait for the kit stage Start buttons; '
            'without a target use cooking open PLAN.json. Only the kit posts the timeline; '
            'never substitute a freehand chat timeline or log a meal.')
