"""Tests for probing the secondary provider endpoint's response_format support."""
import unittest
from unittest.mock import patch, MagicMock

import llm_client


def _settings(enabled, provider):
    values = {'secondary_provider_enabled': enabled, 'secondary_provider': provider}
    return lambda k: values.get(k)


class TestProbeSecondaryProviderFormats(unittest.TestCase):

    def test_probes_enabled_openai_compatible_secondary(self):
        client = MagicMock()
        with patch('llm_client._get_cached_setting', side_effect=_settings('true', 'openai-compatible')), \
                patch('llm_client.get_client_for_provider', return_value=client) as get_client:
            llm_client.probe_secondary_provider_formats()
        get_client.assert_called_once_with('openai-compatible', credential_slot='secondary')
        client.verify_connection.assert_called_once_with(timeout=llm_client.HTTP_TIMEOUT_API)

    def test_skips_disabled_or_anthropic_secondary(self):
        for enabled, provider in (('false', 'openai-compatible'), ('true', 'anthropic')):
            with self.subTest(enabled=enabled, provider=provider), \
                    patch('llm_client._get_cached_setting', side_effect=_settings(enabled, provider)), \
                    patch('llm_client.get_client_for_provider') as get_client:
                llm_client.probe_secondary_provider_formats()
                get_client.assert_not_called()

    def test_swallows_client_errors(self):
        with patch('llm_client._get_cached_setting', side_effect=_settings('true', 'openai-compatible')), \
                patch('llm_client.get_client_for_provider', side_effect=Exception('boom')):
            llm_client.probe_secondary_provider_formats()

    def test_verify_llm_connection_probes_secondary(self):
        with patch('llm_client._verify_primary_connection', return_value=True), \
                patch('llm_client.probe_secondary_provider_formats') as probe:
            self.assertTrue(llm_client.verify_llm_connection())
        probe.assert_called_once()


class TestAfterCommitDedupe(unittest.TestCase):

    def test_same_hook_queued_twice_runs_once(self):
        from api.settings import common
        calls = []
        hook = lambda: calls.append(1)
        with common.post_commit_scope() as buckets:
            common.after_commit(hook)
            common.after_commit(hook)
            self.assertEqual(buckets['side_effects'], [hook])


if __name__ == '__main__':
    unittest.main()
