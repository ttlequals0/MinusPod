"""An episode run with the LLM provider set to none makes no model call."""
import logging
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='llm_optional_pipeline_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import llm_client  # noqa: E402
from api.episodes import runs  # noqa: E402
from main_app.pipeline import finalize as finalize_stage  # noqa: E402
from main_app.pipeline import review as review_stage  # noqa: E402
from llm_client import ProviderAccountChangedError  # noqa: E402
import main_app.processing as processing  # noqa: E402
from main_app.pipeline import admission  # noqa: E402
from ad_chapters import AdChapterConfig  # noqa: E402
from ad_detector import AdDetector  # noqa: E402
from text_pattern_matcher import TextMatch  # noqa: E402
from tests.unit.pipeline_run_support import (  # noqa: E402
    REFUSED_CALLER_ALLOWLIST, _install_recorder, _row, _run_pipeline,
)


@pytest.fixture
def model_calls(monkeypatch, caplog):
    """Fails the test on a client factory or request call, an unlisted refused call, or a WARNING."""
    calls = _install_recorder(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        yield calls
    assert calls == []
    assert {caller for _, caller in calls.refused} <= REFUSED_CALLER_ALLOWLIST, calls.refused
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.fixture
def no_model_calls(model_calls):
    return model_calls


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
    assert detector.initialize_client() is None


def test_admission_needs_no_account_under_none(provider_none, no_model_calls):
    with patch.object(admission, 'Database', MagicMock()):
        assert admission._active_phases_for_admission('feed', 'ep1') == {}
        assert admission._required_providers_for_admission('feed', 'ep1') == []
    assert admission._resolve_route_snapshot() is None


def _detector_with_matcher(matches):
    matcher = MagicMock()
    matcher.find_matches.return_value = matches
    detector = processing.ad_detector
    return (patch.object(detector, 'db', MagicMock()),
            patch.object(detector, 'text_pattern_matcher', matcher),
            patch.object(detector, 'audio_fingerprinter', None),
            patch.object(detector, 'pattern_service', None)), matcher


def _run_with_matcher(matches, **kwargs):
    patches, matcher = _detector_with_matcher(matches)
    with patches[0], patches[1], patches[2], patches[3]:
        return _run_pipeline(_row(), real_detect=True, refine_cuts_all=True, **kwargs), matcher


def test_full_reprocess_under_none_still_matches_patterns(provider_none, no_model_calls):
    match = TextMatch(pattern_id=7, start=1.0, end=9.0, confidence=0.95, sponsor='Acme')
    m, matcher = _run_with_matcher([match], episode_row={'reprocess_mode': 'full'})
    assert m['result'] is True
    matcher.find_matches.assert_called_once()
    assert [ad['pattern_id'] for ad in m['refine'].call_args.args[2]] == [7]


def test_full_reprocess_with_a_provider_skips_patterns(provider_anthropic):
    m, matcher = _run_with_matcher([], episode_row={'reprocess_mode': 'full'})
    matcher.find_matches.assert_not_called()
    assert m['detect'].call_args.args[3] is True


def test_plain_reprocess_under_none_runs_model_free(provider_none, no_model_calls):
    m = _run_pipeline(_row(), episode_row={'reprocess_mode': 'reprocess'})
    assert m['result'] is True
    assert m['detect'].call_args.kwargs['skip_llm'] is True


def test_manual_approval_holds_a_none_run_with_the_real_finalize(provider_none, no_model_calls):
    with patch.object(finalize_stage, '_persist_episode_state') as persist:
        m = _run_pipeline(_row(), manual_approval=True, real_stages=('finalize',))
    assert m['result'] is True
    assert persist.call_args.kwargs['approval']['approval_pending_at']
    assert not persist.call_args.kwargs['detection_degraded']


def test_no_approval_setting_publishes_a_none_run(provider_none, no_model_calls):
    with patch.object(finalize_stage, '_persist_episode_state') as persist:
        _run_pipeline(_row(), manual_approval=False, real_stages=('finalize',))
    assert persist.call_args.kwargs['approval'] == {'approval_pending_at': None}


def test_stale_route_snapshot_does_not_fail_a_none_run(provider_none):
    snapshot = {'detection': {'credential_slot': 'primary', 'account_id': 'old'}}
    with patch.object(admission, 'route_account_mismatch', return_value=('old', 'new')):
        admission._assert_route_snapshot_current(snapshot)


def test_stale_route_snapshot_still_fails_a_provider_run(provider_anthropic):
    snapshot = {'detection': {'credential_slot': 'primary', 'account_id': 'old'}}
    with patch.object(admission, 'route_account_mismatch', return_value=('old', 'new')), \
         pytest.raises(ProviderAccountChangedError):
        admission._assert_route_snapshot_current(snapshot)


@pytest.mark.parametrize('provider,expected_calls', [('none', 0), ('anthropic', 1)])
def test_low_yield_rerun_is_suppressed_under_none(monkeypatch, provider, expected_calls):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: provider)
    resolver = MagicMock(return_value='off')
    monkeypatch.setattr(finalize_stage, 'resolve_low_ad_yield_action', resolver)
    with patch.object(finalize_stage, 'db', MagicMock()):
        finalize_stage._maybe_fire_low_ad_yield_action(
            'feed', 'ep1', 'https://example.com/e.mp3', 'T', 'P', 'D', None, {}, {},
            podcast_row={})
    assert resolver.call_count == expected_calls


