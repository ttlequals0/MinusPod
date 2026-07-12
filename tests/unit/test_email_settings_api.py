"""API tests for the email notification settings endpoints (#491 follow-up).

Uses the main_app boot pattern from test_settings_validation: bind a temp
DATA_DIR and a master passphrase before importing main_app so the secrets
store works.
"""
import os
import sys
import tempfile
import json
from unittest.mock import patch

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='email_settings_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir
os.environ.setdefault('MINUSPOD_MASTER_PASSPHRASE', 'email-settings-test-passphrase')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app

BASE = '/api/v1/settings/notifications/email'


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _put(client, body):
    return client.put(BASE, data=json.dumps(body), content_type='application/json')


class TestGetEmailSettings:
    def test_defaults(self, client):
        response = client.get(BASE)
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['enabled'] is False
        assert data['smtpPort'] == 587
        assert data['smtpSecurity'] == 'starttls'
        assert data['smtpPasswordConfigured'] is False
        assert 'smtpPassword' not in data
        assert 'Episode Failed' in data['events']


class TestPutEmailSettings:
    def test_round_trip(self, client):
        response = _put(client, {
            'smtpHost': 'localhost',
            'smtpPort': 2525,
            'smtpSecurity': 'none',
            'fromAddress': 'minuspod@example.com',
            'recipients': 'a@example.com, b@example.com',
            'events': ['Episode Failed'],
        })
        assert response.status_code == 200, response.data
        data = json.loads(response.data)
        assert data['smtpHost'] == 'localhost'
        assert data['smtpPort'] == 2525
        assert data['recipients'] == 'a@example.com, b@example.com'
        assert data['events'] == ['Episode Failed']

    def test_password_write_only(self, client):
        response = _put(client, {'smtpPassword': 'hunter2'})
        assert response.status_code == 200, response.data
        data = json.loads(response.data)
        assert data['smtpPasswordConfigured'] is True
        assert 'hunter2' not in response.get_data(as_text=True)
        # Empty string clears it.
        response = _put(client, {'smtpPassword': ''})
        assert json.loads(response.data)['smtpPasswordConfigured'] is False

    def test_bad_port(self, client):
        assert _put(client, {'smtpPort': 0}).status_code == 400
        assert _put(client, {'smtpPort': 70000}).status_code == 400
        assert _put(client, {'smtpPort': '587'}).status_code == 400
        assert _put(client, {'smtpPort': True}).status_code == 400

    def test_bad_security(self, client):
        assert _put(client, {'smtpSecurity': 'tls13'}).status_code == 400

    def test_bad_events(self, client):
        assert _put(client, {'events': ['Nope']}).status_code == 400
        assert _put(client, {'events': 'Episode Failed'}).status_code == 400

    def test_empty_events_allowed(self, client):
        response = _put(client, {'events': []})
        assert response.status_code == 200
        assert json.loads(response.data)['events'] == []

    def test_bad_recipient(self, client):
        assert _put(client, {'recipients': 'not-an-address'}).status_code == 400
        assert _put(client, {'recipients': 'a@b.c, junk'}).status_code == 400

    def test_bad_from_address(self, client):
        assert _put(client, {'fromAddress': 'nope'}).status_code == 400

    def test_metadata_ip_host_blocked(self, client):
        response = _put(client, {'smtpHost': '169.254.169.254'})
        assert response.status_code == 400
        assert 'metadata' in json.loads(response.data)['error'].lower()

    def test_private_ip_host_allowed(self, client):
        assert _put(client, {'smtpHost': '192.168.1.25'}).status_code == 200

    def test_enable_requires_config(self, client):
        # Clear any config from earlier tests, then try to enable.
        _put(client, {'smtpHost': '', 'fromAddress': '', 'recipients': '',
                      'enabled': False})
        response = _put(client, {'enabled': True})
        assert response.status_code == 400

    def test_enable_with_config_in_same_request(self, client):
        response = _put(client, {
            'enabled': True,
            'smtpHost': 'localhost',
            'fromAddress': 'minuspod@example.com',
            'recipients': 'a@example.com',
        })
        assert response.status_code == 200
        assert json.loads(response.data)['enabled'] is True

    def test_validation_failure_stages_nothing(self, client):
        _put(client, {'smtpHost': 'localhost'})
        response = _put(client, {'smtpHost': 'other-host', 'smtpPort': 0})
        assert response.status_code == 400
        data = json.loads(client.get(BASE).data)
        assert data['smtpHost'] == 'localhost'


class TestEmailTestEndpoint:
    def test_success(self, client):
        with patch('api.settings.email_service.send_test_email',
                   return_value=(True, 'Test email sent to 1 recipient(s)')):
            response = client.post(f'{BASE}/test')
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True

    def test_failure_message(self, client):
        with patch('api.settings.email_service.send_test_email',
                   return_value=(False, 'Sending failed: refused')):
            response = client.post(f'{BASE}/test')
        data = json.loads(response.data)
        assert data['success'] is False
        assert 'refused' in data['message']
