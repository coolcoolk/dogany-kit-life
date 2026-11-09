#!/usr/bin/env python3
"""dining_card.py -- gate + render one round of dining cards.

usage: dining_card.py <round.json | -> [--sources] [--lang ko|en]
round.json:
  {"event": {"title": "<event title>", "at": "YYYY-MM-DD HH:MM",
             "origin": "<event place>"} | null,
   "partial": "<why fewer than 3>" (optional),
   "cards": [{"facts": <venue_facts.py output>,
              "fit": "<why it fits THIS party, concrete words>",
              "why": "<one-line reason; first card only>"}, ...]}
Cards are passed recommended-first. Output: the message block to send, as
is (stdout, exit 0). A refused round prints one `REFUSE <CODE> <detail>`
line per problem and exits 3 -- fix the round (drop / replace the card),
never argue with the gate. --sources appends a per-field source table
(for audits; never sent to the owner).

The gate (owner 2026-10-15 17:36, "each candidate needs an info card"):
  CARD_COUNT      3-4 cards (fewer only with "partial")
  NO_PLACE_ID     every card links map.naver.com/p/entry/place/<id>
  FACTS_ERROR     the venue page could not be read -> drop the card
  AT_MISMATCH     hours were checked for another time than the event
  CLOSED_AT_EVENT closed / break / past last order at the event time
  DUP_VENUE       same place twice
  DUP_KIND        two cards of the same kind of food (kind.primary)
  FIRST_UNVERIFIED the recommended card's hours are not verified open
  NO_FIT / NO_WHY a card without its fit line; the first without its reason
  UNSOURCED       a field shown without the source it came from
Lines whose fact is unknown are DROPPED (price, walk, booking); unknown hours
render as the explicit "not verified" label. Nothing is estimated here.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LINK = re.compile(r"^https://map\.naver\.com/p/entry/place/(\d+)$")
NOT_OPEN = ("closed", "break", "last_order")

# English fallbacks; the owner-facing copy lives in the kit i18n bundles
# (dining.* keys, payload/config/i18n/<lang>.bundle.json).
FALLBACK = {
    "dining.card.price": "about {amount} x10k won per person ({basis})",
    "dining.card.walk": "{minutes} min walk from {origin}",
    "dining.card.open": "open at {time} \u2713",
    "dining.card.open_lo": "open at {time} \u2713 (last order {last_order})",
    "dining.card.hours_unknown": "opening not verified",
    "dining.card.booking": "reservations",
    "dining.card.map": "[map]({link})",
    "dining.round.pick": "**Pick: {name}** - {why}",
    "dining.round.decide_event": "Put {name} in as the {event} place, or see other options?",
    "dining.round.decide": "Go with {name}, or see other options?",
}


def _roots():
    env = os.environ.get("PROJECT_ROOT")
    out = [env] if env else []
    # installed: <agent>/.claude/skills-bundle/<id>/ ; repo: payload/skills-bundle/<id>/
    out += [os.path.abspath(os.path.join(HERE, "..", "..", "..")),
            os.path.abspath(os.path.join(HERE, "..", ".."))]
    return out


# Repo: payload/skills-bundle/<id>; installed: <root>/.claude/skills-bundle/<id>.
# Both layouts share the kit's stdlib-only numeric mechanics.
for _root in _roots():
    _database = os.path.join(_root, 'database')
    if os.path.isfile(os.path.join(_database, 'money_format.py')):
        sys.path.insert(0, _database)
        break
from money_format import dining_man
import locale_store


def _lang():
    lang = os.environ.get("AGENT_LANG", "")
    if not lang:
        for root in _roots():
            conf = os.path.join(root, "config", "agent.conf")
            if os.path.isfile(conf):
                for ln in open(conf, encoding="utf-8", errors="replace"):
                    if ln.startswith("AGENT_LANG="):
                        lang = ln.split("=", 1)[1].strip().strip("\"'")
                break
    return lang or "ko"


def _framework_bundle(root, lang):
    """The framework's merged kit bundle (routines/lib/i18n_lookup, DGN-1789)
    -- the layer order is the framework's, never listed here -- or None."""
    return locale_store.framework_bundle(root, lang, 'dining_fw_i18n_lookup')


def load_copy(lang):
    """dining.* keys for `lang`, the same way kit_life._kit_bundle reads the
    kit's copy: the framework helper's merged bundle when the instance has
    it; else (old framework, or this repo checkout) the kit's service copy
    service/kit_life/i18n over config/i18n. English literal last."""
    copy = dict(FALLBACK)
    for root in _roots():
        merged = _framework_bundle(root, lang)
        if merged is None:
            merged = locale_store.legacy_bundle(root, lang)
        keys = {k: v for k, v in merged.items()
                if k.startswith("dining.") and isinstance(v, str) and v}
        if keys:
            copy.update(keys)
            return copy
    return copy


def _manwon(won):
    """14000 -> '1.4', 21750 -> '2.2', 30000 -> '3' (units of 10,000 won)."""
    return dining_man(won)


def _basis(name):
    """Menu name for the price line without its bracketed notes
    ("[2-person] X (min. 2)" -> "X"); the per-person maths is already done."""
    out = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name)
    return re.sub(r"\s+", " ", out).strip() or name


