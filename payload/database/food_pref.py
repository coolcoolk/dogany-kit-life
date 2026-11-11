#!/usr/bin/env python3
"""food_pref.py -- kit verbs for the framework preference store (DGN-2120).

WHY THIS EXISTS. The owner's taste has to shape restaurant picks. The
framework owns the preference store; the kit owns the food detail it needs to
SCOPE a preference and the party history it needs to JOIN a group's liking.
Two read-only verbs, contract = DGN-2120 design v2 (LOCKED) section 4,
and the one node write the food-kind "new" path names (node-add, s4.1):

  food-kind <dish-id | venue-id | "words"> [--domain food] [--html F | --facts F] --json
      {"v":1,"available":true,"query","kind":"dish|venue|words",
       "target":"kit_life:food:<node key>",
       "chains":[[{"key","label","level","depth","primary"}]],
       "contains":[{"token","certainty":"primary|possible"}]}
  dining-history --members <holder id,...> [--subset] [--since ISO] [--limit 50] --json
      {"v":1,"available":true,"members":[...],
       "visits":[{"visit":"dine_visit:<ulid>","at","venue","target","party",
                  "match":"exact|subset","verdicts":[{"holder","verdict",
                  "said_by","ref"}],"refs":[...]}],"truncated":false}

  node-add --parent <node> --label "<words>" [--key <ascii>] [--domain food] --json
      {"v":1,"available":true,"target":"kit_life:food:<key>","created":true|false,
       "path":[{"key","label"}]}

Shared rules (section 4): --json required (else exit 2); the two reads are
read-only (ro_open, no row written); NO outbound call -- a Naver venue is read only from facts the
caller already holds (--facts = venue_facts.py output, --html = a saved place
page); kit or module off -> {"v":1,"available":false}, exit 0; exit 3
not-found with "candidates" (<= 5, {target, path}); exit 2 usage; exit 1
internal. Holder ids are the framework forms: owner, person:kit_life:<id>.

node-add is the only write: one config row food_node.<key> = {"parent",
"label"} (the kit's own KV, no table, no migration), found first (a label
already on a node -> that node, created:false), parent unknown -> exit 3
with candidates. The shipped kind_tree.tsv is never edited (a pack install
replaces it); Book reads the config rows on top of it, so a node added here
resolves in food-kind / dining-history from the next call on. Its cuisine is
the parent's.

Ids:
  dish   a kind_tree node key ("mala", "kit_life:food:mala") or a kit meal
         record (block:<id> / its ulid, source_module diet)
  venue  a Naver place id (digits; needs --facts or --html), place:<kit id>,
         or a kit visit block (block:<id> / ulid) -> its recorded place.
         A kit place that carries a Naver id (its place_coord directions end
         or its map_url, written by dine-choose) is keyed naver_<id>, the
         same key the facts path gives; a visit block dine-choose decided
         reads the kinds + category it stored (block_attr dine_venue)
         instead of the place name
  words  anything else, read with kinds.tsv + kind_tree.tsv labels

Chains are root -> leaf. Element "primary" = the hop from this element down
to the next one is primary (a parent hop always is; the target element is
always true); a degree=very event counts only when every element is primary.
A node whose cuisine is not an ancestor gets a second chain
[cuisine(primary=false), node] (mala: spicy>mala and chinese>mala). A venue
gets one chain per kind on its page + its category cuisine, each ending at
the venue node; only the signature (first) kind and the category are primary.

Data (Korean lives here, code stays ASCII):
  skills-bundle/dining-session/kinds.tsv, kind_tree.tsv   words -> nodes
  skills-bundle/dining-session/food_contains.tsv          node -> body tokens
  skills-bundle/dining-session/verdict_words.tsv          words -> verdicts
  database/body_food_token.seed.tsv                       the FROZEN seed

Functions take the kit_life module as K (errand.py convention).
"""
import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys

V = 1
DOMAIN = "food"
NS = "kit_life:food:"
DINING_DIR_ENV = "KIT_DINING_DIR"      # test seam: the dining-session dir
MODULE = "module_dining"
ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
PLACE_ID_RE = re.compile(r"^\d{4,}$")
BLOCK_RE = re.compile(r"^(?:block|visit):(\d+)$")
KIT_PLACE_RE = re.compile(r"^place:(\d+)$")
NODE_RE = re.compile(r"^(?:kit_life:food:)?([a-z][a-z0-9_]*)$")
PERSON_RE = re.compile(r"^person:kit_life:(\d+)$")
VISIT_KINDS = ("appointment", "presence")
CANDIDATES_MAX = 5
LIMIT_DEFAULT = 50
GROUP_MAX = 20                         # design v2 2.4: larger -> no auto-group
CERT_RANK = {"possible": 1, "primary": 2}
CLAUSE_SPLIT = re.compile(r"[.!?\n;]+|,\s*")
LOCAL_NODE_PREFIX = "food_node."       # config key of a node-add node
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
DINE_ATTR = "dine_venue"               # block_attr dine-choose writes
HANGUL_FULL_NAME = re.compile("^[\uac00-\ud7a3]{3}$")
NAVER_URL_ID = re.compile(r"(?:map\.naver\.com/p/entry/place/|place\.naver\.com/"
                          r"(?:restaurant|place)/)(\d{4,})")


