"""Reviewed legacy provenance from ordered, hash-verified offline snapshots.

No ID-only joins. Historical snapshots are copied before SQLite opens them.
Import re-derives every claim rather than trusting editable JSON old values.
"""
import contextlib
import datetime as dt
import hashlib
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import uuid
from zoneinfo import ZoneInfo

import ingest_reset as ir
from ingest_ledger import _open, _parser, _refuse, now_utc

FORMAT = 'dogany-ingest-provenance-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def snapshots(paths, expected=None):
    """Never open an original with SQLite (including read-only/WAL sidecars)."""
    with tempfile.TemporaryDirectory(prefix='ingest-evidence-') as tmp:
        conns, citations = [], []
        try:
            for i, name in enumerate(paths):
                path = Path(name).resolve()
                if path.name == 'kit_life.db':
                    raise ValueError('use offline backups, never live kit_life.db')
                if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-journal')):
                    raise ValueError('backup has a sidecar; provide a consolidated offline snapshot')
                dest = Path(tmp) / ('%d.db' % i)
                shutil.copyfile(path, dest)
                digest = sha(dest)
                if sha(path) != digest:
                    raise ValueError('backup changed during copy')
                citation = {'file': str(path), 'sha256': digest}
                if expected is not None and citation != expected[i]:
                    raise ValueError('backup file/sha256 mismatch')
                c = sqlite3.connect(dest.as_uri() + '?mode=ro&immutable=1', uri=True)
                conns.append(c)
                if c.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('invalid backup')
                citations.append(citation)
            yield conns, citations
        finally:
            for c in conns:
                c.close()


def fingerprint(conn, wid):
    sets = [{k: v for k, v in r.items() if k not in ('id', 'session_id')}
            for r in ir.rows(conn, 'workout_sets', 'session_id=?', (wid,))]
    return hashlib.sha256(ir.encode(sorted(sets, key=ir.encode)).encode()).hexdigest()


def identity(conn, table, row):
    if table == 'metric_log':
        return {k: row[k] for k in ('date', 'metric')}
    # This schema has no workout start column. Retain a start time if one is
    # present in the note, and the creation stamp as an additional discriminator.
    note = row['note'] or ''
    start = re.search(r'\b\d{1,2}:\d{2}(?::\d{2})?\b', note)
    return {'date': row['date'], 'kind': row['session_type_id'],
            'created_at': row['created_at'], 'start_time': start.group() if start else None,
            'note_sha256': hashlib.sha256(ir.encode(row['note']).encode()).hexdigest(),
            'sets_sha256': fingerprint(conn, row['id'])}


def candidates(conn, table, row, ident):
    pool = ir.rows(conn, table, 'date=?', (row['date'],))
    exact = [r for r in pool if identity(conn, table, r) == ident]
    if table == 'metric_log':
        return exact, exact
    # Changed note/kind/sets must not turn an existing owner row into a
    # "created" row. A same-day ambiguous neighbour requires manual resolution.
    near = [r for r in pool if r['created_at'] == row['created_at'] or
            r['session_type_id'] == row['session_type_id']]
    return exact, near


def refkey(row):
    return tuple(row[k] for k in ('source', 'kind', 'ext_id'))


