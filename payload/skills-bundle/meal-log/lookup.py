#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Food nutrition lookup helper (Korea public food DBs).
- barcode: C005 (Food Safety Korea) -> product name -> FoodNtrCpntDbInfo02 nutrition
- name:    FoodNtrCpntDbInfo02 direct search
- cache:   CREW-SHARED food cache in kit_life.db (grams-scaled), plus alias and
           default-portion lookup/registration

API keys are NEVER hardcoded. They are read from a project .env (or the process
environment). If keys are absent, name/barcode public-DB lookups degrade to
{"found": false} instead of crashing -- the skill then falls back to LLM
estimation. The cache path works without any keys.
"""
import sys
import os
import re
import json
import importlib.util
import urllib.request
import urllib.parse

# Skill dir; PROJECT_ROOT is three levels up (.claude/skills/meal-log -> ROOT).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))


def _env_files():
    """Candidate .env files, in priority order. PROJECT_ROOT-relative only."""
    return [
        os.environ.get("DIET_ENV_FILE", ""),
        os.path.join(_PROJECT_ROOT, ".env"),
        os.path.join(_PROJECT_ROOT, ".telegram_bot", ".env"),
        os.path.join(_PROJECT_ROOT, "runtime", ".env"),
    ]


def _load_env_key(name, default=""):
    """Read a key from a project .env, falling back to the process environment.
    No plaintext keys live in source."""
    for env_path in _env_files():
        if not env_path:
            continue
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return os.environ.get(name, default)


FOODSAFETY_KEY = _load_env_key("FOODSAFETY_KEY")
DATAGOV_KEY = _load_env_key("DATAGOV_KEY")
NUTR_API = "http://apis.data.go.kr/1471000/FoodNtrCpntDbInfo02/getFoodNtrCpntDbInq02"

# Brand/restaurant food cache (stores per-serving absolute amounts at `grams`).
# Since migration 029 the SHARED-KNOWLEDGE part -- items / aliases / portions --
# lives in the CREW-SHARED kit_life DB as meal_food / meal_alias / meal_portion,
# NOT in a per-instance JSON file. A food or alias registered by one agent is
# instantly visible to every other agent in the crew. The per-instance JSON was
# the bug: each agent accumulated its own disjoint food and alias set, so the
# same food had to be re-taught -- and re-resolved at full token cost -- once
# per agent.
#
# All access goes through the sibling kit_life SDK, which owns the single DB-path
# source of truth (<ROOT>/database/kit_life.db -> the crew-shared file). Never
# hardcode a DB path here -- every agent in the crew runs this same file.
#
# <ROOT>/database/food_cache.json keeps ONLY the per-instance keys (meal_times /
# fullness_anchors / settings, per migration 029). This module neither reads nor
# writes that file.
LIFEKIT_PATH = os.path.join(_PROJECT_ROOT, "database", "kit_life.py")

_LIFEKIT = None


def kit_life():
    """Import the sibling kit_life SDK once, by path (it is not on sys.path)."""
    global _LIFEKIT
    if _LIFEKIT is None:
        spec = importlib.util.spec_from_file_location("kit_life", LIFEKIT_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"kit_life SDK not found: {LIFEKIT_PATH}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("kit_life", mod)
        spec.loader.exec_module(mod)
        _LIFEKIT = mod
    return _LIFEKIT

# Nutrition field mapping (per 100g).
AMT_MAP = {
    "AMT_NUM1":  ("에너지",   "kcal"),
    "AMT_NUM3":  ("단백질",   "g"),
    "AMT_NUM4":  ("지방",     "g"),
    "AMT_NUM6":  ("탄수화물", "g"),
    "AMT_NUM7":  ("식이섬유", "g"),
    "AMT_NUM8":  ("당류",     "g"),
    "AMT_NUM13": ("나트륨",   "mg"),
    "AMT_NUM24": ("포화지방", "g"),
    "AMT_NUM25": ("트랜스지방", "g"),
}

# meal_food nutrition columns (grams-based absolute) -> display name.
# NOTE: the DB column is `carb` (aligned with the meals table); the JSON era
# called it `carbs`. cache-add still accepts `carbs` on input for compatibility.
CACHE_NUTR_MAP = {
    "kcal":      "에너지",
    "carb":      "탄수화물",
    "protein":   "단백질",
    "fat":       "지방",
    "fiber":     "식이섬유",
    "sugar":     "당류",
    "sodium_mg": "나트륨",
}


def normalize_key(name):
    """Normalize a food name into a cache key: lowercase + strip brackets/symbols
    + collapse whitespace. Same on lookup and store for consistency.
    Byte-matched to kit_life.normalize_food_key so JSON-era keys still resolve."""
    s = name.lower().strip()
    s = re.sub(r"[^0-9a-z가-힣ㄱ-ㅎㅏ-ㅣ\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def cache_lookup(name, grams):
    """Shared-cache lookup (meal_food, alias-resolved first). On hit the SDK has
    already scaled the macros to `grams`. Miss -> None so the caller falls
    through to the public DB. A broken/absent SDK also degrades to None rather
    than crashing the whole lookup (same posture as the old missing-file path)."""
    try:
        res = kit_life().foodcache_lookup(name, grams=grams)
    except Exception as e:                                  # noqa: BLE001
        sys.stderr.write(f"lookup.py: shared cache unavailable: {e}\n")
        return None
    if not res.get("found") or not res.get("base_grams"):
        return None
    result = {
        "food_name": res.get("name"),
        "maker": None,
        "serving_size": res.get("base_grams"),
        "grams": grams,
        "found": True,
        "source": "cache",
        "estimated": bool(res.get("estimated")),
        "updated": res.get("updated", ""),
        "updated_by": res.get("updated_by"),
    }
    for col, disp in CACHE_NUTR_MAP.items():
        val = res.get(col)
        result[disp] = round(float(val), 2) if val is not None else None
    for disp in ("포화지방", "트랜스지방"):
        result[disp] = None
    return result


def receipt(name, mode):
    """Record that this ordered lookup step ran for `name` (migration 043).

    THE POINT (DGN-928): the record step must not be reachable until the
    lookup step has actually run.  That order used to live only in SKILL.md
    prose, and prose carries no execution order -- the model skipped the cache
    lookup and wrote an LLM estimate over a value already stored as measured.
    kit_life.meal_add now refuses a name with no receipt, so this call IS the
    precondition, not an audit trail.

    `name` goes in VERBATIM.  Do not normalize it here, ever: meal_add matches
    the same verbatim string, which is the only reason the match round-trips.
    (The cheaper "it is in the cache so the lookup ran" gate was measured and
    refuted -- 101 of 200 live cache rows have a display name that normalizes
    away from their key, so it refused half the correctly cached foods.)

    Called on a MISS as well as a hit: a miss still means the ordered step ran.

    Never raises.  A broken SDK or database degrades to a stderr note -- the
    reader fails open on the same fault, so the owner can always log a meal."""
    try:
        kit_life().lookup_receipt_write(name, mode)
    except Exception as e:                                  # noqa: BLE001
        sys.stderr.write(f"lookup.py: lookup receipt not written: {e}\n")


def grade(name, verdict):
    """Stamp WHICH branch this lookup took for `name` (migration 048).

    `receipt()` above is written before the cache is consulted, so that every
    exit path leaves one.  This is the other half: once the branch IS known,
    say which it was.  'exact' means the numbers came back pinned (shared-cache
    hit, barcode product, nutrition label, public-DB item); 'estimate' means
    the step ran and found nothing, so whatever the model writes next is a
    guess.

    THE POINT: meal_add asks the satiety question only on 'estimate'.  That
    fact is recorded here, where the branch is taken, rather than inferred at
    the write -- inferring it from cache presence is the same refuted test that
    `receipt()` documents above, and it would fire the question on half the
    foods that were in fact exact hits.

    Never raises, and a failure is not a refusal: an ungraded receipt stays
    NULL, and NULL fails OPEN at the gate.  The owner can always log a meal."""
    try:
        kit_life().lookup_receipt_resolve(name, verdict)
    except Exception as e:                                  # noqa: BLE001
        sys.stderr.write(f"lookup.py: lookup grade not recorded: {e}\n")


def cache_add(payload):
    """Upsert one food into the SHARED cache (meal_food). payload = item fields +
    key_name. `carbs` is still accepted as an input alias for `carb`. The write is
    immediately visible to every other agent in the crew."""
    key, name = kit_life().foodcache_upsert(payload)
    item = {
        "name":      name,
        "grams":     payload.get("grams"),
        "kcal":      payload.get("kcal"),
        "carb":      payload.get("carb", payload.get("carbs")),
        "protein":   payload.get("protein"),
        "fat":       payload.get("fat"),
        "fiber":     payload.get("fiber"),
        "sugar":     payload.get("sugar"),
        "sodium_mg": payload.get("sodium_mg"),
        "source":    payload.get("source", ""),
        "estimated": bool(payload.get("estimated", False)),
        "shared":    True,
    }
    return key, item


def near(name, new_confirmed=False):
    """2026-09-28: the cached foods `name` may be a variant of ("바닐라라테"
    beside "바닐라라떼"). None = nothing near. A broken SDK degrades to None --
    the same posture as cache_lookup; the gate is then simply off."""
    try:
        return kit_life().foodcache_near(name, new_confirmed=new_confirmed)
    except Exception as e:                                  # noqa: BLE001
        sys.stderr.write(f"lookup.py: near-match unavailable: {e}\n")
        return None


def alias_add(alias, canonical):
    """Register alias -> canonical food name in the SHARED cache (meal_alias)."""
    return kit_life().alias_upsert(alias, canonical)


def portion_get(name):
    """Default serving grams for a food (meal_portion, alias-resolved)."""
    try:
        return kit_life().portion_lookup(name)
    except Exception as e:                                  # noqa: BLE001
        sys.stderr.write(f"lookup.py: shared cache unavailable: {e}\n")
        return {"found": False, "key": normalize_key(name)}


def portion_add(name, grams, n=1):
    """Register a default serving in the SHARED cache (meal_portion)."""
    return kit_life().portion_upsert(name, grams, n=n)


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def barcode_to_name(barcode):
    """C005: barcode -> (product name, maker). Needs FOODSAFETY_KEY."""
    if not FOODSAFETY_KEY:
        return None, None
    url = f"https://openapi.foodsafetykorea.go.kr/api/{FOODSAFETY_KEY}/C005/json/1/5/BAR_CD={barcode}"
    d = fetch(url)
    rows = d.get("C005", {}).get("row", [])
    if not rows:
        return None, None
    row = rows[0]
    return row.get("PRDLST_NM"), row.get("BSSH_NM")


def search_nutrition(keyword, maker=None, max_results=5):
    """FoodNtrCpntDbInfo02 name search. Needs DATAGOV_KEY (else empty)."""
    if not DATAGOV_KEY:
        return []
    params = {
        "serviceKey": DATAGOV_KEY,
        "pageNo": 1,
        "numOfRows": max_results,
        "type": "json",
        "FOOD_NM_KR": keyword,
    }
    if maker:
        params["MAKER_NM"] = maker
    url = NUTR_API + "?" + urllib.parse.urlencode(params)
    d = fetch(url)
    return d.get("body", {}).get("items", [])


def extract_nutrition(item, grams):
    """From an item (per-100g) pull the nutrition for `grams`."""
    ratio = grams / 100.0
    result = {
        "food_name": item.get("FOOD_NM_KR"),
        "maker": item.get("MAKER_NM"),
        "serving_size": item.get("SERVING_SIZE"),
        "grams": grams,
        "found": True,
    }
    for key, (name, unit) in AMT_MAP.items():
        val = item.get(key, "")
        if val:
            result[name] = round(float(str(val).replace(",", "")) * ratio, 2)
        else:
            result[name] = None
    return result


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"found": False, "error": "no args"}))
        sys.exit(1)

    # "barcode" | "name" | "cache-add" | "alias-add" | "portion-lookup" | "portion-add"
    mode = sys.argv[1]

    if mode == "cache-add":
        if len(sys.argv) < 3:
            print(json.dumps({"found": False, "error": "cache-add: no json arg"}))
            sys.exit(1)
        try:
            payload = json.loads(sys.argv[2])
        except json.JSONDecodeError as e:
            print(json.dumps({"found": False, "error": f"json parse failed: {e}"}))
            sys.exit(1)
        if "key_name" not in payload:
            print(json.dumps({"found": False, "error": "key_name missing"}))
            sys.exit(1)
        # 2026-09-28 near-match gate: a NEW food key that looks like a cached
        # food is not written. The skill asks the one question in "ask";
        # yes -> alias-add "<name>" "<cached name>", no -> re-run cache-add
        # with "new_confirmed": true (remembered, never asked again). An
        # update of an existing key is never gated (near() returns None).
        hit = near(payload["key_name"],
                   new_confirmed=bool(payload.get("new_confirmed")))
        if hit:
            print(json.dumps({"found": False, "cached": False,
                              "near_match": hit["near_match"],
                              "ask": hit["ask"]}, ensure_ascii=False))
            sys.exit(5)
        payload = {k: v for k, v in payload.items() if k != "new_confirmed"}
        key, item = cache_add(payload)
        # Both strings the caller handed us get a receipt: key_name is what was
        # resolved, and the display `name` (when it differs) is the string the
        # meal will most likely be recorded under.  Both passed through this
        # step verbatim, so neither is an inference.
        # 048: a cache-add carries its own provenance in `source` -- that is
        # the field meal_food already stores to tell a measured row from a
        # guessed one.  An LLM-estimate row is exactly the branch that owes a
        # satiety answer; a label/barcode/DB row is pinned.  A payload with no
        # source is left UNGRADED rather than assumed either way: the gate
        # reads NULL as permission, and inventing a grade here would be the
        # same guess this column exists to replace.
        _src = str(payload.get("source") or "")
        _verdict = ("estimate" if "estimate" in _src.lower()
                    else ("exact" if _src else None))
        for given in dict.fromkeys(
                x for x in (payload.get("key_name"), payload.get("name")) if x):
            receipt(given, "cache-add")
            if _verdict:
                grade(given, _verdict)
        print(json.dumps({"found": True, "cached": True, "shared": True,
                          "key": key, "item": item}, ensure_ascii=False))
        sys.exit(0)

    if mode == "alias-add":
        # alias-add "<alias>" "<canonical>"  -> shared meal_alias
        if len(sys.argv) < 4:
            print(json.dumps({"found": False,
                              "error": "alias-add: need <alias> <canonical>"}))
            sys.exit(1)
        alias, canonical = alias_add(sys.argv[2], sys.argv[3])
        print(json.dumps({"found": True, "cached": True, "shared": True,
                          "alias": alias, "canonical": canonical},
                         ensure_ascii=False))
        sys.exit(0)

    if mode == "portion-lookup":
        # portion-lookup "<name>"  -> default serving grams (shared meal_portion)
        if len(sys.argv) < 3:
            print(json.dumps({"found": False, "error": "portion-lookup: no name"}))
            sys.exit(1)
        print(json.dumps(portion_get(sys.argv[2]), ensure_ascii=False))
        sys.exit(0)

    if mode == "portion-add":
        # portion-add "<name>" <grams> [n]  -> shared meal_portion
        if len(sys.argv) < 4:
            print(json.dumps({"found": False,
                              "error": "portion-add: need <name> <grams> [n]"}))
            sys.exit(1)
        n = sys.argv[4] if len(sys.argv) > 4 else 1
        key, grams, nn = portion_add(sys.argv[2], sys.argv[3], n=n)
        print(json.dumps({"found": True, "cached": True, "shared": True,
                          "key": key, "grams": grams, "n": nn},
                         ensure_ascii=False))
        sys.exit(0)

    if len(sys.argv) < 3:
        print(json.dumps({"found": False, "error": "no query"}))
        sys.exit(1)
    query = sys.argv[2]
    grams = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0

    if mode == "name":
        # The receipt is written FIRST, before the cache hit below can exit:
        # every path out of this branch (hit, miss, API answer, API failure)
        # has run the ordered step, so every path must leave the receipt.
        receipt(query, "name")
        # Every name this run resolves, for the 048 grade stamp below.  The
        # barcode branch adds a second one; the name branch only ever has this.
        graded = [query]
        # 1) shared cache first
        cached = cache_lookup(query, grams)
        if cached:
            # 048: pinned by a real cache row -- no satiety question.
            grade(query, "exact")
            print(json.dumps({"found": True, "candidates": [cached]}, ensure_ascii=False))
            sys.exit(0)
        # 2026-09-28: a cache MISS that looks like a cached food rides along
        # as "near_match" + "ask" on whatever this lookup prints next -- the
        # owner's word from memory may be a food already on file.
        near_hit = near(query)

    if mode == "barcode":
        # Two receipts, both verbatim strings this step actually handled: the
        # barcode as typed, and -- once the product DB answers -- the resolved
        # product name, which is the string the meal gets recorded under.
        receipt(query, "barcode")
        graded = [query]
        name, maker = barcode_to_name(query)
        if not name:
            # The step ran and produced no product: anything recorded under
            # this string from here on is a guess.
            grade(query, "estimate")
            print(json.dumps({"found": False, "error": "barcode lookup failed"}))
            sys.exit(0)
        receipt(name, "barcode")
        graded.append(name)
        items = search_nutrition(name, maker)
        if not items:
            items = search_nutrition(name)
        if not items:
            keywords = name.split()[:2]
            items = search_nutrition(" ".join(keywords))
    elif mode == "name":
        items = search_nutrition(query)
        if not items:
            words = query.split()
            for n in range(len(words) - 1, 1, -1):
                partial = " ".join(words[:n])
                items = search_nutrition(partial)
                if items:
                    break
    else:
        print(json.dumps({"found": False, "error": "mode error"}))
        sys.exit(1)

    if not items:
        # 048: the ordered step ran all the way through and the public DB had
        # nothing either.  Whatever macros the model writes next are its own
        # guess, so the meal owes a satiety answer before it can be recorded.
        for nm in graded:
            grade(nm, "estimate")
        out = {"found": False}
        if mode == "name" and near_hit:
            out.update(near_hit)
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(0)

    candidates = []
    for item in items[:3]:
        candidates.append(extract_nutrition(item, grams))

    # 048: a public-DB item is a real match -- pinned, not guessed.
    for nm in graded:
        grade(nm, "exact")
    out = {"found": True, "candidates": candidates}
    if mode == "name" and near_hit:
        out.update(near_hit)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
