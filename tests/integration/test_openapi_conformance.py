"""Live responses from the Flask test client must match openapi.yaml."""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='openapi-conformance-test-'))

from tests.app_bootstrap import authenticate_test_client  # noqa: E402
from tests.integration.openapi_conformance import (  # noqa: E402
    _spec, assert_response_conforms, response_schema,
)
from tests.fingerprint_match_helpers import record_fingerprint_match

SLUG = 'conformance-feed'
EPISODE_ID = 'a1b2c3d4e5f6'
_SEEDED = {}
MARKERS = [
    {'start': 10.0, 'end': 40.0, 'confidence': 0.9, 'sponsor': 'Acme', 'reason': 'sponsor read'},
    {'start': 100.0, 'end': 130.0, 'confidence': 0.4, 'was_cut': False,
     'validation': {'decision': 'REJECT'}},
    {'start': 200.0, 'end': 230.0, 'confidence': 0.6, 'held_for_review': True, 'was_cut': False},
]


@pytest.fixture(scope='module')
def seeded_rows():
    from api import get_database
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Conformance Test Show')
    try:
        db.upsert_episode(SLUG, EPISODE_ID, original_url='https://example.com/e1.mp3',
                          title='Episode 1', status='processed', original_duration=3600.0)
        db.save_episode_details(SLUG, EPISODE_ID, ad_markers=MARKERS,
                                transcript_text='[00:00:01.000 --> 00:00:05.000] hello there')
        segments = [{'start': 1.0, 'end': 5.0, 'text': 'hello there'}]
        db.save_original_transcript(SLUG, EPISODE_ID, '[00:00:01.000 --> 00:00:05.000] hello there')
        db.save_original_segments(SLUG, EPISODE_ID, segments)
        db.save_final_segments(SLUG, EPISODE_ID, segments)
        db.create_feed_subscriber_key(SLUG, 'Living room')
        _SEEDED['pattern_id'] = db.create_ad_pattern(
            scope='podcast', podcast_id=SLUG, text_template='Acme read for the conformance test ' * 3)
        db.create_audio_fingerprint(_SEEDED['pattern_id'], b'AQAA', 6.0, source_podcast_slug=SLUG,
                                    source_episode_id=EPISODE_ID, source_start=2.0, source_end=8.0)
        record_fingerprint_match(db, _SEEDED['pattern_id'], EPISODE_ID, SLUG, 10.0, 40.0,
                                    0.9, 0.8, True, False)
        db.create_pattern_correction(
            'false_positive', episode_id=EPISODE_ID, podcast_title='Conformance Test Show',
            episode_title='Episode 1', original_bounds={'start': 10.0, 'end': 40.0})
        yield
    finally:
        db.delete_podcast(SLUG)
        if 'pattern_id' in _SEEDED:
            db.delete_ad_pattern(_SEEDED.pop('pattern_id'))
        conn = db.get_connection()
        conn.execute('DELETE FROM pattern_corrections WHERE episode_id = ?', (EPISODE_ID,))
        conn.commit()


@pytest.fixture
def seeded(app_client, seeded_rows):
    return app_client, authenticate_test_client(app_client)


