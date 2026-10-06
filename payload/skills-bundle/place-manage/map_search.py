#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# map_search.py -- naver local search helper for place-manage
# usage: python3 map_search.py "장소명"
#        python3 map_search.py --link "<지도 링크>"
# output: JSON array [{name, address, link}]
# link = naver place URL (map.naver.com/p/entry/place/{id}) via agent-browser, else coord URL
#
# --link mode (2.20.0) exists because sharing a naver/kakao map link is how the
# owner names a venue -- it is the common case, not malformed input. Measured
# 2026-09-12: a next-day appointment held nothing but
# "https://naver.me/Ex4mpLe1", so the kit could mint no place row and derived
# no travel time; the owner found out by asking.
#
# No new dependency -- and, as it turns out, one FEWER than the name mode
# needs. The whole chain is stdlib:
#   naver.me/Ex4mpLe1 -> 307 -> map.naver.com/p/entry/place/10000001
#     -> m.place.naver.com/place/10000001/home, "PlaceDetailBase:10000001"
#     -> "가온 컨벤션 웨딩홀" / "서울 가온구 한빛길 9"
# agent-browser + search() stay as the fallback rung for when that fails.
#
# It NEVER invents: a link it cannot read exits 5 with the reason, and the
# skill falls back to asking the owner. Candidates are still CANDIDATES --
# the place-manage dialogue confirms before anything is written.

import sys
import os
import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(SKILL_DIR, "../../../runtime/.env")


def load_env(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


def search(query, display=5):
    env = load_env(ENV_PATH)
    cid = env.get("NAVER_CLIENT_ID", "")
    secret = env.get("NAVER_CLIENT_SECRET", "")

    if not cid or not secret:
        print("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET not found in .env", file=sys.stderr)
        sys.exit(1)

    url = (
        "https://openapi.naver.com/v1/search/local.json"
        "?query=" + urllib.parse.quote(query)
        + "&display=" + str(display)
    )
    req = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": cid,
            "X-Naver-Client-Secret": secret,
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())

    results = []
    for item in data.get("items", []):
        name = item["title"].replace("<b>", "").replace("</b>", "")
        addr = item.get("roadAddress") or item.get("address", "")
        link = _naver_place_url(query, item)
        results.append({"name": name, "address": addr, "link": link})

    return results


def _agent_browser_bin() -> str:
    """Resolve the agent-browser binary WITHOUT trusting PATH alone.

    DGN-963 (2026-08-21) reordered PATH and dropped $HOME/.npm-global/bin as
    "dead". It was not dead -- agent-browser lives there and nothing else does.
    Worse than a crash: the calls below sit inside `except Exception: pass` and
    fall back to a coordinate URL, so a missing binary produced plausible-
    looking links with no error at all. PATH first, then the known install
    locations. Override with AGENT_BROWSER_BIN.
    """
    override = os.environ.get("AGENT_BROWSER_BIN")
    if override:
        return override
    found = shutil.which("agent-browser")
    if found:
        return found
    for cand in (os.path.expanduser("~/.npm-global/bin/agent-browser"),
                 os.path.expanduser("~/.local/bin/agent-browser"),
                 "/opt/homebrew/bin/agent-browser",
                 "/usr/local/bin/agent-browser"):
        if os.access(cand, os.X_OK):
            return cand
    return ""


def _require_agent_browser() -> str:
    """FAIL LOUD when the browser driver is missing.

    A coord-URL fallback is a legitimate degradation when the PAGE fails; it is
    NOT acceptable when the driver is absent, because that is a fleet
    misconfiguration the operator must see. Never exit 0 on that path.
    """
    ab = _agent_browser_bin()
    if not ab:
        print("ERR: agent-browser not found (PATH + known install dirs) -- "
              "cannot resolve naver place link", file=sys.stderr)
        raise SystemExit(4)
    return ab


