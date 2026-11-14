"""Separate a venue's identity from calendar room/address text."""
import re

# A country/province token followed by address text; never split a bare name.
_REGION = (
    r'(?:대한민국|서울(?:특별시)?|부산(?:광역시)?|대구(?:광역시)?|'
    r'인천(?:광역시)?|광주(?:광역시)?|대전(?:광역시)?|울산(?:광역시)?|'
    r'세종(?:특별자치시)?|경기(?:도)?|강원\S*|충청\S*|충북|충남|'
    r'전라\S*|전북\S*|전남|경상\S*|경북|경남|제주\S*)')
_ADDRESS = re.compile(r'\s+(?=' + _REGION + r'\s+)')
_ROOM = re.compile(
    r'\s+(?:·\s*)?([A-Za-z][0-9]+(?:룸|호실|호|실)?|[0-9]+(?:룸|호실|호|실)|[A-Za-z](?:룸|실)|[^\s·]+룸)$')
_ROAD = re.compile(
    r'^((?:' + _REGION + r'\s+(?:[가-힣0-9·.]+\s+)*?)?'
    r'[가-힣0-9·.]+(?:대로|로|길)\s+\d+(?:-\d+)?)'
    r'((?:\s+(?:지하\s*)?B?\d+(?:[-~]\d+)?(?:층|호))*)'
    r'(?:\s+(.+))?$')
_ADDRESS_ROOM = re.compile(
    r'(?<![^\s,·])((?:지하\s*)?B?\d+(?:[-~]\d+)?(?:층|호실|호|실|룸|동)|'
    r'[A-Za-z][0-9]+(?:룸|호실|호|실)?|[A-Za-z](?:룸|실)|[^\s,·]+룸)(?![^\s,·])',
    re.IGNORECASE)
_LOCALITY = re.compile(r'[가-힣][가-힣0-9·.]*동(?:\d+가)?')


def _address_annotation(match):
    """Keep building names from a road address's locality annotation."""
    return ' '.join(part for part in re.split(r'[\s,]+', match[1])
                    if part and not _LOCALITY.fullmatch(part))


def road_address(value):
    """A road/building query, excluding unit and trailing venue text.

    Floors remain in the stored address. The map locates the building;
    searching for a floor plus a generic category often finds nothing.
    """
    match = _ROAD.fullmatch(' '.join(str(value or '').split()))
    return match[1] if match else ''


def same_road_address(left, right):
    """Compare map building evidence without treating another road as a hit."""
    def key(value):
        road = road_address(value)
        road = re.sub(r'^대한민국\s+', '', road)
        road = re.sub(r'^서울특별시\s+', '서울 ', road)
        return address_key(road)
    return bool(key(left)) and key(left) == key(right)


def split_location(value):
    """Return (label, room, address), leaving the event's raw text to callers."""
    text = ' '.join(str(value or '').split())
    if text.startswith(('http://', 'https://')):
        return text, '', ''
    road = _ROAD.fullmatch(text)
    if road:
        # Keep the existing stored address (including immediately following
        # floors), but separate units wherever they occur in the suffix.
        address = road[1] + road[2]
        suffix = re.sub(r'\(([^()]*)\)', _address_annotation,
                        road[2] + ' ' + (road[3] or ''))
        rooms = [match[1] for match in _ADDRESS_ROOM.finditer(suffix)]
        label = re.sub(r'[\s,·]+', ' ', _ADDRESS_ROOM.sub(' ', suffix)).strip()
        return label or road[1], ' '.join(rooms), address
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
