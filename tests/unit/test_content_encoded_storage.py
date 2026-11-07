"""content:encoded show notes persist through migration and discovery (#969)."""
from rss_parser import RSSParser
from tests.unit.db_test_utils import _seed

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
 xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Show</title>
<item><title>Ep</title><itunes:summary>Short</itunes:summary>
<content:encoded><![CDATA[<p>Full notes</p>]]></content:encoded><guid>g1</guid>
<enclosure url="https://example.com/1.mp3" type="audio/mpeg"/></item></channel></rss>"""


def _columns(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}


def test_column_added_to_existing_rows_as_null(temp_db):
    _seed(temp_db)
    conn = temp_db.get_connection()
    conn.execute("ALTER TABLE episodes DROP COLUMN content_encoded")
    conn.commit()

    temp_db._run_schema_migrations()

    assert 'content_encoded' in _columns(conn)
    assert conn.execute("SELECT content_encoded FROM episodes").fetchone()[0] is None


def test_extract_episodes_carries_content_encoded():
    episodes = RSSParser().extract_episodes(FEED)
    assert episodes[0]['description'] == 'Short'
    assert episodes[0]['content_encoded'] == '<p>Full notes</p>'


def test_discovery_persists_and_backfills_content_encoded(temp_db):
    temp_db.create_podcast('show', 'https://example.com/feed.xml', 'Show')
    episodes = RSSParser().extract_episodes(FEED)
    notes = episodes[0]['content_encoded']
    episodes[0]['content_encoded'] = ''
    temp_db.bulk_upsert_discovered_episodes('show', episodes)
    assert temp_db.get_episode('show', episodes[0]['id'])['content_encoded'] is None

    episodes[0]['content_encoded'] = notes
    temp_db.bulk_upsert_discovered_episodes('show', episodes)
    assert temp_db.get_episode('show', episodes[0]['id'])['content_encoded'] == notes
    assert temp_db.get_processed_episodes_for_feed(
        temp_db.get_podcast_by_slug('show')['id']) == []
