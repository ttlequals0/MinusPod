import pytest
from database import Database
from database.podcasts import is_youtube_feed


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    instance = Database(str(tmp_path))
    yield instance
    Database._instance = None


def test_create_youtube_podcast(db):
    db.create_podcast('yt-show', 'youtube://channel/UCabc', title='YT Show', feed_type='youtube')
    db.update_podcast('yt-show', youtube_source_kind='channel', youtube_source_id='UCabc',
                      yt_process_delay_minutes_override=5, yt_llm_on_top_override=1,
                      yt_captions_override=0, yt_transcribe_override=1)
    row = db.get_podcast_by_slug('yt-show')
    assert is_youtube_feed(row)
    assert row['youtube_source_kind'] == 'channel'
    assert row['yt_process_delay_minutes_override'] == 5
    assert row['yt_llm_on_top_override'] == 1
    assert row['yt_captions_override'] == 0
    assert row['yt_transcribe_override'] == 1
    assert row['youtube_include_shorts'] is None


def test_episode_sponsorblock_columns_roundtrip(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    db.upsert_episode('yt-show', 'a1b2c3d4e5f6', original_url='https://www.youtube.com/watch?v=vid1',
                      title='Ep', status='discovered')
    assert db.update_episode_sponsorblock(
        'yt-show', 'a1b2c3d4e5f6', youtube_video_id='vid1', sb_state='waiting',
        sb_next_check_at='2026-10-29T10:00:00+00:00', sb_attempts=2,
        sb_segment_uuids='["u1"]', sb_note='full')
    ep = db.get_episode('yt-show', 'a1b2c3d4e5f6')
    assert ep['youtube_video_id'] == 'vid1'
    assert ep['sb_state'] == 'waiting'
    assert ep['sb_attempts'] == 2
    assert ep['sb_segment_uuids'] == '["u1"]'
    assert ep['sb_note'] == 'full'


def test_update_episode_sponsorblock_rejects_unknown_column(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    db.upsert_episode('yt-show', 'a1b2c3d4e5f6', original_url='u', status='discovered')
    with pytest.raises(ValueError):
        db.update_episode_sponsorblock('yt-show', 'a1b2c3d4e5f6', status='processed')


def test_due_sponsorblock_episodes_orders_by_next_check(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    for eid, due in (('e1', '2026-10-29T09:00:00+00:00'), ('e2', '2026-10-29T08:00:00+00:00'),
                     ('e3', '2026-10-29T11:00:00+00:00')):
        db.upsert_episode('yt-show', eid, original_url='u', status='discovered')
        db.update_episode_sponsorblock('yt-show', eid, sb_state='waiting', sb_next_check_at=due)
    due = db.get_due_sponsorblock_episodes('2026-10-29T10:00:00+00:00')
    assert [e['episode_id'] for e in due] == ['e2', 'e1']
    assert due[0]['slug'] == 'yt-show'


def test_count_youtube_feeds(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    assert db.count_youtube_feeds() == 1


def test_disarm_sponsorblock_watch_clears_only_an_armed_check(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    db.upsert_episode('yt-show', 'a1b2c3d4e5f6', original_url='https://www.youtube.com/watch?v=vid1')
    db.update_episode_sponsorblock('yt-show', 'a1b2c3d4e5f6', sb_state='watching',
                                   sb_next_check_at='2026-10-29T12:00:00+00:00')
    assert db.disarm_sponsorblock_watch('yt-show', 'a1b2c3d4e5f6') is True
    assert db.get_episode('yt-show', 'a1b2c3d4e5f6')['sb_next_check_at'] is None
    assert db.disarm_sponsorblock_watch('yt-show', 'a1b2c3d4e5f6') is False


def test_yt_metadata_at_column_on_fresh_and_migrated_db(db, tmp_path):
    cols = {r['name'] for r in db.get_connection().execute("PRAGMA table_info(episodes)")}
    assert 'yt_metadata_at' in cols
    conn = db.get_connection()
    conn.execute("ALTER TABLE episodes DROP COLUMN yt_metadata_at")
    conn.commit()
    db._add_column_if_missing(conn, 'episodes', 'yt_metadata_at', 'TEXT', db._get_table_columns(conn, 'episodes'))
    assert 'yt_metadata_at' in db._get_table_columns(conn, 'episodes')


def test_missing_metadata_selection(db):
    db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    db.create_podcast('rss-show', 'https://example.com/feed.xml')
    for slug, eid, desc in (('yt-show', 'e1', ''), ('yt-show', 'e2', 'has one'), ('yt-show', 'e3', None),
                            ('rss-show', 'e4', '')):
        db.upsert_episode(slug, eid, original_url='https://www.youtube.com/watch?v=x', description=desc)
    db.upsert_episode('yt-show', 'e3', yt_metadata_at='2026-10-30T00:00:00Z')
    rows = db.get_youtube_episodes_missing_metadata(10)
    assert sorted(r['episode_id'] for r in rows) == ['e1', 'e2']
    assert rows[0]['slug'] == 'yt-show'


def test_sb_waiting_index_is_created_on_upgrade_without_losing_rows(tmp_path):
    Database._instance = None
    first = Database(str(tmp_path))
    first.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    first.upsert_episode('yt-show', 'a1b2c3d4e5f6', original_url='u', status='discovered')
    first.update_episode_sponsorblock('yt-show', 'a1b2c3d4e5f6', sb_state='waiting')
    first.get_connection().execute("DROP INDEX idx_episodes_sb_waiting")
    first.get_connection().commit()
    Database._instance = None
    upgraded = Database(str(tmp_path))
    names = {r[0] for r in upgraded.get_connection().execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert 'idx_episodes_sb_waiting' in names
    assert upgraded.count_sponsorblock_waiting() == 1
    Database._instance = None
