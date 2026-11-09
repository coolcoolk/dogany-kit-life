#!/usr/bin/env python3
"""Holiday source for the cadence '!hol' exclusion.

PRIMARY  = the `holidays` library (dynamic: any year, multi-country, OFFLINE;
           updates on its OWN pip cadence, independent of the kit_life version
           line -- so kit_life never re-releases for a new year / law change).
FALLBACK = a small static KR table (used ONLY when the library is not installed,
           e.g. a fresh instance before the dependency is provisioned).

Country resolves from (in order): env DOGANY_COUNTRY / AGENT_COUNTRY ->
config/agent.conf (AGENT_COUNTRY, else derive from AGENT_LANG) -> default 'KR'.
stdlib-only at import; the library is imported lazily + defensively."""
import os
import datetime
import functools

HERE = os.path.dirname(os.path.abspath(__file__))
_CONF = os.path.normpath(os.path.join(HERE, "..", "config", "agent.conf"))
_LANG_COUNTRY = {"ko": "KR", "en": "US", "ja": "JP", "zh": "CN", "de": "DE",
                 "fr": "FR", "es": "ES"}


def _conf_val(key):
    """First 'key=value' value in config/agent.conf, else None. Best-effort.

    Whitespace first, then ONE matched surrounding quote pair. The framework
    side SOURCES agent.conf, so a value containing a space must be quoted
    there; this reader takes the raw text after '=', where an unstripped quote
    silently changes the answer -- a quoted AGENT_LANG would miss the
    _LANG_COUNTRY lookup and fall through to 'KR' with no error at all.
    Only a matched pair is removed, so a lone quote character survives.

    Kept inline rather than imported from agent_conf: this module promises
    stdlib-only at import time and must not take on a sibling-path assumption.
    """
    try:
        with open(_CONF) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    val = line.split("=", 1)[1].strip()
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                        val = val[1:-1]
                    return val
    except OSError:
        pass
    return None


@functools.lru_cache(maxsize=1)
def resolve_country():
    """Best-effort country code (ISO-2). Explicit config/env wins; else derive
    from the install locale; else KR. Onboarding should set AGENT_COUNTRY."""
    c = (os.environ.get("DOGANY_COUNTRY") or os.environ.get("AGENT_COUNTRY")
         or _conf_val("AGENT_COUNTRY"))
    if c:
        return c.strip().upper()
    lang = (_conf_val("AGENT_LANG") or os.environ.get("AGENT_LANG") or "").lower()
    return _LANG_COUNTRY.get(lang, "KR")


@functools.lru_cache(maxsize=1)
def _lib():
    try:
        import holidays
        return holidays
    except Exception:
        return None


# static fallback: KR public holidays (generated dev-time from `holidays`).
_STATIC_KR = frozenset({
    "2026-01-01",
    "2026-02-16",
    "2026-02-17",
    "2026-02-18",
    "2026-03-01",
    "2026-03-02",
    "2026-05-01",
    "2026-05-05",
    "2026-05-24",
    "2026-05-25",
    "2026-06-03",
    "2026-06-06",
    "2026-07-17",
    "2026-08-15",
    "2026-08-17",
    "2026-09-24",
    "2026-09-25",
    "2026-09-26",
    "2026-10-03",
    "2026-10-05",
    "2026-10-09",
    "2026-12-25",
    "2027-01-01",
    "2027-02-06",
    "2027-02-07",
    "2027-02-08",
    "2027-02-09",
    "2027-03-01",
    "2027-05-01",
    "2027-05-03",
    "2027-05-05",
    "2027-05-13",
    "2027-06-06",
    "2027-07-17",
    "2027-07-19",
    "2027-08-15",
    "2027-08-16",
    "2027-09-14",
    "2027-09-15",
    "2027-09-16",
    "2027-10-03",
    "2027-10-04",
    "2027-10-09",
    "2027-10-11",
    "2027-12-25",
    "2027-12-27",
    "2028-01-01",
    "2028-01-26",
    "2028-01-27",
    "2028-01-28",
    "2028-03-01",
    "2028-04-12",
    "2028-05-01",
    "2028-05-02",
    "2028-05-05",
    "2028-06-06",
    "2028-07-17",
    "2028-08-15",
    "2028-10-02",
    "2028-10-03",
    "2028-10-04",
    "2028-10-05",
    "2028-10-09",
    "2028-12-25",
    "2029-01-01",
    "2029-02-12",
    "2029-02-13",
    "2029-02-14",
    "2029-03-01",
    "2029-05-01",
    "2029-05-05",
    "2029-05-07",
    "2029-05-20",
    "2029-05-21",
    "2029-06-06",
    "2029-07-17",
    "2029-08-15",
    "2029-09-21",
    "2029-09-22",
    "2029-09-23",
    "2029-09-24",
    "2029-10-03",
    "2029-10-09",
    "2029-12-25",
    "2030-01-01",
    "2030-02-02",
    "2030-02-03",
    "2030-02-04",
    "2030-02-05",
    "2030-03-01",
    "2030-04-03",
    "2030-05-01",
    "2030-05-05",
    "2030-05-06",
    "2030-05-09",
    "2030-06-06",
    "2030-06-12",
    "2030-07-17",
    "2030-08-15",
    "2030-09-11",
    "2030-09-12",
    "2030-09-13",
    "2030-10-03",
    "2030-10-09",
    "2030-12-25",
})


def is_holiday(d, country=None):
    """d: datetime.date or ISO 'YYYY-MM-DD'. True iff a public holiday in the
    resolved country. Uses the library when present (any year); falls back to
    the static KR table otherwise."""
    if isinstance(d, datetime.date):
        dd, iso = d, d.isoformat()
    else:
        iso, dd = d, datetime.date.fromisoformat(d)
    lib = _lib()
    if lib is not None:
        cc = country or resolve_country()
        try:
            return dd in lib.country_holidays(cc, years=dd.year)
        except Exception:
            pass   # unknown country / lib error -> fall through to fallback
    # fallback only covers KR; other countries with no lib -> no holidays known
    return iso in _STATIC_KR
