#!/usr/bin/env python3
"""venue_facts.py -- the facts a dining card may show, each with its source.

usage: venue_facts.py <naver place id> --at "YYYY-MM-DD HH:MM"
                      [--walk-from "<event place>"] [--html FILE]
output: one JSON object (stdout), exit 0; exit 5 when the place page could
not be read at all (the card for this venue is then dropped, never guessed).

WHY THIS EXISTS (owner 2026-10-15 17:36, the life agent 10-15 replay). The cards the life agent sent
carried a link and a vague line: hours "not checked" every time, a cuisine
label taken from the search API (miimiok shown as Korean food), no price, no
distance. Every one of those facts is published on the venue's own Naver
place page, so this reads that page -- keyless, stdlib only, the same
m.place.naver.com read place-manage/map_search.py already relies on -- and
returns each field WITH where it came from. A field the page does not carry
comes back null; the renderer then drops its line. Nothing here estimates.

Fields (all from the place page unless noted):
  category      the place's own category ("PlaceDetailBase.category"),
                never the local-search API category
  menu          [{name, price, repr}] fixed-price items as listed
  per_person    {won, basis} -- one main dish per person, from the menu (see
                per_person()); null when the menu cannot say it
  hours         {state, ...} at --at: open | break | closed | last_order |
                unknown. Weekday row from newBusinessHours (breaks + last
                order), irregular closed days honoured.
  booking       true only when the page offers Naver booking or lists the
                reservation convenience
  walk          {minutes, src} from the kit route path (place-view/route.sh,
                walk mode), accepted only when the map committed THIS place
                id as the destination; null otherwise
  lat, lng, naver_ref   the place's own position on its page (x = lng,
                y = lat) and the Naver directions end "lng,lat,name,id,
                PLACE_POI" place-manage/map_search.py writes -- what
                `kit_life.sh dine-choose` registers the venue with; null when
                the page carries no position
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
      "Mobile/15E148 Safari/604.1")
# Weekday labels as the place page writes them (Mon..Sun), escaped: code
# stays ASCII (tools/nonascii-ratchet.py).
DAYS = ("\uc6d4", "\ud654", "\uc218", "\ubaa9", "\uae08", "\ud1a0", "\uc77c")
EVERY_DAY = "\ub9e4\uc77c"                       # "every day"
RESERVE_WORD = "\uc608\uc57d"                    # convenience "reservation"
LUNCH_WORD = "\uc810\uc2ec"                      # "lunch" (lunch-only items)
# Menu name markers that make a listed price NOT one person's share.
SHARE_MARK = re.compile(
    "\ub9c8\ub9ac|\\d+\\s*g\\b|\\(\\s*[\ub300\uc911\uc18c]\\s*\\)|"
    "\uc138\ud2b8|\ud50c\ub798\ud130|\ubaa8\ub4ec")
# "N-person" in a name: "2\uc778" / "2\uc778\ubd84"; "N\uc778\uc774\uc0c1"
# (minimum order) keeps the listed price per person.
N_PERSON = re.compile("(\\d)\\s*\uc778(?!\\s*\uc774\uc0c1)")
MIN_ORDER = re.compile("\\d\\s*\uc778\\s*\uc774\uc0c1")
MIN_MAIN_WON = 8000           # below this a menu row is a side, not a meal
LAST_ORDER_MARGIN_MIN = 30    # arriving later than this before last order = no


def fetch_page(place_id):
    """The mobile place page HTML, restaurant route first."""
    last = None
    for kind in ("restaurant", "place"):
        url = "https://m.place.naver.com/%s/%s/home" % (kind, place_id)
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", "replace")
        except Exception as e:                        # noqa: BLE001
            last = e
            continue
        if ('"PlaceDetailBase:%s"' % place_id) in body:
            return body
    raise ValueError("place page unreadable for %s (%s)" % (place_id, last))


def apollo_state(html):
    m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n", html, re.S)
    if not m:
        raise ValueError("no __APOLLO_STATE__ on the place page")
    return json.loads(m.group(1))


def _detail(state):
    for k, v in state.get("ROOT_QUERY", {}).items():
        if k.startswith("placeDetail(") and isinstance(v, dict):
            return v
    return {}


def _won(text):
    m = re.fullmatch(r"\s*([\d,]+)\s*\uc6d0\s*", text or "")
    return int(m.group(1).replace(",", "")) if m else None


def menu_items(state):
    out = []
    for k, v in state.items():
        if not k.startswith("PlaceMenuItem:") or not isinstance(v, dict):
            continue
        price = v.get("price") or {}
        won = _won(price.get("displayText")) if price.get("priceType") == "fixed" else None
        out.append({"name": (v.get("name") or "").strip(),
                    "desc": (v.get("description") or "").strip(),
                    "price": won,
                    "repr": "repr" in (v.get("badges") or [])})
    return out


def load_kinds(path=None):
    """[(word, kind, traits set, display)] from kinds.tsv, longest word first
    (so a longer word claims its text before a word it contains)."""
    rows = []
    with open(path or os.path.join(HERE, "kinds.tsv"), encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip() or ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            traits = set() if p[2] == "-" else set(p[2].split(","))
            rows.append((p[0], p[1], traits, p[0] if p[3] == "-" else p[3]))
    rows.sort(key=lambda r: -len(r[0]))
    return rows


def _words_in(text, kinds, menu=False):
    """kinds rows whose word occurs in text; a hit masks its span so a
    shorter word inside it does not hit again. menu=True skips the
    category-only rows."""
    hits = []
    for row in kinds:
        if menu and "category" in row[2]:
            continue
        if row[0] in text:
            hits.append(row)
            text = text.replace(row[0], " " * len(row[0]))
    return hits


def kind_of(category, items, kinds):
    """(kind tokens, header label, label words): the category word plus the
    kinds named by the representative menu items."""
    toks, words = [], []
    cat_rows = _words_in(category or "", kinds)
    generic = not cat_rows or all(r[1] == "generic" for r in cat_rows)
    for r in cat_rows:
        if r[1] not in ("generic", "-") and r[1] not in toks:
            toks.append(r[1])
    for it in items:
        if not it["repr"]:
            continue
        for r in _words_in(it["name"], kinds, menu=True):
            if r[1] in ("generic", "-"):
                continue
            if r[1] not in toks:
                toks.append(r[1])
            if r[3] not in words and r[3] != (category or ""):
                words.append(r[3])
    label = re.sub(r"\s*,\s*", "\u00b7", category or "")
    if generic and words:
        label = "%s(%s)" % (label, "\u00b7".join(words[:3])) if label else "\u00b7".join(words[:3])
    return toks, label, words


def menu_flags(items, kinds):
    """trait -> [menu item names] over the whole menu (evidence for the fit
    line; never an exclusion by itself)."""
    out = {}
    for it in items:
        for r in _words_in(it["name"] + " " + it["desc"], kinds, menu=True):
            for t in r[2]:
                if t in ("single", "broth", "category"):
                    continue
                lst = out.setdefault(t, [])
                if it["name"] not in lst:
                    lst.append(it["name"])
    return out


def per_person(items, at_hour, kinds):
    """(won, basis item name) for one person's main dish, or (None, None).

    Only a REPRESENTATIVE (owner-marked) fixed-price item is read, in menu
    order, and only when the page itself says the price is one person's
    (an explicit "N\uc778" beats a sharing word: "\ubaa8\ub4ec\ud68c \ucf54\uc2a4 1\uc778"):
    a price for N people ("2\uc778", "2\uc778\ubd84") is divided by N; a
    minimum-order mark ("2\uc778\uc774\uc0c1") keeps the listed price per
    person; otherwise the dish must be a one-serving kind (kinds.tsv trait
    `single`: set meal, ramen, katsu, ...). A shared pot, a platter, a
    half/whole bird or grams -> not on the page, so no line. A lunch-only
    item is skipped for a dinner slot. Below MIN_MAIN_WON is a side."""
    for it in items:
        if not it["repr"] or not it["price"]:
            continue
        text = it["name"]
        if at_hour >= 16 and LUNCH_WORD in (text + it["desc"]):
            continue
        won = it["price"]
        m = N_PERSON.search(text)
        if SHARE_MARK.search(text) and not m:
            continue                       # sharing size, head count unknown
        if m and int(m.group(1)) > 1:
            won = won // int(m.group(1))
        elif not (MIN_ORDER.search(text) or (m and m.group(1) == "1")
                  or any("single" in r[2] for r in _words_in(text, kinds, menu=True))):
            continue
        if won < MIN_MAIN_WON:
            continue
        return won, text
    return None, None


def _hm(s):
    try:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None


def hours_at(detail, when):
    """Verdict for arriving at `when` (local datetime)."""
    rows = detail.get("newBusinessHours") or []
    if not rows:
        return {"state": "unknown", "why": "no hours on the place page"}
    blk = rows[0] or {}
    iso = when.date().isoformat()
    for c in blk.get("comingIrregularClosedDays") or []:
        if iso in json.dumps(c, ensure_ascii=False):
            return {"state": "closed", "why": "irregular closed day %s" % iso}
    day = DAYS[when.weekday()]
    row = None
    for r in blk.get("businessHours") or []:
        if r.get("day") == day:
            row = r
            break
    if row is None:
        for r in blk.get("businessHours") or []:
            if r.get("day") == EVERY_DAY:
                row = r
                break
    if row is None:
        regular = blk.get("comingRegularClosedDays") or ""
        if day in regular:
            return {"state": "closed", "why": "regular closed day"}
        return {"state": "unknown", "why": "no hours row for %s" % day}
    bh = row.get("businessHours") or {}
    start, end = _hm(bh.get("start")), _hm(bh.get("end"))
    t = when.hour * 60 + when.minute
    if start is None or end is None:
        return {"state": "closed" if not bh else "unknown",
                "why": "no opening times for the day"}
    if end <= start:                       # closes after midnight
        end += 24 * 60
    # Several last orders = one per session (lunch, dinner): the one that
    # governs arrival at t is the first at or after t, else the last one.
    los = []
    for x in row.get("lastOrderTimes") or []:
        v = _hm(x.get("time"))
        if v is not None:
            los.append(v + 24 * 60 if v < start else v)
    los.sort()
    lo = next((v for v in los if v >= t), los[-1] if los else None)
    out = {"day": day, "open": bh.get("start"), "close": bh.get("end"),
           "last_order": None if lo is None else "%02d:%02d" % ((lo // 60) % 24, lo % 60),
           "breaks": ["%s-%s" % (b.get("start"), b.get("end"))
                      for b in row.get("breakHours") or []]}
    if not (start <= t < end):
        out.update(state="closed", why="outside %s-%s" % (bh.get("start"), bh.get("end")))
        return out
    for b in row.get("breakHours") or []:
        bs, be = _hm(b.get("start")), _hm(b.get("end"))
        if bs is not None and be is not None and bs <= t < be:
            out.update(state="break", why="break %s-%s" % (b.get("start"), b.get("end")))
            return out
    if lo is not None and t > lo - LAST_ORDER_MARGIN_MIN:
        out.update(state="last_order", why="last order %s" % out["last_order"])
        return out
    out["state"] = "open"
    return out


def booking(detail, base):
    nb = detail.get("naverBooking") or {}
    if nb.get("naverBookingUrl"):
        return {"value": True, "src": "naver place: Naver booking (%s)" % nb["naverBookingUrl"]}
    if RESERVE_WORD in (base.get("conveniences") or []):
        return {"value": True, "src": "naver place: conveniences lists reservation"}
    return {"value": None, "src": None}


def walk(origin, place_id, venue_name):
    """Walking minutes from the event place via the kit route path, or None.

    route.sh (place-view) drives the Naver map; the minutes are taken only
    when the committed destination in MAP-URL is this place id -- a map that
    matched another venue of the same name would otherwise be read as ours."""
    script = None
    for cand in (os.path.join(HERE, "..", "place-view", "route.sh"),
                 os.path.join(HERE, "..", "..", "skills-bundle", "place-view", "route.sh")):
        if os.path.isfile(cand):
            script = os.path.normpath(cand)
            break
    if not script:
        return {"minutes": None, "src": None, "why": "place-view/route.sh not installed"}
    try:
        r = subprocess.run(["bash", script, origin, venue_name, "walk"],
                           capture_output=True, text=True, timeout=120)
    except Exception as e:                            # noqa: BLE001
        return {"minutes": None, "src": None, "why": "route.sh: %s" % e}
    url = ""
    for ln in r.stdout.splitlines():
        if ln.startswith("MAP-URL:"):
            url = ln.split(":", 1)[1].strip()
    if ("," + str(place_id) + ",PLACE_POI") not in url:
        return {"minutes": None, "src": None,
                "why": "map destination is not place %s" % place_id}
    m = re.search(r"===[^\n]*\n[^\n]*?(\d+)\s*\ubd84", r.stdout)
    if not m:
        return {"minutes": None, "src": None, "why": "no walking line"}
    return {"minutes": int(m.group(1)),
            "src": "kit route (place-view/route.sh walk): %s -> %s, %s" % (origin, venue_name, url)}


def position(base, place_id):
    """{lat, lng, naver_ref} of the place from its own PlaceDetailBase, or
    all null. Same "x,y,name,id,PLACE_POI" end map_search.py builds."""
    c = base.get("coordinate") if isinstance(base.get("coordinate"), dict) else base
    try:
        lng, lat = float(c.get("x")), float(c.get("y"))
    except (TypeError, ValueError):
        return {"lat": None, "lng": None, "naver_ref": None}
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat, lng) == (0, 0):
        return {"lat": None, "lng": None, "naver_ref": None}
    label = " ".join(re.sub(r"[,/]+", " ", base.get("name") or "").split())
    return {"lat": round(lat, 7), "lng": round(lng, 7),
            "naver_ref": "%.7f,%.7f,%s,%s,PLACE_POI" % (lng, lat, label, place_id)}


def facts(place_id, when, html=None, walk_from=None):
    html = html if html is not None else fetch_page(place_id)
    state = apollo_state(html)
    base = state.get("PlaceDetailBase:%s" % place_id) or {}
    detail = _detail(state)
    page = "https://m.place.naver.com/restaurant/%s/home" % place_id
    items = menu_items(state)
    kinds = load_kinds()
    won, basis = per_person(items, when.hour, kinds)
    toks, label, _words = kind_of(base.get("category"), items, kinds)
    hrs = hours_at(detail, when)
    out = {
        "place_id": str(place_id),
        "link": "https://map.naver.com/p/entry/place/%s" % place_id,
        "name": base.get("name"),
        "address": base.get("roadAddress") or base.get("address"),
        "at": when.strftime("%Y-%m-%d %H:%M"),
        "category": {"value": base.get("category") or None,
                     "src": "naver place page category (%s)" % page if base.get("category") else None},
        "kind": {"primary": toks[0] if toks else None, "tokens": toks,
                 "label": label or None,
                 "src": ("naver place category + representative menu (%s)" % page)
                 if label else None},
        "menu_flags": menu_flags(items, kinds),
        "menu": [{"name": i["name"], "price": i["price"], "repr": i["repr"]} for i in items],
        "per_person": {"won": won, "basis": basis,
                       "src": ("naver place menu: %s %s won (%s)" % (basis, "{:,}".format(won), page))
                       if won else None},
        "hours": dict(hrs, src="naver place hours, %s row (%s)" % (hrs.get("day", "-"), page)
                      if hrs.get("state") != "unknown" else None),
        "booking": booking(detail, base),
        "walk": {"minutes": None, "src": None},
    }
    out.update(position(base, place_id))
    if walk_from:
        out["walk"] = walk(walk_from, place_id, base.get("name") or "")
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("place_id")
    ap.add_argument("--at", required=True, help="YYYY-MM-DD HH:MM (local)")
    ap.add_argument("--walk-from", default=None)
    ap.add_argument("--html", default=None, help="read this saved page instead")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"\d{4,}", a.place_id):
        print("ERR: place id must be the number from map.naver.com/p/entry/place/<id>",
              file=sys.stderr)
        return 2
    when = dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M")
    html = open(a.html, encoding="utf-8").read() if a.html else None
    try:
        out = facts(a.place_id, when, html=html, walk_from=a.walk_from)
    except Exception as e:                            # noqa: BLE001
        print("ERR: %s" % e, file=sys.stderr)
        print(json.dumps({"place_id": a.place_id, "error": str(e)}))
        return 5
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
