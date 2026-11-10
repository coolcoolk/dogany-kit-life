#!/usr/bin/env python3
"""Apply migration 104 and import the agents' legacy answers in one transaction.

Usage: python3 satiety_migrate.py --db /copy/kit_life.db --member owner \
         --cache health=/copy/health-food_cache.json --cache life=/copy/life-food_cache.json

Install hook: satiety_migrate.py --auto --instance /instance/root
Discovers crew.conf beside the resolved DB; source JSON is never modified.
Already-upgraded DBs can import additional legacy caches; reruns are idempotent.
Every agent using this DB must use the same LIFEKIT_MEMBER_ID (default: owner).
"""
import sys
sys.dont_write_bytecode = True
import argparse
import json
import os
from pathlib import Path
import sqlite3


def migrate(db, caches, member='owner'):
    import kit_life as kit
    import satiety_store
    # Validate every input before starting the schema transaction.
    sources = []
    for agent, path in caches:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or not isinstance(data.get('meal_times', {}), dict):
            raise ValueError('invalid meal cache: %s' % path)
        sources.append((agent, data))
    conn = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=rw', uri=True)
    try:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if version == 103:
            sql = (Path(__file__).parent / 'migrations/104_shared_satiety.sql').read_text()
            sql = '\n'.join(line for line in sql.splitlines() if not line.startswith('.'))
            # Keep the DDL transaction open through the data import.
            conn.executescript(sql.replace('COMMIT;', ''))
        elif version >= 104:  # store exists from 104 on (105+ add unrelated tables)
            conn.execute('BEGIN IMMEDIATE')
        else:
            raise ValueError('satiety migration requires kit DB at 103 or later, got %s' % version)
        counts = {agent: satiety_store.import_cache(kit, conn, data, agent, member)
                  for agent, data in sources}
        conn.commit()
        return counts
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def crew_roots(db, root):
    """Use the kit's crew.conf member= registry, or this solo instance.

    Unlike the health-owner display helper, migration must propagate registry
    read errors rather than treating an unreadable crew as an empty crew.
    """
    registry = Path(db).resolve().parent / 'crew.conf'
    try:
        text = registry.read_text(encoding='utf-8')
    except FileNotFoundError:
        return [Path(root).resolve()]
    roots = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('member='):
            value = line.partition('=')[2].strip()
            if not value or not Path(value).is_absolute():
                raise ValueError('invalid crew member in %s' % registry)
            member_root = Path(value).resolve()
            if member_root not in roots:
                roots.append(member_root)
    own = Path(root).resolve()
    if own not in roots:
        roots.append(own)  # first install may precede membership registration
    return roots


def receipt_key(root, member):
    return 'satiety.legacy-import.v1:' + json.dumps([member, str(root)], ensure_ascii=False)


def cache_stamp(path):
    try:
        stat = path.stat()
    except FileNotFoundError:
        if path.is_symlink():
            raise ValueError('broken meal cache link: %s' % path)
        return None
    return [stat.st_mtime_ns, stat.st_size]


def pending_sources(conn, db, root, member, errors=None):
    sources = []
    for inst in crew_roots(db, root):
        key = receipt_key(inst, member)
        path = inst / 'database/food_cache.json'
        try:
            stamp = json.dumps(cache_stamp(path))
            receipt = conn.execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
            if receipt and receipt[0] == stamp:
                continue
            try:
                raw = path.read_text(encoding='utf-8')
            except FileNotFoundError:
                if path.is_symlink():
                    raise ValueError('broken meal cache link: %s' % path)
                if stamp != 'null':
                    raise ValueError('meal cache changed during import: %s' % path)
                sources.append((key, inst.name, None, stamp))
                continue
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('meal_times', {}), dict):
                raise ValueError('invalid meal cache: %s' % path)
            if json.dumps(cache_stamp(path)) != stamp:
                raise ValueError('meal cache changed during import: %s' % path)
        except (OSError, ValueError) as exc:
            if errors is None:
                raise
            errors.append(exc)
            print('satiety import pending: cannot read %s: %s' % (path, exc), file=sys.stderr)
            continue
        sources.append((key, inst.name, data, stamp))
    return sources


