"""Stage-3 (Claude) same-episode false-positive region check (issue #183).

Stages 1 and 2 already skip detections in user-rejected regions via
`_is_region_covered`. This test documents that Stage 3 mirrors that check
on Claude's per-portion output, preventing the editor from displaying ads
the user already explicitly rejected.
"""

from ad_detector import AdDetector


def _detector():
    return AdDetector.__new__(AdDetector)


def test_is_region_covered_treats_overlapping_portion_as_fp():
    """Portion overlapping a rejected region by >50% is reported covered."""
    fp_regions = [{'start': 100.0, 'end': 200.0}]
    fp_pairs = [(r['start'], r['end']) for r in fp_regions]

    detector = _detector()
    # Claude portion fully inside the rejected region
    assert detector._is_region_covered(120.0, 180.0, fp_pairs) is True
    # 51% of the portion overlaps -> skipped
    assert detector._is_region_covered(149.0, 249.0, fp_pairs) is True
    # Only 49% of the portion overlaps -> not skipped
    assert detector._is_region_covered(151.0, 251.0, fp_pairs) is False


def test_no_fp_regions_does_not_block():
    """An empty FP region list never skips a Claude portion."""
    detector = _detector()
    assert detector._is_region_covered(120.0, 180.0, []) is False


