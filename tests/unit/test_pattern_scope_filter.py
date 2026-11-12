"""Scope gate shared by the text and audio matchers."""
import ast
from types import SimpleNamespace

import pytest

import utils.pattern_scope as mod
from text_pattern_matcher import TextPatternMatcher, pattern_passes_scope, UNIVERSAL_TAG

def _filter_result(scope, pat_net, net, pat_pod, pod, source, stags, ptags):
    matcher = TextPatternMatcher.__new__(TextPatternMatcher)
    matcher._patterns = [SimpleNamespace(scope=scope, network_id=pat_net, podcast_id=pat_pod,
                                         source=source, sponsor_id=7)]
    matcher._sponsor_tags = {7: set(stags)}
    return bool(matcher._filter_patterns_by_scope(pod, net, ptags))


def _gate_result(scope, pat_net, net, pat_pod, pod, source, stags, ptags):
    return pattern_passes_scope(scope, pat_net, pat_pod, source, stags,
                                podcast_id=pod, network_id=net, podcast_tag_set=set(ptags))


U = UNIVERSAL_TAG
# (scope, pattern network, episode network, pattern podcast, episode podcast, source,
#  sponsor tags, podcast tags, expected)
FILTER_TABLE = [
    ('global', None, None, None, None, 'local', set(), set(), True),
    ('global', 'n1', 'n2', 'p1', 'p2', 'local', {'tech'}, {'news'}, True),
    ('network', 'n1', 'n1', None, None, 'local', set(), set(), True),
    ('network', 'n1', 'n2', None, None, 'local', set(), set(), False),
    ('network', 'n1', None, None, None, 'local', set(), set(), False),
    ('network', None, None, None, None, 'local', set(), set(), False),
    ('network', None, 'n1', None, None, 'local', set(), set(), False),
    ('podcast', None, None, 'p1', 'p1', 'local', set(), set(), True),
    ('podcast', None, None, 'p1', 'p2', 'local', set(), set(), False),
    ('podcast', None, None, 'p1', None, 'local', set(), set(), False),
    ('podcast', None, None, None, None, 'local', set(), set(), False),
    ('podcast', None, None, None, 'p1', 'local', set(), set(), False),
    ('bogus', 'n1', 'n1', 'p1', 'p1', 'local', set(), set(), False),
    ('global', None, None, None, None, 'local', {'tech'}, {'news'}, True),
    ('global', None, None, None, None, 'community', {'tech'}, {'news'}, False),
    ('global', None, None, None, None, 'community', {'tech'}, {'tech'}, True),
    ('global', None, None, None, None, 'community', {'tech', 'news'}, {'news', 'comedy'}, True),
    ('global', None, None, None, None, 'community', {'sports'}, {'news', 'comedy'}, False),
    ('global', None, None, None, None, 'community', {U}, {'news'}, True),
    ('global', None, None, None, None, 'community', {U, 'tech'}, {'news'}, True),
    ('global', None, None, None, None, 'community', set(), {'news'}, True),
    ('global', None, None, None, None, 'community', {'tech'}, set(), True),
    ('global', None, None, None, None, 'community', set(), set(), True),
    ('global', None, None, None, None, 'community', {'tech'}, {U}, False),
    ('network', 'n1', 'n1', None, None, 'community', {'tech'}, {'news'}, False),
    ('network', 'n1', 'n1', None, None, 'community', {'tech'}, {'tech'}, True),
    ('network', 'n1', 'n2', None, None, 'community', {U}, {'news'}, False),
    ('podcast', None, None, 'p1', 'p1', 'community', {'tech'}, {'news'}, False),
    ('podcast', None, None, 'p1', 'p1', 'community', {U}, {'news'}, True),
    ('podcast', None, None, 'p1', 'p2', 'community', {U}, set(), False),
]


@pytest.mark.parametrize('case', FILTER_TABLE)
def test_filter_table(case):
    assert _filter_result(*case[:-1]) is case[-1]


def test_filter_passes_none_podcast_tags():
    matcher = TextPatternMatcher.__new__(TextPatternMatcher)
    matcher._patterns = [SimpleNamespace(scope='global', network_id=None, podcast_id=None,
                                         source='community', sponsor_id=7)]
    matcher._sponsor_tags = {7: {'tech'}}
    assert matcher._filter_patterns_by_scope('p', None, None) == matcher._patterns


