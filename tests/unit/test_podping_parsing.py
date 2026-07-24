"""Tests for podping parsing and matching."""
import json
from podping_listener import (
    normalize_feed_url,
    extract_podping_events,
    match_iris,
    PODPING_NODES,
    ACTIONABLE_REASONS,
    COOLDOWN_SECONDS,
    MAX_CATCHUP_BLOCKS,
)


class TestConstants:
    """Test module constants."""

    def test_podping_nodes_defined(self):
        """PODPING_NODES should be a list of strings."""
        assert isinstance(PODPING_NODES, list)
        assert len(PODPING_NODES) >= 3
        assert all(isinstance(node, str) for node in PODPING_NODES)
        assert {'https://api.hive.blog', 'https://api.openhive.network', 'https://hived.emre.sh'}.issubset(set(PODPING_NODES))

    def test_actionable_reasons(self):
        """ACTIONABLE_REASONS should contain update and live."""
        assert ACTIONABLE_REASONS == {'update', 'live'}

    def test_cooldown_seconds(self):
        """COOLDOWN_SECONDS should be 300."""
        assert COOLDOWN_SECONDS == 300

    def test_max_catchup_blocks(self):
        """MAX_CATCHUP_BLOCKS should be 100."""
        assert MAX_CATCHUP_BLOCKS == 100


class TestNormalizeFeedUrl:
    """Test normalize_feed_url function."""

    def test_lowercase_scheme_and_host(self):
        """Scheme and host should be lowercased."""
        url = 'HTTPS://Feeds.Transistor.FM/oxide-and-friends/'
        result = normalize_feed_url(url)
        assert result == 'https://feeds.transistor.fm/oxide-and-friends'

    def test_strip_trailing_slash(self):
        """Strip one trailing slash from path."""
        url = 'https://feeds.transistor.fm/oxide-and-friends/'
        result = normalize_feed_url(url)
        assert result == 'https://feeds.transistor.fm/oxide-and-friends'

    def test_no_strip_slash_if_root(self):
        """Do not strip slash if it is the root."""
        url = 'https://feeds.transistor.fm/'
        result = normalize_feed_url(url)
        assert result == 'https://feeds.transistor.fm/'

    def test_preserve_path_case(self):
        """Path case should be preserved."""
        url = 'https://example.com/OxideAndFriends/feed'
        result = normalize_feed_url(url)
        assert result == 'https://example.com/OxideAndFriends/feed'

    def test_preserve_query_string(self):
        """Query strings should be preserved."""
        url = 'https://example.com/feed?format=json&id=123'
        result = normalize_feed_url(url)
        assert 'format=json' in result
        assert 'id=123' in result

    def test_multiple_trailing_slashes(self):
        """Only strip one trailing slash."""
        url = 'https://example.com/feed//'
        result = normalize_feed_url(url)
        assert result == 'https://example.com/feed/'

    def test_no_trailing_slash(self):
        """URL without trailing slash should remain unchanged."""
        url = 'https://example.com/feed'
        result = normalize_feed_url(url)
        assert result == 'https://example.com/feed'

    def test_mixed_case_example(self):
        """Real-world mixed case example."""
        url = 'HTTPS://Feeds.Transistor.FM/oxide-and-friends/'
        result = normalize_feed_url(url)
        assert result == 'https://feeds.transistor.fm/oxide-and-friends'


