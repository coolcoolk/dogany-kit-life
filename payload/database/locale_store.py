"""Locale I/O mechanics; callers own roots, fallback, filtering and caches.

None from framework_bundle means unavailable/invalid; an empty dict is a
successful framework result. No language normalization or implicit fallback.
"""
import importlib.util
import json
import os
from pathlib import Path


def read_json(path, *, encoding=None):
    """Read JSON without fallback, type filtering or caching.

    None retains the caller's default text encoding. I/O and JSON errors
    propagate; tolerant adapters catch their own existing exception set.
    """
    return json.loads(Path(path).read_text(encoding=encoding))


def read_flat(path):
    """UTF-8 JSON object, or {} for a missing/invalid legacy locale file."""
    try:
        with open(path, encoding='utf-8') as source:
            data = json.load(source)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def framework_lookup(root, module_name):
    """Load the optional framework helper by path, without changing sys.path."""
    path = os.path.join(root, 'routines', 'lib', 'i18n_lookup.py')
    if os.path.isfile(path):
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(getattr(mod, 'merged_bundle', None)):
                return mod
        except Exception:
            pass
    return None


def framework_bundle(root, lang, module_name):
    mod = framework_lookup(root, module_name)
    if mod is not None:
        try:
            data = mod.merged_bundle(root, lang)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
    return None


def legacy_bundle(root, lang, reader=read_flat):
    """Old-framework precedence: service copy over config; reader owns cache."""
    merged = {}
    for path in (os.path.join(root, 'config', 'i18n', '%s.json' % lang),
                 os.path.join(root, 'service', 'kit_life', 'i18n', '%s.json' % lang)):
        merged.update(reader(path))
    return merged
