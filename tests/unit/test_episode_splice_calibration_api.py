"""Episode detail exposes the stored splice calibration."""
import json
import uuid

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('episode_splice_calibration_api_test_')

from api import get_database  # noqa: E402

SLUG = 'example-podcast'
CALIBRATION = {'status': 'host_read', 'episodes_considered': 10,
               'long_cut_corroboration': {'episodes': 6, 'cuts': 8, 'corroborated': 1,
                                          'fraction': 0.125}}


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


def test_stored_calibration_is_returned(app_client, seeded):
    db, episode_id = seeded
    db.save_episode_audio_analysis(SLUG, episode_id, json.dumps(
        {'splice_evidence': {'events': [], 'calibration': CALIBRATION}}))
    assert _detail(app_client, episode_id)['spliceCalibration'] == CALIBRATION


@pytest.mark.parametrize('stored', [None, '{"splice_evidence": null}', 'not json',
                                    '{"splice_evidence": {"calibration": "x"}}'])
def test_null_without_a_stored_calibration(app_client, seeded, stored):
    db, episode_id = seeded
    if stored is not None:
        db.save_episode_audio_analysis(SLUG, episode_id, stored)
    assert _detail(app_client, episode_id)['spliceCalibration'] is None
