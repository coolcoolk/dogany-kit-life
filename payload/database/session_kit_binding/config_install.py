"""Private installer seat for manifest-declared session facts and attestations."""
from contextlib import closing
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import tempfile
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from session_command import canonical, digest
from session_delivery import decode, fields, integer, hash_value
from session_v2_contract.policy import CatalogEntry
from session_kit_binding import validate_capability_response
from session_kit_binding import configuration_digest, private_json, activation_authority

OWNERS = ('pack', 'kit', 'framework')
ATTESTED = ('challenge', 'capability_receipt', 'legacy_retirement', 'installation_closure')
FACTS = 'catalog catalog_version program equipment machines splits free_start post timezone capability_max_age_ms'


class InstallError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def need(value, code, detail):
    if not value:
        raise InstallError(code, detail)


def installed_path(root, relative):
    need(type(relative) is str and relative and not Path(relative).is_absolute() and
         all(p not in ('', '.', '..') for p in relative.split('/')),
         'MANIFEST', 'expected a normalized installed root-relative path')
    path = root / relative
    need(path.resolve().is_relative_to(root), 'MANIFEST', 'artifact escapes installed root: ' + relative)
    need(path.is_file(), 'MANIFEST', 'installed file missing: ' + relative)
    return path.resolve()


def delivery_block(declared, activation):
    """Installed delivery identity; per-session scopes are never guessed here.

    A session scope (session id, chat, surface, epoch) exists only once a
    session does, so the block carries a slot template and no scopes. The kit
    derives each scope from the publisher-MAC'd envelope export it verifies.
    The publisher defaults to the activated pack; recovery authority to the
    activation authority. Coordinator authority is never granted here.
    """
    declared = {} if declared is None else declared
    need(type(declared) is dict, 'MANIFEST', 'delivery must be an object')
    fields(declared, '', 'publisher_id actors slots action_slot')
    from session_v2_contract.transport import SURFACE_KINDS
    publisher = declared.get('publisher_id', activation['pack'])
    need(type(publisher) is str and re.fullmatch('[a-z][a-z0-9_]{1,40}', publisher),
         'MANIFEST', 'delivery publisher_id must match [a-z][a-z0-9_]{1,40}')
    authority = activation['authority_id']
    need(re.fullmatch('[a-z][a-z0-9_]{1,40}', authority), 'MANIFEST', 'activation authority_id is not a delivery authority id')
    actors = declared.get('actors', ['member', 'trainer', 'system'])
    slots = declared.get('slots', {'trainer': 'trainer', 'table': 'table'})
    action = declared.get('action_slot', 'trainer')
    need(type(actors) is list and actors and all(type(a) is str for a in actors) and len(set(actors)) == len(actors),
         'MANIFEST', 'delivery actors must be a unique nonempty list')
    need(type(slots) is dict and slots.get(action) == 'trainer' and len(set(slots.values())) == len(slots) and
         all(v in SURFACE_KINDS for v in slots.values()) and 'answer' not in slots,
         'MANIFEST', 'delivery slots must map to unique surface kinds with a trainer action slot')
    return dict(publisher_id=publisher, coordinator=False, recovery_authorities=[authority], scopes=[],
                scope_template=dict(actors=actors, slots=slots, action_slot=action))


