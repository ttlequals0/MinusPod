"""Integration tests for the /split-candidates endpoint (issue #563)."""
import pytest

from api import get_database


def _ts(v):
    return f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{v % 60:06.3f}"


def _vtt(*rows):
    return '\n'.join(f"[{_ts(a)} --> {_ts(b)}] {t}" for a, b, t in rows)


THREE_ADS = _vtt(
    (100.0, 130.0, 'Today you can save at Acme dot com with code SAVE.'),
    (130.0, 160.0, 'This episode is brought to you by Beta Corp, the easy way to file.'),
    (160.0, 190.0, 'And our thanks to Gamma Industries for supporting the show.'),
)


@pytest.fixture
def episode_with_transcript(app_client):
    db = get_database()
    slug = 'split-candidates-feed'
    episode_id = 'a1b2c3d4e5f6'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'The Daily Tech Show')
    db.upsert_episode(slug=slug, episode_id=episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Episode One', original_duration=3600.0,
                      status='processed')
    db.save_episode_details(slug, episode_id, transcript_text=THREE_ADS)
    yield {'slug': slug, 'episodeId': episode_id}
    db.delete_podcast(slug)


def _url(ref, start, end):
    return (f"/api/v1/feeds/{ref['slug']}/episodes/{ref['episodeId']}"
            f"/split-candidates?start={start}&end={end}")


def test_missing_episode_is_404(app_client):
    resp = app_client.get(
        '/api/v1/feeds/no-such-feed/episodes/a1b2c3d4e5f6'
        '/split-candidates?start=0&end=10')
    assert resp.status_code == 404


def test_start_and_end_are_required(app_client, episode_with_transcript):
    ref = episode_with_transcript
    resp = app_client.get(
        f"/api/v1/feeds/{ref['slug']}/episodes/{ref['episodeId']}/split-candidates")
    assert resp.status_code == 400


def test_end_must_exceed_start(app_client, episode_with_transcript):
    resp = app_client.get(_url(episode_with_transcript, 190, 100))
    assert resp.status_code == 400


def test_non_numeric_start_is_rejected(app_client, episode_with_transcript):
    resp = app_client.get(_url(episode_with_transcript, 'banana', 190))
    assert resp.status_code == 400


def test_returns_a_candidate_at_the_sponsor_transition(app_client, episode_with_transcript):
    body = app_client.get(_url(episode_with_transcript, 100, 190)).get_json()
    times = [c['time'] for c in body['candidates']]
    assert 130.0 in times
    assert all('phrase' in c for c in body['candidates'])


def test_pieces_partition_the_span_at_the_candidates(app_client, episode_with_transcript):
    body = app_client.get(_url(episode_with_transcript, 100, 190)).get_json()
    pieces = body['pieces']
    assert pieces[0]['start'] == 100.0
    assert pieces[-1]['end'] == 190.0
    for a, b in zip(pieces, pieces[1:], strict=False):  # pairwise adjacent walk
        assert a['end'] == b['start']


def test_span_without_a_transition_returns_one_piece(app_client, episode_with_transcript):
    """No transition phrase is a valid answer, not an error."""
    resp = app_client.get(_url(episode_with_transcript, 100, 130))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['candidates'] == []
    assert len(body['pieces']) == 1


def test_episode_without_a_transcript_returns_empty_rather_than_500(app_client):
    db = get_database()
    slug = 'split-no-transcript'
    episode_id = 'b2c3d4e5f6a1'
    db.create_podcast(slug, 'https://example.com/nt.xml', 'No Transcript Show')
    db.upsert_episode(slug=slug, episode_id=episode_id,
                      original_url='https://example.com/nt.mp3',
                      title='Episode One', original_duration=600.0,
                      status='processed')
    try:
        resp = app_client.get(
            f'/api/v1/feeds/{slug}/episodes/{episode_id}'
            '/split-candidates?start=10&end=100')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['candidates'] == []
        assert len(body['pieces']) == 1
        assert body['pieces'][0]['text'] == ''
    finally:
        db.delete_podcast(slug)
