def _grouped_counts(db):
    counts = {
        row['content_type']: row['count']
        for row in db.get_connection().execute(
            'SELECT content_type, COUNT(*) AS count FROM search_index GROUP BY content_type'
        )
    }
    counts['total'] = sum(counts.values())
    return counts


def test_search_index_stats_match_grouped_counts_after_index_changes(temp_db):
    conn = temp_db.get_connection()
    assert temp_db.get_search_index_stats() == {'total': 0}

    slug = 'stats-content-type-terms'
    temp_db.create_podcast(
        slug,
        'https://example.com/stats-content-type-terms.xml',
        'Podcast episode pattern sponsor',
    )
    episode_id = 'stats-content-type-episode'
    temp_db.upsert_episode(
        slug,
        episode_id,
        original_url='https://example.com/stats-content-type-episode.mp3',
        title='Podcast episode pattern sponsor',
        description='Podcast episode pattern sponsor',
    )
    temp_db.save_episode_details(
        slug, episode_id, transcript_text='Podcast episode pattern sponsor'
    )
    sponsor_id = temp_db.create_known_sponsor('Stats Terms Sponsor')
    temp_db.create_ad_pattern(
        'global', text_template='Podcast episode pattern sponsor', sponsor_id=sponsor_id
    )

    temp_db.index_episode(episode_id, slug)
    temp_db.rebuild_search_index()
    assert temp_db.get_connection() is conn
    assert temp_db.get_search_index_stats() == _grouped_counts(temp_db)
    assert set(temp_db.get_search_index_stats()) == {
        'podcast', 'episode', 'pattern', 'sponsor', 'total'
    }

    temp_db.upsert_episode(
        slug,
        episode_id,
        original_url='https://example.com/stats-content-type-episode.mp3',
        title='Updated podcast episode pattern sponsor',
        description='Updated podcast episode pattern sponsor',
    )
    temp_db.index_episode(episode_id, slug)
    assert temp_db.get_search_index_stats() == _grouped_counts(temp_db)

    conn.execute(
        "DELETE FROM search_index WHERE content_type = 'episode' AND content_id = ?",
        (episode_id,),
    )
    conn.commit()
    assert temp_db.get_search_index_stats() == _grouped_counts(temp_db)
