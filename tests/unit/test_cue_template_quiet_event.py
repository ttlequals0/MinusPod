"""Tests for the Cue Template Quiet notification event (issue #599)."""
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue_quiet_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

import pytest

import email_service
import webhook_service
from webhook_service import (
    EVENT_CUE_TEMPLATE_QUIET,
    VALID_EVENTS,
    fire_cue_template_quiet_event,
)
from tests.unit.thread_fakes import SyncThread

import main_app.processing as processing


@pytest.fixture(autouse=True)
def _reset_alert_dedup():
    webhook_service._last_alert_time.clear()
    yield
    webhook_service._last_alert_time.clear()


class TestEventRegistration:
    def test_event_in_valid_events(self):
        assert EVENT_CUE_TEMPLATE_QUIET in VALID_EVENTS

    def test_email_formatter_registered(self):
        assert EVENT_CUE_TEMPLATE_QUIET in email_service.FORMATTERS

    def test_email_default_events_include_it(self):
        assert EVENT_CUE_TEMPLATE_QUIET in email_service.DEFAULT_EVENTS


class TestFireCueTemplateQuietEvent:
    @staticmethod
    def _fire(template_id=3):
        fire_cue_template_quiet_event(
            slug='show-a',
            podcast_name='Show A',
            template_id=template_id,
            template_label='break stinger',
            last_match_at='2026-03-01T00:00:00Z',
        )

    def test_dispatches_to_matching_webhook_and_email(self):
        webhook = {'enabled': True, 'events': [EVENT_CUE_TEMPLATE_QUIET],
                   'url': 'https://hooks.example.com/x'}
        with patch.object(webhook_service.threading, 'Thread', SyncThread), \
             patch.object(webhook_service, 'load_webhooks', return_value=[webhook]), \
             patch.object(webhook_service, '_prepare_and_dispatch') as dispatch, \
             patch.object(webhook_service.email_service, 'send_event_email') as email:
            self._fire()

        assert dispatch.call_count == 1
        ctx = dispatch.call_args[0][1]
        assert ctx['event'] == EVENT_CUE_TEMPLATE_QUIET
        assert ctx['template']['id'] == 3
        assert ctx['template']['label'] == 'break stinger'
        assert ctx['podcast']['slug'] == 'show-a'
        email.assert_called_once_with(EVENT_CUE_TEMPLATE_QUIET, ctx)

    def test_dedup_is_per_template(self):
        """A different template on the same feed alerts independently of an
        already-deduped template (own dedup_key), same burst-cap semantics
        as Feed Refresh Failed."""
        fake_now = [1_000_000.0]
        with patch.object(webhook_service.threading, 'Thread', SyncThread), \
             patch.object(webhook_service, 'load_webhooks', return_value=[]), \
             patch.object(webhook_service.time, 'time', lambda: fake_now[0]), \
             patch.object(webhook_service.email_service, 'send_event_email') as email:
            self._fire(template_id=3)
            self._fire(template_id=4)  # inside burst cap: suppressed
            assert email.call_count == 1

            fake_now[0] += webhook_service._ALERT_BURST_SECS + 1
            self._fire(template_id=4)  # past burst cap: fires
            self._fire(template_id=3)  # own 5-min dedup: suppressed

        assert email.call_count == 2


class TestEmailFormatter:
    def test_formatter_output(self):
        subject, rows, hint = email_service._fmt_cue_template_quiet({
            'podcast': {'name': 'Show A', 'slug': 'show-a'},
            'template': {'id': 3, 'label': 'break stinger'},
            'last_match_at': '2026-03-01T00:00:00Z',
            'timestamp': '2026-07-14T00:00:00Z',
        })
        assert 'Show A' in subject
        labels = [label for label, _ in rows]
        assert 'Template' in labels
        assert 'Last matched' in labels
        assert hint


class TestQuietTemplatesToNotify:
    """Unit tests for the pure fire-condition helper (no DB, no threads)."""

    def test_filters_to_quiet_and_enabled(self):
        activity = [
            {'templateId': 1, 'quiet': True, 'lastMatchAt': 'x', 'matchedEpisodes': 3},
            {'templateId': 2, 'quiet': False, 'lastMatchAt': 'y', 'matchedEpisodes': 5},
            {'templateId': 3, 'quiet': True, 'lastMatchAt': 'z', 'matchedEpisodes': 1},
        ]
        result = processing._quiet_templates_to_notify(activity, enabled_template_ids={1})
        assert [a['templateId'] for a in result] == [1]

    def test_quiet_but_disabled_template_excluded(self):
        activity = [{'templateId': 9, 'quiet': True, 'lastMatchAt': 'x', 'matchedEpisodes': 2}]
        result = processing._quiet_templates_to_notify(activity, enabled_template_ids=set())
        assert result == []

    def test_no_activity_yields_nothing(self):
        assert processing._quiet_templates_to_notify([], enabled_template_ids={1, 2}) == []


class TestNotifyQuietCueTemplates:
    def test_fires_for_each_enabled_quiet_template(self):
        with patch.object(processing, 'db') as db, \
             patch.object(processing, 'fire_cue_template_quiet_event') as fire:
            db.cue_template_recent_activity.return_value = [
                {'templateId': 1, 'quiet': True, 'lastMatchAt': '2026-01-01T00:00:00Z',
                 'matchedEpisodes': 3},
                {'templateId': 2, 'quiet': False, 'lastMatchAt': '2026-02-01T00:00:00Z',
                 'matchedEpisodes': 5},
            ]
            cue_templates = [
                {'id': 1, 'label': 'ding', 'enabled': True},
                {'id': 2, 'label': 'dong', 'enabled': True},
            ]
            processing._notify_quiet_cue_templates('show-a', 'Show A', podcast_id=1,
                                                    cue_templates=cue_templates)

        fire.assert_called_once_with('show-a', 'Show A', 1, 'ding', '2026-01-01T00:00:00Z')

    def test_disabled_quiet_template_not_fired(self):
        with patch.object(processing, 'db') as db, \
             patch.object(processing, 'fire_cue_template_quiet_event') as fire:
            db.cue_template_recent_activity.return_value = [
                {'templateId': 1, 'quiet': True, 'lastMatchAt': '2026-01-01T00:00:00Z',
                 'matchedEpisodes': 3},
            ]
            cue_templates = [{'id': 1, 'label': 'ding', 'enabled': False}]
            processing._notify_quiet_cue_templates('show-a', 'Show A', podcast_id=1,
                                                    cue_templates=cue_templates)

        fire.assert_not_called()

    def test_db_failure_is_swallowed(self):
        with patch.object(processing, 'db') as db, \
             patch.object(processing, 'fire_cue_template_quiet_event') as fire:
            db.cue_template_recent_activity.side_effect = RuntimeError('boom')
            processing._notify_quiet_cue_templates('show-a', 'Show A', podcast_id=1,
                                                    cue_templates=[])

        fire.assert_not_called()
