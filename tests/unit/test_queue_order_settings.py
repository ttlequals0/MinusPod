"""The global queueOrder setting: default, save, reset, validation, export and import."""
import json

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('queue_order_settings_test_', passphrase='queue-order-settings-test-pass')

import config_transfer  # noqa: E402
from config_transfer import ConfigTransferError  # noqa: E402
from database.settings import AD_RESET_SETTING_KEYS, SETTINGS_REGISTRY  # noqa: E402
from main_app import app  # noqa: E402

BASE = '/api/v1/settings'


@pytest.fixture
def client(preserve_setting):
    preserve_setting('queue_order')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _put(client, value):
    return client.put(f'{BASE}/ad-detection', data=json.dumps({'queueOrder': value}),
                      content_type='application/json')


def test_registry_entry():
    spec = SETTINGS_REGISTRY['queue_order']
    assert spec.default == 'queued' and spec.seeded and spec.payload_key == 'queueOrder'
    assert 'queue_order' in AD_RESET_SETTING_KEYS


def test_default_is_queued(client):
    body = client.get(BASE).get_json()['queueOrder']
    assert body['value'] == 'queued'


@pytest.mark.parametrize('value', ['oldest_first', 'newest_first', 'queued'])
def test_save_round_trips(client, value):
    assert _put(client, value).status_code == 200
    body = client.get(BASE).get_json()['queueOrder']
    assert body['value'] == value and body['isDefault'] is False


@pytest.mark.parametrize('value', ['sideways', '', None, 3, ['queued']])
def test_invalid_value_is_a_400_and_changes_nothing(client, value):
    _put(client, 'oldest_first')
    assert _put(client, value).status_code == 400
    assert client.get(BASE).get_json()['queueOrder']['value'] == 'oldest_first'


def test_bulk_reset_restores_the_default(client):
    _put(client, 'newest_first')
    assert client.post(f'{BASE}/ad-detection/reset').status_code == 200
    body = client.get(BASE).get_json()['queueOrder']
    assert body['value'] == 'queued' and body['isDefault'] is True


def test_export_carries_the_setting_and_import_validates_it(temp_db):
    temp_db.set_setting('queue_order', 'newest_first', is_default=False)
    document = config_transfer.export_config(temp_db, '0.0.0')
    assert document['settings']['queue_order'] == 'newest_first'
    document['settings']['queue_order'] = 'sideways'
    with pytest.raises(ConfigTransferError):
        config_transfer._validate_document(document)
    document['settings']['queue_order'] = 'oldest_first'
    config_transfer._validate_document(document)


def test_get_reports_the_options_beside_the_value(client):
    body = client.get(BASE).get_json()['queueOrder']
    assert body['options'] == ['queued', 'oldest_first', 'newest_first']
    assert 'min' not in body
