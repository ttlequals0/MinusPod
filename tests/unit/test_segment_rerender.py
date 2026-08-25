"""Tests for recut re-resolution against current segment-category action
maps, and the feed-wide re-render endpoint (issue #565).

Part A drives _recut_episode directly (audio processor mocked, no ffmpeg)
to assert marker-level cut/keep outcomes when the action map has changed
since the marker was last processed: a previously-cut marker whose category
now resolves 'keep' must not cut even after a force-accepted user approval
(no timestamp to compare, so keep wins unconditionally); a previously-kept
marker can now cut if its category resolves 'remove'/'beep'; action_applied
is restamped from the current map, not the stale stored value; an
all-remove map recuts unchanged.

Part B drives POST /feeds/<slug>/rerender-segments through the real Flask
app (start_background_processing mocked, no real processing thread starts):
only processed episodes with a retained original, saved segments, and ad
detections are queued; the response reports {queued, skipped}; CSRF is
enforced like sibling POST endpoints.
"""
import time
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'segment_rerender_test_', passphrase='segment-rerender-test-passphrase',
    reset_storage=True)

import main_app.processing as processing
from config import SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION
from werkzeug.security import generate_password_hash

ALL_REMOVE = {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}


# ---------------------------------------------------------------------------
# Part A: _recut_episode re-resolution
# ---------------------------------------------------------------------------

def _marker(start, end, category, action_applied, was_cut, **overrides):
    m = {'start': start, 'end': end, 'category': category,
        'action_applied': action_applied, 'was_cut': was_cut,
        'confidence': 0.95, 'detection_stage': 'llm'}
    m.update(overrides)
    return m


def _run_recut(ads_to_remove, all_ads, segment_actions, podcast_id=1):
    """Drive _recut_episode with _build_recut_ad_list mocked to return the
    given (ads_to_remove, all_ads), i.e. what the validator/confidence gate
    would have produced on this run, before re-resolution against the
    current action map. Audio processor is mocked out (no ffmpeg). Returns
    the audio segments actually cut and the markers persisted to storage.
    """
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        storage = p(processing, 'storage')
        p(processing, 'status_service')
        p(processing, '_copy_retained_original_to_temp',
          return_value='/tmp/segrerender-work.mp3')
        p(processing, '_build_recut_ad_list',
          return_value=(ads_to_remove, all_ads))
        p(processing, '_generate_assets')
        p(processing, '_finalize_episode')
        local_ap_cls = p(processing, 'AudioProcessor')
        p(processing.os.path, 'exists', return_value=False)
        p(processing.shutil, 'move')

        db.get_episode.return_value = {'podcast_id': podcast_id, 'processed_version': 0}
        db.get_original_segments.return_value = [{'start': 0.0, 'end': 60.0}]
        db.get_all_settings.return_value = {}
        db.resolve_segment_actions.return_value = segment_actions
        storage.get_original_path.return_value.exists.return_value = True
        storage.get_applied_cuts.return_value = None
        storage.get_episode_path.return_value = '/tmp/segrerender-final.mp3'

        local_ap = local_ap_cls.return_value
        local_ap.get_audio_duration.return_value = 60.0
        local_ap.process_episode.side_effect = (
            lambda work_path, segs, cut_barriers=None: (
                '/tmp/segrerender-cut.mp3',
                [{'start': s['start'], 'end': s['end']} for s in segs]))

        result = processing._recut_episode(
            'segrerender-feed', 'ep1', 'Episode', 'Podcast', 'desc',
            time.time(), cancel_event=None)

        assert result is True
        audio_segments = local_ap.process_episode.call_args.args[1]
        saved_markers = storage.save_combined_ads.call_args.args[2]

    return audio_segments, saved_markers


