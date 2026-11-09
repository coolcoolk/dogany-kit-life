"""
Korean lunisolar calendar (eumryeok) for the routine cadence grammar "YL:MM-DD".

Stdlib-only, vendored, PURE (no IO, no clock, no network). The table covers
lunar years 1900..2048 (solar 1900-01-31 .. 2049-01-21) and was generated
from the KASI-derived `korean_lunar_calendar` package, then pinned by
tests/test_routine_recurrence.py against published holidays (Seollal/Chuseok and
leap-month years). Outside the range the converters raise ValueError and the
cadence simply does not fire (documented in the routine-manage reference).

Row = (lunar_year, new_year_mmdd, leap_month, long_month_bits, month_count)
  new_year_mmdd   solar MMDD of lunar 1/1
  leap_month      N: a leap month follows normal month N (0 = none)
  long_month_bits bit i set -> the i-th month of the year (leap month
                  included, in calendar order) has 30 days, else 29
  month_count     12, or 13 in a leap year

LEAP-MONTH POLICY: "YL:MM-DD" names the NORMAL month MM. A leap month is a
different month and never fires the cadence. A day missing from a short
month (lunar DD=30 in a 29-day month) falls on that month's last day.
"""

import datetime

LUNAR_FIRST_YEAR = 1900
LUNAR_LAST_YEAR = 2048

_ROWS = (
    (1900,131,8,5842,13), (1901,219,0,1874,12), (1902,208,0,3749,12), (1903,129,5,5706,13),
    (1904,216,0,1611,12), (1905,204,0,2715,12), (1906,125,4,5462,13), (1907,213,0,1386,12),
    (1908,202,0,2905,12), (1909,122,2,5970,13), (1910,210,0,1874,12), (1911,130,6,6949,13),
    (1912,218,0,2853,12), (1913,206,0,2635,12), (1914,126,5,4763,13), (1915,214,0,2733,12),
    (1916,204,0,1386,12), (1917,123,2,2921,13), (1918,211,0,2985,12), (1919,201,7,6994,13),
    (1920,220,0,3474,12), (1921,208,0,3365,12), (1922,128,5,6733,13), (1923,216,0,2390,12),
    (1924,205,0,693,12), (1925,124,4,5549,13), (1926,213,0,1748,12), (1927,202,0,3497,12),
    (1928,123,2,7570,13), (1929,210,0,3730,12), (1930,130,6,3366,13), (1931,217,0,1319,12),
    (1932,206,0,2647,12), (1933,126,5,4790,13), (1934,214,0,2778,12), (1935,204,0,1748,12),
    (1936,124,3,3753,13), (1937,211,0,1865,12), (1938,131,7,5779,13), (1939,219,0,2707,12),
    (1940,208,0,1323,12), (1941,127,6,2651,13), (1942,215,0,2413,12), (1943,205,0,2922,12),
    (1944,126,4,6996,13), (1945,213,0,2980,12), (1946,202,0,2889,12), (1947,122,2,6803,13),
    (1948,210,0,2709,12), (1949,129,7,5419,13), (1950,217,0,1325,12), (1951,206,0,2733,12),
    (1952,127,5,5482,13), (1953,214,0,3506,12), (1954,204,0,3492,12), (1955,124,3,7497,13),
    (1956,212,0,3402,12), (1957,131,8,6805,13), (1958,219,0,2710,12), (1959,208,0,1366,12),
    (1960,128,6,2741,13), (1961,215,0,2773,12), (1962,205,0,1746,12), (1963,125,4,3749,13),
    (1964,213,0,3749,12), (1965,202,0,3658,12), (1966,122,3,3222,13), (1967,209,0,2715,12),
    (1968,130,7,5462,13), (1969,217,0,1386,12), (1970,206,0,2905,12), (1971,127,5,5970,13),
    (1972,215,0,1874,12), (1973,203,0,1829,12), (1974,123,4,5707,13), (1975,211,0,2635,12),
    (1976,131,8,4779,13), (1977,218,0,685,12), (1978,207,0,1387,12), (1979,128,6,2921,13),
    (1980,216,0,3497,12), (1981,205,0,3474,12), (1982,125,4,6949,13), (1983,213,0,3365,12),
    (1984,202,10,6733,13), (1985,220,0,2646,12), (1986,209,0,694,12), (1987,129,6,5549,13),
    (1988,218,0,1748,12), (1989,206,0,3497,12), (1990,127,5,7570,13), (1991,215,0,3730,12),
    (1992,204,0,3366,12), (1993,123,3,2646,13), (1994,210,0,2647,12), (1995,131,8,4790,13),
    (1996,219,0,2906,12), (1997,208,0,1748,12), (1998,128,5,3785,13), (1999,216,0,1865,12),
    (2000,205,0,1683,12), (2001,124,4,5415,13), (2002,212,0,1323,12), (2003,201,0,2651,12),
    (2004,122,2,5466,13), (2005,209,0,874,12), (2006,129,7,6997,13), (2007,218,0,2980,12),
    (2008,207,0,2889,12), (2009,126,5,6803,13), (2010,214,0,2709,12), (2011,203,0,1325,12),
    (2012,123,3,2653,13), (2013,210,0,2733,12), (2014,131,9,5546,13), (2015,219,0,1490,12),
    (2016,208,0,3493,12), (2017,128,5,7498,13), (2018,216,0,3402,12), (2019,205,0,2709,12),
    (2020,125,4,5421,13), (2021,212,0,1366,12), (2022,201,0,2741,12), (2023,122,2,5546,13),
    (2024,210,0,1746,12), (2025,129,6,3749,13), (2026,217,0,3749,12), (2027,207,0,3658,12),
    (2028,127,5,3222,13), (2029,213,0,3227,12), (2030,203,0,1370,12), (2031,123,3,2773,13),
    (2032,211,0,2921,12), (2033,131,11,5970,13), (2034,219,0,1874,12), (2035,208,0,2853,12),
    (2036,128,6,5707,13), (2037,215,0,2635,12), (2038,204,0,1195,12), (2039,124,5,1371,13),
    (2040,212,0,1389,12), (2041,201,0,2921,12), (2042,122,2,6994,13), (2043,210,0,3474,12),
    (2044,130,7,7461,13), (2045,217,0,3365,12), (2046,206,0,2637,12), (2047,126,5,5293,13),
    (2048,214,0,694,12),
)
_BY_YEAR = {r[0]: r for r in _ROWS}


