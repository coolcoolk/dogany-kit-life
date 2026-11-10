"""Owner time is immutable until an explicit answer; proposals are data only."""
import datetime
import json
import hashlib
import sqlite3

PREFIX = 'owner_clash.'


def row(K, c, token):
    return K._travel_row(c, 'SELECT * FROM timeblock WHERE ulid=? OR id=?', (str(token), str(token)))


def day_off(r):
    """A placeless all-day stay is context, not a reservation of time."""
    return (r['kind'] == 'presence' and r['schedule_kind'] == 'all_day'
            and r.get('place_id') is None and not r.get('location'))


def elapsed(r, now):
    return bool(r.get('end_at') and r['end_at'] <= now)


def expire(K, c, p, r, now):
    if p.get('status') != 'pending' or not elapsed(r, now):
        return False
    p.update(status='expired', expired_at=now)
    try:
        K._config_put(c, PREFIX + p['ulid'], json.dumps(p, ensure_ascii=False))
        c.commit()
    except sqlite3.OperationalError as exc:
        # Read-only consumers still suppress elapsed questions; the next
        # writable sweep persists their lifecycle without deleting history.
        code = getattr(exc, 'sqlite_errorcode', None)
        # Python <3.11 lacks sqlite_errorcode and the SQLITE_* constants.
        readonly = (code & 255 == 8) if code is not None else 'readonly' in str(exc).lower()
        if not readonly:
            raise
    return True


def busy(K, c, r):
    # Presence is a busy band too: proposed alternatives must stay out of work.
    if day_off(r):
        return []
    rows = K._travel_rows(c, 'SELECT * FROM timeblock e WHERE settled_at IS NULL '
        'AND start_at IS NOT NULL AND ulid<>? AND start_at<? AND ?<' + K.EFF_END_SQL,
        (r['ulid'], K._cand_eff_end(r['start_at'],r['end_at'],r.get('open_ended',0)), r['start_at']))
    return [b for b in rows if not day_off(b)]


def free_slot(K, c, r):
    tz = K._day_tz(r.get('display_tz'), K._card_tz_offset())
    start = datetime.datetime.fromisoformat(r['start_at'].replace('Z', '+00:00'))
    end = datetime.datetime.fromisoformat(r['end_at'].replace('Z', '+00:00'))
    dur = end-start
    if dur.total_seconds() <= 0:
        return None
    day = start.astimezone(tz).date()
    lo = datetime.datetime.combine(day, datetime.time(), tzinfo=tz)
    hi = lo + datetime.timedelta(days=1)
    candidates = []
    at = lo
    while at + dur <= hi:
        sa = at.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        ea = (at+dur).astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        if sa > K.now_utc() and not busy(K,c,dict(r,start_at=sa,end_at=ea)):
            candidates.append((abs((at-start).total_seconds()), at < start, sa, ea))
        at += datetime.timedelta(minutes=15)
    return min(candidates)[2:] if candidates else None


def placement_target(K, c, r, target):
    """Apply the same presence guard when proposing and answering a placement."""
    if target and any(b['kind']=='presence' for b in busy(K,c,dict(r,start_at=target[0],end_at=target[1]))):
        return None
    return target


def propose(K, c, r, blockers=(), target=None, placement=False):
    key = PREFIX + r['ulid']
    old = K._config_stored(c,key)
    old = json.loads(old) if old else {}
    now = K.now_utc()
    expire(K, c, old, r, now)
    if (r.get('settled_at') or r.get('block_class') or not r.get('start_at')
            or day_off(r) or elapsed(r, now)):
        return None
    blockers = [b for b in blockers if not day_off(b)]
    placement = placement or bool(target)
    if not placement and not blockers:
        return None
    target=placement_target(K,c,r,target)
    signature = [r['version'], r['start_at'], r['end_at'], sorted(b['ulid'] for b in blockers), target]
    if old.get('signature') == signature and bool(old.get('placement')) == placement:
        return old
    slot = (target if placement else free_slot(K,c,r)) if r.get('end_at') else None
    proposal = dict(id=r['id'], ulid=r['ulid'], title=r['title'], signature=signature,
                    slot=slot, placement=placement, status='pending', blockers=[b['title'] for b in blockers])
    K._config_put(c,key,json.dumps(proposal,ensure_ascii=False))
    c.commit()
    return proposal


def discover(K,c,anchor_id=None):
    pending(K, c)  # Expire history even when no future anchors remain.
    sql = "SELECT * FROM timeblock WHERE settled_at IS NULL AND block_class IS NULL AND start_at>?"
    sql += ' AND id=?' if anchor_id is not None else " AND kind='appointment'"
    anchors = K._travel_rows(c, sql, (K.now_utc(),anchor_id) if anchor_id is not None else (K.now_utc(),))
    found = []
    for anchor in anchors:
        for r in busy(K,c,anchor):
            if r.get('block_class') or r['kind'] not in ('task','presence','appointment'):
                continue
            blockers=[b for b in busy(K,c,r) if (b['kind']=='appointment' or b['ulid']==anchor['ulid']) and not b.get('block_class')]
            proposal = propose(K,c,r,blockers)
            if proposal:
                found.append(proposal)
    return found


