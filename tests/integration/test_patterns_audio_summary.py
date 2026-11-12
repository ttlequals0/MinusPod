"""GET /patterns and GET /patterns/{id} carry an audio summary computed in the database."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, TEMPLATE, csrf  # noqa: E402


@pytest.fixture
def mixed(fp_feed):
    none_id = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE + ' none')
    off_id = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE + ' off')
    active_id = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE + ' active')
    for pid in (off_id, active_id):
        fp_feed.create_audio_fingerprint(pid, b'AQAA', 6.0, source_podcast_slug=SLUG,
                                         source_episode_id=EPISODE, source_start=2.0, source_end=8.0)
    fp_feed.set_fingerprint_enabled(off_id, False)
    return {'none': none_id, 'off': off_id, 'active': active_id}


def _list(client):
    body = client.get('/api/v1/patterns', headers=csrf(client)).get_json()
    return {p['id']: p for p in body['patterns']}


def test_list_includes_audio_summary(app_client, mixed):
    patterns = _list(app_client)
    assert patterns[mixed['none']]['audio'] == {
        'state': 'none', 'available': False, 'matchCount': 0, 'meanSimilarity': None}
    assert patterns[mixed['off']]['audio']['state'] == 'off'
    assert patterns[mixed['active']]['audio']['state'] == 'active'


def test_detail_includes_audio_summary(app_client, fp_feed, mixed):
    fp_feed.record_fingerprint_match(mixed['active'], EPISODE, SLUG, 10.0, 40.0, 0.8, 0.9, True, False)
    fp_feed.record_fingerprint_match(mixed['active'], 'b1c2d3e4f5a6', SLUG, 10.0, 40.0, 0.6, 0.9, True, False)
    body = app_client.get(f"/api/v1/patterns/{mixed['active']}", headers=csrf(app_client)).get_json()
    assert body['audio']['state'] == 'active' and body['audio']['matchCount'] == 2
    assert body['audio']['meanSimilarity'] == pytest.approx(0.7)
    none = app_client.get(f"/api/v1/patterns/{mixed['none']}", headers=csrf(app_client)).get_json()
    assert none['audio']['state'] == 'none'


def test_rejected_matches_leave_count_and_mean(app_client, fp_feed, mixed):
    pid = mixed['active']
    fp_feed.record_fingerprint_match(pid, EPISODE, SLUG, 10.0, 40.0, 0.8, 0.9, True, False)
    fp_feed.record_fingerprint_match(pid, 'b1c2d3e4f5a6', SLUG, 10.0, 40.0, 0.2, 0.9, True, False)
    assert fp_feed.mark_fingerprint_match_rejected(pid, SLUG, 'b1c2d3e4f5a6', 10.0, 40.0)
    audio = _list(app_client)[pid]['audio']
    assert audio['matchCount'] == 1 and audio['meanSimilarity'] == pytest.approx(0.8)


def test_available_reads_the_original_file_column_not_the_disk(app_client, fp_feed, mixed):
    pid = mixed['active']
    assert _list(app_client)[pid]['audio']['available'] is False
    fp_feed.upsert_episode(SLUG, EPISODE, original_file='x.mp3')
    assert _list(app_client)[pid]['audio']['available'] is True
    assert _list(app_client)[mixed['none']]['audio']['available'] is False


def test_available_follows_a_retained_match_episode(app_client, fp_feed, mixed):
    pid = mixed['active']
    fp_feed.upsert_episode(SLUG, 'b1c2d3e4f5a6', original_url='https://example.com/b.mp3',
                           title='Other', status='processed', original_file='y.mp3')
    assert _list(app_client)[pid]['audio']['available'] is False
    fp_feed.record_fingerprint_match(pid, 'b1c2d3e4f5a6', SLUG, 10.0, 40.0, 0.8, 0.9, True, False)
    assert _list(app_client)[pid]['audio']['available'] is True
    assert fp_feed.mark_fingerprint_match_rejected(pid, SLUG, 'b1c2d3e4f5a6', 10.0, 40.0)
    assert _list(app_client)[pid]['audio']['available'] is False


def test_list_runs_one_summary_query_not_one_per_pattern(app_client, fp_feed, mixed, monkeypatch):
    calls = []
    real = type(fp_feed).get_audio_summaries
    monkeypatch.setattr(type(fp_feed), 'get_audio_summaries',
                        lambda self, ids: calls.append(ids) or real(self, ids))
    monkeypatch.setattr(type(fp_feed), 'get_audio_fingerprint',
                        lambda self, pid: pytest.fail('per-row fingerprint lookup'))
    _list(app_client)
    assert len(calls) == 1 and set(mixed.values()) <= set(calls[0])


def test_summaries_for_chunks(temp_db, monkeypatch):
    import database.fingerprint_matches as module
    ids = [temp_db.create_ad_pattern(scope='podcast', podcast_id='show', text_template=f'read number {i} ' * 8)
           for i in range(5)]
    temp_db.create_audio_fingerprint(ids[3], b'AQAA', 6.0)
    monkeypatch.setattr(module, 'SUMMARY_ID_CHUNK', 2)
    chunked = temp_db.get_audio_summaries(ids)
    assert set(chunked) == set(ids) and chunked[ids[3]]['state'] == 'active'
    assert temp_db.get_audio_summaries([]) == {}
    assert temp_db.get_audio_summaries([987654]) == {}
