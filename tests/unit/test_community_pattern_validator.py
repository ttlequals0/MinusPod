"""Tests for the community_pattern_validator (canonicalization + dedupe)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tools.community_pattern_validator import (  # noqa: E402
    canonicalize_for_dedupe,
    dedupe,
    validate_doc,
)
from utils.community_tags import sponsor_seed  # noqa: E402


def test_canonicalize_strips_stopwords_and_dates():
    text = (
        'On Monday March 5 2024, the show is brought to you by Squarespace! '
        'Visit by 12/31. We are sponsored by example dot com.'
    )
    out = canonicalize_for_dedupe(text)
    assert 'monday' not in out
    assert 'march' not in out
    assert '2024' not in out
    assert '12 31' not in out
    assert '12/31' not in out
    assert 'show' in out
    assert 'squarespace' in out
    assert 'the' not in out.split()
    assert 'by' not in out.split()


def test_canonicalize_relative_time_stripped():
    out = canonicalize_for_dedupe('Sign up today or tomorrow for our weekend offer')
    assert 'today' not in out
    assert 'tomorrow' not in out
    assert 'weekend' not in out
    assert 'sign' in out
    assert 'offer' in out


def test_dedupe_identifies_duplicate_when_near_identical():
    base = 'Visit Squarespace to launch your website with confidence today.'
    near = 'Visit Squarespace and launch your website with confidence today!'
    existing = [{'sponsor': 'Squarespace', 'text_template': base, 'community_id': 'A'}]
    doc = {'sponsor': 'Squarespace', 'text_template': near}
    classification, matched, score = dedupe(doc, existing)
    assert classification == 'duplicate'
    assert matched['community_id'] == 'A'
    assert score >= 0.95


def test_dedupe_identifies_variant():
    # Same sponsor + same opener and closing sentence, middle CTA swapped —
    # calibrated to land around 0.78-0.85 on SequenceMatcher.
    base = (
        'Visit Squarespace dot com slash show for a free trial. Use code SHOW '
        'to save ten percent on your first website. Squarespace gives you the '
        'tools to launch any idea online.'
    )
    variant = (
        'Visit Squarespace dot com slash show for a free trial. Build with the '
        'Squarespace tools and launch any idea online today.'
    )
    existing = [{'sponsor': 'Squarespace', 'text_template': base, 'community_id': 'A'}]
    doc = {'sponsor': 'Squarespace', 'text_template': variant}
    classification, _, score = dedupe(doc, existing)
    assert classification == 'variant', f'got {classification} score={score:.3f}'
    assert 0.75 <= score < 0.95


def test_dedupe_identifies_distinct_different_sponsors():
    existing = [
        {'sponsor': 'NordVPN', 'text_template': 'Visit NordVPN today for forty percent off', 'community_id': 'A'}
    ]
    doc = {'sponsor': 'Squarespace', 'text_template': 'Visit Squarespace today for ten percent off your site'}
    classification, matched, score = dedupe(doc, existing)
    assert classification == 'distinct'
    assert score == 0.0


def test_validate_doc_rejects_unknown_tags():
    seed = sponsor_seed()
    doc = {
        'community_id': 'abc',
        'version': 1,
        'sponsor': 'Squarespace',
        'submitted_at': '2026-01-01T00:00:00Z',
        'text_template': 'Squarespace dot com slash show for ten percent off your website today launch confidently!',
        'sponsor_tags': ['tech', 'not_a_real_tag'],
    }
    result = validate_doc('a.json', doc, seed, [])
    assert result.status == 'reject'
    assert any('unknown tag: not_a_real_tag' in e for e in result.errors)


def test_validate_doc_warns_unknown_sponsor():
    seed = sponsor_seed()
    doc = {
        'community_id': 'abc',
        'version': 1,
        'sponsor': 'AcmeBrandThatDoesNotExist',
        'submitted_at': '2026-01-01T00:00:00Z',
        'text_template': 'AcmeBrandThatDoesNotExist dot com slash show ten percent off launch your idea today now',
        'sponsor_tags': [],
    }
    result = validate_doc('a.json', doc, seed, [])
    assert result.status == 'warn'
    assert result.sponsor_match == 'unknown'


def test_validate_doc_rejects_missing_required():
    seed = sponsor_seed()
    doc = {'community_id': 'abc'}
    result = validate_doc('a.json', doc, seed, [])
    assert result.status == 'reject'
    assert any('required field' in e for e in result.errors)
