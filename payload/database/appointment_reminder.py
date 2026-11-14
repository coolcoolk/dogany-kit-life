"""Complete at-start and two-hour appointment notices; read-only renderers."""
import datetime
import html
import json
import re
import sqlite3
from urllib.parse import quote
from pathlib import Path
from zoneinfo import ZoneInfo

import person_labels
import relmod
from place_presentation import (clean, detail as place_presentation_detail,
                                split_legacy_location)

_FMT = '%Y-%m-%dT%H:%M:%SZ'
SENT_KEY = 'appointment_reminder_sent'


def memo(text):
    """Keep ordinary notes whole; long ones end after two whole sentences.

    An unpunctuated long note stays whole: there is no honest sentence cut.
    Neither a character slice nor a synthetic ellipsis is allowed.
    """
    return relmod.brief_text(text)


def words(text):
    return {w.casefold() for w in re.findall(r'[^\W_]+', clean(text)) if len(w) >= 2}


def split_address(name):
    """Compatibility export for callers of the legacy appointment helper."""
    return split_legacy_location(name)


def place_detail(conn, place_id, location):
    """Return display name, address and an owner-approved Naver link."""
    row = conn.execute(
        "SELECT COALESCE(NULLIF(display_name,''),name),address FROM place WHERE id=?",
        (place_id,)).fetchone() if place_id else None
    stored_name = row[0] if row else ''
    stored_address = row[1] if row else ''
    alternate_address = ''
    if place_id:
        try:
            alternate_address = conn.execute(
                "SELECT value FROM place_address WHERE place_id=? AND kind!='map_url' "
                "ORDER BY is_primary DESC,id LIMIT 1", (place_id,)).fetchone()[0]
        except (sqlite3.Error, TypeError):
            pass
    # Legacy rows (place 40) stored "label address" in one name. Split at
    # display time where the address begins; storage stays read-only.
    name, address = place_presentation_detail(
        stored_name, location, stored_address, alternate_address)
    try:
        if place_id:
            import kit_life
            url = kit_life.travel_place_link(conn, place_id, 'naver')
        else:
            url = 'https://map.naver.com/p/search/' + quote(address or name, safe='')
    except (sqlite3.Error, ValueError):
        url = 'https://map.naver.com/p/search/' + quote(address or name, safe='')
    return name, address, url


def place_name(conn, place_id, location):
    return place_detail(conn, place_id, location)[0]


def place_line(conn, place_id, location):
    name, address, url = place_detail(conn, place_id, location)
    if not name:
        return ''
    return '\U0001f4cd [%s](%s)%s' % (name, url, (' `%s`' % address) if address else '')


def history(conn, rel, pid, now, today, anchor):
    rows = conn.execute(
        'SELECT e.id,e.start_at,e.display_tz,e.title,e.location,e.place_id,'
        'e.retro_line,e.summary FROM timeblock e '
        'JOIN event_persons ep ON ep.event_id=e.id '
        "WHERE ep.person_id=? AND e.kind='appointment' AND e.start_at<? "
        "AND COALESCE(e.settled_outcome,'')!='abandoned' "
        'ORDER BY e.start_at DESC,e.id DESC', (pid, now)).fetchall()
    meetings = []
    for bid, start, tz, title, location, place_id, retro, summary in rows:
        location = place_name(conn, place_id, location)
        overlays = []
        try:
            overlays = conn.execute(
                'SELECT x.reaction,x.note FROM event x JOIN event_timeblock et '
                'ON et.event_id=x.id WHERE et.timeblock_id=? '
                'ORDER BY x.created_at DESC,x.id DESC', (bid,)).fetchall()
        except sqlite3.Error:
            pass
        text = next((t for t in [retro] + [r[0] for r in overlays] +
                     [summary] + [r[1] for r in overlays] if clean(t)), '')
        attendees = person_labels.participant_lines(conn, bid)
        meetings.append(dict(id=bid, start=start, day=relmod.local_day(start, tz),
                             title=clean(title), place=clean(location), location=location,
                             place_id=place_id, people=attendees, memo=memo(text)))
    if rel is not None:
        try:
            for tid, day, note in rel.execute(
                    "SELECT id,touched_on,note FROM touches WHERE person_id=? "
                    "AND kind='meet' AND tombstone=0 AND touched_on<? "
                    'ORDER BY touched_on DESC,id DESC', (pid, today)):
                if not any(m['day'] == day for m in meetings):
                    meetings.append(dict(id='touch:%s' % tid, start=day, day=day,
                        title=clean(note), place='', location='', place_id=None,
                        people=[], memo=''))
        except sqlite3.Error:
            pass
    meetings.sort(key=lambda m: m['start'], reverse=True)
    if len(meetings) < 2:
        return meetings
    def score(m):
        same_place = bool((anchor['place_id'] and m['place_id'] == anchor['place_id']) or
                          (anchor['place'] and m['place'] == anchor['place']))
        return (int(same_place), len(words(anchor['title']) & words(m['title'])))
    relevant = max(meetings, key=score)
    return ([meetings[0], relevant] if relevant is not meetings[0] and any(score(relevant))
            else [meetings[0]])


