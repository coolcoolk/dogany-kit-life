"""Pure clock conversion; callers own accepted formats, zones and errors."""
import datetime


def utc_datetime(value, fmt):
    """Parse exactly through strptime, then attach UTC without conversion."""
    return datetime.datetime.strptime(value, fmt).replace(tzinfo=datetime.timezone.utc)


def local_datetime(value, tz):
    """Convert an aware instant to the caller's explicit tzinfo."""
    return value.astimezone(tz)
