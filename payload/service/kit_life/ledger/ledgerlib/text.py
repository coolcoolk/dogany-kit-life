"""Owner-facing copy. The words live in the kit's i18n bundle,
service/kit_life/i18n/<lang>.json (keys `ledger.*`, kit_life 2.46.0; the same
bytes as config/i18n/<lang>.bundle.json); code holds keys and placeholders,
never the sentence.

Language: env AGENT_LANG, else config/agent.conf AGENT_LANG, else ko. A key
missing in that language falls back to ko.
"""
import functools

from . import paths
from money_format import comma, split_man
from locale_store import read_json

DEFAULT_LANG = 'ko'


def lang():
    val = (paths.agent_conf_value('AGENT_LANG') or DEFAULT_LANG).strip().lower()
    return val.split('_')[0].split('-')[0] or DEFAULT_LANG


@functools.lru_cache(maxsize=None)
def _bundle(code):
    try:
        d = read_json(paths.SERVICE_DIR.parent / 'i18n' / ('%s.json' % code), encoding='utf-8')
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _raw(key):
    s = _bundle(lang()).get(key)
    if s is None:
        s = _bundle(DEFAULT_LANG)[key]
    return s


class Terms:
    """`{term.<name>}` in a template: the owner word for a ledger term (keys
    `ledger.term.<name>` -- income, fixed, living, big, saving, surplus, gap;
    owner review 2026-10-10 20:56). The words live on their own keys so an
    owner term-override layer can rename them; code tokens never change.

    `{term.<name>__<particle>}` adds the Korean particle chosen by the word
    (eul / ro / eun / i); `__cap` capitalizes it (a row label in English). seen: a set -> the FIRST use of a term in that set
    carries its gloss (`ledger.term.gloss.<name>`, when the bundle has one)
    and is added to the set; never again after. None = plain words (cards,
    buttons, month close, retro)."""

    def __init__(self, seen=None):
        self.seen = seen

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        base, _, particle = name.partition('__')
        word = _raw('ledger.term.' + base)
        out = word
        if self.seen is not None and base not in self.seen:
            self.seen.add(base)
            gloss = _bundle(lang()).get('ledger.term.gloss.' + base, _bundle(DEFAULT_LANG).get('ledger.term.gloss.' + base))
            if gloss:
                out = _raw('ledger.term.glossed').format(word=word, gloss=gloss)
        if particle == 'cap':
            out = out[:1].upper() + out[1:]
        elif particle:
            out += josa(word, particle)[len(word):]
        return out


def t(key, _terms=None, **kw):
    s = _raw(key)
    if '{USER_LABEL}' in s:
        kw = dict(kw, USER_LABEL=paths.agent_conf_value('AGENT_ADDRESS')
                  or _raw('ledger.owner.address.default'))
    if '{term.' in s:
        kw = dict(kw, term=_terms if _terms is not None else Terms())
    return s.format(**kw) if kw else s


def term(name):
    """The plain owner word for a term (no gloss)."""
    return getattr(Terms(), name)


def join(items):
    """'a<dot>b<dot>c' (ko, a middle dot) / 'a, b or c' (en): separators are copy too."""
    items = list(items)
    if len(items) < 2:
        return ''.join(items)
    return t('ledger.list.sep').join(items[:-1]) + t('ledger.list.last') + items[-1]


def dow(key):
    return t('ledger.dow.' + key)


def money(n):
    """Won for the owner. ko (style 'man'): 7,890 / 4-man 6,800 / 95-man /
    about 33-man (>= 100,000 and not a whole man); other languages: the
    plain amount with separators."""
    n = int(n)
    if t('ledger.money.style') != 'man' or abs(n) < 10000:
        return t('ledger.money.won', n=comma(n))
    man, rest = split_man(n)
    sign = '-' if n < 0 else ''
    if rest == 0:
        return sign + t('ledger.money.man', man=comma(man))
    if abs(n) >= 100000:
        return sign + t('ledger.money.about', v=t('ledger.money.man', man=comma(int(round(abs(n) / 10000.0)))))
    return sign + t('ledger.money.man_rest', man=comma(man), rest=comma(rest))


def money_range(lo, hi):
    """'2-man~9-man' (ko, style man) / '16,420-85,530' (plain)."""
    if t('ledger.money.style') == 'man' and lo >= 10000:
        return t('ledger.money.range_man', lo=format(int(round(lo / 10000.0)), ','),
                 hi=format(int(round(hi / 10000.0)), ','))
    return t('ledger.money.range', lo=format(int(lo), ','), hi=format(int(hi), ','))


def _final(word):
    """'none' | 'rieul' | 'other': the last sound of a Korean word (for the
    particle). Non-Hangul endings: l / r read as rieul, m n ng-like letters
    and 0 3 6 as a consonant, else a vowel."""
    w = (word or '').strip()
    if not w:
        return 'none'
    ch = w[-1]
    code = ord(ch) - 0xAC00
    if 0 <= code < 11172:
        jong = code % 28
        return 'none' if jong == 0 else ('rieul' if jong == 8 else 'other')
    c = ch.lower()
    if c in 'lr1789':
        return 'rieul'
    if c in 'mn036':
        return 'other'
    return 'none'


def josa(word, kind):
    """word + its particle, chosen by the last sound (ko only; other
    languages return the word). kind: 'ro' (ro / euro), 'eul' (eul / reul),
    'eun' (eun / neun), 'i' (i / ga)."""
    if t('ledger.money.style') != 'man':
        return word
    f = _final(word)
    if kind == 'ro':
        return word + t('ledger.josa.ro' if f in ('none', 'rieul') else 'ledger.josa.euro')
    if kind == 'eul':
        return word + t('ledger.josa.reul' if f == 'none' else 'ledger.josa.eul')
    if kind == 'i':
        return word + t('ledger.josa.ga' if f == 'none' else 'ledger.josa.i')
    return word + t('ledger.josa.neun' if f == 'none' else 'ledger.josa.eun')


def month_bounds(first, last):
    """Keep the approved month-only copy unless the range crosses a year."""
    if first[:4] != last[:4]:
        if lang() == 'ko':
            return (t('ledger.installment.ym', y=int(first[:4]), m=int(first[5:])),
                    t('ledger.installment.ym', y=int(last[:4]), m=int(last[5:]))[:-1])
        return first, last
    return int(first[5:]), int(last[5:])


def monthly_count(value):
    # Preserve positive sparse usage while the under-one phrase awaits approval.
    return format(value, '.1f' if 0 < value < 1 else '.0f')


def date_bounds(first, last, today):
    from . import dashboard
    if first[:4] != last[:4]:
        return tuple(t('ledger.question_date.old', y=int(day[:4]), m=int(day[5:7]), d=int(day[8:10]))
                     for day in (first, last))
    return tuple(dashboard.question_date(day, today, short=False) for day in (first, last))
