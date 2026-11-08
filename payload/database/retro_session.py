"""Read-only daily-retro date binding shared by the prompt hook and CLI.

The callback owns the ISO day. Older cards and typed replies use only the
explicit reply-to bubble, never an unrelated recent outbound card.
"""
import datetime as dt
import re
from zoneinfo import ZoneInfo

TOKEN = re.compile(r"^(?:opt:)?retro:daily:(\d{4}-\d{2}-\d{2})$")
REPLY = 'The user is replying to this message you sent outside this conversation (full text):\n'
END = '[/bridge:outbound-context]'



def _day(value):
    day = dt.date.fromisoformat(value)
    if day.isoformat() != value:
        raise ValueError('retro day must be YYYY-MM-DD')
    return day


def reply_card(prompt):
    if REPLY not in prompt or END not in prompt:
        return ''
    card = prompt.split(REPLY, 1)[1].split(END, 1)[0]
    return card.split('\nThe user is answering:', 1)[0].strip()


def resolve(prompt, inbound=None, now=None, date=None):
    """None for unrelated chat; READY/ASK_DATE for a daily start.

    A bare start without a bound date fails closed. Explicit 'today' is the
    only unbound start allowed to use the clock. Legacy M/D card years are
    resolved in code to the most recent matching calendar day (incl. DOW).
    """
    inbound = inbound if isinstance(inbound, dict) else {}
    if not isinstance(prompt, str):
        return None
    answer = prompt.rsplit(END, 1)[-1].strip()
    direct = TOKEN.fullmatch(answer)
    card = reply_card(prompt)
    issued_callback = inbound.get('source') == 'callback' and str(inbound.get('callback_data', '')).startswith('opt:retro:daily:')
    daily_card = bool(card and re.search(r'일간\s*회고|daily retro', card, re.I))
    if not direct and not date and not daily_card and not issued_callback:
        return None
    clock = now or dt.datetime.now(dt.timezone.utc)
    if isinstance(clock, str):
        clock = dt.datetime.fromisoformat(clock.replace('Z', '+00:00'))
    today = clock.astimezone(ZoneInfo('Asia/Seoul')).date()
    data = inbound.get('callback_data', '') if inbound.get('source') == 'callback' else ''
    token = direct or (TOKEN.fullmatch(data) if isinstance(data, str) else None)
    source = 'callback' if token else None
    day = None
    try:
        if date:
            day, source = _day(date), 'typed_date'
        elif token:
            day = _day(token[1])
        elif isinstance(data, str) and data.startswith('opt:retro:daily:'):
            raise ValueError('invalid retro callback')
        else:
            card = reply_card(prompt)
            if card and re.search(r'\uc77c\uac04\s*\ud68c\uace0|daily retro', card, re.I):
                # Only the combined title/date or legacy standalone header date;
                # never dates in its items or the closing opener question.
                header = card.split('\n\n', 1)[0]
                iso = re.search(r'(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)', header)
                if iso:
                    day = _day(iso[1])
                else:
                    md = re.search(r'(?mi)^\s*[`> *]*(?:▪\s*(?:일간\s*회고|daily retro)\s*·\s*)?(\d{1,2})/(\d{1,2})\(([\uc6d4\ud654\uc218\ubaa9\uae08\ud1a0\uc77c])\)[` *]*\s*$', header)
                    if md:
                        # Card is already in the past; weekday also protects
                        # year rollover and leap-day legacy replies.
                        for year in range(today.year, today.year - 9, -1):
                            try:
                                candidate = dt.date(year, int(md[1]), int(md[2]))
                            except ValueError:
                                continue
                            if candidate <= today and '\uc6d4\ud654\uc218\ubaa9\uae08\ud1a0\uc77c'[candidate.weekday()] == md[3]:
                                day = candidate
                                break
                source = 'reply_card'
                if day is None:
                    raise ValueError('card has no unambiguous date')
            elif card:
                raise ValueError('reply is not a daily retro card')
    except ValueError:
        day = None
    if day is None:
        return dict(status='ASK_DATE', next='Ask which daily retro date to open; do not read a clock-derived day.')
    date = day.isoformat()
    tomorrow = (day + dt.timedelta(days=1)).isoformat()
    return dict(status='READY', lane='daily', retro_date=date, tomorrow=tomorrow, source=source,
                commands=['retro-walk ' + date + ' open', 'day-record-summary ' + date, 'task-find ' + date,
                          'task-read ' + date, 'task-overdue --before ' + date,
                          'retro-items ' + date, 'experience-retro-list ' + date],
                tomorrow_commands=['appt-find ' + tomorrow, 'task-read ' + tomorrow],
                next=('Open brief-session daily for retro_date=%s; tomorrow=%s. '
                      'Read reference/daily-form.md first. Every <today> in the daily skill means '
                      'retro_date, including refreshes and the dashboard header. Use only this '
                      'date for completed-task lookup; dispose by id with task-done, never '
                      'task-done-today in a past-day retro. Keep this date through close. '
                      'First run retro-walk %s open. Its immutable ordered items are the card scope: relay current one at a time, including untimed and module rows; after the owner disposition advance only that kind/id. Do not close or plan tomorrow until remaining=0 (unless the owner stops via retro-walk stop). Then use retro-walk finish at close. '
                      'For kind=experience read reference/experiences.md; refresh experience-retro-list D before each question, enrich next_question.event_id with verbatim owner words, and advance only after the root pending queue is empty or the owner skips. Complete experiences are listed without questions. '
                      'Stages 2-3 list only D tasks/events: do not read or display the D+1 '
                      'schedule, even in the dashboard, until stage 4.' % (date, tomorrow, date)))


def hook_context(prompt, inbound=None, now=None):
    result = resolve(prompt, inbound, now)
    if result is None:
        return ''
    if result['status'] != 'READY':
        return '[daily-retro] ' + result['next']
    return ('[daily-retro] Bound card date is ' + result['retro_date'] + '. Interpret the owner intent before selecting an explicit start/stop verb. For a requested start: ' + result['next'] + ' Start reads: ' +
            '; '.join('database/kit_life.sh ' + c for c in result['commands']) +
            '. At stage 4 only, run ' + '; '.join(result['tomorrow_commands']) + '.')
