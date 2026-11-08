"""start_card.py -- task/routine lead and START reminder cards.

Owner decisions 2026-10-11 10:07 KST and 2026-10-20 (cancel): every task and
routine occurrence ("<task start header>" / "<title> <dur>") carries four
buttons from the start, in this order: [snooze] [help me start] [done] [cancel]
(ko: mi-ru-gi / do-um-bat-gi / wan-ryo / chwi-so; remind.start.btn.*). No
second message at the end time. Lead reminders carry [snooze] [done] [cancel]
(owner 2026-10-14; cancel added 2026-10-26, same order as START minus help).
A lead cancel tap behaves exactly like cancel on the start card. A lead tap suppresses the original start alert; an
untapped lead loses its keyboard when the start card posts.
Travel / prep / errand alerts are not
cards: they keep their own map buttons (remind_select helper rows).

DELIVERY. remind_select.py (the framework remind.sh's selector) hands every
due lead/start alert of a plain task/routine row to deliver(): the kit posts the
card itself through the code push path -- routines/push.sh --strict
--buttons-json (opt: callbacks, the same lane as the photo-done and retro
card buttons) -- and keeps the returned message_id. A posted card is not
emitted to remind.sh (its row would be a second copy). No push.sh, no
strict+edit capability, or a refused send -> the row goes to remind.sh as
before (text only): an alert is never lost to the buttons.

CALLBACK CONTRACT. The bridge's opt: route replays the tapped button's TEXT
as the owner's next message (bridge options.resolve_choice) and records the
tapped bubble in .telegram_bot/inbound-context.json (source "callback",
message_id). The agent answers such a tap with
    kit_life.sh start-tap "<tapped text>"
which binds the tap to exactly one occurrence by the bubble's message_id
(the card stores block id + ulid + start instant) and maps the text through
that card's LIVE keyboard {label: action}. Every keyboard change replaces
the live map, so a tap on a spent / replaced keyboard is a no-op (SPENT):
done settles once, a snooze choice applies once, help answers once.

    done      -> force_settle(owner-chat) of that block id (the chat
                 completion verb), photo gate released, mirror enqueued;
                 the bubble is edited in place: buttons removed, "done" mark.
    cancel    -> cancel(start-card-cancel) of that block id, photo gate
                 released, mirror enqueued; buttons removed, cancelled mark.
                 Only this routine occurrence is skipped. task-undone restores
                 this cancellation; the routine definition is untouched.
    snooze    -> the bubble's keyboard becomes the choices (in 30 min /
                 1 hour / 2 hours / tomorrow / say when). Daily routines
                 stay within today; every preset names its clock time.
                 Relative presets count from the item's scheduled start,
                 never the reminder/tap clock. One-off tomorrow is 10:00.
                 Back restores the exact preceding keyboard and card state,
                 editing the same bubble without changing the schedule.
                 A choice = task-reschedule of THAT
                 occurrence (bypass_schedule_apply; a routine occurrence is
                 stamped rec_exception, the rule is untouched).
                 Knowledge rule: after the 3rd postpone of the same item
                 (routine = recurrence_id, task = ulid) the result bubble asks
                 ONCE with buttons shorten? / move? / drop? -- never again
                 for that item, no deadline invented, no nagging.
    help      -> returns the task payload (id, title, note, place, links,
                 type) and the one-action reply contract; the tap's own
                 agent turn IS the session turn, nothing else is injected.
                 The keyboard comes back without the help button.
    reshape_* -> returns the choice + the verbs to use; buttons removed.

Functions take kit_life as K (errand.py convention). English/ASCII only.
"""

from clock_parts import utc_datetime, local_datetime
import datetime
import json
import os
import re
import subprocess
import reminder_owner
from datetime import timedelta, timezone

CARDS_KEY = "task_start_cards"
SNOOZE_KEY = "task_snooze_counts"
CARD_TTL_S = 2 * 86400
RESHAPE_AFTER = 3
SNOOZE_MIN = 30
_FMT = "%Y-%m-%dT%H:%M:%SZ"

