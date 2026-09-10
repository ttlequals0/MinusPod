"""Rate-limit hold probe: re-checks an active hold instead of waiting out
the provider's stated reset, which can be wrong in either direction.

Uses the main_app boot pattern from test_rate_limit_hold.py.
"""
from datetime import timedelta
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('rate_limit_probe_test_')
from llm_client import ProviderRateLimitedError
from main_app import db
from main_app.processing import _handle_processing_failure
from rate_limit_hold import MAX_HOLD_SECONDS, probe_rate_limit
from tests.unit.provider_error_fakes import FakeProviderError, FakeResponse
from utils.time import parse_iso_utc, utc_now, utc_now_iso

SLUG = 'rate-limit-probe-feed'
USAGE_URL = 'https://example-provider.test/v1/usage'
ISO = '%Y-%m-%dT%H:%M:%SZ'
# A real seven-day provider window, well past the old 24h cap.
WEEK_SECONDS = 460241


def _set_hold(seconds_from_now: float) -> None:
    until = (utc_now() + timedelta(seconds=seconds_from_now)).strftime('%Y-%m-%dT%H:%M:%SZ')
    db.set_setting('rate_limit_hold_until', until)
    db.set_setting('rate_limit_hold_since', utc_now_iso())


def _hold_delta_seconds() -> float:
    """Seconds between now and the currently stamped hold_until."""
    until = parse_iso_utc(db.get_setting('rate_limit_hold_until'))
    return (until - utc_now()).total_seconds()


def _probe_payload(payload: dict) -> str:
    """One probe against `payload`, ignoring the probe cadence stamp."""
    db.clear_setting('rate_limit_probe_at')
    with patch('rate_limit_hold.read_usage_status', return_value=payload):
        assert probe_rate_limit(db) is True
    return db.get_setting('rate_limit_hold_until')


def _weekly_payloads(target) -> list[dict]:
    """The same reset expressed in each field usage_reset_iso understands."""
    return [
        {'blocked': True, 'seconds_until_reset': (target - utc_now()).total_seconds()},
        {'blocked': True, 'blocked_until': int(target.timestamp())},
        {'blocked': True, 'blocked_until_iso': target.strftime(ISO)},
    ]


def _clear_state() -> None:
    db.clear_setting('rate_limit_hold_until')
    db.clear_setting('rate_limit_hold_since')
    db.clear_setting('rate_limit_probe_at')
    db.clear_setting('llm_usage_url')
    db.set_setting('rate_limit_probe_minutes', '5')


class TestUsageUrlProbe:
    def setup_method(self):
        _clear_state()
        db.set_setting('llm_usage_url', USAGE_URL)
        _set_hold(300)

    def teardown_method(self):
        _clear_state()

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_blocked_false_clears_hold(self, mock_fire):
        with patch('rate_limit_hold.read_usage_status', return_value={'blocked': False}):
            assert probe_rate_limit(db) is True
        assert db.get_setting('rate_limit_hold_until') is None
        mock_fire.assert_called_once()

    def test_seconds_until_reset_moves_hold_out(self):
        payload = {
            'blocked': True, 'seconds_until_reset': 38460,
            'blocked_until': int((utc_now() + timedelta(seconds=1)).timestamp()),
            'blocked_until_iso': utc_now_iso(),
            'windows': {'session_limit': {'status': 'rejected'}},
        }
        with patch('rate_limit_hold.read_usage_status', return_value=payload):
            assert probe_rate_limit(db) is True
        assert 38400 < _hold_delta_seconds() < 38520

    def test_nearer_value_moves_hold_in(self):
        _set_hold(7200)
        payload = {'blocked': True, 'seconds_until_reset': 60}
        with patch('rate_limit_hold.read_usage_status', return_value=payload):
            assert probe_rate_limit(db) is True
        assert _hold_delta_seconds() < 200

    def test_weekly_window_reset_is_not_truncated(self):
        """A seven-day provider window holds until its own reset, not 24h out."""
        target = utc_now() + timedelta(seconds=WEEK_SECONDS)
        for payload in _weekly_payloads(target):
            _probe_payload(payload)
            assert WEEK_SECONDS - 60 < _hold_delta_seconds() <= WEEK_SECONDS

    def test_absurd_reset_is_capped(self):
        """Milliseconds sent as seconds must not pause the queue for a century."""
        absurd = 3.1e9
        target = utc_now() + timedelta(seconds=absurd)
        for payload in ({'blocked': True, 'seconds_until_reset': absurd},
                        {'blocked': True, 'blocked_until': int(target.timestamp())},
                        {'blocked': True, 'blocked_until_iso': target.strftime(ISO)}):
            _probe_payload(payload)
            assert _hold_delta_seconds() <= MAX_HOLD_SECONDS

    def test_repeat_probes_do_not_slide_the_hold(self):
        """Probes minutes apart against the same window stamp the same reset."""
        now = utc_now()
        target = now + timedelta(seconds=WEEK_SECONDS)
        stamps = []
        for offset in (0, 600):
            probe_at = now + timedelta(seconds=offset)
            payload = {'blocked': True,
                       'seconds_until_reset': (target - probe_at).total_seconds()}
            with patch('rate_limit_hold.utc_now', return_value=probe_at):
                stamps.append(_probe_payload(payload))
        assert stamps[0] == stamps[1] == target.strftime(ISO)

    def test_blocked_until_epoch_used_when_seconds_absent(self):
        target = utc_now() + timedelta(seconds=500)
        payload = {'blocked': True, 'blocked_until': int(target.timestamp())}
        with patch('rate_limit_hold.read_usage_status', return_value=payload):
            assert probe_rate_limit(db) is True
        assert 480 < _hold_delta_seconds() < 520

    def test_blocked_until_iso_used_when_others_absent(self):
        target_iso = (utc_now() + timedelta(seconds=700)).strftime('%Y-%m-%dT%H:%M:%SZ')
        payload = {'blocked': True, 'blocked_until_iso': target_iso}
        with patch('rate_limit_hold.read_usage_status', return_value=payload):
            assert probe_rate_limit(db) is True
        assert db.get_setting('rate_limit_hold_until') == target_iso

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_transport_failure_falls_through_to_completion_probe(self, mock_fire):
        class _Client:
            def messages_create(self, **kw):
                return object()

        with patch('rate_limit_hold.read_usage_status', return_value=None), \
                patch('rate_limit_hold.get_llm_client', return_value=_Client()):
            assert probe_rate_limit(db) is True
        assert db.get_setting('rate_limit_hold_until') is None
        mock_fire.assert_called_once()


