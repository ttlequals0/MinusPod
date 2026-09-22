"""Redaction helper for GET /system/config-export (issue #781)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.config_export import redact_config


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


def test_handles_nested_lists():
    doc = {
        'feeds': [
            {'slug': 'a', 'feedUrl': 'https://host/a?key=1&auth=y'},
            {'slug': 'b', 'feedUrl': 'https://host/b'},
        ]
    }
    result = redact_config(doc)
    assert result == {
        'feeds': [
            {'slug': 'a', 'feedUrl': 'https://host/a'},
            {'slug': 'b', 'feedUrl': 'https://host/b'},
        ]
    }
