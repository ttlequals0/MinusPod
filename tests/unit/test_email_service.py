"""Tests for native email notifications (src/email_service.py).

Covers config loading, per-event formatting, MIME structure (HTML with CID
logo + plain fallback), SMTP branch selection, and the two safety
properties: event filtering and never-raises dispatch.
"""
from unittest.mock import MagicMock, patch

import pytest

import email_service
from utils.url import SSRFError
from webhook_service import VALID_EVENTS
from email_service import (
    EmailConfig,
    build_message,
    is_send_ready,
    load_email_config,
    parse_recipients,
    send_event_email,
    send_test_email,
    FORMATTERS,
)


def _cfg(**overrides):
    base = dict(
        enabled=True,
        events=['Episode Failed', 'Limit Exceeded'],
        host='mail.example.com',
        port=587,
        security='starttls',
        username='',
        password=None,
        from_addr='minuspod@example.com',
        recipients=['op@example.com'],
    )
    base.update(overrides)
    return EmailConfig(**base)


def _mock_db(settings=None, secret=None):
    db = MagicMock()
    settings = settings or {}
    db.get_setting.side_effect = lambda key: settings.get(key)
    db.get_secret.return_value = secret
    return db


EPISODE_CTX = {
    'event': 'Episode Failed',
    'timestamp': '2026-07-12T00:00:00Z',
    'podcast': {'name': 'My Show', 'slug': 'my-show'},
    'episode': {
        'id': 'ep1', 'title': 'Pilot', 'slug': 'my-show',
        'url': 'http://server/ui/feeds/my-show/episodes/ep1',
        'ads_removed': 3, 'processing_time': '1:02',
        'llm_cost_display': '$0.01', 'time_saved': '3:07',
        'error_message': 'boom',
    },
}

ALERT_CTX = {
    'event': 'Limit Exceeded',
    'provider': 'openrouter', 'model': 'test-model',
    'status_code': 403, 'error_message': 'Key limit exceeded',
    'timestamp': '2026-07-12T00:00:00Z',
}


class TestParseRecipients:
    def test_splits_and_strips(self):
        assert parse_recipients(' a@b.c , d@e.f,, ') == ['a@b.c', 'd@e.f']

    def test_empty(self):
        assert parse_recipients('') == []
        assert parse_recipients(None) == []


class TestLoadEmailConfig:
    def test_defaults_when_unset(self):
        cfg = load_email_config(_mock_db())
        assert cfg.enabled is False
        assert cfg.events == email_service.DEFAULT_EVENTS
        assert cfg.port == 587
        assert cfg.security == 'starttls'
        assert cfg.recipients == []

    def test_loads_values_and_secret(self):
        db = _mock_db({
            'email_enabled': 'true',
            'email_events': '["Episode Failed"]',
            'email_smtp_host': 'mail.example.com',
            'email_smtp_port': '465',
            'email_smtp_security': 'ssl',
            'email_smtp_username': 'user',
            'email_smtp_from': 'from@example.com',
            'email_recipients': 'a@b.c, d@e.f',
        }, secret='hunter2')
        cfg = load_email_config(db)
        assert cfg.enabled and cfg.port == 465 and cfg.security == 'ssl'
        assert cfg.events == ['Episode Failed']
        assert cfg.password == 'hunter2'
        assert cfg.recipients == ['a@b.c', 'd@e.f']

    def test_garbage_values_fall_back(self):
        db = _mock_db({
            'email_events': 'not json',
            'email_smtp_port': 'abc',
            'email_smtp_security': 'telnet',
        })
        cfg = load_email_config(db)
        assert cfg.events == email_service.DEFAULT_EVENTS
        assert cfg.port == 587
        assert cfg.security == 'starttls'

    def test_send_ready(self):
        assert is_send_ready(_cfg()) is True
        assert is_send_ready(_cfg(enabled=False)) is False
        assert is_send_ready(_cfg(host='')) is False
        assert is_send_ready(_cfg(recipients=[])) is False
        assert is_send_ready(_cfg(from_addr='')) is False


