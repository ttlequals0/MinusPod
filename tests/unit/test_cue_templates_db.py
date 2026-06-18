"""Unit tests for the CueTemplateMixin DB layer (#350).

Covers CRUD, the raw-PCM source-of-truth round-trip, and the two-tier
(podcast / network) scope resolution that drives matcher selection.
"""
import numpy as np

from audio_analysis.cue_features import N_COEFFS, serialize_mfcc, pcm_to_int16_bytes


def _blobs(seed=0, frames=10):
    rng = np.random.default_rng(seed)
    mfcc = rng.standard_normal((frames, N_COEFFS)).astype(np.float32)
    pcm = rng.standard_normal(frames * 160).astype(np.float32)
    return serialize_mfcc(mfcc), pcm_to_int16_bytes(np.clip(pcm, -1, 1))


def _create(db, podcast_id, label='ding', scope='podcast', network_id=None,
            enabled=True, seed=0):
    mfcc_blob, pcm_blob = _blobs(seed)
    tid = db.create_cue_template(
        podcast_id=podcast_id,
        label=label,
        source_episode_id='ep-1',
        source_offset_s=12.5,
        duration_s=0.6,
        sample_rate=16000,
        n_coeffs=N_COEFFS,
        mfcc_blob=mfcc_blob,
        pcm_blob=pcm_blob,
        pcm_sample_rate=16000,
        scope=scope,
        network_id=network_id,
    )
    if not enabled:
        db.update_cue_template(tid, enabled=False)
    return tid


def test_create_get_roundtrip_preserves_blobs(temp_db):
    pid = temp_db.create_podcast('show-a', 'http://x/a.xml', 'Show A')
    mfcc_blob, pcm_blob = _blobs(seed=3)
    tid = temp_db.create_cue_template(
        podcast_id=pid, label='clink', source_episode_id='ep-9',
        source_offset_s=3.0, duration_s=0.4, sample_rate=16000,
        n_coeffs=N_COEFFS, mfcc_blob=mfcc_blob, pcm_blob=pcm_blob,
        pcm_sample_rate=16000,
    )
    row = temp_db.get_cue_template(tid)
    assert row['label'] == 'clink'
    assert row['scope'] == 'podcast'  # default
    assert row['enabled'] == 1
    assert bytes(row['mfcc_blob']) == mfcc_blob
    assert bytes(row['pcm_blob']) == pcm_blob
    assert row['pcm_sample_rate'] == 16000


def test_metadata_excludes_blobs(temp_db):
    pid = temp_db.create_podcast('show-b', 'http://x/b.xml', 'Show B')
    _create(temp_db, pid, label='swoosh')
    meta = temp_db.list_cue_templates_metadata(pid)
    assert len(meta) == 1
    assert meta[0]['label'] == 'swoosh'
    assert 'mfcc_blob' not in meta[0]
    assert 'pcm_blob' not in meta[0]


def test_update_and_delete(temp_db):
    pid = temp_db.create_podcast('show-c', 'http://x/c.xml', 'Show C')
    tid = _create(temp_db, pid, label='old')
    assert temp_db.update_cue_template(tid, label='new', enabled=False)
    row = temp_db.get_cue_template(tid)
    assert row['label'] == 'new'
    assert row['enabled'] == 0
    assert temp_db.delete_cue_template(tid)
    assert temp_db.get_cue_template(tid) is None


def test_active_list_tracks_enabled_flag(temp_db):
    pid = temp_db.create_podcast('show-d', 'http://x/d.xml', 'Show D')
    assert temp_db.list_active_cue_templates_for_feed(pid) == []
    tid = _create(temp_db, pid)
    assert len(temp_db.list_active_cue_templates_for_feed(pid)) == 1
    temp_db.update_cue_template(tid, enabled=False)
    assert temp_db.list_active_cue_templates_for_feed(pid) == []


def test_active_resolution_podcast_scope_only(temp_db):
    pid_a = temp_db.create_podcast('show-e', 'http://x/e.xml', 'Show E')
    pid_b = temp_db.create_podcast('show-f', 'http://x/f.xml', 'Show F')
    _create(temp_db, pid_a, label='a-cue')
    _create(temp_db, pid_b, label='b-cue')
    rows = temp_db.list_active_cue_templates_for_feed(pid_a)
    assert [r['label'] for r in rows] == ['a-cue']


def test_active_resolution_includes_network_scope_most_specific_first(temp_db):
    # Two shows on the same network. A network-scope template captured on show G
    # must also apply to show H, and a feed's own podcast-scope cue sorts first.
    pid_g = temp_db.create_podcast('show-g', 'http://x/g.xml', 'Show G')
    pid_h = temp_db.create_podcast('show-h', 'http://x/h.xml', 'Show H')
    temp_db.update_podcast('show-g', network_id='net-1')
    temp_db.update_podcast('show-h', network_id='net-1')

    _create(temp_db, pid_g, label='net-cue', scope='network', network_id='net-1')
    _create(temp_db, pid_h, label='h-own', scope='podcast', seed=1)

    rows = temp_db.list_active_cue_templates_for_feed(pid_h)
    labels = [r['label'] for r in rows]
    assert set(labels) == {'h-own', 'net-cue'}
    # Most-specific (podcast scope) first.
    assert labels[0] == 'h-own'


def test_active_resolution_excludes_disabled_and_other_networks(temp_db):
    pid = temp_db.create_podcast('show-i', 'http://x/i.xml', 'Show I')
    temp_db.update_podcast('show-i', network_id='net-2')
    _create(temp_db, pid, label='disabled-own', enabled=False)
    # Network template on a different network must not leak in.
    other = temp_db.create_podcast('show-j', 'http://x/j.xml', 'Show J')
    _create(temp_db, other, label='other-net', scope='network', network_id='net-3')
    assert temp_db.list_active_cue_templates_for_feed(pid) == []
