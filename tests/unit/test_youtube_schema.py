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
                      yt_captions_override=0)
    row = db.get_podcast_by_slug('yt-show')
    assert is_youtube_feed(row)
    assert row['youtube_source_kind'] == 'channel'
    assert row['yt_process_delay_minutes_override'] == 5
    assert row['yt_llm_on_top_override'] == 1
    assert row['yt_captions_override'] == 0
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


def test_youtube_feed_rss_rows_are_processed_only(db):
    pid = db.create_podcast('yt-show', 'youtube://playlist/PL1', feed_type='youtube')
    db.upsert_episode('yt-show', 'done1', original_url='u', status='processed',
                      processed_file='x.mp3', published_at='2026-10-01T00:00:00Z')
    db.upsert_episode('yt-show', 'wait1', original_url='u', status='discovered')
    rows = db.get_youtube_feed_episodes_for_rss(pid, None)
    assert [r['episode_id'] for r in rows] == ['done1']
    assert db.count_youtube_feeds() == 1