def past_lines(conn, rel, eid, now, today, anchor, tr):
    ids = [r[0] for r in conn.execute(
        'SELECT p.id FROM persons p JOIN event_persons ep ON ep.person_id=p.id '
        'WHERE ep.event_id=? ORDER BY p.name,p.id LIMIT 3', (eid,))]
    if not ids:
        return []
    names = [person_labels.person(conn, pid)[0] for pid in ids]
    final = ord(names[-1][-1]) if names and names[-1] else 0
    key = ('remind.people_past_consonant' if 0xAC00 <= final <= 0xD7A3
           and (final - 0xAC00) % 28 else 'remind.people_past')
    lines = ['**' + tr(key, fallback='Past meetings with {names}').format(
        names=', '.join(names)) + '**']
    selected = {}
    for pid in ids:
        meetings = history(conn, rel, pid, now, today, anchor)
        for m in meetings:
            selected[m['id']] = m
    if not selected:
        lines.append('> ' + tr('remind.person_no_history', fallback='No record'))
    items = []
    for m in sorted(selected.values(), key=lambda x: x['start'], reverse=True):
        item = []
        # Owner-approved meeting header: '2025 2/14(금)' (no year suffix).
        date = relmod.brief_date(m['day'], today, lambda k, fallback='': tr(
            'remind.meeting_date_year', fallback='{year} {date}') if k == 'remind.person_date_year'
            else tr(k, fallback=fallback))
        where = place_line(conn, m['place_id'], m['location'])
        title = date + (' ' + m['title'].replace('·', '/') if m['title'] else '')
        if where:
            title += ' / ' + where.removeprefix('📍 ')
        item.append('**' + title + '**')
        if m['people']:
            item.extend(m['people'])
        if m['memo']:
            item.append(m['memo'])
        items.append('\n'.join(item))
    if items:
        from item_grouping import markdown_items
        lines.append(markdown_items(items))
    return lines


def prep_tasks(conn, eid, now, start, anchor, tz):
    # Explicit block links, or blocks in the same declared experience tree.
    linked = set()
    try:
        linked = {r[0] for r in conn.execute(
            'WITH RECURSIVE family(id) AS ('
            'SELECT event_id FROM event_timeblock WHERE timeblock_id=? UNION '
            'SELECT e.parent_id FROM event e JOIN family f ON e.id=f.id '
            'WHERE e.parent_id IS NOT NULL UNION '
            'SELECT e.id FROM event e JOIN family f ON e.parent_id=f.id) '
            'SELECT et.timeblock_id FROM event_timeblock et JOIN family f ON f.id=et.event_id',
            (eid,))}
    except sqlite3.Error:
        pass
    out = []
    for bid, title, sa, owner, place_id, location, dfrom, depends, bclass in conn.execute(
            'SELECT id,title,start_at,owning_agent,place_id,location,derived_from,'
            "(SELECT value FROM block_attr a WHERE a.block_id=timeblock.id "
            "AND a.attr_token='placement_depends_on'),block_class FROM timeblock "
            "WHERE kind='task' AND settled_at IS NULL AND schedule_kind='timed' "
            'AND start_at>=? AND start_at<? ORDER BY start_at,id', (now, start)):
        if owner != anchor['owner'] or bclass == 'travel':
            continue
        same_place = bool((anchor['place_id'] and place_id == anchor['place_id']) or
                          (anchor['place'] and place_name(conn, place_id, location) == anchor['place']))
        is_linked = bid in linked or bool({str(eid), anchor['ulid']} & {str(dfrom), str(depends)})
        if not (same_place or is_linked or words(title) & words(anchor['title'])):
            continue
        clock = datetime.datetime.strptime(sa, _FMT).replace(
            tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(tz)).strftime('%H:%M')
        out.append('\u2022 %s %s' % (clock, clean(title)))
    return out


