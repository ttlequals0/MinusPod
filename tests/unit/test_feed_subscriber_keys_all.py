"""GET /feeds/subscriber-keys lists every subscriber key across feeds."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('subscriber_keys_all_test_')

SLUGS = ('zeta', 'alpha', 'mid', 'keyless')


@pytest.fixture
def feeds(temp_db):
    temp_db.create_podcast('zeta', 'https://example.com/z.xml', 'Zeta Show')
    temp_db.create_podcast('alpha', 'https://example.com/a.xml', 'alpha show')
    temp_db.create_podcast('mid', 'https://example.com/m.xml', 'Source Title')
    temp_db.create_podcast('keyless', 'https://example.com/k.xml', 'Keyless Show')
    temp_db.update_podcast('mid', title_override='Bravo Show')
    yield temp_db
    for slug in SLUGS:
        temp_db.delete_podcast(slug)


def _mine(response):
    return [k for k in response.get_json()['keys'] if k['feedSlug'] in SLUGS]


def test_lists_keys_across_feeds_sorted_by_title_label_created(app_client, feeds):
    feeds.create_feed_subscriber_key('zeta', 'Phone')
    feeds.create_feed_subscriber_key('alpha', 'Tablet')
    feeds.create_feed_subscriber_key('alpha', 'Car')
    feeds.create_feed_subscriber_key('mid', 'Den')

    response = app_client.get('/api/v1/feeds/subscriber-keys')

    assert response.status_code == 200
    keys = _mine(response)
    assert [(k['feedTitle'], k['label']) for k in keys] == [
        ('alpha show', 'Car'), ('alpha show', 'Tablet'),
        ('Bravo Show', 'Den'), ('Zeta Show', 'Phone'),
    ]
    assert keys[0]['feedSlug'] == 'alpha'
    assert set(keys[0]) == {
        'id', 'label', 'createdAt', 'lastUsedAt', 'revokedAt', 'feedSlug', 'feedTitle',
    }


def test_null_feed_title_falls_back_to_slug(app_client, feeds):
    feeds.create_feed_subscriber_key('keyless', 'Den')
    with feeds.transaction(immediate=True) as conn:
        conn.execute("UPDATE podcasts SET title = NULL WHERE slug = 'keyless'")

    keys = _mine(app_client.get('/api/v1/feeds/subscriber-keys'))

    assert [k['feedTitle'] for k in keys if k['feedSlug'] == 'keyless'] == ['keyless']


def test_feed_without_keys_is_absent(app_client, feeds):
    feeds.create_feed_subscriber_key('alpha', 'Car')

    keys = _mine(app_client.get('/api/v1/feeds/subscriber-keys'))

    assert [k['feedSlug'] for k in keys] == ['alpha']
