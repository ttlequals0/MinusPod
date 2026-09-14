"""Artwork candidate fallback + status-aware negative cache.

extract_podcast_artwork_url returns an ordered candidate list (itunes:image,
then <image><url>). storage.download_artwork tries each candidate in turn,
appending the podcast's already-cached artwork_url as a last-resort fallback
so a broken preferred URL never clears or overwrites valid cached art. Each
candidate's failure is memoized separately, in-process and durably in the
podcasts.artwork_failure_state column, with a longer backoff for a
confirmed-missing (404) resource than for a transient error.
"""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from storage import Storage
from utils.time import utc_now

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('artwork_candidates_test_', reset_storage=True)

import main_app.feeds as mf  # noqa: E402
import storage as storage_mod  # noqa: E402
from api import get_database  # noqa: E402
import api.feeds as api_feeds  # noqa: E402
from main_app import app  # noqa: E402


JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 20
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


def _mock_response(content_type: str | None = None, body: bytes = b'',
                   status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.headers = {'Content-Type': content_type} if content_type else {}
    response.iter_content = lambda chunk_size: (
        body[i:i + chunk_size] for i in range(0, len(body), chunk_size))
    if status_code >= 400:
        def _raise():
            raise RuntimeError(f'HTTP {status_code}')
        response.raise_for_status = _raise
    else:
        response.raise_for_status = lambda: None
    return response


@pytest.fixture(autouse=True)
def _isolate_storage_singleton():
    """Save/restore Storage._instance around each test (mirrors conftest's
    temp_db for Database), so a storage-level test's own tmp_path-rooted
    instance never leaks into the main_app/api tests below that rely on
    the bootstrap-created singleton."""
    previous = Storage._instance
    Storage._instance = None
    yield
    Storage._instance = previous


# --- storage.download_artwork: candidate fallback ---------------------------

def test_preferred_candidate_succeeds(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'pref-ok'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    pref = 'https://cdn.example.com/pref.png'
    alt = 'https://cdn.example.com/alt.png'

    with patch('storage.safe_get', return_value=_mock_response('image/png', PNG)) as mock_get:
        result = storage.download_artwork(slug, [pref, alt])

    assert result is True
    assert mock_get.call_count == 1, "the alternate must not be tried once the preferred succeeds"
    row = storage.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == pref
    assert row['artwork_cached'] == 1


def test_preferred_404_alternate_succeeds_persists_and_skips_preferred_next_refresh(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'pref-404-alt-ok'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    pref = 'https://cdn.example.com/dead-pref.png'
    alt = 'https://cdn.example.com/working-alt.png'
    responses = {pref: _mock_response(status_code=404),
                alt: _mock_response('image/png', PNG)}

    with patch('storage.safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get:
        result = storage.download_artwork(slug, [pref, alt])

    assert result is True
    assert mock_get.call_count == 2
    row = storage.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == alt, "the successful source URL must be persisted"
    assert row['artwork_cached'] == 1

    # A later routine refresh must not re-hit the known-bad preferred
    # candidate, and reads the working alternate from the on-disk cache.
    with patch('storage.safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get2:
        result2 = storage.download_artwork(slug, [pref, alt])

    assert result2 is True
    assert mock_get2.call_count == 0


def test_both_candidates_fail_but_existing_cache_is_retained(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'both-fail-cached'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    old_url = 'https://cdn.example.com/old.png'
    with patch('storage.safe_get', return_value=_mock_response('image/png', PNG)):
        assert storage.download_artwork(slug, old_url) is True
    cached_before = storage.get_artwork(slug)

    pref = 'https://cdn.example.com/new-pref.png'
    alt = 'https://cdn.example.com/new-alt.png'
    responses = {pref: _mock_response(status_code=404),
                alt: _mock_response(status_code=500)}

    with patch('storage.safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get:
        result = storage.download_artwork(slug, [pref, alt])

    assert result is True, "falls back to the still-valid cached cover"
    assert mock_get.call_count == 2, "only the two feed-declared candidates reach the network"
    row = storage.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == old_url, "a failed candidate must not overwrite the cached URL"
    assert row['artwork_cached'] == 1
    assert storage.get_artwork(slug) == cached_before


def test_both_candidates_fail_without_cache_records_404_backoff(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'both-fail-no-cache'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    pref = 'https://cdn.example.com/missing-pref.png'
    alt = 'https://cdn.example.com/missing-alt.png'
    responses = {pref: _mock_response(status_code=404),
                alt: _mock_response(status_code=404)}

    with patch('storage.safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get:
        result = storage.download_artwork(slug, [pref, alt])

    assert result is False
    assert storage.get_artwork(slug) is None
    assert mock_get.call_count == 2

    row = storage.db.get_podcast_by_slug(slug)
    state = json.loads(row['artwork_failure_state'])
    assert state[pref]['status'] == 'not_found'
    assert state[alt]['status'] == 'not_found'

    # Existing failure backoff: an immediate retry makes no further requests.
    with patch('storage.safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get2:
        result2 = storage.download_artwork(slug, [pref, alt])
    assert result2 is False
    assert mock_get2.call_count == 0


# --- negative cache: status-aware + durable ---------------------------------

def test_negative_cache_is_404_aware_and_survives_a_restart(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'durable-pod'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    not_found_url = 'https://cdn.example.com/gone.png'
    error_url = 'https://cdn.example.com/flaky.png'

    seven_hours_ago = (utc_now() - timedelta(hours=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    state = {
        not_found_url: {'status': 'not_found', 'at': seven_hours_ago},
        error_url: {'status': 'error', 'at': seven_hours_ago},
    }
    storage.db.update_podcast(slug, artwork_failure_state=json.dumps(state))

    # Simulate a process restart: a fresh Storage, cold in-process caches,
    # same on-disk data dir and DB row.
    Storage._instance = None
    storage2 = Storage(data_dir=str(tmp_path))

    with patch('storage.safe_get', return_value=_mock_response(status_code=404)) as mock_get:
        storage2.download_artwork(slug, error_url)
        assert mock_get.called, "the shorter generic-error window (6h) must have expired"

        mock_get.reset_mock()
        storage2.download_artwork(slug, not_found_url)
        assert not mock_get.called, "the longer 404 window (24h) must still be active"


def test_negative_cache_resets_after_a_forced_retry_succeeds(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'reset-on-success'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    url = 'https://cdn.example.com/flip.png'

    with patch('storage.safe_get', return_value=_mock_response(status_code=500)):
        assert storage.download_artwork(slug, url) is False
    row = storage.db.get_podcast_by_slug(slug)
    assert json.loads(row['artwork_failure_state'])[url]['status'] == 'error'

    with patch('storage.safe_get', return_value=_mock_response('image/png', PNG)):
        assert storage.download_artwork(slug, url, force=True) is True
    row = storage.db.get_podcast_by_slug(slug)
    assert json.loads(row['artwork_failure_state'] or '{}') == {}, \
        "a successful retry must clear the durable failure record"


def test_negative_cache_is_scoped_to_the_specific_url(temp_db, tmp_path):
    storage = Storage(data_dir=str(tmp_path))
    slug = 'url-change'
    storage.db.create_podcast(slug, 'https://example.com/feed.xml')
    old_url = 'https://cdn.example.com/v1.png'
    new_url = 'https://cdn.example.com/v2.png'

    with patch('storage.safe_get', return_value=_mock_response(status_code=500)):
        assert storage.download_artwork(slug, old_url) is False

    # A different candidate URL (e.g. the feed changed its declared cover)
    # is unaffected by the older URL's backoff.
    with patch('storage.safe_get', return_value=_mock_response('image/png', PNG)) as mock_get:
        assert storage.download_artwork(slug, new_url) is True
    assert mock_get.called


# --- main_app.feeds: candidate fallback in the scheduled refresh path -------

def _feed_with_dead_preferred_and_working_alt():
    return """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Churn Check</title><link>https://example.com</link>
  <description>D</description>
  <itunes:image href="https://cdn.example.com/dead-pref.png"/>
  <image><url>https://cdn.example.com/working-alt.png</url></image>
</channel></rss>"""


def test_refresh_persists_the_alternate_and_stops_retrying_a_dead_preferred():
    slug = 'churn-check'
    mf.db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    mf.invalidate_feed_cache()
    feed = _feed_with_dead_preferred_and_working_alt()
    responses = {
        'https://cdn.example.com/dead-pref.png': _mock_response(status_code=404),
        'https://cdn.example.com/working-alt.png': _mock_response('image/png', PNG),
    }

    with patch.object(mf.rss_parser, 'fetch_feed_conditional', return_value=(feed, None, None)), \
         patch.object(storage_mod, 'safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get:
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml', force=True)

    row = mf.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == 'https://cdn.example.com/working-alt.png'
    assert row['artwork_cached'] == 1
    assert mock_get.call_count == 2

    # A routine (non-forced) second refresh must not churn: no repeat
    # request to the dead preferred candidate, and the alternate is served
    # from the on-disk cache rather than re-fetched.
    mf._refresh_coalesce.invalidate()
    with patch.object(mf.rss_parser, 'fetch_feed_conditional', return_value=(feed, None, None)), \
         patch.object(storage_mod, 'safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get2:
        mf.refresh_rss_feed(slug, f'https://example.com/{slug}.xml', force=False)

    assert mock_get2.call_count == 0
    row = mf.db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == 'https://cdn.example.com/working-alt.png'


# --- api.feeds: missing-cache recovery endpoint uses the candidate fallback -

def test_recovery_endpoint_uses_candidate_fallback():
    db = get_database()
    slug = 'recovery-fallback'
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    pref = 'https://cdn.example.com/dead-pref.png'
    alt = 'https://cdn.example.com/working-alt.png'
    # DB claims cached art, but the file is gone (e.g. restored DB).
    db.update_podcast(slug, artwork_url=pref, artwork_cached=1)

    responses = {pref: _mock_response(status_code=404),
                alt: _mock_response('image/png', PNG)}

    app.config['TESTING'] = True
    with app.test_client() as client, \
         patch.object(api_feeds, '_extract_artwork_candidates_from_feed',
                      return_value=[pref, alt]), \
         patch.object(storage_mod, 'safe_get', side_effect=lambda url, **kw: responses[url]) as mock_get:
        resp = client.get(f'/api/v1/feeds/{slug}/artwork')

    assert resp.status_code == 200
    assert mock_get.call_count == 2
    row = db.get_podcast_by_slug(slug)
    assert row['artwork_url'] == alt
    assert row['artwork_cached'] == 1
