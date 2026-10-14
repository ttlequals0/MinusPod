from tests.app_bootstrap import bootstrap

bootstrap('search_html_strip_')

import database
from database.search import strip_html

db = database.Database()


def test_strip_html_removes_tags_and_decodes_entities():
    assert strip_html('<p>Hello &amp; <b>world</b></p>\n<br/>next') == 'Hello & world next'


def test_strip_html_handles_none():
    assert strip_html(None) == ''


def _snippet(ep_id):
    return next(e for e in db.search_grouped('politics')['episodes']
                if e['episodeId'] == ep_id)['snippet']


def test_rebuilt_index_holds_plain_text_descriptions():
    slug = 'html-strip-rebuild'
    ep_id = 'a1b2c3d4e5f6'
    db.create_podcast(slug, 'https://example.com/html-strip.xml', 'Example Show')
    db.upsert_episode(slug, ep_id, title='Episode One',
                      description='<p>Politics &amp; more</p>')
    db.rebuild_search_index()
    snippet = _snippet(ep_id)
    assert '<p>' not in snippet and '&lt;' not in snippet
    assert '<mark>' in snippet


def test_incremental_index_holds_plain_text_descriptions():
    slug = 'html-strip-incremental'
    ep_id = 'b1c2d3e4f5a6'
    db.create_podcast(slug, 'https://example.com/html-strip2.xml', 'Example Show Two')
    db.upsert_episode(slug, ep_id, title='Episode Two',
                      description='<div>Politics <i>today</i></div>')
    db.index_episode(ep_id, slug)
    snippet = _snippet(ep_id)
    assert '&lt;' not in snippet
    assert '<mark>' in snippet


PLAIN_TEXT_GATE = 'search_index_plain_text_2_100_12'


def test_plain_text_reindex_runs_once_and_records_gate(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(temp_db, 'rebuild_search_index', lambda: calls.append(1) or 7)
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (PLAIN_TEXT_GATE,))
    conn.commit()
    temp_db._run_reindex_search_plain_text(conn)
    temp_db._run_reindex_search_plain_text(conn)
    assert calls == [1]
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?",
                        (PLAIN_TEXT_GATE,)).fetchone() is not None


def test_plain_text_reindex_skips_rebuild_when_just_populated(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(temp_db, 'rebuild_search_index', lambda: calls.append(1) or 7)
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (PLAIN_TEXT_GATE,))
    conn.commit()
    temp_db._run_reindex_search_plain_text(conn, already_rebuilt=True)
    assert calls == []
