"""Tests for the tag-eligibility filter in TextPatternMatcher._filter_patterns_by_scope."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from text_pattern_matcher import AdPattern, TextPatternMatcher  # noqa: E402


def _matcher_with_patterns(patterns, sponsor_tags):
    """Build a matcher with the supplied patterns and sponsor->tags map (bypasses DB)."""
    m = TextPatternMatcher(db=None)
    m._patterns = patterns
    m._sponsor_tags = {sid: set(tags) for sid, tags in sponsor_tags.items()}
    return m


def _community(pid, sponsor_id, scope='global'):
    return AdPattern(
        id=pid, text_template='x', intro_variants=[], outro_variants=[],
        sponsor='S', scope=scope, sponsor_id=sponsor_id, source='community',
    )


def _local(pid, sponsor_id=None, scope='global'):
    return AdPattern(
        id=pid, text_template='x', intro_variants=[], outro_variants=[],
        sponsor='S', scope=scope, sponsor_id=sponsor_id, source='local',
    )


def test_local_patterns_never_filtered_by_tags():
    p = _local(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: ['tech']})
    out = m._filter_patterns_by_scope(podcast_tags={'comedy'})
    assert [x.id for x in out] == [1]


def test_universal_sponsor_matches_everything():
    p = _community(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: ['universal', 'tech']})
    out = m._filter_patterns_by_scope(podcast_tags={'comedy'})
    assert [x.id for x in out] == [1]


def test_tag_overlap_matches():
    p = _community(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: ['tech', 'saas']})
    out = m._filter_patterns_by_scope(podcast_tags={'business', 'tech'})
    assert [x.id for x in out] == [1]


def test_no_overlap_drops_community_pattern():
    p = _community(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: ['gambling']})
    out = m._filter_patterns_by_scope(podcast_tags={'kids_family'})
    assert out == []


def test_empty_sponsor_tags_fallback():
    p = _community(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: []})
    out = m._filter_patterns_by_scope(podcast_tags={'comedy'})
    assert [x.id for x in out] == [1]


def test_empty_podcast_tags_fallback():
    p = _community(1, sponsor_id=10)
    m = _matcher_with_patterns([p], sponsor_tags={10: ['tech']})
    out = m._filter_patterns_by_scope(podcast_tags=None)
    assert [x.id for x in out] == [1]


def test_scope_still_enforced_for_community():
    p = _community(1, sponsor_id=10, scope='podcast')
    p.podcast_id = 'desert-island-discs'
    m = _matcher_with_patterns([p], sponsor_tags={10: ['universal']})
    out = m._filter_patterns_by_scope(podcast_id='other-podcast', podcast_tags=None)
    assert out == []
