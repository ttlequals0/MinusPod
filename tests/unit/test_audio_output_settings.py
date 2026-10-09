import pytest


def test_audio_output_defaults_and_global_choices(temp_db):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml')
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': True, 'mp3_stream_copy_enabled': False,
    }
    temp_db.set_setting('audio_replacement_sound_enabled', 'false')
    temp_db.set_setting('audio_mp3_stream_copy_enabled', 'true')
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': False, 'mp3_stream_copy_enabled': True,
    }


@pytest.mark.parametrize('feed_type', ['subscribed', 'local'])
def test_audio_output_overrides_and_frozen_feed(temp_db, feed_type):
    source = 'local://example-feed' if feed_type == 'local' else 'https://example.com/feed.xml'
    temp_db.create_podcast('example-feed', source, feed_type=feed_type)
    temp_db.update_podcast('example-feed', audio_replacement_sound_override=False,
                           audio_mp3_stream_copy_override=True)
    frozen = temp_db.get_podcast_by_slug('example-feed')
    temp_db.update_podcast('example-feed', audio_replacement_sound_override=True,
                           audio_mp3_stream_copy_override=False)
    assert temp_db.resolve_audio_output('example-feed', frozen) == {
        'replacement_sound_enabled': False, 'mp3_stream_copy_enabled': True,
    }
    temp_db.update_podcast('example-feed', audio_replacement_sound_override=None,
                           audio_mp3_stream_copy_override=None)
    assert temp_db.resolve_audio_output('example-feed') == {
        'replacement_sound_enabled': True, 'mp3_stream_copy_enabled': False,
    }


def test_audio_output_migration_preserves_populated_data(temp_db):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode('example-feed', 'episode1', title='Saved episode', status='discovered')
    temp_db.set_setting('audio_normalize_enabled', 'true', is_default=False)
    conn = temp_db.get_connection()
    conn.execute('ALTER TABLE podcasts DROP COLUMN audio_replacement_sound_override')
    conn.execute('ALTER TABLE podcasts DROP COLUMN audio_mp3_stream_copy_override')
    conn.commit()
    temp_db._init_schema()
    temp_db._init_schema()
    podcast = temp_db.get_podcast_by_slug('example-feed')
    assert podcast['title'] == 'Example'
    assert podcast['audio_replacement_sound_override'] is None
    assert podcast['audio_mp3_stream_copy_override'] is None
    assert temp_db.get_episode('example-feed', 'episode1')['title'] == 'Saved episode'
    assert temp_db.get_setting('audio_normalize_enabled') == 'true'
