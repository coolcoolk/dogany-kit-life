#!/usr/bin/env python3
"""Compatibility prompt hook; weekly-retro-start owns the typed transition.

No free message routes a weekly review or stamps its marker.
"""
import sys as _sys, os as _os; _sys.dont_write_bytecode = True; _os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # C4b: residue under payload/ makes pack_install refuse the pack
import importlib.util
import json
import os
import sys


def root():
    env = os.environ.get('DOGANY_INSTANCE_ROOT')
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    # Compatibility hook is inert. The agent selects weekly-retro-start with
    # a typed week end; neither marker writes nor routing depend on wording.
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
