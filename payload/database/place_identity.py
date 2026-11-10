"""Confirmation joins place identity to the person registry, before minting."""
import json
import re
import sqlite3

RELATIONS = {
    '남친': ('partner', '남자친구'), '남자친구': ('partner', '남친'),
    '여친': ('partner', '여자친구'), '여자친구': ('partner', '여친'),
    '엄마': ('family', '어머니'), '어머니': ('family', '엄마'),
    '아빠': ('family', '아버지'), '아버지': ('family', '아빠'),
    '남편': ('partner', '배우자'), '아내': ('partner', '배우자'),
}


def ready(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='place_identity_ask'").fetchone() is not None


def compact(value):
    return re.sub(r'[\s,·]', '', str(value or '')).casefold()


def occupant(name):
    match = re.fullmatch(r'(.+?)(?:님\s*집|네\s*집|네|\s+집)', name.strip())
    return match[1].strip() if match else None


def relation_word(name):
    key = compact(name)
    return next((w for w in RELATIONS if key in (w+'집', w+'네', w+'네집')), None)


def homes(k, conn):
    """Explicit registry links plus existing residence/name evidence; no writes."""
    people = conn.execute('SELECT id,name,relation,aliases,residence FROM persons').fetchall()
    links = set(conn.execute('SELECT person_id,place_id FROM person_home').fetchall())
    places = k._near_rows_place(conn)
    result = []
    for row in places:
        place = k.travel_place_get(conn, row[0])
        who = occupant(place['name'])
        matches = []
        for p in people:
            names = [p[1]] + (p[3] or '').split(',')
            if ((p[0], place['id']) in links
                    or (who and compact(who) in map(compact, names))
                    or (p[4] and compact(p[4]) in (compact(place['name']), compact(place.get('address'))))):
                matches.append(p)
        if who or matches:
            result.append((place, matches, who))
    return result


def candidate(k, conn, query, address=None, lat=None, lng=None):
    if not ready(conn):
        return None
    all_homes = homes(k, conn)
    word = relation_word(query)
    person_key = occupant(query)
    candidates = []
    # Address equality includes secondary road/parcel addresses. Do not strip
    # unit numbers or floor information: two units in one building can differ.
    for r in k._near_rows_place(conn):
        p = k.travel_place_get(conn, r[0])
        addresses = [p.get('address')]
        if k._place_split_ready(conn):
            addresses += [a[0] for a in conn.execute('SELECT value FROM place_address WHERE place_id=?', (p['id'],))]
        same = bool(address and compact(address) in [compact(a) for a in addresses if a])
        if not same and lat is not None and lng is not None:
            try:
                latitude, longitude = float(lat), float(lng)
            except (TypeError, ValueError):
                continue
            c = conn.execute('SELECT lat,lng FROM place_coord WHERE place_id=?', (p['id'],)).fetchone()
            same = bool(c and c[0] is not None and abs(latitude-c[0]) < 0.00001 and abs(longitude-c[1]) < 0.00001)
        if same:
            candidates.append((p, 'address'))
    if not candidates and person_key:
        for p, people, who in all_homes:
            if any(compact(person_key) in map(compact, [x[1]]+(x[3] or '').split(',')) for x in people):
                candidates.append((p, 'person'))
    if not candidates and word:
        exact = [(p, 'person') for p, people, _ in all_homes
                 if any(word in (x[3] or '').split(',') or RELATIONS[word][1] in (x[3] or '').split(',') for x in people)]
        # A generic partner/family category can include several people. Only
        # a unique candidate may be offered, and is still never auto-accepted.
        related = [(p, 'person') for p, people, _ in all_homes
                   if any(x[2] == RELATIONS[word][0] for x in people)]
        candidates = exact or related
        if not candidates and len(all_homes) == 1:
            candidates = [(all_homes[0][0], 'person')]
    candidates = list({p['id']: (p, why) for p, why in candidates}.values())
    for p, why in candidates:
        old = conn.execute('SELECT state FROM place_identity_ask WHERE query=? AND place_id=?', (query, p['id'])).fetchone()
        if old and old[0] == 'no':
            continue
        return p, why
    return None


def gate(k, conn, query, address=None, lat=None, lng=None, payload=None):
    address = address or (query if re.search(r"(?:로|길)\s*\d", query) else None)
    hit = candidate(k, conn, query, address, lat, lng)
    if not hit:
        return False
    p, why = hit
    conn.execute('INSERT OR IGNORE INTO place_identity_ask(query,place_id,address,payload) VALUES(?,?,?,?)', (query,p['id'],address,json.dumps(payload or {})))
    conn.commit()
    ask = conn.execute('SELECT id FROM place_identity_ask WHERE query=? AND place_id=?', (query,p['id'])).fetchone()[0]
    key = 'place.identity.same_address' if why == 'address' else 'place.identity.same_home'
    fallback = '{name}과 주소가 같아요. 같은 곳인가요?' if why == 'address' else '{query}이 {name}인가요?'
    print('PLACE_IDENTITY_ASK\t%s\t%s\t%s' % (ask,p['id'],query))
    print('ASK\t'+k._i18n(key, fallback).format(name=p['name'],query=query))
    print('ANSWER\tplace-identity-answer\t%s\tyes|no' % ask)
    return True


def teach(k, conn, place, query):
    """Caller owns transaction. No committing person/place helper is used."""
    query = k._place_norm_name(query)
    other = k._resolve_location_place(conn, query)
    if other is not None and other['id'] != place['id']:
        raise ValueError('This name now belongs to another place; confirmation is stale.')
    aliases = k._near_json_list(conn.execute('SELECT aliases FROM place WHERE id=?',(place['id'],)).fetchone()[0])
    if query != place['name'] and query not in aliases:
        aliases.append(query)
    conn.execute('UPDATE place SET aliases=? WHERE id=?',(json.dumps(aliases,ensure_ascii=False),place['id']))
    word = relation_word(query)
    person_alias = occupant(query)
    if not word and not person_alias:
        return
    matches = next(((people,who) for p,people,who in homes(k,conn) if p['id']==place['id']),([],None))
    people, who = matches
    if len(people) > 1:
        specific = [p for p in people if word and
                    (word in (p[3] or '').split(',') or RELATIONS[word][1] in (p[3] or '').split(','))]
        named = [p for p in people if who and compact(who) in map(compact, [p[1]]+(p[3] or '').split(','))]
        people = specific or named or people
        if len(people) > 1:
            raise ValueError('Home belongs to several people; select a person before teaching this relation.')
    if not people and who:
        pid = conn.execute('INSERT INTO persons(name) VALUES(?)',(who,)).lastrowid
        people = [(pid,who,None,None,None)]
    if not people:
        raise ValueError('No person linked to this home; link the person before confirming.')
    p = people[0]
    aliases = [a.strip() for a in (p[3] or '').split(',') if a.strip()]
    for alias in ((word, RELATIONS[word][1]) if word else (person_alias,)):
        if alias != p[1] and alias not in aliases:
            aliases.append(alias)
    conn.execute('UPDATE persons SET relation=?,aliases=? WHERE id=?',(RELATIONS[word][0] if word else p[2],','.join(aliases),p[0]))
    conn.execute('INSERT OR IGNORE INTO person_home VALUES(?,?)',(p[0],place['id']))


def answer(k, argv):
    if len(argv)!=2 or argv[1] not in ('yes','no'):
        k._err('usage: place-identity-answer <ask id> yes|no')
    conn = k.event_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT query,place_id,address,state,payload FROM place_identity_ask WHERE id=?',(argv[0],)).fetchone()
        if not row:
            raise ValueError('Unknown place confirmation')
        query,pid,address,state,payload = row
        if state != 'pending' and state != argv[1]:
            raise ValueError('Confirmation already answered')
        if argv[1]=='yes':
            teach(k,conn,k.travel_place_get(conn,pid),query)
        conn.execute('UPDATE place_identity_ask SET state=? WHERE id=?',(argv[1],argv[0]))
        conn.commit()
        if argv[1]=='no' and k._resolve_location_place(conn,query) is None:
            # Explicit no is the only path that permits this identity twin.
            kv = json.loads(payload or '{}')
            args = {key:kv[key] for key in ('to_min','from_min','prep_min','note') if key in kv}
            if kv.get('aliases'):
                args['aliases'] = kv['aliases'].split('|')
            place = k.travel_place_add(conn,query,address=address,**args)
            k._place_coord_from_kv(conn,place['id'],kv)
        for ulid,location in conn.execute('SELECT ulid,location FROM timeblock WHERE location=? AND settled_at IS NULL AND block_class IS NULL',(query,)).fetchall():
            k._travel_autoattach_appt(conn,ulid,location)
        print('PLACE_IDENTITY_ANSWER\t%s\t%s' % (argv[0],argv[1]))
    except (ValueError,sqlite3.Error) as exc:
        conn.rollback()
        k._err(str(exc))
    finally:
        conn.close()
