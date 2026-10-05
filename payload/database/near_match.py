#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""near_match.py -- the ONE near-variant check every name/alias create path
runs before it writes a new row.  Stdlib only; imports nothing from kit_life.

WHY (owner, 2026-09-28 12:26).  Ag registered "유디학동치과" as a NEW place while
"학동유디치과" (place 20, home<->clinic 15 min declared) was on file.  Every
resolver in the kit matches name/alias by EQUALITY, so a word-order variant
missed, the appointment fell back to the 60-minute stopgap and the owner was
asked a question he had already answered.  His rule: "alias 쓰는 곳들은 다
이렇게 해줘야 할듯. 나도 정확한 이름이 아니고 기억에 의존하다보니" -- every
resolver that matches by name/alias must catch the near variants a person
produces from memory.

WHAT THIS IS NOT.  It is not fuzzy RESOLUTION.  The place lane forbids runtime
fuzzy matching (rule 6) and that stays true: nothing here ever answers "this
IS that row".  A near candidate only STOPS A CREATE and turns it into ONE
question to the owner.  Yes -> the new string becomes an alias of the existing
row (after which exact matching finds it forever).  No -> the create goes
ahead with an explicit confirmation, and the refusal is stored so the same
pair is never asked again.  A false "same" costs a merge; an extra question
costs one turn -- so every threshold below leans toward NOT firing.

SIGNALS, strongest first (the first that fires is the one reported):
  compact_equal  equal after NFKC + casefold + dropping whitespace/punctuation
                 ("학동 유디치과" == "학동유디치과")
  noise_equal    equal after stripping one declared suffix/prefix noise word
                 ("학동유디치과의원" == "학동유디치과", "강남역" == "강남").  Two
                 DIFFERENT noise words from different groups do not match
                 ("신논현역" vs "신논현점": a station is not a store).
  variant_equal  (food only) equal after dropping portion tokens ("120g",
                 "2개", "(1조각)") and a flavor suffix ("맛"/"소스") on a
                 non-first word ("허닭 실온닭가슴살 (양념치킨맛)" ==
                 "허닭 실온 닭가슴살 양념치킨소스 (120g)").  reordered /
                 edit_distance also run on these keys.  WHY (owner, 2026-09-28
                 food-merge): the live cache held "X" and "X (120g)" as two
                 foods, and 양념치킨맛 / 양념치킨소스 as two, and 2.35.0 caught
                 neither.
  reordered      same character multiset, >= 4 characters
                 ("학동유디치과" == "유디학동치과")
  edit_distance  Levenshtein over JAMO (a syllable is split into its
                 initial/vowel/final letters) <= 1 when both names are >= 5
                 characters, <= 2 when both are >= 10 (profile may cap it lower
                 or switch it off).  Jamo, not syllables, because a memory/typing
                 slip changes ONE letter -- 날/널, 떼/테, 과/고 -- while a
                 different word changes a whole syllable: measured on the live
                 food cache, 말랑두부 vs 말린두부 (two different foods) is 1
                 syllable but 2 jamo apart, and fired as "same" on syllables.
  contains       one compact form inside the other, the shorter >= 3 chars
                 (2 for people) and >= half the longer
VETOES (any one kills every signal except compact_equal):
  * the two differ in their ASCII letters/digits ("2호점"/"3호점", "A룸"/"D룸",
    "현대카드M"/"현대카드") -- in a Korean name the Latin/digit part is almost
    always the discriminating part;
  * the extra part of the longer name is a RELATIONAL word ("회사" vs
    "회사 근처", "역 앞") -- that names a different spot on purpose.