def appointment_title(conn, eid, title):
    names = set()
    for (pid,) in conn.execute('SELECT person_id FROM event_persons WHERE event_id=?', (eid,)):
        name, _emoji = person_labels.person(conn, pid)
        names.add(name)
        raw = conn.execute('SELECT name,aliases FROM persons WHERE id=?', (pid,)).fetchone()
        names.add(raw[0])
        try:
            aliases = json.loads(raw[1] or '[]')
        except (ValueError, TypeError):
            aliases = (raw[1] or '').replace('|', ',').split(',')
        if isinstance(aliases, list):
            names.update(a.strip() for a in aliases if isinstance(a, str))
        elif isinstance(aliases, str):
            names.add(aliases)
    parts = clean(title).split(' \u00b7 ')
    kept = [p for p in parts if p not in names]
    return ' \u00b7 '.join(kept) or clean(title)


def render_start(conn, eid, tz, tr):
    """The complete at-start notice; shared place and uncapped roster rules."""
    title, start, location, place_id = conn.execute(
        'SELECT title,start_at,location,place_id FROM timeblock WHERE id=?',
        (eid,)).fetchone()
    local = datetime.datetime.strptime(start, _FMT).replace(
        tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(tz or relmod.DEFAULT_DISPLAY_TZ))
    lines = [tr('remind.appointment_start', fallback='🚨 <b>Appointment starts</b>'),
             '<b>%s %s</b>' % (local.strftime('%H:%M'), html.escape(clean(title)))]
    name, address, url = place_detail(conn, place_id, location)
    if name:
        where = '📍 <a href="%s">%s</a>' % (html.escape(url, quote=True), html.escape(name))
        if address:
            where += ' <code>%s</code>' % html.escape(address)
        lines.append('<blockquote>' + where + '</blockquote>')
    people = person_labels.participant_lines(conn, eid, collapse_over=20)
    if people:
        from item_grouping import quote_items
        lines.append(quote_items(html.escape(p) for p in people))
    return '\n'.join(lines)


