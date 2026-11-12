"""GET /stats/audio-matching counts match history rows and patterns with audio."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match

URL = '/api/v1/stats/audio-matching'
OTHER = 'other-example-feed'


def _get(client, query=''):
    return client.get(f'{URL}{query}', headers=csrf(client))


def _row(db, pid, created, slug=SLUG, rejected=0, rejected_at=None, audio_only=0, start=10.0):
    mid = record_fingerprint_match(db, pid, EPISODE, slug, start, start + 30.0, 0.9, 0.9, True, bool(audio_only))
    conn = db.get_connection()
    conn.execute("UPDATE audio_fingerprint_matches SET created_at = ?, rejected = ?, rejected_at = ? WHERE id = ?",
                 (created, rejected, rejected_at, mid))
    conn.commit()
    return mid


@pytest.fixture
def world(fp_feed):
    """Clean pattern tables, a second feed and one pattern per scope kind."""
    conn = fp_feed.get_connection()
    conn.execute("DELETE FROM ad_patterns")
    conn.commit()
    fp_feed.create_podcast(OTHER, 'https://example.com/other.xml', 'Other Example')
    fp_feed.update_podcast(SLUG, network_id='net-a')
    w = {
        'db': fp_feed,
        'global': fp_feed.create_ad_pattern(scope='global', text_template='global read one ' * 6),
        'mine': fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='mine read two ' * 6),
        'theirs': fp_feed.create_ad_pattern(scope='podcast', podcast_id=OTHER, text_template='theirs read three ' * 6),
        'net_a': fp_feed.create_ad_pattern(scope='network', network_id='net-a', text_template='net a read four ' * 6),
        'net_b': fp_feed.create_ad_pattern(scope='network', network_id='net-b', text_template='net b read five ' * 6),
        'inactive': fp_feed.create_ad_pattern(scope='global', text_template='inactive read six ' * 6),
    }
    fp_feed.update_ad_pattern(w['inactive'], is_active=0)
    for key in ('global', 'mine', 'theirs', 'net_a', 'inactive'):
        fp_feed.create_audio_fingerprint(w[key], b'AQAA', 6.0)
    fp_feed.create_audio_fingerprint(w['net_b'], b'AQAA', 6.0)
    fp_feed.set_fingerprint_enabled(w['net_b'], False)
    yield w
    conn.execute("DELETE FROM ad_patterns")
    conn.commit()
    fp_feed.delete_podcast(OTHER)
    fp_feed.update_podcast(SLUG, network_id=None)


def test_pattern_counts_cover_active_patterns_with_an_enabled_fingerprint(app_client, world):
    body = _get(app_client).get_json()
    assert body['patternsTotal'] == 5
    assert body['patternsWithAudio'] == 4
    assert set(body) == {'patternsWithAudio', 'patternsTotal', 'audioMatches', 'foundByAudioOnly', 'notThisAd'}


def test_pattern_counts_follow_the_feed_scope(app_client, world):
    body = _get(app_client, f'?podcastSlug={SLUG}').get_json()
    assert body['patternsTotal'] == 3 and body['patternsWithAudio'] == 3
    other = _get(app_client, f'?podcastSlug={OTHER}').get_json()
    assert other['patternsTotal'] == 2 and other['patternsWithAudio'] == 2


def test_feed_network_override_changes_the_scope(app_client, world):
    world['db'].update_podcast(SLUG, network_id_override='net-b')
    body = _get(app_client, f'?podcastSlug={SLUG}').get_json()
    assert body['patternsTotal'] == 3 and body['patternsWithAudio'] == 2


def test_counts_in_period(app_client, world):
    db, p = world['db'], world['mine']
    _row(db, p, '2026-11-01T00:00:00Z')
    _row(db, p, '2026-11-15T12:00:00Z', audio_only=1, start=50.0)
    _row(db, p, '2026-11-30T23:59:59Z', audio_only=1, start=90.0)
    _row(db, p, '2026-12-01T00:00:00Z', start=130.0)
    _row(db, p, '2026-10-31T23:59:59Z', start=170.0)
    body = _get(app_client, '?from=2026-11-01&to=2026-11-30').get_json()
    assert (body['audioMatches'], body['foundByAudioOnly'], body['notThisAd']) == (3, 2, 0)
    assert _get(app_client).get_json()['audioMatches'] == 5


def test_to_accepts_a_timestamp_and_means_the_whole_utc_day(app_client, world):
    db, p = world['db'], world['mine']
    _row(db, p, '2026-11-30T23:59:59Z')
    _row(db, p, '2026-12-01T00:00:00Z', start=50.0)
    assert _get(app_client, '?to=2026-11-30T03:00:00Z').get_json()['audioMatches'] == 1
    assert _get(app_client, '?from=2026-11-30T23:00:00Z&to=2026-11-30').get_json()['audioMatches'] == 1
    assert _get(app_client, '?from=2026-12-01').get_json()['audioMatches'] == 1


def test_not_this_ad_counts_by_rejected_at_and_leaves_audio_matches(app_client, world):
    db, p = world['db'], world['mine']
    _row(db, p, '2026-10-01T00:00:00Z', rejected=1, rejected_at='2026-11-10T00:00:00Z', audio_only=1)
    _row(db, p, '2026-11-10T00:00:00Z', rejected=1, rejected_at='2026-12-10T00:00:00Z', start=50.0)
    _row(db, p, '2026-11-10T00:00:00Z', start=90.0, audio_only=1)
    body = _get(app_client, '?from=2026-11-01&to=2026-11-30').get_json()
    assert body['notThisAd'] == 1
    assert body['audioMatches'] == 1 and body['foundByAudioOnly'] == 1


def test_podcast_filter_applies_to_rows(app_client, world):
    db = world['db']
    _row(db, world['mine'], '2026-11-02T00:00:00Z')
    _row(db, world['theirs'], '2026-11-02T00:00:00Z', slug=OTHER, audio_only=1)
    _row(db, world['global'], '2026-11-03T00:00:00Z', slug=OTHER, rejected=1, rejected_at='2026-11-04T00:00:00Z',
         start=50.0)
    mine = _get(app_client, f'?podcastSlug={SLUG}').get_json()
    assert (mine['audioMatches'], mine['foundByAudioOnly'], mine['notThisAd']) == (1, 0, 0)
    other = _get(app_client, f'?podcastSlug={OTHER}').get_json()
    assert (other['audioMatches'], other['foundByAudioOnly'], other['notThisAd']) == (1, 1, 1)
    assert _get(app_client).get_json()['audioMatches'] == 2


def test_unknown_feed_answers_zeros(app_client, world):
    _row(world['db'], world['mine'], '2026-11-02T00:00:00Z')
    r = _get(app_client, '?podcastSlug=no-such-feed')
    assert r.status_code == 200
    assert r.get_json() == {'patternsWithAudio': 0, 'patternsTotal': 0, 'audioMatches': 0,
                            'foundByAudioOnly': 0, 'notThisAd': 0}


@pytest.mark.parametrize('query,name', [('?from=notadate', 'from'), ('?to=2026-13-45', 'to'),
                                        ('?from=2026-11-01&to=junk', 'to')])
def test_bad_dates_are_400_like_the_other_stats_routes(app_client, world, query, name):
    r = _get(app_client, query)
    assert r.status_code == 400 and r.get_json()['error'].startswith(f'{name} must be a date')
    neighbour = app_client.get(f'/api/v1/stats/llm{query}', headers=csrf(app_client))
    assert neighbour.get_json()['error'] == r.get_json()['error']


def test_empty_history_is_all_zero_counts(app_client, world):
    body = _get(app_client, '?from=2026-11-01&to=2026-11-30').get_json()
    assert (body['audioMatches'], body['foundByAudioOnly'], body['notThisAd']) == (0, 0, 0)
