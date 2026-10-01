"""The review modal's sponsor reaches pattern creation on confirm and adjust (#804)."""
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

    # Extraction from the reason alone would name a different sponsor.
    primary_id, _ = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, ad_text,
        {'start': 0.0, 'end': 30.0, 'reason': 'Sponsored by Globex.'},
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

    primary_id, _ = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, ad_text,
        # original_ad has a wrong/stale sponsor: the override must win.
        {'start': 0.0, 'end': 30.0, 'sponsor': 'OldSponsorName'},
        label='confirmed',
        sponsor_override='Designer Shoe Warehouse',
    )

    assert primary_id is not None
    pattern = temp_db.get_ad_pattern_by_id(primary_id)
    assert pattern['sponsor'] == 'Designer Shoe Warehouse'


def test_without_the_override_no_pattern_is_created(temp_db):
    """Text extraction finds no sponsor here, so only the modal sponsor creates a pattern."""
    from api.patterns import _resolve_or_create_pattern_from_text
    from pattern_service import PatternService

    slug = 'modal-regression-test'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Modal Regression Test')
    ad_text = (
        "Designer Shoe Warehouse has the best selection of shoes available online. "
        "Stop by any store for twenty percent off your first order from Designer "
        "Shoe Warehouse today and every day this week."
    )
    for i, (override, created) in enumerate([(None, False), ('Designer Shoe Warehouse', True)]):
        episode_id = f'deadbeef00{i + 3}'
        temp_db.upsert_episode(
            slug=slug, episode_id=episode_id, original_url='https://example.com/ep.mp3',
            title='Test Episode', original_duration=3600.0)
        primary_id, _ = _resolve_or_create_pattern_from_text(
            temp_db, PatternService(temp_db), slug, episode_id, ad_text,
            {'start': 0.0, 'end': 30.0}, label='confirmed', sponsor_override=override)
        assert (primary_id is not None) is created


@pytest.mark.parametrize('kind', ['confirm', 'adjust'])
@pytest.mark.parametrize('sponsor', [42, ['Acme'], {'name': 'Acme'}])
def test_non_string_modal_sponsor_is_rejected(client, kind, sponsor):
    db = _mock_db(CLEAN_AD_MODAL_SPONSOR)
    send = _confirm_with_modal_sponsor if kind == 'confirm' else (
        lambda c, d, s, e, sp: _adjust_with_modal_sponsor(c, d, s, e + 5.0, s, e, sp))
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        resp = send(client, db, 0.0, 30.0, sponsor)
    assert resp.status_code == 400
    mock_resolve.assert_not_called()


def test_whitespace_only_modal_sponsor_is_none(client):
    db = _mock_db(CLEAN_AD_MODAL_SPONSOR)
    with patch('api.patterns._resolve_or_create_pattern_from_text') as mock_resolve:
        mock_resolve.return_value = (None, [])
        resp = _confirm_with_modal_sponsor(client, db, 0.0, 30.0, '   ')
    assert resp.status_code == 200
    assert mock_resolve.call_args.kwargs['sponsor_override'] is None
