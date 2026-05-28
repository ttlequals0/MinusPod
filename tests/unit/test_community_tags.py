import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.community_tags import slugify, expected_filename  # noqa: E402


def test_slugify_lowercases_and_hyphenates():
    assert slugify('Shopify') == 'shopify'
    assert slugify('TD Bank') == 'td-bank'
    assert slugify('Capital One') == 'capital-one'


def test_slugify_collapses_punctuation_and_unicode():
    assert slugify('Hims.com') == 'hims-com'
    assert slugify('badcholesterol.com') == 'badcholesterol-com'
    assert slugify('  Spaces   Everywhere  ') == 'spaces-everywhere'


def test_slugify_empty_falls_back_to_sponsor():
    assert slugify('') == 'sponsor'
    assert slugify('!!!') == 'sponsor'


def test_expected_filename_combines_slug_and_short_uuid():
    uuid_s = '07df78ed-9b7f-4600-a9b7-1aee45b5bfc7'
    assert expected_filename('Shopify', uuid_s) == 'shopify-07df78ed.json'


def test_expected_filename_handles_missing_inputs():
    assert expected_filename('', 'aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee') == 'sponsor-aaaa1111.json'
    assert expected_filename('Shopify', '') is None
