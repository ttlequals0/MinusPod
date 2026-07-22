"""Check GitHub Releases for newer MinusPod versions.

Channels: 'stable' (newest non-prerelease) and 'edge' (newest release of
any kind). Drafts are ignored. This module owns the release fetch, an
in-process cache, version comparison, the /system/updates payload, and
the daily background tick that notifies once per newly seen version.
"""
import logging

from version import __version__

logger = logging.getLogger('update_checker')

NOTES_MAX_CHARS = 2000


def parse_version(text):
    """'v2.73.0' or '2.73.0' -> (2, 73, 0); None when not three ints."""
    if not text:
        return None
    parts = str(text).strip().lstrip('v').split('.')
    if len(parts) != 3:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _release_info(release):
    date = (release.get('published_at') or '')[:10] or None
    return {
        'version': (release.get('tag_name') or '').lstrip('v'),
        'releaseDate': date,
        'url': release.get('html_url'),
        'notes': (release.get('body') or '')[:NOTES_MAX_CHARS],
    }


def build_status(releases, channel, current_version=__version__):
    """Assemble the /system/updates payload from a GitHub releases list.

    The releases list is newest-first, as GitHub returns it.
    """
    published = [r for r in releases if not r.get('draft')]
    stable = next((r for r in published if not r.get('prerelease')), None)
    edge = published[0] if published else None

    current = {'version': current_version}
    for r in published:
        if (r.get('tag_name') or '').lstrip('v') == current_version:
            date = (r.get('published_at') or '')[:10]
            if date:
                current['releaseDate'] = date
            break

    status = {
        'current': current,
        'stable': _release_info(stable) if stable else None,
        'edge': _release_info(edge) if edge else None,
        'channel': channel,
        'updateAvailable': False,
    }
    target = status['stable'] if channel == 'stable' else status['edge']
    cur = parse_version(current_version)
    if target and cur:
        remote = parse_version(target['version'])
        if remote and remote > cur:
            status['updateAvailable'] = True
    return status
