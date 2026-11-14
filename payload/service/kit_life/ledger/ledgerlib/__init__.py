"""Resolve shared kit helpers for every direct ledger service entry point."""
import sys
from pathlib import Path

from . import paths

# Some entry points import renderers before the SDK facade. Their shared
# helpers belong to this payload when it has a sibling database directory;
# installed service generations can instead use the instance's kit entry.
database = Path(__file__).resolve().parents[4] / 'database'
if not (database / 'item_grouping.py').is_file():
    database = paths.kit_sh().parent
sys.path.insert(0, str(database))
