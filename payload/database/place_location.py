"""Separate a venue's identity from calendar room/address text."""
import re

# A country/province token followed by address text; never split a bare name.
_ADDRESS = re.compile(
    r'\s+(?=(?:대한민국|서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|'
    r'인천(?:광역시)?|광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|'
    r'세종(?:특별자치시)?|경기(?:도)?|강원\S*|충청\S*|충북|충남|'
    r'전라\S*|전북\S*|전남|경상\S*|경북|경남|제주\S*)\s+)')
_ROOM = re.compile(
    r'\s+(?:·\s*)?([A-Za-z][0-9]+(?:룸|호실|호|실)?|[0-9]+(?:룸|호실|호|실)|[A-Za-z](?:룸|실)|[^\s·]+룸)$')


def split_location(value):
    """Return (label, room, address), leaving the event's raw text to callers."""
    text = ' '.join(str(value or '').split())
    if text.startswith(('http://', 'https://')):
        return text, '', ''
    match = _ADDRESS.search(text)
    address = text[match.end():].strip() if match else ''
    label = text[:match.start()].rstrip(' ,') if match else text
    label = re.sub(r'[ ,]+[0-9]{5}$', '', label)
    room = _ROOM.search(label)
    if room:
        label, room = label[:room.start()].strip(), room[1].strip(' ,')
    else:
        room = ''
    return label, room, address


def address_key(value):
    """Compare exact address spellings, ignoring country and whitespace only."""
    return re.sub(r'\s+', '', re.sub(r'^대한민국\s+', '', str(value or '').strip())).casefold()
