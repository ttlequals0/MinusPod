"""Reprocessing an episode must not double-count it in episode-scoped
stats, while run-scoped stats (ads, cost, tokens) keep counting every
run (#727).
"""


def _seed_episode(db, slug, episode_id, *, original, new, ads_per_run):
    """Create/update one episode and record one completed run per entry
    in ads_per_run, leaving the episode row at its final duration."""
    db.upsert_episode(slug, episode_id,
                      original_url=f'https://example.com/{episode_id}.mp3',
                      title=episode_id, status='processed',
                      original_duration=original, new_duration=new)
    podcast = db.get_podcast_by_slug(slug)
    for ads in ads_per_run:
        db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug=slug, podcast_title=podcast['title'],
            episode_id=episode_id, episode_title=episode_id, status='completed',
            ads_detected=ads, processing_duration_seconds=5.0,
        )


def test_reprocessed_episode_counts_once_but_runs_stay_scoped(temp_db):
    temp_db.create_podcast('show-a', 'https://example.com/a.xml', 'Show A')
    _seed_episode(temp_db, 'show-a', 'ep1', original=3600, new=3300, ads_per_run=(2, 3))

    stats = temp_db.get_dashboard_stats()
    assert stats['totalEpisodesProcessed'] == 1
    assert stats['totalRuns'] == 2
    assert stats['totalTimeSavedSeconds'] == 300.0
    assert stats['totalAdsRemoved'] == 5


def test_avg_time_saved_times_episodes_with_saving_equals_total(temp_db):
    temp_db.create_podcast('show-b', 'https://example.com/b.xml', 'Show B')
    _seed_episode(temp_db, 'show-b', 'ep1', original=3600, new=3300, ads_per_run=(1,))
    _seed_episode(temp_db, 'show-b', 'ep2', original=1200, new=1000, ads_per_run=(1,))

    stats = temp_db.get_dashboard_stats()
    assert stats['episodesWithTimeSaved'] == 2
    assert round(stats['avgTimeSavedSeconds'] * stats['episodesWithTimeSaved'], 1) == stats['totalTimeSavedSeconds']


def test_episode_with_no_saving_excluded_from_saved_population(temp_db):
    temp_db.create_podcast('show-c', 'https://example.com/c.xml', 'Show C')
    _seed_episode(temp_db, 'show-c', 'ep1', original=1200, new=1200, ads_per_run=(0,))

    stats = temp_db.get_dashboard_stats()
    assert stats['totalEpisodesProcessed'] == 1
    assert stats['episodesWithTimeSaved'] == 0
    assert stats['totalTimeSavedSeconds'] == 0


def test_podcast_slug_filter_narrows_both_populations(temp_db):
    temp_db.create_podcast('show-d', 'https://example.com/d.xml', 'Show D')
    temp_db.create_podcast('show-e', 'https://example.com/e.xml', 'Show E')
    _seed_episode(temp_db, 'show-d', 'ep1', original=3600, new=3300, ads_per_run=(2, 3))
    _seed_episode(temp_db, 'show-e', 'ep1', original=1800, new=1700, ads_per_run=(1,))

    stats = temp_db.get_dashboard_stats(podcast_slug='show-d')
    assert stats['totalEpisodesProcessed'] == 1
    assert stats['totalRuns'] == 2
    assert stats['totalTimeSavedSeconds'] == 300.0
    assert stats['totalAdsRemoved'] == 5


def test_stats_by_podcast_episode_count_distinct_run_count_total(temp_db):
    temp_db.create_podcast('show-f', 'https://example.com/f.xml', 'Show F')
    _seed_episode(temp_db, 'show-f', 'ep1', original=3600, new=3300, ads_per_run=(2, 3))
    _seed_episode(temp_db, 'show-f', 'ep2', original=1800, new=1700, ads_per_run=(1,))

    by_podcast = {p['podcastSlug']: p for p in temp_db.get_stats_by_podcast()}
    row = by_podcast['show-f']
    assert row['episodeCount'] == 2
    assert row['runCount'] == 3


def test_credit_time_saved_dedups_reprocess(temp_db):
    temp_db.create_podcast('show-g', 'https://example.com/g.xml', 'Show G')
    temp_db.upsert_episode('show-g', 'ep1',
                           original_url='https://example.com/ep1.mp3',
                           title='ep1', status='processed',
                           original_duration=3600, new_duration=3300)

    delta1 = temp_db.credit_time_saved('show-g', 'ep1', 300.0)
    assert delta1 == 300.0
    assert temp_db.get_total_time_saved() == 300.0

    # Same saving credited again (e.g. a recut with an identical result) adds nothing.
    delta2 = temp_db.credit_time_saved('show-g', 'ep1', 300.0)
    assert delta2 == 0.0
    assert temp_db.get_total_time_saved() == 300.0

    # A larger saving on a later reprocess adds only the difference.
    delta3 = temp_db.credit_time_saved('show-g', 'ep1', 500.0)
    assert delta3 == 200.0
    assert temp_db.get_total_time_saved() == 500.0

    # Deleting the episode afterwards must not touch the lifetime total.
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM episodes WHERE episode_id = 'ep1'")
    conn.commit()
    assert temp_db.get_total_time_saved() == 500.0
