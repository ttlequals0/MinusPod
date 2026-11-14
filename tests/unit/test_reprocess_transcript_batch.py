"""LLM-only reprocess loops check transcripts with one query per feed."""
from tests.app_bootstrap import bootstrap

bootstrap('reprocess_transcript_batch_test_')

from tests.integration.fingerprint_helpers import csrf  # noqa: E402

SLUG = 'example-podcast'
IDS = ('a1b2c3d4e5f6', 'b1b2c3d4e5f6', 'c1b2c3d4e5f6')


def _feed_with_one_transcript(db):
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    for eid in IDS:
        db.upsert_episode(SLUG, eid, title=eid, status='processed')
    db.save_episode_details(SLUG, IDS[0], transcript_text='full transcript text')
    db.save_episode_details(SLUG, IDS[1], transcript_text='')


def test_episode_ids_with_transcript_matches_has_transcript(temp_db):
    _feed_with_one_transcript(temp_db)
    assert temp_db.get_episode_ids_with_transcript(SLUG) == {
        eid for eid in IDS if temp_db.has_transcript(SLUG, eid)} == {IDS[0]}
    assert temp_db.get_episode_ids_with_transcript('missing') == set()


def test_llm_reprocess_loops_skip_the_per_episode_check(app_client, temp_db, monkeypatch):
    _feed_with_one_transcript(temp_db)

    def boom(*_a, **_k):
        raise AssertionError('has_transcript ran per episode')

    monkeypatch.setattr(temp_db, 'has_transcript', boom)
    headers = csrf(app_client)

    bulk = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/bulk', headers=headers,
                           json={'episodeIds': list(IDS), 'action': 'reprocess_llm'})
    assert bulk.status_code == 200
    assert (bulk.get_json()['queued'], bulk.get_json()['skipped']) == (1, 2)

    temp_db.upsert_episode(SLUG, IDS[0], status='processed')
    everything = app_client.post(f'/api/v1/feeds/{SLUG}/reprocess-all', headers=headers,
                                 json={'mode': 'llm'})
    assert everything.status_code == 200
    episodes = everything.get_json()['episodes']
    assert [e['episodeId'] for e in episodes['queued']] == [IDS[0]]
    assert {e['episodeId'] for e in episodes['skipped']} == {IDS[1], IDS[2]}


def test_failing_transcript_read_gives_per_episode_errors(app_client, temp_db, monkeypatch):
    _feed_with_one_transcript(temp_db)

    def boom(*_a, **_k):
        raise RuntimeError('db down')

    monkeypatch.setattr(temp_db, 'get_episode_ids_with_transcript', boom)
    response = app_client.post(f'/api/v1/feeds/{SLUG}/episodes/bulk', headers=csrf(app_client),
                               json={'episodeIds': list(IDS), 'action': 'reprocess_llm'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['queued'] == 0
    assert body['errors'] == [f'{eid}: bulk action failed' for eid in IDS]
