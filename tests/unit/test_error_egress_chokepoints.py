"""API, webhook, email and log egress each redact the same error sample."""
import logging

import webhook_service
from utils.log_format import QueryStringRedactingFilter

KEY = 'c' * 64
SAMPLE = f'GET https://alice:PASS@cdn.example.com/a.mp3?X-Amz-Signature=SIG failed {KEY}'
LEAKS = ('PASS', 'SIG', KEY, 'alice')


class _InlineThread:
    def __init__(self, target, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _clean(text):
    assert 'cdn.example.com' in text
    for leak in LEAKS:
        assert leak not in text


def test_webhook_alert_context_is_redacted(monkeypatch):
    sent = []
    monkeypatch.setattr(webhook_service, '_last_alert_time', {})
    monkeypatch.setattr(webhook_service, 'load_webhooks', lambda: [
        {'enabled': True, 'events': [webhook_service.EVENT_SERVICE_OFFLINE], 'url': 'https://example.com/h'}])
    monkeypatch.setattr(webhook_service, '_prepare_and_dispatch', lambda wh, ctx: sent.append(ctx))
    monkeypatch.setattr(webhook_service, '_timestamp_fields', lambda: {})
    monkeypatch.setattr(webhook_service.email_service, 'send_event_email', lambda event, ctx: sent.append(ctx))
    monkeypatch.setattr(webhook_service.threading, 'Thread', _InlineThread)
    webhook_service.fire_service_offline_event('llm', SAMPLE, 'slug', 'a1b2c3d4e5f6', 'Pod')
    assert len(sent) == 2
    _clean(str(sent))


def test_log_filter_redacts():
    record = logging.LogRecord('t', logging.ERROR, __file__, 1, 'failed: %s', (SAMPLE,), None)
    QueryStringRedactingFilter().filter(record)
    _clean(record.getMessage())
