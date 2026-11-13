"""Test helpers for podping declarations."""
from database.podcasts import podping_declaration_columns


def set_declaration(db, slug, uses_podping, hive_accounts):
    db.update_podcast(slug, **podping_declaration_columns(uses_podping, hive_accounts))
