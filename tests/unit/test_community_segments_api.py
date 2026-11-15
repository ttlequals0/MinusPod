"""API routes of show-bound community segments (fake-show fixtures); no response leaks hashes or buckets."""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('community_segments_api_test_')

import community_segments as seg  # noqa: E402
from main_app import app  # noqa: E402
from tests.unit.test_community_segments import (GUID, SLUG, _bound, _entry, _exportable, _import,  # noqa: E402
                                                _podcast)


@pytest.fixture
def client(db):
    app.config['TESTING'] = True
    with patch('api.community_segments.get_database', return_value=db), app.test_client() as c:
        yield c


@pytest.fixture
def db(temp_db):
    temp_db.create_podcast(SLUG, 'https://feeds.kettle.example/hour.xml', 'Copper Kettle Hour')
    temp_db.set_setting('community_show_segments', 'true')
    temp_db.set_setting('community_sync_enabled', 'true')
    return temp_db


def _no_hashes(payload, entry):
    text = json.dumps(payload)
    assert not any(h in text for h in entry['core'])
    assert '"bucket"' not in text and 'core' not in text


def test_feed_list_and_summary(client, db):
    body = _entry()
    _bound(db, body)
    out = client.get(f'/api/v1/community-segments?feed={SLUG}').get_json()
    assert [e['state'] for e in out['entries']] == ['candidate']
    assert out['entries'][0]['category'] == 'interaction' and out['entries'][0]['counts']['bind'] == 3
    _no_hashes(out, body)
    summary = client.get('/api/v1/community-segments/summary').get_json()
    assert summary['enabled'] is True and summary['counts'] == {'candidate': 1}
    _no_hashes(summary, body)
    assert client.get('/api/v1/community-segments?feed=nope').status_code == 404


def test_switch_round_trip_and_validation(client, db):
    r = client.put('/api/v1/community-segments/settings', json={'enabled': False})
    assert r.status_code == 200 and r.get_json()['enabled'] is False
    assert db.get_setting('community_show_segments') == 'false'
    assert client.put('/api/v1/community-segments/settings', json={'enabled': 'yes'}).status_code == 400


def test_suspend_reactivate_protect_and_delete(client, db):
    body = _entry()
    entry = _bound(db, body)
    base = f"/api/v1/community-segments/{entry['id']}"
    assert client.post(base + '/suspend').get_json()['state'] == 'suspended'
    assert client.post(base + '/reactivate').get_json()['state'] == 'candidate'
    assert client.post(base + '/protect').get_json()['protected'] is True
    _no_hashes(client.post(base + '/unprotect').get_json(), body)
    assert client.post(base + '/explode').status_code == 404
    assert client.delete(base).get_json() == {'deleted': 1}
    assert client.delete(base).status_code == 404


def test_remove_all(client, db):
    _import(db, _entry(), _entry('self_promo'))
    assert client.delete('/api/v1/community-segments').get_json() == {'deleted': 2}
    assert seg.get_entries(db) == []


def test_export_returns_files_and_reasons(client, db):
    pid = _exportable(db)
    r = client.post('/api/v1/community-segments/export', json={'patternIds': [pid, 999999]})
    out = r.get_json()
    assert r.status_code == 200
    assert out['rejected'] == [{'id': 999999, 'reasons': ['pattern not found']}]
    assert out['files'][0]['filename'].startswith('seg-') and out['files'][0]['entry']['kind'] == 'show_segment'
    assert out['bundle']['patterns'][0]['bucket'] == seg.core.bucket_for(GUID)
    assert client.post('/api/v1/community-segments/export', json={'patternIds': []}).status_code == 400
    assert _podcast(db)['slug'] not in json.dumps(out)