def build_configuration(root, manifest_path):
    """Deterministic, key-free input for the native capability probe."""
    manifest_file = installed_path(root, manifest_path)
    manifest = decode(manifest_file.read_bytes())
    need(type(manifest) is dict and 'session_configuration' in manifest,
         'MANIFEST', 'manifest must declare session_configuration (kit_session_configuration/1)')
    m = manifest['session_configuration']
    fields(m, 'schema pack_v2_path templates_path facts_path artifact_files activation', 'delivery')
    need(m['schema'] in ('kit_session_configuration/1', 'kit_session_configuration/2'),
         'MANIFEST', 'unsupported session configuration schema')
    fields(m['activation'], 'authority_id pack engine generation')
    activation = m['activation']
    for key in ('authority_id', 'pack', 'engine'):
        need(type(activation[key]) is str and re.fullmatch('[a-z][a-z0-9_-]{1,63}', activation[key]),
             'MANIFEST', 'invalid activation identity: ' + key)
    integer(activation['generation'], 1)
    need(activation['pack'] == manifest.get('id') and activation['engine'] == 'v2',
         'MANIFEST', 'activation must identify this v2 pack')
    delivery = delivery_block(m.get('delivery'), activation)
    facts_path = installed_path(root, m['facts_path'])
    config = decode(facts_path.read_bytes())
    program_v2 = m['schema'] == 'kit_session_configuration/2'
    if program_v2:
        from session_program import validate_executable
        validate_executable(config)
    else:
        fields(config, FACTS, 'legacy_member_sessions')
        integer(config['catalog_version'], 1)
        integer(config['capability_max_age_ms'], 1)
        ZoneInfo(config['timezone'])
        need(type(config['catalog']) is list and config['catalog'], 'MANIFEST', 'catalog must be nonempty')
        variants = set()
        for entry in config['catalog']:
            entry = CatalogEntry(entry)
            need(entry['catalog_version'] == config['catalog_version'] and entry['review_status'] == 'approved' and
                 entry['variant_id'] not in variants, 'MANIFEST', 'catalog must be reviewed, unique and version-pinned')
            variants.add(entry['variant_id'])
        for name in ('program', 'equipment', 'machines', 'splits'):
            need(type(config[name]) is dict, 'MANIFEST', name + ' must be an object')
        need(variants <= config['program'].keys() and variants <= config['equipment'].keys(),
             'MANIFEST', 'catalog variants need program and equipment facts')
        for items in [config['free_start'], config['post'], *config['splits'].values()]:
            need(type(items) is list and all(type(v) is str and v in variants for v in items),
                 'MANIFEST', 'variant lists must reference the reviewed catalog')
    # Resolve only paths declared by the installed manifest. Never consult a
    # source checkout, process environment or live workspace for missing facts.
    compiler = installed_path(root, m['pack_v2_path'] + '/__init__.py').parent
    installed_path(root, m['pack_v2_path'] + '/trainer_runtime.py')
    templates = installed_path(root, m['templates_path'])
    config['delivery'] = delivery
    config.update(installed_root=str(root), db_path=str(root/'database/kit_life.db'),
                  pack_v2_path=str(compiler), templates_path=str(templates), activation=dict(activation))
    need((root/'database/kit_life.db').is_file(), 'MANIFEST', 'installed shared kit DB is missing')
    declarations = m['artifact_files']
    need(type(declarations) is dict and 'installer' not in declarations,
         'MANIFEST', 'artifact_files must not declare installer; use capability_receipt.artifacts.installer')
    fields(declarations, 'pack framework')
    artifacts = {}
    for owner in ('pack', 'framework'):
        paths = declarations[owner]
        need(type(paths) is list and paths and all(type(p) is str for p in paths) and len(set(paths)) == len(paths),
             'MANIFEST', owner + ' artifact list must be nonempty and unique')
        artifacts[owner] = {installed_path(root, p) for p in paths}
    required_pack = {p.resolve() for p in compiler.iterdir() if p.suffix in ('.py', '.json')}
    required_pack.update((manifest_file, facts_path, templates))
    need(required_pack <= artifacts['pack'], 'MANIFEST', 'pack artifacts must pin manifest, facts, compiler and templates')
    from session_kit_binding import executing_kit_files
    artifacts['kit'] = set(executing_kit_files(root))
    files = {}
    for path in sorted(set.union(*artifacts.values())):
        need(path.is_file(), 'MANIFEST', 'installed kit artifact missing: ' + str(path))
        files[str(path)] = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    config['files'] = [dict(path=p, digest=d) for p, d in sorted(files.items())]
    config['artifact_files'] = {o: sorted(str(p) for p in artifacts[o]) for o in OWNERS}
    config['artifact_digests'] = {o: digest({p: files[p] for p in config['artifact_files'][o]}) for o in OWNERS}
    config['config_digest'] = configuration_digest(config)
    if program_v2:
        from session_kit_binding.program_runtime import preflight
        preflight(config)
    return config


