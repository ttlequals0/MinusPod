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
