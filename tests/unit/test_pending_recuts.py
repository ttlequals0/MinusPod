"""Review decisions accumulate, then apply in one pass per episode.

Review is bulk work: an episode often collects several decisions at
different times, so each one stamps the episode instead of rewriting its
audio or its chapters, and the operator applies them together. The apply
decides per episode whether the decisions need a recut or only a chapter
rebuild.
"""
import json
import os
import tempfile
import threading
import time

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pending_recut_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app import app
from api.episodes import chapters_only_decisions

SLUG = 'pending-recut-test'
EPISODE_ID = 'a1b2c3d4e5f6'

CUT_AD = {'start': 300.0, 'end': 360.0, 'confidence': 0.95, 'category': 'sponsor',
          'reason': 'sponsor read', 'was_cut': True, 'action_applied': 'remove'}
KEPT_OUTRO = {'start': 1669.8, 'end': 1726.4, 'confidence': 1.0, 'category': 'outro',
              'reason': 'sign-off', 'was_cut': False, 'action_applied': 'keep'}
# Uncut for a different reason: the validator rejected it, so no category
# action is holding it and the keep guard does not apply.
UNCUT_REJECT = {'start': 800.0, 'end': 830.0, 'confidence': 0.4, 'category': 'sponsor',
                'reason': 'weak match', 'was_cut': False, 'action_applied': None}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The apply endpoint allows 5 per minute; this module posts more."""
    from api import limiter
    limiter.reset()
    yield


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Pending Recut Test')
    temp_db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                           original_url='https://example.com/ep.mp3',
                           title='Episode One', original_duration=1800.0)
    temp_db.save_episode_details(SLUG, EPISODE_ID,
                                 ad_markers=[dict(CUT_AD), dict(KEPT_OUTRO),
                                             dict(UNCUT_REJECT)],
                                 pending_review_count=0)
    return temp_db


def _correct(client, payload):
    return client.post(f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections', json=payload)


def _original(ad):
    return {'start': ad['start'], 'end': ad['end']}


def _pending(db):
    return db.count_episodes_pending_recut()


def _markers(db):
    return json.loads(db.get_episode(SLUG, EPISODE_ID)['ad_markers_json'])


class TestPendingStamp:
    def test_confirming_a_cut_marker_stamps_it(self, client, seeded):
        assert _correct(client, {'type': 'confirm',
                                 'original_ad': _original(CUT_AD)}).status_code == 200
        # Every recorded decision stamps; the apply works out what it needs.
        assert _pending(seeded) == 1

    def test_rejecting_a_cut_marker_stamps_it(self, client, seeded):
        assert _correct(client, {'type': 'reject',
                                 'original_ad': _original(CUT_AD)}).status_code == 200
        assert _pending(seeded) == 1

    def test_rejecting_an_uncut_marker_also_stamps(self, client, seeded):
        # Nothing to restore in the audio, but the chapters still change.
        assert _correct(client, {'type': 'reject',
                                 'original_ad': _original(UNCUT_REJECT)}).status_code == 200
        assert _pending(seeded) == 1

    def test_several_decisions_stamp_the_episode_once(self, client, seeded):
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        first = seeded.get_episodes_pending_recut()[0]['pending_recut_at']
        _correct(client, {'type': 'recategorize', 'category': 'intro',
                          'original_ad': _original(KEPT_OUTRO)})
        rows = seeded.get_episodes_pending_recut()
        assert len(rows) == 1
        # First-write-wins: the stamp marks when the episode went stale.
        assert rows[0]['pending_recut_at'] == first


class TestRecategorize:
    def test_sets_the_marker_category(self, client, seeded):
        r = _correct(client, {'type': 'recategorize', 'category': 'sponsor',
                              'original_ad': _original(KEPT_OUTRO)})
        assert r.status_code == 200
        assert r.get_json()['previousCategory'] == 'outro'
        markers = _markers(seeded)
        changed = [m for m in markers if m['start'] == KEPT_OUTRO['start']][0]
        assert changed['category'] == 'sponsor'

    def test_is_exempt_from_the_keep_guard(self, client, seeded):
        """A keep marker refuses confirm/reject, but recategorizing it is the
        supported way to change that verdict."""
        blocked = _correct(client, {'type': 'confirm',
                                    'original_ad': _original(KEPT_OUTRO)})
        assert blocked.status_code == 409
        allowed = _correct(client, {'type': 'recategorize', 'category': 'sponsor',
                                    'original_ad': _original(KEPT_OUTRO)})
        assert allowed.status_code == 200

    def test_stamps_the_episode(self, client, seeded):
        _correct(client, {'type': 'recategorize', 'category': 'intro',
                          'original_ad': _original(KEPT_OUTRO)})
        assert _pending(seeded) == 1
        seeded.clear_episode_pending_recut(SLUG, EPISODE_ID)
        # Same resolved action either side: the apply, not the endpoint,
        # decides that this one needs no recut.
        _correct(client, {'type': 'recategorize', 'category': 'cross_promo',
                          'original_ad': _original(CUT_AD)})
        assert _pending(seeded) == 1

    def test_rejects_an_unknown_category(self, client, seeded):
        r = _correct(client, {'type': 'recategorize', 'category': 'nonsense',
                              'original_ad': _original(CUT_AD)})
        assert r.status_code == 400

    def test_404s_when_no_marker_matches(self, client, seeded):
        r = _correct(client, {'type': 'recategorize', 'category': 'sponsor',
                              'original_ad': {'start': 9999.0, 'end': 9999.5}})
        assert r.status_code == 404


class TestApplyEndpoint:
    def test_lists_and_counts_pending_episodes(self, client, seeded):
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        body = client.get('/api/v1/episodes/pending-recuts').get_json()
        assert body['count'] == 1
        assert body['episodes'][0]['episodeId'] == EPISODE_ID
        assert body['episodes'][0]['podcast'] == 'Pending Recut Test'

    def test_apply_skips_episodes_without_retained_audio_and_keeps_them(
            self, client, seeded):
        """A skipped episode must keep its stamp, or its decisions are lost
        with nothing left to re-apply."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        body = client.post('/api/v1/episodes/pending-recuts/apply').get_json()
        assert body == {'queued': 0, 'skipped': 1, 'chaptersRebuilding': 0}
        assert _pending(seeded) == 1

    def test_slug_scopes_the_list(self, client, seeded):
        """A feed page applies only its own episodes."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        mine = client.get(f'/api/v1/episodes/pending-recuts?slug={SLUG}').get_json()
        assert mine['count'] == 1
        other = client.get(
            '/api/v1/episodes/pending-recuts?slug=some-other-feed').get_json()
        assert other['count'] == 0
        assert other['episodes'] == []

    def test_apply_scoped_to_another_feed_leaves_this_one_stamped(
            self, client, seeded):
        """The client sends {slug}; a feed page must not touch other feeds."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        body = client.post(
            '/api/v1/episodes/pending-recuts/apply',
            data=json.dumps({'slug': 'some-other-feed'}),
            content_type='application/json').get_json()
        assert body == {'queued': 0, 'skipped': 0, 'chaptersRebuilding': 0}
        assert _pending(seeded) == 1

    def test_apply_survives_a_body_that_is_not_an_object(self, client, seeded):
        """A JSON string reached .get('slug') and raised a 500."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        r = client.post('/api/v1/episodes/pending-recuts/apply',
                        data='"a string"', content_type='application/json')
        assert r.status_code == 200


class TestUnmatchedMarkerStillStamps:
    """Client bounds can trail a recut past the 0.5s match tolerance; the
    decision still has to reach the audio."""

    def test_reject_with_drifted_bounds_stamps(self, client, seeded):
        r = _correct(client, {'type': 'reject',
                              'original_ad': {'start': CUT_AD['start'] + 2.0,
                                              'end': CUT_AD['end'] + 2.0}})
        assert r.status_code == 200
        assert _pending(seeded) == 1

    def test_adjust_with_drifted_bounds_stamps(self, client, seeded):
        r = _correct(client, {'type': 'adjust',
                              'original_ad': {'start': CUT_AD['start'] + 2.0,
                                              'end': CUT_AD['end'] + 2.0},
                              'adjusted_start': 305.0, 'adjusted_end': 355.0})
        assert r.status_code == 200
        assert _pending(seeded) == 1


class TestMidRunDecisions:
    """A run only cuts what it loaded; a decision landing mid-run must
    survive the run's completion."""

    OLD = '2020-01-01T00:00:00Z'

    def _backdate(self, db):
        conn = db.get_connection()
        conn.execute("UPDATE episodes SET pending_recut_at = ? WHERE episode_id = ?",
                     (self.OLD, EPISODE_ID))
        conn.commit()

    def test_processing_episode_gets_a_fresh_stamp(self, seeded):
        seeded.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, status='processing')
        self._backdate(seeded)
        seeded.mark_episode_pending_recut(SLUG, EPISODE_ID)
        assert seeded.get_episodes_pending_recut()[0]['pending_recut_at'] > self.OLD

    def test_idle_episode_keeps_first_write_wins(self, seeded):
        self._backdate(seeded)
        seeded.mark_episode_pending_recut(SLUG, EPISODE_ID)
        assert seeded.get_episodes_pending_recut()[0]['pending_recut_at'] == self.OLD

    def test_clear_before_keeps_a_newer_stamp(self, seeded):
        seeded.mark_episode_pending_recut(SLUG, EPISODE_ID)
        seeded.clear_episode_pending_recut(SLUG, EPISODE_ID, before=self.OLD)
        assert _pending(seeded) == 1
        seeded.clear_episode_pending_recut(SLUG, EPISODE_ID,
                                           before='2099-01-01T00:00:00Z')
        assert _pending(seeded) == 0


