"""An episode run with the LLM provider set to none makes no model call."""
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='llm_optional_pipeline_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import llm_client  # noqa: E402
import main_app.processing as processing  # noqa: E402
from main_app.pipeline import admission  # noqa: E402
from ad_detector import AdDetector  # noqa: E402
from text_pattern_matcher import TextMatch  # noqa: E402
from tests.unit.test_processing_mode import _row, _run_pipeline  # noqa: E402


def _all_client_classes(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_client_classes(sub)


@pytest.fixture
def no_model_calls(monkeypatch):
    """Any attempt to build a client or call a model fails the test."""
    def boom(*args, **kwargs):
        raise AssertionError('model call attempted while the LLM provider is none')
    monkeypatch.setattr(llm_client, '_build_client', boom)
    for cls in (llm_client.LLMClient, *_all_client_classes(llm_client.LLMClient)):
        for name in ('messages_create', 'list_models', 'verify_connection',
                     'probe_once', 'probe_json_format_support'):
            if name in vars(cls):
                monkeypatch.setattr(cls, name, boom)
    llm_client._client_cache.clear()


@pytest.fixture
def provider_none(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')


@pytest.fixture
def provider_anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')


def test_none_run_is_model_free(provider_none, no_model_calls):
    m = _run_pipeline(_row(), enable_ad_review=True)
    assert m['result'] is True
    assert m['detect'].call_args.kwargs['skip_llm'] is True
    assert m['refine'].call_args.kwargs['apply_heuristic_rolls'] is False
    assert m['verify'].call_args.kwargs['skip_verification'] is True
    m['reviewer'].assert_not_called()


def test_none_run_skips_reservation_and_llm_probes(provider_none, no_model_calls):
    m = _run_pipeline(_row())
    m['db'].reserve_provider_spend.assert_not_called()
    m['db'].reconcile_provider_spend.assert_not_called()
    m['probe_selector'].assert_called_once_with({}, whisper_required=True)
    m['ensure_probes'].assert_called_once_with([])


def test_none_run_is_not_degraded(provider_none, no_model_calls):
    m = _run_pipeline(_row())
    run_stats = m['finalize'].call_args.kwargs['run_stats']
    assert run_stats['verification_skipped'] is True
    assert not any(key.endswith('degraded') for key in run_stats)
    assert m['result'] is True


def test_real_provider_run_is_unchanged(provider_anthropic):
    m = _run_pipeline(_row(), enable_ad_review=True,
                      route_snapshot={'detection': {'credential_slot': 'primary',
                                                    'provider_key': 'anthropic'}})
    assert m['result'] is True
    assert m['detect'].call_args.kwargs['skip_llm'] is False
    assert m['refine'].call_args.kwargs['apply_heuristic_rolls'] is True
    assert m['verify'].call_args.kwargs['skip_verification'] is False
    m['reviewer'].assert_called_once()
    m['db'].reserve_provider_spend.assert_called_once()


def test_none_run_cuts_text_pattern_match_without_a_model(provider_none, no_model_calls):
    match = TextMatch(pattern_id=7, start=1.0, end=9.0, confidence=0.95, sponsor='Acme')
    matcher = MagicMock()
    matcher.find_matches.return_value = [match]
    detector = processing.ad_detector
    with patch.object(detector, 'db', MagicMock()), \
         patch.object(detector, 'text_pattern_matcher', matcher), \
         patch.object(detector, 'audio_fingerprinter', None), \
         patch.object(detector, 'pattern_service', None):
        m = _run_pipeline(_row(), real_detect=True, refine_cuts_all=True)
    assert m['result'] is True
    cut_ads = m['refine'].call_args.args[2]
    assert [ad['pattern_id'] for ad in cut_ads] == [7]
    assert m['detect'].call_args.kwargs['skip_llm'] is True


def test_none_run_with_no_detections_publishes_uncut(provider_none, no_model_calls):
    matcher = MagicMock()
    matcher.find_matches.return_value = []
    detector = processing.ad_detector
    with patch.object(detector, 'db', MagicMock()), \
         patch.object(detector, 'text_pattern_matcher', matcher), \
         patch.object(detector, 'audio_fingerprinter', None), \
         patch.object(detector, 'pattern_service', None):
        m = _run_pipeline(_row(), real_detect=True, refine_cuts_all=True)
    assert m['result'] is True
    assert m['refine'].call_args.args[2] == []
    m['finalize'].assert_called_once()


def test_detector_builds_no_client_under_none(provider_none, no_model_calls):
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    detector.initialize_client()


def test_admission_needs_no_account_under_none(provider_none, no_model_calls):
    with patch.object(admission, 'Database', MagicMock()):
        assert admission._active_phases_for_admission('feed', 'ep1') == {}
        assert admission._required_providers_for_admission('feed', 'ep1') == []
    assert admission._resolve_route_snapshot() is None
