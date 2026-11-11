"""Per-stop opinions on existing experiences, offered once per course.

The normal reminder heartbeat handles the after-end fallback. Config holds
only delivery state; owner words and evidence stay on the experience layer.
"""
import datetime as dt
import html
import json
import re
import shlex
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo


def _instant(value):
    if not value or 'T' not in value:
        return None
    stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return stamp if stamp.tzinfo else None


def clock(kit):
    return _instant(kit.now_utc())


def _key(root):
    return 'experience_course_review.' + root['ulid']


def state(conn, root):
    row = conn.execute('SELECT value FROM config WHERE key=?', (_key(root),)).fetchone()
    return json.loads(row[0]) if row else {}


def _put(conn, root, value):
    conn.execute('INSERT OR REPLACE INTO config(key,value) VALUES(?,?)',
                 (_key(root), json.dumps(value, ensure_ascii=True)))


def _conf(root, filename, name):
    try:
        for line in (Path(root) / 'config' / filename).read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key.strip() == name:
                return value.strip().strip('\"\'')
    except OSError:
        pass
    return None


def _zone(kit):
    root = kit._kit_root()
    zone = _conf(root, 'agent.conf', 'AGENT_TZ')
    if zone:
        return ZoneInfo(zone)
    offset = _conf(root, 'agent.conf', 'AGENT_TZ_OFFSET_HOURS')
    return dt.timezone(dt.timedelta(hours=float(offset or 0)))


def _retro(kit, day):
    root = kit._kit_root()
    sunday = dt.date.fromisoformat(day).weekday() == 6
    switch = 'BUNDLE_WEEKLY_REVIEW' if sunday else 'BUNDLE_DAILY_RETRO'
    enabled = next((_conf(root, filename, switch) for filename in
                    ('kit_life.conf', 'lifekit.conf')
                    if (Path(root) / 'config' / filename).exists()), None) != 'off'
    name = 'BRIEF_TIME_WEEKLY' if sunday else 'BRIEF_TIME_RETRO'
    value = _conf(root, 'agent.conf', name)
    if not value:
        # Read the kit's single default-time table, rather than another clock.
        routine = 'weekly-review' if sunday else 'daily-retro'
        path = Path(root) / 'service/kit_life/bundle.conf'
        for line in path.read_text().splitlines():
            cols = line.split('\t')
            if cols[0] == routine:
                value = cols[2]
                break
    hour, minute = map(int, (value or '22:00').split()[-1].split(':'))
    return enabled, dt.datetime.combine(dt.date.fromisoformat(day),
                                       dt.time(hour, minute), _zone(kit))


def annotate(kit, conn, root, day, now=None, route='retro'):
    """Read-only readiness: future/ongoing stops cannot trigger an opinion."""
    import experience_retro as E
    # Inspect every sibling for readiness, while the lived-day list itself
    # continues to show only reports selected by its original date gate.
    children = E._dicts(conn.execute(
        'SELECT id,ulid,title,reaction,occurred_on,occurred_at FROM event WHERE parent_id=? ORDER BY id',
        (root['id'],)))
    if len(children) < 2:
        root['course_review'] = None
        return
    now = now or clock(kit)
    ends = []
    running = False
    for member in [root] + children:
        stamp = _instant(member.get('occurred_at'))
        if stamp:
            ends.append(stamp)
        elif member.get('occurred_on'):
            ends.append(dt.datetime.combine(dt.date.fromisoformat(member['occurred_on']),
                                            dt.time(), _zone(kit)))
        for block in E._blocks(conn, member['id']):
            if block['kind_token'] not in ('appointment', 'presence'):
                continue
            end = _instant(block['end_at']) or _instant(block['settled_at'])
            if end:
                ends.append(end)
            elif _instant(block['start_at']):
                running = True
    ended = max(ends) if ends else dt.datetime.combine(
        dt.date.fromisoformat(day), dt.time(), _zone(kit))
    enabled, retro_at = _retro(kit, day)
    reserved = enabled and ended <= retro_at
    # Opinion bullets use capture order, without pretending that an
    # unclocked mid-day report happened after the final timed stop.
    stops = [c for c in sorted(children, key=lambda c: c['id'])
             if not (c.get('reaction') or '').strip()]
    ready = bool(stops) and not running and ended <= now and not state(conn, root)
    ready = ready and (route != 'standalone' or not reserved)
    # The course suffix belongs to the question template, not the area name.
    area = re.sub(kit._i18n('retro.experience.course.area_suffix'), '',
                  root['title'], flags=re.I).strip() or root['title']
    text = kit._i18n('retro.experience.course.ask').format(area=area)
    text += ''.join('\n\u2022 ' + c['title'] for c in stops)
    root['course_review'] = dict(ready=ready, text=text, day=day,
        stop_ids=[c['id'] for c in stops], ended_at=ended.isoformat(),
        reserved_for_retro=reserved)
    if ready:
        question = dict(root_id=root['id'], event_id=root['id'],
                        field='reaction', title=root['title'], text=text,
                        stop_ids=[c['id'] for c in stops])
        root['pending'].append(question)
        root['next_question'] = question


