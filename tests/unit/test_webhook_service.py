"""Tests for webhook_service module (src/webhook_service.py)."""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SecurityError

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from webhook_service import (
    render_template_preview,
    _build_context,
    _prepare_and_dispatch,
    _format_duration,
    _format_cost,
    load_webhooks,
    fire_event,
    _fire_event_sync,
    EVENT_EPISODE_PROCESSED,
    VALID_EVENTS,
)


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------

class TestRenderTemplatePreview:

    def test_render_template_preview_valid(self):
        """Renders a simple Jinja2 template with dummy context."""
        result = render_template_preview("Event: {{ event }}")
        assert result == f"Event: {EVENT_EPISODE_PROCESSED}"

    def test_render_template_preview_invalid(self):
        """Raises on bad template syntax."""
        with pytest.raises(TemplateSyntaxError):
            render_template_preview("{{ invalid(")

    def test_render_template_preview_sandboxed(self):
        """Sandboxing blocks dangerous operations like MRO traversal."""
        with pytest.raises(SecurityError):
            render_template_preview("{{ ''.__class__.__mro__ }}")


# ---------------------------------------------------------------------------
# Context building tests
# ---------------------------------------------------------------------------

class TestBuildContext:

    def test_build_context_success(self):
        """All fields present, time_saved computed correctly."""
        ctx = _build_context(
            event=EVENT_EPISODE_PROCESSED,
            episode_id='ep1',
            slug='my-pod',
            episode_title='My Episode',
            processing_time=30.0,
            llm_cost=0.00412345,
            ads_removed=2,
            error_message=None,
            original_duration=600.0,
            new_duration=500.0,
        )
        assert ctx['event'] == EVENT_EPISODE_PROCESSED
        assert ctx['episode']['id'] == 'ep1'
        assert ctx['episode']['title'] == 'My Episode'
        assert ctx['episode']['slug'] == 'my-pod'
        assert ctx['episode']['ads_removed'] == 2
        assert ctx['episode']['processing_time_secs'] == 30.0
        assert ctx['episode']['processing_time'] == '0:30'
        assert ctx['episode']['llm_cost'] == 0.004123  # rounded to 6 decimal places
        assert ctx['episode']['llm_cost_display'] == '$0.00'
        assert ctx['episode']['time_saved_secs'] == 100.0
        assert ctx['episode']['time_saved'] == '1:40'
        assert ctx['episode']['error_message'] is None
        assert 'timestamp' in ctx

    def test_build_context_no_duration(self):
        """When original_duration/new_duration are None, time_saved_secs is None."""
        ctx = _build_context(
            event=EVENT_EPISODE_PROCESSED,
            episode_id='ep2',
            slug='pod',
            episode_title='Title',
            processing_time=10.0,
            llm_cost=0.0,
            ads_removed=0,
            error_message=None,
            original_duration=None,
            new_duration=None,
        )
        assert ctx['episode']['time_saved_secs'] is None
        assert ctx['episode']['time_saved'] is None
        assert ctx['episode']['processing_time'] == '0:10'
        assert ctx['episode']['llm_cost_display'] == '$0.00'

    def test_build_context_uses_base_url_env(self):
        """BASE_URL env var is used in episode URL when UI_BASE_URL is not set."""
        with patch.dict(os.environ, {'BASE_URL': 'https://my-server:9000'}, clear=False):
            # Ensure UI_BASE_URL is not set
            os.environ.pop('UI_BASE_URL', None)
            ctx = _build_context(
                event=EVENT_EPISODE_PROCESSED,
                episode_id='ep3',
                slug='slug1',
                episode_title='T',
                processing_time=1.0,
                llm_cost=0.0,
                ads_removed=0,
                error_message=None,
                original_duration=None,
                new_duration=None,
            )
        assert ctx['episode']['url'] == 'https://my-server:9000/ui/feeds/slug1/episodes/ep3'

    def test_build_context_ui_base_url_takes_priority(self):
        """UI_BASE_URL takes priority over BASE_URL for episode URLs."""
        with patch.dict(os.environ, {
            'BASE_URL': 'https://feed.example.com',
            'UI_BASE_URL': 'https://app.example.com',
        }):
            ctx = _build_context(
                event=EVENT_EPISODE_PROCESSED,
                episode_id='ep4',
                slug='slug2',
                episode_title='T',
                processing_time=1.0,
                llm_cost=0.0,
                ads_removed=0,
                error_message=None,
                original_duration=None,
                new_duration=None,
            )
        assert ctx['episode']['url'] == 'https://app.example.com/ui/feeds/slug2/episodes/ep4'


# ---------------------------------------------------------------------------
# HMAC signing tests
# ---------------------------------------------------------------------------

class TestPrepareAndDispatchSigning:

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_with_secret(self, mock_dispatch, _mock_url):
        """X-MinusPod-Signature header is added when secret is set."""
        config = {'url': 'https://hook.example.com', 'secret': 'mysecret'}
        context = {'event': 'Episode Processed', 'episode': {}}
        _prepare_and_dispatch(config, context)

        args = mock_dispatch.call_args[0]
        headers = args[2]
        assert 'X-MinusPod-Signature' in headers
        assert headers['X-MinusPod-Signature'].startswith('sha256=')

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_no_secret(self, mock_dispatch, _mock_url):
        """No signature header when no secret."""
        config = {'url': 'https://hook.example.com'}
        context = {'event': 'Episode Processed', 'episode': {}}
        _prepare_and_dispatch(config, context)

        args = mock_dispatch.call_args[0]
        headers = args[2]
        assert 'X-MinusPod-Signature' not in headers

    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_ssrf_blocked(self, mock_dispatch):
        """SSRF check at dispatch time blocks private IPs."""
        from utils.url import SSRFError
        config = {'url': 'https://hook.example.com'}
        context = {'event': 'Episode Processed', 'episode': {}}
        with patch('webhook_service.validate_url', side_effect=SSRFError('Blocked private IP')):
            result = _prepare_and_dispatch(config, context)
        assert result is None
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# Dispatch / payload tests
# ---------------------------------------------------------------------------

