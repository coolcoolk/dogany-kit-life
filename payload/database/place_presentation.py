"""Pure place-display projections shared by read-only renderers."""


def clean(value):
    return ' '.join(str(value or '').split())


def split_legacy_location(value):
    """Split the legacy combined label/address using the canonical parser."""
    from place_location import split_location
    label, _room, address = split_location(value)
    return label, address


def detail(stored_name, fallback_name, stored_address, alternate_address=None):
    """Apply appointment's existing name/address priority, without I/O."""
    name = clean(stored_name) or clean(fallback_name)
    address = clean(alternate_address) or clean(stored_address)
    name, embedded = split_legacy_location(name)
    return name, address or embedded
