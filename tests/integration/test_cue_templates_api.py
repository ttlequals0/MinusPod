"""Integration tests for the cue-template REST API (#350).

Exercises the Flask routes end to end via app_client: validation, CRUD, scope
promotion, export/import, the diagnostic scan/preview, loud-spots, and the new
settings validation. Audio-backed routes seed a synthetic original-audio file.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import wave
import zipfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Point Storage/Database at a writable temp dir before main_app imports (the
# app_client fixture constructs the Storage singleton, whose default is the
# in-container /app/data). setdefault leaves an already-running suite's dir
# alone.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-api-test-'))


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _chirp(duration_s, sr=16000):
    t = np.arange(int(sr * duration_s)) / sr
    freq = 3000 + 2000 * (t / max(duration_s, 1e-9))
    phase = 2 * np.pi * np.cumsum(freq) / sr
    env = np.sin(np.pi * t / duration_s) ** 2
    return (0.7 * env * np.sin(phase)).astype(np.float32)


def _write_wav(path, samples, sr=16000):
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


@pytest.fixture
def seeded(app_client):
    """A feed with one processed episode whose original audio is on disk."""
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'cue-api-feed'
    episode_id = 'abcdef012345'
    # The app DB is a shared singleton across integration tests; clear any feed
    # leaked by a prior run before seeding.
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/cue.xml', title='Cue Show')
    db.upsert_episode(slug, episode_id, title='Ep 1', status='processed')
    # Second upsert hits the update path, which persists original_file.
    db.upsert_episode(slug, episode_id, status='processed', original_file='original.mp3')
    # Build a 2 s file: silence with a 0.5 s chirp planted at 0.6 s.
    sr = 16000
    audio = 0.01 * np.random.default_rng(0).standard_normal(int(sr * 2.0)).astype(np.float32)
    chirp = _chirp(0.5)
    start = int(0.6 * sr)
    audio[start:start + len(chirp)] = chirp
    path = storage.get_original_path(slug, episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path, audio, sr)
    yield {'slug': slug, 'episode_id': episode_id, 'db': db, 'storage': storage, 'path': path}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _seed_template(db, slug):
    from audio_analysis.cue_features import N_COEFFS, serialize_mfcc, pcm_to_int16_bytes
    pid = db.get_podcast_by_slug(slug)['id']
    rng = np.random.default_rng(2)
    mfcc = rng.standard_normal((10, N_COEFFS)).astype(np.float32)
    pcm = np.clip(rng.standard_normal(1600), -1, 1).astype(np.float32)
    return db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary', source_episode_id=None,
        source_offset_s=0.0, duration_s=0.5, sample_rate=16000, n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc), pcm_blob=pcm_to_int16_bytes(pcm),
        pcm_sample_rate=16000,
    )


# --- validation (no audio needed) -----------------------------------------

def test_list_empty(app_client, seeded):
    # Authenticate: the shared-singleton DB may carry a password set by an
    # earlier test in the full suite, which flips the global auth gate on.
    _csrf(app_client)
    r = app_client.get(f"/api/v1/feeds/{seeded['slug']}/cue-templates")
    assert r.status_code == 200
    assert r.get_json()['templates'] == []


def test_create_validation_errors(app_client, seeded):
    hdr = _csrf(app_client)
    slug, ep = seeded['slug'], seeded['episode_id']
    base = f'/api/v1/feeds/{slug}/cue-templates'
    # missing startS/endS
    assert app_client.post(base, json={'episodeId': ep}, headers=hdr).status_code == 400
    # invalid cueType (not in the fixed vocabulary)
    assert app_client.post(base, json={'episodeId': ep, 'startS': 0.6, 'endS': 1.1, 'cueType': 'freeform'}, headers=hdr).status_code == 400
    # too short
    assert app_client.post(base, json={'episodeId': ep, 'startS': 0.6, 'endS': 0.61}, headers=hdr).status_code == 400
    # bad scope
    assert app_client.post(base, json={'episodeId': ep, 'startS': 0.6, 'endS': 1.1, 'scope': 'global'}, headers=hdr).status_code == 400


def test_create_missing_episode_404(app_client, seeded):
    hdr = _csrf(app_client)
    r = app_client.post(
        f"/api/v1/feeds/{seeded['slug']}/cue-templates",
        json={'episodeId': 'ffffffffffff', 'startS': 0.6, 'endS': 1.1, 'cueType': 'ad_break_start'},
        headers=hdr)
    assert r.status_code == 404


def test_intro_capture_ceiling_reads_db_setting(app_client, seeded):
    # The show-intro per-type ceiling is the DB setting
    # audio_cue_capture_max_intro_seconds, not the hardcoded constant. Lower it
    # to 20s and a 30s show-intro selection must fail the cap gate (which runs
    # before any audio decode, like the too-short check above). The default 60s
    # ceiling would have let 30s through.
    hdr = _csrf(app_client)
    slug, ep = seeded['slug'], seeded['episode_id']
    base = f'/api/v1/feeds/{slug}/cue-templates'
    assert app_client.put('/api/v1/settings/ad-detection',
                          json={'audioCueCaptureMaxIntroSeconds': 20}, headers=hdr).status_code == 200
    r = app_client.post(base, json={'episodeId': ep, 'startS': 0.0, 'endS': 30.0,
                                    'cueType': 'show_intro'}, headers=hdr)
    assert r.status_code == 400
    assert 'at most 20' in r.get_json().get('error', '')


# --- audio-backed CRUD + scan/preview/loud-spots --------------------------

def test_full_lifecycle(app_client, seeded):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    hdr = _csrf(app_client)
    slug, ep = seeded['slug'], seeded['episode_id']
    base = f'/api/v1/feeds/{slug}/cue-templates'

    # create (decode -> mfcc -> persist)
    r = app_client.post(base, json={'episodeId': ep, 'startS': 0.6, 'endS': 1.1, 'cueType': 'ad_break_start'}, headers=hdr)
    assert r.status_code == 201, r.get_data(as_text=True)
    tpl = r.get_json()['template']
    assert tpl['cueType'] == 'ad_break_start' and tpl['label'] == 'ad-break start'
    assert tpl['scope'] == 'podcast'
    tid = tpl['id']

    # list shows it
    assert len(app_client.get(base).get_json()['templates']) == 1

    # scan finds the chirp
    scan = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-scan', json={}, headers=hdr)
    assert scan.status_code == 200
    sjson = scan.get_json()
    assert sjson['templates'][0]['peakScore'] > 0.5

    # preview the one template
    prev = app_client.post(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-template-preview', json={'templateId': tid}, headers=hdr)
    assert prev.status_code == 200
    assert 'matches' in prev.get_json()

    # loud spots endpoint returns the expected shape
    spots = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}/cue-loud-spots')
    assert spots.status_code == 200
    assert 'loudSpots' in spots.get_json()

    # export the template (it has raw PCM)
    exp = app_client.get(f'/api/v1/cue-templates/{tid}/export')
    assert exp.status_code == 200
    assert exp.mimetype == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(exp.get_data())) as z:
        assert {'cue.flac', 'template.json'} <= set(z.namelist())

    # import it back into the same feed
    imp = app_client.post(
        f'/api/v1/feeds/{slug}/cue-templates/import',
        data={'file': (io.BytesIO(exp.get_data()), 'cue.zip')},
        headers=hdr, content_type='multipart/form-data')
    assert imp.status_code == 201
    assert len(app_client.get(base).get_json()['templates']) == 2

    # delete
    assert app_client.delete(f'/api/v1/cue-templates/{tid}', headers=hdr).status_code == 200


def test_patch_scope_validated_before_write(app_client, seeded):
    hdr = _csrf(app_client)
    tid = _seed_template(seeded['db'], seeded['slug'])
    # Invalid scope must 400 AND not apply the cueType change (validate-before-write).
    r = app_client.patch(f'/api/v1/cue-templates/{tid}',
                         json={'cueType': 'ad_break_start', 'scope': 'bogus'}, headers=hdr)
    assert r.status_code == 400
    row = seeded['db'].get_cue_template(tid)
    assert row['cue_type'] == 'ad_break_boundary'  # unchanged


def test_promote_to_network(app_client, seeded):
    hdr = _csrf(app_client)
    db = seeded['db']
    db.update_podcast(seeded['slug'], network_id='net-x')
    tid = _seed_template(db, seeded['slug'])
    r = app_client.patch(f'/api/v1/cue-templates/{tid}',
                         json={'scope': 'network', 'networkId': 'net-x'}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['template']['scope'] == 'network'
    # network without networkId is rejected
    assert app_client.patch(f'/api/v1/cue-templates/{tid}', json={'scope': 'network'}, headers=hdr).status_code == 400


def test_export_without_pcm_is_422(app_client, seeded):
    from audio_analysis.cue_features import N_COEFFS, serialize_mfcc
    hdr = _csrf(app_client)
    db = seeded['db']
    pid = db.get_podcast_by_slug(seeded['slug'])['id']
    mfcc = np.zeros((5, N_COEFFS), dtype=np.float32)
    tid = db.create_cue_template(
        podcast_id=pid, cue_type='ad_break_boundary', source_episode_id=None, source_offset_s=0.0,
        duration_s=0.5, sample_rate=16000, n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc), pcm_blob=None, pcm_sample_rate=None)
    assert app_client.get(f'/api/v1/cue-templates/{tid}/export').status_code == 422


def test_import_rejects_wrong_sample_rate(app_client, seeded):
    hdr = _csrf(app_client)
    # Build a 44100 Hz WAV zip; import must hard-reject.
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(44100)
        wf.writeframes((np.zeros(44100, dtype='<i2')).tobytes())
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, 'w') as z:
        z.writestr('cue.wav', buf.getvalue())
        z.writestr('template.json', json.dumps({'schemaVersion': 1, 'label': 'x'}))
    zbuf.seek(0)
    r = app_client.post(
        f"/api/v1/feeds/{seeded['slug']}/cue-templates/import",
        data={'file': (zbuf, 'cue.zip')}, headers=hdr, content_type='multipart/form-data')
    assert r.status_code == 400
    assert '44100' in r.get_json().get('error', '')


# --- settings validation ---------------------------------------------------

def test_settings_validation_for_new_keys(app_client):
    hdr = _csrf(app_client)
    # out of range
    bad = app_client.put('/api/v1/settings/ad-detection',
                        json={'audioCueSnapConfidence': 2.0}, headers=hdr)
    assert bad.status_code == 400
    # valid round-trips through GET
    ok = app_client.put('/api/v1/settings/ad-detection',
                       json={'audioCuePairMaxBreakSeconds': 600, 'audioCueTemplateScore': 0.6},
                       headers=hdr)
    assert ok.status_code == 200
    g = app_client.get('/api/v1/settings').get_json()
    assert g['audioCuePairMaxBreakSeconds']['value'] == 600
    assert abs(g['audioCueTemplateScore']['value'] - 0.6) < 1e-9
    # restore defaults so this doesn't leak into other tests
    from api import get_database
    db = get_database()
    db.reset_setting('audio_cue_pair_max_break_seconds')
    db.reset_setting('audio_cue_template_score')
