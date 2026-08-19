"""Structured is_ad verdict contract for the ad reviewer."""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='structured_verdict_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from dataclasses import dataclass
from unittest.mock import MagicMock

from ad_reviewer import AdReviewer, is_contradiction_hold


CONTRA = "This segment contains no advertising content."


def test_legacy_hold_still_fires_without_structured_field():
    assert is_contradiction_hold("confirmed", CONTRA) is True


def test_structured_true_suppresses_hold():
    assert is_contradiction_hold(
        "confirmed", CONTRA, structured_is_ad=True) is False


def test_structured_false_never_reaches_hold_path():
    # is_ad False is turned into a reject verdict upstream; reject
    # verdicts never hold
    assert is_contradiction_hold("reject", CONTRA) is False


def test_structured_none_is_legacy():
    assert is_contradiction_hold(
        "confirmed", CONTRA, structured_is_ad=None) is True


def _mock_segments():
    return [
        {'start': 0.0, 'end': 60.0, 'text': 'show content'},
        {'start': 60.0, 'end': 120.0, 'text': 'before ad'},
        {'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'},
        {'start': 180.0, 'end': 240.0, 'text': 'after ad'},
        {'start': 240.0, 'end': 300.0, 'text': 'more show content'},
    ]


def _mock_episode_meta():
    return {
        'podcast_name': 'Test Podcast', 'episode_title': 'Test Episode',
        'episode_description': 'desc', 'podcast_description': 'pod desc',
        'slug': 'test-pod', 'episode_id': 'ep1', 'podcast_id': 'p1',
    }


def _build_reviewer(db_settings=None):
    db_settings = db_settings or {}
    db = MagicMock()
    db.get_setting.side_effect = lambda key: db_settings.get(key)
    db.get_connection.return_value = MagicMock()
    llm_client = MagicMock()
    return AdReviewer(db=db, llm_client=llm_client, sponsor_service=None)


@dataclass
class _LLMResp:
    content: str
    model: str = "test-model"


def _resp(body: str) -> _LLMResp:
    return _LLMResp(content=body)


def test_structured_false_rejects_regardless_of_bounds():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"is_ad": false, "start": 120.0, "end": 180.0, '
        '"reason": "editorial mention"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    verdict = result.verdicts[0]
    assert verdict.verdict == 'reject'
    assert verdict.structured_is_ad is False
    assert result.accepted_after_review == []
    assert result.held_by_contradiction == []


def test_structured_true_suppresses_pool_split_hold():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"is_ad": true, "start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{CONTRA}"}}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    verdict = result.verdicts[0]
    assert verdict.verdict == 'confirmed'
    assert verdict.structured_is_ad is True
    assert result.held_by_contradiction == []
    assert result.accepted_after_review == [ad]


def test_missing_is_ad_field_is_legacy_byte_identical():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    verdict = result.verdicts[0]
    assert verdict.structured_is_ad is None
    assert verdict.verdict == 'confirmed'
    assert verdict.success is True
    assert result.accepted_after_review == [ad]
    assert result.held_by_contradiction == []


def test_is_ad_as_string_is_treated_as_absent():
    # A non-bool is_ad (e.g. the model emits the string "false" instead of
    # the JSON literal) must fall through to the legacy path, not reject.
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"is_ad": "false", "start": 120.0, "end": 180.0, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    verdict = result.verdicts[0]
    assert verdict.structured_is_ad is None
    assert verdict.verdict == 'confirmed'
    assert result.accepted_after_review == [ad]


def test_contradiction_guard_fired_logs_once_with_context(caplog):
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{CONTRA}"}}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    with caplog.at_level('INFO', logger='ad_reviewer'):
        reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=_mock_segments(), episode_meta=_mock_episode_meta(),
            pass_num=1, pass_model='claude-test',
        )
    fired = [line for line in caplog.text.splitlines()
             if 'reviewer_contradiction_guard_fired' in line]
    assert len(fired) == 1, f"expected exactly one guard-fired log, got {fired}"
    assert 'slug=test-pod' in fired[0]
    assert 'episode_id=ep1' in fired[0]
    assert 'start=120.0' in fired[0] and 'end=180.0' in fired[0]


def test_structured_true_suppression_logs_once_with_context(caplog):
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"is_ad": true, "start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{CONTRA}"}}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    with caplog.at_level('INFO', logger='ad_reviewer'):
        reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=_mock_segments(), episode_meta=_mock_episode_meta(),
            pass_num=1, pass_model='claude-test',
        )
    fired = [line for line in caplog.text.splitlines()
             if 'reviewer_contradiction_suppressed_by_structured' in line]
    assert len(fired) == 1, f"expected exactly one suppression log, got {fired}"
    assert 'slug=test-pod' in fired[0]
    assert 'episode_id=ep1' in fired[0]


def test_structured_false_in_resurrection_pool_rejects_not_resurrects():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"is_ad": false, "start": 120.0, "end": 180.0, '
        '"reason": "not a real ad"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.65}
    result = reviewer.review(
        accepted_ads=[], resurrection_eligible=[ad],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    verdict = result.verdicts[0]
    assert verdict.verdict == 'reject'
    assert verdict.structured_is_ad is False
    assert result.resurrected == []
    assert result.accepted_after_review == []
