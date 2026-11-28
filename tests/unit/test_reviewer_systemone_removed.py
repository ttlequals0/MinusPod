"""System One and TypeSafe cannot be the reviewer: validation, run-time skip, migration, import/export."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('reviewer_systemone_removed_')

import config_transfer  # noqa: E402
import run_context  # noqa: E402
from api.settings import systemone_phases  # noqa: E402
from config import PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_TYPESAFE  # noqa: E402
from llm_capabilities import SYSTEMONE_REVIEWER_ERROR, systemone_supported_phases  # noqa: E402
from llm_route import review_route_is_systemone  # noqa: E402
from main_app.pipeline import review  # noqa: E402
from systemone.tuning import LEGACY_REVIEW_FIELDS, profile_from_snapshot, stored_legacy_review_values  # noqa: E402
from tools import reviewer_calibration  # noqa: E402

MESSAGE = 'System One cannot be used for the reviewer. Choose an LLM provider.'
PROFILE_KEY = 'systemone_tunables_primary_typesafe'
LEGACY_PROFILE = {
    'detectionEnter': 0.9, 'refineBoundaries': True, 'reviewEvidenceEnter': 0.3,
    'reviewChoiceEnter': None, 'reviewProgrammeVeto': 0.5, 'reviewBoundaryCapSeconds': 20.0,
    'reviewContextSeconds': 10.0,
}


def _set(db, **values):
    for key, value in values.items():
        db.set_setting(key, value, is_default=False)


def _issue(db, data):
    return systemone_phases._validate_systemone_phase_enables(db, data)


def test_message_matches_the_contract():
    assert SYSTEMONE_REVIEWER_ERROR == MESSAGE


def test_system_one_no_longer_supports_the_review_phase_but_keeps_the_others():
    for provider in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
        assert systemone_supported_phases(provider, 'any') == {'detection', 'verification'}
    assert systemone_supported_phases('openai-compatible', 'jev-latest') == {'detection', 'verification'}


class TestApiValidation:
    def test_explicit_slot_on_system_one_primary_is_rejected(self, temp_db):
        _set(temp_db, llm_provider='typesafe')
        assert _issue(temp_db, {'reviewProvider': 'primary'}) == (MESSAGE, 400)

    def test_explicit_secondary_slot_on_system_one_is_rejected(self, temp_db):
        _set(temp_db, secondary_provider_enabled='true', secondary_provider='systemone-compatible')
        assert _issue(temp_db, {'reviewProvider': 'secondary'}) == (MESSAGE, 400)

    def test_provider_type_named_for_the_review_stage_is_rejected(self, temp_db):
        for name in ('typesafe', 'systemone-compatible'):
            assert _issue(temp_db, {'reviewProvider': name}) == (MESSAGE, 400)

    def test_system_one_model_on_a_valid_provider_is_rejected(self, temp_db):
        _set(temp_db, llm_provider='openai-compatible')
        for model in ('jev-latest', 'jev-preview', 'typesafe/jev'):
            assert _issue(temp_db, {'reviewModel': model}) == (MESSAGE, 400)

    def test_failover_review_model_on_system_one_is_rejected(self, temp_db):
        assert _issue(temp_db, {'failoverLlmReviewModel': 'jev-latest'}) == (MESSAGE, 400)

    def test_switching_the_provider_under_an_explicit_review_slot_is_rejected(self, temp_db):
        _set(temp_db, review_provider='primary')
        assert _issue(temp_db, {'llmProvider': 'typesafe'}) == (MESSAGE, 400)

    def test_inheriting_review_stage_is_accepted_even_when_detection_is_system_one(self, temp_db):
        _set(temp_db, llm_provider='typesafe')
        assert _issue(temp_db, {'reviewProvider': 'same_as_pass'}) is None

    def test_llm_reviewer_settings_are_accepted(self, temp_db):
        _set(temp_db, llm_provider='anthropic')
        assert _issue(temp_db, {'reviewProvider': 'primary', 'reviewModel': 'claude-sonnet-5-5'}) is None

    def test_unrelated_save_is_not_blocked_by_a_stale_stored_value(self, temp_db):
        _set(temp_db, llm_provider='typesafe', review_provider='primary')
        assert _issue(temp_db, {'holdForReview': True}) is None


class TestRunTimeRule:
    class FakeDb:
        def __init__(self, **settings):
            self.settings = settings

        def get_setting(self, key):
            return self.settings.get(key)

    def test_inherited_review_on_typesafe_detection_resolves_to_system_one(self):
        db = self.FakeDb(llm_provider='typesafe', claude_model='jev-latest')
        assert review_route_is_systemone(db, 'detection') is True

    def test_llm_detection_is_not_blocked(self):
        db = self.FakeDb(llm_provider='anthropic', claude_model='claude-sonnet-5-5')
        assert review_route_is_systemone(db, 'detection') is False

    def test_pass_two_follows_the_verification_slot(self):
        db = self.FakeDb(llm_provider='typesafe', claude_model='jev-latest',
                         secondary_provider_enabled='true', secondary_provider='anthropic',
                         verification_provider='secondary', verification_model='claude-sonnet-5-5')
        assert review_route_is_systemone(db, 'detection') is True
        assert review_route_is_systemone(db, 'verification') is False

    def test_reviewer_skips_with_one_warning_per_episode(self, caplog):
        db = self.FakeDb(enable_ad_review='true', llm_provider='typesafe', claude_model='jev-latest')
        ctx = run_context.begin('feed', 'ep1')
        try:
            with caplog.at_level('WARNING'):
                assert review._reviewer_will_run(db, 1, 'feed', 'ep1') is False
                assert review._reviewer_will_run(db, 2, 'feed', 'ep1') is False
        finally:
            run_context.end(ctx)
        warnings = [r.message for r in caplog.records if 'Reviewer skipped' in r.message]
        assert len(warnings) == 1
        assert ('Reviewer skipped: the review provider is System One, which cannot review. '
                'Choose an LLM provider under Settings > Ad reviewer.') in warnings[0]

    def test_disabled_reviewer_stays_silent(self, caplog):
        db = self.FakeDb(enable_ad_review='false', llm_provider='typesafe', claude_model='jev-latest')
        with caplog.at_level('WARNING'):
            assert review._reviewer_will_run(db, 1, 'feed', 'ep1') is False
        assert not [r for r in caplog.records if 'Reviewer skipped' in r.message]

    def test_run_ad_reviewer_returns_inputs_untouched_and_builds_no_reviewer(self, monkeypatch):
        db = self.FakeDb(enable_ad_review='true', llm_provider='typesafe', claude_model='jev-latest')
        monkeypatch.setattr(review, 'db', db)
        monkeypatch.setattr(review, '_build_reviewer', lambda *a, **k: pytest.fail('reviewer built'))
        ads = [{'start': 1.0, 'end': 20.0, 'confidence': 0.9}]
        all_ads = list(ads)
        out = review._run_ad_reviewer(
            'feed', 'ep1', 'pid', ads, all_ads, [], 'Show', 'Ep', '', '', 0.8, 1,
            'jev-latest', pass_provider='typesafe')
        assert out == (ads, all_ads)

    def test_calibration_refuses_a_system_one_route(self, temp_db):
        _set(temp_db, llm_provider='typesafe', claude_model='jev-latest')
        with pytest.raises(ValueError, match='System One cannot be used for the reviewer'):
            reviewer_calibration.resolve_calibration_route()
        assert reviewer_calibration.calibration_route_revision()[0] == reviewer_calibration.UNRESOLVED_REVISION


class TestSettingsPayload:
    def test_reviewer_needs_llm_follows_enable_and_route(self, temp_db):
        from api.settings.core import _build_settings_payload
        import api.settings.core as core

        original = core.get_database
        core.get_database = lambda: temp_db
        try:
            _set(temp_db, llm_provider='typesafe', claude_model='jev-latest', enable_ad_review='true')
            assert _build_settings_payload()['reviewerNeedsLlm'] is True
            _set(temp_db, enable_ad_review='false')
            assert _build_settings_payload()['reviewerNeedsLlm'] is False
            _set(temp_db, enable_ad_review='true', llm_provider='anthropic', claude_model='claude-sonnet-5-5')
            assert _build_settings_payload()['reviewerNeedsLlm'] is False
        finally:
            core.get_database = original


class TestMigration:
    GATE = 'reviewer_systemone_reset_v1'

    @pytest.fixture
    def old_db(self, tmp_path):
        from database import Database
        Database._instance = None
        db = Database(data_dir=str(tmp_path))
        _set(db, llm_provider='typesafe', secondary_provider_enabled='true',
             secondary_provider='anthropic', review_provider='primary',
             review_model='jev-latest', failover_llm_review_model='typesafe/jev',
             claude_model='jev-latest', verification_provider='secondary')
        db.set_setting(PROFILE_KEY, json.dumps(LEGACY_PROFILE), is_default=False)
        conn = db.get_connection()
        conn.execute('DELETE FROM schema_migrations WHERE name = ?', (self.GATE,))
        conn.commit()
        yield tmp_path
        Database._instance = None

    @staticmethod
    def _reopen(path):
        from database import Database
        Database._instance = None
        return Database(data_dir=str(path))

    @staticmethod
    def _rows(db):
        return {row['key']: (row['value'], row['is_default'])
                for row in db.get_connection().execute('SELECT key, value, is_default FROM settings')}

    def test_resets_review_settings_that_name_system_one_and_nothing_else(self, old_db, caplog):
        db = self._reopen(old_db)  # the first open migrates; rebuild the old state
        _set(db, review_provider='primary', review_model='jev-latest',
             failover_llm_review_model='typesafe/jev')
        conn = db.get_connection()
        conn.execute('DELETE FROM schema_migrations WHERE name = ?', (self.GATE,))
        conn.commit()
        before = self._rows(db)
        with caplog.at_level('INFO'):
            db = self._reopen(old_db)
        after = self._rows(db)
        assert after['review_provider'] == ('same_as_pass', 1)
        assert after['review_model'] == ('same_as_pass', 1)
        assert after['failover_llm_review_model'] == ('', 1)
        changed = {key for key in before if before[key] != after.get(key)}
        assert {'review_provider', 'review_model', 'failover_llm_review_model'} <= changed
        assert changed - {'review_provider', 'review_model', 'failover_llm_review_model'} <= {'updated_at'}
        assert set(before) <= set(after)
        assert json.loads(after[PROFILE_KEY][0]) == LEGACY_PROFILE
        assert [r for r in caplog.records if 'reset review_provider' in r.message and r.levelname == 'INFO']

    def test_secondary_on_an_llm_provider_is_left_alone(self, old_db):
        db = self._reopen(old_db)
        _set(db, review_provider='secondary', review_model='claude-sonnet-5-5',
             failover_llm_review_model='')
        conn = db.get_connection()
        conn.execute('DELETE FROM schema_migrations WHERE name = ?', (self.GATE,))
        conn.commit()
        after = self._rows(self._reopen(old_db))
        assert after['review_provider'] == ('secondary', 0)
        assert after['review_model'] == ('claude-sonnet-5-5', 0)

    def test_rerun_is_a_no_op(self, old_db):
        db = self._reopen(old_db)
        _set(db, review_provider='primary')
        first = self._rows(db)
        second = self._rows(self._reopen(old_db))
        assert second['review_provider'] == first['review_provider'] == ('primary', 0)
        gate = db.get_connection().execute(
            'SELECT 1 FROM schema_migrations WHERE name = ?', (self.GATE,)).fetchone()
        assert gate is not None


class TestStoredLegacyTuning:
    def test_stored_review_fields_are_ignored_on_read_and_kept_in_the_row(self, temp_db):
        temp_db.set_setting(PROFILE_KEY, json.dumps(LEGACY_PROFILE), is_default=False)
        snapshot = temp_db.get_all_settings()
        profile = profile_from_snapshot(snapshot, 'primary', 'typesafe')
        assert profile['detectionEnter'] == 0.9
        assert not LEGACY_REVIEW_FIELDS & set(profile)
        assert stored_legacy_review_values(snapshot, 'primary', 'typesafe') == {
            key: value for key, value in LEGACY_PROFILE.items() if key in LEGACY_REVIEW_FIELDS}

    def test_saving_a_profile_keeps_the_stored_review_fields(self, temp_db):
        temp_db.set_setting(PROFILE_KEY, json.dumps(LEGACY_PROFILE), is_default=False)
        values, issue = systemone_phases._systemone_tunable_values(
            temp_db, {'systemOneTunables': {'primary': {'typesafe': {'categoryContext': 3}}}})
        assert issue is None
        saved = values[PROFILE_KEY][0]
        assert saved['categoryContext'] == 3
        assert saved['reviewEvidenceEnter'] == 0.3

    def test_patch_carrying_review_fields_is_accepted_and_ignored(self, temp_db):
        values, issue = systemone_phases._systemone_tunable_values(
            temp_db, {'systemOneTunables': {'primary': {'typesafe': {'reviewProgrammeVeto': 0.2}}}})
        assert issue is None
        assert 'reviewProgrammeVeto' not in values[PROFILE_KEY][0]


class TestConfigTransfer:
    def test_export_omits_reviewer_tuning(self, temp_db):
        temp_db.set_setting(PROFILE_KEY, json.dumps(LEGACY_PROFILE), is_default=False)
        document = config_transfer.export_config(temp_db, '2.103.1')
        exported = document['settings'][PROFILE_KEY]
        assert exported['detectionEnter'] == 0.9
        assert not LEGACY_REVIEW_FIELDS & set(exported)

    def test_old_export_imports_and_lists_the_ignored_keys(self, temp_db):
        document = config_transfer.export_config(temp_db, '2.103.0')
        document['settings'][PROFILE_KEY] = dict(LEGACY_PROFILE)
        preview = config_transfer.build_preview(temp_db, document, 'global', [])
        ignored = set(preview['skippedUnknownSettings'])
        assert {f'{PROFILE_KEY}.{field}' for field in LEGACY_REVIEW_FIELDS} <= ignored
        config_transfer.apply_config(temp_db, document, 'global', [], preview['previewToken'])
        stored = profile_from_snapshot(temp_db.get_all_settings(), 'primary', 'typesafe')
        assert stored['detectionEnter'] == 0.9
        assert not LEGACY_REVIEW_FIELDS & set(stored)


class TestHttpAndImport:
    @pytest.fixture
    def client(self):
        from main_app import app
        app.config['TESTING'] = True
        with app.test_client() as c:
            with c.session_transaction() as sess:
                sess['authenticated'] = True
            c.get('/api/v1/auth/status')
            yield c

    @staticmethod
    def _put(client, payload):
        headers = {cookie.key: cookie.value for cookie in client._cookies.values()}
        return client.put('/api/v1/settings/ad-detection', data=json.dumps(payload),
                          content_type='application/json',
                          headers={'X-CSRF-Token': headers.get('minuspod_csrf', '')})

    def test_put_answers_400_with_the_message(self, client):
        for payload in ({'reviewProvider': 'typesafe'}, {'reviewModel': 'jev-latest'},
                        {'failoverLlmReviewModel': 'typesafe/jev'}):
            response = self._put(client, payload)
            assert response.status_code == 400
            assert MESSAGE in json.dumps(response.get_json())

    def test_put_accepts_inheriting_review(self, client):
        assert self._put(client, {'reviewProvider': 'same_as_pass'}).status_code == 200

    def test_import_of_a_system_one_review_route_is_rejected(self, temp_db):
        document = config_transfer.export_config(temp_db, '2.103.1')
        document['settings'].update({'llm_provider': 'typesafe', 'review_provider': 'primary'})
        with pytest.raises(config_transfer.ConfigTransferError, match='System One cannot be used'):
            config_transfer.build_preview(temp_db, document, 'global', [])
