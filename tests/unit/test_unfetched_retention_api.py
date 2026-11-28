"""API and serve-path tests for unfetched-episode retention."""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'unfetched_retention_api_test_', passphrase='unfetched-retention-test-passphrase')

from main_app import app, db as main_db


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _restore(preserve_setting):
    for key in ('retention_days', 'original_retention_days',
                'unfetched_retention_days', 'fetched_retention_grace_days'):
        preserve_setting(key)


def _put(client, **body):
    return client.put('/api/v1/settings/retention', data=json.dumps(
        {'retentionDays': 30, **body}), content_type='application/json')


class TestRetentionApi:
    def test_get_defaults(self, client):
        main_db.clear_setting('unfetched_retention_days')
        main_db.clear_setting('fetched_retention_grace_days')
        data = json.loads(client.get('/api/v1/settings/retention').data)
        assert data['unfetchedRetentionDays'] == 0
        assert data['fetchedRetentionGraceDays'] == 3

    def test_put_round_trip(self, client):
        resp = _put(client, unfetchedRetentionDays=90, fetchedRetentionGraceDays=5)
        assert resp.status_code == 200, resp.data
        assert json.loads(resp.data)['unfetchedRetentionDays'] == 90
        data = json.loads(client.get('/api/v1/settings/retention').data)
        assert data['unfetchedRetentionDays'] == 90
        assert data['fetchedRetentionGraceDays'] == 5

    def test_put_omitted_fields_keep_values(self, client):
        _put(client, unfetchedRetentionDays=60)
        _put(client)
        data = json.loads(client.get('/api/v1/settings/retention').data)
        assert data['unfetchedRetentionDays'] == 60

    @pytest.mark.parametrize('body', [
        {'unfetchedRetentionDays': -1}, {'unfetchedRetentionDays': 3651},
        {'unfetchedRetentionDays': True}, {'fetchedRetentionGraceDays': 366},
        {'fetchedRetentionGraceDays': 'x'},
    ])
    def test_put_rejects_invalid_without_partial_write(self, client, body):
        main_db.set_setting('retention_days', '45', is_default=False)
        resp = _put(client, **body)
        assert resp.status_code == 400
        assert next(iter(body)) in json.loads(resp.data)['error']
        assert main_db.get_setting('retention_days') == '45'


class TestServeStampsFirstFetch:
    SLUG = 'unfetched-serve'
    EP = 'a1b2c3d4e5f7'

    @pytest.fixture
    def episode(self, tmp_path):
        main_db.create_podcast(self.SLUG, 'https://example.com/f.xml', 'Serve')
        main_db.upsert_episode(self.SLUG, self.EP, original_url='https://example.com/a.mp3',
                               title='Ep', status='processed')
        audio = tmp_path / 'ep.mp3'
        audio.write_bytes(b'ID3' + b'\0' * 64)
        yield audio
        main_db.delete_podcast(self.SLUG)

    def _get(self, client, audio, method='GET'):
        feed_map = {self.SLUG: {'in': 'https://example.com/f.xml', 'out': self.SLUG}}
        with patch('main_app.routes.get_feed_map', return_value=feed_map), \
                patch('main_app.routes._published_processed_path', return_value=audio):
            return client.open(f'/episodes/{self.SLUG}/{self.EP}.mp3', method=method)

    def test_head_does_not_stamp(self, client, episode):
        assert self._get(client, episode, method='HEAD').status_code == 200
        assert main_db.get_episode(self.SLUG, self.EP)['first_fetched_at'] is None

    def test_get_stamps_once(self, client, episode):
        assert self._get(client, episode).status_code == 200
        first = main_db.get_episode(self.SLUG, self.EP)['first_fetched_at']
        assert first is not None
        with patch.object(main_db, 'mark_episode_fetched') as mark:
            self._get(client, episode)
        mark.assert_not_called()


class TestConfigTransferValidation:
    @pytest.mark.parametrize('key,value', [
        ('unfetched_retention_days', 3651), ('unfetched_retention_days', '90'),
        ('fetched_retention_grace_days', 366), ('fetched_retention_grace_days', True),
    ])
    def test_rejects_out_of_range(self, key, value):
        from config_transfer import ConfigTransferError, _validate_setting_value
        with pytest.raises(ConfigTransferError):
            _validate_setting_value(key, value)

    @pytest.mark.parametrize('key,value', [
        ('unfetched_retention_days', 0), ('unfetched_retention_days', 3650),
        ('fetched_retention_grace_days', 365),
    ])
    def test_accepts_range(self, key, value):
        from config_transfer import _validate_setting_value
        _validate_setting_value(key, value)
