"""Helpers to build and apply cleanup suggestions in the audio integration tests."""
from tests.integration.fingerprint_helpers import FIRST_READ, SECOND_TEMPLATE
import pattern_cleanup  # noqa: E402  must follow the helpers, which bootstrap the data dir

OLD = b'AQAAold'
PIECES = [{'text': FIRST_READ, 'sponsor': 'Acme'}, {'text': SECOND_TEMPLATE, 'sponsor': 'Beta Corp'}]


def suggest(db, pid, kind, payload):
    before = pattern_cleanup._before_snapshot(db.get_ad_pattern_by_id(pid))
    return db.upsert_cleanup_suggestion(None, pid, kind, 0.9, ['r'], payload, before)


def source_span(db, pid):
    """(slug, episode_id, start, end) the pattern's fingerprint was built from, None without one."""
    row = db.get_audio_fingerprint(pid, with_blob=False)
    return row and (row['source_podcast_slug'], row['source_episode_id'], row['source_start'], row['source_end'])


def trim(db, pid, text=SECOND_TEMPLATE):
    sid = suggest(db, pid, 'trim', {'text': text})
    return sid, pattern_cleanup.apply_suggestion(db, sid)


def split(db, pid):
    sid = suggest(db, pid, 'split', {'pieces': PIECES})
    result = pattern_cleanup.apply_suggestion(db, sid)
    return sid, result, result['applied']['new_pattern_ids']