class TestFormatters:
    def test_all_events_have_formatters(self):
        assert set(FORMATTERS) == VALID_EVENTS

    def test_episode_failed(self):
        subject, rows, hint = FORMATTERS['Episode Failed'](EPISODE_CTX)
        assert subject == '[MinusPod] Episode Failed: My Show - Pilot'
        assert ('Error', 'boom') in rows
        assert hint is None

    def test_episode_processed(self):
        ctx = dict(EPISODE_CTX, event='Episode Processed')
        subject, rows, hint = FORMATTERS['Episode Processed'](ctx)
        assert subject == '[MinusPod] Episode Processed: My Show - Pilot'
        assert ('Ads removed', '3') in rows
        assert ('Time saved', '3:07') in rows
        assert hint is None

    def test_limit_exceeded_has_action(self):
        subject, rows, hint = FORMATTERS['Limit Exceeded'](ALERT_CTX)
        assert subject == '[MinusPod] Limit Exceeded: openrouter / test-model'
        assert ('Error', 'Key limit exceeded') in rows
        assert 'reprocess' in hint

    def test_auth_failure_action(self):
        _, _, hint = FORMATTERS['Auth Failure'](dict(ALERT_CTX, event='Auth Failure'))
        assert 'rotate' in hint

    def test_structural_rate_limit_rows(self):
        ctx = {'provider': 'groq', 'model': 'm', 'limit': 6000, 'used': 100,
               'requested': 9000, 'error_message': 'too big',
               'timestamp': '2026-07-12T00:00:00Z'}
        _, rows, hint = FORMATTERS['Rate Limit Structural'](ctx)
        assert ('Per-minute cap', '6000') in rows
        assert 'window' in hint

    def test_missing_values_render_dash(self):
        subject, rows, _ = FORMATTERS['Auth Failure']({})
        assert subject == '[MinusPod] Auth Failure: unknown / unknown'
        assert all(value == '-' for _, value in rows)


class TestBuildMessage:
    def setup_method(self):
        # Reset the logo cache so each test controls it.
        email_service._logo_bytes.cache_clear()

    def test_mime_structure_with_logo(self, monkeypatch, tmp_path):
        logo = tmp_path / 'logo.png'
        logo.write_bytes(b'\x89PNG fake')
        monkeypatch.setattr(email_service, 'LOGO_PATH', logo)
        msg = build_message('subj', [('A', '1')], 'do things',
                            'a@b.c', ['d@e.f', 'g@h.i'])
        assert msg['To'] == 'd@e.f, g@h.i'
        types = [p.get_content_type() for p in msg.walk()]
        assert types == ['multipart/alternative', 'text/plain',
                         'multipart/related', 'text/html', 'image/png']
        html = msg.get_body(preferencelist=('html',)).get_content()
        assert 'cid:' in html and 'Action:' in html
        plain = msg.get_body(preferencelist=('plain',)).get_content()
        assert 'A:' in plain and 'Action: do things' in plain

    def test_logo_missing_omits_image(self, monkeypatch, tmp_path):
        monkeypatch.setattr(email_service, 'LOGO_PATH', tmp_path / 'missing.png')
        msg = build_message('subj', [('A', '1')], None, 'a@b.c', ['d@e.f'])
        types = [p.get_content_type() for p in msg.walk()]
        assert types == ['multipart/alternative', 'text/plain', 'text/html']
        assert 'cid:' not in msg.get_body(preferencelist=('html',)).get_content()

    def test_html_escapes_values(self, monkeypatch, tmp_path):
        monkeypatch.setattr(email_service, 'LOGO_PATH', tmp_path / 'missing.png')
        msg = build_message('subj', [('Error', '<script>x</script>')], None,
                            'a@b.c', ['d@e.f'])
        html = msg.get_body(preferencelist=('html',)).get_content()
        assert '<script>' not in html
        assert '&lt;script&gt;' in html


