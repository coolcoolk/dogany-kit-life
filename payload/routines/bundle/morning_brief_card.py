#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Morning brief weather card generator (DGN-660 card-block design system).

Fetches live weather + air quality from Open-Meteo (no API key required) and
renders the DGN-376/DGN-660 reference weather look via the framework-canonical
card_blocks primitive:

  * card_blocks.py     -- R7 card blocks + R8 RCT gate (Chrome two-pass render)
  * card_blocks_demo   -- the locked reference weather consumer (WeatherHeroCard
                          / PmCard / TimelineCard). We reuse these domain card
                          classes verbatim and only swap the HARDCODED sample
                          data for a live Open-Meteo fetch.
  * design_tokens.py   -- brand palette + air-quality grade-* scale + fonts

Design compliance: zero hardcoded hex here. Colors come from the card_blocks
CARD_LIGHT palette (which is derived from the design_tokens card-light theme +
GRADE_LIGHT scale). Air-quality bands and grade slots come from the demo's
pm10_grade / pm25_grade (official Korean PM bands). RCT (phone) width/aspect/
byte budget is enforced by CardStack.save().

Usage:
    morning_brief_card.py --output <path> [--lat <lat> --lon <lon>]
    morning_brief_card.py --output <path>  # defaults to Seoul coords

Exit codes:
    0  success -- PNG written to --output path
    1  fatal error (missing dep, network failure, render error) -- caller keeps
       the text briefing as fallback so the brief never breaks.
