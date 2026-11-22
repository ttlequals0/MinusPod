"""In-episode twin search: a cut ad's other copies in the same episode become additional markers."""
import os
import random
import tempfile
from unittest.mock import MagicMock, patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='episode_repeat_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from ad_validator import AdValidator
from audio_fingerprinter import AudioFingerprinter, find_episode_repeats
from config import REPEAT_STAGE
from main_app.pipeline import detect

IPS = 8.0
DURATION = 300.0
KW = dict(strictness=0.65, gap_tolerance_s=3.5)


def _rand_ints(rng, n):
    return [rng.getrandbits(32) - (1 << 31) for _ in range(n)]


def _plant(episode, pattern, at_s, rng=None, flips=0):
    idx = int(at_s * IPS)
    for j, v in enumerate(pattern):
        for _ in range(flips):
            v ^= 1 << rng.randrange(32)
        episode[idx + j] = v


def _episode(copies, pattern_len_s=30, seed=1):
    """Random 300 s episode with the same spot planted (noisily) at each start in ``copies``."""
    rng = random.Random(seed)
    ep = _rand_ints(rng, int(DURATION * IPS))
    spot = _rand_ints(rng, int(pattern_len_s * IPS))
    for i, at in enumerate(copies):
        _plant(ep, spot, at, rng, flips=0 if i == 0 else 1)
    return ep, spot, rng


def _find(ep, spans, blocked=(), **over):
    return find_episode_repeats(ep, DURATION, spans, blocked, **{**KW, **over})


def test_twin_found_from_source_span():
    ep, _, _ = _episode([40, 200])
    found = _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0)])
    assert len(found) == 1
    index, match = found[0]
    assert index == 0
    assert abs(match.start - 200.0) < 0.5 and abs(match.end - 230.0) < 0.5
    assert match.similarity >= 0.85 and match.coverage >= 0.7


def test_twin_span_is_the_source_span_projected_over_the_match():
    ep, spot, rng = _episode([40])
    _plant(ep, spot, 200.0, rng, flips=1)
    # The last 2 s of the copy is different audio, so the matched run stops short of the copy's end.
    tail = int(2 * IPS)
    for j in range(len(spot) - tail, len(spot)):
        ep[int(200.0 * IPS) + j] = rng.getrandbits(32) - (1 << 31)
    (_, match), = _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0)])
    assert match.end - match.start == 30.0
    assert abs(match.start - 200.0) < 0.2


def test_no_twin_when_only_half_the_source_repeats():
    ep, spot, rng = _episode([40])
    _plant(ep, spot[:len(spot) // 2], 200.0, rng, flips=1)
    assert _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0)]) == []


def test_twin_overlapping_a_known_ad_is_dropped():
    ep, _, _ = _episode([40, 200])
    assert _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0), (195.0, 225.0)]) == []


def test_source_alone_finds_nothing():
    ep, _, _ = _episode([40])
    assert _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0)]) == []


def test_two_sources_matching_each_other_yield_no_twin():
    ep, _, _ = _episode([40, 200])
    spans = [(40.0, 70.0), (200.0, 230.0)]
    assert _find(ep, spans, blocked=spans) == []


def test_short_jingle_inside_a_longer_source_is_not_a_twin():
    ep, spot, rng = _episode([40])
    _plant(ep, spot[:int(8 * IPS)], 200.0, rng, flips=1)
    assert _find(ep, [(40.0, 70.0)], blocked=[(40.0, 70.0)]) == []


# --- AudioFingerprinter.find_in_episode_repeats ---------------------------------------------------

def _fingerprinter(ints, duration=DURATION, enabled=True):
    db = MagicMock()
    db.get_setting_bool.return_value = enabled
    db.get_setting_float.side_effect = lambda key, default=None: default
    fp = AudioFingerprinter(db=db)
    fp._fpcalc_path = '/usr/bin/fpcalc'
    fp._generate_full_fingerprint = MagicMock(return_value=(ints, duration))
    return fp


def test_method_returns_twins_with_source_index():
    ep, _, _ = _episode([40, 200])
    fp = _fingerprinter(ep)
    found = fp.find_in_episode_repeats('/a.mp3', [(40.0, 70.0)], [(40.0, 70.0)], DURATION)
    assert [(i, round(m.start)) for i, m in found] == [(0, 200)]


