"""retry-ad-detection must honor the per-episode pass-through override, not
just the feed mode: an episode marked pass-through makes no LLM call.
"""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR',
                      tempfile.mkdtemp(prefix='passthrough-retry-test-'))

import run_context  # noqa: E402

SLUG = 'passthrough-retry-feed'
EP = 'aa11bb22cc33'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture
def feed(app_client):
    from api import get_database
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Retry Guard Test')
    db.upsert_episode(SLUG, EP, title='Episode', status='processed',
                      original_url='https://example.com/e.mp3')
    yield {'client': app_client, 'db': db}
    db.delete_podcast(SLUG)


def _retry(client):
    return client.post(f'/api/v1/feeds/{SLUG}/episodes/{EP}/retry-ad-detection',
                       headers=_csrf_headers(client))


class TestRetryAdDetectionPerEpisodeGuard:
    def test_per_episode_passthrough_override_is_refused(self, feed):
        client, db = feed['client'], feed['db']
        _authed(client)
        db.set_episodes_passthrough(SLUG, [EP], True)
        resp = _retry(client)
        assert resp.status_code == 409
        assert 'passthrough' in resp.get_json()['error']

    def test_standard_episode_is_not_refused_by_the_mode_guard(self, feed):
        client = feed['client']
        _authed(client)
        resp = _retry(client)
        # No transcript on this seeded episode, so the mode guard is past.
        assert resp.status_code == 400


class TestRetryAdDetectionRoute:
    def test_run_resolves_detection_to_secondary_slot(self, feed):
        client, db = feed['client'], feed['db']
        _authed(client)
        db.set_setting('claude_model', 'secondary-model-x')
        db.set_setting('detection_provider', 'secondary')
        db.set_setting('secondary_provider_enabled', 'true')
        db.set_setting('secondary_provider', 'openrouter')
        seen = {}

        def _capture(*args, **kwargs):
            seen['route'] = run_context.route_for_phase('detection')
            return {'status': 'success', 'ads': []}

        segments = [{'start': 0.0, 'end': 5.0, 'text': 'hello'}]
        with patch('api.episodes.runs.get_storage') as storage, \
                patch('api.episodes.runs.parse_transcript_segments', return_value=segments), \
                patch('ad_detector.AdDetector.process_transcript', side_effect=_capture):
            storage.return_value.get_transcript.return_value = 'x'
            _retry(client)
        route = seen['route']
        assert route['credential_slot'] == 'secondary'
        assert route['provider_key'] == 'openrouter'
        assert route['configured_model'] == 'secondary-model-x'