"""

import sys
import os
import argparse
import datetime
import json
import time
import urllib.request

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
_parser = argparse.ArgumentParser(description='Morning brief weather card renderer')
_parser.add_argument('--output', required=True, help='Output PNG path (required)')
_parser.add_argument('--lat', type=float, default=37.5665, help='Latitude (default: Seoul)')
_parser.add_argument('--lon', type=float, default=126.9780, help='Longitude (default: Seoul)')
_args = _parser.parse_args()

LAT = _args.lat
LON = _args.lon
OUT_PATH = _args.output

# ---------------------------------------------------------------------------
# Load the framework-canonical card primitive + reference weather consumer.
# This script sits in routines/bundle/, so routines/lib/ is a sibling. We walk
# up to find routines/lib and add it to sys.path, then import card_blocks and
# the demo consumer by name (no package install assumed). We NEVER edit either
# module -- they are framework canon; we consume them.
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_lib_dir():
    parent = _SCRIPT_DIR
    for _ in range(6):
        cand = os.path.join(parent, 'routines', 'lib')
        if os.path.isfile(os.path.join(cand, 'card_blocks.py')):
            return cand
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        parent = nxt
    raise ImportError('routines/lib (card_blocks.py) not found from %s' % _SCRIPT_DIR)


_LIB_DIR = _find_lib_dir()
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from card_blocks import CardStack, QuoteCard, Row          # noqa: E402
from card_blocks_demo import (                              # noqa: E402
    WeatherHeroCard, PmCard, pm10_grade, pm25_grade,
)

# ---------------------------------------------------------------------------
# WMO weather code -> (display text, card_blocks icon name)
# Display text is Korean (user-facing content). Icon names are the bounded
# card_blocks ICONS vocabulary: sun/few/cloudy/overcast/rain/shower/drizzle/
# snow/thunder/fog/unknown.
# ---------------------------------------------------------------------------
WMO_KO = {
    0:  ('맑음',        'sun'),
    1:  ('구름 조금', 'few'),
    2:  ('구름 많음', 'cloudy'),
    3:  ('흐림',        'overcast'),
    45: ('안개',        'fog'),
    48: ('안개',        'fog'),
    51: ('이슬비',  'drizzle'),
    53: ('이슬비',  'drizzle'),
    55: ('이슬비',  'drizzle'),
    61: ('비',              'rain'),
    63: ('비',              'rain'),
    65: ('강한 비', 'rain'),
    71: ('눈',              'snow'),
    73: ('눈',              'snow'),
    75: ('강한 눈', 'snow'),
    80: ('소나기',  'shower'),
    81: ('소나기',  'shower'),
    82: ('강한 소나기', 'shower'),
    95: ('뇌우',       'thunder'),
    96: ('우박 뇌우', 'thunder'),
    99: ('우박 뇌우', 'thunder'),
}


def wmo_info(code):
    """Return (display_text, icon_name) for a WMO weather code."""
    return WMO_KO.get(code, (f'코드{code}', 'unknown'))


# ---------------------------------------------------------------------------
# Cache path: runtime/weather_cache.json relative to AGENT_ROOT.
# AGENT_ROOT is injected by morning-brief.sh as an env var; fall back to
# walking up from this script's own location if absent (standalone / test use).
# ---------------------------------------------------------------------------
def _cache_path():
    agent_root = os.environ.get('AGENT_ROOT', '')
    if not agent_root:
        # walk up from routines/bundle/ -> routines/ -> agent root
        agent_root = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
    return os.path.join(agent_root, 'runtime', 'weather_cache.json')


_CACHE_PATH = _cache_path()

# Retry config: 3 attempts with 2 s then 4 s back-off.
_FETCH_RETRIES = 3
_FETCH_BACKOFFS = [0, 2, 4]   # seconds before each attempt (0 = no pre-sleep on first)


def _write_cache(weather_raw, aq_raw):
    """Persist the last-good raw API payloads to the local cache file."""
    payload = {
        'cached_at': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'weather': weather_raw,
        'air_quality': aq_raw,
    }
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp = _CACHE_PATH + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(payload, fh)
        os.replace(tmp, _CACHE_PATH)
        sys.stderr.write(f"morning_brief_card: cache written -> {_CACHE_PATH}\n")
    except Exception as e:
        sys.stderr.write(f"morning_brief_card: cache write failed ({e}); continuing\n")


def _read_cache():
    """Return (weather_raw, aq_raw, cached_at_str) from cache, or raise FileNotFoundError.

    The cache file is SHARED with morning-brief.sh's text-weather fallback path,
    which fetches a narrower payload (hourly temp/precip only -- no `daily`, no
    `weather_code`). Parsing such an entry as a card payload would raise a bare
    KeyError mid-render, so validate the shape here and refuse it explicitly.
    """
    with open(_CACHE_PATH) as fh:
        payload = json.load(fh)
    weather = payload['weather']
    hourly = weather.get('hourly', {}) if isinstance(weather, dict) else {}
    if 'daily' not in weather or 'weather_code' not in hourly:
        raise ValueError('cached weather is text-shape, not card-shape')
    return weather, payload['air_quality'], payload['cached_at']


# ---------------------------------------------------------------------------
# Data fetching (Open-Meteo, no API key). Same endpoints as the incumbent card.
# Retry wrapper + cache fallback for transient network failures.
# ---------------------------------------------------------------------------
def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'morning-brief-card/2.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def fetch_json_with_retry(url, label):
    """Fetch url with _FETCH_RETRIES attempts; raise last exception on exhaustion."""
    last_exc = None
    for attempt in range(_FETCH_RETRIES):
        wait = _FETCH_BACKOFFS[attempt] if attempt < len(_FETCH_BACKOFFS) else _FETCH_BACKOFFS[-1]
        if wait:
            sys.stderr.write(f"morning_brief_card: {label} retry {attempt}/{_FETCH_RETRIES - 1} (backoff {wait}s)\n")
            time.sleep(wait)
        try:
            return fetch_json(url)
        except Exception as e:
            last_exc = e
            sys.stderr.write(f"morning_brief_card: {label} attempt {attempt + 1} failed: {e}\n")
    raise last_exc


def fetch_weather():
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=temperature_2m,weather_code,apparent_temperature"
        f"&hourly=temperature_2m,weather_code,precipitation_probability"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&timezone=Asia%2FSeoul&forecast_days=1"
    )
    return fetch_json_with_retry(url, 'weather')


def fetch_air_quality():
    url = (
        f"https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={LAT}&longitude={LON}"
        f"&current=pm10,pm2_5"
        f"&hourly=pm10,pm2_5"
        f"&timezone=Asia%2FSeoul&forecast_days=1"
    )
    return fetch_json_with_retry(url, 'air-quality')


TARGET_HOURS = [6, 9, 12, 15, 18, 21]


def _fmt_hour(h):
    """Format hour as '6AM' / '12PM' / '3PM' style."""
    if h < 12:
        return f"{h}AM"
    elif h == 12:
        return "12PM"
    else:
        return f"{h - 12}PM"


def _slot_index(times, target_h):
    for i, t in enumerate(times):
        if int(t[11:13]) == target_h:
            return i
    return None


def parse_weather(data):
    cur = data['current']
    daily = data['daily']
    hourly = data['hourly']

    temp_now = cur['temperature_2m']
    feels_like = cur.get('apparent_temperature', temp_now)
    code_now = cur['weather_code']
    temp_max = daily['temperature_2m_max'][0]
    temp_min = daily['temperature_2m_min'][0]

    times = hourly['time']
    slots = []
    for target_h in TARGET_HOURS:
        idx = _slot_index(times, target_h)
        if idx is not None:
            slots.append({
                'time':   _fmt_hour(target_h),
                'temp':   hourly['temperature_2m'][idx],
                'code':   hourly['weather_code'][idx],
                'precip': hourly['precipitation_probability'][idx],
            })

    return {
        'temp_now':   temp_now,
        'feels_like': feels_like,
        'code_now':   code_now,
        'temp_max':   temp_max,
        'temp_min':   temp_min,
        'slots':      slots,
    }


def parse_air_quality(aq_data):
    current = aq_data.get('current', {})
    pm10_now = current.get('pm10', 0.0) or 0.0
    pm25_now = current.get('pm2_5', 0.0) or 0.0

    hourly = aq_data.get('hourly', {})
    times_h = hourly.get('time', [])
    pm10_h = hourly.get('pm10', [])
    pm25_h = hourly.get('pm2_5', [])

    slots = []
    for target_h in TARGET_HOURS:
        idx = _slot_index(times_h, target_h)
        if idx is not None:
            pm10_val = (pm10_h[idx] if idx < len(pm10_h) else 0.0) or 0.0
            pm25_val = (pm25_h[idx] if idx < len(pm25_h) else 0.0) or 0.0
            slots.append({
                'time': f"{target_h:02d}:00",
                'pm10': pm10_val,
                'pm25': pm25_val,
            })

    return {
        'pm10_now': pm10_now,
        'pm25_now': pm25_now,
        'slots':    slots,
    }


# ---------------------------------------------------------------------------
# Daily quotes (Korean motivational quotes, date-cycled) -- unchanged content.
# ---------------------------------------------------------------------------
QUOTES = [
    # general
    ('오늘 할 수 있는 일을 내일로 미루지 마라.', '벤저민 프랭클린'),
    ('뭔가를 잃는 것은 더 나은 것이 예정되어 있기 때문입니다.', '맨디 헤일'),
    ('성공은 준비가 기회를 만났을 때 일어납니다.', '세네카'),
    ('가장 어두운 밤도 결국 끝나고 해는 떠오릅니다.', '빅토르 위고'),
    ('당신이 할 수 있다고 믿으면, 이미 반은 된 것입니다.', '시어도어 루스벨트'),
    ('작은 일에도 최선을 다하라. 그것이 큰 일을 이루는 길이다.', '공자'),
    ('우리가 두려워해야 할 유일한 것은 두려움 그 자체입니다.', '프랭클린 루스벨트'),
    ('천 리 길도 한 걸음부터.', '노자'),
    ('시작이 반이다.', '아리스토텔레스'),
    ('더 나은 세상을 만들고 싶다면 먼저 자기 자신을 바꿔라.', '마하트마 간디'),
    # existentialism
    ('삶의 의미는 주어지는 것이 아니라 스스로 만들어 가는 것이다.', '장폴 사르트르'),
    ('나를 죽이지 못하는 것은 나를 더 강하게 만든다.', '프리드리히 니체'),
    ('반항해야 한다. 그것이 삶이다.', '알베르 카뮈'),
    ('우리는 시지프스를 행복한 사람으로 상상해야 한다.', '알베르 카뮈'),
    ('불안은 자유의 어지러움이다.', '쇠렌 키르케고르'),
    # buddhism
    ('마음이 모든 것이다. 당신이 생각하는 대로 된다.', '붓다'),
    ('분노는 뜨거운 숯을 맨손으로 잡는 것과 같다. 남을 태우기 전에 자신을 태운다.', '붓다'),
    ('현재 이 순간이 유일한 집이다.', '틱낫한'),
    ('모든 것은 변한다. 집착만이 고통을 만든다.', '붓다'),
    ('자신을 사랑하는 것이 진정한 자비의 시작이다.', '달라이 라마'),
]


def get_quote():
    today = datetime.date.today()
    idx = today.timetuple().tm_yday % len(QUOTES)
    return QUOTES[idx]


# ---------------------------------------------------------------------------
# Korean weekday abbreviations for the card header (user-facing content)
# ---------------------------------------------------------------------------
WDAY_KO = ['월', '화', '수', '목', '금', '토', '일']


def build_card_data(weather, aq):
    """Assemble the card_blocks_demo SAMPLE-shaped dict from live data."""
    today = datetime.date.today()
    wday = WDAY_KO[today.weekday()]
    date_str = f"{today.year}.{today.month:02d}.{today.day:02d} ({wday})"

    condition, hero_icon = wmo_info(weather['code_now'])

    # Align both slot lists to <= 6 entries.
    w_slots = weather['slots']
    a_slots = aq['slots']
    n = min(len(w_slots), len(a_slots), 6)
    w_slots = w_slots[:n]
    a_slots = a_slots[:n]

    # Timeline slots: (time, temp, icon, precip%)
    slots = []
    for s in w_slots:
        _, icon = wmo_info(s['code'])
        slots.append((s['time'], f"{s['temp']:.0f}°", icon, int(s['precip'])))

    pm25_series = [round(s['pm25']) for s in a_slots]
    pm10_series = [round(s['pm10']) for s in a_slots]

    quote_text, quote_author = get_quote()

    # PM cards: the demo PmCard shows a max / min pair with grade tags. Use the
    # day's slot extrema so the card conveys the range, matching the reference.
    pm25_vals = pm25_series or [round(aq['pm25_now'])]
    pm10_vals = pm10_series or [round(aq['pm10_now'])]

    return {
        "city": "서울 날씨",
        "date": date_str,
        "temp": f"{weather['temp_now']:.0f}°",
        "icon": hero_icon,
        "condition": condition,
        "feels": f"체감 {weather['feels_like']:.0f}°",
        "hi": f"{weather['temp_max']:.0f}°",
        "lo": f"{weather['temp_min']:.0f}°",
        "pm25": {"label": "초미세먼지", "tag": "PM2.5",
                 "max": max(pm25_vals), "min": min(pm25_vals)},
        "pm10": {"label": "미세먼지", "tag": "PM10",
                 "max": max(pm10_vals), "min": min(pm10_vals)},
        "slots": slots,
        "pm25_series": pm25_series,
        "pm10_series": pm10_series,
        "quote": quote_text,
        "quote_author": quote_author,
    }


def build_stack(data):
    """Assemble the reference weather stack from live data. Card classes and
    palette come from the framework canon (never edited here)."""
    stack = CardStack()
    stack.add(WeatherHeroCard(data))
    stack.add(Row(PmCard(data["pm25"], pm25_grade),
                  PmCard(data["pm10"], pm10_grade)))
    if data["slots"]:
        from card_blocks_demo import TimelineCard
        stack.add(TimelineCard("시간대 예보", data["slots"],
                               data["pm25_series"], data["pm10_series"]))
    stack.add(QuoteCard(data["quote"], data["quote_author"]))
    return stack


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # --- live fetch with retry --------------------------------------------------
    weather_raw = None
    aq_raw = None
    cached_at = None   # set when we fall back to cache

    sys.stderr.write(f"morning_brief_card: fetching weather (lat={LAT}, lon={LON})...\n")
    try:
        weather_raw = fetch_weather()
    except Exception as e:
        sys.stderr.write(f"morning_brief_card: weather fetch failed after retries: {e}\n")

    if weather_raw is not None:
        sys.stderr.write("morning_brief_card: fetching air quality...\n")
        try:
            aq_raw = fetch_air_quality()
        except Exception as e:
            sys.stderr.write(f"morning_brief_card: air-quality fetch failed after retries: {e}\n")

    # --- cache write on full success -------------------------------------------
    if weather_raw is not None and aq_raw is not None:
        _write_cache(weather_raw, aq_raw)

    # --- cache fallback on any fetch failure -----------------------------------
    if weather_raw is None or aq_raw is None:
        sys.stderr.write(f"morning_brief_card: attempting cache fallback from {_CACHE_PATH}\n")
        try:
            weather_raw, aq_raw, cached_at = _read_cache()
            sys.stderr.write(f"morning_brief_card: using cached weather from {cached_at}\n")
        except FileNotFoundError:
            sys.stderr.write("morning_brief_card: no cache available; cannot render card\n")
            sys.exit(1)
        except Exception as e:
            sys.stderr.write(f"morning_brief_card: cache read failed ({e}); cannot render card\n")
            sys.exit(1)

    # --- parse + render --------------------------------------------------------
    weather = parse_weather(weather_raw)
    aq = parse_air_quality(aq_raw)

    pm10 = aq['pm10_now']
    pm25 = aq['pm25_now']
    sys.stderr.write(
        f"morning_brief_card: temp={weather['temp_now']:.0f}C "
        f"hi={weather['temp_max']:.0f} lo={weather['temp_min']:.0f} "
        f"pm10={pm10:.0f} pm25={pm25:.0f}"
        + (f" [cached {cached_at}]" if cached_at else "") + "\n"
    )

    sys.stderr.write("morning_brief_card: rendering (card_blocks)...\n")
    data = build_card_data(weather, aq)

    # If using cached data, annotate the city label with the cache timestamp so
    # the user sees "<도시> 날씨 (날씨 조회불가, 마지막 기록: HH:MM)" in the card header.
    # Append to the existing label rather than re-hardcoding a city name here.
    if cached_at:
        try:
            dt = datetime.datetime.strptime(cached_at, '%Y-%m-%dT%H:%M:%SZ')
            # cached_at is stored UTC; render it in the host's local timezone
            # rather than a hardcoded region offset.
            dt_local = dt.replace(tzinfo=datetime.timezone.utc).astimezone()
            ts_label = dt_local.strftime('%m/%d %H:%M')
        except Exception:
            ts_label = cached_at
        data['city'] = f"{data.get('city', '')} (날씨 조회불가, 마지막 기록: {ts_label})".strip()

    report = build_stack(data).save(OUT_PATH)
    sys.stderr.write(f"morning_brief_card: {report}\n")
    print(OUT_PATH)