def pending(K,c,date=None,anchor_id=None,now=None):
    now = now or K.now_utc()
    out=[]
    for raw, in c.execute('SELECT value FROM config WHERE key LIKE ?', (PREFIX+'%',)).fetchall():
        p=json.loads(raw)
        r=row(K,c,p['ulid'])
        if r and expire(K, c, p, r, now):
            continue
        if p['status']!='pending' or not r or r['settled_at']:
            continue
        if day_off(r):
            continue
        if [r['version'],r['start_at'],r['end_at']]!=p['signature'][:3]:
            continue
        if p['signature'][3] and not any(b['ulid'] in p['signature'][3] for b in busy(K,c,r)):
            continue
        if date and K._travel_local_date(r['start_at'],r.get('display_tz'))!=date:
            continue
        if anchor_id is not None:
            anchor=row(K,c,anchor_id)
            if not anchor or anchor['ulid'] not in p['signature'][3]:
                continue
        out.append(p)
    return out


def lines(K,c,p):
    choices=[('cancel',K._i18n('clash.cancel','Cancel this occurrence'))]
    if p['slot']:
        r=row(K,c,p['ulid']); tz=K._day_tz(r.get('display_tz'),K._card_tz_offset())
        time=K._day_hm(p['slot'][0],tz)+'-'+K._day_hm(p['slot'][1],tz)
        choices.append(('move',K._i18n('clash.move','Move to {time}').format(time=time)))
    choices.append(('keep',K._i18n('clash.keep','Keep both')))
    ac=K._ask_choices()
    question=K._i18n('clash.ask','{title} overlaps another block. What should I do?').format(title=p['title'])
    return ['CLASH_ASK\t%s\t%s\t%s'%(p['ulid'],question,ac.choices_field(choices)),
            ac.options_line('CLASH_ASK',p['ulid'],choices, ask=hashlib.sha256(json.dumps(p['signature']).encode()).hexdigest()[:16])]


def answer(K,c,token,value,expected_ask=None):
    r=row(K,c,token)
    raw=K._config_stored(c,PREFIX+str(token))
    if not r or not raw:
        raise ValueError('no pending clash')
    p=json.loads(raw)
    if expire(K, c, p, r, K.now_utc()):
        raise ValueError('stale clash; refresh the question')
    if expected_ask and expected_ask != hashlib.sha256(json.dumps(p['signature']).encode()).hexdigest()[:16]:
        raise ValueError('stale clash; refresh the question')
    if p['signature'][3] and not all(any(b['ulid']==u for b in busy(K,c,r)) for u in p['signature'][3]):
        raise ValueError('clash changed; refresh the question')
    if p['status']!='pending' or r['settled_at'] or [r['version'],r['start_at'],r['end_at']]!=p['signature'][:3]:
        raise ValueError('stale clash; refresh the question')
    if value=='move':
        slot=p['slot']
        now=K.now_utc()
        if p.get('placement'):
            rr=K._roller_mod()
            defn=K.routine_def_get(c,r['recurrence_id'])
            target=None
            if defn is not None:
                try:
                    # Use the proposal's selector with the answer-time boundary.
                    current=rr.def_candidate(c,defn,r['rec_date'],now=now)
                    if current['start_at'] > now:
                        target=[current['start_at'],current['end_at']]
                except ValueError:
                    pass  # No candidate remains; ask without a move choice.
            target=placement_target(K,c,r,target)
            valid=slot and list(slot)==target
            if not valid:
                refreshed=propose(K,c,r,target=target,placement=True)
                for line in lines(K,c,refreshed):
                    print(line)
        else:
            valid=slot and slot[0]>now and not busy(K,c,dict(r,start_at=slot[0],end_at=slot[1]))
        if not valid:
            raise ValueError('proposed slot is no longer free; refresh the question')
        result=K.event_move(c,r['id'],r['version'],slot[0],slot[1])
    elif value=='cancel':
        result=K.cancel(c,r['id'],r['version'],'owner-clash-answer')
    elif value=='keep':
        result=K.MutationResult.APPLIED
    else:
        raise ValueError('clash answer: cancel | move | keep')
    if result!=K.MutationResult.APPLIED:
        raise ValueError('clash write refused: '+str(result))
    if value=='move' and r.get('recurrence_id'):
        K._tb_update(c,r['id'],dict(rec_exception=1));c.commit()
    p['status']=value;K._config_put(c,PREFIX+r['ulid'],json.dumps(p,ensure_ascii=False));c.commit()
    if value!='keep':
        K.appt_rederive(c,r['ulid'],before=r,declared=True)
        K._outbox_enqueue_best_effort(r['ulid'])
    print('CLASH_SET\t%s\t%s'%(r['ulid'],value))
    K._print_cards(c,[r['id']])