class TestPrepareAndDispatchPayload:

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_with_template(self, mock_dispatch, _mock_url):
        """Renders template and dispatches."""
        config = {
            'url': 'https://hook.example.com',
            'payloadTemplate': 'hello {{ event }}',
        }
        context = {'event': 'Episode Processed', 'episode': {}}
        _prepare_and_dispatch(config, context)

        args = mock_dispatch.call_args[0]
        body_bytes = args[1]
        assert body_bytes == b'hello Episode Processed'

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_default_payload(self, mock_dispatch, _mock_url):
        """Uses json.dumps of context when no template."""
        config = {'url': 'https://hook.example.com'}
        context = {'event': 'Episode Processed', 'data': 123}
        _prepare_and_dispatch(config, context)

        args = mock_dispatch.call_args[0]
        body_bytes = args[1]
        parsed = json.loads(body_bytes)
        assert parsed['event'] == 'Episode Processed'
        assert parsed['data'] == 123

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_test_flag_default(self, mock_dispatch, _mock_url):
        """test: true is in payload for default (no template) path."""
        config = {'url': 'https://hook.example.com'}
        context = {'event': 'Episode Processed'}
        _prepare_and_dispatch(config, context, add_test_flag=True)

        args = mock_dispatch.call_args[0]
        parsed = json.loads(args[1])
        assert parsed['test'] is True

    @patch('webhook_service.validate_url')
    @patch('webhook_service._dispatch_webhook', return_value=200)
    def test_prepare_and_dispatch_test_flag_template(self, mock_dispatch, _mock_url):
        """test: true is available in context for the template path too."""
        config = {
            'url': 'https://hook.example.com',
            'payloadTemplate': '{% if test %}TEST{% endif %} {{ event }}',
        }
        context = {'event': 'Episode Processed'}
        _prepare_and_dispatch(config, context, add_test_flag=True)

        args = mock_dispatch.call_args[0]
        assert args[1] == b'TEST Episode Processed'


# ---------------------------------------------------------------------------
# Event validation tests
# ---------------------------------------------------------------------------

class TestFireEvent:

    @patch('webhook_service.threading.Thread')
    def test_fire_event_invalid_event(self, mock_thread):
        """Logs error and returns without dispatching on invalid event."""
        fire_event(
            event='not.a.real.event',
            episode_id='ep1',
            slug='pod',
            episode_title='Title',
            processing_time=1.0,
            llm_cost=0.0,
        )
        mock_thread.assert_not_called()

    @patch('webhook_service.load_webhooks', return_value=[])
    @patch('webhook_service._prepare_and_dispatch')
    def test_fire_event_no_webhooks(self, mock_dispatch, mock_load):
        """_prepare_and_dispatch is never called when no webhooks configured."""
        _fire_event_sync(
            event=EVENT_EPISODE_PROCESSED,
            episode_id='ep1',
            slug='pod',
            episode_title='Title',
            processing_time=1.0,
            llm_cost=0.0,
            ads_removed=0,
            error_message=None,
            original_duration=None,
            new_duration=None,
        )
        mock_dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# load_webhooks tests
# ---------------------------------------------------------------------------

class TestLoadWebhooks:

    def test_load_webhooks_empty(self):
        """Returns [] when no setting."""
        mock_db = MagicMock()
        mock_db.get_setting.return_value = None
        result = load_webhooks(db=mock_db)
        assert result == []

    def test_load_webhooks_invalid_json(self):
        """Returns [] on bad JSON."""
        mock_db = MagicMock()
        mock_db.get_setting.return_value = '{not valid json'
        result = load_webhooks(db=mock_db)
        assert result == []

    def test_load_webhooks_valid(self):
        """Returns parsed list."""
        webhooks = [{'url': 'https://hook.example.com', 'enabled': True}]
        mock_db = MagicMock()
        mock_db.get_setting.return_value = json.dumps(webhooks)
        result = load_webhooks(db=mock_db)
        assert result == webhooks


# ---------------------------------------------------------------------------
# Formatting helper tests
# ---------------------------------------------------------------------------

class TestFormatDuration:

    def test_seconds_only(self):
        assert _format_duration(5) == '0:05'

    def test_minutes_and_seconds(self):
        assert _format_duration(187.0) == '3:07'

    def test_exact_minute(self):
        assert _format_duration(60) == '1:00'

    def test_hours(self):
        assert _format_duration(3661) == '1:01:01'

    def test_none(self):
        assert _format_duration(None) is None

    def test_zero(self):
        assert _format_duration(0) == '0:00'


class TestFormatCost:

    def test_small_cost(self):
        assert _format_cost(0.0035) == '$0.00'

    def test_normal_cost(self):
        assert _format_cost(0.72) == '$0.72'

    def test_whole_dollar(self):
        assert _format_cost(1.0) == '$1.00'

    def test_none(self):
        assert _format_cost(None) is None

    def test_zero(self):
        assert _format_cost(0.0) == '$0.00'
