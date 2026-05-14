"""Tests for the community export pipeline (src/community_export.py)."""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from community_export import (  # noqa: E402
    ExportError,
    URL_LENGTH_LIMIT_BYTES,
    build_export_payload,
    build_pr_url,
    strip_pii,
)


def _sponsor(name='Squarespace', aliases=None, tags=None, sponsor_id=1):
    return {
        'id': sponsor_id,
        'name': name,
        'aliases': json.dumps(aliases or []),
        'tags': json.dumps(tags or ['tech', 'saas', 'universal']),
        'is_active': 1,
    }


def _pattern(text=None, sponsor_id=1, **overrides):
    base = dict(
        id=42,
        text_template=(
            text
            or 'Go to Squarespace dot com slash show to start your free trial. '
            'Save ten percent on your first website with code SHOW. '
            'Squarespace gives you the tools to launch any idea.'
        ),
        intro_variants=json.dumps(['Go to Squarespace dot com slash show to start your free trial.']),
        outro_variants=json.dumps([]),
        scope='global',
        sponsor_id=sponsor_id,
        confirmation_count=3,
        false_positive_count=0,
        avg_duration=30.0,
        source='local',
    )
    base.update(overrides)
    return base


def test_export_happy_path():
    p = _pattern()
    s = _sponsor()
    payload = build_export_payload(p, [s])
    assert payload['sponsor'] == 'Squarespace'
    assert payload['version'] == 1
    assert payload['sponsor_match'] == 'exact'
    assert 'community_id' in payload
    assert payload['sponsor_tags'] == ['tech', 'saas', 'universal']


def test_export_rejects_short_text():
    p = _pattern(text='Squarespace dot com slash code')
    with pytest.raises(ExportError) as excinfo:
        build_export_payload(p, [_sponsor()])
    assert any('too short' in r for r in excinfo.value.reasons)


def test_export_rejects_low_confirmation():
    p = _pattern(confirmation_count=0)
    with pytest.raises(ExportError) as excinfo:
        build_export_payload(p, [_sponsor()])
    assert any('confirmation_count' in r for r in excinfo.value.reasons)


def test_export_rejects_when_sponsor_not_in_text():
    p = _pattern(text='Some long ad text that does not name the brand at all '
                      'but is long enough to pass the length check and exceeds the minimum '
                      'fifty characters that the gate requires. Visit example dot com for more.')
    with pytest.raises(ExportError) as excinfo:
        build_export_payload(p, [_sponsor()])
    assert any('sponsor name' in r for r in excinfo.value.reasons)


def test_export_rejects_foreign_sponsor_in_text():
    base_text = (
        'Today\'s episode is brought to you by Squarespace. Go to Squarespace dot com '
        'slash show. But also try BetterHelp for your mental health needs.'
    )
    p = _pattern(text=base_text)
    sponsors = [
        _sponsor(),
        {
            'id': 2,
            'name': 'BetterHelp',
            'aliases': '[]',
            'tags': json.dumps(['mental_health', 'universal']),
            'is_active': 1,
        },
    ]
    with pytest.raises(ExportError) as excinfo:
        build_export_payload(p, sponsors)
    assert any('foreign sponsor' in r for r in excinfo.value.reasons)


def test_export_strips_consumer_emails():
    text = (
        'Squarespace ad text long enough. Reach out at user@gmail.com '
        'or contact us at sales@business.example for partnerships. Visit Squarespace today.'
    )
    out = strip_pii(text)
    assert '[email]' in out
    assert 'user@gmail.com' not in out
    assert 'sales@business.example' in out  # business kept


def test_export_strips_non_tollfree_phones_keeps_tollfree():
    text = 'Squarespace. Call 1-800-555-1234 or (212) 555-1234 for help.'
    out = strip_pii(text)
    assert '1-800-555-1234' in out
    assert '(212) 555-1234' not in out
    assert '[phone]' in out


def test_pr_url_fits_for_typical_pattern():
    p = _pattern()
    s = _sponsor()
    payload = build_export_payload(p, [s])
    url, filename, too_large = build_pr_url(payload)
    assert filename.startswith('squarespace-')
    assert filename.endswith('.json')
    assert url.startswith('https://github.com/ttlequals0/MinusPod/new/main/patterns/community')
    assert too_large is False


def test_pr_url_falls_back_when_too_large():
    long_text = 'Squarespace ' + ('x ' * 1700)
    p = _pattern(text=long_text)
    s = _sponsor()
    payload = build_export_payload(p, [s])
    url, _, too_large = build_pr_url(payload)
    # Force the size check
    assert (len(url.encode('utf-8')) > URL_LENGTH_LIMIT_BYTES) == too_large
