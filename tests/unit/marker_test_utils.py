"""Shared helpers for marker member tests."""


def member_bases(members):
    """Member spans reduced to start, end and stage."""
    if isinstance(members, dict):
        return {k: members[k] for k in ('start', 'end', 'stage')}
    return [member_bases(m) for m in members]