def render(conn, eid, now_ts, tz, db_path, tr):
    now = datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc).strftime(_FMT)
    row = conn.execute('SELECT title,start_at,location,place_id,owning_agent,ulid,retro_line,summary,end_at '
                       'FROM timeblock WHERE id=?', (eid,)).fetchone()
    title, start, location, place_id, owner, ulid, retro, summary, end = row
    anchor = dict(title=appointment_title(conn, eid, title),
                  place=place_name(conn, place_id, location),
                  place_id=place_id, owner=owner, ulid=ulid)
    local = datetime.datetime.strptime(start, _FMT).replace(
        tzinfo=datetime.timezone.utc).astimezone(ZoneInfo(tz or relmod.DEFAULT_DISPLAY_TZ))
    today = datetime.datetime.fromtimestamp(now_ts, local.tzinfo).date().isoformat()
    clock = local.strftime('%H:%M')
    if end and end != start:
        end_local = datetime.datetime.strptime(end, _FMT).replace(
            tzinfo=datetime.timezone.utc).astimezone(local.tzinfo)
        clock += '-' + end_local.strftime('%H:%M')
    heading = anchor['title'].replace('·', '/')
    name, address, _url = place_detail(conn, place_id, location)
    if name:
        heading += ' / ' + name + (' `%s`' % address if address else '')
    lines = [tr('remind.appointment_two_hours', fallback='\u23f0 **In 2 hours / {time} {title}**').replace('·', '/').format(
        time=clock, title=heading)]
    people = person_labels.participant_lines(conn, eid, collapse_over=20)
    if people:
        from item_grouping import markdown_items
        lines.append(markdown_items(people))
    current_memo = memo(retro or summary)
    if current_memo:
        lines.append(current_memo)
    rel = None
    try:
        path = db_path or next((r[2] for r in conn.execute('PRAGMA database_list') if r[1] == 'main'), '')
        if path:
            sidecar = Path(path).absolute().with_name('relationship.db')
            try:
                rel = sqlite3.connect(sidecar.as_uri() + '?mode=ro', uri=True)
            except sqlite3.Error:
                pass
        past = past_lines(conn, rel, eid, now, today, anchor, tr)
        if past:
            lines.append('')
            lines += past
    finally:
        if rel is not None:
            rel.close()
    # The selector polls within a two-minute window around the due instant.
    # A task at exactly 15:00 must survive a 15:00:35 poll of a 17:00 alert.
    due = (local.astimezone(datetime.timezone.utc) - datetime.timedelta(hours=2))
    task_since = now
    if 0 <= now_ts - due.timestamp() <= 120:
        task_since = due.strftime(_FMT)
    tasks = prep_tasks(conn, eid, task_since, start, anchor, str(local.tzinfo))
    if tasks:
        lines.append('**' + tr('remind.appointment_before_tasks', fallback='Before then') + '**')
        from item_grouping import markdown_items
        lines.append(markdown_items(tasks))
    return '\n'.join(lines)


def deliver(K, db_path, alert, now, push=None):
    """Own the complete message like start_card, avoiding a second shell header.
    Successful sends deduplicate by the existing appointment occurrence key.
    Failed/unavailable transport leaves the selector's ordinary fallback intact.
    """
    import start_card
    conn = None
    try:
        push = push if push is not None else K._photo_push_sh()
        if not push or not start_card._capable(push):
            return False
        conn = K.event_conn(db_path)
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT value FROM config WHERE key=?', (SENT_KEY,)).fetchone()
        sent = json.loads(row[0]) if row else {}
        sent = {k: v for k, v in sent.items() if v >= now[:10]}
        if alert['key'] in sent:
            conn.commit()
            return True
        args = ['--strict', '--text', alert['message']]
        if alert.get('links'):
            from remind_select import _buttons_json
            args += ['--buttons-json', _buttons_json(alert['links'])]
        rc, ack = start_card._push(push, args)
        if rc != 0 or not ack.get('message_id'):
            conn.rollback()
            return False
        sent[alert['key']] = alert['start_at'][:10]
        conn.execute('INSERT OR REPLACE INTO config(key,value) VALUES (?,?)',
                     (SENT_KEY, json.dumps(sent)))
        conn.commit()
        return True
    except (sqlite3.Error, ValueError, OSError):
        return False
    finally:
        if conn is not None:
            conn.close()


def main(argv=None):
    """Preview only: never posts or changes the selected database."""
    import argparse
    import kit_life as K
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', default=K.DB_PATH)
    ap.add_argument('--event', type=int, required=True)
    def instant(value):
        try:
            return int(value)
        except ValueError:
            try:
                date = datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
                if date.tzinfo is None:
                    raise ValueError('timezone required')
                return int(date.timestamp())
            except ValueError:
                raise argparse.ArgumentTypeError('use epoch seconds or ISO time with an offset')
    ap.add_argument('--now', type=instant, required=True,
                    help='epoch seconds or ISO time with an explicit offset')
    ap.add_argument('--tz', default=relmod.DEFAULT_DISPLAY_TZ)
    args = ap.parse_args(argv)
    import ro_open
    conn = ro_open.open_ro(args.db)
    try:
        K._emit_card_block([render(conn, args.event, args.now, args.tz, args.db, K._i18n)],
                           K._event_card_map_buttons(conn, [args.event], destination_fallback=True))
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
