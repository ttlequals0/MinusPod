"""Whisper repetition-loop guard: loop runs and the validator rule."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision
from config import HOLD_REASON_TRANSCRIPT_LOOP
from transcriber.segments import drop_loop_cues, find_loop_runs

FIXTURE = os.path.join(os.path.dirname(__file__), '..', 'fixtures', 'transcript_loop_cues.json')

SPONSOR_READ = [
    'This episode is brought to you by Acme Widgets.',
    'Acme Widgets makes the best widgets, go to acmewidgets.example/show for ten percent off.',
    'Use code SHOW at checkout, that is acmewidgets.example/show.',
]


def _cues(rows):
    return [{'start': a, 'end': b, 'text': t} for a, b, t in rows]


def _loop(start, n, step=1.0):
    return _cues([(start + i * step, start + (i + 1) * step,
                   'line A' if i % 2 == 0 else 'line B') for i in range(n)])


def _speech(start, n):
    words = ['alpha', 'bravo', 'charlie', 'delta', 'echo', 'foxtrot']
    return _cues([(start + i * 3.0, start + i * 3.0 + 2.5,
                   f'{words[i % 6]} sentence number {i * 7919} about the show')
                  for i in range(n)])


def _read(start):
    return _cues([(start + i * 5.0, start + i * 5.0 + 4.5, t) for i, t in enumerate(SPONSOR_READ)])


def _validate(segments, start, end, **fields):
    ad = {'start': start, 'end': end, 'confidence': 0.95, 'category': 'sponsor',
          'detection_stage': 'verification', 'reason': 'sponsor read', **fields}
    return AdValidator(episode_duration=2600.0, segments=segments).validate([ad]).ads


# --- detection ---

def test_synthetic_loop_is_one_run():
    assert find_loop_runs(_loop(100.0, 40)) == [(100.0, 140.0, 40)]


def test_identical_cues_loop():
    segs = _cues([(i, i + 1.0, 'watch the full episode') for i in range(10)])
    assert len(find_loop_runs(segs)) == 1


def test_clean_transcript_has_no_runs():
    segs = _speech(0.0, 200)
    assert find_loop_runs(segs) == []
    assert drop_loop_cues(segs, []) is segs


def test_short_run_and_long_cues_are_not_loops():
    assert find_loop_runs(_loop(0.0, 7)) == []
    long_cues = _cues([(i * 6.0, i * 6.0 + 5.0, 'same words again') for i in range(12)])
    assert find_loop_runs(long_cues) == []


def test_fixture_run_covers_the_loop():
    with open(FIXTURE) as f:
        segs = _cues(json.load(f))
    runs = find_loop_runs(segs)
    assert runs and runs[-1][0] <= 692.0 and runs[-1][1] >= 1229.0
    assert not any(c['text'].startswith('show line') for c in drop_loop_cues(segs, runs)
                   if c['start'] > 700 and c['end'] < 1229)


# --- the guard ---

def test_sponsor_read_under_a_loop_is_cut():
    """A sponsor read followed by a long loop over the ad audio stays one cut."""
    segs = _speech(0.0, 40) + _read(120.0) + _loop(135.0, 300) + _speech(440.0, 20)
    ad, = _validate(segs, 120.0, 435.0)
    assert (ad['start'], ad['end']) == (120.0, 435.0)
    assert not ad.get('held_for_review')
    assert ad['validation']['decision'] == Decision.ACCEPT.value


def test_loop_before_the_read_is_trimmed_away():
    """A loop that does not touch the evidence is no part of the cut."""
    segs = _speech(0.0, 10) + _loop(30.0, 60) + _speech(90.0, 10) + _read(120.0) + _loop(135.0, 100)
    ad, = _validate(segs, 30.0, 235.0)
    assert ad['start'] >= 90.0 and ad['start'] <= 120.0
    assert ad['end'] == 235.0
    assert not ad.get('held_for_review')


def test_show_speech_loop_without_ad_signal_raises_no_hold():
    """A 483 s loop over story speech is dropped quietly, not queued for review."""
    segs = _speech(0.0, 100) + _loop(1948.0, 483) + _speech(2440.0, 10)
    ads = _validate(segs, 1948.0, 2431.0, detection_stage='transcript_differential', confidence=0.6)
    for ad in ads:
        assert not ad.get('held_for_review')
        assert ad.get('hold_reason') is None
        assert ad['validation']['decision'] == Decision.REJECT.value


def test_weak_sponsor_mention_in_a_loop_still_holds():
    segs = _speech(0.0, 20) + _cues([(100.0, 103.0, 'we should thank Acme Widgets for lending the ladder')]) \
        + _loop(104.0, 200) + _speech(310.0, 10)
    ad, = _validate(segs, 100.0, 304.0, sponsor='Acme Widgets')
    assert ad['held_for_review'] and ad['hold_reason'] == HOLD_REASON_TRANSCRIPT_LOOP
    assert ad['validation']['decision'] == Decision.REVIEW.value


def test_measured_evidence_is_exempt():
    segs = _speech(0.0, 20) + _loop(100.0, 200) + _speech(310.0, 10)
    ad, = _validate(segs, 100.0, 300.0, detection_stage='fingerprint')
    assert ad.get('hold_reason') != HOLD_REASON_TRANSCRIPT_LOOP
    assert ad['validation']['decision'] != Decision.REJECT.value


def test_marker_below_the_coverage_threshold_is_untouched():
    segs = _speech(0.0, 20) + _loop(60.0, 40) + _speech(100.0, 20)
    for start, end in ((76.0, 126.0), (92.0, 132.0)):   # 48% and 20% inside the loop
        ad, = _validate(segs, start, end, category='cross_promo', reason='promo for another show')
        assert ad.get('hold_reason') != HOLD_REASON_TRANSCRIPT_LOOP
        assert ad['validation']['decision'] != Decision.REJECT.value or (ad['validation']['flags'])


def test_clean_transcript_marker_not_held_for_loop():
    ad, = _validate(_speech(0.0, 100), 50.0, 110.0)
    assert ad.get('hold_reason') != HOLD_REASON_TRANSCRIPT_LOOP


def test_read_under_two_loops_split_by_clean_cues_is_one_cut():
    segs = (_speech(0.0, 10) + _loop(30.0, 60) + _speech(90.0, 10) + _read(120.0)
            + _loop(135.0, 60) + _cues([(195.0, 197.0, 'and a few clean words')]) + _loop(198.0, 80))
    ad, = _validate(segs, 30.0, 278.0)
    assert ad['start'] >= 90.0 and ad['end'] == 278.0


def test_marker_matching_a_human_confirm_keeps_its_bounds():
    segs = _speech(0.0, 10) + _loop(30.0, 60) + _speech(90.0, 10) + _read(120.0) + _loop(135.0, 100)
    ad = {'start': 30.0, 'end': 235.0, 'confidence': 0.95, 'category': 'sponsor',
          'detection_stage': 'verification', 'reason': 'sponsor read'}
    confirmed = [{'start': 30.0, 'end': 235.0, 'correction_type': 'confirm'}]
    out = AdValidator(episode_duration=2600.0, segments=segs,
                      confirmed_corrections=confirmed).validate([ad]).ads
    assert [(a['start'], a['end']) for a in out] == [(30.0, 235.0)]