def claim(conn, root, route, now):
    """Caller owns the writer transaction; duplicate offers fail closed."""
    review = root.get('course_review')
    if not review or not review['ready'] or state(conn, root):
        raise ValueError('COURSE_REVIEW_ALREADY_OFFERED_OR_NOT_READY')
    _put(conn, root, dict(route=route, asked_at=now,
                         day=review['day'], stop_ids=review['stop_ids'], text=review['text']))


def taste_map_write(kit, conn, event_id, reaction):
    """TODO(framework-2.7-taste-map): forward explicit stop reactions here."""


def split_reply(children, words):
    """Match explicit stop subjects only; ambiguous fragments stay outside.

    A unique short subject can occur inside a longer venue name (bakery).
    No sentiment paraphrase, inference from food words, or fuzzy scoring.
    """
    matched, unmatched = {}, []
    for fragment in re.split(r'[,;\n]+', words):
        fragment = fragment.strip()
        if not fragment:
            continue
        compact = re.sub(r'\s+', '', fragment).casefold()
        full = [c for c in children if re.sub(r'\s+', '', c['title']).casefold() in compact]
        subject = re.split(r'\s+', fragment, maxsplit=1)[0].casefold()
        subject = re.sub(r'(?:\uc5d0\uc11c|\uc740|\ub294|\uc774|\uac00)$', '', subject)
        short = [c for c in children if len(subject) >= 2 and
                 subject in re.sub(r'\s+', '', c['title']).casefold()]
        candidates = full or short
        if len(candidates) == 1:
            matched.setdefault(candidates[0]['id'], []).append(fragment)
        else:
            unmatched.append(fragment)
    return {eid: '\n'.join(parts) for eid, parts in matched.items()}, unmatched


def answer(kit, conn, event, words):
    """Use the existing enrichment writer under its atomic transaction."""
    import experience_retro as E
    children = E._dicts(conn.execute('SELECT * FROM event WHERE parent_id=? ORDER BY id', (event['id'],)))
    if len(children) < 2:
        return False
    matched, unmatched = split_reply(children, words)
    for child in children:
        reaction = matched.get(child['id'])
        if reaction:
            kit.experience_ensure(conn, child['title'], event_id=child['id'],
                reaction=reaction, verbatim=reaction)
            taste_map_write(kit, conn, child['id'], reaction)
    if unmatched:
        # Keep the whole reply when nothing matches, including punctuation.
        remainder = '\n'.join(unmatched) if matched else words
        kit.experience_ensure(conn, event['title'], event_id=event['id'],
                              reaction=remainder, verbatim=remainder)
    previous = state(conn, event)
    previous.update(answered_at=kit.now_utc())
    _put(conn, event, previous)
    return True


