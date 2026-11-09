"""sheet_read.py -- the export file as rows: CSV, xlsx, or a zip holding one
(kit_life 2.46.0; owner answer 2026-10-10 19:38: Banksalad mails a ZIP
"<name>_YYYY-MM-DD~YYYY-MM-DD.zip" with an Excel file inside, possibly
password-protected).

Standard library only (no openpyxl in the kit's interpreter): zipfile for
the archive and the xlsx container, xml.etree for the sheet XML.

  kind(path)            'csv' | 'zip' (xlsx or archive) | 'ole' (an
                        encrypted xlsx or a legacy .xls: not readable here)
  read(path, password)  -> (fieldnames, [(line, {field: text})]) for a zip /
                        xlsx; the header row is the first row (of any sheet,
                        within HEADER_SCAN rows) holding every `need` field.

Errors are ValueError with a machine token first (the callers map it to the
owner's words): password-needed, password-wrong, zip-encryption-unsupported,
xlsx-locked, no-table, too-large. The password lives only in this call:
never stored, never logged, never part of an error message.
"""
import datetime
import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
import zlib

HEADER_SCAN = 30
MAX_MEMBER = 64 * 1024 * 1024       # a zip bomb stops here (an export is < 5 MB)
OLE_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
MAIN = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
EXCEL_EPOCH = datetime.date(1899, 12, 30)


def kind(path):
    with open(path, 'rb') as f:
        head = f.read(8)
    if head.startswith(b'PK\x03\x04') or head.startswith(b'PK\x05\x06'):
        return 'zip'
    if head == OLE_MAGIC:
        return 'ole'
    return 'csv'


