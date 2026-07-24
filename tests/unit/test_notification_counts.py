"""Tests for held/not-cut counts in Episode Processed notifications (Task 13).

Covers WebhookPayload -> _build_context field passthrough, email formatter
row visibility, and the marker-counting helpers used at the Episode
Processed fire site.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import count_not_cut, count_pending_review
from webhook_service import WebhookPayload, _build_context, EVENT_EPISODE_PROCESSED
from email_service import _fmt_episode_processed


def _payload(**overrides):
    defaults = dict(
        event=EVENT_EPISODE_PROCESSED,
        episode_id='ep1',
        slug='my-pod',
        episode_title='My Episode',
        processing_time=30.0,
        llm_cost=0.0,
    )
    defaults.update(overrides)
    return WebhookPayload(**defaults)


class TestBuildContextCounts:

    def test_context_includes_held_and_not_cut(self):
        payload = _payload(ads_held=2, ads_not_cut=1)
        ctx = _build_context(payload)
        assert ctx['episode']['ads_held'] == 2
        assert ctx['episode']['ads_not_cut'] == 1

    def test_context_defaults_to_zero_when_omitted(self):
        payload = _payload()
        ctx = _build_context(payload)
        assert ctx['episode']['ads_held'] == 0
        assert ctx['episode']['ads_not_cut'] == 0


class TestEmailFormatterRows:

    def _ctx(self, ads_held, ads_not_cut):
        return {
            'event': EVENT_EPISODE_PROCESSED,
            'timestamp': '2026-07-23T00:00:00Z',
            'podcast': {'name': 'My Show', 'slug': 'my-show'},
            'episode': {
                'id': 'ep1', 'title': 'Pilot', 'slug': 'my-show',
                'url': 'http://server/ui/feeds/my-show/episodes/ep1',
                'ads_removed': 3, 'processing_time': '1:02',
                'llm_cost_display': '$0.01', 'time_saved': '3:07',
                'ads_held': ads_held, 'ads_not_cut': ads_not_cut,
            },
        }

    def test_rows_present_when_nonzero(self):
        _, rows, _ = _fmt_episode_processed(self._ctx(2, 1))
        labels = dict(rows)
        assert labels['Ads held for review'] == '2'
        assert labels['Detections not cut'] == '1'

    def test_rows_absent_when_zero(self):
        _, rows, _ = _fmt_episode_processed(self._ctx(0, 0))
        labels = [label for label, _ in rows]
        assert 'Ads held for review' not in labels
        assert 'Detections not cut' not in labels


class TestFireSiteCounting:
    """Marker-counting helpers as used at the Episode Processed fire site."""

    def test_held_and_not_cut_from_marker_list(self):
        markers = [
            {'held_for_review': True, 'was_cut': False},   # pending review
            {'held_for_review': True, 'was_cut': False},   # pending review
            {'held_for_review': False, 'was_cut': False},  # rejected, not pending
            {'was_cut': True},
            {'was_cut': True},
            {'was_cut': True},
        ]
        assert count_pending_review(markers) == 2
        assert count_not_cut(markers) == 1