All word lists are DATA (PROFILES below), never literals scattered in code.
"""
import sqlite3
import unicodedata

SIGNALS = ("compact_equal", "noise_equal", "variant_equal", "reordered",
           "edit_distance", "contains")
_RANK = {s: i for i, s in enumerate(SIGNALS)}

# Relational qualifiers: a name that is another name PLUS one of these points
# at a different spot on purpose.  Shared by every profile.
RELATIONAL = ("근처", "앞", "옆", "뒤", "건너편", "맞은편", "주변", "부근", "입구",
              "쪽", "방향", "near")

# Per-domain data.  suffix_groups/prefix_groups: words stripped from the END /
# START of the compact form; two stripped words match only when they sit in
# the same group (or one side stripped nothing).  drop: literals removed
# anywhere BEFORE compaction (corporate marks whose punctuation would
# otherwise leave a bare "주").  noun: which question wording applies.
PROFILES = {
    "place": {
        "noun": "place",
        "suffix_groups": (("본점", "지점", "직영점", "점"),
                          ("의원", "병원", "클리닉"),
                          ("역",)),
        "prefix_groups": (),
        "drop": (),
        "contains_min": 3,
        "edit": True, "edit_max": 2,
    },
    "person": {
        "noun": "person",
        # honorifics / titles said after a name from memory
        "suffix_groups": (("님", "씨"),
                          ("선생님", "선생", "쌤"),
                          ("대표님", "대표"), ("팀장님", "팀장"),
                          ("과장님", "과장"), ("부장님", "부장"),
                          ("실장님", "실장"), ("원장님", "원장"),
                          ("교수님", "교수"),
                          ("형", "누나", "언니", "오빠")),  # user-gate-lint: match-corpus -- third-party titles stripped from other people's names when matching
        "prefix_groups": (),
        "drop": (),
        "contains_min": 2,
        # Two different people can be one syllable apart: edit distance
        # is meaningless on 3-syllable names and is switched off.
        "edit": False, "edit_max": 0,
    },
    "thing": {
        "noun": "thing",
        "suffix_groups": (),
        "prefix_groups": (),
        "drop": ("(주)", "㈜", "주식회사", "(유)"),
        # Payment instruments: one name inside another is a DIFFERENT product
        # (live: 토스뱅크 / 모하토스뱅크, 네이버페이 / 네이버페이하나체크).
        "contains_min": None,
        "edit": True, "edit_max": 2,
    },
    "food": {
        "noun": "thing",
        "suffix_groups": (),
        "prefix_groups": (),
        "drop": (),
        # 김치찌개 inside 참치김치찌개 is a different dish: no containment.
        "contains_min": None,
        "edit": True, "edit_max": 1,
        # Words that say HOW MUCH or WHICH SAUCE-WORD, not WHICH FOOD.
        # units: a word that is a number + one of these ("120g", "2개") is a
        # portion, dropped (a whole bracket of it too).  flavor_suffixes:
        # stripped from the end of a NON-FIRST word only, remainder >= 2
        # ("양념치킨맛" -> "양념치킨"); a first/only word keeps it because there
        # it names the food itself (마늘 vs 마늘소스, 굴소스).
        "variant": {
            "units": ("kg", "mg", "g", "ml", "l", "oz", "kcal",
                      "개", "알", "줄", "조각", "쪽", "장", "스쿱", "인분",
                      "봉지", "봉", "팩", "컵", "잔", "병", "캔", "마리",
                      "공기", "그릇", "피스", "pcs", "ea"),
            "flavor_suffixes": ("소스", "맛"),
        },
    },
    "exercise": {
        "noun": "thing",
        "suffix_groups": (),
        "prefix_groups": (),
        "drop": (),
        "contains_min": None,
        # snake_case ASCII names: the ASCII veto would kill every signal, so
        # it is off here; distance is capped at 1 (incline/decline differ by 2).
        "ascii_veto": False,
        "edit": True, "edit_max": 1,
    },
}

# Which question a domain asks.  The wording itself lives in the kit copy
# table (kit bundle config/i18n/<lang>.bundle.json, keys near_match.ask.<noun>[.multi]).
DOMAIN_PROFILE = {"place": "place", "person": "person", "method": "thing",
                  "food": "food", "exercise": "exercise"}

EDIT_MIN_LEN = 5          # both names at least this long for distance 1
EDIT_WIDE_LEN = 10        # ... and this long for distance 2
REORDER_MIN_LEN = 4
CONTAINS_MIN_RATIO = 0.5
MAX_CANDIDATES = 3


def _profile(domain):
    return PROFILES[DOMAIN_PROFILE.get(domain, domain)]


def _nfkc(text):
    return unicodedata.normalize("NFKC", str(text or "")).casefold()


def compact(text, domain="place"):
    """Comparison key: NFKC + casefold, profile `drop` literals removed, then
    every character that is not a letter/digit dropped (whitespace, '_', '-',
    '.', brackets...).  Hangul syllables are letters, so they survive."""
    s = _nfkc(text)
    for d in _profile(domain)["drop"]:
        s = s.replace(_nfkc(d), " ")
    return "".join(ch for ch in s if ch.isalnum())


def _tokens(text):
    return [t for t in "".join(ch if ch.isalnum() else " "
                               for ch in _nfkc(text)).split() if t]


def _strip_noise(key, prof):
    """(core, suffix_group_index or None, prefix_group_index or None).
    Longest noise word first; at most one suffix and one prefix; the core
    must keep >= 2 characters or nothing is stripped."""
    core, sgi, pgi = key, None, None
    cands = sorted(((w, i) for i, g in enumerate(prof["suffix_groups"])
                    for w in g), key=lambda x: -len(x[0]))
    for w, i in cands:
        if core.endswith(w) and len(core) - len(w) >= 2:
            core, sgi = core[:-len(w)], i
            break
    cands = sorted(((w, i) for i, g in enumerate(prof["prefix_groups"])
                    for w in g), key=lambda x: -len(x[0]))
    for w, i in cands:
        if core.startswith(w) and len(core) - len(w) >= 2:
            core, pgi = core[len(w):], i
            break
    return core, sgi, pgi


def _is_portion(tok, units):
    """'120g' / '2개' / '0.5공기': digits (one optional '.') then a unit."""
    i = 0
    while i < len(tok) and (tok[i].isdigit() or tok[i] == "."):
        i += 1
    num, unit = tok[:i], tok[i:]
    return (bool(num) and num.count(".") <= 1 and num.strip(".") != ""
            and unit in units)


def _variant_key(text, prof, domain):
    """Compact key with portion words and non-first flavor suffixes dropped
    (profile `variant`).  None when the profile has no variant data or the
    result would be empty (then the plain key stands)."""
    var = prof.get("variant")
    if not var:
        return None
    s = _nfkc(text)
    for d in prof["drop"]:
        s = s.replace(_nfkc(d), " ")
    units = set(_nfkc(u) for u in var["units"])
    toks = _tokens(s)
    kept = [t for t in toks if not _is_portion(t, units)]
    if not kept:
        return None
    sufs = sorted((_nfkc(x) for x in var["flavor_suffixes"]), key=len,
                  reverse=True)
    out = [kept[0]]
    for t in kept[1:]:
        for suf in sufs:
            if t.endswith(suf) and len(t) - len(suf) >= 2:
                t = t[:-len(suf)]
                break
        out.append(t)
    return "".join(out)


def levenshtein(a, b, cap=None):
    """Plain Levenshtein on code points.  `cap`: stop early once every cell
    of a row exceeds it (returns cap + 1)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        if cap is not None and min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def jamo(text):
    """Hangul syllables -> initial/vowel/final jamo; everything else as is."""
    out = []
    for ch in text:
        o = ord(ch) - 0xAC00
        if 0 <= o < 11172:
            out.append(chr(0x1100 + o // 588))
            out.append(chr(0x1161 + (o % 588) // 28))
            if o % 28:
                out.append(chr(0x11A7 + o % 28))
        else:
            out.append(ch)
    return "".join(out)


def _ascii_part(key):
    return sorted(ch for ch in key if ch.isascii() and ch.isalnum())


def _relational_extra(a_raw, b_raw, ka, kb):
    """True when the longer name's extra part is a relational qualifier."""
    rel = set(_nfkc(w) for w in RELATIONAL)
    short_raw, long_raw = (a_raw, b_raw) if len(ka) <= len(kb) else (b_raw, a_raw)
    short_tok = set(_tokens(short_raw))
    if any(t in rel for t in _tokens(long_raw) if t not in short_tok):
        return True
    ks, kl = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
    if ks and ks in kl:
        rest = kl.replace(ks, "", 1)
        if rest in rel or any(rest.startswith(r) or rest.endswith(r)
                              for r in rel if len(r) >= 2):
            return True
    return False


def compare(a, b, domain="place"):
    """The signal under which `a` and `b` look like one thing, or None.
    Symmetric.  Exact raw equality is None too -- that is the exact resolver's
    job and it has already run by the time anyone asks this."""
    prof = _profile(domain)
    if a is None or b is None:
        return None
    ka, kb = compact(a, domain), compact(b, domain)
    if not ka or not kb:
        return None
    if ka == kb:
        return None if " ".join(str(a).split()) == " ".join(str(b).split()) \
            else "compact_equal"
    va, vb = _variant_key(a, prof, domain), _variant_key(b, prof, domain)
    if va is None or vb is None:
        va = vb = None
    # The ASCII veto reads the variant key when there is one: "(120g)" is a
    # portion, not the discriminating Latin/digit part of the name.
    xa, xb = (va, vb) if va is not None else (ka, kb)
    if prof.get("ascii_veto", True) and _ascii_part(xa) != _ascii_part(xb):
        return None
    if _relational_extra(a, b, ka, kb):
        return None
    ca, sa, pa = _strip_noise(ka, prof)
    cb, sb, pb = _strip_noise(kb, prof)
    if ca == cb:
        # Same core, and the noise words agree (or one side has none) -> one
        # thing.  Same core under two DIFFERENT groups (신논현역 / 신논현점) is
        # the noise word doing its job of telling them apart: no signal.
        if (sa is None or sb is None or sa == sb) \
                and (pa is None or pb is None or pa == pb):
            return "noise_equal"
        return None
    if va is not None and va == vb:
        return "variant_equal"
    pairs = [(ka, kb), (ca, cb)]
    if va is not None:
        pairs.append((va, vb))
    for x, y in pairs:
        if len(x) == len(y) and len(x) >= REORDER_MIN_LEN \
                and sorted(x) == sorted(y):
            return "reordered"
    if prof["edit"]:
        for x, y in [(ka, kb)] + ([(va, vb)] if va is not None else []):
            n = min(len(x), len(y))
            allow = 0
            if n >= EDIT_WIDE_LEN:
                allow = 2
            elif n >= EDIT_MIN_LEN:
                allow = 1
            allow = min(allow, prof["edit_max"])
            ja, jb = jamo(x), jamo(y)
            if allow and abs(len(ja) - len(jb)) <= allow \
                    and levenshtein(ja, jb, cap=allow) <= allow:
                return "edit_distance"
    cmin = prof["contains_min"]
    if cmin is not None:
        for x, y in ((ka, kb), (ca, cb)):
            s, l = (x, y) if len(x) <= len(y) else (y, x)
            if len(s) >= cmin and s in l and len(s) >= CONTAINS_MIN_RATIO * len(l):
                return "contains"
    return None


def candidates(query, rows, domain="place", refused=()):
    """Rank existing rows that `query` may be a variant of.

    rows    iterable of (ref, name, [aliases...]) -- ref is the row's stable id
            (int id, key, snake_case name ...), name its display name.
    refused refs the owner already said are NOT this query (skipped).

    Returns [{'ref', 'name', 'signal', 'matched'}], best first, at most
    MAX_CANDIDATES.  `matched` is the stored string (name or alias) that fired.
    A row that matches query EXACTLY (raw, whitespace-collapsed) makes the
    whole answer [] -- the exact resolver owns that case."""
    qn = " ".join(str(query or "").split())
    if not compact(qn, domain):
        return []
    refused = set(str(r) for r in refused)
    best = {}
    for ref, name, aliases in rows:
        strings = [name] + [x for x in (aliases or []) if x]
        for s in strings:
            if s is None:
                continue
            if " ".join(str(s).split()) == qn:
                return []
        if str(ref) in refused:
            continue
        for s in strings:
            sig = compare(qn, s, domain)
            if sig is None:
                continue
            cur = best.get(ref)
            if cur is None or _RANK[sig] < _RANK[cur["signal"]]:
                best[ref] = {"ref": ref, "name": name, "signal": sig,
                             "matched": " ".join(str(s).split())}
    out = sorted(best.values(),
                 key=lambda c: (_RANK[c["signal"]], str(c["ref"]).zfill(12)))
    return out[:MAX_CANDIDATES]


# ── refusal memory (060 near_match_refusal) ─────────────────────────────────
# Keyed on (domain, compact(query), ref): the owner said "no, <query> is not
# <ref>", and a spacing variant of the same query is the same answer.
# FAIL-OPEN on a database without the table (pre-060): nothing is remembered,
# so the question may come back -- never a crash, never a silent create.

def refused_refs(conn, domain, query):
    try:
        rows = conn.execute(
            "SELECT existing_ref FROM near_match_refusal "
            "WHERE domain=? AND query_key=?;",
            (domain, compact(query, domain))).fetchall()
    except sqlite3.Error:
        return set()
    return set(r[0] for r in rows)


def record_refusal(conn, domain, query, refs, said=None):
    """Store 'query is not ref' for every ref.  Caller owns the transaction
    (commit).  Returns the number of refs stored, or None when the table does
    not exist (pre-060 database)."""
    key = compact(query, domain)
    n = 0
    try:
        for ref in refs:
            conn.execute(
                "INSERT OR IGNORE INTO near_match_refusal(domain, query_key, "
                "existing_ref, query_text, said) VALUES (?,?,?,?,?);",
                (domain, key, str(ref), " ".join(str(query).split()), said))
            n += 1
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None
        raise
    return n


def question_key(domain, n):
    """Copy-table key for the one question: near_match.ask.<noun>[.multi]."""
    noun = _profile(domain)["noun"]
    return "near_match.ask.%s%s" % (noun, ".multi" if n > 1 else "")