def import_pending(kit, conn, db, root, member):
    """Caller holds the write lock; receipts and facts share its transaction."""
    import satiety_store
    sources = pending_sources(conn, db, root, member)
    counts = {}
    for key, agent, data, stamp in sources:
        counts[agent] = (satiety_store.import_cache(kit, conn, data, agent, member)
                         if data is not None else 0)
        conn.execute('INSERT INTO config(key,value) VALUES (?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, stamp))
    return counts


def auto_import(root, member=None, kit=None, db=None):
    """Import the entire crew before acknowledging any source, atomically.

    The framework applies schema migrations before post_install. If this hook
    fails, receipts and shared answers roll back together; the compatibility
    reader continues reading the untouched legacy caches until a retry succeeds.
    Receipts track cache versions so a lagging legacy writer is imported again.
    """
    if kit is None:
        import kit_life as kit
    import satiety_store
    member = satiety_store.member_id() if member is None else member
    if not member.strip():
        raise ValueError('member must not be empty')
    db = Path(db) if db is not None else Path(root) / 'database/kit_life.db'
    conn = sqlite3.connect(db.resolve().as_uri() + '?mode=rw', uri=True)
    try:
        conn.execute('BEGIN IMMEDIATE')
        counts = import_pending(kit, conn, db, root, member)
        conn.commit()
        return counts
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_import(kit, conn):
    """First-use fallback for frameworks without post_install support.

    Recheck receipts under BEGIN IMMEDIATE (or the caller's existing write
    transaction) so concurrent agents cannot import the same cache twice.
    A savepoint keeps a failed import from disturbing the caller's work.
    """
    import satiety_store
    member = satiety_store.member_id()
    root = kit._kit_root()
    try:
        if not pending_sources(conn, kit.DB_PATH, root, member):
            return
        if conn.in_transaction and not conn.execute('PRAGMA query_only').fetchone()[0]:
            conn.execute('SAVEPOINT satiety_legacy_import')
            try:
                import_pending(kit, conn, kit.DB_PATH, root, member)
            except BaseException:
                conn.execute('ROLLBACK TO satiety_legacy_import')
                raise
            finally:
                conn.execute('RELEASE satiety_legacy_import')
        else:
            auto_import(root, member, kit=kit, db=kit.DB_PATH)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print('satiety import pending: %s' % exc, file=sys.stderr)


def load_compatible(kit, conn):
    """Import on first use, keeping old answers readable if import fails.

    This is a read-only overlay, never a second writer or a silent empty store.
    Successful install receipts retire it, including caches for absent sources.
    """
    import satiety_store
    ensure_import(kit, conn)
    member = satiety_store.member_id()
    shared = satiety_store.load(conn, member)
    errors = []
    for _key, agent, data, _stamp in pending_sources(
            conn, kit.DB_PATH, kit._kit_root(), member, errors=errors):
        if data is not None:
            satiety_store.merge_cache(kit, conn, shared, data, agent)
    if errors and not shared['meal_times']:
        raise ValueError('legacy satiety import incomplete; no readable calibration source')
    # Saving a later answer must only persist the caller's actual changes.
    return satiety_store.State(shared['meal_times'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db')
    parser.add_argument('--auto', action='store_true')
    parser.add_argument('--instance')
    parser.add_argument('--member', default=os.environ.get('LIFEKIT_MEMBER_ID', 'owner'))
    parser.add_argument('--cache', action='append', default=[], metavar='AGENT=PATH')
    args = parser.parse_args()
    if args.auto:
        if not args.instance or args.db or args.cache:
            parser.error('--auto requires --instance, without --db or --cache')
        print(json.dumps(auto_import(args.instance, args.member), ensure_ascii=False))
        return
    if not args.db or not args.cache:
        parser.error('explicit migration requires --db and --cache')
    caches = []
    for value in args.cache:
        agent, sep, path = value.partition('=')
        if not sep or not agent.strip() or not path:
            parser.error('--cache requires AGENT=PATH')
        caches.append((agent, path))
    if not args.member.strip():
        parser.error('--member must not be empty')
    print(json.dumps(migrate(args.db, caches, args.member), ensure_ascii=False))


if __name__ == '__main__':
    main()