class TestCompletionProbe:
    """No usage URL configured: the completion probe is the only tier."""

    def setup_method(self):
        _clear_state()
        _set_hold(300)

    def teardown_method(self):
        _clear_state()

    @patch('rate_limit_hold.fire_queue_resumed_event')
    def test_successful_completion_clears_hold(self, mock_fire):
        class _Client:
            def messages_create(self, **kw):
                return object()

        with patch('rate_limit_hold.get_llm_client', return_value=_Client()):
            assert probe_rate_limit(db) is True
        assert db.get_setting('rate_limit_hold_until') is None
        mock_fire.assert_called_once()

    def test_rate_limit_error_restamps_hold(self):
        err = FakeProviderError(
            message='rate limit 429', response=FakeResponse(headers={'Retry-After': '900'}))

        class _Client:
            def messages_create(self, **kw):
                raise err

        with patch('rate_limit_hold.get_llm_client', return_value=_Client()):
            assert probe_rate_limit(db) is False
        assert 850 < _hold_delta_seconds() < 950

    def test_other_exception_leaves_hold_untouched(self):
        original = db.get_setting('rate_limit_hold_until')

        class _Client:
            def messages_create(self, **kw):
                raise ValueError('boom')

        with patch('rate_limit_hold.get_llm_client', return_value=_Client()):
            assert probe_rate_limit(db) is False
        assert db.get_setting('rate_limit_hold_until') == original


class TestCadence:
    def setup_method(self):
        _clear_state()

    def teardown_method(self):
        _clear_state()

    def test_probe_minutes_zero_never_probes(self):
        _set_hold(300)
        db.set_setting('llm_usage_url', USAGE_URL)
        db.set_setting('rate_limit_probe_minutes', '0')
        with patch('rate_limit_hold.read_usage_status') as mock_read, \
                patch('rate_limit_hold.get_llm_client') as mock_client:
            assert probe_rate_limit(db) is False
        mock_read.assert_not_called()
        mock_client.assert_not_called()

    def test_interval_respected(self):
        _set_hold(300)
        db.set_setting('llm_usage_url', USAGE_URL)
        db.set_setting('rate_limit_probe_at', utc_now_iso())
        with patch('rate_limit_hold.read_usage_status') as mock_read:
            assert probe_rate_limit(db) is False
        mock_read.assert_not_called()

    def test_no_hold_active_never_probes(self):
        db.set_setting('llm_usage_url', USAGE_URL)
        with patch('rate_limit_hold.read_usage_status') as mock_read:
            assert probe_rate_limit(db) is False
        mock_read.assert_not_called()


class TestFailureHandlerPrefersUsageEndpoint:
    """_handle_processing_failure's hold branch prefers a configured usage
    endpoint's reset over the 429's own stated reset (issue: a 429 said
    3600s while the provider's usage endpoint said 38460s)."""

    def setup_method(self):
        _clear_state()
        db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Rate Limit Probe Test')
        db.upsert_episode(SLUG, 'ep-1', title='Episode 1', status='processing',
                          original_url='https://example.com/ep1.mp3', retry_count=1)
        db.set_setting('rate_limit_hold_enabled', 'true')

    def teardown_method(self):
        db.delete_podcast(SLUG)
        db.set_setting('rate_limit_hold_enabled', 'false')
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        _clear_state()

    def _fail(self, retry_after=600.0):
        episode_data = db.get_episode(SLUG, 'ep-1')
        error = ProviderRateLimitedError('provider rate limit reached', retry_after_seconds=retry_after)
        with patch('main_app.processing.status_service'):
            _handle_processing_failure(SLUG, 'ep-1', 'Episode 1', 'Rate Limit Probe Test',
                                       episode_data, error, start_time=0.0)

    def test_prefers_usage_endpoint_reset(self):
        db.set_setting('llm_usage_url', USAGE_URL)
        payload = {'blocked': True, 'seconds_until_reset': 38460}
        with patch('rate_limit_hold.read_usage_status', return_value=payload):
            self._fail(retry_after=3600.0)
        assert 38400 < _hold_delta_seconds() < 38520

    def test_falls_back_when_usage_endpoint_fails(self):
        db.set_setting('llm_usage_url', USAGE_URL)
        with patch('rate_limit_hold.read_usage_status', return_value=None):
            self._fail(retry_after=600.0)
        assert 550 < _hold_delta_seconds() < 650
