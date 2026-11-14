"""Deleting a pattern's audio is one transaction."""
import pytest
from tests.fingerprint_match_helpers import record_fingerprint_match


@pytest.fixture
def audio_pattern(temp_db):
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id='show', text_template='acme read ' * 12)
    temp_db.create_audio_fingerprint(pid, b'AQAA', 6.0)
    record_fingerprint_match(temp_db, pid, 'ep1', 'show', 10.0, 40.0, 0.9, 0.9, True, False)
    return pid


def test_removes_fingerprint_and_history_and_sets_the_flag(temp_db, audio_pattern):
    assert temp_db.delete_pattern_audio(audio_pattern) is True
    assert temp_db.get_audio_fingerprint(audio_pattern) is None
    assert temp_db.count_fingerprint_matches(audio_pattern) == 0
    assert temp_db.get_ad_pattern_by_id(audio_pattern)['audio_removed'] == 1


def test_pattern_without_audio_changes_nothing(temp_db):
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id='show', text_template='acme read ' * 12)
    assert temp_db.delete_pattern_audio(pid) is False
    assert temp_db.get_ad_pattern_by_id(pid)['audio_removed'] == 0


def test_failure_rolls_everything_back(temp_db, audio_pattern):
    conn = temp_db.get_connection()
    conn.execute("CREATE TRIGGER block_flag BEFORE UPDATE OF audio_removed ON ad_patterns "
                 "BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    conn.commit()
    with pytest.raises(Exception, match='blocked'):
        temp_db.delete_pattern_audio(audio_pattern)
    assert temp_db.get_audio_fingerprint(audio_pattern) is not None
    assert temp_db.count_fingerprint_matches(audio_pattern) == 1
    assert temp_db.get_ad_pattern_by_id(audio_pattern)['audio_removed'] == 0