class TestApplySkipsQueuedRuns:
    def _queue_row(self, db):
        db.queue_episode_for_processing(SLUG, EPISODE_ID,
                                        'https://example.com/ep.mp3')

    def test_apply_keeps_a_queued_reruns_mode(self, client, seeded):
        """Upserting recut over a queued full run would silently downgrade
        the rerun the user asked for."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        seeded.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                              status='pending', reprocess_mode='full')
        self._queue_row(seeded)
        body = client.post('/api/v1/episodes/pending-recuts/apply').get_json()
        assert body == {'queued': 0, 'skipped': 1, 'chaptersRebuilding': 0}
        assert seeded.get_episode(SLUG, EPISODE_ID)['reprocess_mode'] == 'full'
        assert _pending(seeded) == 1

    def test_stranded_pending_episode_is_not_skipped_for_status(self, client, seeded):
        """'pending' with no queue row (a cleared queue) has no run coming;
        only the recut preconditions may skip it, not its status."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        seeded.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, status='pending')
        listed = client.get('/api/v1/episodes/pending-recuts').get_json()
        assert listed['episodes'][0]['inFlight'] is False

    def test_get_reports_queued_rows_in_flight(self, client, seeded):
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        seeded.upsert_episode(slug=SLUG, episode_id=EPISODE_ID, status='pending')
        self._queue_row(seeded)
        body = client.get('/api/v1/episodes/pending-recuts').get_json()
        assert body['episodes'][0]['inFlight'] is True
        assert body['episodes'][0]['recutReady'] is False

    def test_get_reports_recut_readiness(self, client, seeded):
        """No retained original or saved segments here, so the row says a
        recut cannot rebuild it."""
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        body = client.get('/api/v1/episodes/pending-recuts').get_json()
        assert body['episodes'][0]['recutReady'] is False


