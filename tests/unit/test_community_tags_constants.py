"""Tests for utils.community_tags loaders and PII helpers."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.community_tags import (  # noqa: E402
    UNIVERSAL_TAG,
    CONSUMER_EMAIL_DOMAINS,
    is_tollfree,
    map_itunes_category,
    sponsor_seed,
    valid_tags,
)


def test_valid_tags_includes_universal_and_vocab():
    vt = valid_tags()
    assert UNIVERSAL_TAG in vt
    # spot-check vocabulary entries
    assert 'tech' in vt
    assert 'mental_health' in vt
    assert 'true_crime' in vt
    assert 'gambling' in vt


def test_sponsor_seed_rows_have_name_aliases_and_tags():
    seed = sponsor_seed()
    assert seed
    for row in seed:
        assert {'name', 'aliases', 'tags'} <= row.keys()
        assert isinstance(row['aliases'], list)
        assert isinstance(row['tags'], list)


def test_sponsor_seed_universal_marker_present():
    seed = sponsor_seed()
    universal_count = sum(1 for s in seed if UNIVERSAL_TAG in s['tags'])
    # Plan promises many sponsors carry universal; sanity check at least 10.
    assert universal_count >= 10


def test_itunes_category_map_basic():
    assert map_itunes_category('Technology') == 'technology'
    assert map_itunes_category('technology') == 'technology'
    assert map_itunes_category('True Crime') == 'true_crime'
    assert map_itunes_category('Health & Fitness') == 'health'
    assert map_itunes_category('Mental Health') == 'mental_health'
    assert map_itunes_category('Comedy Interviews') == 'comedy'
    # Unknown returns None.
    assert map_itunes_category('Knitting Club Reviews') is None
    assert map_itunes_category('') is None
    assert map_itunes_category(None) is None  # type: ignore[arg-type]


def test_consumer_email_domains_complete():
    expected = {
        'gmail.com', 'yahoo.com', 'aol.com', 'hotmail.com', 'outlook.com',
        'icloud.com', 'me.com', 'mac.com', 'protonmail.com', 'proton.me',
    }
    assert expected.issubset(CONSUMER_EMAIL_DOMAINS)


def test_is_tollfree_classification():
    # NANP toll-free numbers
    assert is_tollfree('1-800-555-1234') is True
    assert is_tollfree('(866) 555-1234') is True
    assert is_tollfree('877-555-1234') is True
    # Not toll-free
    assert is_tollfree('212-555-1234') is False
    assert is_tollfree('+1-415-555-1234') is False
    # UK toll-free
    assert is_tollfree('0800-555-1234') is True
    # AU toll-free
    assert is_tollfree('1800-555-1234') is True