GET_ROUTES = [
    ('/auth/status', ''),
    ('/feeds', ''),
    ('/feeds/{slug}', ''),
    ('/feeds/{slug}/episodes', ''),
    ('/feeds/{slug}/episodes/{episode_id}', ''),
    ('/feeds/{slug}/ad-distribution', ''),
    ('/feeds/{slug}/subscriber-keys', ''),
    ('/feeds/subscriber-keys', ''),
    ('/feeds/{slug}/tags', ''),
    ('/feeds/{slug}/cue-candidate-dismissals', ''),
    ('/feeds/{slug}/cue-detections/advisory', ''),
    ('/feeds/{slug}/cue-templates', ''),
    ('/feeds/{slug}/episodes/{episode_id}/transcript', ''),
    ('/feeds/{slug}/episodes/{episode_id}/original-transcript', ''),
    ('/feeds/{slug}/episodes/{episode_id}/original-segments', ''),
    ('/feeds/{slug}/episodes/{episode_id}/final-segments', ''),
    ('/feeds/{slug}/episodes/{episode_id}/split-candidates', 'start=1&end=5'),
    ('/feeds/{slug}/episodes/{episode_id}/detected-cues', ''),
    ('/feeds/{slug}/episodes/{episode_id}/sponsorblock/candidates', ''),
    ('/episodes/pending-recuts', ''),
    ('/episodes/awaiting-approval', ''),
    ('/episodes/held', ''),
    ('/episodes/processing', ''),
    ('/stats/dashboard', ''),
    ('/stats/by-day', ''),
    ('/stats/by-podcast', ''),
    ('/stats/reviewer', ''),
    ('/stats/addressing', ''),
    ('/stats/model-usage', ''),
    ('/stats/systemone', ''),
    ('/stats/llm', ''),
    ('/stats/cleanup', ''),
    ('/stats/audio-matching', 'from=2026-11-01&to=2026-11-30'),
    ('/stats/episode-costs', ''),
    ('/stats/spend/attempts', 'slug={slug}&episodeId={episode_id}'),
    ('/stats/episode-costs/runs', 'slug={slug}&episodeId={episode_id}'),
    ('/stats/ledger-filter-options', ''),
    ('/patterns', ''),
    ('/patterns/corrections/unresolved', ''),
    ('/patterns/stats', ''),
    ('/patterns/health', ''),
    ('/patterns/contaminated', ''),
    ('/patterns/merge-suggestions', ''),
    ('/patterns/cleanup', ''),
    ('/patterns/cleanup/runs', ''),
    ('/patterns/cleanup/suggestions', ''),
    ('/search', 'q=episode'),
    ('/search/stats', ''),
    ('/networks', ''),
    ('/status', ''),
    ('/status/processing-admission', ''),
    ('/detections', ''),
    ('/detections', 'status=all'),
    ('/history', ''),
    ('/history/stats', ''),
    ('/history/downloads', ''),
    ('/failover', ''),
    ('/sponsors', ''),
    ('/sponsors/normalizations', ''),
    ('/settings', ''),
    ('/settings/providers', ''),
    ('/settings/models', ''),
    ('/settings/whisper-models', ''),
    ('/settings/whisper/capacity', ''),
    ('/settings/provider-budget', ''),
    ('/settings/retention', ''),
    ('/settings/offline-queue', ''),
    ('/settings/processing-schedule', ''),
    ('/settings/rate-limit-hold', ''),
    ('/settings/update-check', ''),
    ('/settings/youtube', ''),
    ('/youtube/search', 'q=example'),
    ('/settings/audio', ''),
    ('/settings/processing-timeouts', ''),
    ('/settings/webhooks', ''),
    ('/settings/notifications/timezone', ''),
    ('/settings/notifications/email', ''),
    ('/settings/reviewer', ''),
    ('/settings/community-sync', ''),
    ('/settings/replacement-audio', ''),
    ('/settings/db-backup', ''),
    ('/health', ''),
    ('/health/live', ''),
    ('/system/status', ''),
    ('/changelog', ''),
    ('/system/token-usage', ''),
    ('/system/model-pricing', ''),
    ('/system/queue', ''),
    ('/tags/vocabulary', ''),
    ('/podping/hosts', ''),
    ('/podping/nodes', ''),
    ('/community-patterns/sync-status', ''),
    ('/cue-detections/aggregate', ''),
    ('/patterns', ''),
    ('/patterns/{pattern_id}', ''),
    ('/patterns/{pattern_id}/audio', ''),
    ('/patterns/{pattern_id}/audio/matches', 'perPage=5'),
]


def _fill(template):
    return template.format(slug=SLUG, episode_id=EPISODE_ID, pattern_id=_SEEDED.get('pattern_id'))


def _spec_paths_with_json_200(method, prefix=''):
    paths = []
    for path, item in _spec()['paths'].items():
        if not path.startswith(prefix) or method not in item:
            continue
        try:
            response_schema(method, path)
        except KeyError:
            continue
        paths.append(path)
    return paths


