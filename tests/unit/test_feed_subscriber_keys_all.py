"""GET /feeds/subscriber-keys lists every subscriber key across feeds."""
from tests.app_bootstrap import bootstrap

bootstrap('subscriber_keys_all_test_')


def test_lists_keys_across_feeds_sorted_by_title_label_created(app_client, temp_db):
    temp_db.create_podcast('zeta', 'https://example.com/z.xml', 'Zeta Show')
    temp_db.create_podcast('alpha', 'https://example.com/a.xml', 'alpha show')
    temp_db.create_podcast('mid', 'https://example.com/m.xml', 'Source Title')
    temp_db.update_podcast('mid', title_override='Bravo Show')
    temp_db.create_feed_subscriber_key('zeta', 'Phone')
    temp_db.create_feed_subscriber_key('alpha', 'Tablet')
    temp_db.create_feed_subscriber_key('alpha', 'Car')
    temp_db.create_feed_subscriber_key('mid', 'Den')

    response = app_client.get('/api/v1/feeds/subscriber-keys')

    assert response.status_code == 200
    mine = {'zeta', 'alpha', 'mid'}
    keys = [k for k in response.get_json()['keys'] if k['feedSlug'] in mine]
    assert [(k['feedTitle'], k['label']) for k in keys] == [
        ('alpha show', 'Car'), ('alpha show', 'Tablet'),
        ('Bravo Show', 'Den'), ('Zeta Show', 'Phone'),
    ]
    assert keys[0]['feedSlug'] == 'alpha'
    assert set(keys[0]) == {
        'id', 'label', 'createdAt', 'lastUsedAt', 'revokedAt', 'feedSlug', 'feedTitle',
    }


def test_empty_when_no_keys(app_client, temp_db):
    conn = temp_db.get_connection()
    conn.execute('DELETE FROM feed_subscriber_keys')
    conn.commit()
    assert app_client.get('/api/v1/feeds/subscriber-keys').get_json() == {'keys': []}
