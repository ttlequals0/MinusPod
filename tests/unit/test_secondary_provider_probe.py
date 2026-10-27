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

    def test_skips_disabled_or_anthropic_secondary(self):
        for enabled, provider in (('false', 'openai-compatible'), ('true', 'anthropic')):
            with self.subTest(enabled=enabled, provider=provider), \
                    patch('llm_client._get_cached_setting', side_effect=_settings(enabled, provider)), \
                    patch('llm_client.get_client_for_provider') as get_client:
                llm_client._probe_secondary_provider_formats()
                get_client.assert_not_called()

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



class TestReprobeAllSlots(unittest.TestCase):

    def test_verifies_primary_and_probes_secondary(self):
        client = MagicMock()
        with patch('api.settings.provider_phases.get_llm_client', return_value=client), \
                patch('api.settings.provider_phases.threading.Thread', SyncThread), \
                patch('api.settings.provider_phases.probe_secondary_provider_formats') as probe:
            provider_phases._reprobe_all_slots()
        client.verify_connection.assert_called_once_with(timeout=provider_phases.HTTP_TIMEOUT_API)
        probe.assert_called_once()

    def test_clearing_probes_queues_a_reprobe(self):
        with common.post_commit_scope() as buckets:
            provider_phases._clear_format_probes(MagicMock())
        self.assertIn(provider_phases._reprobe_all_slots, buckets['side_effects'])


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

    def test_key_save_with_unchanged_type_keeps_probe_answers(self):
        db = _FakeDb(secondary_provider='openai-compatible')
        hooks = self._apply(db, {'secondaryProviderApiKey': 'k', 'secondaryProvider': 'openai-compatible'})
        self.assertIn(provider_phases._reprobe_all_slots, hooks)
        self.assertNotIn(provider_phases.reset_schema_probe_memo, hooks)

    def test_new_base_url_clears_probe_answers(self):
        db = _FakeDb(secondary_provider='openai-compatible')
        hooks = self._apply(db, {'secondaryProviderBaseUrl': 'http://b.example/v1'})
        self.assertIn(provider_phases.reset_schema_probe_memo, hooks)
        self.assertEqual(hooks.count(provider_phases._reprobe_all_slots), 1)


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
