"""Redaction helper for GET /system/config-export (issue #781)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.config_export import DomainIdentity, redact_config


def test_drops_known_secret_keys():
    doc = {
        'feedAuthKey': 'abc123',
        'feed_auth_key': 'abc123',
        'apiKey': 'sk-xyz',
        'secret': 'shh',
        'password': 'hunter2',
        'passphrase': 'correct horse',
        'token': 'tok_1',
        'key': 'raw-key',
        'claudeModel': 'claude-3',
    }
    result = redact_config(doc)
    assert result == {'claudeModel': 'claude-3'}


def test_drops_secret_shaped_keys_not_in_exact_name_list():
    doc = {
        'smtpPassword': 'hunter2',
        'openaiApiKey': 'sk-xyz',
        'slackToken': 'xoxb-1',
        'chapterKeywords': ['ad', 'sponsor'],
        'apiKeyConfigured': True,
    }
    result = redact_config(doc)
    assert result == {
        'chapterKeywords': ['ad', 'sponsor'],
        'apiKeyConfigured': True,
    }


def test_keeps_configured_booleans_even_with_matching_names():
    doc = {'apiKeyConfigured': True, 'secret': False}
    result = redact_config(doc)
    assert result == {'apiKeyConfigured': True, 'secret': False}


def test_strips_key_query_param_from_feed_url():
    doc = {'feedUrl': 'https://podsrv.example.com/my-show?key=abc123'}
    result = redact_config(doc)
    assert result == {'feedUrl': 'https://podsrv.example.com/my-show'}


def test_strips_userinfo_from_url():
    doc = {'sourceUrl': 'https://user:pass@example.com/feed.xml'}
    result = redact_config(doc)
    assert result == {'sourceUrl': 'https://example.com/feed.xml'}


def test_reduces_webhook_url_to_origin():
    doc = {
        'webhooks': [{
            'id': 'wh1',
            'url': 'https://api.pushover.net/1/messages.json?token=xyz',
            'events': ['episode.processed'],
            'secret': 'wh-secret',
        }]
    }
    result = redact_config(doc)
    assert result == {
        'webhooks': [{
            'id': 'wh1',
            'url': 'https://api.pushover.net',
            'events': ['episode.processed'],
        }]
    }


def test_leaves_models_prompts_and_thresholds_untouched():
    doc = {
        'systemPrompt': {'value': 'Detect ads.', 'isDefault': True},
        'minCutConfidence': {'value': 0.75, 'isDefault': False},
        'chaptersEnabled': {'value': True, 'isDefault': True},
    }
    assert redact_config(doc) == doc


def test_redacts_instance_host_to_placeholder_keeping_scheme_and_path():
    doc = {'feedUrl': 'https://feeds.example.com/example-podcast?key=abc'}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'feedUrl': 'https://<domain>/example-podcast'}


def test_redacts_instance_host_keeps_http_scheme():
    doc = {'feedUrl': 'http://feeds.example.com/example-podcast'}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'feedUrl': 'http://<domain>/example-podcast'}


def test_third_party_host_untouched_by_instance_redaction():
    doc = {'sourceFeedUrl': 'https://other-host.example.com/feed.xml'}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == doc


def test_default_instance_hosts_is_empty_and_leaves_urls_alone():
    doc = {'feedUrl': 'https://feeds.example.com/example-podcast?key=abc'}
    result = redact_config(doc)
    assert result == {'feedUrl': 'https://feeds.example.com/example-podcast'}


def test_settings_section_keeps_known_public_provider_hosts():
    doc = {'settings': {'llmApiUrl': 'https://api.openai.com/v1/chat/completions'}}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'settings': {'llmApiUrl': 'https://api.openai.com/v1/chat/completions'}}

    doc = {'settings': {'llmApiUrl': 'https://openrouter.ai/api/v1'}}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'settings': {'llmApiUrl': 'https://openrouter.ai/api/v1'}}


def test_settings_section_masks_lan_ip_as_private_host():
    doc = {'settings': {'ollamaBaseUrl': 'http://192.168.1.5:11434/v1'}}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'settings': {'ollamaBaseUrl': 'http://<private-host>/v1'}}


def test_settings_section_masks_unknown_public_host_as_private_host():
    doc = {'settings': {'llmApiUrl': 'https://llm.example.org/v1'}}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'settings': {'llmApiUrl': 'https://<private-host>/v1'}}


def test_settings_section_still_redacts_instance_host_to_domain():
    doc = {'settings': {'opmlModifiedUrl': 'https://feeds.example.com/opml/modified.opml?key=abc'}}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'settings': {'opmlModifiedUrl': 'https://<domain>/opml/modified.opml'}}


def test_feeds_section_third_party_host_unaffected_by_provider_allowlist():
    doc = {'feeds': [{'sourceFeedUrl': 'https://feeds.megaphone.fm/example-podcast.xml'}]}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'feeds': [{'sourceFeedUrl': 'https://feeds.megaphone.fm/example-podcast.xml'}]}


def test_private_host_masked_outside_settings_too():
    doc = {'feeds': [{'sourceFeedUrl': 'http://localhost:9000/feed.xml'}]}
    result = redact_config(doc, instance_hosts=frozenset({'feeds.example.com'}))
    assert result == {'feeds': [{'sourceFeedUrl': 'http://<private-host>/feed.xml'}]}


def test_masks_email_in_settings_value():
    doc = {'settings': {'smtpFrom': 'Alerts <alerts@example.com>'}}
    result = redact_config(doc)
    assert result == {'settings': {'smtpFrom': 'Alerts <<email>>'}}


def test_masks_email_in_nested_list():
    doc = {'feeds': [{'detectionNotes': 'contact ops@example.com for help'}]}
    result = redact_config(doc)
    assert result == {'feeds': [{'detectionNotes': 'contact <email> for help'}]}


def test_masks_domain_host_mention_outside_url():
    doc = {'feeds': [{'detectionNotes': 'Hosted by feeds.example.com'}]}
    identity = DomainIdentity(host='feeds.example.com', registrable_domain='example.com')
    result = redact_config(doc, domain_identity=identity)
    assert result == {'feeds': [{'detectionNotes': 'Hosted by <domain>'}]}


def test_masks_bare_registrable_domain():
    doc = {'feeds': [{'author': 'example.com'}]}
    identity = DomainIdentity(host='feeds.example.com', registrable_domain='example.com')
    result = redact_config(doc, domain_identity=identity)
    assert result == {'feeds': [{'author': '<domain>'}]}


def test_masks_bare_first_label_when_long_enough():
    doc = {'feeds': [{'author': 'example'}]}
    identity = DomainIdentity(host='feeds.example.com', registrable_domain='example.com')
    result = redact_config(doc, domain_identity=identity)
    assert result == {'feeds': [{'author': '<domain>'}]}


def test_short_first_label_not_masked():
    doc = {'feeds': [{'author': 'ab'}]}
    identity = DomainIdentity(host='ab.io', registrable_domain='ab.io')
    result = redact_config(doc, domain_identity=identity)
    assert result == {'feeds': [{'author': 'ab'}]}


def test_handles_nested_lists():
    doc = {
        'feeds': [
            {'slug': 'a', 'feedUrl': 'https://host.example.com/a?key=1&auth=y'},
            {'slug': 'b', 'feedUrl': 'https://host.example.com/b'},
        ]
    }
    result = redact_config(doc)
    assert result == {
        'feeds': [
            {'slug': 'a', 'feedUrl': 'https://host.example.com/a'},
            {'slug': 'b', 'feedUrl': 'https://host.example.com/b'},
        ]
    }