def _naver_place_url(query, item):
    """Try to get a naver place URL via agent-browser; fall back to coord URL."""
    ab = _require_agent_browser()
    try:
        search_url = "https://map.naver.com/p/search/" + urllib.parse.quote(query)
        subprocess.run(
            [ab, "open", search_url],
            capture_output=True, text=True, timeout=15
        )
        subprocess.run(
            [ab, "wait", "3000"],
            capture_output=True, text=True, timeout=10
        )
        r2 = subprocess.run(
            [ab, "get", "url"],
            capture_output=True, text=True, timeout=10
        )
        subprocess.run([ab, "close"], capture_output=True, timeout=5)

        current_url = r2.stdout.strip()
        # extract place_id from URL like .../place/1720353052?...
        m = re.search(r"/place/(\d+)", current_url)
        if m:
            return f"https://map.naver.com/p/entry/place/{m.group(1)}"
    except Exception:
        pass

    # fallback: coordinate-based URL
    mapx = item.get("mapx", "")
    mapy = item.get("mapy", "")
    if mapx and mapy:
        lng = int(mapx) / 1e7
        lat = int(mapy) / 1e7
        return f"https://map.naver.com/p/entry/place?lng={lng}&lat={lat}&zoom=17"
    return "https://map.naver.com/v5/search/" + urllib.parse.quote(query)


# ── --link: a map link -> the venue behind it ─────────────────────────────

