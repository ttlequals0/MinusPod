import pytest

from ad_yield import latest_completed_run


def test_latest_completed_run_skips_chapter_only_runs():
    audio_run = {'status': 'completed', 'stats': {'mode': 'reprocess'}}
    chapter_run = {'status': 'completed', 'stats': {'mode': 'chapters'}}

    assert latest_completed_run([audio_run, chapter_run]) is audio_run


@pytest.mark.parametrize('legacy_stats', [None, '{malformed'])
def test_history_selectors_keep_legacy_rows_eligible(temp_db, legacy_stats):
    temp_db.create_podcast('chapter-history', 'https://example.com/feed.xml', 'History')
    podcast = temp_db.get_podcast_by_slug('chapter-history')
    episode_id = 'episode-legacy'
    audio_id = temp_db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug='chapter-history',
        podcast_title='History', episode_id=episode_id,
        episode_title='Audio run', status='completed',
        input_tokens=10, output_tokens=2, llm_cost=0.1,
        processing_stats={'mode': 'reprocess'},
    )
    chapter_id = temp_db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug='chapter-history',
        podcast_title='History', episode_id=episode_id,
        episode_title='Chapter run', status='completed',
        input_tokens=50, output_tokens=9, llm_cost=0.4,
        processing_stats={'mode': 'chapters'},
    )
    legacy_id = temp_db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug='chapter-history',
        podcast_title='History', episode_id=episode_id,
        episode_title='Legacy run', status='completed',
        input_tokens=4, output_tokens=1, llm_cost=0.05,
    )
    conn = temp_db.get_connection()
    conn.execute(
        "UPDATE processing_history SET processing_stats_json = ? WHERE id = ?",
        (legacy_stats, legacy_id))
    conn.execute(
        "UPDATE processing_history SET processed_at = CASE id "
        "WHEN ? THEN '2026-10-08T00:00:01Z' "
        "WHEN ? THEN '2026-10-08T00:00:02Z' "
        "WHEN ? THEN '2026-10-08T00:00:03Z' END "
        "WHERE id IN (?, ?, ?)",
        (audio_id, chapter_id, legacy_id, audio_id, chapter_id, legacy_id))
    conn.commit()

    assert temp_db.get_latest_completed_processing()['episode_title'] == 'Legacy run'
    assert temp_db.increment_episode_token_usage(
        podcast['id'], episode_id, 3, 2, 0.07)

    rows = {row['id']: row for row in temp_db.get_episode_processing_runs(
        podcast['id'], episode_id)}
    assert rows[legacy_id]['input_tokens'] == 7
    assert rows[legacy_id]['output_tokens'] == 3
    assert rows[chapter_id]['input_tokens'] == 50
    assert rows[audio_id]['input_tokens'] == 10
