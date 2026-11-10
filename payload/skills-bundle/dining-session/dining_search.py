#!/usr/bin/env python3
"""dining_search.py -- Naver local search with VERIFIED place ids.

usage: dining_search.py "<area> <conditions>" [--display N]
output: JSON [{name, address, search_category, place_id, link, lat, lng}]

Ported from the life agent's local skill restaurant-find/restaurant_search.py (retired by
the kit installer's alias carry step, see service/kit_life/
local-skill-retire.tsv). Two deliberate differences:

  * NO coordinate fallback. The old script turned an unresolved place into a
    `map.naver.com/p/entry/place?lng=..&lat=..` link that looked like a
    success (owner rule: only verified Naver place links). Unresolved now
    means place_id null + link null, and the card renderer refuses it.
  * The local-search API category is returned as `search_category` -- a
    filter hint only. The card's cuisine comes from the place page
    (venue_facts.py), because the API label is often wrong (10-15:
    a shabu / pho place came back as plain "korean").

Place-id resolution, two rungs: (1) the keyless pcmap place-list page, the
hit taken only when name AND road address match the search item; (2) the
agent-browser map search (the life agent's method, stale-iframe name check). The browser
is a singleton: NEVER run this script in parallel; one query at a time.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _env_path():
    """runtime/.env of the agent: $PROJECT_ROOT first, else three levels up
    from the skill dir (.claude/skills[-bundle]/<id>/ -> agent root)."""
    root = os.environ.get("PROJECT_ROOT") or os.path.abspath(
        os.path.join(HERE, "..", "..", ".."))
    return os.path.join(root, "runtime", ".env")


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


def _agent_browser_bin():
    """PATH first, then the known install dirs (DGN-963: a PATH change once
    unplugged the browser and every link silently degraded)."""
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


def _require_agent_browser():
    ab = _agent_browser_bin()
    if not ab:
        print("ERR: agent-browser not found (PATH + known install dirs) -- "
              "cannot resolve place links", file=sys.stderr)
        sys.exit(4)
    return ab


def naver_local_search(query, display=5):
    env = load_env(_env_path())
    cid = env.get("NAVER_CLIENT_ID", "")
    secret = env.get("NAVER_CLIENT_SECRET", "")
    if not cid or not secret:
        print("ERR: NAVER_CLIENT_ID / NAVER_CLIENT_SECRET missing in runtime/.env",
              file=sys.stderr)
        sys.exit(1)
    url = ("https://openapi.naver.com/v1/search/local.json?query="
           + urllib.parse.quote(query) + "&display=" + str(display))
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read())
    items = []
    for item in data.get("items", []):
        lat = lng = None
        try:
            lng, lat = int(item.get("mapx")) / 1e7, int(item.get("mapy")) / 1e7
        except (TypeError, ValueError):
            pass
        items.append({
            "name": re.sub(r"<[^>]+>", "", item["title"]),
            "address": item.get("roadAddress") or item.get("address", ""),
            "search_category": item.get("category", ""),
            "lat": lat, "lng": lng,
        })
    return items


def name_matches(expected, search_text):
    """Loose brand match of the iframe's searchText against the name we
    searched for (first 4 non-space chars), to reject a stale iframe left
    from the previous search."""
    key = re.sub(r"\s+", "", expected).lower()[:4]
    return bool(key) and key in re.sub(r"\s+", "", search_text).lower()


def place_id_from_iframe_src(src, expected_name):
    if not src:
        return None
    st = re.search(r"searchText=([^&]+)", src)
    if st and not name_matches(expected_name, urllib.parse.unquote(st.group(1))):
        return None
    m = re.search(r"/place/(\d+)", src)
    return m.group(1) if m else None


def resolve_place_id(ab, name):
    subprocess.run([ab, "open", "https://map.naver.com/p/search/" + urllib.parse.quote(name)],
                   capture_output=True, text=True, timeout=15)
    time.sleep(3)
    r = subprocess.run(
        [ab, "eval",
         "Array.from(document.querySelectorAll('iframe')).map(f=>f.src)"
         ".find(s=>s.includes('pcmap.place.naver.com/place/')) || ''"],
        capture_output=True, text=True, timeout=10)
    pid = place_id_from_iframe_src(r.stdout.strip().strip('"'), name)
    if pid:
        return pid
    # a single exact hit makes the map jump straight to the place: the id is
    # then in the page URL instead of an iframe
    r = subprocess.run([ab, "get", "url"], capture_output=True, text=True, timeout=10)
    url = urllib.parse.unquote(r.stdout.strip())
    m = re.search(r"/place/(\d+)", url)
    st = re.search(r"searchText=([^&]+)", url)
    if m and (not st or name_matches(name, st.group(1))):
        return m.group(1)
    return None


LIST_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")


def list_items(html):
    """[(id, name, roadAddress)] of the place list page's embedded state."""
    m = re.search(r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\});\s*\n", html, re.S)
    if not m:
        return []
    out = []
    for k, v in json.loads(m.group(1)).items():
        if k.startswith("PlaceListBusinessesItem:") and isinstance(v, dict):
            out.append((str(v.get("id") or k.split(":", 1)[1]), v.get("name") or "",
                        v.get("roadAddress") or v.get("address") or ""))
    return out


def match_listed(items, name, address):
    """The listed place that IS this search hit: same name (spaces ignored)
    AND its road-address head ("<road> <number>") inside the API address.
    Same-name branches elsewhere (ten "oegajip"s nationwide) never match."""
    key = re.sub(r"\s+", "", name)
    addr = re.sub(r"\s+", "", address or "")
    for pid, nm, road in items:
        head = "".join(road.split()[:2])
        if re.sub(r"\s+", "", nm) == key and head and head in addr:
            return pid
    return None


def resolve_keyless(name, address):
    """Rung 1: the pcmap place-list page (no keys, no browser)."""
    url = "https://pcmap.place.naver.com/place/list?query=" + urllib.parse.quote(name)
    req = urllib.request.Request(url, headers={"User-Agent": LIST_UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html = resp.read().decode("utf-8", "replace")
    return match_listed(list_items(html), name, address)


def resolve_links(items):
    ab = None
    out = []
    try:
        for it in items:
            pid = None
            try:
                pid = resolve_keyless(it["name"], it["address"])
            except Exception as e:                    # noqa: BLE001
                print("list read failed for %s: %s" % (it["name"], e), file=sys.stderr)
            if not pid:
                try:
                    ab = ab or _require_agent_browser()
                    pid = resolve_place_id(ab, it["name"])
                except SystemExit:
                    raise
                except Exception as e:                # noqa: BLE001
                    print("browser error for %s: %s" % (it["name"], e), file=sys.stderr)
            out.append(dict(it, place_id=pid,
                            link=("https://map.naver.com/p/entry/place/%s" % pid) if pid else None))
    finally:
        if ab:
            subprocess.run([ab, "close"], capture_output=True, timeout=5)
    return out


def main(argv):
    display = 5
    if "--display" in argv:
        i = argv.index("--display")
        display = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    query = " ".join(argv).strip()
    if not query:
        print("usage: dining_search.py <query> [--display N]", file=sys.stderr)
        return 1
    try:
        items = naver_local_search(query, display=display)
    except Exception as e:                            # noqa: BLE001
        print("search error: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(resolve_links(items) if items else [], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
