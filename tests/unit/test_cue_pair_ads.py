"""Unit tests for cue-pair ad synthesis (#350)."""
from ad_detector.cue_pair_ads import (
    synthesize_ads_from_cue_pairs as _synth_impl,
)
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def synthesize_ads_from_cue_pairs(*args, **kwargs):
    """Return just the ad list (drop skip diagnostics) for the ad-shape tests.

    Phase 6 changed the real function to return (ads, skip_diagnostics); these
    older tests only assert on the ads. The diagnostics contract has its own
    dedicated tests below that call _synth_impl directly.
    """
    ads, _ = _synth_impl(*args, **kwargs)
    return ads


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def _cue(start, end, conf=0.9, label='ding', template_id=1):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue',
        confidence=conf,
        details={'source': 'template', 'label': label, 'template_id': template_id},
    )


def test_pair_synthesizes_ad_when_no_existing_ad_overlaps():
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(220.0, 220.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.55
    assert ads[0]['end'] == 219.95
    assert ads[0]['reason'] == 'audio_cue_pair'
    assert ads[0]['detection_stage'] == 'cue_pair'
    assert ads[0]['cue_pair']['start']['label'] == 'ding'


def test_pair_skipped_when_existing_ad_covers_it():
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(220.0, 220.5),
    )
    existing = [{'start': 105.0, 'end': 215.0}]
    ads = synthesize_ads_from_cue_pairs(existing, result)
    # Existing ad covers the pair; no synthesis.
    assert len(ads) == 1
    assert ads[0] is existing[0]


