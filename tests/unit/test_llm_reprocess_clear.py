"""Transcript-preserving clears used by LLM-only reprocess (issue #349).

``clear_episode_ad_data`` and ``batch_clear_episode_ad_data`` must null the
ad-detection outputs and regenerated assets while PRESERVING the transcript
inputs, so a reprocess reuses the saved transcript instead of paying the
re-transcription cost. Unlike ``clear_episode_details`` they must never delete
the row or the transcript.
"""


def _seed_details(db, slug, episode_id):
    db.save_episode_details(
        slug, episode_id,
        transcript_text='full transcript text',
        transcript_vtt='WEBVTT\n\n00:00.000 --> 00:05.000\nhi',
        chapters_json='[{"title": "Intro"}]',
        ad_markers=[{'start': 1.0, 'end': 2.0}],
        first_pass_prompt='p1', first_pass_response='r1',
        second_pass_prompt='p2', second_pass_response='r2',
    )
    db.save_original_transcript(slug, episode_id, 'full transcript text')
    db.save_original_segments(slug, episode_id, [{'start': 0.0, 'end': 1.0, 'text': 'hi'}])
    # final_segments_json is written deep in the pipeline; set it directly so the
    # clear has something to null.
    db_id = db._get_episode_db_id(slug, episode_id)
    conn = db.get_connection()
    conn.execute(
        "UPDATE episode_details SET final_segments_json = ? WHERE episode_id = ?",
        ('[{"start": 0.0}]', db_id),
    )
    conn.commit()


def _details_row(db, slug, episode_id):
    db_id = db._get_episode_db_id(slug, episode_id)
    conn = db.get_connection()
    return conn.execute(
        "SELECT * FROM episode_details WHERE episode_id = ?", (db_id,)
    ).fetchone()


def _assert_cleared_but_transcript_kept(row):
    # Preserved transcript inputs
    assert row['transcript_text'] == 'full transcript text'
    assert row['original_transcript_text'] == 'full transcript text'
    assert row['original_segments_json'] is not None
    # Cleared ad-detection outputs and regenerated assets
    assert row['ad_markers_json'] is None
    assert row['transcript_vtt'] is None
    assert row['chapters_json'] is None
    assert row['first_pass_prompt'] is None
    assert row['first_pass_response'] is None
    assert row['second_pass_prompt'] is None
    assert row['second_pass_response'] is None
    assert row['final_segments_json'] is None


def test_clear_episode_ad_data_preserves_transcript(temp_db, mock_podcast, mock_episode):
    slug = mock_podcast['slug']
    episode_id = mock_episode['episode_id']
    _seed_details(temp_db, slug, episode_id)

    temp_db.clear_episode_ad_data(slug, episode_id)

    _assert_cleared_but_transcript_kept(_details_row(temp_db, slug, episode_id))


def test_batch_clear_episode_ad_data_preserves_transcript(temp_db, mock_podcast, mock_episode):
    slug = mock_podcast['slug']
    episode_id = mock_episode['episode_id']
    _seed_details(temp_db, slug, episode_id)

    temp_db.batch_clear_episode_ad_data(slug, [episode_id])

    _assert_cleared_but_transcript_kept(_details_row(temp_db, slug, episode_id))


def test_clear_episode_details_still_deletes_everything(temp_db, mock_podcast, mock_episode):
    # Control: the full clear used by reprocess/full modes removes the row
    # entirely, including the transcript.
    slug = mock_podcast['slug']
    episode_id = mock_episode['episode_id']
    _seed_details(temp_db, slug, episode_id)

    temp_db.clear_episode_details(slug, episode_id)

    assert _details_row(temp_db, slug, episode_id) is None


def test_clear_episode_ad_data_noop_without_details(temp_db, mock_podcast, mock_episode):
    # No episode_details row yet: must be a safe no-op, not an error.
    temp_db.clear_episode_ad_data(mock_podcast['slug'], mock_episode['episode_id'])
    temp_db.batch_clear_episode_ad_data(mock_podcast['slug'], [mock_episode['episode_id']])


def test_has_transcript_gates_llm_mode(temp_db, mock_podcast, mock_episode):
    # The cheap existence check that gates LLM-only reprocess.
    slug = mock_podcast['slug']
    episode_id = mock_episode['episode_id']

    assert temp_db.has_transcript(slug, episode_id) is False
    # Empty-string transcript counts as no transcript, matching
    # storage.get_transcript's truthiness semantics.
    temp_db.save_episode_details(slug, episode_id, transcript_text='')
    assert temp_db.has_transcript(slug, episode_id) is False
    temp_db.save_episode_details(slug, episode_id, transcript_text='full transcript text')
    assert temp_db.has_transcript(slug, episode_id) is True
    # Unknown episode is False, not an error.
    assert temp_db.has_transcript(slug, 'does-not-exist') is False
