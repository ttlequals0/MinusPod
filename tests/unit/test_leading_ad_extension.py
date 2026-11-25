"""The first ad runs from the start of the file when nobody speaks before it (pre-roll music beds)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision, ValidationResult


def _ad_words(start=1.136):
    return {'start': start, 'end': start + 4.5, 'text': 'A word from our sponsor.',
            'words': [{'start': start, 'end': start + 0.9}, {'start': start + 1.0, 'end': start + 4.5}]}


def _show():
    return {'start': 23.86, 'end': 26.9, 'text': 'Welcome to the show.',
            'words': [{'start': 23.86, 'end': 24.5}, {'start': 24.6, 'end': 26.9}]}


def _extend(segments, start=1.136, **extra):
    validator = AdValidator(episode_duration=1140.5, segments=segments)
    head = {'start': start, 'end': 22.18, **extra}
    validator._extend_leading_ad([head], ValidationResult(ads=[]))
    return head['start']


def test_first_ad_runs_from_zero_over_wordless_audio():
    assert _extend([_ad_words(), _show()]) == 0.0


def test_extension_reaches_thirty_seconds():
    assert _extend([_ad_words(30.0)], start=30.0) == 0.0


def test_no_extension_past_thirty_seconds():
    assert _extend([_ad_words(30.5)], start=30.5) == 30.5


def test_extension_stops_at_speech_before_the_ad():
    cold_open = {'start': 0.4, 'end': 0.9, 'text': 'Hello.',
                 'words': [{'start': 0.4, 'end': 0.9}]}

    assert _extend([cold_open, _ad_words(), _show()]) == 1.136


def test_extension_stops_at_an_untimed_segment_before_the_ad():
    cold_open = {'start': 0.2, 'end': 0.9, 'text': 'Hello there.'}

    assert _extend([cold_open, _ad_words(), _show()]) == 1.136


def test_extension_ignores_word_timing_slop():
    early = {'start': 0.95, 'end': 1.5, 'text': 'A',
             'words': [{'start': 0.95, 'end': 1.5}]}

    assert _extend([early, _show()]) == 0.0


def test_held_marker_is_not_extended():
    assert _extend([_ad_words(), _show()], held_for_review=True) == 1.136


def test_extension_takes_the_marker_that_starts_first():
    validator = AdValidator(episode_duration=1140.5, segments=[_ad_words(), _show()])
    head = {'start': 1.136, 'end': 22.18}
    later = {'start': 394.19, 'end': 448.11}

    validator._extend_leading_ad([later, head], ValidationResult(ads=[]))

    assert head['start'] == 0.0
    assert later['start'] == 394.19


def test_validate_cuts_the_pre_roll_from_zero():
    validator = AdValidator(episode_duration=1140.5, segments=[_ad_words(), _show()],
                            min_cut_confidence=0.8)
    ad = {'start': 1.136, 'end': 22.18, 'confidence': 0.97, 'category': 'sponsor',
          'sponsor': 'Acme', 'reason': 'Acme sponsor read with a URL'}

    [out] = validator.validate([ad]).ads

    assert (out['start'], out['end']) == (0.0, 22.18)
    assert out['validation']['decision'] == Decision.ACCEPT.value
    assert not any(key.startswith('_leading') for key in out)


def test_auto_filed_span_keeps_the_leading_extension():
    validator = AdValidator(
        episode_duration=1140.5, segments=[_ad_words(), _show()],
        confirmed_corrections=[{
            'start': 1.136, 'end': 22.18, 'correction_type': 'confirm', 'auto_filed': True,
            'confirmed_span': {'start': 1.136, 'end': 22.18}, 'hold_reason': 'no_splice_evidence'}])
    ad = {'start': 1.136, 'end': 22.18, 'confidence': 0.9, 'reason': 'Acme sponsor read'}

    [out] = validator.validate([ad]).ads

    assert (out['start'], out['end']) == (0.0, 22.18)
    assert out['validation']['decision'] == Decision.ACCEPT.value


def test_operator_start_still_pins_the_first_ad():
    validator = AdValidator(
        episode_duration=1140.5, segments=[_ad_words(), _show()],
        confirmed_corrections=[{
            'start': 1.136, 'end': 22.18, 'correction_type': 'confirm',
            'confirmed_span': {'start': 1.136, 'end': 22.18}}])
    ad = {'start': 1.136, 'end': 22.18, 'confidence': 0.9, 'reason': 'Acme sponsor read'}

    [out] = validator.validate([ad]).ads

    assert out['start'] == 1.136
