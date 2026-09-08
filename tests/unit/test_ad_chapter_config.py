"""Ad chapter settings: defaults, category map resolution, per-feed overrides."""
import json
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('ad_chapter_config_test_')

from config import (  # noqa: E402
    DEFAULT_AD_CHAPTER_CATEGORIES_JSON, SEGMENT_CATEGORIES,
    resolve_ad_chapter_categories_map, resolve_ad_chapters_enabled,
    valid_ad_chapter_title_format,
)
from database.settings import SETTINGS_REGISTRY, registry_default  # noqa: E402


def test_registry_defaults_are_off_and_sponsor_cross_promo():
    assert registry_default('ad_chapters_enabled') == 'false'
    assert registry_default('ad_chapters_include_held') == 'false'
    assert registry_default('ad_chapter_title_format') == 'Ad: {label}'
    assert registry_default('ad_chapter_held_title_format') == 'Possible ad: {label}'
    assert registry_default('ad_chapter_resume_title') == 'Show'
    assert registry_default('ad_chapter_min_confidence') == '0.9'
    assert json.loads(registry_default('ad_chapter_categories')) == {
        'sponsor': True, 'cross_promo': True, 'self_promo': False,
        'interaction': False, 'intro': False, 'outro': False, 'recap': False}
    for key in ('ad_chapters_enabled', 'ad_chapter_categories',
                'ad_chapters_include_held', 'ad_chapter_title_format',
                'ad_chapter_held_title_format', 'ad_chapter_resume_title',
                'ad_chapter_min_confidence'):
        assert SETTINGS_REGISTRY[key].in_ad_reset
        assert SETTINGS_REGISTRY[key].seeded


def test_categories_map_fills_every_category_and_ignores_junk():
    full = resolve_ad_chapter_categories_map(None)
    assert set(full) == set(SEGMENT_CATEGORIES)
    assert full['sponsor'] is True and full['recap'] is False
    merged = resolve_ad_chapter_categories_map(
        json.dumps({'recap': True, 'bogus': True, 'sponsor': 'yes'}))
    assert merged['recap'] is True
    assert merged['sponsor'] is True  # non-bool ignored, default kept
    assert 'bogus' not in merged
    assert resolve_ad_chapter_categories_map('not json') == full
    layered = resolve_ad_chapter_categories_map(
        json.dumps({'sponsor': False}), baseline=full)
    assert layered['sponsor'] is False and layered['cross_promo'] is True


def test_default_json_matches_resolver():
    assert json.loads(DEFAULT_AD_CHAPTER_CATEGORIES_JSON) == resolve_ad_chapter_categories_map(None)


def test_enabled_follows_override_then_global():
    db = MagicMock()
    db.get_setting_bool.return_value = True
    assert resolve_ad_chapters_enabled(db, {'ad_chapters_enabled_override': 'off'}) is False
    assert resolve_ad_chapters_enabled(db, {'ad_chapters_enabled_override': 'on'}) is True
    assert resolve_ad_chapters_enabled(db, {'ad_chapters_enabled_override': None}) is True
    assert resolve_ad_chapters_enabled(db, None) is True
    db.get_setting_bool.return_value = False
    assert resolve_ad_chapters_enabled(db, {}) is False
    db.get_setting_bool.assert_called_with('ad_chapters_enabled', False)


def test_title_format_validator():
    assert valid_ad_chapter_title_format('[mp:{category}]')
    assert valid_ad_chapter_title_format('Ad break')
    assert valid_ad_chapter_title_format('Held: {label}')
    assert valid_ad_chapter_title_format('{category} / {category}')
    assert not valid_ad_chapter_title_format('{category')
    assert not valid_ad_chapter_title_format('{nope}')
    assert not valid_ad_chapter_title_format('')
    assert not valid_ad_chapter_title_format(None)


@pytest.mark.parametrize('value', [
    '{category.foo}',       # AttributeError out of str.format
    '{category[foo]}',      # TypeError out of str.format
    '{category.__class__}',  # formats, but renders junk
    '{}',                   # positional
    '{0}',                  # positional
    '{category!r}',         # conversion
    '{category:>10}',       # format spec
])
def test_title_format_validator_rejects_field_access(value):
    assert not valid_ad_chapter_title_format(value)


def test_per_feed_categories_layer_over_global(temp_db):
    db = temp_db
    db.set_setting('ad_chapter_categories', json.dumps({'recap': True}), is_default=False)
    db.create_podcast('example-podcast', 'https://example.com/feed', 'Example')
    db.update_podcast('example-podcast',
                      ad_chapter_categories_override=json.dumps({'sponsor': False}))
    resolved = db.resolve_ad_chapter_categories('example-podcast')
    assert resolved['recap'] is True
    assert resolved['sponsor'] is False
    assert resolved['cross_promo'] is True
    row = db.get_podcast_by_slug('example-podcast')
    assert row['ad_chapter_categories_override'] == json.dumps({'sponsor': False})
    db.update_podcast('example-podcast', ad_chapters_enabled_override='on')
    assert db.get_podcast_by_slug('example-podcast')['ad_chapters_enabled_override'] == 'on'
