"""Tests for positional prior hint injection into the detection prompt (issue #360)."""
import sys
import os
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector
from positional_prior import LearnedZone, PositionalPrior, format_prior_hint


SEGMENTS = [
    {'start': 0.0, 'end': 500.0, 'text': 'first half of the episode'},
    {'start': 500.0, 'end': 1000.0, 'text': 'second half of the episode'},
]


def _hint():
    prior = PositionalPrior(
        episodes_considered=8, median_duration=1000.0,
        zones=[LearnedZone(center=0.30, low=0.25, high=0.35,
                           support=7, boost=0.084)])
    return format_prior_hint(prior, 1000.0)


def _detect(positional_prior_hint):
    detector = AdDetector(api_key='test-key')
    run_windows = MagicMock(return_value=[])
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, '_detect_foreign_language_ads', return_value=[]), \
         patch.object(detector, 'get_system_prompt', return_value='system'), \
         patch.object(detector, 'get_model', return_value='model'), \
         patch.object(detector, '_get_podcast_sponsor_history', return_value=''), \
         patch.object(detector, '_run_windows', run_windows), \
         patch('ad_detector._resolve_parallel_windows', return_value=1), \
         patch('ad_detector.get_llm_timeout', return_value=60), \
         patch('ad_detector.get_llm_max_retries', return_value=1):
        result = detector.detect_ads(
            SEGMENTS, podcast_name='Test', episode_title='Ep', slug='test',
            episode_id='e1', positional_prior_hint=positional_prior_hint)
    assert result['status'] == 'success'
    return run_windows.call_args.kwargs['description_section']


class TestPriorHintInjection:

    def test_hint_present_with_prior(self):
        description_section = _detect(_hint())

        assert 'Historical ad-break positions' in description_section
        # 0.30 of the 1000s episode -> 5:00
        assert '5:00' in description_section
        assert 'do NOT report an ad' in description_section

    def test_hint_absent_without_prior(self):
        description_section = _detect("")

        assert 'Historical ad-break positions' not in description_section