def test_method_disabled_fingerprint_stage_does_no_work():
    ep, _, _ = _episode([40, 200])
    fp = _fingerprinter(ep, enabled=False)
    assert fp.find_in_episode_repeats('/a.mp3', [(40.0, 70.0)], [], DURATION) == []
    fp._generate_full_fingerprint.assert_not_called()


def test_method_fpcalc_failure_returns_nothing():
    fp = _fingerprinter([])
    fp._generate_full_fingerprint.return_value = None
    assert fp.find_in_episode_repeats('/a.mp3', [(40.0, 70.0)], [], DURATION) == []


def test_method_without_fpcalc_returns_nothing():
    fp = _fingerprinter([])
    fp._fpcalc_path = None
    assert fp.find_in_episode_repeats('/a.mp3', [(40.0, 70.0)], [], DURATION) == []


# --- detect._find_episode_repeats ---------------------------------------------------------------

def _ad(start, end, **kw):
    return {'start': start, 'end': end, 'confidence': 0.95, 'sponsor': 'Acme',
            'category': 'sponsor', 'reason': 'Acme read', 'detection_stage': 'claude', **kw}


class _Match:
    def __init__(self, start, end, similarity=0.96, coverage=0.98):
        self.start, self.end, self.similarity, self.coverage = start, end, similarity, coverage


def _stage(ads, existing=None, matches=None, audio_path='/a.mp3', actions=None, raises=None):
    fingerprinter = MagicMock()
    if raises:
        fingerprinter.find_in_episode_repeats.side_effect = raises
    else:
        fingerprinter.find_in_episode_repeats.return_value = matches if matches is not None else []
    detector = MagicMock(audio_fingerprinter=fingerprinter)
    with patch.object(detect, 'ad_detector', detector):
        out = detect._find_episode_repeats(
            's', 'e', ads, existing if existing is not None else ads, audio_path, DURATION, actions or {})
    return out, fingerprinter


def test_stage_builds_marker_inheriting_category_and_sponsor():
    src = _ad(4000.0, 4030.0)
    twins, fp = _stage([src], matches=[(0, _Match(2000.0, 2029.5))])
    assert len(twins) == 1
    twin = twins[0]
    assert twin['detection_stage'] == REPEAT_STAGE
    assert (twin['start'], twin['end']) == (2000.0, 2029.5)
    assert twin['sponsor'] == 'Acme' and twin['category'] == 'sponsor'
    assert twin['confidence'] == 0.96
    assert 'Repeats the ad at 1:06:40' in twin['reason']
    spans = fp.find_in_episode_repeats.call_args.args[1]
    assert spans == [(4000.0, 4030.0)]


def test_stage_skips_held_and_out_of_range_sources():
    held = _ad(100.0, 130.0, held_for_review=True, hold_reason='no_cue_evidence')
    short = _ad(200.0, 205.0)
    long = _ad(300.0, 600.0)
    twins, fp = _stage([held, short, long])
    assert twins == []
    fp.find_in_episode_repeats.assert_not_called()


def test_stage_skips_keep_action_sources():
    twins, fp = _stage([_ad(100.0, 130.0)], actions={'sponsor': 'keep'})
    assert twins == []
    fp.find_in_episode_repeats.assert_not_called()


def test_stage_blocks_against_all_existing_markers():
    src = _ad(4000.0, 4030.0)
    rejected = _ad(1000.0, 1030.0)
    _, fp = _stage([src], existing=[src, rejected])
    assert set(fp.find_in_episode_repeats.call_args.args[2]) == {(4000.0, 4030.0), (1000.0, 1030.0)}


def test_stage_with_no_markers_is_a_noop():
    twins, fp = _stage([])
    assert twins == []
    fp.find_in_episode_repeats.assert_not_called()


def test_refine_with_no_ads_never_searches():
    assert _refine([], []) == ([], [])


def test_stage_without_audio_path_does_nothing():
    twins, fp = _stage([_ad(100.0, 130.0)], audio_path=None)
    assert twins == []
    fp.find_in_episode_repeats.assert_not_called()


def test_stage_failure_is_not_fatal():
    twins, _ = _stage([_ad(100.0, 130.0)], raises=RuntimeError('fpcalc exploded'))
    assert twins == []


