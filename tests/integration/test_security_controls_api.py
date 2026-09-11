from tests.app_bootstrap import bootstrap


bootstrap('security_controls_test_')


def test_subscriber_key_api_reveals_once_and_revokes(app_client, temp_db):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')

    created = app_client.post(
        '/api/v1/feeds/example-feed/subscriber-keys', json={'label': 'Phone'}
    )
    assert created.status_code == 201
    body = created.get_json()
    assert body['token'] in body['feedUrl']

    listed = app_client.get('/api/v1/feeds/example-feed/subscriber-keys')
    assert listed.status_code == 200
    assert listed.get_json()['keys'][0]['label'] == 'Phone'
    assert 'token' not in listed.get_json()['keys'][0]

    revoked = app_client.delete(
        f"/api/v1/feeds/example-feed/subscriber-keys/{body['id']}"
    )
    assert revoked.status_code == 200
    assert not temp_db.verify_feed_subscriber_key('example-feed', body['token'])


def test_subscriber_key_api_deletes_only_revoked_records(app_client, temp_db):
    temp_db.create_podcast('example-feed', 'https://example.com/feed.xml', 'Example')
    created = app_client.post(
        '/api/v1/feeds/example-feed/subscriber-keys', json={'label': 'Phone'}
    ).get_json()
    record_url = f"/api/v1/feeds/example-feed/subscriber-keys/{created['id']}/record"

    assert app_client.delete(record_url).status_code == 409
    assert temp_db.verify_feed_subscriber_key('example-feed', created['token'])
    assert app_client.delete(
        f"/api/v1/feeds/example-feed/subscriber-keys/{created['id']}"
    ).status_code == 200
    assert not temp_db.verify_feed_subscriber_key('example-feed', created['token'])
    assert app_client.delete(record_url).get_json() == {'deleted': True}
    assert not temp_db.verify_feed_subscriber_key('example-feed', created['token'])


def test_provider_budget_api_validates_and_updates_atomically(app_client, temp_db):
    response = app_client.put('/api/v1/settings/provider-budget', json={
        'enabled': True,
        'dailyLimitMicrousd': 2_500_000,
        'maxReservations': 2,
        'unknownCost': 'reserve',
        'unknownReserveMicrousd': 250_000,
    })
    assert response.status_code == 200
    assert response.get_json()['dailyLimitMicrousd'] == 2_500_000
    assert temp_db.get_setting('provider_budget_unknown_cost') == 'reserve'


def test_provider_budget_rejects_zero_unknown_reservation(app_client):
    response = app_client.put('/api/v1/settings/provider-budget', json={
        'enabled': True,
        'dailyLimitMicrousd': 2_500_000,
        'maxReservations': 2,
        'unknownCost': 'reserve',
        'unknownReserveMicrousd': 0,
    })
    assert response.status_code == 400