class TestExtractPodpingEvents:
    """Test extract_podping_events function."""

    def _make_block(self, op_id, json_payload, account, op_type='custom_json'):
        """Helper to create a block dict."""
        json_str = json.dumps(json_payload) if json_payload is not None else ''
        return {
            'transactions': [
                {
                    'operations': [
                        [
                            op_type,
                            {
                                'id': op_id,
                                'json': json_str,
                                'required_posting_auths': [account],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }

    def test_id_filter_podping_accepted(self):
        """Operations with id 'podping' should be accepted."""
        block = self._make_block('podping', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://example.com']

    def test_id_filter_pp_podcast_update_accepted(self):
        """Operations with id 'pp_podcast_update' should be accepted."""
        block = self._make_block('pp_podcast_update', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1

    def test_id_filter_pp_prefix_variant_accepted(self):
        """Any id starting with 'pp_' should be accepted, not just 'pp_podcast_update'."""
        block = self._make_block('pp_podcast_live', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1

    def test_id_filter_follow_rejected(self):
        """Operations with id 'follow' should be rejected."""
        block = self._make_block('follow', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_id_filter_custom_operation_rejected(self):
        """Operations with arbitrary id should be rejected."""
        block = self._make_block('some_other_id', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_allowed_account_in_set(self):
        """Operation from account in allowed_accounts should be accepted."""
        block = self._make_block('podping', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account1', 'account2'})
        assert len(result) == 1

    def test_allowed_account_not_in_set(self):
        """Operation from account not in allowed_accounts should be rejected."""
        block = self._make_block('podping', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, {'account2', 'account3'})
        assert len(result) == 0

    def test_allowed_account_at_second_index_accepted(self):
        """Allow-list check must intersect ALL required_posting_auths, not just index 0."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': ['other_account', 'account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1

    def test_empty_allowed_accounts_rejects_all(self):
        """Empty allowed_accounts should reject all operations (fail closed)."""
        block = self._make_block('podping', {'version': '1.1', 'iris': ['http://example.com']}, 'account1')
        result = extract_podping_events(block, set())
        assert len(result) == 0

    def test_version_1_1_payload(self):
        """Version 1.1 payload with iris and reason should be extracted."""
        payload = {'version': '1.1', 'iris': ['http://ex1.com', 'http://ex2.com'], 'reason': 'update'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://ex1.com', 'http://ex2.com']
        assert result[0]['reason'] == 'update'

    def test_version_1_0_payload(self):
        """Version 1.0 payload should be extracted."""
        payload = {'version': '1.0', 'iris': ['http://example.com'], 'reason': 'liveEnd'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://example.com']
        assert result[0]['reason'] == 'liveEnd'

    def test_version_0_x_urls_array(self):
        """Version 0.x with urls array should be extracted."""
        payload = {'urls': ['http://ex1.com', 'http://ex2.com']}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://ex1.com', 'http://ex2.com']
        assert result[0]['reason'] is None

    def test_version_0_x_url_string(self):
        """Version 0.x with single url string should be extracted."""
        payload = {'url': 'http://example.com'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://example.com']
        assert result[0]['reason'] is None

    def test_malformed_json_ignored(self):
        """Malformed JSON should be silently ignored."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': 'not valid json {',
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_non_string_version_ignored(self):
        """A non-string version must not raise and must fall through to the urls/url path."""
        payload = {'version': 11, 'iris': ['http://example.com']}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_non_dict_op_data_ignored(self):
        """op[1] that is not a dict should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': [
                        ['custom_json', 'not-a-dict']
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_non_string_json_field_ignored(self):
        """A json field that is not a string (e.g. dict) should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': {'version': '1.1', 'iris': ['http://example.com']},
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_iris_as_non_list_ignored(self):
        """iris field that is not a list should be ignored."""
        payload = {'version': '1.1', 'iris': 'http://example.com'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_urls_as_non_list_ignored(self):
        """urls field that is not a list should be ignored."""
        payload = {'urls': 'http://example.com'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_empty_urls_ignored(self):
        """Empty urls array should be ignored."""
        payload = {'urls': []}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_reason_passthrough_none(self):
        """None reason should be passed through."""
        payload = {'version': '1.1', 'iris': ['http://example.com']}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['reason'] is None

    def test_reason_passthrough_liveEnd(self):
        """'liveEnd' reason should be passed through (filtering happens in listener tick)."""
        payload = {'version': '1.1', 'iris': ['http://example.com'], 'reason': 'liveEnd'}
        block = self._make_block('podping', payload, 'account1')
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['reason'] == 'liveEnd'

    def test_multiple_operations_per_block(self):
        """Block with multiple operations should process all."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://ex1.com']}),
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ],
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://ex2.com']}),
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 2

    def test_empty_block(self):
        """Empty block should return empty list."""
        block = {'transactions': []}
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_missing_required_posting_auths(self):
        """Operation without required_posting_auths should be rejected."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': [],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_empty_json_string_ignored(self):
        """Operation with empty json string should be ignored."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': '',
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_non_dict_json_ignored(self):
        """JSON that parses to non-dict should be ignored."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps(['http://example.com']),
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_non_list_operation_ignored(self):
        """Operation that is not a list should be ignored."""
        block = {
            'transactions': [
                {
                    'operations': [
                        {'type': 'custom_json', 'data': {}}
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_short_operation_list_ignored(self):
        """Operation list with < 2 elements should be ignored."""
        block = {
            'transactions': [
                {
                    'operations': [['custom_json']]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_required_posting_auths_as_bool_skipped(self):
        """required_posting_auths as bool (True) should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': True,
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_required_posting_auths_as_int_skipped(self):
        """required_posting_auths as int (5) should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': 5,
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_required_posting_auths_mixed_types_filtered(self):
        """required_posting_auths with mixed types: string accounts kept, dict ignored, match if account allowed."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': ['account1', {'x': 1}],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://example.com']

    def test_required_posting_auths_all_non_strings_rejected(self):
        """required_posting_auths with only non-string elements should be rejected."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example.com']}),
                                'required_posting_auths': [{'x': 1}, {'y': 2}],
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_block_with_transactions_none(self):
        """Block with transactions=None should return empty list without raising."""
        block = {'transactions': None}
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_block_missing_transactions(self):
        """Block missing transactions key should return empty list."""
        block = {}
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_tx_as_string_skipped(self):
        """Transaction that is a string should be skipped without raising."""
        block = {
            'transactions': [
                'not-a-dict'
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_operations_as_none_skipped(self):
        """Transaction with operations=None should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': None
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_operations_as_string_skipped(self):
        """Transaction with operations as string should be skipped without raising."""
        block = {
            'transactions': [
                {
                    'operations': 'not-a-list'
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 0

    def test_mixed_valid_and_invalid_in_same_block(self):
        """Block with mix of valid and invalid ops should extract only valid ones."""
        block = {
            'transactions': [
                {
                    'operations': [
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://valid.com']}),
                                'required_posting_auths': ['account1'],
                                'required_auths': []
                            }
                        ],
                        'not-a-list',
                        [
                            'custom_json',
                            {
                                'id': 'podping',
                                'json': json.dumps({'version': '1.1', 'iris': ['http://example2.com']}),
                                'required_posting_auths': True,
                                'required_auths': []
                            }
                        ]
                    ]
                }
            ]
        }
        result = extract_podping_events(block, {'account1'})
        assert len(result) == 1
        assert result[0]['iris'] == ['http://valid.com']


class TestMatchIris:
    """Test match_iris function."""

    def test_single_iris_hit(self):
        """Single IRI that matches should return slug."""
        feed_map = {'https://example.com/feed': 'slug1'}
        iris = ['https://example.com/feed']
        result = match_iris(iris, feed_map)
        assert result == ['slug1']

    def test_single_iris_miss(self):
        """Single IRI that does not match should return empty list."""
        feed_map = {'https://example.com/feed': 'slug1'}
        iris = ['https://different.com/feed']
        result = match_iris(iris, feed_map)
        assert result == []

    def test_multiple_iris_mixed_hits(self):
        """Multiple IRIs with some hits should return matching slugs."""
        feed_map = {
            'https://example.com/feed1': 'slug1',
            'https://example.com/feed2': 'slug2'
        }
        iris = ['https://example.com/feed1', 'https://other.com/feed', 'https://example.com/feed2']
        result = match_iris(iris, feed_map)
        assert set(result) == {'slug1', 'slug2'}

    def test_duplicate_iris_returns_one_slug(self):
        """Duplicate IRI pointing to same slug should return deduped."""
        feed_map = {'https://example.com/feed': 'slug1'}
        iris = ['https://example.com/feed', 'https://example.com/feed']
        result = match_iris(iris, feed_map)
        assert result == ['slug1']

    def test_multiple_iris_same_slug(self):
        """Multiple IRIs pointing to same slug should be deduped."""
        feed_map = {
            'https://example.com/feed1': 'slug1',
            'https://example.com/feed2': 'slug1'
        }
        iris = ['https://example.com/feed1', 'https://example.com/feed2']
        result = match_iris(iris, feed_map)
        assert result == ['slug1']

    def test_empty_iris(self):
        """Empty iris list should return empty."""
        feed_map = {'https://example.com/feed': 'slug1'}
        iris = []
        result = match_iris(iris, feed_map)
        assert result == []

    def test_empty_feed_map(self):
        """Empty feed map should return empty."""
        feed_map = {}
        iris = ['https://example.com/feed']
        result = match_iris(iris, feed_map)
        assert result == []

    def test_normalize_iri_before_match(self):
        """IRI should be normalized before matching feed_map."""
        feed_map = {'https://example.com/feed': 'slug1'}
        iris = ['HTTPS://EXAMPLE.COM/feed/']
        result = match_iris(iris, feed_map)
        assert result == ['slug1']

    def test_sorted_output(self):
        """Output should be sorted."""
        feed_map = {
            'https://example.com/feed1': 'zebra',
            'https://example.com/feed2': 'apple',
            'https://example.com/feed3': 'banana'
        }
        iris = ['https://example.com/feed1', 'https://example.com/feed2', 'https://example.com/feed3']
        result = match_iris(iris, feed_map)
        assert result == ['apple', 'banana', 'zebra']

    def test_case_insensitive_matching_via_normalization(self):
        """Feed map key and IRI should both normalize to match."""
        feed_map = {'https://example.com/Feed': 'slug1'}
        iris = ['https://EXAMPLE.COM/Feed/']
        result = match_iris(iris, feed_map)
        assert result == ['slug1']