# --- _refine_and_validate wiring ----------------------------------------------------------------

def _refine(all_ads, matches, fp_corrections=()):
    fingerprinter = MagicMock()
    fingerprinter.find_in_episode_repeats.return_value = matches
    detector = MagicMock(audio_fingerprinter=fingerprinter)
    with patch.object(detect, 'db', MagicMock()), patch.object(detect, 'storage', MagicMock()), \
            patch.object(detect, 'ad_detector', detector), \
            patch.object(detect, '_build_validator',
                         side_effect=lambda *a, **k: AdValidator(
                             DURATION, a[1], a[2], false_positive_corrections=list(fp_corrections))):
        return detect._refine_and_validate(
            's', 'e', all_ads, [], '/a.mp3', '', DURATION, 0.8, 'Show',
            corrections=(list(fp_corrections), []), apply_heuristic_rolls=False, segment_actions={})


def test_twin_is_validated_and_cut_in_the_same_run():
    src = _ad(200.0, 230.0, confidence=0.97)
    to_remove, all_ads = _refine([src], [(0, _Match(100.0, 129.6))])
    twins = [a for a in to_remove if a['detection_stage'] == REPEAT_STAGE]
    assert len(twins) == 1
    assert twins[0]['was_cut'] is True and 'validation' in twins[0]
    assert twins[0] in all_ads
    assert [a['start'] for a in to_remove] == sorted(a['start'] for a in to_remove)


def test_twins_get_the_standard_boundary_refinement():
    src = _ad(200.0, 230.0, confidence=0.97)
    with patch.object(detect, '_refine_boundaries', wraps=detect._refine_boundaries) as refine:
        _refine([src], [(0, _Match(100.0, 129.6))])
    twin_calls = [c for c in refine.call_args_list
                  if c.args[0] and c.args[0][0].get('detection_stage') == REPEAT_STAGE]
    assert len(twin_calls) == 1


def test_twin_on_a_user_rejected_region_is_not_cut():
    src = _ad(200.0, 230.0, confidence=0.97)
    fp = [{'start': 100.0, 'end': 130.0}]
    to_remove, all_ads = _refine([src], [(0, _Match(100.0, 129.6))], fp_corrections=fp)
    assert [a for a in to_remove if a['detection_stage'] == REPEAT_STAGE] == []
    assert all(a.get('was_cut') is False for a in all_ads if a['detection_stage'] == REPEAT_STAGE)


# --- whole-file fingerprint reuse -----------------------------------------------------------------

def _counting_fingerprinter():
    fp = AudioFingerprinter(db=None)
    fp._fpcalc_path = '/usr/bin/fpcalc'
    fp._run_full_fingerprint = MagicMock(side_effect=lambda path, *a, **k: ([1, 2, 3], 1.0))
    return fp


def test_same_file_is_fingerprinted_once(tmp_path):
    audio = tmp_path / 'ep.mp3'
    audio.write_bytes(b'a' * 10)
    fp = _counting_fingerprinter()
    assert fp._generate_full_fingerprint(str(audio)) == fp._generate_full_fingerprint(str(audio))
    assert fp._run_full_fingerprint.call_count == 1


def test_other_path_or_changed_file_is_not_a_cache_hit(tmp_path):
    first, processed = tmp_path / 'ep.mp3', tmp_path / 'ep_processed.mp3'
    first.write_bytes(b'a' * 10)
    processed.write_bytes(b'a' * 10)
    fp = _counting_fingerprinter()
    fp._generate_full_fingerprint(str(first))
    fp._generate_full_fingerprint(str(processed))
    assert fp._run_full_fingerprint.call_count == 2
    first.write_bytes(b'b' * 20)
    fp._generate_full_fingerprint(str(first))
    assert fp._run_full_fingerprint.call_count == 3


def test_failed_fingerprint_is_not_cached(tmp_path):
    audio = tmp_path / 'ep.mp3'
    audio.write_bytes(b'a' * 10)
    fp = _counting_fingerprinter()
    fp._run_full_fingerprint.side_effect = [None, ([1], 1.0)]
    assert fp._generate_full_fingerprint(str(audio)) is None
    assert fp._generate_full_fingerprint(str(audio)) == ([1], 1.0)