# Routes with their own conformance tests below (they need a generated original)
OWN_TESTS = {'/feeds/{slug}/episodes/{episode_id}/peaks'}

UNCOVERED = {
    '/feeds/{slug}/episodes/{episode_id}/runs/{run_number}/log': 'needs a recorded processing run',
    '/feeds/{slug}/episodes/{episode_id}/transcript-span': 'needs an argument the seed cannot provide',
    '/feeds/{slug}/episodes/{episode_id}/cue-loud-spots': 'needs retained original audio',
    '/feeds/{slug}/episodes/{episode_id}/cue-candidates': 'needs retained original audio',
    '/feeds/{slug}/import/status': 'local feeds only',
    '/patterns/export': 'binary or streaming body',
    '/patterns/{pattern_id}/audio/preview': 'binary audio body; covered in test_audio_preview_route',
    '/podcast-search': 'network call',
    '/sponsors/{sponsor_id}': 'needs an argument the seed cannot provide',
    '/history/export': 'binary or streaming body',
    '/settings/providers/{slot}/affected-runs': 'needs an argument the seed cannot provide',
    '/system/updates': 'network call',
    '/settings/provider-budget/currencies': 'network call',
    '/settings/youtube/sb-user-id': 'needs a stored secret and secret encryption; covered in test_youtube_settings_api',
    '/settings/provider-budget/rate/{currency}': 'network call',
    '/system/config-backup': 'binary or streaming body',
    '/system/config-export': 'binary or streaming body',
    '/system/diagnostic-export': 'binary or streaming body',
}


def test_every_spec_get_route_is_covered():
    covered = {template for template, _ in GET_ROUTES} | set(UNCOVERED) | OWN_TESTS
    missing = [p for p in _spec_paths_with_json_200('get') if p not in covered]
    assert not missing, f'GET routes in openapi.yaml with no conformance test or UNCOVERED reason: {missing}'


@pytest.mark.parametrize('template,query', GET_ROUTES, ids=[t for t, _ in GET_ROUTES])
def test_get_response_matches_spec(seeded, template, query):
    client, headers = seeded
    url = _fill(template + (f'?{query}' if query else ''))
    assert_response_conforms('get', template, client.get(f'/api/v1{url}', headers=headers))


@pytest.fixture
def peaks_original(seeded, tmp_path, monkeypatch):
    import subprocess
    from api import get_database
    src = tmp_path / f'{EPISODE_ID}-original.wav'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                    '-ac', '1', '-ar', '44100', str(src)], check=True, capture_output=True)

    class _Storage:
        def get_original_path(self, slug, episode_id, extension='.mp3'):
            return src
    monkeypatch.setattr('api.episodes.media.get_storage', lambda: _Storage())
    db = get_database()
    db.upsert_episode(SLUG, EPISODE_ID, original_file=str(src), original_duration=3.0)
    yield src
    db.upsert_episode(SLUG, EPISODE_ID, original_file=None, original_duration=3600.0)


def test_peaks_200_matches_spec(seeded, peaks_original):
    client, headers = seeded
    url = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/peaks?start=0&resolution_ms=100'
    assert_response_conforms('get', '/feeds/{slug}/episodes/{episode_id}/peaks',
                             client.get(url, headers=headers))


def test_peaks_503_matches_spec(seeded, peaks_original, monkeypatch):
    import audio_peaks
    from api import get_database
    monkeypatch.setattr(audio_peaks, 'PEAKS_INLINE_MAX_SECONDS', 1.0)
    monkeypatch.setattr(audio_peaks, 'start_background_build', lambda *a, **k: None)
    client, headers = seeded
    get_database().upsert_episode(SLUG, EPISODE_ID, original_duration=3.0)
    url = f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/peaks?start=0&resolution_ms=100'
    resp = client.get(url, headers=headers)
    template = '/feeds/{slug}/episodes/{episode_id}/peaks'
    assert_response_conforms('get', template, resp, status=503)
    declared = _spec()['paths'][template]['get']['responses']['503']['headers']
    assert 'Retry-After' in declared
    assert resp.headers['Retry-After'] == '5'


