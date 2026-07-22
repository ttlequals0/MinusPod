import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from update_checker import build_status, parse_version  # noqa: E402


def rel(tag, prerelease=True, draft=False, published='2026-07-22T20:00:00Z',
        body='notes', url='https://example.invalid/r'):
    return {'tag_name': tag, 'prerelease': prerelease, 'draft': draft,
            'published_at': published, 'body': body, 'html_url': url}


RELEASES = [
    rel('v2.75.0'),                        # newest, prerelease -> edge
    rel('v2.74.0', prerelease=False),      # newest stable
    rel('v2.73.0'),
    rel('v2.72.0', prerelease=False),
]


class TestParseVersion:
    def test_parses_and_strips_v(self):
        assert parse_version('v2.73.0') == (2, 73, 0)
        assert parse_version('2.73.0') == (2, 73, 0)

    def test_rejects_garbage(self):
        assert parse_version(None) is None
        assert parse_version('') is None
        assert parse_version('2.73') is None
        assert parse_version('2.73.x') is None

    def test_orders_numerically_not_lexically(self):
        assert parse_version('2.100.0') > parse_version('2.9.0')


class TestBuildStatus:
    def test_stable_and_edge_selection(self):
        s = build_status(RELEASES, 'stable', current_version='2.73.0')
        assert s['stable']['version'] == '2.74.0'
        assert s['edge']['version'] == '2.75.0'
        assert s['channel'] == 'stable'

    def test_update_available_per_channel(self):
        assert build_status(RELEASES, 'stable', '2.73.0')['updateAvailable'] is True
        assert build_status(RELEASES, 'stable', '2.74.0')['updateAvailable'] is False
        assert build_status(RELEASES, 'edge', '2.74.0')['updateAvailable'] is True

    def test_current_release_date_found_and_omitted(self):
        s = build_status(RELEASES, 'stable', '2.73.0')
        assert s['current'] == {'version': '2.73.0', 'releaseDate': '2026-07-22'}
        s2 = build_status(RELEASES, 'stable', '9.9.9')
        assert s2['current'] == {'version': '9.9.9'}

    def test_drafts_ignored(self):
        releases = [rel('v3.0.0', prerelease=False, draft=True)] + RELEASES
        s = build_status(releases, 'stable', '2.74.0')
        assert s['stable']['version'] == '2.74.0'
        assert s['updateAvailable'] is False

    def test_empty_releases(self):
        s = build_status([], 'stable', '2.73.0')
        assert s['stable'] is None and s['edge'] is None
        assert s['updateAvailable'] is False

    def test_release_info_shape_and_notes_cap(self):
        long_body = 'x' * 5000
        s = build_status([rel('v2.74.0', prerelease=False, body=long_body)],
                         'stable', '2.73.0')
        info = s['stable']
        assert set(info) == {'version', 'releaseDate', 'url', 'notes'}
        assert len(info['notes']) == 2000

    def test_unparseable_remote_version_never_flags(self):
        s = build_status([rel('vNext', prerelease=False)], 'stable', '2.73.0')
        assert s['updateAvailable'] is False
