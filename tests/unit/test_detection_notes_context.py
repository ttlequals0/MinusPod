"""Feed detection notes (#709) travel as their own prompt block, not inside the description."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('detection_notes_test_')
from ad_detector import AdDetector
from tests.unit.reviewer_test_utils import _build_reviewer, _mock_episode_meta

NOTES = 'One ad zone mid-episode: jingle, host reads, then a house ad.'
BLOCK_HEAD = 'Operator notes for this show (written by the person running this server;'
SEGMENTS = [{'start': 0.0, 'end': 500.0, 'text': 'first half of the episode'}]


def _detect_section(**kwargs):
    detector = AdDetector(api_key='test-key')
    run_windows = MagicMock(return_value=[])
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, '_detect_foreign_language_ads', return_value=[]), \
         patch.object(detector, 'get_system_prompt', return_value='system'), \
         patch.object(detector, 'get_model', return_value='model'), \
         patch.object(detector, '_build_known_pattern_hint', return_value=''), \
         patch.object(detector, '_run_windows', run_windows), \
         patch('ad_detector._resolve_parallel_windows', return_value=1), \
         patch('llm_route.llm_client.get_llm_timeout', return_value=60), \
         patch('llm_route.llm_client.get_llm_max_retries', return_value=1):
        detector.detect_ads(SEGMENTS, podcast_name='Test', episode_title='Ep',
                            slug='test', episode_id='e1',
                            episode_description='Episode text.', **kwargs)
    return run_windows.call_args.kwargs['description_section']


def test_detector_block_after_podcast_description_before_episode():
    section = _detect_section(podcast_description='A show.', detection_notes=NOTES)
    expected = (
        BLOCK_HEAD + ' they describe how this show structures its ads and override '
        'the general guidance above when they apply):\n' + NOTES + '\n\n')
    assert expected in section
    assert (section.index('Podcast Description:') < section.index(BLOCK_HEAD)
            < section.index('Episode Description'))


def test_detector_block_survives_long_podcast_description():
    section = _detect_section(podcast_description='word ' * 600, detection_notes=NOTES)
    assert NOTES in section
    assert BLOCK_HEAD in section


@pytest.mark.parametrize('notes', [None, '', '   '])
def test_detector_no_block_without_notes(notes):
    assert BLOCK_HEAD not in _detect_section(podcast_description='A show.',
                                             detection_notes=notes)


def _reviewer_prompt(**meta):
    reviewer = _build_reviewer({'review_prompt': 'review'})
    return reviewer._build_user_prompt(
        ad={'start': 120.0, 'end': 180.0},
        segments=[{'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'}],
        episode_meta={**_mock_episode_meta(), **meta}, pool='accepted')


def test_reviewer_prompt_has_notes_block_after_description():
    prompt = _reviewer_prompt(podcast_description='A show.', detection_notes=NOTES)
    assert BLOCK_HEAD in prompt
    assert prompt.index('Podcast description:') < prompt.index(BLOCK_HEAD)
    assert NOTES in prompt


@pytest.mark.parametrize('notes', [None, ''])
def test_reviewer_prompt_no_block_without_notes(notes):
    assert BLOCK_HEAD not in _reviewer_prompt(detection_notes=notes)
