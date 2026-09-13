from tests.app_bootstrap import bootstrap
from decimal import Decimal
from unittest.mock import patch

from fx_rates import FxRate, FxRateError


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


def test_provider_budget_converts_local_amounts_and_records_rate(app_client, temp_db):
    rate = FxRate(currency='EUR', local_per_usd=Decimal('0.8'), source='Frankfurter', source_date='2026-09-10')
    with patch('api.settings.get_usd_rate', return_value=rate):
        response = app_client.put('/api/v1/settings/provider-budget', json={
            'enabled': True,
            'displayCurrency': 'eur',
            'dailyLimit': '1.00',
            'maxReservations': 2,
            'unknownCost': 'reserve',
            'unknownReserve': '0.01',
        })

    assert response.status_code == 200
    body = response.get_json()
    assert body['dailyLimitMicrousd'] == 1_250_000
    assert body['unknownReserveMicrousd'] == 12_500
    assert body['displayCurrency'] == 'EUR'
    assert body['fxRate']['localPerUsd'] == '0.8'
    assert temp_db.get_setting('provider_budget_fx_source_date') == '2026-09-10'


def test_provider_budget_rate_failure_preserves_existing_values(app_client, temp_db):
    temp_db.set_setting('provider_budget_daily_limit_microusd', '2500000')
    with patch('api.settings.get_usd_rate', side_effect=FxRateError('Currency rate is unavailable or too old')):
        response = app_client.put('/api/v1/settings/provider-budget', json={
            'enabled': True,
            'displayCurrency': 'EUR',
            'dailyLimit': '1',
            'maxReservations': 2,
            'unknownCost': 'deny',
            'unknownReserve': '0',
        })

    assert response.status_code == 503
    assert temp_db.get_setting('provider_budget_daily_limit_microusd') == '2500000'


def test_provider_budget_legacy_save_keeps_non_usd_snapshot_offline(app_client, temp_db):
    temp_db.set_setting('provider_budget_daily_limit_microusd', '2500000')
    temp_db.set_setting('provider_budget_display_currency', 'EUR')
    temp_db.set_setting('provider_budget_fx_rate', '0.8')
    temp_db.set_setting('provider_budget_fx_source_date', '2026-09-10')
    with patch('api.settings.get_usd_rate', side_effect=AssertionError('must not fetch')):
        response = app_client.put('/api/v1/settings/provider-budget', json={
            'enabled': False,
            'dailyLimitMicrousd': 2_500_000,
            'maxReservations': 2,
            'unknownCost': 'deny',
            'unknownReserveMicrousd': 0,
        })

    assert response.status_code == 200
    assert temp_db.get_setting('provider_budget_daily_limit_microusd') == '2500000'
    assert temp_db.get_setting('provider_budget_display_currency') == 'EUR'
    assert temp_db.get_setting('provider_budget_fx_rate') == '0.8'


def test_provider_budget_rejects_tiny_positive_local_limit(app_client):
    rate = FxRate(currency='EUR', local_per_usd=Decimal('10000000'), source='Frankfurter', source_date='2026-09-10')
    with patch('api.settings.get_usd_rate', return_value=rate):
        response = app_client.put('/api/v1/settings/provider-budget', json={
            'enabled': True,
            'displayCurrency': 'EUR',
            'dailyLimit': '0.00000001',
            'maxReservations': 1,
            'unknownCost': 'deny',
            'unknownReserve': '0',
        })

    assert response.status_code == 400


def test_provider_budget_rate_preview_rejects_extreme_exponents(app_client):
    rate = FxRate(currency='EUR', local_per_usd=Decimal('0.8'), source='Frankfurter', source_date='2026-09-10')
    with patch('api.settings.get_usd_rate', return_value=rate):
        response = app_client.get(
            '/api/v1/settings/provider-budget/rate/EUR'
            '?from=USD&fromRate=1e-999999&dailyLimit=1&unknownReserve=0'
        )

    assert response.status_code == 400


def test_provider_budget_rate_preview_converts_unsaved_amounts(app_client):
    rate = FxRate(currency='EUR', local_per_usd=Decimal('0.8'), source='Frankfurter', source_date='2026-09-10')
    with patch('api.settings.get_usd_rate', return_value=rate):
        response = app_client.get(
            '/api/v1/settings/provider-budget/rate/EUR'
            '?from=USD&fromRate=1&dailyLimit=2.5&unknownReserve=0.1'
        )

    assert response.status_code == 200
    assert response.get_json()['dailyLimit'] == '2.00'
    assert response.get_json()['unknownReserve'] == '0.08'
