"""Ad chapter settings: defaults and title format validation.

ad_chapters_enabled/ad_chapter_categories are retired; see test_mark_action_migration.py.
"""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('ad_chapter_config_test_')

from config import valid_ad_chapter_title_format  # noqa: E402
from database.settings import SETTINGS_REGISTRY, registry_default  # noqa: E402


def test_registry_defaults_for_remaining_ad_chapter_settings():
    assert registry_default('ad_chapters_include_held') == 'false'
    assert registry_default('ad_chapter_title_format') == 'Ad: {label}'
    assert registry_default('ad_chapter_held_title_format') == 'Possible ad: {label}'
    assert registry_default('ad_chapter_resume_title') == 'Show'
    assert registry_default('ad_chapter_min_confidence') == '0.9'
    for key in ('ad_chapters_include_held', 'ad_chapter_title_format',
                'ad_chapter_held_title_format', 'ad_chapter_resume_title',
                'ad_chapter_min_confidence'):
        assert SETTINGS_REGISTRY[key].in_ad_reset
        assert SETTINGS_REGISTRY[key].seeded
    # ad_chapters_enabled / ad_chapter_categories have no registry entry at
    # all: src/api derives their API fields from segment_category_actions.
    assert 'ad_chapters_enabled' not in SETTINGS_REGISTRY
    assert 'ad_chapter_categories' not in SETTINGS_REGISTRY


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


def test_old_machine_form_defaults_are_migrated_once(temp_db):
    conn = temp_db.get_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value, is_default) "
                 "VALUES ('ad_chapter_title_format', '[mp:{category}]', 1)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value, is_default) "
                 "VALUES ('ad_chapter_held_title_format', '[mp:{category}?]', 0)")
    conn.execute("DELETE FROM schema_migrations WHERE name = 'ad_chapter_title_defaults_2969'")
    conn.commit()
    temp_db._run_ad_chapter_title_defaults(conn)
    assert temp_db.get_setting('ad_chapter_title_format') == 'Ad: {label}'
    assert temp_db.get_setting('ad_chapter_held_title_format') == '[mp:{category}?]'
    conn.execute("UPDATE settings SET value = '[mp:{category}]' WHERE key = 'ad_chapter_title_format'")
    conn.commit()
    temp_db._run_ad_chapter_title_defaults(conn)
    assert temp_db.get_setting('ad_chapter_title_format') == '[mp:{category}]'
