#!/usr/bin/env python3
"""ledger-session-start.py -- SessionStart hook for module_ledger (declared in
the kit's pack-manifest.json hooks[], wired by pack_install.sh -- DGN-1496;
pack_ledger declared the same script at the same path before kit_life 2.46.0,
so an instance that ran the pack keeps ONE wiring: same command, idempotent).

One line or nothing: while module onboarding is outstanding
(service/kit_life/ledger/onboarding verdict = OFFER), name the entry skill so
the session knows the setup entry even when module management is still off.
Skill availability is the gate (.claude/skills/ledger-setup), not a bundle
activation flag or management consent. The service's verdict suppresses
completed, declined and otherwise ineligible onboarding. This signal does
not itself enable management or start onboarding. Retro sections
are supplied by the kit's registry, not this hook. The line itself is copy:
i18n key ledger.hook.offer in service/kit_life/i18n/<lang>.json.

CONTRACT (fail-open, always): any error, timeout, missing kit or missing
service prints nothing and exits 0. Reads only -- this hook writes nothing.
"""
import json
import os
import subprocess
import sys

TIMEOUT_S = 20
SKILL = 'ledger-setup'
KEY = 'ledger.hook.offer'


def root():
    env = os.environ.get('LEDGER_ROOT') or os.environ.get('DOGANY_INSTANCE_ROOT')
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(args):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True,
                          timeout=TIMEOUT_S, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))


def lang(r):
    val = os.environ.get('AGENT_LANG')
    if not val:
        try:
            with open(os.path.join(r, 'config', 'agent.conf'), encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    if line.startswith('AGENT_LANG='):
                        val = line.split('=', 1)[1].strip().strip('"\'')
                        break
        except OSError:
            pass
    return (val or 'ko').lower().split('_')[0].split('-')[0] or 'ko'


def copy(r):
    for code in (lang(r), 'ko'):
        try:
            with open(os.path.join(r, 'service', 'kit_life', 'i18n', code + '.json'), encoding='utf-8') as fh:
                s = json.load(fh).get(KEY)
            if s:
                return s
        except (OSError, ValueError):
            continue
    return None


def main():
    r = root()
    if not os.path.exists(os.path.join(r, '.claude', 'skills', SKILL)):
        return 0
    gate = os.path.join(r, 'service', 'kit_life', 'ledger', 'onboarding')
    if not os.path.isfile(gate):
        return 0
    p = run([gate, 'verdict'])
    first = (p.stdout or '').splitlines()[:1]
    parts = first[0].split('\t') if first else []
    if p.returncode == 0 and parts[:2] == ['VERDICT', 'OFFER']:
        line = copy(r)
        if line:
            print(line)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
