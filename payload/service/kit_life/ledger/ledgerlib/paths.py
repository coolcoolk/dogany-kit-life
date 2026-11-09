"""Instance layout as seen from module_ledger. Every path is derived here once.

Installed shape: <root>/service/kit_life/ledger/ledgerlib/paths.py, so the
root is four levels up (kit_life 2.46.0; pack_ledger shipped it at
<root>/service/ledger/). LEDGER_ROOT overrides (tests, and hooks that already know
the root); DOGANY_INSTANCE_ROOT is the framework's own hook variable.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE_DIR = HERE.parent


def root():
    for key in ('LEDGER_ROOT', 'DOGANY_INSTANCE_ROOT'):
        val = os.environ.get(key)
        if val and os.path.isdir(val):
            return Path(val)
    return SERVICE_DIR.parent.parent.parent


def kit_sh():
    return Path(os.environ.get('LEDGER_KIT_SH') or root() / 'database' / 'kit_life.sh')


def state_dir():
    """Module-owned instance state (decisions, automation check and sender
    list, SMS unread queue, receiver cursor, poll log).
    Never ledger data -- that lives in the kit DB and only there."""
    d = root() / 'files' / 'ledger'
    d.mkdir(parents=True, exist_ok=True)
    return d


def templates_dir():
    return SERVICE_DIR / 'templates'


def routine_ctl():
    return Path(os.environ.get('LEDGER_ROUTINE_CTL') or root() / 'routines' / 'lib' / 'routine-ctl.sh')


CONF = 'kit_life.conf'
# pack_ledger (<= 0.2.1) kept these keys in config/pack_ledger.conf. An
# instance that ran the pack still has its answers there (LEDGER_SPEND_APP,
# a changed LEDGER_SMS_POLL_AT): read it after kit_life.conf so those values
# keep winning until conf_set moves the key over.
LEGACY_CONF = 'pack_ledger.conf'


def _read_conf(p, out):
    try:
        for line in p.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                if v.strip() or k.strip() not in out:
                    out[k.strip()] = v.strip()
    except OSError:
        pass


def conf():
    """LEDGER_* keys of kit_life.conf (merged add-only into <root>/config/),
    then the legacy pack_ledger.conf. KEY=VALUE, # comments. An empty value
    never overrides a set one."""
    out = {}
    for p in (SERVICE_DIR.parent.parent.parent / 'config' / CONF,
              root() / 'config' / CONF,
              root() / 'config' / LEGACY_CONF):
        _read_conf(p, out)
    defaults = {'LEDGER_SMS_POLL_AT': '21:40', 'LEDGER_SMS_SCAN_DAYS': '7',
                'LEDGER_RECEIVER_DB': '~/.transporter/events.sqlite3',
                'LEDGER_TOP_CARDS_DAYS': '90', 'LEDGER_TOP_CARDS_MAX': '3',
                'LEDGER_PAIR_RECOMMENDATION_COPY': 'on'}
    for k, v in defaults.items():
        if not out.get(k):
            out[k] = v
    return out


def _rewrite(p, key, new):
    """Replace KEY's line in p (or append it when new is not None); drop it
    when new is None. Atomic. Missing file + nothing to write = no-op."""
    try:
        lines = p.read_text(encoding='utf-8').splitlines()
    except OSError:
        if new is None:
            return
        lines = []
    out, hit = [], False
    for line in lines:
        if line.strip().partition('=')[0].strip() == key and not line.strip().startswith('#'):
            if new is not None and not hit:
                out.append(new)
            hit = True
            continue
        out.append(line)
    if not hit:
        if new is None:
            return
        out.append(new)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text('\n'.join(out) + '\n', encoding='utf-8')
    tmp.replace(p)


def conf_set(key, value):
    """Write one KEY=VALUE into the instance's config/kit_life.conf (the
    instance value wins on the kit's add-only merge). Replaces the key's
    line if present, else appends; the same key in the legacy
    pack_ledger.conf is dropped so the two never disagree. One line, no
    newline in the value."""
    value = ' '.join(str(value).split())
    _rewrite(root() / 'config' / CONF, key, '%s=%s' % (key, value))
    _rewrite(root() / 'config' / LEGACY_CONF, key, None)


def agent_conf_value(key, env=True):
    """env <key> (when env), else the first `key=value` in config/agent.conf
    (one matched quote pair stripped -- the framework sources that file).
    None when unset."""
    if env and os.environ.get(key):
        return os.environ[key]
    try:
        for line in (root() / 'config' / 'agent.conf').read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith('export '):
                line = line[7:].lstrip()
            if line.startswith(key + '='):
                val = line.split('=', 1)[1].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                return val or None
    except OSError:
        pass
    return None
