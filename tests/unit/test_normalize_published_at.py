"""normalize_published_at reads ISO 8601 and RFC 2822, digit-leading or not."""
import pytest

from database.episodes import normalize_published_at


@pytest.mark.parametrize('value,expected', [
    ('Tue, 10 Mar 2026 19:10:06 GMT', '2026-03-10T19:10:06Z'),
    ('10 Mar 2026 19:10:06 GMT', '2026-03-10T19:10:06Z'),
    ('10 Mar 2026 12:10:06 -0700', '2026-03-10T19:10:06Z'),
    ('2026-03-10T21:10:06+02:00', '2026-03-10T19:10:06Z'),
    ('2026-03-10T19:10:06Z', '2026-03-10T19:10:06Z'),
    ('not a date', 'not a date'),
    ('', ''),
    (None, None),
])
def test_normalize(value, expected):
    assert normalize_published_at(value) == expected