def derive(conn, backups, citations, timezone):
    zone = ZoneInfo(timezone)
    refs = sorted(ir.rows(conn, 'ingest_ref', 'source=?', ('healthkit',)), key=ir.encode)
    histories = [ir.rows(c, 'ingest_ref', 'source=?', ('healthkit',)) for c in backups]
    previous = set()
    for history in histories:
        keys = {refkey(r) for r in history}
        if not previous <= keys:
            raise ValueError('backups are out of order or contain a reset/reused ref')
        previous = keys
    result = {'format': FORMAT, 'source': 'healthkit', 'timezone': timezone,
              'backups': citations, 'refs': refs, 'targets': [], 'unresolved': []}
    for table, target in sorted({(r['target_table'], r['target_id']) for r in refs}, key=str):
        item = {'table': table, 'id': target}
        try:
            if table not in ir.TARGETS:
                raise ValueError('unsupported target')
            row = ir.get(conn, table, {'id': target})
            if row is None:
                raise ValueError('missing current target')
            selected = [r for r in refs if (r['target_table'], r['target_id']) == (table, target)]
            if len(ir.rows(conn, 'ingest_ref', 'target_table=? AND target_id=?', (table, target))) != len(selected):
                raise ValueError('target is shared with another source')
            keys = {refkey(r) for r in selected}
            appearances = [i for i, h in enumerate(histories) if keys & {refkey(r) for r in h}]
            if not appearances or appearances[0] == 0:
                raise ValueError('need both a pre-ref and a post-ref backup')
            first = appearances[0]
            # Require independent post-ingest evidence, not just the current DB.
            if any(r['revision'] != 1 for r in selected):
                raise ValueError('revised legacy refs need a write history')
            last_refs = {refkey(r): r for r in histories[-1]}
            if any(last_refs.get(refkey(r)) != r for r in selected):
                raise ValueError('latest backup does not witness all current refs unchanged')
            ident = identity(conn, table, row)
            observations = [candidates(c, table, row, ident) for c in backups]
            if any(len(exact) > 1 for exact, near in observations):
                raise ValueError('ambiguous content identity')
            if any(not exact for exact, near in observations[first:]):
                raise ValueError('post-ref content identity changed or disappeared')
            current_refs = {refkey(r): r for r in selected}
            for c, history, (exact, near) in zip(backups[first:], histories[first:], observations[first:]):
                matched = exact[0]
                witnessed = [r for r in history if refkey(r) in keys]
                if any(r['target_table'] != table or r['target_id'] != str(matched['id']) or
                       any(r[k] != current_refs[refkey(r)][k] for k in
                           ('payload_hash', 'revision', 'occurred_at', 'first_seen'))
                       for r in witnessed):
                    raise ValueError('historical ref does not identify the matched content unchanged')
                if any(refkey(r) not in keys for r in ir.rows(c, 'ingest_ref',
                       'target_table=? AND target_id=?', (table, str(matched['id'])))):
                    raise ValueError('historical target has another contributing source/ref')
            post = observations[first][0][0]
            for exact, near in observations[first:]:
                if any(exact[0][k] != row[k] for k in ir.FIELDS[table]):
                    raise ValueError('field changed after first witnessed ingest; owner edit possible')
            pre, near = observations[first - 1]
            if pre:
                old = pre[0]
                if ir.rows(backups[first-1], 'ingest_ref', 'target_table=? AND target_id=?', (table, str(old['id']))):
                    raise ValueError('pre-ref row already has another ingest receipt')
                fields = {k for k in ir.FIELDS[table] if old[k] != post[k]}
                if table == 'workouts' and fields & {'kcal', 'kcal_origin'}:
                    fields |= {'kcal', 'kcal_origin'}
                effect = {'created': False,
                          'before': {k: old[k] for k in sorted(fields)},
                          'after': {k: row[k] for k in sorted(fields)}}
                classification = 'attached'
            else:
                if near or any(exact for exact, near in observations[:first]):
                    raise ValueError('possible owner row changed/disappeared before ref')
                # Absence alone is not sufficient: require creation/write time
                # to fall in the witnessed ref batch (SQLite local timestamp).
                stamp = dt.datetime.fromisoformat(row['created_at']).replace(tzinfo=zone).timestamp()
                times = [dt.datetime.fromisoformat(r['first_seen'].replace('Z', '+00:00')).timestamp() for r in selected]
                if not min(times) - 5 <= stamp <= max(times) + 5:
                    raise ValueError('creation time is not in the witnessed ingest batch')
                if any({k: v for k, v in exact[0].items() if k != 'id'} !=
                       {k: v for k, v in row.items() if k != 'id'}
                       for exact, near in observations[first:]):
                    raise ValueError('created row changed after first witnessed ingest')
                effect = {'created': True, 'row': row}
                if table == 'workouts':
                    if ir.dependent_rows(conn, table, row):
                        raise ValueError('created candidate has owner dependents')
                    effect['classifications'] = ir.children(conn, row['id'])
                    if any(ir.children(c, exact[0]['id']) != effect['classifications']
                           for c, (exact, near) in zip(backups[first:], observations[first:])):
                        raise ValueError('created classifications changed after ingest')
                classification = 'created'
            item.update(classification=classification, identity=ident, current=row,
                        effect=effect, pre_backup=first-1, post_backup=first,
                        refs=[list(refkey(r)) for r in selected])
            result['targets'].append(item)
        except (ValueError, KeyError) as exc:
            result['unresolved'].append(dict(item, reason=str(exc)))
    return result


def review(doc):
    lines = ['# Legacy ingest provenance review', '',
             'Snapshot-boundary reconstruction; verify the listed transitions before applying.',
             'Dates, kind, start (when stored), note hash and set fingerprint identify workouts; IDs only locate current refs.',
             'Created requires absence before the ref, an independent post-ref witness and a matching ingest-batch timestamp.',
             'A snapshot interval cannot prove the absence of an unrecorded intermediate owner write.', '',
             '| Target | Classification | Pre-ingest -> current | Before / after backup |',
             '| --- | --- | --- | --- |']
    for t in doc['targets']:
        e = t['effect']
        if e['created']:
            fields = sorted((ir.FIELDS[t['table']] | {'date'}) - {'note'})
            changes = 'absent -> ' + '; '.join('%s=%s' % (k, ir.encode(e['row'][k])) for k in fields)
            if t['table'] == 'metric_log':
                changes += '; metric=' + e['row']['metric']
            changes += ' (archive + delete; complete image in evidence JSON)'
        else:
            changes = '; '.join('%s: %s -> %s' % (k, ir.encode(v), ir.encode(e['after'][k]))
                                for k, v in e['before'].items()) or 'no field changes'
        names = [Path(doc['backups'][t[k]]['file']).name for k in ('pre_backup', 'post_backup')]
        lines.append('| %s %s | %s | %s | %s |' % (t['table'], t['id'], t['classification'], changes.replace('|', '\\|'), ' / '.join(names)))
    for t in doc['unresolved']:
        lines.append('| %s %s | REFUSED | %s | - |' % (t['table'], t['id'], t['reason']))
    lines += ['', 'Backup citations (SHA-256):', '']
    lines += ['* `%s` - `%s`' % (b['file'], b['sha256']) for b in doc['backups']]
    return '\n'.join(lines) + '\n'


