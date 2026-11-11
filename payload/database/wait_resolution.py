"""Resolve explicit waits from owner facts or completed steps in the same plan.

No fuzzy project/title overlap: a subject and a completion fact must both match.
Writers call these helpers inside their transaction; providers only read evidence.
"""
import datetime
import json
import re
import shlex


WAIT = re.compile(r"^(?:(?:waiting|wait) for\s+(.+?)(?:\s+(?:to arrive|delivery|arrival|to finish))?|(.+?)\s*(?:도착|배송|수령|완료|종료)?\s*기다림)$", re.I)


def subject(title):
    match = WAIT.fullmatch(title.strip())
    if not match:
        return None
    name = (match[1] or match[2]).strip(' :.-')
    name = re.sub(r'\s+(?:도착|배송|수령|완료|종료)$', '', name)
    return name or None


def mentions(name, words):
    return bool(re.search(r'(?<![\w])' + re.escape(name) +
                         r'(?=$|[^\w]|[은는이가을를도왔])', words, re.I))


def affirms(name, words):
    """Compatibility reader: completion is an explicit wait id action."""
    return False


def owner_fact(name, words):
    return False


def open_waits(conn, agent=None):
    rows = conn.execute("SELECT id,title,version,owning_agent,created_at FROM timeblock "
                        "WHERE kind='task' AND schedule_kind='untimed' AND block_class IS NULL "
                        "AND status='open' AND settled_at IS NULL").fetchall()
    return [r for r in rows if subject(r[1]) and
            (not agent or r[3] in agent.split(','))]


def word_matches(conn, words, agent=None, wait_id=None):
    # The explicit verb identifies the wait. No current sentence is parsed.
    if wait_id is None:
        return []
    return [r for r in open_waits(conn, agent) if str(r[0]) == str(wait_id)]


def linked_evidence(conn, row, as_of=None):
    as_of = as_of or datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    name = subject(row[1])
    if not name:
        return None
    for bid, title, stamp in conn.execute(
            "SELECT e.id,e.title,e.settled_at FROM timeblock e "
            "JOIN project_block p ON p.block_id=e.id "
            "JOIN project_block w ON w.project_id=p.project_id "
            "WHERE w.block_id=? AND e.id<>? AND e.owning_agent=? "
            "AND e.settled_outcome='done' AND e.settled_at BETWEEN ? AND ? ORDER BY e.id",
            (row[0], row[0], row[3], row[4], as_of)):
        if mentions(name, title) and re.search(
                r'픽업|수령|받기|전달|도착|\b(?:pickup|pick up|receipt|receive|delivery|arrival)\b', title, re.I):
            return dict(block_id=bid, title=title, settled_at=stamp)
    return None


def settle(kit, conn, rows, proof):
    closed = []
    for row in rows:
        stamp = kit.now_utc()
        n = kit._tb_update(conn, row[0], dict(settled_at=stamp, settled_by=kit._facade_agent(),
                           settled_outcome='done', updated_at=stamp),
                           where_extra=' AND settled_at IS NULL', bump_version=True, cas_version=row[2])
        if n:
            # A wait is administrative state; resolving it does not invent
            # another experience alongside the actual pickup/arrival fact.
            kit._store_status(conn, row[0], 'done')
            conn.execute("INSERT OR REPLACE INTO block_attr(block_id,attr_token,value,source) "
                         "VALUES (?,'wait_resolution',?,'user')",
                         (row[0], json.dumps(proof, ensure_ascii=False)))
            closed.append(row[0])
    if closed:
        for key, raw in conn.execute("SELECT key,value FROM config WHERE key LIKE 'retro_walk.%'").fetchall():
            state = json.loads(raw)
            if state.get('phase') not in ('ready', 'active'):
                continue
            present = {i['id'] for i in state['items'] if i['kind'] == 'task'}
            retired = sorted(present.intersection(map(str, closed)))
            if retired:
                state['resolved_wait_ids'] = sorted(set(state.get('resolved_wait_ids', [])) | set(retired))
                conn.execute('UPDATE config SET value=? WHERE key=?',
                             (json.dumps(state, ensure_ascii=False), key))
    return closed


def resolve_words(kit, conn, words, agent=None, wait_id=None, occurred_on=None):
    rows = word_matches(conn, words, agent, wait_id)
    if occurred_on:
        # An old or future experience is no evidence for today's open wait.
        today = kit.now_utc()[:10]
        rows = [r for r in rows if r[4][:10] <= occurred_on <= today]
    return settle(kit, conn, rows, dict(said=words))


def reconcile(kit, conn, agent=None):
    closed = []
    for row in open_waits(conn, agent):
        proof = linked_evidence(conn, row, as_of=kit.now_utc())
        if proof:
            closed.extend(settle(kit, conn, [row], proof))
    return closed


def question(kit, title):
    name = subject(title)
    if name and re.search(r'완료|종료|끝|finish|complet', title, re.I):
        return kit._i18n('retro.daily.wait.finished', 'Is {subject} finished?').format(subject=name)
    if name:
        return kit._i18n('retro.daily.wait.received', 'Did {subject} arrive?').format(subject=name)
    return kit._i18n('retro.daily.wait.finished', 'Is {subject} finished?').format(subject=title)


def reply_context(conn, words, agent):
    rows = open_waits(conn, agent)
    if not rows:
        return ''
    return ('[plan-wait] Existing open waits: ' + json.dumps([dict(id=r[0], title=r[1]) for r in rows], ensure_ascii=False) +
            '. Interpret the owner message. Only an owner-reported completion authorizes '
            'plan-wait-resolve ID --said WORDS; identify the id yourself and preserve their exact words. '
            'Do not create a duplicate task.')


def cli(kit, argv):
    argv, words = kit._pop_opt(argv, '--said')
    argv, sweep = kit._pop_flag(argv, '--reconcile')
    if (sweep and (words is not None or argv)) or (not sweep and (words is None or len(argv) != 1)):
        raise ValueError('plan-wait-resolve ID --said WORDS | --reconcile')
    conn = kit.event_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        closed = (reconcile(kit, conn, kit._facade_agent()) if sweep else
                  resolve_words(kit, conn, words, kit._facade_agent(), argv[0] if argv else None))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(json.dumps(dict(status='RESOLVED' if closed else 'NO_MATCH', closed=closed)))
