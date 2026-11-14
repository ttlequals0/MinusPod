"""PATCH/GET /feeds/{slug} carry the per-feed queueOrder override."""
import pytest

from tests.app_bootstrap import authenticate_test_client
from api import get_database


@pytest.fixture
def feed(app_client):
    db = get_database()
    slug = 'queue-order-feed'
    db.create_podcast(slug, 'https://example.com/queue-order.xml', title='Queue Order')
    yield slug
    db.delete_podcast(slug)


def _patch(client, slug, body):
    return client.patch(f'/api/v1/feeds/{slug}', json=body, headers=authenticate_test_client(client))


def _get(client, slug):
    return client.get(f'/api/v1/feeds/{slug}').get_json()


def test_new_feed_follows_the_global_setting(app_client, feed):
    authenticate_test_client(app_client)
    assert _get(app_client, feed)['queueOrder'] is None


@pytest.mark.parametrize('value', ['queued', 'oldest_first', 'newest_first'])
def test_save_and_read_back(app_client, feed, value):
    assert _patch(app_client, feed, {'queueOrder': value}).status_code == 200
    assert _get(app_client, feed)['queueOrder'] == value
    assert get_database().get_podcast_by_slug(feed)['queue_order'] == value


def test_null_clears_the_override(app_client, feed):
    _patch(app_client, feed, {'queueOrder': 'oldest_first'})
    assert _patch(app_client, feed, {'queueOrder': None}).status_code == 200
    assert _get(app_client, feed)['queueOrder'] is None
    assert get_database().get_podcast_by_slug(feed)['queue_order'] is None


@pytest.mark.parametrize('value', ['sideways', '', 3, True])
def test_invalid_value_is_a_400(app_client, feed, value):
    _patch(app_client, feed, {'queueOrder': 'newest_first'})
    assert _patch(app_client, feed, {'queueOrder': value}).status_code == 400
    assert _get(app_client, feed)['queueOrder'] == 'newest_first'


def test_unknown_feed_is_a_404(app_client):
    assert _patch(app_client, 'no-such-feed', {'queueOrder': 'queued'}).status_code == 404
