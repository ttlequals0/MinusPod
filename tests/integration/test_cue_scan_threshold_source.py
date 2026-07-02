"""Integration tests for cue-scan thresholdSource and per-feed override (#350 Phase 5).

Tests verify:
- thresholdSource='global' when no per-feed override is set.
- thresholdSource='override' when per-feed override is set and no body scoreThreshold.
- thresholdSource='request' when a body scoreThreshold is provided.
- Per-feed override takes precedence over global setting (uses override value).

These tests mock the AudioCueTemplateMatcher to avoid requiring real audio files.
"""
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-thresh-test-'))


class _FakeMatcher:
    is_usable = True

    def __init__(self, *a, score_threshold=0.75, **kw):
        self.score_threshold = score_threshold

    def detect_with_debug(self, path):
        return [], {'threshold': self.score_threshold, 'elapsed_s': 0.01, 'templates': []}


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def seeded(app_client, monkeypatch):
    from api import get_database, get_storage
    from unittest.mock import MagicMock
    import audio_analysis.cue_template_matcher as ctm

    db = get_database()
    slug = 'thresh-src-feed'
    episode_id = 'aabbcc001122'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    podcast_id = db.create_podcast(slug, 'https://example.com/thresh.xml', title='Thresh Feed')
    db.upsert_episode(slug, episode_id, title='Ep', status='processed',
                      original_url='https://example.com/ep.mp3')
    db.upsert_episode(slug, episode_id, status='processed', original_file='original.mp3')

    storage = get_storage()
    orig_path = storage.get_original_path(slug, episode_id)
    orig_path.parent.mkdir(parents=True, exist_ok=True)
    orig_path.write_bytes(b'FAKE')

    # Seed one template row so cue-scan doesn't 400 "no enabled cue templates".
    db.create_cue_template(
        podcast_id=podcast_id,
        cue_type='ad_break_boundary',
        source_episode_id=episode_id,
        source_offset_s=0.0,
        duration_s=0.5,
        sample_rate=16000,
        n_coeffs=13,
        mfcc_blob=b'\x00' * 104,
        pcm_blob=b'\x00' * 16000,
    )

    # Patch AudioCueTemplateMatcher in the api.cue_templates namespace so the
    # route uses our stub rather than attempting to decode the fake audio file.
    import api.cue_templates as act
    monkeypatch.setattr(ctm, 'AudioCueTemplateMatcher', _FakeMatcher)
    monkeypatch.setattr(act, 'AudioCueTemplateMatcher', _FakeMatcher)

    yield {'slug': slug, 'episode_id': episode_id, 'db': db, 'podcast_id': podcast_id}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def test_threshold_source_global(app_client, seeded):
    slug, ep = seeded['slug'], seeded['episode_id']
    hdr = _csrf(app_client)
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-scan', json={}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['thresholdSource'] == 'global'


def test_threshold_source_override(app_client, seeded):
    slug, ep = seeded['slug'], seeded['episode_id']
    db = seeded['db']
    hdr = _csrf(app_client)
    db.update_podcast(slug, cue_template_score_override=0.65)
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-scan', json={}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['thresholdSource'] == 'override'
    assert abs(body['thresholdUsed'] - 0.65) < 0.001


def test_threshold_source_request(app_client, seeded):
    slug, ep = seeded['slug'], seeded['episode_id']
    hdr = _csrf(app_client)
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-scan',
                        json={'scoreThreshold': 0.55}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['thresholdSource'] == 'request'


def test_override_takes_precedence_over_global(app_client, seeded):
    slug, ep = seeded['slug'], seeded['episode_id']
    db = seeded['db']
    hdr = _csrf(app_client)
    db.set_setting('audio_cue_template_score', '0.80')
    db.update_podcast(slug, cue_template_score_override=0.65)
    r = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-scan', json={}, headers=hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['thresholdSource'] == 'override'
    assert abs(body['thresholdUsed'] - 0.65) < 0.001