def test_none_run_with_real_stages_makes_no_model_call(provider_none, model_calls):
    match = TextMatch(pattern_id=7, start=1.0, end=9.0, confidence=0.95, sponsor='Acme')
    patches, matcher = _detector_with_matcher([match])
    with patches[0], patches[1], patches[2], patches[3], \
         patch.object(finalize_stage, '_persist_episode_state'):
        m = _run_pipeline(_row(), real_detect=True, enable_ad_review=True,
                          real_stages=('refine', 'reviewer', 'verify', 'assets', 'finalize'))
    assert m['result'] is True
    matcher.find_matches.assert_called_once()
    assert m['verify'].call_args.kwargs['skip_verification'] is True
    m['reviewer'].assert_not_called()
    assert model_calls == []


def test_none_run_still_transcribes(provider_none, no_model_calls):
    m = _run_pipeline(_row())
    assert m['result'] is True
    m['dat'].assert_called_once()


def test_none_run_applies_cross_fetch_differential_detections(provider_none, no_model_calls):
    differential = {'regions': [{'kind': 'differential', 'corr': 0.1, 'start_s': 20.0, 'end_s': 45.0}]}
    patches, matcher = _detector_with_matcher([])
    matcher.find_matches.return_value = []
    db = MagicMock()
    db.get_setting_float.side_effect = lambda key, default=None: default
    with patch.object(processing.ad_detector, 'db', db), patches[1], patches[2], patches[3]:
        m = _run_pipeline(_row(), real_detect=True, refine_cuts_all=True, differential=differential)
    assert m['result'] is True
    m['diff_fetch'].assert_called_once()
    cut_ads = m['refine'].call_args.args[2]
    assert [(ad['detection_stage'], ad['start'], ad['end']) for ad in cut_ads] == [
        ('dai_differential', 20.0, 45.0)]


def test_recorder_sees_detection_with_a_real_provider(provider_anthropic, monkeypatch):
    calls = _install_recorder(monkeypatch)
    patches, _ = _detector_with_matcher([])
    with patches[0], patches[1], patches[2], patches[3], \
         patch.object(processing.ad_detector, '_api_key_override', 'test-key'), \
         patch.object(finalize_stage, '_persist_episode_state'):
        _run_pipeline(_row(), real_detect=True, enable_ad_review=True,
                      real_stages=('refine', 'reviewer', 'verify', 'assets', 'finalize'))
    assert 'get_llm_client' in calls
    assert calls, 'recorder saw no model call on a real-provider run'


def test_recorder_sees_the_reviewer_with_a_real_provider(provider_anthropic, monkeypatch):
    calls = _install_recorder(monkeypatch)
    ad = {'start': 1.0, 'end': 9.0, 'confidence': 0.95, 'reason': 'sponsor read', 'was_cut': True}
    review_db = MagicMock()
    review_db.get_setting.return_value = 'true'
    with patch.object(review_stage, 'db', review_db):
        review_stage._run_ad_reviewer(
            'mode-feed', 'ep1', 'mode-feed', [ad], [ad], [{'start': 0.0, 'end': 10.0, 'text': 'hi'}],
            'Podcast', 'Episode', '', '', 0.8, 1, 'test-model')
    assert calls, 'recorder saw no model call from the reviewer'


