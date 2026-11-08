#!/usr/bin/env python3
"""Compatibility prompt hook; the agent selects the explicit ingest-link verb.

No owner-word, negation or topic regex routes a free message.
"""
import json
import os
from pathlib import Path
import re
import sys

def matches(prompt):
    """Compatibility reader; the agent selects the explicit ingest-link verb."""
    return False


def main():
    try:
        payload = json.load(sys.stdin)
        prompt = payload.get('prompt', '')
        if not isinstance(prompt, str) or not matches(prompt):
            return
        root = Path(os.environ.get('DOGANY_INSTANCE_ROOT') or Path(__file__).absolute().parents[2])
        sys.path.insert(0, str(root / 'database'))
        from ingest_link import copy_bundle
        print(copy_bundle()['ingest.link.hook'])
    except (OSError, ValueError, TypeError, AttributeError, ImportError, KeyError):
        # A kit-less install must still not invent an on/off state.
        return


if __name__ == '__main__':
    main()
