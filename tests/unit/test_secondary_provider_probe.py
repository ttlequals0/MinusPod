"""Tests for probing the secondary provider endpoint's response_format support."""
import unittest
from unittest.mock import patch, MagicMock

import llm_client
from api.settings import common, provider_phases
from tests.unit.thread_fakes import SyncThread


def _settings(enabled, provider):
    values = {'secondary_provider_enabled': enabled, 'secondary_provider': provider}
    return lambda k: values.get(k)


class TestProbeSecondaryProviderFormats(unittest.TestCase):

    def test_probes_enabled_openai_compatible_secondary(self):
        client = MagicMock()
        with patch('llm_client._get_cached_setting', side_effect=_settings('true', 'openai-compatible')), \
                patch('llm_client.get_client_for_provider', return_value=client) as get_client:
            llm_client._probe_secondary_provider_formats()
        get_client.assert_called_once_with('openai-compatible', credential_slot='secondary')
        client.verify_connection.assert_called_once_with(timeout=llm_client.HTTP_TIMEOUT_API)

    def test_skips_providers_without_format_probes(self):
        for provider in ('anthropic', 'typesafe', 'openrouter'):
            with self.subTest(provider=provider), \
                    patch('llm_client._get_cached_setting', side_effect=_settings('true', provider)), \
                    patch('llm_client.get_client_for_provider') as get_client:
                llm_client._probe_secondary_provider_formats()
                get_client.assert_not_called()

    def test_disabled_secondary_starts_no_thread(self):
        with patch('llm_client._get_cached_setting', side_effect=_settings('false', 'openai-compatible')), \
                patch('llm_client.threading.Thread') as thread:
            llm_client.probe_secondary_provider_formats()
        thread.assert_not_called()

    def test_swallows_client_errors(self):
        with patch('llm_client._get_cached_setting', side_effect=_settings('true', 'openai-compatible')), \
                patch('llm_client.get_client_for_provider', side_effect=Exception('boom')):
            llm_client._probe_secondary_provider_formats()

    def test_public_entry_probes_on_a_thread(self):
        client = MagicMock()
        with patch('llm_client.threading.Thread', SyncThread), \
                patch('llm_client._get_cached_setting', side_effect=_settings('true', 'openai-compatible')), \
                patch('llm_client.get_client_for_provider', return_value=client):
            llm_client.probe_secondary_provider_formats()
        client.verify_connection.assert_called_once_with(timeout=llm_client.HTTP_TIMEOUT_API)

    def test_verify_llm_connection_probes_secondary(self):
        with patch('llm_client._verify_primary_connection', return_value=True), \
                patch('llm_client.probe_secondary_provider_formats') as probe:
            self.assertTrue(llm_client.verify_llm_connection())
        probe.assert_called_once()



class TestSlotClientEndpoint(unittest.TestCase):

    def test_secondary_and_failover_slots_use_their_own_base_url(self):
        settings = {'secondary_provider_base_url': 'http://b.example/v1',
                    'failover_llm_base_url': 'http://f.example/v1'}
        for slot, expected in (('secondary', 'http://b.example/v1'), ('failover', 'http://f.example/v1')):
            with self.subTest(slot=slot), \
                    patch('llm_client._get_cached_setting', side_effect=settings.get), \
                    patch('llm_client.get_effective_base_url', return_value='http://a.example/v1'), \
                    patch('llm_client.get_effective_secondary_provider_api_key', return_value='k'), \
                    patch('llm_client.get_effective_failover_llm_api_key', return_value='k'):
                client = llm_client._build_client('openai-compatible', credential_slot=slot)
                key = llm_client._resolve_cache_key('openai-compatible', credential_slot=slot)
            self.assertEqual(client.base_url, expected)
            self.assertEqual(key[1], expected)


class TestReprobeHooks(unittest.TestCase):

    def test_primary_reprobe_verifies_off_thread(self):
        client = MagicMock()
        with patch('api.settings.provider_phases.get_llm_client', return_value=client), \
                patch('api.settings.provider_phases.threading.Thread', SyncThread):
            provider_phases._reprobe_primary()
        client.verify_connection.assert_called_once_with(timeout=provider_phases.HTTP_TIMEOUT_API)

    def test_clearing_probes_queues_both_slots_once(self):
        with common.post_commit_scope() as buckets:
            provider_phases._clear_format_probes(MagicMock())
            provider_phases._clear_format_probes(MagicMock())
        hooks = buckets['side_effects']
        self.assertEqual(hooks.count(provider_phases._reprobe_primary), 1)
        self.assertEqual(hooks.count(provider_phases.probe_secondary_provider_formats), 1)


class _FakeDb:
    def __init__(self, **values):
        self.values = values

    def get_setting(self, key):
        return self.values.get(key)

    def set_setting(self, key, value, is_default=False):
        self.values[key] = value

    def clear_setting(self, key):
        self.values.pop(key, None)


class TestSecondaryChangeDetection(unittest.TestCase):

    def _apply(self, db, data):
        with patch('api.settings.provider_phases.set_or_clear_secret'), \
                common.post_commit_scope() as buckets:
            provider_phases._apply_secondary_provider_fields(db, data)
        return buckets['side_effects']

    def test_key_save_with_unchanged_type_probes_only_the_secondary(self):
        db = _FakeDb(secondary_provider='openai-compatible', secondary_provider_enabled='true')
        hooks = self._apply(db, {'secondaryProviderApiKey': 'k', 'secondaryProvider': 'openai-compatible'})
        self.assertIn(provider_phases.probe_secondary_provider_formats, hooks)
        self.assertNotIn(provider_phases._reprobe_primary, hooks)

    def test_new_base_url_clears_probe_answers(self):
        db = _FakeDb(secondary_provider='openai-compatible')
        hooks = self._apply(db, {'secondaryProviderBaseUrl': 'http://b.example/v1'})
        self.assertIn(provider_phases.reset_schema_probe_memo, hooks)
        self.assertEqual(hooks.count(provider_phases._reprobe_primary), 1)
        self.assertEqual(hooks.count(provider_phases.probe_secondary_provider_formats), 1)

    def test_primary_key_save_with_unchanged_provider_keeps_probe_answers(self):
        db = _FakeDb(llm_provider='openrouter')
        with patch('api.settings.provider_phases.set_or_clear_secret'), \
                common.post_commit_scope() as buckets:
            provider_phases._apply_provider_fields(db, {'openrouterApiKey': 'k', 'llmProvider': 'openrouter'})
        self.assertNotIn(provider_phases.reset_schema_probe_memo, buckets['side_effects'])
        self.assertIn(provider_phases._refresh_after_provider_change, buckets['side_effects'])

    def test_primary_provider_switch_clears_probe_answers(self):
        db = _FakeDb(llm_provider='anthropic')
        with common.post_commit_scope() as buckets:
            provider_phases._apply_provider_fields(db, {'llmProvider': 'openai-compatible'})
        self.assertIn(provider_phases.reset_schema_probe_memo, buckets['side_effects'])
        self.assertIn(provider_phases._reprobe_primary, buckets['side_effects'])


class TestAfterCommitDedupe(unittest.TestCase):

    def test_same_hook_queued_twice_runs_once(self):
        calls = []
        hook = lambda: calls.append(1)
        with common.post_commit_scope() as buckets:
            common.after_commit(hook)
            common.after_commit(hook)
            self.assertEqual(buckets['side_effects'], [hook])


if __name__ == '__main__':
    unittest.main()