# action -> (copy key, en fallback). Order of START is the owner's order.
LABELS = {
    "session": ("remind.start.btn.session", "Start"),
    "snooze": ("remind.start.btn.snooze", "Snooze"),
    "help": ("remind.start.btn.help", "Help me start"),
    "done": ("remind.start.btn.done", "Done"),
    "cancel": ("remind.start.btn.cancel", "Cancel"),
    "back": ("remind.start.btn.back", "Back"),
    "snooze_30m": ("remind.start.snooze.30m", "In 30 min"),
    "snooze_1h": ("remind.start.snooze.1h", "In 1 hour"),
    "snooze_2h": ("remind.start.snooze.2h", "In 2 hours"),
    "snooze_tomorrow": ("remind.start.snooze.tomorrow", "Tomorrow"),
    "snooze_custom": ("remind.start.snooze.custom", "Say when"),
    "reshape_shrink": ("remind.start.reshape.shrink", "Shorten it?"),
    "reshape_move": ("remind.start.reshape.move", "Move it?"),
    "reshape_drop": ("remind.start.reshape.drop", "Drop it?"),
}
START = ("snooze", "help", "done", "cancel")
LEAD = ("snooze", "done", "cancel")
RESHAPE = ("reshape_shrink", "reshape_move", "reshape_drop")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

SAY_NOTHING = ("the card was updated in place: reply exactly NO_PUSH and "
               "nothing else (the bridge's silent reply: no confirmation "
               "line, no placeholder like \"(no output)\", no [[OPTIONS]])")
HELP_CONTRACT = (
    "reply with ONE concrete first action only, in one message that names "
    "the item (`task.title`, the card's bubble may be far up), matched to "
    "`type`: habit -> "
    "shrink it to the smallest start (e.g. open the app, one lesson, 5 min); "
    "prepared -> surface its note / place / links so it can be done now; "
    "vague -> split off one first step. No reasons asked, no lecture, no "
    "list, no [[OPTIONS]]; the card keeps its snooze, done and cancel buttons.")
RESHAPE_NEXT = {
    "reshape_shrink": (
        "propose ONE smaller version in one line and apply it only after the "
        "owner agrees: routine -> `routine update <def_ulid> duration=<min>`; "
        "task -> `task-retitle <id> \"<smaller first piece>\"`."),
    "reshape_move": (
        "ask once when it fits better and apply the answer: routine -> "
        "`routine update <def_ulid> time=HH:MM`; task -> "
        "`task-reschedule <id> <YYYY-MM-DD> --at HH:MM`. No invented deadline."),
    "reshape_drop": (
        "task -> `task-archive <id>`, then one short line. routine -> confirm "
        "once that the whole routine stops, then `routine retire <def_ulid>` "
        "(or `routine pause` if they want it back later)."),
}


def _label(K, action):
    key, fallback = LABELS[action]
    return K._i18n(key, fallback=fallback)


def _keyboard(K, actions):
    """{label: action} + Telegram rows (one button per row, opt: callbacks)."""
    live, rows = {}, []
    for a in actions:
        label = _label(K, a)
        live[label] = a
        rows.append([{"text": label, "callback_data": "opt:" + label}])
    return live, rows


def _now_dt(now):
    return utc_datetime(now, _FMT)


def _load(K, conn, now):
    """{key: card} with expired cards pruned."""
    raw = K._config_stored(conn, CARDS_KEY)
    try:
        cards = json.loads(raw) if raw else {}
    except ValueError:
        cards = {}
    if not isinstance(cards, dict):
        cards = {}
    cut = _now_dt(now) - timedelta(seconds=CARD_TTL_S)
    out = {}
    for k, c in cards.items():
        try:
            if _now_dt(c["posted_at"]) >= cut:
                out[k] = c
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save(K, conn, cards):
    K._config_put(conn, CARDS_KEY, json.dumps(cards, sort_keys=True,
                                              ensure_ascii=False))


def eligible(alert):
    """A lead/start alert of a plain task/routine row (not a travel / prep
    helper, not an errand, not an appointment)."""
    return (alert.get("alert") in ("lead", "start") and not alert.get("helper")
            and alert.get("kind") == "task" and alert.get("block_id"))


def already_posted(K, conn, alert, now):
    """Shared global keys suppress fallback even when the transport is absent."""
    try:
        return alert["key"] in _load(K, conn, now)
    except Exception:
        return False


