"""Queue admission explanation: why a waiting job is not starting, scored
from the same resolved phase set admission itself gates on.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR',
                      tempfile.mkdtemp(prefix='admission-explain-test-'))

from tests.app_bootstrap import bootstrap

bootstrap('admission_explain_test_')

from datetime import timedelta  # noqa: E402

from main_app import db  # noqa: E402
from main_app.processing import admission_explanation  # noqa: E402
from llm_route import account_identity  # noqa: E402
from rate_limit_hold import clear_all_holds, record_hold_until  # noqa: E402
from utils.time import ISO_FORMAT, utc_now  # noqa: E402

SLUG = 'admission-explain-feed'
EP = 'a1b2c3d4e5f6'


def _route(provider, slot='primary', base_url=None, account_id=None):
    return {'provider_key': provider, 'configured_model': 'model-x',
            'base_url': base_url, 'credential_slot': slot,
            'account_id': account_id}


SNAPSHOT = {
    'detection': _route('provider-a'),
    'review': _route('provider-a'),
    'verification': _route('provider-b'),
    'chapters': _route('provider-c'),
}

GATES = {'review': True, 'chapters_enabled': 'true'}


def _hold(provider, slot='primary', manual=False, minutes=10):
    reset = (utc_now() + timedelta(minutes=minutes)).strftime(ISO_FORMAT)
    record_hold_until(db, provider, reset, credential_slot=slot, manual=manual)
    return reset


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='The Daily Tech Show')
    db.upsert_episode(SLUG, EP, title='One episode', status='pending',
                      original_url='https://example.com/e.mp3')
    yield
    clear_all_holds(db)
    db.delete_podcast(SLUG)


def _explain(**kwargs):
    return admission_explanation(db, SLUG, EP, snapshot=SNAPSHOT, gates=GATES,
                                 paused=False, **kwargs)


class TestUnblocked:
    def test_no_hold_reports_not_blocked(self, feed):
        assert _explain() == {'blocked': False, 'phase': None, 'slot': None,
                              'reason': None, 'resumesAt': None}

    def test_paused_processing_is_reported_without_a_phase(self, feed):
        result = admission_explanation(db, SLUG, EP, snapshot=SNAPSHOT,
                                       gates=GATES, paused=True)
        assert result == {'blocked': True, 'phase': None, 'slot': None,
                          'reason': 'processing_paused', 'resumesAt': None}


class TestHeldAccounts:
    def test_names_the_blocked_phase_slot_and_reset(self, feed):
        reset = _hold('provider-b')
        result = _explain()
        assert result['blocked'] is True
        assert result['phase'] == 'verification'
        assert result['slot'] == 'primary'
        assert result['reason'] == 'provider_rate_limit'
        assert result['resumesAt'] == reset

    def test_manual_cap_is_distinguishable_from_a_provider_429(self, feed):
        _hold('provider-a', manual=True)
        assert _explain()['reason'] == 'manual_rate_limit'

    def test_earliest_pipeline_phase_wins(self, feed):
        _hold('provider-a')
        _hold('provider-c')
        assert _explain()['phase'] == 'detection'

    def test_a_hold_on_an_unused_account_does_not_block(self, feed):
        _hold('provider-z')
        assert _explain()['blocked'] is False


class TestSkippedPhasesNeverAppear:
    def test_skipped_verification_phase_is_not_the_blocker(self, feed):
        """F04's rule, read back through the explanation: a feed that skips
        its second pass is not blocked by the verification account."""
        db.update_podcast(SLUG, skip_second_pass=1)
        _hold('provider-b')
        assert _explain()['blocked'] is False

    def test_chapters_off_removes_that_phase(self, feed):
        _hold('provider-c')
        assert admission_explanation(
            db, SLUG, EP, snapshot=SNAPSHOT,
            gates={'review': True, 'chapters_enabled': 'false'},
            paused=False)['blocked'] is False


class TestAccountChangeIsExplained:
    def test_stale_route_reports_provider_account_changed(self, feed):
        snapshot = {'detection': _route(
            'anthropic', account_id=account_identity('anthropic', 'https://gone'))}
        result = admission_explanation(db, SLUG, EP, snapshot=snapshot,
                                       gates=GATES, paused=False)
        assert result['reason'] == 'provider_account_changed'
        assert result['phase'] == 'detection'
        assert result['resumesAt'] is None


class TestHoldCacheIsShared:
    def test_cache_is_reused_across_rows(self, feed):
        _hold('provider-a')
        cache = {}
        first = _explain(hold_cache=cache)
        second = _explain(hold_cache=cache)
        assert first == second
        assert ('provider-a', 'primary') in cache
