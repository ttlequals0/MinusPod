"""Tests for `sponsor_normalize.get_or_create_known_sponsor`."""
import pytest

from sponsor_normalize import get_or_create_known_sponsor


# --- Non-string and empty input rejection --------------------------------

@pytest.mark.parametrize('bad', [None, 0, 1, 1.5, [], {}, ('x',), b'bytes', True])
def test_non_string_returns_none(temp_db, bad):
    assert get_or_create_known_sponsor(temp_db, bad) is None


@pytest.mark.parametrize('blank', ['', ' ', '\t', '\n', '   \t \n  ', '"', ',', '...', '"  ,  "'])
def test_blank_or_punct_only_returns_none(temp_db, blank):
    assert get_or_create_known_sponsor(temp_db, blank) is None


# --- Control-char rejection ----------------------------------------------

@pytest.mark.parametrize('payload', [
    'Acme\x00Sponsor',
    'Acme\x07Sponsor',
    'Acme\x1FSponsor',
    'Acme\x7FSponsor',
    '\x00Acme',
    'Acme\x00',
])
def test_control_chars_rejected(temp_db, payload):
    assert get_or_create_known_sponsor(temp_db, payload) is None


# --- Length cap ----------------------------------------------------------

def test_length_cap_at_100_chars_accepted(temp_db):
    name = 'A' * 100
    sid = get_or_create_known_sponsor(temp_db, name)
    assert sid is not None
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == name


def test_over_100_chars_rejected(temp_db):
    assert get_or_create_known_sponsor(temp_db, 'A' * 101) is None


def test_long_after_strip_still_rejected(temp_db):
    payload = '   "' + 'A' * 101 + '"   '
    assert get_or_create_known_sponsor(temp_db, payload) is None


# --- Sanitization shapes -------------------------------------------------

def test_plain_name_creates_row(temp_db):
    sid = get_or_create_known_sponsor(temp_db, 'Squarespace')
    assert sid is not None
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == 'Squarespace'


def test_surrounding_whitespace_stripped(temp_db):
    sid = get_or_create_known_sponsor(temp_db, '   Squarespace   ')
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == 'Squarespace'


@pytest.mark.parametrize('payload,expected', [
    ('"Squarespace"', 'Squarespace'),
    ("'Squarespace'", 'Squarespace'),
    ('`Squarespace`', 'Squarespace'),
    ('Squarespace.', 'Squarespace'),
    ('Squarespace,', 'Squarespace'),
    ('Squarespace!', 'Squarespace'),
    ('-Squarespace-', 'Squarespace'),
    ('"  Squarespace,  "', 'Squarespace'),
    ('...Squarespace...', 'Squarespace'),
])
def test_outer_quotes_and_punct_stripped(temp_db, payload, expected):
    sid = get_or_create_known_sponsor(temp_db, payload)
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == expected


def test_internal_whitespace_collapsed(temp_db):
    sid = get_or_create_known_sponsor(temp_db, 'Better    Help')
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == 'Better Help'


def test_mixed_internal_whitespace_collapsed(temp_db):
    sid = get_or_create_known_sponsor(temp_db, 'Better\t \n  Help')
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == 'Better Help'


def test_internal_punctuation_preserved(temp_db):
    sid = get_or_create_known_sponsor(temp_db, 'Coca-Cola')
    row = temp_db.get_known_sponsor_by_id(sid)
    assert row['name'] == 'Coca-Cola'


# --- Case-insensitive resolution -----------------------------------------

def test_case_insensitive_returns_existing_id(temp_db):
    first = get_or_create_known_sponsor(temp_db, 'Squarespace')
    second = get_or_create_known_sponsor(temp_db, 'squarespace')
    third = get_or_create_known_sponsor(temp_db, 'SQUARESPACE')
    assert first == second == third


def test_no_duplicate_rows_on_case_variants(temp_db):
    get_or_create_known_sponsor(temp_db, 'Squarespace')
    get_or_create_known_sponsor(temp_db, 'squarespace')
    get_or_create_known_sponsor(temp_db, 'SQUARESPACE')
    rows = [r for r in temp_db.get_known_sponsors() if r['name'].lower() == 'squarespace']
    assert len(rows) == 1


def test_sanitization_then_match(temp_db):
    first = get_or_create_known_sponsor(temp_db, 'Squarespace')
    second = get_or_create_known_sponsor(temp_db, '  "squarespace,"  ')
    assert first == second


# --- Distinct names create distinct rows ---------------------------------

def test_distinct_names_create_distinct_rows(temp_db):
    a = get_or_create_known_sponsor(temp_db, 'BetterHelp')
    b = get_or_create_known_sponsor(temp_db, 'Squarespace')
    assert a != b
    assert temp_db.get_known_sponsor_by_id(a)['name'] == 'BetterHelp'
    assert temp_db.get_known_sponsor_by_id(b)['name'] == 'Squarespace'
