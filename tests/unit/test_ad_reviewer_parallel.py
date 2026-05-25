"""Tests for the parallel ad-review batch executor in src/ad_reviewer.

Covers:
- max_workers=1 preserves the original sequential behavior (regression).
- max_workers=N returns (verdict, ad) pairs in input order regardless of
  future completion order.
- _resolve_reviewer_parallel_ads clamps into [1, 32] and prefers DB over env.
"""
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from ad_reviewer import AdReviewer, ReviewVerdict, _resolve_reviewer_parallel_ads


def _make_reviewer():
    """Construct an AdReviewer with a no-op LLM client. Only _review_single is
    used by the batch executor and we stub it in each test."""
    llm_client = MagicMock()
    db = MagicMock()
    db.get_setting.return_value = None
    return AdReviewer(db=db, llm_client=llm_client, sponsor_service=MagicMock())


def _make_ad(i):
    return {
        'start': i * 60.0,
        'end': i * 60.0 + 20.0,
        'sponsor': f'sponsor-{i}',
    }


def _make_verdict(i):
    return ReviewVerdict(
        pool='accepted',
        pass_num=1,
        verdict='confirm',
        original_start=i * 60.0,
        original_end=i * 60.0 + 20.0,
        confidence=0.9,
        reasoning=f'verdict-{i}',
        model_used='test-model',
    )


class TestResolveReviewerParallelAds:
    def test_clamps_to_default_when_env_invalid(self):
        with patch.dict(os.environ, {'AD_REVIEWER_PARALLEL_ADS': '0'}, clear=False):
            with patch('llm_client._get_cached_setting', return_value=None):
                assert _resolve_reviewer_parallel_ads() == 4

    def test_db_overrides_env(self):
        with patch.dict(os.environ, {'AD_REVIEWER_PARALLEL_ADS': '4'}, clear=False):
            with patch('llm_client._get_cached_setting', return_value='8'):
                assert _resolve_reviewer_parallel_ads() == 8

    def test_db_value_clamped_to_max(self):
        with patch('llm_client._get_cached_setting', return_value='9999'):
            assert _resolve_reviewer_parallel_ads() == 32

    def test_garbage_db_falls_back_to_default(self):
        env = os.environ.copy()
        env.pop('AD_REVIEWER_PARALLEL_ADS', None)
        with patch.dict(os.environ, env, clear=True):
            with patch('llm_client._get_cached_setting', return_value='not a number'):
                assert _resolve_reviewer_parallel_ads() == 4


class TestRunReviewBatchOrdering:
    """_run_review_batch returns results in input-ad order even when futures
    complete in a different order."""

    def _stub_review_single(self, delay_map=None):
        delays = delay_map or {}

        def stub(*, ad, pool, pass_num, segments, episode_meta,
                 system_prompt, model, max_shift):
            idx = int(ad['sponsor'].split('-')[1])
            time.sleep(delays.get(idx, 0))
            updated_ad = dict(ad)
            updated_ad['reviewer_idx'] = idx
            return (_make_verdict(idx), updated_ad)

        return stub

    def test_sequential_path_preserves_order(self):
        reviewer = _make_reviewer()
        ads = [_make_ad(i) for i in range(5)]
        with patch.object(reviewer, '_review_single', side_effect=self._stub_review_single()):
            results = reviewer._run_review_batch(
                ads, pool='accepted', pass_num=1, segments=[], episode_meta={},
                system_prompt='', model='m', max_shift=20.0, max_workers=1,
            )
        assert [r[1]['reviewer_idx'] for r in results] == [0, 1, 2, 3, 4]

    def test_parallel_path_preserves_order(self):
        reviewer = _make_reviewer()
        ads = [_make_ad(i) for i in range(5)]
        # First ad slow on purpose so it completes last; ordering must still
        # reflect input position.
        with patch.object(reviewer, '_review_single',
                          side_effect=self._stub_review_single({0: 0.10})):
            results = reviewer._run_review_batch(
                ads, pool='accepted', pass_num=1, segments=[], episode_meta={},
                system_prompt='', model='m', max_shift=20.0, max_workers=4,
            )
        assert [r[1]['reviewer_idx'] for r in results] == [0, 1, 2, 3, 4]

    def test_empty_list_returns_empty(self):
        reviewer = _make_reviewer()
        with patch.object(reviewer, '_review_single') as stub:
            results = reviewer._run_review_batch(
                [], pool='accepted', pass_num=1, segments=[], episode_meta={},
                system_prompt='', model='m', max_shift=20.0, max_workers=4,
            )
        assert results == []
        stub.assert_not_called()

    def test_single_item_uses_sequential_path(self):
        """1 ad shouldn't spin up a thread pool; verify by stubbing the executor."""
        reviewer = _make_reviewer()
        ads = [_make_ad(0)]
        with patch.object(reviewer, '_review_single', side_effect=self._stub_review_single()), \
             patch('ad_reviewer.ThreadPoolExecutor') as mock_executor:
            reviewer._run_review_batch(
                ads, pool='accepted', pass_num=1, segments=[], episode_meta={},
                system_prompt='', model='m', max_shift=20.0, max_workers=4,
            )
        mock_executor.assert_not_called()