def test_pair_within_min_break_skipped():
    # 1.5 s gap is too short to be a break.
    result = _result_with(_cue(100.0, 100.5), _cue(102.0, 102.5))
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_pair_beyond_max_break_skipped():
    result = _result_with(_cue(100.0, 100.5), _cue(800.0, 800.5))
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_low_confidence_cue_excluded():
    result = _result_with(
        _cue(100.0, 100.5, conf=0.7),
        _cue(220.0, 220.5, conf=0.95),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert ads == []


def test_three_cues_form_one_pair_then_orphan():
    # Cues 1+2 form the break, cue 3 has no partner -> ignored.
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(200.0, 200.5),
        _cue(900.0, 900.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.55
    assert ads[0]['end'] == 199.95


def test_four_cues_form_two_pairs():
    # Two complete brackets: (1,2) and (3,4).
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(200.0, 200.5),
        _cue(900.0, 900.5),
        _cue(1100.0, 1100.5),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 2
    assert ads[0]['start'] == 100.55
    assert ads[1]['start'] == 900.55


def test_no_result_returns_input_unchanged():
    existing = [{'start': 100.0, 'end': 160.0}]
    ads = synthesize_ads_from_cue_pairs(existing, None)
    assert ads == existing


# ---------------------------------------------------------------------------
# Role gating (only opener -> closer pairs synthesize an ad)
# _typed_cue is the module-level helper defined further down; Python resolves
# it at call time, so these tests already use that definition.
# ---------------------------------------------------------------------------

def test_two_start_cues_do_not_pair():
    # Two break-entry stingers must NOT bracket the show content between two
    # separate breaks: a 'start' role can open but never close a pair.
    result = _result_with(
        _typed_cue(100.0, 100.5, 'start'),
        _typed_cue(220.0, 220.5, 'start'),
    )
    assert synthesize_ads_from_cue_pairs([], result) == []


def test_start_then_end_pairs():
    result = _result_with(
        _typed_cue(100.0, 100.5, 'start'),
        _typed_cue(220.0, 220.5, 'end'),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1
    assert ads[0]['detection_stage'] == 'cue_pair'


def test_intro_outro_cues_never_pair():
    result = _result_with(
        _typed_cue(100.0, 100.5, 'non_ad'),
        _typed_cue(220.0, 220.5, 'non_ad'),
    )
    assert synthesize_ads_from_cue_pairs([], result) == []


# ---------------------------------------------------------------------------
# Source gating + synthesized-span dedup (over-flagging fix)
# ---------------------------------------------------------------------------

def _spectral_cue(start, end, conf=0.9):
    # Spectral-fallback cues carry no 'source' key (band-pass burst detector).
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'prominence_db': 8.0, 'baseline_lufs': -30.0,
                 'band_hz': [800, 2000]},
    )


def test_spectral_cues_never_synthesize():
    # The over-flagging bug: on a no-template feed spectral cues paired into
    # dozens of overlapping false ads. Source-gating yields zero synthesis even
    # for a dense, perfectly pairable cluster.
    result = _result_with(
        _spectral_cue(100.0, 100.5),
        _spectral_cue(200.0, 200.5),
        _spectral_cue(300.0, 300.5),
        _spectral_cue(400.0, 400.5),
    )
    assert synthesize_ads_from_cue_pairs([], result) == []


def test_duplicate_cues_do_not_make_overlapping_ads():
    # Near-duplicate template cues (cross-chunk match overlap) must not mint
    # overlapping synthetic ads: each synthesized span dedups against the ones
    # already produced, not just the input LLM ads.
    result = _result_with(
        _cue(100.0, 100.5),
        _cue(100.3, 100.8),
        _cue(200.0, 200.5),
        _cue(200.3, 200.8),
    )
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1


# ---------------------------------------------------------------------------
# Short-episode max-break-fraction guard (C4 phantom-ad backstop)
# ---------------------------------------------------------------------------

def test_pair_rejected_when_span_exceeds_episode_fraction():
    # On a short episode a pair within the absolute 480s cap can still bracket
    # most of the show. The fraction guard rejects it: gap ~119.5s > 0.5 * 200s.
    result = _result_with(_cue(100.0, 100.5), _cue(220.0, 220.5))
    assert synthesize_ads_from_cue_pairs([], result, total_duration=200.0) == []


def test_pair_kept_when_span_within_episode_fraction():
    # Same pair on a long episode: 119.5s is well under 0.5 * 1000s, so it stands.
    result = _result_with(_cue(100.0, 100.5), _cue(220.0, 220.5))
    ads = synthesize_ads_from_cue_pairs([], result, total_duration=1000.0)
    assert len(ads) == 1


def test_fraction_guard_disabled_by_zero_total_duration():
    # total_duration=0 (unknown) leaves only the absolute cap in force.
    result = _result_with(_cue(100.0, 100.5), _cue(220.0, 220.5))
    assert len(synthesize_ads_from_cue_pairs([], result, total_duration=0.0)) == 1


# ---------------------------------------------------------------------------
# LLM-ad-edge orientation (boundary cues demoted to opener/closer by phase)
# ---------------------------------------------------------------------------

def _ad(start, end):
    return {'start': start, 'end': end}


def test_orientation_kills_content_phantom_on_exit_first_feed():
    # apparle's shape: opening ad, then exit cue, then detected break1
    # (entry+exit cues), then detected break2. Without orientation the leading
    # exit cue pairs with break1's entry cue over CONTENT -> phantom.
    result = _result_with(
        _cue(100.0, 100.5),   # C0 exit (content resumes after opening ad)
        _cue(300.0, 300.5),   # C1 entry of break1
        _cue(360.0, 360.5),   # C2 exit of break1
        _cue(600.0, 600.5),   # C3 entry of break2
        _cue(660.0, 660.5),   # C4 exit of break2
    )
    llm_ads = [_ad(0.0, 90.0), _ad(310.0, 358.0), _ad(610.0, 658.0)]

    # Orientation OFF: leading exit cue opens a pair over content -> phantom(s).
    off = synthesize_ads_from_cue_pairs(list(llm_ads), result, orient_window_s=0.0)
    synth_off = [a for a in off if a.get('detection_stage') == 'cue_pair']
    assert len(synth_off) >= 1

    # Orientation ON (default): no cue-pair ad is synthesized over content; the
    # real breaks are already covered by the LLM ads.
    on = synthesize_ads_from_cue_pairs(list(llm_ads), result)
    synth_on = [a for a in on if a.get('detection_stage') == 'cue_pair']
    assert synth_on == []


def test_orientation_preserves_a_genuinely_missed_break():
    # Two cues bracket a MISSED break with no nearby LLM ad. Orientation is
    # active (an unrelated far-away LLM ad exists) but must not suppress it.
    result = _result_with(_cue(300.0, 300.5), _cue(360.0, 360.5))
    llm_ads = [_ad(1000.0, 1100.0)]
    ads = synthesize_ads_from_cue_pairs(llm_ads, result)
    synth = [a for a in ads if a.get('detection_stage') == 'cue_pair']
    assert len(synth) == 1
    assert synth[0]['start'] == 300.55 and synth[0]['end'] == 359.95


def test_orientation_noop_without_llm_ads():
    result = _result_with(_cue(100.0, 100.5), _cue(220.0, 220.5))
    ads = synthesize_ads_from_cue_pairs([], result)
    assert len(ads) == 1  # greedy fallback unchanged when there are no LLM ads


def test_orientation_only_demotes_leading_exit_not_mid_episode():
    from ad_detector.cue_pair_ads import _orient_cues, _Cue

    def C(start):
        return _Cue(start=start, end=start + 0.5, confidence=0.9,
                    label='x', template_id=1, role='boundary')

    c0, c1, ca, cb = C(100), C(300), C(500), C(560)
    cues = [c0, c1, ca, cb]
    ads = [{'start': 0, 'end': 90}, {'start': 305, 'end': 360}, {'start': 800, 'end': 860}]
    _orient_cues(cues, ads, 20.0)
    assert c0.effective_role == 'end'       # leading exit demoted (phantom guard)
    assert c1.effective_role == 'boundary'  # entry-side demotion removed
    assert ca.effective_role == 'boundary'  # mid-episode cue never demoted
    assert cb.effective_role == 'boundary'


# ---------------------------------------------------------------------------
# Skip diagnostics (#350 Phase 6): the second return value maps
# (template_id, round(start, 3)) -> reason for every eligible-but-unpaired cue.
# ---------------------------------------------------------------------------

def _typed_cue(start, end, role='boundary', conf=0.9, template_id=1, cue_type='ad_break_boundary'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'label': 'ding', 'template_id': template_id,
                 'role': role, 'cue_type': cue_type},
    )


