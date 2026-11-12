"""Playable source selection for a pattern's audio."""
from tests.app_bootstrap import bootstrap

bootstrap('resolve_audio_source_test_')

import pytest  # noqa: E402
from flask import Flask  # noqa: E402

from api.patterns_audio import resolve_audio_source  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match

SLUG = 'example-podcast'


class _Storage:
    def __init__(self, root):
        self.root = root

    def get_original_path(self, slug, episode_id, extension='.mp3'):
        return self.root / f'{slug}-{episode_id}-original{extension}'


@pytest.fixture
def app_context():
    with Flask(__name__).app_context():
        yield


@pytest.fixture
def world(temp_db, tmp_path):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example Podcast')
    storage = _Storage(tmp_path)
    pid = temp_db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='acme read ' * 12)

    def episode(eid, retained):
        temp_db.upsert_episode(SLUG, eid, original_url='https://example.com/e.mp3', title=eid,
                               status='processed')
        if retained:
            storage.get_original_path(SLUG, eid).write_bytes(b'ID3')
            temp_db.upsert_episode(SLUG, eid, original_file='x.mp3')

    return temp_db, storage, pid, episode


def _fingerprint(db, pid, episode_id='built1'):
    db.create_audio_fingerprint(pid, b'AQAA', 6.0, source_podcast_slug=SLUG,
                                source_episode_id=episode_id, source_start=2.0, source_end=8.0)


def _code(err):
    return err.get_json()['code'], err.status_code


def test_newest_retained_match_wins(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', True)
    episode('old', True)
    episode('new', True)
    record_fingerprint_match(db, pid, 'old', SLUG, 10.0, 40.0, 0.9, 0.9, True, False)
    record_fingerprint_match(db, pid, 'new', SLUG, 20.0, 50.0, 0.9, 0.9, True, False)
    source, err = resolve_audio_source(db, storage, pid)
    assert err is None
    assert {k: v for k, v in source.items() if k != 'path'} == {
        'podcastSlug': SLUG, 'episodeId': 'new', 'start': 20.0, 'end': 50.0}
    assert source['path'] == storage.get_original_path(SLUG, 'new')


def test_skips_matches_whose_original_is_gone_then_uses_the_build_source(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', True)
    episode('gone', False)
    record_fingerprint_match(db, pid, 'gone', SLUG, 10.0, 40.0, 0.9, 0.9, True, False)
    source, err = resolve_audio_source(db, storage, pid)
    assert err is None and (source['episodeId'], source['start'], source['end']) == ('built1', 2.0, 8.0)


def test_flag_set_but_file_missing_is_not_retained(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', True)
    storage.get_original_path(SLUG, 'built1').unlink()
    source, err = resolve_audio_source(db, storage, pid)
    assert source is None and _code(err) == ('original_not_retained', 404)


def test_nothing_retained(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', False)
    source, err = resolve_audio_source(db, storage, pid)
    assert source is None and _code(err) == ('original_not_retained', 404)


def test_rejected_matches_are_not_a_source(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', False)
    episode('bad', True)
    record_fingerprint_match(db, pid, 'bad', SLUG, 10.0, 40.0, 0.9, 0.9, True, False)
    assert db.mark_fingerprint_match_rejected(pid, SLUG, 'bad', 10.0, 40.0)
    source, err = resolve_audio_source(db, storage, pid)
    assert source is None and _code(err) == ('original_not_retained', 404)


def test_given_match_is_used_and_must_belong_to_the_pattern(world, app_context):
    db, storage, pid, episode = world
    other = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='other read ' * 12)
    _fingerprint(db, pid)
    episode('built1', True)
    episode('old', True)
    episode('new', True)
    old = record_fingerprint_match(db, pid, 'old', SLUG, 10.0, 40.0, 0.9, 0.9, True, False)
    record_fingerprint_match(db, pid, 'new', SLUG, 20.0, 50.0, 0.9, 0.9, True, False)
    foreign = record_fingerprint_match(db, other, 'new', SLUG, 20.0, 50.0, 0.9, 0.9, True, False)
    source, err = resolve_audio_source(db, storage, pid, old)
    assert err is None and source['episodeId'] == 'old'
    for bad in (foreign, 9999):
        source, err = resolve_audio_source(db, storage, pid, bad)
        assert source is None and err.status_code == 404


def test_given_match_without_original_does_not_fall_back(world, app_context):
    db, storage, pid, episode = world
    _fingerprint(db, pid)
    episode('built1', True)
    episode('gone', False)
    gone = record_fingerprint_match(db, pid, 'gone', SLUG, 10.0, 40.0, 0.9, 0.9, True, False)
    source, err = resolve_audio_source(db, storage, pid, gone)
    assert source is None and _code(err) == ('original_not_retained', 404)


def test_pattern_without_audio(world, app_context):
    db, storage, pid, _ = world
    source, err = resolve_audio_source(db, storage, pid)
    assert source is None and _code(err) == ('no_audio', 404)


def test_fingerprint_without_a_recorded_source_and_no_matches(world, app_context):
    db, storage, pid, _ = world
    db.create_audio_fingerprint(pid, b'AQAA', 6.0)
    source, err = resolve_audio_source(db, storage, pid)
    assert source is None and _code(err) == ('original_not_retained', 404)