@pytest.mark.parametrize('case', FILTER_TABLE)
def test_gate_matches_table(case):
    assert _gate_result(*case[:-1]) is case[-1]


# One row per rule: (scope, pattern network, episode network, pattern podcast, episode podcast,
#  source, sponsor tags, podcast tags, expected)
RULE_TABLE = [
    pytest.param('global', 'n1', None, 'p1', None, 'local', set(), set(), True,
                 id='global ignores network and podcast ids'),
    pytest.param(None, None, None, None, None, 'local', set(), set(), False, id='missing scope'),
    pytest.param('', None, None, None, None, 'local', set(), set(), False, id='empty scope'),
    pytest.param('network', '', '', None, None, 'local', set(), set(), False,
                 id='empty network ids never match'),
    pytest.param('podcast', None, None, '', '', 'local', set(), set(), False,
                 id='empty podcast ids never match'),
    pytest.param('network', 'n1', 'n1', 'p1', 'p2', 'local', set(), set(), True,
                 id='network scope ignores the podcast'),
    pytest.param('podcast', 'n1', 'n2', 'p1', 'p1', 'local', set(), set(), True,
                 id='podcast scope ignores the network'),
    pytest.param('global', None, None, None, None, None, {'tech'}, {'news'}, True,
                 id='missing source is not community'),
    pytest.param('global', None, None, None, None, 'imported', {'tech'}, {'news'}, True,
                 id='other sources skip the tag check'),
    pytest.param('global', None, None, None, None, 'community', {'tech'}, {'news', 'tech'}, True,
                 id='community needs one shared tag'),
    pytest.param('global', None, None, None, None, 'community', {'tech', 'news'}, {'comedy'}, False,
                 id='community drops disjoint tags'),
    pytest.param('global', None, None, None, None, 'community', {U, 'sports'}, {'tech'}, True,
                 id='universal sponsor tag passes'),
    pytest.param('global', None, None, None, None, 'community', {'sports'}, {U}, False,
                 id='universal podcast tag is not a wildcard'),
    pytest.param('global', None, None, None, None, 'community', set(), {'tech'}, True,
                 id='untagged sponsor passes'),
    pytest.param('global', None, None, None, None, 'community', {'sports'}, set(), True,
                 id='untagged podcast passes'),
    pytest.param('network', 'n1', 'n2', None, None, 'community', set(), set(), False,
                 id='scope is checked before tags'),
]


@pytest.mark.parametrize('scope,pat_net,net,pat_pod,pod,source,stags,ptags,expected', RULE_TABLE)
def test_gate_and_filter_follow_each_rule(scope, pat_net, net, pat_pod, pod, source, stags, ptags,
                                          expected):
    case = (scope, pat_net, net, pat_pod, pod, source, stags, ptags)
    assert _gate_result(*case) is expected
    assert _filter_result(*case) is expected


def test_global_matches_everywhere():
    assert pattern_passes_scope('global', None, None, 'local', set(),
                                podcast_id='p', network_id=None, podcast_tag_set=set())


def test_network_requires_matching_network():
    assert not pattern_passes_scope('network', 'n1', None, 'local', set(),
                                    podcast_id='p', network_id=None, podcast_tag_set=set())
    assert pattern_passes_scope('network', 'n1', None, 'local', set(),
                                podcast_id='p', network_id='n1', podcast_tag_set=set())


def test_podcast_scope_and_community_tags():
    assert pattern_passes_scope('podcast', None, 'p', 'local', set(),
                                podcast_id='p', network_id=None, podcast_tag_set=set())
    assert not pattern_passes_scope('global', None, None, 'community', {'tech'},
                                    podcast_id='p', network_id=None, podcast_tag_set={'news'})
    assert pattern_passes_scope('global', None, None, 'community', {UNIVERSAL_TAG},
                                podcast_id='p', network_id=None, podcast_tag_set={'news'})


def test_unknown_scope_is_dropped():
    assert not pattern_passes_scope('bogus', None, None, 'local', set(),
                                    podcast_id='p', network_id='n', podcast_tag_set=set())


def test_scope_module_is_a_leaf():
    tree = ast.parse(open(mod.__file__).read())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported == {'utils.community_tags'}