class TestSend:
    def test_plain_smtp(self):
        with patch('email_service.smtplib.SMTP') as smtp_cls:
            email_service._send(_cfg(security='none'), MagicMock())
        smtp_cls.assert_called_once_with('mail.example.com', 587, timeout=10)
        conn = smtp_cls.return_value
        conn.starttls.assert_not_called()
        conn.login.assert_not_called()
        conn.send_message.assert_called_once()

    def test_starttls(self):
        with patch('email_service.smtplib.SMTP') as smtp_cls:
            email_service._send(_cfg(security='starttls'), MagicMock())
        conn = smtp_cls.return_value
        conn.starttls.assert_called_once()

    def test_ssl(self):
        with patch('email_service.smtplib.SMTP_SSL') as smtp_cls:
            email_service._send(_cfg(security='ssl', port=465), MagicMock())
        assert smtp_cls.call_args.args == ('mail.example.com', 465)
        assert smtp_cls.call_args.kwargs['timeout'] == 10

    def test_blocked_host_raises_before_connect(self):
        with patch('email_service.smtplib.SMTP') as smtp_cls:
            with pytest.raises(SSRFError):
                email_service._send(_cfg(host='169.254.169.254'), MagicMock())
        smtp_cls.assert_not_called()

    def test_login_only_with_both_creds(self):
        with patch('email_service.smtplib.SMTP') as smtp_cls:
            email_service._send(_cfg(username='u', password=None), MagicMock())
        smtp_cls.return_value.login.assert_not_called()
        with patch('email_service.smtplib.SMTP') as smtp_cls:
            email_service._send(_cfg(username='u', password='p'), MagicMock())
        smtp_cls.return_value.login.assert_called_once_with('u', 'p')


class TestSendEventEmail:
    def test_sends_for_subscribed_event(self):
        with patch('email_service.load_email_config', return_value=_cfg()), \
             patch('email_service._send') as send:
            send_event_email('Limit Exceeded', ALERT_CTX)
        send.assert_called_once()

    def test_skips_unsubscribed_event(self):
        with patch('email_service.load_email_config',
                   return_value=_cfg(events=['Episode Failed'])), \
             patch('email_service._send') as send:
            send_event_email('Limit Exceeded', ALERT_CTX)
        send.assert_not_called()

    def test_skips_when_not_ready(self):
        with patch('email_service.load_email_config',
                   return_value=_cfg(enabled=False)), \
             patch('email_service._send') as send:
            send_event_email('Limit Exceeded', ALERT_CTX)
        send.assert_not_called()

    def test_never_raises_on_smtp_failure(self):
        with patch('email_service.load_email_config', return_value=_cfg()), \
             patch('email_service._send', side_effect=OSError('boom')):
            send_event_email('Limit Exceeded', ALERT_CTX)  # must not raise

    def test_never_raises_on_config_failure(self):
        with patch('email_service.load_email_config', side_effect=RuntimeError('db down')):
            send_event_email('Limit Exceeded', ALERT_CTX)  # must not raise


class TestSendTestEmail:
    def test_not_configured(self):
        db = _mock_db()
        success, message = send_test_email(db)
        assert success is False
        assert 'not configured' in message

    def test_success(self):
        db = _mock_db({
            'email_enabled': 'true',
            'email_smtp_host': 'mail.example.com',
            'email_smtp_from': 'from@example.com',
            'email_recipients': 'a@b.c',
        })
        with patch('email_service._send') as send:
            success, message = send_test_email(db)
        assert success is True
        assert '1 recipient' in message
        send.assert_called_once()

    def test_undecryptable_password_diagnosed(self):
        db = _mock_db({
            'email_enabled': 'true',
            'email_smtp_host': 'mail.example.com',
            'email_smtp_username': 'user',
            'email_smtp_from': 'from@example.com',
            'email_recipients': 'a@b.c',
            'email_smtp_password': 'enc:v1:ciphertext',
        }, secret=None)
        success, message = send_test_email(db)
        assert success is False
        assert 'MINUSPOD_MASTER_PASSPHRASE' in message

    def test_failure_reports_error(self):
        db = _mock_db({
            'email_enabled': 'true',
            'email_smtp_host': 'mail.example.com',
            'email_smtp_from': 'from@example.com',
            'email_recipients': 'a@b.c',
        })
        with patch('email_service._send', side_effect=OSError('connection refused')):
            success, message = send_test_email(db)
        assert success is False
        assert 'connection refused' in message
