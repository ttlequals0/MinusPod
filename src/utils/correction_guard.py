"""Which stored user corrections may still steer a cut; the recut replay and the validator share this rule."""

import logging

from utils.time import overlap_seconds

logger = logging.getLogger('podcast.audio')

MIN_CORRECTION_SPAN_S = 1.0


def _approved_span(correction):
    span = correction.get('confirmed_span') or correction
    start, end = span.get('start'), span.get('end')
    if start is None or end is None:
        return None
    return float(start), float(end)


def id_suffix(correction):
    """' (id N)' for a log line when the row carries an id, else ''."""
    return f" (id {correction['id']})" if correction.get('id') is not None else ''


def correction_skip_reason(correction, held_windows=(), newer=()):
    """Why a stored correction must not be applied (tiny, outside its held windows, or overtaken by newer), else None."""
    span = _approved_span(correction)
    if span is None:
        return None
    start, end = span
    if end - start < MIN_CORRECTION_SPAN_S:
        return f'shorter than {MIN_CORRECTION_SPAN_S:g}s'
    for lo, hi in held_windows:
        if start < lo or end > hi:
            return f'outside the allowed window {lo:.1f}-{hi:.1f}'
    if correction.get('correction_type') == 'boundary_adjustment':
        c_start, c_end = correction.get('start'), correction.get('end')
        if c_start is not None and c_end is not None:
            for other in newer:
                if (other.get('correction_type') == 'confirm'
                        and other.get('start') is not None
                        and other.get('end') is not None
                        and overlap_seconds(c_start, c_end,
                                            other['start'], other['end']) > 0):
                    return 'superseded by a newer confirm'
    return None


def usable_confirmed_corrections(corrections, markers, window_for=None, label=''):
    """Newest-first corrections minus the rows correction_skip_reason rejects, with one WARNING per skipped row."""
    kept = []
    for index, correction in enumerate(corrections):
        windows = []
        if (window_for is not None and correction.get('start') is not None
                and correction.get('end') is not None):
            for marker in markers or ():
                if (marker.get('held_for_review')
                        and marker.get('start') is not None
                        and marker.get('end') is not None
                        and overlap_seconds(correction['start'], correction['end'],
                                            marker['start'], marker['end']) > 0):
                    windows.append(window_for(marker))
        reason = correction_skip_reason(correction, windows, corrections[:index])
        if reason:
            span = _approved_span(correction) or (0.0, 0.0)
            logger.warning(f"{label}skipped legacy correction "
                           f"{span[0]:.1f}-{span[1]:.1f}: {reason}{id_suffix(correction)}")
            continue
        kept.append(correction)
    return kept