AD_CHAPTERS = AdChapterConfig(
    actions={'sponsor': 'cut'}, include_held=True, title_format='[mp:{category}]',
    held_title_format='[mp:{category}?]', resume_title='Show', min_confidence=0.5)


def test_none_run_with_the_real_chapters_stage_makes_no_model_call(provider_none, model_calls):
    import chapters_generator
    from main_app.pipeline import publish as publish_stage
    match = TextMatch(pattern_id=7, start=1.0, end=9.0, confidence=0.95, sponsor='Acme')
    patches, _ = _detector_with_matcher([match])
    generator = MagicMock()
    with patches[0], patches[1], patches[2], patches[3], \
         patch.object(chapters_generator, 'ChaptersGenerator', generator), \
         patch.object(publish_stage, 'resolve_ad_chapter_config', return_value=AD_CHAPTERS), \
         patch.object(publish_stage, 'merge_ad_chapters',
                      wraps=publish_stage.merge_ad_chapters) as merge, \
         patch.object(publish_stage, 'probe_chapters', return_value=[]), \
         patch.object(publish_stage, 'embed_chapters', return_value=True), \
         patch.object(finalize_stage, '_persist_episode_state'):
        m = _run_pipeline(_row(), real_detect=True, enable_ad_review=True, refine_cuts_all=True,
                          settings={'chapters_enabled': 'true'},
                          real_stages=('reviewer', 'verify', 'assets', 'finalize'))
    assert m['result'] is True
    generator.assert_not_called()
    merge.assert_called_once()
    run_stats = m['finalize'].call_args.kwargs['run_stats']
    assert 'chapters_degraded' not in run_stats
    assert model_calls == []


@pytest.fixture
def retry_feed(app_client):
    from api import get_database
    from tests.app_bootstrap import authenticate_test_client
    db = get_database()
    db.create_podcast('retry-none-feed', 'https://example.com/feed.xml', 'Retry None')
    db.upsert_episode('retry-none-feed', 'aa11bb22cc33', title='Episode', status='processed',
                      original_url='https://example.com/e.mp3')
    token = authenticate_test_client(app_client)
    yield app_client, db, token
    db.delete_podcast('retry-none-feed')


def test_retry_ad_detection_under_none_applies_patterns(retry_feed, provider_none, no_model_calls):
    client, db, token = retry_feed
    match = TextMatch(pattern_id=7, start=1.0, end=9.0, confidence=0.95, sponsor='Acme')
    storage = MagicMock()
    storage.get_transcript.return_value = 'transcript'
    with patch.object(runs, 'get_storage', return_value=storage), \
         patch.object(runs, 'parse_transcript_segments',
                      return_value=[{'start': 0.0, 'end': 10.0, 'text': 'hello'}]), \
         patch('text_pattern_matcher.TextPatternMatcher.find_matches', return_value=[match]):
        response = client.post('/api/v1/feeds/retry-none-feed/episodes/aa11bb22cc33/retry-ad-detection',
                               headers=token)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['adsFound'] == 1
    assert db.get_episode('retry-none-feed', 'aa11bb22cc33')['ad_detection_status'] == 'success'


def test_retry_ad_detection_with_a_provider_still_reports_a_failed_detection(retry_feed, provider_anthropic):
    client, db, token = retry_feed
    storage = MagicMock()
    storage.get_transcript.return_value = 'transcript'
    failed = {'ads': [], 'status': 'failed', 'error': 'boom', 'retryable': True}
    with patch.object(runs, 'get_storage', return_value=storage), \
         patch.object(runs, 'parse_transcript_segments',
                      return_value=[{'start': 0.0, 'end': 10.0, 'text': 'hello'}]), \
         patch('ad_detector.AdDetector.process_transcript', return_value=failed) as process:
        response = client.post('/api/v1/feeds/retry-none-feed/episodes/aa11bb22cc33/retry-ad-detection',
                               headers=token)
    assert process.call_args.kwargs['skip_llm'] is False
    assert response.status_code == 500
    assert response.get_json()['status'] == 'failed'
    assert db.get_episode('retry-none-feed', 'aa11bb22cc33')['ad_detection_status'] == 'failed'