def tick(kit, read_conn, db_path, now_ts, send=None):
    """Existing reminder heartbeat, with transport injectable for scratch gates.

    Keep the writer lock through delivery, as start-card does. No success ack
    means no claim, so an unavailable/failed transport can be retried.
    """
    if kit.scope_self()['role'] == 'domain':
        return
    import experience_retro as E
    now = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc)
    today = now.astimezone(_zone(kit)).date()
    # Include older outings that only finish now; do not lose a multi-day
    # course just because its original report is no longer today/yesterday.
    days = [r[0] for r in read_conn.execute(
        "SELECT DISTINCT COALESCE(e.occurred_on, CASE WHEN json_valid(k.capture_key) "
        "THEN json_extract(k.capture_key,'$[0]') END) AS day FROM event e "
        'LEFT JOIN experience_capture_key k ON k.event_id=e.id '
        'WHERE e.parent_id IS NULL AND (SELECT count(*) FROM event c WHERE c.parent_id=e.id)>=2 '
        "AND NOT EXISTS (SELECT 1 FROM config WHERE key='experience_course_review.'||e.ulid) "
        'AND day<=? ORDER BY day', (today.isoformat(),)) if r[0]]
    candidates = [r for day in days for r in E.listing(kit, read_conn, day, now, 'standalone')
                  if r.get('course_review') and r['course_review']['ready']]
    if not candidates:
        return
    if send is None:
        import start_card
        push = kit._photo_push_sh()
        if not push or not start_card._capable(push):
            return
        send = lambda text: start_card._push(push, ['--strict', '--text', text])
    for candidate in candidates:
        conn = kit.event_conn(db_path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            day = candidate['course_review']['day']
            roots = E.listing(kit, conn, day, now, 'standalone')
            root = next((r for r in roots if r['id'] == candidate['id']), None)
            if root and root.get('course_review') and root['course_review']['ready']:
                rc, ack = send(root['course_review']['text'])
                if rc == 0 and ack.get('message_id'):
                    claim(conn, root, 'standalone', now.isoformat())
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def cli(kit, argv):
    """Claim a conversation question immediately before relaying it."""
    if len(argv) != 2:
        raise ValueError('usage: experience-review-ask YYYY-MM-DD COURSE_ID')
    import experience_retro as E
    conn = kit.get_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        root = next((r for r in E.listing(kit, conn, argv[0]) if r['id'] == int(argv[1])), None)
        if not root:
            raise ValueError('course missing')
        claim(conn, root, 'conversation', kit.now_utc())
        conn.commit()
        print(json.dumps(root['course_review'], ensure_ascii=True))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reply_context(db_path, prompt):
    """Bind an explicit reply bubble to its outstanding course, read-only.

    The hook prevents a separate-message answer from becoming a new activity
    or a meal log. Unrelated recent outbound cards never bind a reply.
    """
    import retro_session
    card = retro_session.reply_card(prompt)
    if not card or retro_session.resolve(prompt) is not None:
        return ''
    words = prompt.rsplit(retro_session.END, 1)[-1].strip()
    if not words:
        return ''
    clean = ' '.join(html.unescape(re.sub(r'<[^>]*>', '', card)).split())
    try:
        conn = sqlite3.connect(Path(db_path).absolute().as_uri() + '?mode=ro', uri=True)
        try:
            rows = conn.execute("SELECT e.id,c.value FROM config c JOIN event e "
                "ON c.key='experience_course_review.'||e.ulid").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return ''
    matches = []
    for eid, raw in rows:
        saved = json.loads(raw)
        first = (saved.get('text') or '').split('\n', 1)[0]
        if first and not saved.get('answered_at') and ' '.join(first.split()) in clean:
            matches.append((eid, saved.get('day')))
    if len(matches) != 1:
        return ''
    eid, day = matches[0]
    return ('[course-review] This is the answer to the offered course review. '
            'Read brief-session reference/experiences.md. FIRST run '
            'database/kit_life.sh experience-enrich ' + str(eid) + ' ' +
            shlex.quote('reaction=' + words) + ' ' + shlex.quote('verbatim=' + words) +
            '. The kit routes explicit stop opinions verbatim. Do not capture '
            'another experience or treat review food words as a new meal. '
            'Refresh experience-retro-list ' + (day or 'for the bound course day') +
            '; do not ask the review again.')