# Hosts this mode knows how to READ. Deliberately narrower than kit_life's
# _MAP_LINK_HOSTS, which is what classifies a location as a map link in the
# first place (outcome code `link_place`). Classification is about shape and
# can be generous; resolution has to actually work, and every kakao/google
# shape here would be code written against a URL nobody has produced -- the
# live DB holds four URL locations and not one of them is kakao or google
# (2026-09-12). Those still get CLASSIFIED as a map link, so the owner gets
# asked; they just do not get auto-read. Widen this list when a real link
# shows up to test against, not before.
LINK_HOSTS = (
    "naver.me", "map.naver.com", "place.naver.com", "m.place.naver.com",
    "pcmap.place.naver.com",
)
_MAX_REDIRECTS = 5
_TITLE_SUFFIXES = (" - 네이버지도", " : 네이버지도", " - 네이버 지도")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Stop at each hop so we can read Location ourselves.

    Deliberate: urllib's own follower would land us on the final page and
    throw the chain away, and the chain is the evidence -- a short link that
    redirects somewhere OTHER than a map host must be refused, not scraped.
    """

    def redirect_request(self, *a, **k):
        return None


def _host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_link_host(url):
    h = _host_of(url)
    return any(h == k or h.endswith("." + k) for k in LINK_HOSTS)


def follow_link(url, max_hops=_MAX_REDIRECTS):
    """Expand a short map link to its final URL. Refuses to leave the known
    map hosts -- following a redirect to an arbitrary host is how a link
    resolver turns into an open fetcher."""
    cur = url
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(max_hops):
        if not _is_link_host(cur):
            raise ValueError(
                "cannot read links from this host: %s (naver only so far -- "
                "ask the owner for the venue name)" % (_host_of(cur) or cur))
        req = urllib.request.Request(cur, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        try:
            resp = opener.open(req, timeout=8)
            return resp.geturl()            # 2xx: this IS the final URL
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                raise
            nxt = e.headers.get("Location")
            if not nxt:
                raise ValueError("redirect with no Location: %s" % cur)
            cur = urllib.parse.urljoin(cur, nxt)
    raise ValueError("too many redirects (>%d)" % max_hops)


def place_id_of(url):
    """naver place id out of a resolved map URL, or ''."""
    m = re.search(r"/place/(\d+)", url)
    return m.group(1) if m else ""


# The mobile place page ships its data as embedded JSON. Reachable with the
# stdlib and a mobile UA -- NO api keys, NO browser. That matters: it is the
# lowest rung of the ladder that answers the question, and it makes the link
# path work on an instance that has neither the naver search keys nor
# agent-browser installed.
_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
              "Mobile/15E148 Safari/604.1")


def _place_json(place_id):
    """(name, road_address) from the mobile place page, or ('', '').

    ANCHORED ON THE ID, and that is not fussiness. The page embeds dozens of
    other entities -- nearby POIs, reviewers, parking entrances -- so the
    obvious `"name":"..."` scan returns the FIRST one, and measured
    2026-09-12 on place 10000001 (tomorrow's wedding) that first one is
    "RELA": a different tenant in the same building. Registering it would
    have put a real, plausible, wrong venue on the owner's appointment,
    which is worse than the empty row we started with. `PlaceDetailBase:<id>`
    is the object that IS this place, so the fields read after it are this
    place's fields."""
    url = "https://m.place.naver.com/place/%s/home" % place_id
    req = urllib.request.Request(url, headers={"User-Agent": _MOBILE_UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
    i = body.find('"PlaceDetailBase:%s"' % place_id)
    if i < 0:
        return "", ""
    seg = body[i:i + 8000]

    def field(key):
        m = re.search(r'"%s"\s*:\s*"([^"]{1,160})"' % key, seg)
        return m.group(1).strip() if m else ""

    return field("name"), (field("roadAddress") or field("address"))


def _venue_name_via_browser(place_id):
    """Fallback reader: agent-browser on the desktop map page.

    Only reached when the keyless read above fails (naver rate-limits, or the
    embedded shape changes). The desktop page's <title> IS the venue name
    once the app renders, and driving that page is already this file's
    established use of agent-browser."""
    ab = _require_agent_browser()
    url = "https://map.naver.com/p/entry/place/%s" % place_id
    subprocess.run([ab, "open", url], capture_output=True, text=True,
                   timeout=30)
    subprocess.run([ab, "wait", "5000"], capture_output=True, text=True,
                   timeout=15)
    r = subprocess.run([ab, "get", "title"], capture_output=True, text=True,
                       timeout=15)
    subprocess.run([ab, "close"], capture_output=True, timeout=10)
    title = (r.stdout or "").strip().strip('"')
    for suf in _TITLE_SUFFIXES:
        if title.endswith(suf):
            title = title[: -len(suf)].strip()
    # The generic shell title means the page never rendered the venue (rate
    # limit, cold load). Returning it would register a place literally named
    # "네이버 지도", which is worse than failing.
    if not title or title in ("네이버 지도", "네이버지도", "네이버 플레이스",
                              "플레이스"):
        return ""
    return title


def search_by_link(url):
    """Map link -> the same [{name, address, link}] the name mode returns.

    Ladder, cheapest rung first:
      1. expand the link (stdlib), take the place id;
      2. read name + road address off the mobile page (stdlib, keyless);
      3. only if that fails, agent-browser for the name + search() for the
         address -- the two dependencies this file already had.
    A name with no address is still returned: the NAME is the datum the owner
    was missing, and place-manage's one-turn ask can get the address."""
    final = follow_link(url)
    pid = place_id_of(final)
    if not pid:
        raise ValueError("no place id in resolved url: %s" % final)
    canonical = "https://map.naver.com/p/entry/place/%s" % pid

    name = address = ""
    try:
        name, address = _place_json(pid)
    except Exception:
        pass                            # fall through to the browser rung
    if name:
        return [{"name": name, "address": address, "link": canonical}]

    name = _venue_name_via_browser(pid)
    if not name:
        raise ValueError("could not read the venue name off %s" % final)
    try:
        hits = search(name)
    except SystemExit:
        hits = []                       # no API keys: the name still stands
    except Exception:
        hits = []
    for h in hits:
        if h.get("name") == name:
            h["link"] = canonical
            return [h]
    return (hits or []) + [{"name": name, "address": "", "link": canonical}]


if __name__ == "__main__":
    argv = sys.argv[1:]
    link_mode = False
    if argv and argv[0] == "--link":
        link_mode = True
        argv = argv[1:]
    if not argv or not argv[0].strip():
        print("usage: map_search.py <장소명>\n"
              "       map_search.py --link <지도 링크>", file=sys.stderr)
        sys.exit(1)

    query = argv[0].strip()
    if link_mode:
        try:
            results = search_by_link(query)
        except SystemExit:
            raise
        except Exception as e:
            # Exit 5 = "this link could not be read". Distinct from the
            # name mode's exit 1 so the skill can tell "the resolver failed"
            # from "the search failed" and ask the right question.
            print(f"link resolve error: {e}", file=sys.stderr)
            print("[]")
            sys.exit(5)
    else:
        try:
            results = search(query)
        except Exception as e:
            print(f"search error: {e}", file=sys.stderr)
            sys.exit(1)

    print(json.dumps(results, ensure_ascii=False, indent=2))