def _new_year(ly):
    r = _BY_YEAR[ly]
    return datetime.date(ly, r[1] // 100, r[1] % 100)


def lunar_months(ly):
    """[(month_no, is_leap, length_days)] in calendar order for lunar year ly."""
    if ly not in _BY_YEAR:
        raise ValueError("lunar year %r outside %d..%d"
                         % (ly, LUNAR_FIRST_YEAR, LUNAR_LAST_YEAR))
    _y, _ny, leap, bits, n = _BY_YEAR[ly]
    out = []
    no = 0
    for i in range(n):
        is_leap = bool(leap) and i == leap
        if not is_leap:
            no += 1
        out.append((no, is_leap, 30 if (bits >> i) & 1 else 29))
    return out


def solar_to_lunar(d):
    """(lunar_year, month, day, is_leap) of solar datetime.date d."""
    ly = d.year if (d.year in _BY_YEAR and d >= _new_year(d.year)) else d.year - 1
    if ly not in _BY_YEAR:
        raise ValueError("date %s outside the lunar table" % d.isoformat())
    start = _new_year(ly)
    if d < start:
        raise ValueError("date %s outside the lunar table" % d.isoformat())
    off = (d - start).days
    for no, is_leap, length in lunar_months(ly):
        if off < length:
            return ly, no, off + 1, is_leap
        off -= length
    raise ValueError("date %s outside the lunar table" % d.isoformat())


def lunar_to_solar(ly, month, day, leap=False):
    """Solar date of lunar ly-month-day. A day past the month's length is
    clamped to the month's last day. ValueError when the month does not exist
    (e.g. leap=True in a year without that leap month)."""
    off = 0
    for no, is_leap, length in lunar_months(ly):
        if no == month and is_leap == bool(leap):
            return _new_year(ly) + datetime.timedelta(days=off + min(day, length) - 1)
        off += length
    raise ValueError("no lunar month %s%d in %d" % ("leap " if leap else "", month, ly))


def fires_on(d, month, day):
    """True iff solar date d is the solar date of NORMAL lunar month/day
    (day clamped to the month's last day)."""
    try:
        ly, m, dd, is_leap = solar_to_lunar(d)
    except ValueError:
        return False
    if is_leap or m != month:
        return False
    if dd == day:
        return True
    length = [l for no, lp, l in lunar_months(ly) if no == m and not lp][0]
    return day > length and dd == length