class NotFound(Exception):
    def __init__(self, why, candidates=()):
        Exception.__init__(self, why)
        self.why = why
        self.candidates = list(candidates)[:CANDIDATES_MAX]


class Usage(Exception):
    pass


# -- data -------------------------------------------------------------------

def dining_dir(K):
    """The dining-session skill dir: env seam, the payload layout
    (database/../skills-bundle), then the installed agent layouts."""
    env = os.environ.get(DINING_DIR_ENV)
    if env:
        return env if os.path.isfile(os.path.join(env, "kind_tree.tsv")) else None
    root = K._agent_root() if hasattr(K, "_agent_root") else os.path.dirname(K.SCRIPT_DIR)
    for cand in (os.path.join(os.path.dirname(K.SCRIPT_DIR), "skills-bundle", "dining-session"),
                 os.path.join(root, ".claude", "skills-bundle", "dining-session"),
                 os.path.join(root, ".claude", "skills", "dining-session")):
        if os.path.isfile(os.path.join(cand, "kind_tree.tsv")):
            return cand
    return None


def _rows(path, ncol):
    out = []
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip() or ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= ncol:
                out.append(p)
    return out


def _venue_facts(ddir):
    spec = importlib.util.spec_from_file_location(
        "kit_venue_facts", os.path.join(ddir, "venue_facts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_tree(path):
    """{node: {parent, cuisine, label, words}} from kind_tree.tsv, file order."""
    tree = {}
    for p in _rows(path, 4):
        words = [] if len(p) < 5 or p[4] == "-" else [w.strip() for w in p[4].split(",") if w.strip()]
        tree[p[0]] = {"parent": None if p[1] == "-" else p[1],
                      "cuisine": None if p[2] == "-" else p[2],
                      "label": p[3], "words": words}
    return tree


def load_seed(K):
    return [p[0] for p in _rows(os.path.join(K.SCRIPT_DIR, "body_food_token.seed.tsv"), 2)]


def local_rows(K, conn=None):
    """[(key, {"parent", "label"})] node-add wrote, oldest first; [] when
    the kit DB is absent or unreadable (a read never fails on the overlay)."""
    own = conn is None
    try:
        if own:
            if not os.path.isfile(K.DB_PATH):
                return []
            conn = _ro(K)
        rows = conn.execute("SELECT key, value FROM config WHERE key LIKE ? "
                            "ORDER BY updated_at, key;",
                            (LOCAL_NODE_PREFIX + "%",)).fetchall()
    except Exception:                                 # noqa: BLE001
        return []
    finally:
        if own and conn is not None:
            conn.close()
    out = []
    for key, val in rows:
        try:
            v = json.loads(val)
        except (TypeError, ValueError):
            continue
        if isinstance(v, dict) and v.get("parent") and v.get("label"):
            out.append((key[len(LOCAL_NODE_PREFIX):], v))
    return out


def merge_local(tree, rows):
    """Add node-add nodes under parents already in the tree (a parent may
    itself be a local node); a key the shipped tree has is never shadowed."""
    pending = [r for r in rows if r[0] not in tree]
    while pending:
        rest = [r for r in pending if r[1]["parent"] not in tree]
        for key, v in pending:
            if v["parent"] in tree and key not in tree:
                tree[key] = {"parent": v["parent"],
                             "cuisine": tree[v["parent"]]["cuisine"],
                             "label": v["label"], "words": [], "local": True}
        if len(rest) == len(pending):
            break
        pending = rest
    return tree


class Book(object):
    """The dining rulebook (words, tree, contains, verdict words), loaded once."""

    def __init__(self, K, conn=None):
        self.dir = dining_dir(K)
        if not self.dir:
            raise RuntimeError("dining-session skill (kind_tree.tsv) not installed")
        self.vf = _venue_facts(self.dir)
        self.kinds = self.vf.load_kinds(os.path.join(self.dir, "kinds.tsv"))
        self.tree = merge_local(load_tree(os.path.join(self.dir, "kind_tree.tsv")),
                                local_rows(K, conn))
        self.seed = set(load_seed(K))
        self.contains = {}
        for node, tok, cert in _rows(os.path.join(self.dir, "food_contains.tsv"), 3):
            if tok not in self.seed or cert not in CERT_RANK:
                raise RuntimeError("food_contains.tsv: %s %s %s not in the frozen seed"
                                   % (node, tok, cert))
            self.contains.setdefault(node, []).append((tok, cert))
        self.vwords = _rows(os.path.join(self.dir, "verdict_words.tsv"), 3)

    # tree -------------------------------------------------------------
    def chain(self, node):
        """[node keys] root first; [] when the key is not in the tree."""
        out, seen = [], set()
        while node and node in self.tree and node not in seen:
            seen.add(node)
            out.append(node)
            node = self.tree[node]["parent"]
        return list(reversed(out))

    def elem(self, node, depth, primary):
        return {"key": node, "label": self.tree[node]["label"],
                "level": "family" if self.tree[node]["parent"] is None else "dish",
                "depth": depth, "primary": bool(primary)}

    def node_chains(self, node):
        """Parent chain, plus [cuisine, node] when the cuisine is a link."""
        base = self.chain(node)
        out = [[self.elem(n, i, True) for i, n in enumerate(base)]]
        cz = self.tree[node]["cuisine"]
        if cz and cz in self.tree and cz not in base:
            out.append([self.elem(cz, 0, False), self.elem(node, 1, True)])
        return out

    def node_contains(self, node, cap=None):
        """{token: certainty} over the node and its ancestors; cap='possible'
        downgrades every row (a venue's non-signature kind)."""
        out = {}
        for n in self.chain(node):
            for tok, cert in self.contains.get(n, ()):
                if cap == "possible":
                    cert = "possible"
                if CERT_RANK[cert] > CERT_RANK.get(out.get(tok), 0):
                    out[tok] = cert
        return out

    def path(self, node):
        return [{"key": n, "label": self.tree[n]["label"]} for n in self.chain(node)]

    # words ------------------------------------------------------------
    def cuisine_of_category(self, category):
        """Cuisine node a place-page category names (the 'ilsikdang' word -> japanese)."""
        for part in re.split(r"\s*[,>]\s*", category or ""):
            for node, row in self.tree.items():
                if part and part in row["words"]:
                    return node
        return None

    def nodes_of_text(self, text):
        """Tree nodes the words name, best first: a node label / category
        word equal to the text, then kinds.tsv words found in it (longest
        first, venue_facts' masking), then trait words that are tree nodes."""
        t = (text or "").strip()
        if not t:
            return []
        for node, row in self.tree.items():
            if t == row["label"] or t in row["words"]:
                return [node]
        out, traits, cz = [], [], None
        for word, kind, tr, _disp in self.vf._words_in(t, self.kinds):
            if kind == "generic" or "category" in tr:
                cz = cz or self.cuisine_of_category(word)
            if kind in self.tree and kind not in out:
                out.append(kind)
            traits += [x for x in sorted(tr) if x in self.tree]
        for node in traits + ([cz] if cz else []):
            if node not in out:
                out.append(node)
        return out

    def candidates(self, text):
        """Closed option set for words that resolve to nothing: tree nodes
        ranked by shared character bigrams with their label / words
        (deterministic, no embeddings; a one-character query compares
        characters), at most 5. Empty when nothing is shared: one common
        syllable ("chi" in kimchi and chicken) is not a match."""
        def grams(s):
            s = (s or "").replace(" ", "")
            return set(s) if len(s) < 2 else {s[i:i + 2] for i in range(len(s) - 1)}
        q = grams(text)
        scored = []
        for i, (node, row) in enumerate(self.tree.items()):
            names = [row["label"]] + row["words"] + [
                w for w, k, _t, _d in self.kinds if k == node]
            best = max(len(q & grams(n)) for n in names) if names else 0
            if best:
                scored.append((-best, i, node))
        scored.sort()
        return [{"target": NS + n, "path": self.path(n)} for _s, _i, n in scored[:CANDIDATES_MAX]]


# -- availability -------------------------------------------------------------

def module_off(K):
    """True when the owner switched module_dining off (manage.<module>=off or
    its bundle flag off). 'not-onboarded' is NOT off: dining has no onboarding
    and recording never reads management state."""
    try:
        conn = _ro(K) if os.path.isfile(K.DB_PATH) else None
    except Exception:                                 # noqa: BLE001
        conn = None
    try:
        _on, reason = K.manage_state(conn, MODULE)
    except Exception:                                 # noqa: BLE001
        return False
    finally:
        if conn is not None:
            conn.close()
    return reason in ("declined", "bundle-off")


# -- food-kind --------------------------------------------------------------

def _result(query, kind, target, chains, contains):
    return {"v": V, "available": True, "query": query, "kind": kind,
            "target": target, "chains": chains,
            "contains": [{"token": t, "certainty": c} for t, c in sorted(contains.items())]}


def _node_result(book, query, kind, node):
    return _result(query, kind, NS + node, book.node_chains(node), book.node_contains(node))


def _venue_result(book, query, venue_key, venue_label, nodes, category_node):
    """One chain per kind node (+ the category cuisine), each ending at the
    venue. nodes[0] is the signature; others are secondary (possible)."""
    chains, contains = [], {}
    seen = []
    for i, node in enumerate(nodes):
        sig = i == 0
        for ch in book.node_chains(node):
            if ch[0]["primary"] is False and category_node == ch[0]["key"]:
                continue                  # the category chain below says it
            ch[-1]["primary"] = sig
            ch.append({"key": venue_key, "label": venue_label, "level": "venue",
                       "depth": len(ch), "primary": True})
            chains.append(ch)
        for tok, cert in book.node_contains(node, cap=None if sig else "possible").items():
            if CERT_RANK[cert] > CERT_RANK.get(contains.get(tok), 0):
                contains[tok] = cert
        seen += book.chain(node)
    if category_node and category_node not in seen:
        ch = [book.elem(category_node, 0, True)]
        ch.append({"key": venue_key, "label": venue_label, "level": "venue",
                   "depth": 1, "primary": True})
        chains.append(ch)
    return _result(query, "venue", NS + venue_key, chains, contains)


def _venue_facts_in(book, place_id, facts=None, html=None):
    """(name, category, kind tokens) from facts the caller holds: a
    venue_facts.py JSON (--facts) or a saved place page (--html). NEVER
    fetches: without either, the kit holds nothing for a Naver id."""
    if facts is None and html is not None:
        try:
            facts = book.vf.facts(place_id, datetime.datetime(2000, 1, 1, 12, 0), html=html)
        except Exception as e:                        # noqa: BLE001
            raise NotFound("saved place page unreadable: %s" % e)
    if facts is None:
        raise NotFound("no kit-held facts for naver place %s (run the dining-session "
                       "facts step, pass its JSON with --facts)" % place_id)
    if str(facts.get("place_id") or place_id) != str(place_id):
        raise NotFound("--facts is for place %s, not %s" % (facts.get("place_id"), place_id))
    cat = (facts.get("category") or {}).get("value")
    toks = [t for t in (facts.get("kind") or {}).get("tokens") or [] if t in book.tree]
    return facts.get("name") or place_id, cat, toks


def _block_row(conn, token):
    m = BLOCK_RE.match(token)
    col, val = ("id", int(m.group(1))) if m else ("ulid", token)
    return conn.execute(
        "SELECT b.id, b.ulid, b.title, b.source_module, b.place_id, "
        "p.display_name, p.name, COALESCE(p.is_base, 0) "
        "FROM block b LEFT JOIN place p ON p.id = b.place_id "
        "WHERE b.%s=?;" % col, (val,)).fetchone()


def place_naver_id(conn, pid):
    """The Naver place id a kit place carries -- the 4th field of its stored
    directions end (place_coord.naver_ref), else its map_url -- or None."""
    try:
        row = conn.execute("SELECT naver_ref FROM place_coord WHERE place_id=?;",
                           (pid,)).fetchone()
    except Exception:                                 # noqa: BLE001  (pre-066)
        row = None
    parts = ((row[0] if row else None) or "").split(",")
    if len(parts) > 3 and PLACE_ID_RE.match(parts[3].strip()):
        return parts[3].strip()
    try:
        urls = conn.execute("SELECT value FROM place_address WHERE place_id=? "
                            "AND kind='map_url' ORDER BY is_primary DESC, id;",
                            (pid,)).fetchall()
    except Exception:                                 # noqa: BLE001  (pre-050)
        urls = []
    for (url,) in urls:
        m = NAVER_URL_ID.search(url or "")
        if m:
            return m.group(1)
    return None


def dine_attr(conn, bid):
    """The venue facts dine-choose stored on a decided appointment
    ({"naver_id", "category", "kinds"}), or None."""
    if bid is None:
        return None
    row = conn.execute("SELECT value FROM block_attr WHERE block_id=? AND "
                       "attr_token=? ORDER BY source LIMIT 1;",
                       (bid, DINE_ATTR)).fetchone()
    try:
        v = json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def venue_of(book, conn, pid, label, bid=None):
    """(venue key, nodes, category cuisine) of a kit place, as a visit to
    it reads: naver_<id> when the place carries a Naver id, else place_<id>;
    nodes from what dine-choose stored on the visit, else the place name."""
    attr = dine_attr(conn, bid)
    nid = (attr or {}).get("naver_id") or place_naver_id(conn, pid)
    key = "naver_%s" % nid if nid else "place_%d" % pid
    if attr and attr.get("naver_id"):
        nodes = [t for t in attr.get("kinds") or [] if t in book.tree]
        cz = book.cuisine_of_category(attr.get("category"))
        if nodes or cz:
            return key, nodes, cz
    return key, book.nodes_of_text(label), None


def _place_venue(book, conn, query, pid, pdisp, pname, base, bid=None):
    label = pdisp or pname
    if base:
        raise NotFound("place:%d is the home base, not a venue" % pid)
    key, nodes, cz = venue_of(book, conn, pid, label, bid)
    if not nodes and not cz:
        raise NotFound("the kit place %r carries no food word" % label,
                       book.candidates(label))
    return _venue_result(book, query, key, label, nodes, cz)


def food_kind(K, conn_open, query, html=None, facts=None):
    """The section 4.1 object; raises NotFound (exit 3)."""
    book = Book(K)
    q = (query or "").strip()
    if not q:
        raise Usage("empty query")
    if PLACE_ID_RE.match(q):
        name, cat, toks = _venue_facts_in(book, q, facts=facts, html=html)
        cz = book.cuisine_of_category(cat)
        if not toks and not cz:
            raise NotFound("no known kind in the facts for place %s" % q,
                           book.candidates(cat or ""))
        return _venue_result(book, q, "naver_%s" % q, name, toks, cz)
    m = NODE_RE.match(q)
    if m and m.group(1) in book.tree:
        return _node_result(book, q, "dish", m.group(1))
    if q.startswith(NS):
        raise NotFound("no node %s" % q, book.candidates(q[len(NS):]))
    pm = KIT_PLACE_RE.match(q)
    if pm or BLOCK_RE.match(q) or ULID_RE.match(q):
        conn = conn_open()
        try:
            if pm:
                row = conn.execute("SELECT id, display_name, name, COALESCE(is_base,0) "
                                   "FROM place WHERE id=?;", (int(pm.group(1)),)).fetchone()
                if row is None:
                    raise NotFound("no kit place %s" % q)
                return _place_venue(book, conn, q, *row)
            row = _block_row(conn, q)
            if row is None:
                raise NotFound("no kit block %s" % q)
            bid, _ulid, title, module, pid, pdisp, pname, base = row
            if module == "diet":
                title = re.sub(r"\s*\([^)]*\)\s*$", "", title or "").strip()
                nodes = book.nodes_of_text(title)
                if not nodes:
                    raise NotFound("the meal record %r carries no food word" % title,
                                   book.candidates(title))
                return _node_result(book, q, "dish", nodes[0])
            if pid is None:
                raise NotFound("block %d has no recorded place" % bid)
            return _place_venue(book, conn, q, pid, pdisp, pname, base, bid=bid)
        finally:
            conn.close()
    nodes = book.nodes_of_text(q)
    if not nodes:
        raise NotFound("no food node for these words", book.candidates(q))
    return _node_result(book, q, "words", nodes[0])


# -- dining-history ---------------------------------------------------------

def _person_hits(K, conn, tok):
    rows = [(r[0], r[1]) for r in K._person_exact_matches(conn, tok)]
    return rows


def _cand_person(pid, name):
    t = "person:kit_life:%d" % pid
    return {"target": t, "path": [{"key": t, "label": name}]}


def canon_member(K, conn, tok):
    """A member token -> its framework holder id through the kit's merge
    fold. The kit merges a person with `person-del --into`, which re-points
    the rows and keeps the old name as an alias of the survivor: so a name or
    alias folds onto the survivor here, a live id is itself. Refused, never
    guessed: unknown -> NotFound with up to 5 candidates."""
    t = tok.strip()
    if t == "owner":
        return "owner"
    if t.startswith("group:"):
        raise Usage("members are owner / person ids, not a group (%s)" % t)
    m = PERSON_RE.match(t)
    pid = int(m.group(1)) if m else (int(t) if t.isdigit() else None)
    if pid is not None:
        if conn.execute("SELECT 1 FROM persons WHERE id=?;", (pid,)).fetchone():
            return "person:kit_life:%d" % pid
        raise NotFound("no person %s (a merged id is gone; give the name)" % t)
    rows = _person_hits(K, conn, t)
    if len(rows) == 1:
        return "person:kit_life:%d" % rows[0][0]
    near = rows or [(r[0], r[1]) for r in K.person_find(t, conn=conn)]
    raise NotFound("%s person %s" % ("ambiguous" if rows else "no", t),
                   [_cand_person(p, n) for p, n in near])


def auto_group(members):
    """design v2 2.4: group:auto:<first 16 hex of sha256 over the sorted
    canonical members joined by newline>; None outside 2..20 members."""
    ms = sorted(set(members))
    if not (2 <= len(ms) <= GROUP_MAX):
        return None
    return "group:auto:" + hashlib.sha256("\n".join(ms).encode("utf-8")).hexdigest()[:16]


def party_match(members, party, subset):
    """'exact' | 'subset' | None. Default = the visit's party IS the given
    set. --subset adds a party that is a proper subset of the given set with
    at least 2 people (Q4a: the sub-groups of today's party)."""
    g, p = set(members), set(party)
    if p == g:
        return "exact"
    if subset and p < g and len(p) >= 2:
        return "subset"
    return None


def _surfaces(conn, pid):
    r = conn.execute("SELECT name, display_name, aliases FROM persons WHERE id=?;",
                     (pid,)).fetchone()
    if not r:
        return []
    out = [r[0]] + ([r[1].strip()] if r[1] and r[1].strip() else [])
    out += [a.strip() for a in (r[2] or "").split(",") if a.strip()]
    return out


def party_surfaces(conn, pids):
    """{holder: [names]} for a visit's party. A three-syllable Hangul name
    also answers to its given name (the last two syllables: the owner says
    "Hyeongseok-i" for a full name), unless another party person shares that
    surface -- then neither gets it."""
    raw = {}
    for p in pids:
        names = _surfaces(conn, p)
        for n in list(names):
            if HANGUL_FULL_NAME.match(n) and n[1:] not in names:
                names.append(n[1:])
        raw["person:kit_life:%d" % p] = names
    seen = {}
    for names in raw.values():
        for n in set(names):
            seen[n] = seen.get(n, 0) + 1
    return {h: [n for n in names if seen.get(n) == 1] for h, names in raw.items()}


def read_verdicts(book, text, party, surfaces, venue, ref_base, asked=False):
    """[(holder, verdict, ref)] from one recorded text, clause by clause.
    A clause names a party person -> that person's verdict; a group word ->
    the party's auto-group; else the owner's own. Rule-based (verdict_words.tsv);
    a clause no rule reads gives no verdict. asked=True: the text answers a
    question about the venue itself, so an evaluation word needs no anchor."""
    words = {c: [] for c in ("taste", "eval", "anchor", "negate", "group")}
    for cls, w, verdict in book.vwords:
        if cls in words:
            words[cls].append((w, verdict))
    flip = {"liked": "disliked", "disliked": "liked", "neutral": "neutral"}
    out = []
    clauses = [c.strip() for c in CLAUSE_SPLIT.split(text or "") if c.strip()]
    for i, cl in enumerate(clauses, 1):
        verdict = None
        for w, v in sorted(words["taste"], key=lambda x: -len(x[0])):
            if w in cl:
                verdict = v
                break
        if verdict is None:
            anchored = (asked or any(w in cl for w, _v in words["anchor"])
                        or (venue and venue in cl)
                        or bool(book.vf._words_in(cl, book.kinds)))
            if anchored:
                for w, v in sorted(words["eval"], key=lambda x: -len(x[0])):
                    j = cl.find(w)
                    if j < 0:
                        continue
                    neg = any(cl[max(0, j - len(n)):j] == n for n, _v in words["negate"])
                    verdict = flip[v] if neg else v
                    break
        if verdict is None:
            continue
        holder = "owner"
        named = [h for h, names in surfaces.items() if any(n and n in cl for n in names)]
        if len(named) == 1:
            holder = named[0]
        elif any(w in cl for w, _v in words["group"]):
            holder = auto_group(party) or "owner"
        out.append((holder, verdict, "%s#%d" % (ref_base, i)))
    return out


def _sources(K, conn, bid, ulid, retro_line):
    """[(ref base, text)] the kit recorded about this visit, owner's words."""
    out = []
    if retro_line and retro_line.strip():
        out.append(("kit_life:block:%s:retro_line" % ulid, retro_line))
    for root in sorted(K._block_bundle_roots(conn, bid)):
        for eulid, reaction in conn.execute(
                "SELECT ulid, reaction FROM event WHERE (id=? OR parent_id=?) "
                "AND reaction IS NOT NULL AND TRIM(reaction)<>'' ORDER BY id;",
                (root, root)):
            out.append(("kit_life:event:%s:reaction" % eulid, reaction))
    return out


def visit_talk_verdicts(conn, bid):
    try:
        import visit_talk
    except ImportError:
        return [], set()
    return visit_talk.history_verdicts(conn, bid)


def _at(start_at, date):
    return start_at or date or ""


def dining_history(K, conn, member_tokens, subset=False, since=None,
                   limit=LIMIT_DEFAULT, now=None, book=None):
    members = []
    for tok in member_tokens:
        if tok.strip():
            c = canon_member(K, conn, tok)
            if c not in members:
                members.append(c)
    if not members:
        raise Usage("no members given")
    members.sort()
    pids = [int(m.rsplit(":", 1)[1]) for m in members if m != "owner"]
    now = now or K.now_utc()
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(now, "strftime") else str(now)
    # The kit calendar is the owner's: the owner is in every visit's party.
    # A visit with nobody else is a solo meal, never a party match.
    if "owner" not in members or not pids:
        return {"v": V, "available": True, "members": members, "visits": [],
                "truncated": False}
    marks = ",".join("?" * len(pids))
    rows = conn.execute(
        "SELECT DISTINCT b.id, b.ulid, b.start_at, b.date, b.place_id, "
        "p.display_name, p.name, b.retro_line "
        "FROM block b JOIN event_persons ep ON ep.event_id = b.id "
        "JOIN place p ON p.id = b.place_id "
        "WHERE ep.person_id IN (%s) AND b.kind_token IN (%s) "
        "AND (b.settled_outcome IS NULL OR b.settled_outcome <> 'abandoned') "
        "AND NOT EXISTS (SELECT 1 FROM block_attr c WHERE c.block_id=b.id "
        " AND c.attr_token='block_class') "
        "AND COALESCE(b.start_at, b.date) <= ? "
        "ORDER BY COALESCE(b.start_at, b.date) DESC, b.id DESC;"
        % (marks, ",".join("?" * len(VISIT_KINDS))),
        tuple(pids) + VISIT_KINDS + (now_s,)).fetchall()
    visits, truncated = [], False
    for bid, ulid, sa, date, pid, pdisp, pname, rl in rows:
        at = _at(sa, date)
        if since and at < since:
            continue
        party_ids = [r[0] for r in conn.execute(
            "SELECT person_id FROM event_persons WHERE event_id=? ORDER BY person_id;",
            (bid,))]
        party = sorted(["owner"] + ["person:kit_life:%d" % p for p in party_ids])
        match = party_match(members, party, subset)
        if match is None:
            continue
        if len(visits) >= limit:
            truncated = True
            break
        venue = pdisp or pname
        target = None
        if book is not None:
            vkey, nodes, cz = venue_of(book, conn, pid, venue, bid)
            target = NS + vkey if (nodes or cz) else None
        surfaces = party_surfaces(conn, party_ids)
        # the evening small talk's captured verdicts (visit_talk.py), with
        # the owner's words; a retro line that IS a captured reply is not
        # read a second time.
        verdicts, told = visit_talk_verdicts(conn, bid)
        refs = ["kit_life:block:%s:visit_talk" % ulid] if verdicts or told else []
        for ref_base, text in _sources(K, conn, bid, ulid, rl):
            refs.append(ref_base)
            if book is None or " ".join(text.split()) in told:
                continue
            for holder, verdict, ref in read_verdicts(book, text, party, surfaces,
                                                      venue, ref_base):
                verdicts.append({"holder": holder, "verdict": verdict,
                                 "said_by": "owner", "ref": ref})
        visits.append({"visit": "dine_visit:%s" % ulid, "at": at, "venue": venue,
                       "target": target, "party": party, "match": match,
                       "verdicts": verdicts, "refs": refs})
    return {"v": V, "available": True, "members": members, "visits": visits,
            "truncated": truncated}


# -- node-add -----------------------------------------------------------------

def node_add(K, conn, parent, label, key=None):
    """Add one food node under `parent` (the food-kind exit-3 "new" path,
    design v2 s4.1). Find-first: a node already labelled `label` is
    returned with created False. Returns (node key, created)."""
    book = Book(K, conn)
    lab = " ".join((label or "").split())
    if not lab:
        raise Usage("--label is empty")
    pm = NODE_RE.match((parent or "").strip())
    par = pm.group(1) if pm else None
    if par not in book.tree:
        raise NotFound("no parent node %s" % parent, book.candidates(parent or ""))
    for node, row in book.tree.items():
        if lab == row["label"] or lab in row["words"]:
            return book, node, False
    if key is not None:
        key = key[len(NS):] if key.startswith(NS) else key
        if not KEY_RE.match(key):
            raise Usage("--key must be lowercase ascii [a-z][a-z0-9_]")
        if key in book.tree:
            raise Usage("--key %s is already a node (%s)" % (key, book.tree[key]["label"]))
    else:
        key = "n_" + hashlib.sha256(lab.encode("utf-8")).hexdigest()[:10]
        if key in book.tree:
            raise Usage("generated key %s is taken; pass --key" % key)
    conn.execute("BEGIN IMMEDIATE;")
    try:
        conn.execute("INSERT INTO config(key, value, updated_at) VALUES(?, ?, ?);",
                     (LOCAL_NODE_PREFIX + key,
                      json.dumps({"parent": par, "label": lab}, ensure_ascii=False),
                      K.now_utc()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return Book(K, conn), key, True


# -- CLI --------------------------------------------------------------------

FOOD_KIND_USAGE = (
    "usage: kit_life.sh food-kind <dish-id | venue-id | \"words\"> [--domain food] "
    "[--facts FILE | --html FILE] --json")
NODE_ADD_USAGE = (
    "usage: kit_life.sh node-add --parent <node | kit_life:food:<node>> "
    "--label \"<words>\" [--key <ascii key>] [--domain food] --json")
HISTORY_USAGE = (
    "usage: kit_life.sh dining-history --members <owner|person:kit_life:<id>|name>[,...] "
    "[--subset] [--since ISO] [--limit N] --json")


def _ro(K):
    import ro_open
    return ro_open.open_ro(K.DB_PATH)


def _emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()
    if code:
        sys.exit(code)


def _usage(msg, usage):
    print("%s\n%s" % (msg, usage) if msg else usage, file=sys.stderr)
    sys.exit(2)


def _common(K, argv, usage):
    argv = list(argv)
    if "--json" not in argv:
        _usage("--json is required", usage)
    argv = [a for a in argv if a != "--json"]
    argv, domain = K._pop_opt(argv, "--domain")
    if domain is not None and domain != DOMAIN:
        _usage("--domain: only %s" % DOMAIN, usage)
    return argv


def _run(K, fn, query, usage):
    """The shared exit contract around one verb body."""
    try:
        return fn()
    except Usage as e:
        _usage(str(e), usage)
    except NotFound as e:
        out = {"v": V, "available": True, "error": "not-found", "why": e.why,
               "candidates": e.candidates}
        if query is not None:
            out["query"] = query
            out["new"] = "node-add"
        _emit(out, 3)
    except SystemExit:
        raise
    except Exception as e:                            # noqa: BLE001
        print("internal: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        sys.exit(1)


def cli_food_kind(K, argv):
    argv = _common(K, argv, FOOD_KIND_USAGE)
    argv, html_path = K._pop_opt(argv, "--html")
    argv, facts_path = K._pop_opt(argv, "--facts")
    if len(argv) != 1 or not argv[0].strip() or (html_path and facts_path):
        _usage(None, FOOD_KIND_USAGE)
    query = argv[0].strip()

    def body():
        try:
            Book(K)
        except RuntimeError:
            return _emit({"v": V, "available": False})
        if module_off(K):
            return _emit({"v": V, "available": False})
        html = facts = None
        if html_path:
            with open(html_path, encoding="utf-8") as fh:
                html = fh.read()
        if facts_path:
            with open(facts_path, encoding="utf-8") as fh:
                facts = json.load(fh)
        _emit(food_kind(K, lambda: _ro(K), query, html=html, facts=facts))
    _run(K, body, query, FOOD_KIND_USAGE)


def cli_dining_history(K, argv):
    argv = _common(K, argv, HISTORY_USAGE)
    subset = "--subset" in argv
    argv = [a for a in argv if a != "--subset"]
    argv, members = K._pop_opt(argv, "--members")
    argv, since = K._pop_opt(argv, "--since")
    argv, limit = K._pop_opt(argv, "--limit")
    if argv or not members:
        _usage(None, HISTORY_USAGE)
    if since and not re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?Z?)?$", since):
        _usage("--since takes an ISO date or datetime", HISTORY_USAGE)
    if limit is not None and (not limit.isdigit() or int(limit) < 1):
        _usage("--limit takes a positive integer", HISTORY_USAGE)

    def body():
        if not os.path.isfile(K.DB_PATH) or module_off(K):
            return _emit({"v": V, "available": False})
        try:
            book = Book(K)
        except RuntimeError:
            return _emit({"v": V, "available": False})
        conn = _ro(K)
        try:
            _emit(dining_history(K, conn, re.split("[,\\uff0c]", members),
                                 subset=subset, since=since,
                                 limit=int(limit) if limit else LIMIT_DEFAULT,
                                 book=book))
        finally:
            conn.close()
    _run(K, body, None, HISTORY_USAGE)


def cli_node_add(K, argv):
    argv = _common(K, argv, NODE_ADD_USAGE)
    argv, parent = K._pop_opt(argv, "--parent")
    argv, label = K._pop_opt(argv, "--label")
    argv, key = K._pop_opt(argv, "--key")
    if argv or not parent or label is None:
        _usage(None, NODE_ADD_USAGE)

    def body():
        if not os.path.isfile(K.DB_PATH) or module_off(K):
            return _emit({"v": V, "available": False})
        try:
            Book(K)
        except RuntimeError:
            return _emit({"v": V, "available": False})
        conn = K.event_conn()
        try:
            book, node, created = node_add(K, conn, parent, label, key=key)
        finally:
            conn.close()
        _emit({"v": V, "available": True, "target": NS + node, "created": created,
               "path": book.path(node)})
    _run(K, body, None, NODE_ADD_USAGE)


if __name__ == "__main__":
    sys.exit("food_pref.py is driven by kit_life.sh (food-kind / dining-history / node-add)")
