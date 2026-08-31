"""Review decisions accumulate, then apply in one recut per episode.

Review is bulk work: an episode often collects several decisions at
different times, so each one stamps the episode instead of re-cutting it,
and the operator applies them together.
"""
import json
import os
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pending_recut_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app import app

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
    def test_confirming_an_uncut_marker_stamps_it(self, client, seeded):
        assert _correct(client, {'type': 'confirm',
                                 'original_ad': _original(CUT_AD)}).status_code == 200
        # CUT_AD is already cut, so confirming it changes no audio.
        assert _pending(seeded) == 0

    def test_rejecting_a_cut_marker_stamps_it(self, client, seeded):
        assert _correct(client, {'type': 'reject',
                                 'original_ad': _original(CUT_AD)}).status_code == 200
        assert _pending(seeded) == 1

    def test_rejecting_an_uncut_marker_does_not(self, client, seeded):
        # Nothing to restore: the span was never cut out.
        assert _correct(client, {'type': 'reject',
                                 'original_ad': _original(UNCUT_REJECT)}).status_code == 200
        assert _pending(seeded) == 0

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

    def test_stamps_only_when_the_resolved_action_changes(self, client, seeded):
        # outro -> intro: both default to remove, but the marker was kept, so
        # the audio does change.
        _correct(client, {'type': 'recategorize', 'category': 'intro',
                          'original_ad': _original(KEPT_OUTRO)})
        assert _pending(seeded) == 1
        seeded.clear_episode_pending_recut(SLUG, EPISODE_ID)
        # sponsor -> cross_promo on an already-removed marker: same action.
        _correct(client, {'type': 'recategorize', 'category': 'cross_promo',
                          'original_ad': _original(CUT_AD)})
        assert _pending(seeded) == 0

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
        assert body == {'queued': 0, 'skipped': 1}
        assert _pending(seeded) == 1