def validate_attestation(config, attestation):
    fields(attestation, ' '.join(ATTESTED))
    from session_program import capability_response
    receipt = capability_response(attestation['challenge'], attestation['capability_receipt'],
                                  config.get('required_features', ()))
    # The framework measures its running installer source, which is never
    # copied into the instance. Bind that receipt digest before checking the
    # final configuration digest; --describe only knows installed artifacts.
    config['artifact_digests']['installer'] = receipt['artifacts']['installer']
    config['config_digest'] = configuration_digest(config)
    now = time.time_ns() // 1_000_000
    need(receipt['installed_root'] == config['installed_root'] and receipt['config_digest'] == config['config_digest'] and
         receipt['artifacts'] == config['artifact_digests'] and receipt['capabilities']['kit_schema'] >= 78 and
         0 <= now - receipt['checked_at'] <= config['capability_max_age_ms'],
         'ATTESTATION', 'capability receipt differs from installed inputs or is stale')
    retirement = attestation['legacy_retirement']
    fields(retirement, 'capability_receipt_id routes_revoked workers_fenced surfaces')
    need(retirement['capability_receipt_id'] == receipt['receipt_id'] and
         type(retirement['routes_revoked']) is bool and type(retirement['workers_fenced']) is bool and
         type(retirement['surfaces']) is list, 'ATTESTATION', 'invalid legacy retirement')
    for surface in retirement['surfaces']:
        fields(surface, 'chat_id message_id markup_state acknowledged', 'ack_digest')
        need(type(surface['chat_id']) is int and type(surface['message_id']) is int and surface['message_id'] > 0 and
             surface['markup_state'] in ('clear', 'active', 'unknown') and type(surface['acknowledged']) is bool,
             'ATTESTATION', 'invalid legacy surface')
        if 'ack_digest' in surface:
            hash_value(surface['ack_digest'])
    targets = [dict(chat_id=s['chat_id'], message_id=s['message_id']) for s in retirement['surfaces']]
    need(targets == sorted(targets, key=lambda t:(t['chat_id'], t['message_id'])) and
         len({canonical(t) for t in targets}) == len(targets),
         'ATTESTATION', 'legacy surface inventory must be sorted and unique')
    config.update(attestation)
    closure = attestation['installation_closure']
    if closure is None:
        # Bootstrap is required before the framework can obtain its first F/Q.
        # Writer.capability/admission still refuse until a closure is persisted.
        return
    need(type(closure) is dict, 'ATTESTATION', 'installation closure must be an object or null')
    need(all(closure.get(k) == config['activation'][k] for k in ('authority_id', 'pack')),
         'ATTESTATION', 'closure activation differs')
    integer(closure['generation'], 1)
    config['activation']['generation'] = closure['generation']
    authority = activation_authority(config)
    # The framework owns activation. Verify the supplied closure against its
    # durable F/Q and retirement acknowledgements without changing the DB.
    with closing(sqlite3.connect(Path(config['db_path']).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        from session_command import row
        stored = row(conn, 'SELECT fence,checkpoint FROM session_pack_fences WHERE pack=?', (closure['pack'],))
        need(stored, 'ATTESTATION', 'closure has no installed writer fence')
        authority.validate_closure(conn, closure, decode(stored['fence']), decode(stored['checkpoint']))
    need(retirement['routes_revoked'] and retirement['workers_fenced'] and
         all(s['markup_state'] == 'clear' and s['acknowledged'] for s in retirement['surfaces']) and
         targets == closure['inventory'] and
         all('ack_digest' not in s or s['ack_digest'] == a['ack_digest']
             for s, a in zip(retirement['surfaces'], closure['retirements'])),
         'ATTESTATION', 'legacy retirement must cover the exact cleared closure inventory')


def private_directory(root):
    parent = root/'config'
    if not parent.exists():
        parent.mkdir(mode=0o700)
    st = parent.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022,
         'PRIVATE', 'config parent must be an owner-controlled directory without a symlink')
    target = parent/'kit_life'
    if not target.exists():
        target.mkdir(mode=0o700)
    st = target.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077,
         'PRIVATE', 'config/kit_life must be an owner-private directory without a symlink')
    return target


def install(root, config, attestation, rotate=False):
    from session_program import guard_configuration
    guard_configuration(config)
    if config.get('schema') == 'session_program_facts/2':
        from session_kit_binding.program_runtime import installation_preflight
        installation_preflight(config)
    validate_attestation(config, attestation)
    directory = private_directory(root)
    target = directory/'session-v2.json'
    fd = os.open(directory/'.session-v2.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        st = os.fstat(lock.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077,
             'PRIVATE', 'configuration lock must be owner-private')
        fcntl.flock(lock, fcntl.LOCK_EX)
        key = secrets.token_bytes(32)
        if target.exists() or target.is_symlink():
            st = target.lstat()
            need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o077,
                 'PRIVATE', 'existing configuration must be an owner-private regular file')
            try:
                old = private_json(target)
                old_key = bytes.fromhex(old['service_key_hex'])
            except (ValueError, KeyError, TypeError) as error:
                raise InstallError('PRIVATE', 'existing private service key is malformed') from error
            need(len(old_key) >= 32, 'PRIVATE', 'existing service key is too short')
            if not rotate:
                key = old_key
        config['service_key_hex'] = key.hex()
        config['capability_signature'] = hmac.new(key, canonical(attestation).encode(), hashlib.sha256).hexdigest()
        fd, temporary = tempfile.mkstemp(prefix='.session-v2-', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(canonical(config) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return dict(ok=True, path=str(target), config_digest=config['config_digest'], rotated=rotate)


def main(manifest=None, *, rotate=False, describe=False):
    root = Path(__file__).resolve().parents[2]
    phase = 'MANIFEST'
    try:
        config = build_configuration(root, manifest or 'service/health/v2/session-manifest.json')
        if describe:
            need(not rotate, 'MANIFEST', '--rotate cannot be used with --describe')
            print(canonical(config))
            return 0
        phase = 'ATTESTATION'
        attestation = decode(sys.stdin.read())
        result = install(root, config, attestation, rotate)
        print(canonical(result))
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, ZoneInfoNotFoundError) as error:
        code = error.code if isinstance(error, InstallError) else 'STORAGE' if isinstance(error, (OSError, sqlite3.Error)) else phase
        print(json.dumps(dict(ok=False, error=code, detail=str(error)), ensure_ascii=True), file=sys.stderr)
        return dict(ATTESTATION=2, MANIFEST=3, PRIVATE=4, STORAGE=5)[code]
