"""Existing podcasts rows gain title_include_patterns as NULL without data loss."""
from tests.unit.db_test_utils import assert_column_migrated


def test_column_added_to_existing_rows_as_null(temp_db):
    assert_column_migrated(temp_db, 'podcasts', 'title_include_patterns')