HELD = [{'start': 400.0 + i * 100, 'end': 440.0 + i * 100, 'confidence': 0.8,
         'category': 'sponsor', 'reason': 'held read', 'was_cut': False,
         'action_applied': 'remove', 'held_for_review': True,
         'hold_reason': 'low_confidence'}
        for i in range(5)]


EPISODE_ID_2 = 'b2c3d4e5f6a1'


def _seed_reviewable(db, episode_id):
    """One cut ad already in the applied cuts, plus the held markers."""
    db.upsert_episode(slug=SLUG, episode_id=episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Episode One', original_duration=1800.0,
                      status='processed')
    db.save_episode_details(SLUG, episode_id,
                            ad_markers=[dict(CUT_AD)] + [dict(m) for m in HELD],
                            pending_review_count=len(HELD))
    db.save_applied_cuts(SLUG, episode_id,
                         [{'start': CUT_AD['start'], 'end': CUT_AD['end']}])


@pytest.fixture
def reviewable(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Pending Recut Test')
    _seed_reviewable(temp_db, EPISODE_ID)
    return temp_db


@pytest.fixture
def reviewable_pair(reviewable):
    """Two chapters-only episodes, so one apply carries a batch."""
    _seed_reviewable(reviewable, EPISODE_ID_2)
    return reviewable


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _reject_held(client, episode_id=EPISODE_ID, held=HELD[0]):
    return client.post(
        f'/api/v1/episodes/{SLUG}/{episode_id}/corrections',
        json={'type': 'reject', 'original_ad': _original(held)})


@pytest.fixture
def rebuilds(monkeypatch):
    from main_app import processing
    calls = []
    monkeypatch.setattr(processing, 'rebuild_ad_chapters',
                        lambda slug, episode_id, markers, episode=None:
                        calls.append(markers) or True)
    return calls


class TestChaptersOnlyApply:
    def test_five_rejects_stamp_once_and_rebuild_nothing_in_the_request(
            self, client, reviewable, rebuilds):
        for held in HELD:
            assert _correct(client, {'type': 'reject',
                                     'original_ad': _original(held)}).status_code == 200
        assert rebuilds == []
        assert _pending(reviewable) == 1

    def test_applying_rebuilds_the_chapters_once_and_clears_the_stamp(
            self, client, reviewable, rebuilds):
        for held in HELD:
            _correct(client, {'type': 'reject', 'original_ad': _original(held)})
        body = client.post('/api/v1/episodes/pending-recuts/apply').get_json()
        assert body == {'queued': 0, 'skipped': 0, 'chaptersRebuilding': 1}
        assert _wait_for(lambda: _pending(reviewable) == 0)
        assert len(rebuilds) == 1

    def test_a_real_audio_change_still_takes_the_recut_path(
            self, client, reviewable, rebuilds):
        # Rejecting a cut ad restores audio, so the recorded false positive
        # has to pull the episode onto the recut path.
        _correct(client, {'type': 'reject', 'original_ad': _original(CUT_AD)})
        body = client.post('/api/v1/episodes/pending-recuts/apply').get_json()
        assert body['chaptersRebuilding'] == 0
        assert rebuilds == []
        # No retained original here, so the recut is skipped and kept stamped.
        assert body['skipped'] == 1
        assert _pending(reviewable) == 1

    def test_a_chapters_only_episode_is_ready_without_the_recut_inputs(
            self, client, reviewable, rebuilds):
        _correct(client, {'type': 'reject', 'original_ad': _original(HELD[0])})
        body = client.get('/api/v1/episodes/pending-recuts').get_json()
        assert body['episodes'][0]['recutReady'] is True


class TestChaptersOnlyRunInTheBackground:
    """The rebuild is a full-file remux, so the apply must not wait on it."""

    def test_the_request_returns_before_the_remux_finishes(
            self, client, reviewable, monkeypatch):
        from main_app import processing
        release, started = threading.Event(), threading.Event()

        def _slow_rebuild(slug, episode_id, markers, episode=None):
            started.set()
            release.wait(5)
            return True

        monkeypatch.setattr(processing, 'rebuild_ad_chapters', _slow_rebuild)
        _reject_held(client)
        began = time.monotonic()
        body = client.post('/api/v1/episodes/pending-recuts/apply').get_json()
        assert time.monotonic() - began < 1.0
        assert body['chaptersRebuilding'] == 1
        assert _wait_for(started.is_set)
        # The stamp is still there while the remux runs.
        assert _pending(reviewable) == 1
        release.set()
        assert _wait_for(lambda: _pending(reviewable) == 0)

    def test_a_batch_is_rebuilt_one_episode_at_a_time(
            self, client, reviewable_pair, monkeypatch):
        from main_app import processing
        counter_lock = threading.Lock()
        state = {'active': 0, 'peak': 0}
        order = []

        def _tracked_rebuild(slug, episode_id, markers, episode=None):
            with counter_lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            time.sleep(0.05)
            with counter_lock:
                state['active'] -= 1
            order.append(episode_id)
            return True

        monkeypatch.setattr(processing, 'rebuild_ad_chapters', _tracked_rebuild)
        _reject_held(client)
        _reject_held(client, EPISODE_ID_2)
        client.post('/api/v1/episodes/pending-recuts/apply')
        assert _wait_for(lambda: len(order) == 2)
        assert state['peak'] == 1
        assert sorted(order) == sorted([EPISODE_ID, EPISODE_ID_2])
        assert _wait_for(lambda: _pending(reviewable_pair) == 0)

    def test_a_failed_rebuild_keeps_its_stamp_and_the_batch_goes_on(
            self, client, reviewable_pair, monkeypatch):
        from main_app import processing
        done = []

        def _failing_first(slug, episode_id, markers, episode=None):
            if episode_id == EPISODE_ID:
                raise RuntimeError('ffmpeg is missing')
            done.append(episode_id)
            return True

        monkeypatch.setattr(processing, 'rebuild_ad_chapters', _failing_first)
        _reject_held(client)
        _reject_held(client, EPISODE_ID_2)
        client.post('/api/v1/episodes/pending-recuts/apply')
        # The failed episode keeps its stamp, so the next apply retries it.
        assert _wait_for(lambda: [r['episode_id'] for r
                                  in reviewable_pair.get_episodes_pending_recut()]
                         == [EPISODE_ID])
        assert done == [EPISODE_ID_2]


class TestChaptersOnlyDecisions:
    """The apply's per-episode question: do the markers still describe the
    cuts the audio already has?"""

    CUTS = [{'start': 300.0, 'end': 360.0}]
    FP = [{'start': 300.0, 'end': 360.0}]

    def test_true_when_the_markers_reproduce_the_applied_cuts(self):
        assert chapters_only_decisions([dict(CUT_AD)], self.CUTS, 1800.0) is True

    def test_false_when_a_cut_marker_was_rejected(self):
        rejected = dict(CUT_AD, was_cut=False, validation={'decision': 'REJECT'})
        assert chapters_only_decisions([rejected], self.CUTS, 1800.0) is False

    def test_false_when_a_false_positive_correction_covers_a_cut_marker(self):
        """A reject on a marker that is not held leaves the marker alone; the
        recorded correction is the only sign the audio must change."""
        assert chapters_only_decisions([dict(CUT_AD)], self.CUTS, 1800.0,
                                       false_positives=self.FP) is False

    def test_false_when_a_confirmed_correction_covers_an_uncut_marker(self):
        uncut = dict(CUT_AD, start=800.0, end=860.0, was_cut=False)
        assert chapters_only_decisions(
            [dict(CUT_AD), uncut], self.CUTS, 1800.0,
            confirmed=[{'start': 800.0, 'end': 860.0}]) is False

    def test_false_when_the_category_action_now_keeps_the_marker(self):
        assert chapters_only_decisions([dict(CUT_AD)], self.CUTS, 1800.0,
                                       actions={'sponsor': 'keep'}) is False

    def test_false_when_an_approved_hold_is_waiting_to_be_cut(self):
        approved = dict(HELD[0], approved=True)
        assert chapters_only_decisions(
            [dict(CUT_AD), approved], self.CUTS, 1800.0) is False

    def test_true_when_a_held_marker_is_still_awaiting_review(self):
        assert chapters_only_decisions(
            [dict(CUT_AD), dict(HELD[0])], self.CUTS, 1800.0) is True

    def test_true_when_a_kept_marker_is_left_in_the_audio(self):
        assert chapters_only_decisions(
            [dict(CUT_AD), dict(KEPT_OUTRO)], self.CUTS, 1800.0) is True

    def test_false_when_boundaries_moved(self):
        moved = dict(CUT_AD, start=310.0)
        assert chapters_only_decisions([moved], self.CUTS, 1800.0) is False

    def test_false_without_a_persisted_cut_list_or_duration(self):
        assert chapters_only_decisions([dict(CUT_AD)], None, 1800.0) is False
        assert chapters_only_decisions([dict(CUT_AD)], self.CUTS, None) is False
