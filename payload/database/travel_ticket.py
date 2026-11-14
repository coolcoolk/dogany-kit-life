"""Fixed-departure tickets as a leg, boarding buffer, and ride.

Ticket metadata lives on the ride block.  The other two rows are ordinary
derived travel rows, so the existing calendar mirror treats all three exactly
like the travel/buffer rows it already owns.  Preferences use config because
the kit has one owner; bus and train intentionally share one key.
"""
import argparse
import json


KINDS = {
    "bus": ("bus_train", 30, "🚌", "버스", "bus"),
    "train": ("bus_train", 30, "🚆", "기차", "train"),
    "plane-domestic": ("plane_domestic", 60, "✈️", "비행기", "plane"),
    "plane-international": ("plane_international", 150, "✈️", "비행기", "plane"),
}
PREF = "ticket.departure_buffer."
META = "fixed_departure_ticket"


def _attrs(conn, block_id, values):
    conn.executemany(
        "INSERT INTO block_attr(block_id,attr_token,value,source) VALUES(?,?,?,'module')",
        [(block_id, key, str(value)) for key, value in values.items()])


def _event_buffer(conn, ride_id):
    row = conn.execute(
        "SELECT value FROM block_attr WHERE block_id=? AND attr_token='appointment_buffer' "
        "AND source='user'", (ride_id,)).fetchone()
    if not row:
        return None
    try:
        value = json.loads(row[0])
        return int(value["minutes"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def buffer_minutes(K, conn, ride_id, kind):
    """event > ticket situation > owner default > product 15."""
    event = _event_buffer(conn, ride_id)
    if event is not None:
        return event
    group, default, _, _, _ = KINDS[kind]
    stored = K._config_stored(conn, PREF + group)
    if stored is not None:
        return int(stored)
    owner = K._config_stored(conn, "appointment_buffer.rule.default")
    if owner:
        try:
            return int(json.loads(owner)["minutes"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            pass
    return 15


def set_buffer(K, conn, kind, minutes):
    group = "bus_train" if kind in ("bus", "train", "bus_train") else kind.replace("-", "_")
    if group not in ("bus_train", "plane_domestic", "plane_international"):
        raise ValueError("kind must be bus|train|bus_train|plane-domestic|plane-international")
    if minutes < 0:
        raise ValueError("minutes must be nonnegative")
    with conn:
        K._config_put(conn, PREF + group, str(minutes))


def ask_once(K, conn, kind):
    group, default, _, _, family = KINDS[kind]
    if K._config_stored(conn, PREF + group) is not None:
        return None
    # Persist the stated default at first use. A subsequent typed answer simply
    # replaces it through ticket-buffer-set; the question is never repeated.
    set_buffer(K, conn, group, default)
    if family == "plane":
        return {"text": K._i18n("ticket.buffer.ask.plane"),
                "buttons": [K._i18n("ticket.buffer.button.default"),
                            K._i18n("ticket.buffer.button.custom")]}
    return {"text": K._i18n("ticket.buffer.ask.ground"),
            "buttons": [K._i18n("ticket.buffer.button.30"),
                        K._i18n("ticket.buffer.button.custom")]}


def _insert(K, conn, title, start, end, ride_id, role):
    cur = conn.execute(
        "INSERT INTO block(ulid,source_module,title,start_at,end_at,kind_token,derived_from) "
        "VALUES(?,?,?,?,?,'appointment',?)",
        (K.new_ulid(), "schedule", title, start, end, ride_id))
    bid = cur.lastrowid
    _attrs(conn, bid, {"schedule_kind": "timed", "display_tz": K.TRAVEL_DISPLAY_TZ,
                      "block_class": "travel", "derived_role": role,
                      "owning_agent": K._facade_agent(), "created_by": K._facade_agent()})
    return bid


def relay(K, conn, ride_id):
    row = conn.execute(
        "SELECT b.id,b.start_at,b.end_at,a.value FROM block b JOIN block_attr a ON a.block_id=b.id "
        "WHERE (b.id=? OR b.ulid=?) AND a.attr_token=? AND b.settled_at IS NULL",
        (str(ride_id), str(ride_id), META)).fetchone()
    if not row:
        raise ValueError("live ticket not found")
    bid, depart, arrive, raw = row
    data = json.loads(raw)
    minutes = buffer_minutes(K, conn, bid, data["kind"])
    buffer_start = K._iso_shift_min(depart, -minutes)
    leg_start = K._iso_shift_min(buffer_start, -int(data["travel_min"]))
    _, _, _, label_ko, _ = KINDS[data["kind"]]
    label = K._i18n("ticket.kind." + data["kind"], label_ko)
    with conn:
        old = conn.execute("SELECT id FROM block WHERE derived_from=? AND settled_at IS NULL", (bid,)).fetchall()
        for (old_id,) in old:
            conn.execute("UPDATE block SET settled_at=?,settled_outcome='abandoned',settled_by='system',"
                         "settled_verdict='machine' WHERE id=?", (K.now_utc(), old_id))
        leg_title = K._i18n("ticket.title.leg").format(origin=data["origin"], terminal=data.get("terminal_place", data["terminal"]))
        buffer_title = K._i18n("ticket.title.buffer").format(terminal=data["terminal"], kind=label)
        leg_id = _insert(K, conn, leg_title, leg_start, buffer_start, bid, "ticket_leg")
        buffer_id = _insert(K, conn, buffer_title, buffer_start, depart, bid, "ticket_buffer")
    return {"ride_id": bid, "leg_id": leg_id, "buffer_id": buffer_id,
            "buffer_min": minutes, "start": leg_start}


def render(K, conn, ride_id):
    """Literal calendar-card rendering of a ticket's three mirrored rows."""
    ride = conn.execute("SELECT id FROM block WHERE id=? OR ulid=?", (str(ride_id), str(ride_id))).fetchone()
    if not ride:
        raise ValueError("ticket not found")
    rows = conn.execute(
        "SELECT title,start_at,end_at FROM block WHERE (id=? OR derived_from=?) "
        "AND settled_at IS NULL ORDER BY start_at,id", (ride[0], ride[0])).fetchall()
    return "\n".join("%s %s~%s" % (title, K._day_hm(start, K._day_tz(K.TRAVEL_DISPLAY_TZ, None)),
                                     K._day_hm(end, K._day_tz(K.TRAVEL_DISPLAY_TZ, None)))
                     for title, start, end in rows)


def add(K, conn, args):
    group, _, icon, _, _ = KINDS[args.kind]
    ask = ask_once(K, conn, args.kind)
    hm = K._day_hm(args.depart, K._day_tz(K.TRAVEL_DISPLAY_TZ, None))
    title = K._i18n("ticket.title.ride").format(icon=icon, terminal=args.terminal,
                                                time=hm, destination=args.destination)
    data = {"kind": args.kind, "origin": args.origin, "terminal": args.terminal,
            "destination": args.destination, "travel_min": args.travel_min,
            "terminal_place": args.terminal_place or args.terminal}
    with conn:
        cur = conn.execute(
            "INSERT INTO block(ulid,source_module,title,start_at,end_at,kind_token) "
            "VALUES(?,?,?,?,?,'appointment')",
            (K.new_ulid(), "schedule", title, args.depart, args.arrive))
        ride_id = cur.lastrowid
        _attrs(conn, ride_id, {"schedule_kind": "timed", "display_tz": K.TRAVEL_DISPLAY_TZ,
                              "block_class": "travel", "derived_role": "ticket_ride",
                              "owning_agent": K._facade_agent(), "created_by": K._facade_agent(),
                              META: json.dumps(data, ensure_ascii=False)})
    result = relay(K, conn, ride_id)
    result["ask"] = ask
    return result


def _parser(verb):
    p = argparse.ArgumentParser(prog="kit_life.sh " + verb)
    if verb == "ticket-add":
        p.add_argument("--kind", choices=tuple(KINDS), required=True)
        p.add_argument("--origin", required=True); p.add_argument("--terminal", required=True)
        p.add_argument("--terminal-place")
        p.add_argument("--destination", required=True); p.add_argument("--depart", required=True)
        p.add_argument("--arrive", required=True); p.add_argument("--travel-min", type=int, required=True)
    elif verb == "ticket-buffer-set":
        p.add_argument("kind"); p.add_argument("minutes", type=int)
    else:
        p.add_argument("ticket")
    return p


def cli(K, verb, argv):
    ns = _parser(verb).parse_args(argv)
    conn = K.event_conn()
    try:
        if verb == "ticket-add":
            result = add(K, conn, ns)
        elif verb == "ticket-buffer-set":
            set_buffer(K, conn, ns.kind, ns.minutes); result = {"minutes": ns.minutes}
        else:
            result = relay(K, conn, ns.ticket)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        conn.close()