class TestRecutReResolvesAgainstCurrentMap:
    def test_flipped_map_keep_to_remove_cuts_previously_kept_marker(self):
        # Stale stored action_applied='keep' from a run where cross_promo
        # resolved 'keep'; the map has since flipped it back to 'remove'.
        marker = _marker(30.0, 40.0, 'cross_promo', 'keep', True)
        actions = ALL_REMOVE

        audio_segments, saved = _run_recut([marker], [marker], actions)

        assert (30.0, 40.0) in {(s['start'], s['end']) for s in audio_segments}
        saved_marker = next(m for m in saved if m['start'] == 30.0)
        assert saved_marker['action_applied'] == 'remove'

    def test_flipped_map_remove_to_keep_uncuts_previously_cut_marker(self):
        # Previously cut; the map has since flipped sponsor -> 'keep', so
        # it must not cut even though it's sitting in ads_to_remove.
        marker = _marker(10.0, 20.0, 'sponsor', 'remove', True)
        actions = dict(ALL_REMOVE, sponsor='keep')

        audio_segments, saved = _run_recut([marker], [marker], actions)

        assert audio_segments == []
        saved_marker = next(m for m in saved if m['start'] == 10.0)
        assert saved_marker['action_applied'] == 'keep'
        assert saved_marker['was_cut'] is False

    def test_keep_wins_over_a_stale_user_approval(self):
        # Simulates a marker force-accepted via a confirmed user approval:
        # was_cut=True, in ads_to_remove, no timestamp to distinguish a
        # fresh approval from a stale one. Keep wins unconditionally.
        marker = _marker(50.0, 60.0, 'sponsor', 'remove', True,
                         validation={'decision': 'ACCEPT',
                                    'flags': ['INFO: User confirmed as ad']})
        actions = dict(ALL_REMOVE, sponsor='keep')

        audio_segments, saved = _run_recut([marker], [marker], actions)

        assert audio_segments == []
        saved_marker = next(m for m in saved if m['start'] == 50.0)
        assert saved_marker['action_applied'] == 'keep'
        assert saved_marker['was_cut'] is False

    def test_action_applied_restamped_from_current_map_not_stale_value(self):
        # Stale action_applied='remove' from an earlier run; the current
        # map resolves this category to 'beep'.
        marker = _marker(5.0, 15.0, 'interaction', 'remove', True)
        actions = dict(ALL_REMOVE, interaction='beep')

        audio_segments, saved = _run_recut([marker], [marker], actions)

        by_span = {(s['start'], s['end']): s for s in audio_segments}
        assert by_span[(5.0, 15.0)]['beep'] is True
        saved_marker = next(m for m in saved if m['start'] == 5.0)
        assert saved_marker['action_applied'] == 'beep'

    def test_all_remove_map_regresses_exactly_as_before(self):
        # No 'keep' anywhere in the map: _partition_keep_ads is a no-op
        # (same objects, same order), so the recut path is byte-identical.
        sponsor = _marker(10.0, 20.0, 'sponsor', 'remove', True)
        promo = _marker(30.0, 40.0, 'cross_promo', 'remove', True)

        audio_segments, saved = _run_recut(
            [sponsor, promo], [sponsor, promo], dict(ALL_REMOVE))

        spans = {(s['start'], s['end']) for s in audio_segments}
        assert spans == {(10.0, 20.0), (30.0, 40.0)}
        assert all(s['beep'] is False for s in audio_segments)
        assert all(m['action_applied'] == 'remove' for m in saved)


# ---------------------------------------------------------------------------
# Part B: POST /feeds/<slug>/rerender-segments
# ---------------------------------------------------------------------------

from main_app import app as flask_app  # noqa: E402  (after bootstrap, by design)


@pytest.fixture
def client():
    from api import get_database, get_storage
    db = get_database()
    db.set_setting('app_password', '')
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c, db, get_storage()


