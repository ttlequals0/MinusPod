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


def _create(db, podcast_id, cue_type='ad_break_boundary', scope='podcast',
            network_id=None, enabled=True, seed=0):
    mfcc_blob, pcm_blob = _blobs(seed)
    tid = db.create_cue_template(
        podcast_id=podcast_id,
        cue_type=cue_type,
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
        podcast_id=pid, cue_type='ad_break_start', source_episode_id='ep-9',
        source_offset_s=3.0, duration_s=0.4, sample_rate=16000,
        n_coeffs=N_COEFFS, mfcc_blob=mfcc_blob, pcm_blob=pcm_blob,
        pcm_sample_rate=16000,
    )
    row = temp_db.get_cue_template(tid)
    assert row['cue_type'] == 'ad_break_start'
    assert row['label'] == 'ad-break start'  # derived from the type
    assert row['scope'] == 'podcast'  # default
    assert row['enabled'] == 1
    assert bytes(row['mfcc_blob']) == mfcc_blob
    assert bytes(row['pcm_blob']) == pcm_blob
    assert row['pcm_sample_rate'] == 16000


def test_metadata_excludes_blobs(temp_db):
    pid = temp_db.create_podcast('show-b', 'http://x/b.xml', 'Show B')
    _create(temp_db, pid, cue_type='ad_break_end')
    meta = temp_db.list_cue_templates_metadata(pid)
    assert len(meta) == 1
    assert meta[0]['cue_type'] == 'ad_break_end'
    assert meta[0]['label'] == 'ad-break end'
    assert 'mfcc_blob' not in meta[0]
    assert 'pcm_blob' not in meta[0]


def test_update_changes_type_and_label(temp_db):
    pid = temp_db.create_podcast('show-c', 'http://x/c.xml', 'Show C')
    tid = _create(temp_db, pid, cue_type='ad_break_start')
    # Changing the type resets the derived label and can also flip enabled.
    assert temp_db.update_cue_template(tid, cue_type='show_intro', enabled=False)
    row = temp_db.get_cue_template(tid)
    assert row['cue_type'] == 'show_intro'
    assert row['label'] == 'show intro'
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
    _create(temp_db, pid_a)
    _create(temp_db, pid_b)
    rows = temp_db.list_active_cue_templates_for_feed(pid_a)
    assert len(rows) == 1
    assert rows[0]['podcast_id'] == pid_a


def test_active_resolution_includes_network_scope_most_specific_first(temp_db):
    # Two shows on the same network. A network-scope template captured on show G
    # must also apply to show H, and a feed's own podcast-scope cue sorts first.
    pid_g = temp_db.create_podcast('show-g', 'http://x/g.xml', 'Show G')
    pid_h = temp_db.create_podcast('show-h', 'http://x/h.xml', 'Show H')
    temp_db.update_podcast('show-g', network_id='net-1')
    temp_db.update_podcast('show-h', network_id='net-1')

    _create(temp_db, pid_g, cue_type='ad_break_end', scope='network', network_id='net-1')
    _create(temp_db, pid_h, cue_type='ad_break_start', scope='podcast', seed=1)

    rows = temp_db.list_active_cue_templates_for_feed(pid_h)
    assert {r['scope'] for r in rows} == {'podcast', 'network'}
    # Most-specific (podcast scope) first.
    assert rows[0]['scope'] == 'podcast'
    assert rows[0]['cue_type'] == 'ad_break_start'


def test_active_resolution_uses_network_id_override(temp_db):
    # Same-creator feeds with no auto-detected network: a manual
    # network_id_override links them, so a network-scope template captured on
    # one feed must apply to the other.
    pid_k = temp_db.create_podcast('show-k', 'http://x/k.xml', 'Show K')
    pid_l = temp_db.create_podcast('show-l', 'http://x/l.xml', 'Show L')
    temp_db.update_podcast('show-k', network_id_override='creator-x')
    temp_db.update_podcast('show-l', network_id_override='creator-x')

    _create(temp_db, pid_k, scope='network', network_id='creator-x')

    rows = temp_db.list_active_cue_templates_for_feed(pid_l)
    assert [r['scope'] for r in rows] == ['network']
    assert rows[0]['network_id'] == 'creator-x'


def test_ui_listing_includes_sibling_network_templates(temp_db):
    # Two feeds on one manual network. A network template captured on feed A
    # must appear in feed B's UI listing (marked not-owned by the API layer),
    # while a podcast-scope template on A must not leak to B.
    pid_a = temp_db.create_podcast('ui-a', 'http://x/uia.xml', 'UI A')
    pid_b = temp_db.create_podcast('ui-b', 'http://x/uib.xml', 'UI B')
    temp_db.update_podcast('ui-a', network_id_override='creator-z')
    temp_db.update_podcast('ui-b', network_id_override='creator-z')

    net_tid = _create(temp_db, pid_a, scope='network', network_id='creator-z')
    _create(temp_db, pid_a, cue_type='ad_break_start', scope='podcast', seed=1)
    own_b = _create(temp_db, pid_b, cue_type='ad_break_end', scope='podcast', seed=2)

    b_rows = temp_db.list_cue_templates_for_feed_ui(pid_b)
    ids = [r['id'] for r in b_rows]
    assert net_tid in ids          # sibling network template is visible
    assert own_b in ids            # feed B's own template is visible
    assert len(b_rows) == 2        # A's podcast-scope template did not leak
    # Own rows sort ahead of shared network rows.
    assert b_rows[0]['podcast_id'] == pid_b


def test_active_resolution_blank_override_falls_back_to_network_id(temp_db):
    # A blank override stored as '' (not NULL) must not shadow the auto-detected
    # network_id; the feed still resolves its network-scope templates.
    pid_m = temp_db.create_podcast('show-m', 'http://x/m.xml', 'Show M')
    pid_n = temp_db.create_podcast('show-n', 'http://x/n.xml', 'Show N')
    temp_db.update_podcast('show-m', network_id='net-9')
    temp_db.update_podcast('show-n', network_id='net-9', network_id_override='')

    _create(temp_db, pid_m, scope='network', network_id='net-9')

    rows = temp_db.list_active_cue_templates_for_feed(pid_n)
    assert [r['network_id'] for r in rows] == ['net-9']


def test_active_resolution_excludes_disabled_and_other_networks(temp_db):
    pid = temp_db.create_podcast('show-i', 'http://x/i.xml', 'Show I')
    temp_db.update_podcast('show-i', network_id='net-2')
    _create(temp_db, pid, enabled=False)
    # Network template on a different network must not leak in.
    other = temp_db.create_podcast('show-j', 'http://x/j.xml', 'Show J')
    _create(temp_db, other, scope='network', network_id='net-3')
    assert temp_db.list_active_cue_templates_for_feed(pid) == []