def _read_member(zf, info, password):
    if info.file_size > MAX_MEMBER:
        raise ValueError('too-large: archive member over %d MB' % (MAX_MEMBER // 1048576))
    if not info.flag_bits & 0x1:
        return zf.read(info)
    if not password:
        raise ValueError('password-needed: the file is password-protected')
    tried = []
    for enc in ('utf-8', 'cp949'):
        try:
            pwd = password.encode(enc)
        except UnicodeEncodeError:
            continue
        if pwd in tried:
            continue
        tried.append(pwd)
        try:
            return zf.read(info, pwd=pwd)
        except NotImplementedError:
            raise ValueError('zip-encryption-unsupported: this zip encryption (AES) needs another tool')
        except (RuntimeError, zipfile.BadZipFile, zlib.error):
            # RuntimeError "Bad password"; a check-byte collision (1 in 256)
            # surfaces as a CRC / inflate error instead
            continue
    raise ValueError('password-wrong: the password did not open the file')


def _is_xlsx(zf):
    return 'xl/workbook.xml' in zf.namelist()


def _shared(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    out = []
    for si in ET.fromstring(zf.read('xl/sharedStrings.xml')).findall(MAIN + 'si'):
        # plain <t> or rich-text runs <r><t>; phonetic <rPh> is not the text
        parts = [t.text or '' for t in si.findall(MAIN + 't')]
        parts += [t.text or '' for r in si.findall(MAIN + 'r') for t in r.findall(MAIN + 't')]
        out.append(''.join(parts))
    return out


def _col(ref):
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _num(v):
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f == int(f) else repr(f)


def _sheet_rows(data, shared):
    rows = []
    for row in ET.fromstring(data).iter(MAIN + 'row'):
        cells = {}
        nxt = 0
        for c in row.findall(MAIN + 'c'):
            idx = _col(c.get('r')) if c.get('r') else nxt
            nxt = idx + 1
            t = c.get('t')
            v = c.find(MAIN + 'v')
            if t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter(MAIN + 't'))
            elif v is None or v.text is None:
                val = ''
            elif t == 's':
                val = shared[int(v.text)]
            elif t in ('str', 'b', 'e'):
                val = v.text
            else:
                val = _num(v.text)
            cells[idx] = val.strip() if isinstance(val, str) else val
        rows.append((int(row.get('r') or len(rows) + 1), cells))
    return rows


def _sheets(zf):
    wb = ET.fromstring(zf.read('xl/workbook.xml'))
    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
    target = {r.get('Id'): r.get('Target') for r in rels.iter(PKG_REL + 'Relationship')}
    shared = _shared(zf)
    sheets = wb.find(MAIN + 'sheets')
    for s in (sheets if sheets is not None else ()):
        t = target.get(s.get(REL + 'id'))
        if not t:
            continue
        path = t.lstrip('/') if t.startswith('/') else posixpath.normpath(posixpath.join('xl', t))
        if path in zf.namelist():
            yield _sheet_rows(zf.read(path), shared)


def _excel_date(v):
    try:
        return (EXCEL_EPOCH + datetime.timedelta(days=int(float(v)))).isoformat()
    except (ValueError, OverflowError):
        return v


def _excel_time(v):
    try:
        f = float(v) % 1
    except ValueError:
        return v
    m = int(round(f * 1440))
    return '%02d:%02d' % (m // 60 % 24, m % 60)


def _table(rows, need, date_field, time_field):
    for k, (line, cells) in enumerate(rows[:HEADER_SCAN]):
        names = [str(cells.get(i, '')).strip() for i in range(max(cells) + 1)] if cells else []
        if not set(need) <= set(names):
            continue
        out = []
        for line2, cells2 in rows[k + 1:]:
            if not any(str(v).strip() for v in cells2.values()):
                continue
            d = {}
            for i, name in enumerate(names):
                if not name:
                    continue
                v = cells2.get(i, '')
                v = '' if v is None else str(v)
                if name == date_field and v.replace('.', '', 1).isdigit() and '-' not in v and len(v) <= 8:
                    v = _excel_date(v)
                elif name == time_field and v and v.replace('.', '', 1).isdigit():
                    v = _excel_time(v)
                d[name] = v
            out.append((line2, d))
        return [n for n in names if n], out
    return None


def _from_xlsx(zf, need, date_field, time_field):
    for rows in _sheets(zf):
        t = _table(rows, need, date_field, time_field)
        if t:
            return t
    raise ValueError('no-table: no sheet holds the export headers')


def read(path, need, password=None, date_field=None, time_field=None):
    """A zip / xlsx export -> (fieldnames, [(line, row)]). A CSV inside an
    archive comes back as ('csv-text', text) for the caller's CSV reader.
    A broken archive / sheet is a ValueError (no-table), never a traceback."""
    try:
        return _read(path, need, password, date_field, time_field)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, EOFError, zlib.error) as e:
        raise ValueError('no-table: the file is not a readable zip / xlsx (%s)' % type(e).__name__)


def _read(path, need, password, date_field, time_field):
    k = kind(path)
    if k == 'ole':
        raise ValueError('xlsx-locked: an Excel file with its own password (or a legacy .xls)')
    if k != 'zip':
        raise ValueError('not-an-archive')
    with zipfile.ZipFile(path) as zf:
        if _is_xlsx(zf):
            return _from_xlsx(zf, need, date_field, time_field)
        members = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith('__MACOSX/')
                   and i.filename.lower().endswith(('.xlsx', '.csv'))]
        if not members:
            raise ValueError('no-table: the archive holds no xlsx / csv file')
        info = sorted(members, key=lambda i: (not i.filename.lower().endswith('.xlsx'), i.filename))[0]
        data = _read_member(zf, info, password)
    if info.filename.lower().endswith('.csv'):
        for enc in ('utf-8-sig', 'cp949'):
            try:
                return 'csv-text', data.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError('no-table: the csv in the archive is not text')
    if data[:8] == OLE_MAGIC:
        raise ValueError('xlsx-locked: the Excel file inside has its own password')
    try:
        inner = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError('no-table: the xlsx in the archive is not readable')
    with inner:
        if not _is_xlsx(inner):
            raise ValueError('no-table: the xlsx in the archive is not readable')
        return _from_xlsx(inner, need, date_field, time_field)