def gate(rnd):
    """[(code, detail)] -- empty when the round may be sent."""
    bad = []
    cards = rnd.get("cards") or []
    ev = rnd.get("event") or {}
    if not (3 <= len(cards) <= 4) and not (rnd.get("partial") and 1 <= len(cards) <= 4):
        bad.append(("CARD_COUNT", "%d cards (3-4, or 1-4 with partial)" % len(cards)))
    seen_ids, seen_kinds = {}, {}
    for i, c in enumerate(cards, 1):
        f = c.get("facts") or {}
        tag = "card %d (%s)" % (i, f.get("name") or f.get("place_id") or "?")
        if f.get("error"):
            bad.append(("FACTS_ERROR", "%s: %s" % (tag, f["error"])))
            continue
        m = LINK.match(f.get("link") or "")
        if not m or m.group(1) != str(f.get("place_id")):
            bad.append(("NO_PLACE_ID", tag))
        if ev.get("at") and f.get("at") != ev["at"]:
            bad.append(("AT_MISMATCH", "%s: checked %s, event %s" % (tag, f.get("at"), ev["at"])))
        hrs = f.get("hours") or {}
        state = hrs.get("state", "unknown")
        if state in NOT_OPEN:
            bad.append(("CLOSED_AT_EVENT", "%s: %s (%s)" % (tag, state, hrs.get("why", ""))))
        if i == 1 and state != "open":
            bad.append(("FIRST_UNVERIFIED", "%s: hours %s" % (tag, state)))
        pid = str(f.get("place_id"))
        if pid in seen_ids:
            bad.append(("DUP_VENUE", "%s = card %d" % (tag, seen_ids[pid])))
        seen_ids.setdefault(pid, i)
        kind = (f.get("kind") or {}).get("primary") or (f.get("kind") or {}).get("label")
        if kind:
            if kind in seen_kinds:
                bad.append(("DUP_KIND", "%s: %s = card %d" % (tag, kind, seen_kinds[kind])))
            seen_kinds.setdefault(kind, i)
        if not (c.get("fit") or "").strip():
            bad.append(("NO_FIT", tag))
        if i == 1 and not (c.get("why") or "").strip():
            bad.append(("NO_WHY", tag))
        for field, shown in (("kind", (f.get("kind") or {}).get("label")),
                             ("per_person", (f.get("per_person") or {}).get("won")),
                             ("walk", (f.get("walk") or {}).get("minutes")),
                             ("booking", (f.get("booking") or {}).get("value")),
                             ("hours", state == "open")):
            if shown and not (f.get(field) or {}).get("src"):
                bad.append(("UNSOURCED", "%s: %s" % (tag, field)))
    return bad


def render_card(c, ev, copy):
    f = c["facts"]
    head = "**%s**" % f["name"]
    label = (f.get("kind") or {}).get("label")
    if label:
        head += " \u00b7 %s" % label
    lines = [head]
    pp = f.get("per_person") or {}
    if pp.get("won"):
        lines.append("\u2022 " + copy["dining.card.price"].format(
            amount=_manwon(pp["won"]), won="{:,}".format(pp["won"]),
            basis=_basis(pp.get("basis") or "")))
    wk = f.get("walk") or {}
    if wk.get("minutes") is not None and ev.get("origin"):
        lines.append("\u2022 " + copy["dining.card.walk"].format(
            minutes=wk["minutes"], origin=ev["origin"]))
    hrs = f.get("hours") or {}
    time_s = (f.get("at") or "")[11:16]
    if hrs.get("state") == "open":
        key = "dining.card.open_lo" if hrs.get("last_order") else "dining.card.open"
        status = copy[key].format(time=time_s, last_order=hrs.get("last_order") or "")
    else:
        status = copy["dining.card.hours_unknown"]
    if (f.get("booking") or {}).get("value"):
        status += " \u00b7 " + copy["dining.card.booking"]
    lines.append("\u2022 " + status)
    lines.append("\u2022 " + c["fit"].strip())
    lines.append(copy["dining.card.map"].format(link=f["link"]))
    return "\n".join(lines)


def render(rnd, copy):
    ev = rnd.get("event") or {}
    cards = rnd["cards"]
    first = cards[0]
    out = [copy["dining.round.pick"].format(name=first["facts"]["name"], why=first["why"].strip())]
    for c in cards:
        out.append(render_card(c, ev, copy))
    if ev.get("title"):
        out.append(copy["dining.round.decide_event"].format(
            name=first["facts"]["name"], event=ev["title"]))
    else:
        out.append(copy["dining.round.decide"].format(name=first["facts"]["name"]))
    return "\n\n".join(out)


def sources(rnd):
    rows = []
    for c in rnd["cards"]:
        f = c["facts"]
        rows.append("## %s  %s" % (f.get("name"), f.get("link")))
        for field in ("kind", "per_person", "walk", "hours", "booking"):
            d = f.get(field) or {}
            if d.get("src"):
                rows.append("  %-10s %s" % (field, d["src"]))
            else:
                rows.append("  %-10s (none -> line dropped%s)" % (
                    field, "; hours label = not verified" if field == "hours" else ""))
        rows.append("  %-10s agent, from the party's stated conditions" % "fit")
    return "\n".join(rows)


def main(argv):
    show_src = "--sources" in argv
    lang = None
    if "--lang" in argv:
        i = argv.index("--lang")
        lang = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv if a != "--sources"]
    if len(argv) != 1:
        print("usage: dining_card.py <round.json|-> [--sources] [--lang ko|en]", file=sys.stderr)
        return 2
    raw = sys.stdin.read() if argv[0] == "-" else open(argv[0], encoding="utf-8").read()
    rnd = json.loads(raw)
    bad = gate(rnd)
    if bad:
        for code, detail in bad:
            print("REFUSE %s %s" % (code, detail))
        return 3
    print(render(rnd, load_copy(lang or _lang())))
    if show_src:
        print("\n--- sources ---\n" + sources(rnd))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
