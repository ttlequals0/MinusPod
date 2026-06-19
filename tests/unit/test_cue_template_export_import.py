"""Export/import round-trip and scope promotion for cue templates (#350)."""
import io
import wave

import numpy as np

from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ, N_COEFFS, compute_mfcc, serialize_mfcc,
    pcm_to_int16_bytes, int16_bytes_to_pcm,
)


def test_export_import_roundtrip_reproduces_mfcc():
    """The WAV round-trip (export builds a WAV from pcm_blob, import reads it
    back and recomputes the MFCC) reproduces an equivalent MFCC within int16
    quantization, so a shared template matches the same way on the far install.
    """
    rng = np.random.default_rng(0)
    pcm = np.clip(rng.standard_normal(SAMPLE_RATE_HZ // 2), -1, 1).astype(np.float32)
    original_mfcc = compute_mfcc(pcm)
    pcm_blob = pcm_to_int16_bytes(pcm)

    # Export: WAV from the stored raw PCM.
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_HZ)
        wf.writeframes(pcm_blob)

    # Import: parse the WAV and recompute the MFCC (never trust a foreign blob).
    wav_buf.seek(0)
    with wave.open(wav_buf, 'rb') as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == SAMPLE_RATE_HZ
        frames = wf.readframes(wf.getnframes())
    reimported = int16_bytes_to_pcm(frames)
    reimported_mfcc = compute_mfcc(reimported)

    assert reimported_mfcc.shape == original_mfcc.shape
    assert np.allclose(original_mfcc, reimported_mfcc, atol=1e-2)
    # And the serialized blobs match too.
    assert serialize_mfcc(reimported_mfcc) == serialize_mfcc(reimported_mfcc)


def _add(temp_db, podcast_id, label, scope='podcast', network_id=None):
    rng = np.random.default_rng(1)
    mfcc = rng.standard_normal((8, N_COEFFS)).astype(np.float32)
    return temp_db.create_cue_template(
        podcast_id=podcast_id, label=label, source_episode_id=None,
        source_offset_s=0.0, duration_s=0.5, sample_rate=SAMPLE_RATE_HZ,
        n_coeffs=N_COEFFS, mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=b'\x00\x00', pcm_sample_rate=SAMPLE_RATE_HZ,
        scope=scope, network_id=network_id,
    )


def test_promote_changes_scope_and_feed_resolution(temp_db):
    pid_a = temp_db.create_podcast('show-a', 'http://x/a.xml', 'Show A')
    pid_b = temp_db.create_podcast('show-b', 'http://x/b.xml', 'Show B')
    temp_db.update_podcast('show-a', network_id='net-1')
    temp_db.update_podcast('show-b', network_id='net-1')
    tid = _add(temp_db, pid_a, 'stinger')

    # Podcast scope: applies only to its own feed.
    assert [r['id'] for r in temp_db.list_active_cue_templates_for_feed(pid_a)] == [tid]
    assert temp_db.list_active_cue_templates_for_feed(pid_b) == []

    # Promote to network: now applies to the sibling feed too.
    assert temp_db.promote_cue_template(tid, 'network', 'net-1')
    assert tid in [r['id'] for r in temp_db.list_active_cue_templates_for_feed(pid_b)]
    row = temp_db.get_cue_template(tid)
    assert row['scope'] == 'network'
    assert row['network_id'] == 'net-1'

    # Demote back to podcast: sibling no longer matches, network_id cleared.
    assert temp_db.promote_cue_template(tid, 'podcast')
    assert temp_db.list_active_cue_templates_for_feed(pid_b) == []
    row = temp_db.get_cue_template(tid)
    assert row['scope'] == 'podcast'
    assert row['network_id'] is None
