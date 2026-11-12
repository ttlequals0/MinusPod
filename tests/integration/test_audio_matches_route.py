"""GET /patterns/{id}/audio/matches pages through the match history."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match


def _matches(client, pid, query=''):
    return client.get(f'/api/v1/patterns/{pid}/audio/matches{query}', headers=csrf(client))


@pytest.fixture
def twelve(fp_feed, fp_audio_pattern):
    for i in range(12):
        record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 10.0 + i, 40.0 + i,
                                         0.9, 0.8, True, False)
    return fp_audio_pattern


def test_pagination_defaults_to_ten(app_client, twelve):
    body = _matches(app_client, twelve).get_json()
    assert body['perPage'] == 10 and body['page'] == 1
    assert len(body['matches']) == 10 and body['total'] == 12


def test_second_page_and_newest_first(app_client, twelve):
    first = _matches(app_client, twelve).get_json()['matches']
    second = _matches(app_client, twelve, '?page=2').get_json()
    assert len(second['matches']) == 2 and second['page'] == 2
    ids = [m['id'] for m in first] + [m['id'] for m in second['matches']]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 12


def test_per_page_is_capped_at_fifty_and_floored_at_one(app_client, twelve):
    assert _matches(app_client, twelve, '?perPage=500').get_json()['perPage'] == 50
    assert _matches(app_client, twelve, '?perPage=0').get_json()['perPage'] == 1
    assert _matches(app_client, twelve, '?perPage=5&page=3').get_json()['matches'][0]['start'] == 10.0 + 1


def test_page_past_the_end_is_empty(app_client, twelve):
    body = _matches(app_client, twelve, '?page=9').get_json()
    assert body['matches'] == [] and body['total'] == 12


def test_junk_paging_falls_back_to_defaults(app_client, twelve):
    body = _matches(app_client, twelve, '?page=abc&perPage=x').get_json()
    assert (body['page'], body['perPage']) == (1, 10)
    assert _matches(app_client, twelve, '?page=-4').get_json()['page'] == 1


def test_row_shape(app_client, fp_feed, fp_audio_pattern, fp_original):
    record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 12.5, 41.0, 0.81, 0.62, False, None)
    row = _matches(app_client, fp_audio_pattern).get_json()['matches'][0]
    assert set(row) == {'id', 'episodeId', 'podcastSlug', 'episodeTitle', 'start', 'end', 'similarity',
                        'coverage', 'storedLength', 'inScope', 'audioOnly', 'rejected', 'available',
                        'createdAt'}
    assert (row['episodeId'], row['podcastSlug'], row['episodeTitle']) == (EPISODE, SLUG, 'Episode One')
    assert (row['start'], row['end'], row['similarity'], row['coverage']) == (12.5, 41.0, 0.81, 0.62)
    assert row['storedLength'] == 6.0
    assert row['inScope'] is False and row['audioOnly'] is None and row['rejected'] is False
    assert row['available'] is True and row['createdAt']


def test_audio_only_and_rejected_flags(app_client, fp_feed, fp_audio_pattern):
    record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 10.0, 40.0, 0.9, 0.8, True, True)
    assert fp_feed.mark_fingerprint_match_rejected(fp_audio_pattern, SLUG, EPISODE, 10.0, 40.0)
    row = _matches(app_client, fp_audio_pattern).get_json()['matches'][0]
    assert row['audioOnly'] is True and row['rejected'] is True


def test_expired_rows_marked_unavailable(app_client, fp_feed, fp_audio_pattern):
    record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 10.0, 40.0, 0.9, 0.8, True, False)
    assert _matches(app_client, fp_audio_pattern).get_json()['matches'][0]['available'] is False


def test_flag_set_but_file_gone_is_unavailable(app_client, fp_feed, fp_audio_pattern):
    fp_feed.upsert_episode(SLUG, EPISODE, original_file='gone.mp3')
    record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 10.0, 40.0, 0.9, 0.8, True, False)
    assert _matches(app_client, fp_audio_pattern).get_json()['matches'][0]['available'] is False


def test_row_of_a_deleted_episode_stays_with_no_title(app_client, fp_feed, fp_audio_pattern):
    record_fingerprint_match(fp_feed, fp_audio_pattern, 'ffffffffffff', SLUG, 10.0, 40.0, 0.9, 0.8, True, False)
    row = _matches(app_client, fp_audio_pattern).get_json()['matches'][0]
    assert row['episodeTitle'] is None and row['available'] is False


def test_rows_with_the_same_episode_id_in_two_feeds_stay_apart(app_client, fp_feed, fp_audio_pattern, fp_original):
    fp_feed.create_podcast('other-feed', 'https://example.com/other.xml', 'Other')
    fp_feed.upsert_episode('other-feed', EPISODE, original_url='https://example.com/o.mp3',
                           title='Other feed episode', status='processed')
    try:
        record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, 'other-feed', 10.0, 40.0, 0.9, 0.8, True, False)
        row = _matches(app_client, fp_audio_pattern).get_json()['matches'][0]
        assert row['episodeTitle'] == 'Other feed episode' and row['available'] is False
    finally:
        fp_feed.delete_podcast('other-feed')


def test_pattern_without_audio_has_an_empty_history(app_client, fp_pattern):
    body = _matches(app_client, fp_pattern).get_json()
    assert body['matches'] == [] and body['total'] == 0


def test_unknown_pattern_is_404(app_client, fp_feed):
    assert _matches(app_client, 987654).status_code == 404
