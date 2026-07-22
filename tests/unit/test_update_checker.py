import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from datetime import datetime, timezone  # noqa: E402

from update_checker import build_status, parse_version  # noqa: E402
import update_checker  # noqa: E402


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


class FakeDb:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})

    def get_setting(self, key):
        return self.settings.get(key)

    def get_setting_bool(self, key, default=False):
        v = self.settings.get(key)
        if v is None:
            return default
        return str(v).strip().lower() in ('true', '1', 'yes', 'on')

    def set_setting(self, key, value, is_default=False):
        self.settings[key] = value


def reset_cache():
    update_checker._cache = {'at': 0.0, 'releases': None}


class TestGetReleasesCache:
    def setup_method(self):
        reset_cache()

    def test_caches_within_ttl(self):
        with patch.object(update_checker, 'fetch_releases',
                          return_value=RELEASES) as f:
            update_checker.get_releases()
            update_checker.get_releases()
        assert f.call_count == 1

    def test_force_bypasses_ttl_but_not_throttle(self):
        with patch.object(update_checker, 'fetch_releases',
                          return_value=RELEASES) as f:
            update_checker.get_releases()
            update_checker.get_releases(force=True)   # within 30s of fetch
        assert f.call_count == 1

    def test_force_refetches_after_throttle_window(self):
        with patch.object(update_checker, 'fetch_releases',
                          return_value=RELEASES) as f:
            update_checker.get_releases()
            update_checker._cache['at'] = time.time() - 60
            update_checker.get_releases(force=True)
        assert f.call_count == 2

    def test_expired_cache_refetches(self):
        with patch.object(update_checker, 'fetch_releases',
                          return_value=RELEASES) as f:
            update_checker.get_releases()
            update_checker._cache['at'] = time.time() - update_checker.CACHE_TTL_S - 1
            update_checker.get_releases()
        assert f.call_count == 2


class TestGetUpdateStatus:
    def setup_method(self):
        reset_cache()

    def test_reads_channel_setting_and_defaults_stable(self):
        with patch.object(update_checker, 'fetch_releases', return_value=RELEASES):
            assert update_checker.get_update_status(FakeDb())['channel'] == 'stable'
            reset_cache()
            db = FakeDb({'update_channel': 'edge'})
            assert update_checker.get_update_status(db)['channel'] == 'edge'
            reset_cache()
            db = FakeDb({'update_channel': 'bogus'})
            assert update_checker.get_update_status(db)['channel'] == 'stable'


class TestUpdateCheckTick:
    def setup_method(self):
        reset_cache()

    def _db(self, **extra):
        return FakeDb({'update_channel': 'stable', **extra})

    def test_disabled_does_nothing(self):
        db = self._db(update_check_enabled='false')
        with patch.object(update_checker, 'get_update_status') as g:
            update_checker.update_check_tick(db)
        g.assert_not_called()

    def test_daily_gate(self):
        db = self._db(update_check_last_run=datetime.now(timezone.utc).isoformat())
        with patch.object(update_checker, 'get_update_status') as g:
            update_checker.update_check_tick(db)
        g.assert_not_called()

    def test_notifies_once_per_version(self):
        db = self._db()
        status = {'updateAvailable': True, 'channel': 'stable',
                  'stable': {'version': '2.75.0', 'releaseDate': '2026-07-23',
                             'url': 'u', 'notes': ''},
                  'edge': None, 'current': {'version': '2.74.0'}}
        with patch.object(update_checker, 'get_update_status', return_value=status), \
             patch('webhook_service.fire_update_available_event') as fire:
            update_checker.update_check_tick(db)
            db.settings.pop('update_check_last_run')
            update_checker.update_check_tick(db)          # same version again
        assert fire.call_count == 1
        assert db.settings['update_check_last_notified'] == '2.75.0'

    def test_no_update_no_notify_but_stamps_run(self):
        db = self._db()
        status = {'updateAvailable': False, 'channel': 'stable',
                  'stable': None, 'edge': None, 'current': {'version': '2.74.0'}}
        with patch.object(update_checker, 'get_update_status', return_value=status), \
             patch('webhook_service.fire_update_available_event') as fire:
            update_checker.update_check_tick(db)
        fire.assert_not_called()
        assert 'update_check_last_run' in db.settings

    def test_fetch_failure_does_not_stamp_run(self):
        db = self._db()
        with patch.object(update_checker, 'get_update_status',
                          side_effect=ValueError('boom')):
            update_checker.update_check_tick(db)   # must not raise
        assert 'update_check_last_run' not in db.settings


class TestUpdateAvailableFormatting:
    def test_email_formatter_registered_and_shaped(self):
        import email_service
        formatter = email_service.FORMATTERS['Update Available']
        subject, rows, hint = formatter({
            'version': '2.75.0', 'channel': 'stable',
            'release_date': '2026-07-23', 'release_url': 'https://example.invalid'})
        assert subject == '[MinusPod] Update Available: 2.75.0'
        labels = [label for label, _ in rows]
        assert labels == ['Version', 'Channel', 'Released', 'Release notes']
        assert hint

    def test_webhook_event_registered(self):
        import webhook_service
        assert 'Update Available' in webhook_service.VALID_EVENTS
