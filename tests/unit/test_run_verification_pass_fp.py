"""End-to-end plumbing test for verification-pass false-positive handling
(issue #183).

Walks the same data flow `_run_verification_pass` walks: take the user's
per-episode rejections in original time, translate them through pass-1
cuts into processed-audio time, hand them to `AdValidator` as
`false_positive_corrections`, and confirm a verification ad overlapping
the mapped region is auto-rejected.

Avoids mocking `_run_verification_pass` itself (it stitches together a
transcriber, an LLM client, the audio_analyzer, and storage) and instead
proves the contract on the seam where the new wiring lives: between the
mapping helper and the validator.
"""

from ad_validator import AdValidator, Decision
from verification_pass import _build_timestamp_map, _map_correction_to_processed


def _segments_for(duration_s: float):
    """Synthesize one transcript segment per second so the validator sees
    `processed_duration` correctly."""
    return [{'start': float(i), 'end': float(i + 1), 'text': ''} for i in range(int(duration_s))]


def test_mapped_fp_correction_rejects_overlapping_verification_ad():
    """Issue #183 regression: a reject at original (451.65, 551.05) plus a
    pass-1 cut [0, 275.7] should auto-reject the verification ad at
    processed-time (169.3, 232.4)."""
    pass1_cuts = [{'start': 0.0, 'end': 275.7}]
    rejects_orig = [{'start': 451.65, 'end': 551.05}]

    ts_map = _build_timestamp_map(pass1_cuts)
    fp_corrections_processed = []
    for c in rejects_orig:
        proc = _map_correction_to_processed(c['start'], c['end'], ts_map)
        if proc is not None:
            fp_corrections_processed.append({'start': proc[0], 'end': proc[1]})

    assert len(fp_corrections_processed) == 1
    assert abs(fp_corrections_processed[0]['start'] - 175.95) < 1e-6
    assert abs(fp_corrections_processed[0]['end'] - 275.35) < 1e-6

    processed_duration = 1056.0
    segments = _segments_for(processed_duration)
    validator = AdValidator(
        processed_duration, segments,
        episode_description=None,
        false_positive_corrections=fp_corrections_processed,
        min_cut_confidence=0.7,
    )

    verification_ads = [
        {'start': 169.3, 'end': 232.4, 'confidence': 0.95,
         'reason': "Missed Coca-Cola sponsor read"},
    ]
    result = validator.validate(verification_ads)

    assert len(result.ads) == 1
    decision = result.ads[0].get('validation', {}).get('decision')
    assert decision == Decision.REJECT.value, (
        f"Expected verification ad to be auto-rejected by mapped FP "
        f"correction, got {decision}. flags={result.ads[0].get('validation', {}).get('flags')}"
    )


def test_no_pass1_cuts_identity_mapping():
    """When pass 1 removed nothing, original-time rejects pass through 1:1."""
    rejects_orig = [{'start': 100.0, 'end': 200.0}]
    ts_map = _build_timestamp_map([])
    fp = []
    for c in rejects_orig:
        proc = _map_correction_to_processed(c['start'], c['end'], ts_map)
        if proc is not None:
            fp.append({'start': proc[0], 'end': proc[1]})

    assert fp == [{'start': 100.0, 'end': 200.0}]

    validator = AdValidator(
        500.0, _segments_for(500.0),
        episode_description=None,
        false_positive_corrections=fp,
        min_cut_confidence=0.7,
    )
    result = validator.validate(
        [{'start': 110.0, 'end': 190.0, 'confidence': 0.9, 'reason': 'x'}]
    )
    assert result.ads[0]['validation']['decision'] == Decision.REJECT.value


def test_correction_inside_pass1_cut_is_dropped():
    """A correction whose entire range was already removed by pass 1 has no
    representation in processed audio and must not be passed to the validator
    (would otherwise carry stale original-time coordinates)."""
    pass1_cuts = [{'start': 100.0, 'end': 300.0}]
    rejects_orig = [{'start': 150.0, 'end': 250.0}]

    ts_map = _build_timestamp_map(pass1_cuts)
    fp_corrections_processed = []
    for c in rejects_orig:
        proc = _map_correction_to_processed(c['start'], c['end'], ts_map)
        if proc is not None:
            fp_corrections_processed.append({'start': proc[0], 'end': proc[1]})

    assert fp_corrections_processed == []
