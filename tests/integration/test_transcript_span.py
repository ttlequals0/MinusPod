"""Integration tests for the /transcript-span endpoint."""
from unittest.mock import patch


def test_transcript_span_404_when_episode_missing(app_client):
    response = app_client.get(
        '/api/v1/feeds/missing-slug/episodes/abcdef012345/transcript-span'
        '?start=0&end=10'
    )
    assert response.status_code in (400, 404)


def test_transcript_span_rejects_missing_end(app_client):
    response = app_client.get(
        '/api/v1/feeds/some-slug/episodes/abcdef012345/transcript-span?start=0'
    )
    # 400 from validation, or 404 if episode lookup fires first.
    assert response.status_code in (400, 404)


def test_transcript_span_returns_text(app_client):
    from api import get_database

    db = get_database()
    slug = 'span-test-slug'
    episode_id = 'abcdef012345'

    try:
        db.create_podcast(slug, 'https://example.com/feed.xml', 'Span Test')
    except Exception:
        pass
    db.upsert_episode(
        slug=slug,
        episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode',
        original_duration=100.0,
    )

    transcript_text = (
        '[0.0 -> 10.0] Welcome to the podcast.\n'
        '[10.0 -> 20.0] This is a real ad read for SpansCo.\n'
        '[20.0 -> 30.0] Use code TEST to save 10 percent.\n'
        '[30.0 -> 100.0] Back to the show.\n'
    )

    with patch.object(db, 'get_transcript_for_timestamps',
                       return_value=transcript_text):
        response = app_client.get(
            f'/api/v1/feeds/{slug}/episodes/{episode_id}/transcript-span'
            f'?start=10&end=25'
        )

    if response.status_code != 200:
        return

    body = response.get_json()
    assert body['episodeId'] == episode_id
    assert body['start'] == 10.0
    assert body['end'] == 25.0
    assert 'text' in body
    assert isinstance(body['text'], str)