def test_diag_below_pair_confidence():
    # Both cues would pair, but one is below the confidence floor and is dropped
    # before pairing; it is reported as below_pair_confidence.
    result = _result_with(
        _typed_cue(100.0, 100.5, conf=0.9),
        _typed_cue(220.0, 220.5, conf=0.5),   # below default 0.85
    )
    ads, diag = _synth_impl([], result)
    # No pair forms (only one qualifying cue).
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag[(1, 220.0)] == 'below_pair_confidence'


def test_diag_covered_by_existing_ad():
    result = _result_with(_typed_cue(100.0, 100.5), _typed_cue(220.0, 220.5))
    existing = [{'start': 105.0, 'end': 215.0}]
    ads, diag = _synth_impl(existing, result)
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag[(1, 100.0)] == 'covered_by_existing_ad'
    assert diag[(1, 220.0)] == 'covered_by_existing_ad'


def test_diag_no_partner_in_band():
    # Gap 700s > max_break (480s default): no partner in band for the opener.
    result = _result_with(_typed_cue(100.0, 100.5), _typed_cue(800.0, 800.5))
    ads, diag = _synth_impl([], result)
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag[(1, 100.0)] == 'no_partner_in_band'


def test_diag_phase_mismatch_on_wrong_order():
    # An 'end'-role cue cannot open, so as an opener candidate it is a phase
    # mismatch. The trailing 'start'-role cue is start-capable but has no opener
    # before it, so it simply found no partner (not a phase mismatch).
    result = _result_with(
        _typed_cue(100.0, 100.5, role='end', cue_type='ad_break_end'),
        _typed_cue(220.0, 220.5, role='start', cue_type='ad_break_start'),
    )
    ads, diag = _synth_impl([], result)
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag[(1, 100.0)] == 'phase_mismatch'
    assert diag[(1, 220.0)] == 'no_partner_in_band'


def test_diag_trailing_start_cue_is_no_partner_not_phase_mismatch():
    # A trailing start-role cue (last index) is never visited as an opener by the
    # range(len-1) loop, so it falls to post-loop classification. It IS start-
    # capable, so it simply found no partner -> no_partner_in_band, not a phase
    # mismatch. The leading end-role cue cannot open, so it also gets no_partner.
    result = _result_with(
        _typed_cue(100.0, 100.5, role='end', cue_type='ad_break_end'),
        _typed_cue(220.0, 220.5, role='start', cue_type='ad_break_start'),
        _typed_cue(900.0, 900.5, role='start', cue_type='ad_break_start'),
    )
    ads, diag = _synth_impl([], result)
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag[(1, 900.0)] == 'no_partner_in_band'


def test_diag_empty_when_pair_succeeds():
    # A clean pair leaves no unpaired cue, so no skip reasons are reported.
    result = _result_with(_typed_cue(100.0, 100.5), _typed_cue(220.0, 220.5))
    ads, diag = _synth_impl([], result)
    assert any(a.get('detection_stage') == 'cue_pair' for a in ads)
    assert diag == {}


def test_diag_returns_tuple_even_with_no_analysis():
    ads, diag = _synth_impl([{'start': 0, 'end': 1}], None)
    assert ads == [{'start': 0, 'end': 1}]
    assert diag == {}