def cmd_import(ctx, argv):
    p = _parser('ingest-provenance-import')
    p.add_argument('--evidence', required=True)
    p.add_argument('--apply', action='store_true')
    a = p.parse_args(argv)
    conn = _open(ctx)
    try:
        doc = ir.decode(Path(a.evidence).read_text())
        if not isinstance(doc, dict) or doc.get('format') != FORMAT or not doc.get('backups'):
            raise ValueError('unsupported evidence or missing backup citations')
        with snapshots([b['file'] for b in doc['backups']], doc['backups']) as (backups, citations):
            conn.execute('BEGIN IMMEDIATE' if a.apply else 'BEGIN')
            verified = derive(conn, backups, citations, doc['timezone'])
            if ir.encode(verified) != ir.encode(doc):
                raise ValueError('evidence differs from backup-derived claims/current refs or target images')
            if doc['unresolved']:
                raise ValueError('unresolved targets; no evidence imported')
            inserts = []
            for t in doc['targets']:
                for source, kind, ext_id in t['refs']:
                    row = dict(source=source, kind=kind, ext_id=ext_id,
                               target_table=t['table'], target_id=t['id'], effect_json=ir.encode(t['effect']))
                    key = {k: row[k] for k in ('source', 'kind', 'ext_id')}
                    existing = ir.get(conn, 'ingest_effect', key)
                    if existing is not None and existing != row:
                        raise ValueError('existing provenance conflicts; undo the old import first')
                    if existing is None:
                        inserts.append((key, row))
            print('PROVENANCE %s targets=%d refs=%d' % ('apply' if a.apply else 'dry-run', len(doc['targets']), len(inserts)))
            if not a.apply or not inserts:
                return 0
            import_id = uuid.uuid4().hex
            audit = {'operation': 'provenance-import', 'evidence': doc}
            conn.execute('INSERT INTO archive_ingest_reset_runs VALUES(?,?,?,?,NULL)',
                         (import_id, 'healthkit', ir.encode(audit), now_utc()))
            for i, (key, row) in enumerate(inserts):
                cur = conn.execute('INSERT INTO ingest_effect VALUES(?,?,?,?,?,?)', tuple(row.values()))
                conn.execute('INSERT INTO archive_ingest_reset_rows VALUES(?,?,?,?,?,?,?)',
                             (import_id, i, 'ingest_effect', ir.encode(key), 'null', ir.encode(row), cur.lastrowid))
            for key, row in inserts:
                if ir.get(conn, 'ingest_effect', key) != row:
                    raise ValueError('trigger changed imported evidence')
            conn.commit()
            print('IMPORTED import_id=%s refs=%d' % (import_id, len(inserts)))
            return 0
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, IndexError) as exc:
        return _refuse(str(exc))
    finally:
        conn.rollback()
        conn.close()


def cmd_import_undo(ctx, argv):
    p = _parser('ingest-provenance-import-undo')
    p.add_argument('--import-id', required=True)
    a = p.parse_args(argv)
    conn = _open(ctx)
    try:
        conn.execute('BEGIN IMMEDIATE')
        run = ir.get(conn, 'archive_ingest_reset_runs', {'reset_id': a.import_id})
        if run is None or not isinstance(ir.decode(run['kinds_json']), dict) or ir.decode(run['kinds_json']).get('operation') != 'provenance-import':
            raise ValueError('unknown provenance import-id')
        if run['undone_at']:
            print('IMPORT-UNDO SAME import_id=' + a.import_id)
            return 0
        changes = ir.rows(conn, 'archive_ingest_reset_rows', 'reset_id=?', (a.import_id,))
        for c in changes:
            key = ir.decode(c['key_json'])
            where, args = ir.predicate(key)
            rowid = conn.execute('SELECT rowid FROM ingest_effect WHERE ' + where, args).fetchone()
            if (ir.get(conn, 'ingest_effect', key) != ir.decode(c['after_json']) or
                    rowid != (c['archive_rowid'],)):
                raise ValueError('import undo conflict; undo reset or subsequent evidence changes first')
        for c in changes:
            where, args = ir.predicate(ir.decode(c['key_json']))
            conn.execute('DELETE FROM ingest_effect WHERE ' + where, args)
        for c in changes:
            if ir.get(conn, 'ingest_effect', ir.decode(c['key_json'])) is not None:
                raise ValueError('trigger prevented import undo')
        conn.execute('UPDATE archive_ingest_reset_runs SET undone_at=? WHERE reset_id=?', (now_utc(), a.import_id))
        conn.commit()
        print('IMPORT-UNDO restored import_id=' + a.import_id)
        return 0
    except (sqlite3.Error, ValueError) as exc:
        return _refuse(str(exc))
    finally:
        conn.rollback()
        conn.close()