def card_text(K, alert, start_line):
    """Keep the lead header/body; starts use the short "<title> <dur>" line."""
    if alert.get("alert") == "lead":
        header = (K._i18n("remind.task_lead", fallback="")
                  or K._i18n("remind.generic_lead", fallback="")
                  or K._i18n("reminder_header", fallback=""))
        minutes = int(alert["lead_min"])
        # Match remind.sh's _fmt_lead, including its English "before" suffix.
        if K.agent_conf.conf_str("AGENT_LANG", "en") == "ko":
            time = ("%d\uc2dc\uac04" % (minutes // 60) if minutes % 60 == 0
                    else "%d\ubd84" % minutes)
        else:
            time = ("%dh before" % (minutes // 60) if minutes % 60 == 0
                    else "%dmin before" % minutes)
        header = header.replace("{time}", time).replace("{min}", str(minutes))
        body = "\n".join(ln.replace("\t", " ") for ln in alert["lines"])
        return header + "\n" + body if header else body
    header = (K._i18n("remind.task_start", fallback="")
              or K._i18n("remind.generic_start", fallback=""))
    line = (alert.get("lines") or [""])[0] if alert.get("ride") else start_line
    return header + "\n" + line if header else line


def start_line(K, title, start_at, end_at, dur):
    """remind.routine_start "{title} {dur}"; no end -> the title alone."""
    if not end_at:
        return title
    mins = int((_now_dt(end_at) - _now_dt(start_at)).total_seconds() // 60)
    if mins <= 0:
        return title
    return K._i18n("remind.routine_start",
                   fallback="Time for {title} {dur}").format(
                       title=title, dur=dur(mins))


def _push(push, args):
    """(rc, ack dict) of one push.sh call; never raises."""
    try:
        p = subprocess.run([push] + args, capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 2, {}
    ack = {}
    for ln in (p.stdout or "").splitlines():
        try:
            ack = json.loads(ln)
            break
        except ValueError:
            continue
    return p.returncode, ack if isinstance(ack, dict) else {}


def _capable(push):
    try:
        p = subprocess.run([push, "--capabilities"], capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    caps = (p.stdout or "").split()
    return p.returncode == 0 and "strict" in caps and "edit" in caps


def _lead_for(card, alert):
    """Same occurrence, not another task or a rescheduled start instant."""
    return (card.get("alert") == "lead"
            and card.get("ulid") == alert["ulid"]
            and card.get("start_at") == alert["start_at"])


def suppresses_start(K, conn, alert, now):
    """A lead tap owns this occurrence even while snooze choices are open.
    Check before transport capability/fallback so no plain start row leaks."""
    if alert.get("alert") != "start":
        return False
    try:
        cards = _load(K, conn, now)
    except Exception:
        return False  # an older store without config must still emit its row
    return any(_lead_for(c, alert) and c.get("suppress_start")
               for c in cards.values())


def _retire_leads(K, conn, alert, now, push):
    """After a start posts, spend its untapped lead and remove only buttons.
    Keep failed edits pending so subsequent selector polls retry them."""
    if alert.get("alert") != "start":
        return
    conn.execute("BEGIN IMMEDIATE;")
    cards = _load(K, conn, now)
    pending = []
    for card in cards.values():
        if not reminder_owner.owns_card(K, card) or not _lead_for(card, alert):
            continue
        if card.get("state") == "open" and card.get("keyboard"):
            card.update(state="superseded", keyboard={}, rows=[],
                        retire_pending=True)
        if card.get("retire_pending"):
            pending.append(card)
    _save(K, conn, cards)
    conn.commit()
    for card in pending:
        if _edit(K, card, push=push):
            card.pop("retire_pending", None)
            _store(K, conn, card, now)


def deliver(K, db_path, alert, now, dur, push=None):
    """Post the lead/start card for one due alert. True = handled (posted now,
    or already posted earlier: the selector must not emit the row); False =
    remind.sh sends the row as before. Never raises."""
    try:
        push = push if push is not None else K._photo_push_sh()
        if not push or not _capable(push):
            return False
        conn = K.event_conn(db_path)
    except Exception:
        return False
    handled = False
    try:
        conn.execute("BEGIN IMMEDIATE;")
        cards = _load(K, conn, now)
        if alert["key"] in cards:
            handled = True
            conn.commit()
            _retire_leads(K, conn, alert, now, push)
            return True
        text = card_text(K, alert, start_line(
            K, alert["title_raw"], alert["start_at"], alert.get("end_at"), dur))
        actions = LEAD if alert["alert"] == "lead" else START
        kind = conn.execute("SELECT value FROM block_attr WHERE block_id=? "
                            "AND attr_token='action_kind'", (alert["block_id"],)).fetchone()
        if kind and kind[0] != "check":
            actions = ("session",) + actions
        task_text = text
        if K._config_stored(conn, 'cooking.plan.' + str(alert['block_id'])):
            import cooking_session
            text += '\n' + cooking_session.reminder(K, conn, alert['block_id'])
        live, rows = _keyboard(K, actions)
        card = dict(key=alert["key"], alert=alert["alert"],
                    sending_agent=reminder_owner.instance_agent(K),
                    block_id=int(alert["block_id"]),
                    ulid=alert["ulid"], start_at=alert["start_at"],
                    rid=alert.get("recurrence_id"), title=alert["title_raw"],
                    text=text, task_text=task_text, marks=[], keyboard=live, rows=rows,
                    state="posting", posted_at=now, message_id=None,
                    chat_id=None)
        cards[alert["key"]] = card
        _save(K, conn, cards)
        conn.commit()
        rc, ack = _push(push, ["--strict", "--text", text, "--buttons-json",
                               json.dumps(rows, ensure_ascii=False, sort_keys=True)])
        conn.execute("BEGIN IMMEDIATE;")
        cards = _load(K, conn, now)
        if rc not in (0, 6):
            cards.pop(alert["key"], None)
            _save(K, conn, cards)
            conn.commit()
            return False
        handled = True
        card = cards.get(alert["key"], card)
        card.update(state="open", message_id=ack.get("message_id"),
                    chat_id=ack.get("chat_id"))
        cards[alert["key"]] = card
        _save(K, conn, cards)
        conn.commit()
        _retire_leads(K, conn, alert, now, push)
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        # A successful send must never leak a duplicate fallback row just
        # because saving/retiring an older keyboard failed.
        return handled
    finally:
        conn.close()


def inbound_message_id(K):
    """message_id of the tapped bubble from the bridge's per-turn context
    (source "callback"), or None."""
    path = os.path.join(os.path.dirname(K.SCRIPT_DIR), ".telegram_bot",
                        "inbound-context.json")
    try:
        with open(path, encoding="utf-8") as fh:
            ctx = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(ctx, dict) or ctx.get("source") != "callback":
        return None
    return ctx.get("message_id")


def _find(cards, text, message_id, agent="ag"):
    cards = {k: c for k, c in cards.items()
             if agent and reminder_owner.card_sender(c) == agent}
    if message_id is not None:
        for c in cards.values():
            if c.get("message_id") is not None and \
                    str(c["message_id"]) == str(message_id):
                return c
            if c.get("tapped_mid") is not None and \
                    str(c["tapped_mid"]) == str(message_id):
                return c    # a typed late row already answered from this bubble
        # A typed completion's late row has no bubble of ours yet: the tap
        # carries the reply's id, so match its live keyboard by text, only
        # when unambiguous.
        hits = [c for c in cards.values() if c.get("alert") == "late"
                and c.get("message_id") is None
                and text in (c.get("keyboard") or {})]
        return hits[0] if len(hits) == 1 else None
    # no bubble id (an older bridge): only an unambiguous live card counts
    hits = [c for c in cards.values() if text in (c.get("keyboard") or {})]
    return hits[0] if len(hits) == 1 else None


def _render(card):
    return "\n".join([card["text"]] + list(card.get("marks") or []))


def _edit(K, card, push=None):
    """Re-render the bubble: text + marks, the live keyboard (none = buttons
    removed). True when the bubble shows it (edited or already current)."""
    if card.get("message_id") is None or not reminder_owner.owns_card(K, card):
        return False
    push = push if push is not None else K._photo_push_sh()
    if not push:
        return False
    args = ["--strict", "--edit", str(card["message_id"]), "--text",
            _render(card)]
    if card.get("chat_id") is not None:
        args += ["--chat", str(card["chat_id"])]
    if card.get("rows"):
        # Config persistence sorts nested keys; use the same serialization at
        # delivery and edit so restored keyboards are byte-identical as well.
        args += ["--buttons-json", json.dumps(card["rows"], ensure_ascii=False,
                                            sort_keys=True)]
    rc, _ack = _push(push, args)
    return rc in (0, 8)


def _set_keyboard(K, card, actions):
    card["keyboard"], card["rows"] = _keyboard(K, actions)


def _block(K, conn, bid):
    return conn.execute(
        "SELECT id, ulid, title, start_at, end_at, schedule_kind, display_tz, "
        "settled_outcome, settled_at, version, recurrence_id, note, purpose, "
        "location, location_url, place_id FROM timeblock WHERE id=?;",
        (bid,)).fetchone()


def _local(ts, tz):
    from zoneinfo import ZoneInfo
    return local_datetime(_now_dt(ts), ZoneInfo(tz or "Asia/Seoul"))


def snooze_choices(K, row, now, conn=None):
    """Offsets use the scheduled start; elapsed choices cannot move backwards.

    Daily routines stay on their scheduled day; other routines avoid tomorrow
    collisions. A one-off has 30m / 1h / tomorrow 10:00 / custom.
    """
    loc = _local(now, row[6])
    scheduled = _local(row[3], row[6])
    routine = row[10] is not None
    out = [a for a, minutes in (("snooze_30m", 30), ("snooze_1h", 60),
                                ("snooze_2h", 120))
           if (routine or minutes != 120) and
           _now_dt(row[3]) + timedelta(minutes=minutes) > _now_dt(now) and
           (not routine or (_now_dt(row[3]) + timedelta(minutes=minutes)).astimezone(
               scheduled.tzinfo).date() == scheduled.date())]
    tomorrow = not routine
    if routine and conn is not None:
        definition = conn.execute(
            "SELECT cadence, status, end_date FROM routine_def WHERE recurrence_id=?",
            (row[10],)).fetchone()
        if definition and definition[0] != "D" and definition[1] == "active":
            for days in range(1, 366 * 8):
                day = loc.date() + timedelta(days=days)
                if definition[2] and day.isoformat() > definition[2]:
                    break
                if K._projection_mod().occurs(definition[0], day):
                    tomorrow = days > 1
                    break
        # A moved/materialized occurrence can collide even if the rule does not.
        if tomorrow:
            for (start,) in conn.execute(
                    "SELECT start_at FROM timeblock WHERE recurrence_id=? AND id!=? "
                    "AND settled_at IS NULL AND start_at>?", (row[10], row[0], now)):
                if _local(start, row[6]).date() == loc.date() + timedelta(days=1):
                    tomorrow = False
                    break
    if tomorrow:
        out.append("snooze_tomorrow")
    return out + ["snooze_custom"]


def _snooze_target(K, row, action, now, at=None):
    """Return the exact clock time printed on the button, preserving duration."""
    tz = row[6] or "Asia/Seoul"
    loc = _local(now, tz)
    if action == "snooze_tomorrow":
        day = (loc + timedelta(days=1)).date().isoformat()
        sk, sa, ea = K._defer_shape(row[5], row[3], row[4], tz, day,
                                  at_hhmm="10:00" if row[10] is None else None)
        return sk, sa, ea, _local(sa, tz).strftime("%H:%M")
    if action == "snooze_custom":
        target = datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M").replace(tzinfo=loc.tzinfo)
        if target <= loc:
            raise ValueError("snooze time must be in the future")
    else:
        minutes = {"snooze_30m": 30, "snooze_1h": 60, "snooze_2h": 120}[action]
        sk, sa, ea = K._defer_by_shape(row[5], row[3], row[4], "+%dm" % minutes)
        return sk, sa, ea, _local(sa, tz).strftime("%H:%M")
    day, hhmm = target.date().isoformat(), target.strftime("%H:%M")
    sk, sa, ea = K._defer_shape(row[5], row[3], row[4], tz, day, at_hhmm=hhmm)
    return sk, sa, ea, hhmm


def _snooze_keyboard(K, conn, card, row, now):
    live, buttons = {}, []
    card["targets"] = {}
    for action in snooze_choices(K, row, now, conn):
        label = _label(K, action)
        if action != "snooze_custom":
            target = _snooze_target(K, row, action, now)
            card["targets"][action] = target
            separator = " " if action == "snooze_tomorrow" else " \u00b7 "
            label += separator + target[3]
        live[label] = action
        buttons.append([{"text": label, "callback_data": "opt:" + label}])
    card["keyboard"], card["rows"] = live, buttons
    back, rows = _keyboard(K, ("back",))
    card["keyboard"].update(back)
    card["rows"].extend(rows)


def is_late(start_at, end_at, now):
    """The one late-done rule (owner 2026-10-18 19:07): done after start +
    duration + 30 min. Strict: exactly 30 min after the end is not late."""
    return bool(start_at and _now_dt(now) > _now_dt(end_at or start_at)
                + timedelta(minutes=30))


def _late_keyboard(K, card, row, now):
    scheduled = _local(row[3], row[6])
    hour = scheduled.hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 18 else "evening" if 18 <= hour < 22 else "night"
    labels = [K._i18n("remind.start.done." + part, fallback="Around {hhmm}").format(
                  hhmm=scheduled.strftime("%H:%M")),
              K._i18n("remind.start.done.now", fallback="Just now {hhmm}").format(
                  hhmm=_local(now, row[6]).strftime("%H:%M"))]
    card["keyboard"] = dict(zip(labels, ("done_scheduled", "done_now")))
    card["rows"] = [[{"text": label, "callback_data": "opt:" + label} for label in labels]]
    card["done_default"], card["done_tapped_at"] = row[3], now


def _late_question(K):
    return K._i18n(
        "remind.start.done.late_question",
        fallback="Checked later than planned. When did you actually do it?")


LATE_OPTIONS_RELAY = (
    "CARD_RELAY The lines between CARD_BEGIN and CARD_END are the ALREADY "
    "RENDERED card of the task this write changed: a state line, then the "
    "card. Your whole reply IS that card, relayed EXACTLY ONCE and VERBATIM "
    "-- keep the state line, the date header and the leading '> ' of the "
    "row. Add no success line and no sentence about it. Keep the late-done "
    "question line printed below verbatim as the final line of the card. Then "
    "put the [[OPTIONS: ...]] marker on its own line LAST, exactly as printed "
    "below "
    "(the late-done time choice; the task is already done). Never print "
    "CARD_BEGIN, CARD_END or this CARD_RELAY line itself.")


def offer_late(K, conn, block_id, now):
    """Typed-completion twin of the card-button late row: the task is already
    done at its scheduled time; post the same two-button choice (same keys,
    same copy) as a live card with no bubble yet. A tap arrives as the usual
    start-tap and records the chosen time exactly like the tap path. Returns
    the [[OPTIONS: ...]] marker line, or ''. Never raises."""
    try:
        row = _block(K, conn, block_id)
        if row is None or row[7] != "done" or row[8] != row[3] or row[5] != "timed":
            return ""
        card = dict(key="late:%s:%s" % (row[0], row[1]), alert="late",
                    sending_agent=reminder_owner.instance_agent(K),
                    block_id=int(row[0]), ulid=row[1], start_at=row[3],
                    rid=row[10], title=row[2], text="", task_text="",
                    marks=[], state="done", suppress_start=True,
                    posted_at=now, message_id=None, chat_id=None)
        _late_keyboard(K, card, row, now)
        conn.execute("BEGIN IMMEDIATE;")
        try:
            cards = _load(K, conn, now)
            # one open late row per task: a newer completion replaces it
            cards[card["key"]] = card
            _save(K, conn, cards)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return "%s\n[[OPTIONS: %s]]" % (
            _late_question(K), " | ".join(card["keyboard"]))
    except Exception:
        return ""


def _bump(K, conn, item):
    """Postpone count of one item; True once, when the reshape question is
    due (3rd postpone, never asked before)."""
    raw = K._config_stored(conn, SNOOZE_KEY)
    try:
        counts = json.loads(raw) if raw else {}
    except ValueError:
        counts = {}
    rec = counts.get(item) or {"n": 0, "asked": False}
    rec["n"] = int(rec.get("n", 0)) + 1
    ask = rec["n"] >= RESHAPE_AFTER and not rec.get("asked")
    if ask:
        rec["asked"] = True
    counts[item] = rec
    K._config_put(conn, SNOOZE_KEY, json.dumps(counts, sort_keys=True))
    return ask


def help_payload(K, conn, row, card):
    note = (row[11] or "").strip()
    purpose = (row[12] or "").strip()
    place = None
    if row[15] is not None:
        p = conn.execute("SELECT name, address FROM place WHERE id=?;",
                         (row[15],)).fetchone()
        if p:
            place = {"name": p[0], "address": p[1] or ""}
    if place is None and (row[13] or "").strip():
        place = {"name": row[13].strip(), "address": ""}
    links = []
    for src in (row[14] or "", note, purpose):
        for u in _URL_RE.findall(src):
            if u not in links:
                links.append(u)
    if row[10] is not None:
        kind = "habit"
    elif note or purpose or place or links:
        kind = "prepared"
    else:
        kind = "vague"
    tz = row[6] or "Asia/Seoul"
    task = dict(id=row[0], ulid=row[1], title=row[2],
                start_local=_local(row[3], tz).strftime("%Y-%m-%d %H:%M")
                if row[3] else "",
                minutes=(int((_now_dt(row[4]) - _now_dt(row[3])).total_seconds()
                             // 60) if row[3] and row[4] else None),
                note=note, purpose=purpose, place=place, links=links,
                routine=row[10] is not None)
    return dict(type=kind, task=task)


def _claim(K, conn, text, message_id, now, at=None):
    """Spend the tapped button: (card, action) with the card's keyboard
    emptied in the same transaction (a second tap finds nothing live), or
    (card|None, None)."""
    conn.execute("BEGIN IMMEDIATE;")
    try:
        cards = _load(K, conn, now)
        card = _find(cards, text, message_id, reminder_owner.instance_agent(K))
        action = (card.get("keyboard") or {}).get(text) if card else None
        if at is not None:
            action = "snooze_custom_apply" if (card and message_id is not None and
                     card.get("state") == "awaiting_time") else None
        if action is not None:
            if card.get("alert") == "late" and card.get("message_id") is None \
                    and message_id is not None:
                card["tapped_mid"] = message_id    # repeat taps read SPENT
            card["spent"] = dict(keyboard=card["keyboard"], rows=card["rows"])
            card["keyboard"], card["rows"] = {}, []
            cards[card["key"]] = card
            _save(K, conn, cards)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return card, action


def _store(K, conn, card, now, restore=False):
    """Write the card back (restore=True puts the claimed keyboard back:
    nothing was applied)."""
    spent = card.pop("spent", None)
    if restore and spent:
        card["keyboard"], card["rows"] = spent["keyboard"], spent["rows"]
    # Completion stays available after a routine's snooze/session handoff,
    # including custom time and the third-postpone question. The preset menu
    # has Back instead, so returning restores its preceding keyboard exactly.
    if (card.get("rid") and card.get("state") in
            ("awaiting_time", "snoozed", "reshape", "started", "closed")
            and "done" not in card.get("keyboard", {}).values()):
        done, rows = _keyboard(K, ("done",))
        card.setdefault("keyboard", {}).update(done)
        card.setdefault("rows", []).extend(rows)
    conn.execute("BEGIN IMMEDIATE;")
    try:
        cards = _load(K, conn, now)
        cards[card["key"]] = card
        _save(K, conn, cards)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def tap(K, conn, text, message_id=None, now=None, push=None, at=None):
    """Apply one tap. Returns a dict with status: DONE / ALREADY_DONE /
    CANCELLED / CHOOSING / BACK / SNOOZED / HELP / RESHAPE / SPENT / NO_CARD / RETRY.
    SPENT / NO_CARD write and edit nothing (a double tap is a no-op)."""
    now = now or K.now_utc()
    text = (text or "").strip()
    card, action = _claim(K, conn, text, message_id, now, at=at)
    if card is None:
        return dict(status="NO_CARD", next=(
            "not a start-card tap: handle the message as ordinary chat"))
    if action is None:
        return dict(status="SPENT", id=card["block_id"], next=SAY_NOTHING)
    row = _block(K, conn, card["block_id"])
    out = dict(id=card["block_id"], title=card["title"], action=action)
    enqueue = None
    if action == "snooze_custom_apply" and row is not None:
        try:
            custom_target = _snooze_target(K, row, "snooze_custom", now, at=at)
        except (ValueError, TypeError):
            _store(K, conn, card, now, restore=True)
            return dict(status="RETRY", id=card["block_id"], next="Ask for a future local date and time.")
    if row is None or (row[8] is not None and action not in ("done", "done_scheduled", "done_now")):
        card["state"] = "closed"
        out.update(status="SPENT", next=SAY_NOTHING)
    elif action in ("done_scheduled", "done_now"):
        if row[7] == "done" and row[8] == card.get("done_default"):
            ts = card["done_tapped_at"] if action == "done_now" else card["done_default"]
            conn.execute("BEGIN IMMEDIATE;")
            try:
                fields = {"settled_at": ts, "updated_at": now}
                if action == "done_now":
                    fields.update(K._late_done_move_fields(conn, row[0], ts, now))
                changed = K._tb_update(conn, row[0], fields,
                    where_extra=" AND settled_outcome='done'", bump_version=True, cas_version=row[9])
                if changed:
                    K._recompute_and_store(conn, row[0], now=now)
                conn.commit()
            except Exception:
                conn.rollback()
                _store(K, conn, card, now, restore=True)
                raise
            enqueue = row[1] if changed else None
            if changed:
                rendered = K._done_card(conn, "start-tap", row[0])
                if rendered:
                    card["text"], card["marks"] = rendered, []
        card["state"] = "done"
        out.update(status="DONE_TIME", next=SAY_NOTHING)
    elif action == "done":
        if row[8] is not None:
            out["status"] = "ALREADY_DONE" if row[7] == "done" else "SPENT"
        else:
            late = is_late(row[3], row[4], now)
            res = K.force_settle(conn, row[0], row[9], "owner-chat",
                                 settled_at_ts=row[3] if late else now, move_late=not late)
            if res == K.MutationResult.SESSION_OPEN:
                _store(K, conn, card, now, restore=True)
                return dict(status="SESSION_OPEN", id=row[0],
                            next=K._workout_history.SESSION_NEXT)
            if res != K.MutationResult.APPLIED:
                _store(K, conn, card, now, restore=True)
                return dict(status="RETRY", id=row[0], next=(
                    "re-run the same start-tap once; never claim done "
                    "before DONE"))
            K._photo_gate_release(conn, now, row[0])
            out["status"] = "DONE"
            enqueue = row[1]
            if late:
                _late_keyboard(K, card, row, now)
                out["late"] = True
        if out["status"] in ("DONE", "ALREADY_DONE"):
            card["marks"] = [K._i18n("remind.start.done_mark",
                                     fallback="\u2705 Done")]
            if out.get("late"):
                card["marks"].append(_late_question(K))
        card["state"] = "done"
        card["suppress_start"] = True
        out["next"] = SAY_NOTHING
    elif action == "cancel":
        # The task-archive settle path, scoped to this block. A settled
        # occurrence occupies its date, so the roller cannot recreate it;
        # the definition and all other occurrences remain active.
        res = K.cancel(conn, row[0], row[9], "start-card-cancel")
        if res != K.MutationResult.APPLIED:
            _store(K, conn, card, now, restore=True)
            return dict(status="RETRY", id=row[0], next=(
                "re-run the same start-tap once; never claim cancelled "
                "before CANCELLED"))
        K._photo_gate_release(conn, now, row[0])
        card["marks"] = [K._i18n("day.card.removed", fallback=" \u2190 cancelled")]
        card["state"] = "cancelled"
        card["suppress_start"] = True
        enqueue = row[1]
        out.update(status="CANCELLED", routine=row[10] is not None,
                   undo="task-undone %s" % row[0], next=SAY_NOTHING)
    elif action == "session":
        out.update(K.session_start_entry(conn, row[0]))
        if K._config_stored(conn, 'cooking.plan.' + str(row[0])):
            # Retire the reminder's preview after handoff; the ingredient card
            # and subsequent step edits own the cooking content from here.
            card['text'] = card.get('task_text', card['title'])
        card["state"] = "started"
        card["suppress_start"] = True
    elif action == "snooze":
        # _claim holds the keyboard that actually preceded this menu. Do not
        # rebuild START/LEAD: sessions and prior help can have different rows.
        card["snooze_return"] = dict(card["spent"], **{
            key: card[key] for key in ("state", "suppress_start", "targets")
            if key in card})
        _snooze_keyboard(K, conn, card, row, now)
        card["state"] = "choosing"
        card["suppress_start"] = True
        out.update(status="CHOOSING", next=SAY_NOTHING)
    elif action == "back":
        previous = card.pop("snooze_return")
        for key in ("keyboard", "rows", "state", "suppress_start", "targets"):
            if key in previous:
                card[key] = previous[key]
            else:
                card.pop(key, None)
        out.update(status="BACK", next=SAY_NOTHING)
    elif action == "snooze_custom":
        card["state"] = "awaiting_time"
        out.update(status="ASK_TIME", message_id=card["message_id"],
                   question=K._i18n("remind.start.snooze.ask", fallback="When should I move it to?"),
                   next="Ask question verbatim. Parse the owner's answer in the task timezone "
                        "and run start-tap <answer> --message-id %s --at YYYY-MM-DDTHH:MM. "
                        "Do not undate the task or edit the routine rule." % card["message_id"],
                   timezone=row[6], now=now)
    elif action.startswith("snooze_"):
        sk, sa, ea, when = custom_target if action == "snooze_custom_apply" else card["targets"][action]
        if sa < now:
            _snooze_keyboard(K, conn, card, row, now)
            card["state"] = "choosing"
            _store(K, conn, card, now)
            _edit(K, card, push=push)
            return dict(status="CHOOSING", next=SAY_NOTHING)
        res, _warn = K.bypass_schedule_apply(conn, row[1], sk, sa, ea)
        if res != "applied":
            _store(K, conn, card, now, restore=True)
            return dict(status="RETRY", id=row[0], reason=res, next=(
                "say in one line that it could not be moved; nothing was "
                "changed"))
        enqueue = row[1]
        item = ("rid:%s" % row[10]) if row[10] else ("ulid:%s" % row[1])
        card["marks"] = [K._i18n("remind.start.snoozed",
                                 fallback="\u23f0 Snoozed: {when}").format(
                                     when=when)]
        conn.execute("BEGIN IMMEDIATE;")
        try:
            ask = _bump(K, conn, item)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if ask:
            _set_keyboard(K, card, RESHAPE)
            card["state"] = "reshape"
        else:
            card["state"] = "snoozed"
        out.update(status="SNOOZED", when=when, start_at=sa, asked=ask,
                   next=SAY_NOTHING)
    elif action == "help":
        _set_keyboard(K, card, [a for a in START if a != "help"])
        out.update(status="HELP", next=HELP_CONTRACT,
                   **help_payload(K, conn, row, card))
    else:   # reshape_*
        card["state"] = "closed"
        out.update(status="RESHAPE", choice=action[len("reshape_"):],
                   routine=row[10] is not None, next=RESHAPE_NEXT[action])
        if row[10] is not None:
            d = conn.execute("SELECT ulid FROM routine_def WHERE "
                             "recurrence_id=?;", (row[10],)).fetchone()
            out["def_ulid"] = d[0] if d else None
    if action == "help" and row is not None:
        related = K._related_shopping(conn, row[2], row[0])
        if related:
            out["related_shopping"] = related
    _store(K, conn, card, now)
    out["edited"] = _edit(K, card, push=push)
    if enqueue:
        K._outbox_enqueue_best_effort(enqueue)
    return out
