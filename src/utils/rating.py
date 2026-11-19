"""Thumbs rating: the API speaks "up"/"down", storage keeps the legacy 1-5 integer column."""

STORED_UP = 5
STORED_DOWN = 1


def parse_rating(value) -> str | None:
    """"up" or "down" from a request value; legacy ints too (5 is up, 1-4 down); None if invalid."""
    if value in ('up', 'down'):
        return value
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        return None
    return rating_label(value)


def stored_rating(label: str) -> int:
    return STORED_UP if label == 'up' else STORED_DOWN


def rating_label(stored) -> str | None:
    """"up" for a stored 5, "down" for any other value, None when unrated."""
    if stored is None:
        return None
    return 'up' if stored == STORED_UP else 'down'
