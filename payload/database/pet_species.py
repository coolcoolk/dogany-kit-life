"""Deterministic species evidence. Pass the owner's words, not model prose.

Unknown breeds stay unknown. Conflicting species evidence asks for clarification
without writing. Negated/correction prose must use pet-species-set explicitly.
"""
import json
from pathlib import Path
import re
import unicodedata


# Korean particles/copulas attach to a noun without a word boundary. Require
# a complete ending so breed names never match arbitrary longer words.
_ENDINGS = ("\uc57c", "\uc774\uc57c", "\uc608\uc694", "\uc774\uc5d0\uc694", "\uc785\ub2c8\ub2e4",
            "\uc774\uace0", "\uace0", "\uc778\ub370", "\uc740", "\ub294", "\uc774", "\uac00",
            "\uc744", "\ub97c", "\uc758", "\ub3c4", "\ub791", "\uc774\ub791")


def species_table():
    return json.loads(Path(__file__).with_suffix(".json").read_text())


def _normalize(words):
    return re.sub(r"[\s-]+", " ", unicodedata.normalize("NFKC", words)).casefold()


def infer_species(words):
    table = species_table()
    words = _normalize(words)
    ending = r"(?:" + "|".join(_ENDINGS) + r")?(?!\w)"
    hits = {species for species, terms in table.items() for term in terms
            if re.search(r"(?<!\w)" + re.escape(_normalize(term)) + ending,
                         words)}
    if len(hits) > 1:
        raise ValueError("conflicting pet species evidence; ask once before saving")
    return next(iter(hits), None)


def is_pet_relation(value):
    return bool(value and value.startswith("pet:") and value[4:].strip()) or value in {"pet_" + species for species in species_table()} or value in ("pet", "cat", "dog",
                     "\ubc18\ub824\uacac", "\ubc18\ub824\ubb18", "\ubc18\ub824\ub3d9\ubb3c",
                     "\uace0\uc591\uc774", "\uac15\uc544\uc9c0")


def stated_relation(species):
    """Explicit owner answer, never an inferred breed or model-written prose.

    Preserve unlisted animals verbatim in the same identity's relation. The
    reserved pet: namespace avoids imposing a cat/dog-only vocabulary.
    """
    species = species.strip()
    if not species or len(species) > 80 or any(ord(c) < 32 for c in species):
        raise ValueError("species must be a nonempty single line of at most 80 characters")
    if species == "unknown":
        return "pet"
    if species in species_table():
        return "pet_" + species
    return "pet:" + species