def _seed_episode(db, storage, slug, episode_id, *, status='processed',
                  retained_original=True, segments=True, markers=True):
    db.upsert_episode(
        slug, episode_id, original_url='https://example.com/ep.mp3',
        title=f'Episode {episode_id}', description='desc', status=status)
    if retained_original:
        path = storage.get_original_path(slug, episode_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fake-audio')
    if segments:
        db.save_original_segments(
            slug, episode_id, [{'start': 0.0, 'end': 60.0, 'text': 'hi'}])
    if markers:
        db.save_episode_details(
            slug, episode_id,
            ad_markers=[{'start': 10.0, 'end': 20.0, 'category': 'sponsor'}])


def _seed_feed(db, storage, slug):
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Rerender Test')
    _seed_episode(db, storage, slug, 'aaaaaaaaaaaa')  # fully qualifying
    _seed_episode(db, storage, slug, 'bbbbbbbbbbbb')  # fully qualifying
    _seed_episode(db, storage, slug, 'cccccccccccc',
                 retained_original=False)              # no retained original
    _seed_episode(db, storage, slug, 'dddddddddddd',
                 status='discovered')                   # never processed
    return slug


class TestRerenderSegmentsEndpoint:
    def test_queues_only_qualifying_processed_episodes_and_returns_counts(self, client):
        c, db, storage = client
        slug = _seed_feed(db, storage, 'segrerender-counts-feed')

        with patch('main_app.processing.start_background_processing',
                   return_value=(True, 'started')) as mock_start:
            resp = c.post(f'/api/v1/feeds/{slug}/rerender-segments')

        assert resp.status_code == 200
        body = resp.get_json()
        assert body == {'queued': 2, 'skipped': 1}
        queued_ids = {call.args[1] for call in mock_start.call_args_list}
        assert queued_ids == {'aaaaaaaaaaaa', 'bbbbbbbbbbbb'}

    def test_queued_episode_is_marked_recut_mode(self, client):
        c, db, storage = client
        slug = _seed_feed(db, storage, 'segrerender-mode-feed')

        with patch('main_app.processing.start_background_processing',
                   return_value=(True, 'started')):
            c.post(f'/api/v1/feeds/{slug}/rerender-segments')

        ep = db.get_episode(slug, 'aaaaaaaaaaaa')
        assert ep['reprocess_mode'] == 'recut'
        assert ep['status'] == 'pending'

    def test_unknown_feed_404s(self, client):
        c, _db, _storage = client
        resp = c.post('/api/v1/feeds/does-not-exist/rerender-segments')
        assert resp.status_code == 404

    def test_no_processed_episodes_returns_zero_counts(self, client):
        c, db, storage = client
        slug = 'segrerender-empty'
        db.create_podcast(slug, 'https://example.com/feed.xml', 'Empty')
        _seed_episode(db, storage, slug, 'eeeeeeeeeeee', status='discovered')

        resp = c.post(f'/api/v1/feeds/{slug}/rerender-segments')

        assert resp.status_code == 200
        assert resp.get_json() == {'queued': 0, 'skipped': 0}


class TestRerenderSegmentsCsrf:
    def _login(self, c, db, password='segrerender-pw'):
        db.set_setting('app_password', generate_password_hash(password))
        resp = c.post('/api/v1/auth/login', json={'password': password})
        assert resp.status_code == 200

    def _csrf_header(self, c):
        for cookie in c._cookies.values():
            if cookie.key == 'minuspod_csrf':
                return {'X-CSRF-Token': cookie.value}
        return {}

    def test_rejected_without_csrf_token(self, client):
        c, db, storage = client
        slug = _seed_feed(db, storage, slug='segrerender-csrf-feed')
        self._login(c, db)

        resp = c.post(f'/api/v1/feeds/{slug}/rerender-segments')

        assert resp.status_code == 403

    def test_accepted_with_csrf_token(self, client):
        c, db, storage = client
        slug = _seed_feed(db, storage, slug='segrerender-csrf-feed-2')
        self._login(c, db)
        headers = self._csrf_header(c)

        with patch('main_app.processing.start_background_processing',
                   return_value=(True, 'started')):
            resp = c.post(f'/api/v1/feeds/{slug}/rerender-segments',
                          headers=headers)

        assert resp.status_code == 200
