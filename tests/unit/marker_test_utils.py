"""Shared helpers for marker member tests."""


def member_bases(members):
    """Member spans reduced to start, end and stage."""
    if isinstance(members, dict):
        return {k: members[k] for k in ('start', 'end', 'stage')}
    return [member_bases(m) for m in members]


def _ad(start, end, stage=None, **extra):
    """Minimal marker dict for a detection stage."""
    return {'start': start, 'end': end, 'detection_stage': stage, **extra}


def applied_cut(start, end, replacement=1.0):
    """A rendered cut as compute_applied_cuts returns it."""
    return {'start': start, 'end': end, 'replacement_duration': replacement}


def registry_confirms(validator, ad):
    """Run the validator's registry check over the ad's own bounded text."""
    return validator._registry_confirms(ad, validator._bounded_text_segments(ad))
