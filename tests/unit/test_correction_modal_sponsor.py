"""Reviewer-supplied sponsor in the ad review modal must reach pattern creation.

Issue #804: when an ad is detected with no sponsor (pattern_id=None,
sponsor=None), the reviewer types a sponsor in the modal and saves. The
sponsor was silently dropped: approve() did not forward it, and
_resolve_or_create_pattern_from_text fell back to text extraction, which
failed the brand-placement gate for domains like dsw.com.

The fix passes the modal sponsor through the confirm/adjust correction payload
as a top-level 'sponsor' field and uses it as sponsor_override in
_resolve_or_create_pattern_from_text before falling back to original_ad and
text extraction.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('modal_sponsor_test_')
from main_app import app

SLUG = 'modal-sponsor-podcast'
EPISODE_ID = 'abc123def804'

# Ad text whose sponsor is mentioned twice so it passes the brand-placement gate.
CLEAN_AD_MODAL_SPONSOR = (
    "[00:00:00.000 --> 00:00:30.000] Designer Shoe Warehouse has the best "
    "selection of shoes available online. Head to dsw dot com slash podcast "
    "for twenty percent off your first order from Designer Shoe Warehouse today."
)

# Ad text that contains only a domain -- text extraction resolves the wrong
# casing ('Dsw') and fails the brand-placement gate without a modal sponsor.
AD_TEXT_DOMAIN_ONLY = (
    "[00:00:00.000 --> 00:00:30.000] Head to dsw dot com slash podcast for "
    "twenty percent off your first order of shoes and accessories at dsw today."
)

# Ad text with no recognisable sponsor and no actionable domain.
AD_TEXT_NO_SPONSOR = (
    "[00:00:00.000 --> 00:00:30.000] Head to our sponsor website and use "
    "the code podcast at checkout for twenty percent off your entire order "
    "of shoes and accessories available now at that link in the show notes today."
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _mock_db(transcript):
    db = MagicMock()
    db.get_transcript_for_timestamps.return_value = transcript
    db.get_podcast_by_slug.return_value = {'id': 99, 'slug': SLUG}
    db.find_pattern_by_text.return_value = None
    db.get_known_sponsor_by_name.return_value = None
    db.get_ad_patterns.return_value = []
    db.get_setting_float.side_effect = lambda key, default=None: default
    db.get_setting_bool.side_effect = lambda key, default=None: default
    db.create_ad_pattern.return_value = 5678
    return db


def _confirm_with_modal_sponsor(client, db, start, end, modal_sponsor):
    """Simulate the reviewer typing a sponsor in the modal and confirming."""
    payload = {
        'type': 'confirm',
        'original_ad': {'start': start, 'end': end},
        'sponsor': modal_sponsor,
    }
    with patch('api.patterns.get_database', return_value=db):
        return client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(payload), content_type='application/json',
        )


def _adjust_with_modal_sponsor(client, db, orig_start, orig_end,
                               adj_start, adj_end, modal_sponsor):
    """Simulate the reviewer adjusting bounds and typing a sponsor."""
    payload = {
        'type': 'adjust',
        'original_ad': {'start': orig_start, 'end': orig_end},
        'adjusted_start': adj_start,
        'adjusted_end': adj_end,
        'sponsor': modal_sponsor,
    }
    with patch('api.patterns.get_database', return_value=db):
        return client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(payload), content_type='application/json',
        )


def test_confirm_with_modal_sponsor_reaches_resolve(client):
    """Modal sponsor is forwarded to the pattern resolution path on confirm."""
    db = _mock_db(CLEAN_AD_MODAL_SPONSOR)
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (5678, [5678])
        resp = _confirm_with_modal_sponsor(
            client, db, 0.0, 30.0, 'Designer Shoe Warehouse')
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    _, kwargs = mock_resolve.call_args
    assert kwargs.get('sponsor_override') == 'Designer Shoe Warehouse'


def test_confirm_without_modal_sponsor_passes_none_override(client):
    """When no modal sponsor is given, sponsor_override is None (fallback runs)."""
    db = _mock_db(CLEAN_AD_MODAL_SPONSOR)
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        payload = {
            'type': 'confirm',
            'original_ad': {'start': 0.0, 'end': 30.0},
        }
        with patch('api.patterns.get_database', return_value=db):
            resp = client.post(
                f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
                data=json.dumps(payload), content_type='application/json',
            )
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    _, kwargs = mock_resolve.call_args
    assert kwargs.get('sponsor_override') is None


def test_adjust_with_modal_sponsor_reaches_resolve(client):
    """Modal sponsor is forwarded to the pattern resolution path on adjust."""
    db = _mock_db(CLEAN_AD_MODAL_SPONSOR)
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (5678, [5678])
        resp = _adjust_with_modal_sponsor(
            client, db, 0.0, 35.0, 0.0, 30.0, 'Designer Shoe Warehouse')
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    _, kwargs = mock_resolve.call_args
    assert kwargs.get('sponsor_override') == 'Designer Shoe Warehouse'


def test_adjust_empty_modal_sponsor_passes_none_override(client):
    """An empty sponsor string from the modal is normalised to None."""
    db = _mock_db(AD_TEXT_NO_SPONSOR)
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        resp = _adjust_with_modal_sponsor(
            client, db, 0.0, 35.0, 0.0, 30.0, '')
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    _, kwargs = mock_resolve.call_args
    assert kwargs.get('sponsor_override') is None


def test_confirm_modal_sponsor_used_as_override_in_resolve(temp_db):
    """Unit: sponsor_override in _resolve_or_create_pattern_from_text
    takes precedence over original_ad.sponsor and text extraction."""
    from api.patterns import _resolve_or_create_pattern_from_text
    from pattern_service import PatternService

    slug = 'modal-resolve-test'
    episode_id = 'deadbeef0001'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Modal Resolve Test')
    temp_db.upsert_episode(
        slug=slug, episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )
    svc = PatternService(temp_db)

    # Ad text mentions the brand twice so it passes the brand-placement gate.
    ad_text = (
        "Designer Shoe Warehouse has the best selection of shoes available "
        "online. Head to dsw dot com slash podcast for twenty percent off your "
        "first order from Designer Shoe Warehouse today and every day this week."
    )

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, ad_text,
        {'start': 0.0, 'end': 30.0},
        label='confirmed',
        sponsor_override='Designer Shoe Warehouse',
    )

    assert primary_id is not None
    pattern = temp_db.get_ad_pattern_by_id(primary_id)
    assert pattern['sponsor'] == 'Designer Shoe Warehouse'


def test_sponsor_override_beats_original_ad_sponsor(temp_db):
    """sponsor_override wins over the stale sponsor name on the original_ad row."""
    from api.patterns import _resolve_or_create_pattern_from_text
    from pattern_service import PatternService

    slug = 'modal-override-test'
    episode_id = 'deadbeef0002'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Modal Override Test')
    temp_db.upsert_episode(
        slug=slug, episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )
    svc = PatternService(temp_db)

    ad_text = (
        "Designer Shoe Warehouse has the best shoes you can find online. "
        "Head to dsw dot com slash podcast for twenty percent off your "
        "first order from Designer Shoe Warehouse today and every single day."
    )

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, ad_text,
        # original_ad has a wrong/stale sponsor: the override must win.
        {'start': 0.0, 'end': 30.0, 'sponsor': 'OldSponsorName'},
        label='confirmed',
        sponsor_override='Designer Shoe Warehouse',
    )

    assert primary_id is not None
    pattern = temp_db.get_ad_pattern_by_id(primary_id)
    assert pattern['sponsor'] == 'Designer Shoe Warehouse'
