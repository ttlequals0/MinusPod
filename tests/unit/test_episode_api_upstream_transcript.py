"""Episode detail and run stats expose the upstream transcript differential."""
import uuid

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('episode_upstream_transcript_api_test_')

from api import get_database  # noqa: E402
from api.episodes import _run_stats_to_api  # noqa: E402

SLUG = 'example-podcast'
PAYLOAD = {
    'status': 'ok', 'source_url': 'https://example.com/ep.vtt', 'mime': 'text/vtt',
    'timed': True, 'coverage': 0.93, 'fetched_at': '2026-10-05T00:00:00Z', 'error': None,
    'spans': [
        {'start': 120.5, 'end': 181.0, 'words': 140, 'offset_confirmed': True,
         'text_preview': 'this episode is brought to you by'},
        {'start': 900.0, 'end': 915.0, 'words': 41, 'offset_confirmed': False,
         'text_preview': 'and now a word'},
    ],
}


@pytest.fixture
def seeded(app_client):
    db = get_database()
    if not db.get_podcast_by_slug(SLUG):
        db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    episode_id = uuid.uuid4().hex[:12]
    db.upsert_episode(slug=SLUG, episode_id=episode_id,
                      original_url='https://example.com/ep.mp3', title='Ep', status='processed')
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    return db, episode_id


def _detail(client, episode_id):
    return client.get(f'/api/v1/feeds/{SLUG}/episodes/{episode_id}').get_json()


def test_stored_result_is_returned_in_api_shape(app_client, seeded):
    db, episode_id = seeded
    db.save_episode_upstream_transcript(SLUG, episode_id, PAYLOAD)
    assert _detail(app_client, episode_id)['upstreamTranscript'] == {
        'status': 'ok',
        'coverage': 0.93,
        'sourceType': 'text/vtt',
        'spans': [
            {'start': 120.5, 'end': 181.0, 'offsetConfirmed': True},
            {'start': 900.0, 'end': 915.0, 'offsetConfirmed': False},
        ],
    }


def test_error_result_has_no_spans(app_client, seeded):
    db, episode_id = seeded
    db.save_episode_upstream_transcript(SLUG, episode_id, {
        'status': 'error', 'source_url': 'https://example.com/ep.vtt', 'mime': None,
        'coverage': None, 'spans': [], 'error': 'fetch failed'})
    assert _detail(app_client, episode_id)['upstreamTranscript'] == {
        'status': 'error', 'coverage': None, 'sourceType': None, 'spans': []}


def test_null_without_a_stored_result(app_client, seeded):
    _db, episode_id = seeded
    assert _detail(app_client, episode_id)['upstreamTranscript'] is None


def test_run_stats_carry_transcript_diff():
    out = _run_stats_to_api({
        'mode': 'auto',
        'stage_hits': {'fingerprint': 0, 'text_pattern': 1, 'differential': 0,
                       'llm': 2, 'transcript_differential': 3},
        'timings': {'transcript_diff': 1.25},
        'transcript_diff': {'status': 'ok', 'coverage': 0.9, 'spans': 3},
    })
    assert out['stageHits']['transcriptDifferential'] == 3
    assert out['timings']['transcriptDiffSeconds'] == 1.25
    assert out['transcriptDiff'] == {'status': 'ok', 'coverage': 0.9, 'spans': 3}


def test_run_stats_without_transcript_diff():
    out = _run_stats_to_api({'mode': 'auto', 'stage_hits': {'llm': 1}, 'timings': {}})
    assert out['stageHits']['transcriptDifferential'] == 0
    assert out['timings']['transcriptDiffSeconds'] is None
    assert 'transcriptDiff' not in out