def test_served_spec_carries_running_version(seeded):
    client, headers = seeded
    from utils.app_version import APP_VERSION
    body = client.get('/api/v1/openapi.yaml', headers=headers).get_data(as_text=True)
    assert re.search(r'^\s*version:\s*(.+)$', body, re.M).group(1).strip() == APP_VERSION
    assert '0.0.0-source' not in body


MUTATIONS = [
    ('put', '/settings/processing-schedule',
     {'enabled': True, 'start': '22:00', 'end': '06:00', 'days': ['mon', 'fri']}),
    ('patch', '/feeds/{slug}', {'titleOverride': 'Renamed', 'descriptionOverride': 'About'}),
    ('put', '/feeds/{slug}/tags', {'user_tags': []}),
    ('put', '/settings/ad-detection', {'whisperBackend': 'local'}),
    ('put', '/patterns/{pattern_id}/audio', {'enabled': True}),
    ('post', '/patterns/{pattern_id}/audio/rebuild',
     {'podcastSlug': SLUG, 'episodeId': EPISODE_ID, 'start': 10.0, 'end': 40.0}),
    ('put', '/settings/provider-budget',
     {'enabled': False, 'dailyLimitMicrousd': 0, 'maxReservations': 8,
      'unknownCost': 'deny', 'unknownReserveMicrousd': 0}),
]

ECHO_EXCLUDED = {}

ECHO_PUTS = [p for p in _spec_paths_with_json_200('get', '/settings')
             if p not in ECHO_EXCLUDED and p in _spec_paths_with_json_200('put', '/settings')]


@pytest.mark.parametrize('method,template,body', MUTATIONS, ids=[f'{m} {t}' for m, t, _ in MUTATIONS])
def test_mutation_response_matches_spec(seeded, method, template, body):
    client, headers = seeded
    response = getattr(client, method)(f'/api/v1{_fill(template)}', json=body, headers=headers)
    assert_response_conforms(method, template, response)


def test_delete_pattern_audio_matches_spec(seeded):
    from api import get_database
    client, headers = seeded
    db = get_database()
    pid = db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='Throwaway read for the delete test ' * 3)
    db.create_audio_fingerprint(pid, b'AQAA', 6.0)
    template = '/patterns/{pattern_id}/audio'
    assert_response_conforms('delete', template, client.delete(f'/api/v1/patterns/{pid}/audio', headers=headers))
    db.delete_ad_pattern(pid)


@pytest.mark.parametrize('template', ECHO_PUTS)
def test_put_echoing_current_settings_matches_spec(seeded, template):
    client, headers = seeded
    current = client.get(f'/api/v1{template}', headers=headers).get_json()
    assert_response_conforms('put', template, client.put(f'/api/v1{template}', json=current, headers=headers))


@pytest.mark.parametrize('value', ['oldest_first', 'newest_first', 'queued', None])
def test_feed_queue_order_patch_and_get_match_spec(seeded, value):
    client, headers = seeded
    patched = client.patch(f'/api/v1/feeds/{SLUG}', json={'queueOrder': value}, headers=headers)
    assert_response_conforms('patch', '/feeds/{slug}', patched)
    fetched = client.get(f'/api/v1/feeds/{SLUG}', headers=headers)
    assert_response_conforms('get', '/feeds/{slug}', fetched)
    assert fetched.get_json()['queueOrder'] == value


def test_global_queue_order_put_and_get_match_spec(seeded):
    client, headers = seeded
    put = client.put('/api/v1/settings/ad-detection', json={'queueOrder': 'newest_first'}, headers=headers)
    assert_response_conforms('put', '/settings/ad-detection', put)
    got = client.get('/api/v1/settings', headers=headers)
    assert_response_conforms('get', '/settings', got)
    entry = got.get_json()['queueOrder']
    assert (entry['value'], entry['isDefault']) == ('newest_first', False)
    assert entry['options'] == ['queued', 'oldest_first', 'newest_first']
