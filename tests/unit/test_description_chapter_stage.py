"""Description chapter marker: appended in the detect stage and force-accepted past every ceiling."""
import os
import tempfile
from unittest.mock import MagicMock, patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='desc_chapter_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from ad_validator import AdValidator, Decision
from main_app.pipeline import detect

DESCRIPTION = "<p>(00:00) - Weekly Show</p><p>(31:26) - Throwback 10-8-18 - Host rambles</p>"
DURATION = 3600.0


def _ad(start, end, **kw):
    return {'start': start, 'end': end, 'confidence': 0.95, 'reason': 'Acme sponsor read',
            'sponsor': 'Acme', **kw}


def _run(all_ads, description_trim, description=DESCRIPTION):
    with patch.object(detect, 'db', MagicMock()), patch.object(detect, 'storage', MagicMock()), \
            patch.object(detect, '_build_validator',
                         side_effect=lambda *a, **k: AdValidator(DURATION, a[1], a[2])):
        return detect._refine_and_validate(
            's', 'e', all_ads, [], '/x.mp3', description, DURATION, 0.8, 'Show',
            corrections=([], []), description_trim=description_trim)


def _chapter(ads):
    return [a for a in ads if a.get('detection_stage') == 'description_chapter']


def test_marker_appended_and_force_accepted_past_duration_ceiling():
    to_remove, all_ads = _run([], ('*Throwback*', 'to_end'))
    marker = _chapter(to_remove)[0]
    assert (marker['start'], marker['end']) == (1886.0, DURATION)
    assert marker['category'] == 'outro'
    assert marker['confidence'] == 1.0
    assert marker['reason'] == 'Description chapter: Throwback 10-8-18 - Host rambles'
    assert marker['sponsor'] is None
    assert marker['validation']['decision'] == Decision.ACCEPT.value
    assert marker['validation']['user_confirmed'] is True
    assert marker['was_cut'] is True
    assert marker in all_ads


def test_overlapping_detected_ad_does_not_merge_or_shrink_the_span():
    to_remove, _ = _run([_ad(2000.0, 2060.0)], ('*Throwback*', 'to_end'))
    marker = _chapter(to_remove)[0]
    assert (marker['start'], marker['end']) == (1886.0, DURATION)


def test_to_next_mode_with_no_following_chapter_cuts_to_end():
    to_remove, _ = _run([], ('*Throwback*', 'to_next'))
    assert _chapter(to_remove)[0]['end'] == DURATION


def test_no_pattern_or_no_match_adds_nothing():
    assert _run([], (None, 'to_end')) == ([], [])
    assert _run([], ('*Nothing*', 'to_end')) == ([], [])
    assert _run([], None) == ([], [])


def test_stored_marker_survives_revalidation():
    marker = {'start': 1886.0, 'end': DURATION, 'category': 'outro', 'confidence': 1.0,
              'detection_stage': 'description_chapter', 'sponsor': None,
              'reason': 'Description chapter: Throwback'}
    result = AdValidator(DURATION).validate([marker, _ad(100.0, 130.0)])
    kept = _chapter(result.ads)[0]
    assert (kept['start'], kept['end']) == (1886.0, DURATION)
    assert kept['validation']['user_confirmed'] is True


def test_inner_llm_ad_and_chapter_span_render_as_one_contiguous_cut():
    from audio_processor import AudioProcessor
    to_remove, _ = _run([_ad(2000.0, 2060.0)], ('*Throwback*', 'to_end'))
    cuts = AudioProcessor().compute_applied_cuts(to_remove, DURATION)
    assert [(c['start'], c['end']) for c in cuts] == [(1886.0, DURATION)]


def _ads(start, end):
    return [{'start': start, 'end': end, 'detection_stage': 'description_chapter',
             'reason': 'Description chapter: old'}, _ad(100.0, 130.0)]


def test_recut_drops_stored_marker_when_pattern_cleared():
    out = detect.reconcile_description_chapter(_ads(1886.0, DURATION), DESCRIPTION, DURATION,
                                               (None, 'to_end'))
    assert _chapter(out) == [] and len(out) == 1


def test_recut_rederives_marker_when_pattern_changed():
    out = detect.reconcile_description_chapter(
        _ads(1886.0, DURATION), DESCRIPTION, DURATION, ('Weekly*', 'to_next'))
    marker = _chapter(out)[0]
    assert (marker['start'], marker['end']) == (0.0, 1886.0)
    assert len(_chapter(out)) == 1


def test_recut_keeps_stored_marker_when_span_unchanged():
    ads = _ads(1886.0, DURATION)
    assert detect.reconcile_description_chapter(
        ads, DESCRIPTION, DURATION, ('*Throwback*', 'to_end')) is ads


def _chapter_marker(mode='to_next'):
    return {'start': 1200.0, 'end': 1500.0, 'detection_stage': 'description_chapter',
            'source': 'generated_chapter', 'trim_mode': mode,
            'reason': 'Chapter: Listener Donations'}


def test_recut_keeps_chapter_marker_while_pattern_and_mode_match():
    ads = [_chapter_marker()]
    assert detect.reconcile_description_chapter(
        ads, '', DURATION, ('*Donations*', 'to_next')) is ads


def test_recut_drops_chapter_marker_when_pattern_cleared_or_no_longer_matches():
    for trim in ((None, 'to_next'), ('*Other*', 'to_next')):
        assert detect.reconcile_description_chapter([_chapter_marker()], '', DURATION, trim) == []


def test_recut_mode_change_re_ends_chapter_marker_instead_of_dropping_it():
    out = detect.reconcile_description_chapter(
        [_chapter_marker('to_next')], '', DURATION, ('*Donations*', 'to_end'))
    assert [(m['start'], m['end'], m['trim_mode']) for m in out] == [(1200.0, DURATION, 'to_end')]
    out = detect.reconcile_description_chapter(
        [{**_chapter_marker('to_end'), 'end': DURATION}], '', DURATION, ('*Donations*', 'to_next'))
    assert [(m['start'], m['end'], m['trim_mode']) for m in out] == [(1200.0, DURATION, 'to_next')]
