"""Integration test: cue candidate scan wires ad-affinity typing from ad_markers_json."""
import json
import os
import sys
import tempfile
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-affinity-test-'))


def _write_wav(path, samples, sr=16000):
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


@pytest.fixture
def seeded_with_ad_history(app_client):
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'affinity-test-feed'
    episode_id = 'af01234567ab'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/affinity.xml', title='Affinity Show')
    db.upsert_episode(slug, episode_id, title='Ep 1', status='processed',
                      original_file='original.mp3')
    # Plant post-review markers (was_cut set) near the expected occurrences;
    # raw markers without was_cut are untrusted and yield no affinity.
    ad_markers = [
        {'start': 100.0, 'end': 160.0, 'confidence': 0.9, 'was_cut': True},
        {'start': 600.0, 'end': 660.0, 'confidence': 0.9, 'was_cut': True},
    ]
    db.save_episode_details(slug, episode_id, ad_markers=ad_markers)
    sr = 16000
    audio = 0.01 * np.random.default_rng(1).standard_normal(int(sr * 2.0)).astype(np.float32)
    path = storage.get_original_path(slug, episode_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path, audio, sr)
    yield {'slug': slug, 'episode_id': episode_id, 'db': db, 'storage': storage}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def test_cue_candidates_affinity_wired(seeded_with_ad_history):
    """Stub fingerprinter so occurrences near ad boundaries -> suggestedType set."""
    slug = seeded_with_ad_history['slug']
    episode_id = seeded_with_ad_history['episode_id']
    db = seeded_with_ad_history['db']
    storage = seeded_with_ad_history['storage']

    podcast = db.get_podcast_by_slug(slug)
    real_audio = str(storage.get_original_path(slug, episode_id))

    # Stub discover_recurring_spots to return a candidate with occurrences near
    # both ad boundaries -> affinity = 4/4 = 1.0, should be typed.
    fake_candidates = [
        {'start': 99.0, 'end': 102.0, 'count': 4,
         'occurrences': [99.5, 599.5, 99.8, 599.8]},
    ]

    from api.cue_templates import _run_cue_candidate_scan
    from config import AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS
    # Claim the scan slot so the UPDATE inside _run_cue_candidate_scan has a row.
    db.claim_cue_candidate_scan(podcast['id'], episode_id,
                                stale_seconds=AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS)
    fake_fp = ([0] * 100, 10.0)  # (raw_ints, duration)
    with patch('audio_fingerprinter.AudioFingerprinter._generate_full_fingerprint',
               return_value=fake_fp), \
         patch('audio_fingerprinter.AudioFingerprinter.discover_recurring_spots',
               return_value=fake_candidates), \
         patch('audio_fingerprinter.AudioFingerprinter.discover_cross_episode_cues',
               return_value=[]):
        _run_cue_candidate_scan(
            podcast['id'], episode_id, slug, real_audio,
            similarity=0.9, min_count=2,
        )

    row = db.get_cue_candidate_scan(podcast['id'], episode_id)
    assert row and row['status'] == 'ready'
    candidates = json.loads(row['candidates_json'])
    recurring = [c for c in candidates if c.get('kind') == 'recurring']
    assert recurring, 'expected recurring candidates'
    typed = recurring[0]
    # All 4 occurrences are near ad start boundaries -> should be typed
    assert typed.get('suggestedType') in ('ad_break_boundary', 'ad_break_start', 'ad_break_end'), \
        f"expected an ad type, got {typed.get('suggestedType')!r}"
    assert typed.get('adBoundaryHits') is not None
    assert typed.get('boundaryAffinity') is not None
    assert typed.get('affinitySource') == 'episode'
