"""Audio fingerprinting settings: validation, GET, reset, export, import and the provider none case."""
import json

import pytest

import config
import config_transfer
from tests.integration.fingerprint_helpers import csrf  # noqa: E402

KEYS = {
    'fingerprint_match_strictness': 'fingerprintMatchStrictness',
    'fingerprint_min_coverage': 'fingerprintMinCoverage',
    'fingerprint_gap_tolerance_s': 'fingerprintGapToleranceS',
    'fingerprint_min_ad_length_s': 'fingerprintMinAdLengthS',
}
ALL_PAYLOAD_KEYS = ['fingerprintEnabled', *KEYS.values()]
URL = '/api/v1/settings/ad-detection'


def _put(client, body):
    return client.put(URL, json=body, headers=csrf(client))


def _put_raw(client, raw):
    return client.put(URL, data=raw, headers={**csrf(client), 'Content-Type': 'application/json'})


def _get(client):
    return client.get('/api/v1/settings', headers=csrf(client)).get_json()


@pytest.fixture(autouse=True)
def reset_settings(app_client):
    yield
    app_client.post(f'{URL}/reset', headers=csrf(app_client))


def test_defaults_are_returned_with_is_default(app_client):
    body = _get(app_client)
    assert (body['fingerprintEnabled']['value'], body['fingerprintEnabled']['isDefault']) == (True, True)
    assert body['fingerprintMatchStrictness']['value'] == pytest.approx(0.65)
    assert body['fingerprintMinCoverage']['value'] == pytest.approx(0.5)
    assert body['fingerprintGapToleranceS']['value'] == pytest.approx(3.5)
    assert body['fingerprintMinAdLengthS']['value'] == pytest.approx(5.0)
    assert all(body[k]['isDefault'] for k in ALL_PAYLOAD_KEYS)
    assert body['defaults']['fingerprintEnabled'] is True
    assert body['defaults']['fingerprintMinAdLengthS'] == pytest.approx(5.0)


def test_put_and_get_roundtrip(app_client):
    r = _put(app_client, {'fingerprintEnabled': False, 'fingerprintMatchStrictness': 0.7,
                          'fingerprintMinCoverage': 0.7, 'fingerprintGapToleranceS': 9,
                          'fingerprintMinAdLengthS': 12})
    assert r.status_code == 200
    body = _get(app_client)
    assert (body['fingerprintEnabled']['value'], body['fingerprintEnabled']['isDefault']) == (False, False)
    assert body['fingerprintMatchStrictness']['value'] == pytest.approx(0.7)
    assert body['fingerprintMinCoverage']['value'] == pytest.approx(0.7)
    assert body['fingerprintMinCoverage']['isDefault'] is False
    assert body['fingerprintGapToleranceS']['value'] == pytest.approx(9.0)
    assert body['fingerprintMinAdLengthS']['value'] == pytest.approx(12.0)


@pytest.mark.parametrize('key', KEYS)
def test_range_ends_are_accepted_and_just_outside_is_400_naming_field_and_range(app_client, key):
    field = KEYS[key]
    _default, lo, hi = config.FINGERPRINT_SETTING_RANGES[key]
    for ok in (lo, hi):
        assert _put(app_client, {field: ok}).status_code == 200
    for bad in (lo - 0.01, hi + 0.01):
        r = _put(app_client, {field: bad})
        message = r.get_json()['error']
        assert r.status_code == 400
        assert field in message and f'{lo:g}' in message and f'{hi:g}' in message
    assert _get(app_client)[field]['value'] == pytest.approx(hi)


@pytest.mark.parametrize('field', list(KEYS.values()))
@pytest.mark.parametrize('bad', ['abc', '0.7', '', None, True, False, [1], {'a': 1}, 10 ** 400])
def test_non_numbers_are_400(app_client, field, bad):
    r = _put(app_client, {field: bad})
    assert r.status_code == 400 and field in r.get_json()['error']
    assert _get(app_client)[field]['isDefault'] is True


@pytest.mark.parametrize('field', list(KEYS.values()))
@pytest.mark.parametrize('raw', ['NaN', 'Infinity', '-Infinity', '1e999'])
def test_non_finite_numbers_are_400(app_client, field, raw):
    r = _put_raw(app_client, '{"%s": %s}' % (field, raw))
    assert r.status_code == 400 and field in r.get_json()['error']


@pytest.mark.parametrize('bad', ['true', 'false', 1, 0, None, 'yes', [True]])
def test_enabled_must_be_a_boolean(app_client, bad):
    r = _put(app_client, {'fingerprintEnabled': bad})
    assert r.status_code == 400 and 'fingerprintEnabled' in r.get_json()['error']
    assert _get(app_client)['fingerprintEnabled']['isDefault'] is True


def test_a_rejected_field_saves_none_of_the_payload(app_client):
    r = _put(app_client, {'fingerprintMinCoverage': 0.7, 'fingerprintGapToleranceS': 99})
    assert r.status_code == 400
    assert _get(app_client)['fingerprintMinCoverage']['isDefault'] is True


def test_reset_restores_every_default(app_client):
    _put(app_client, {'fingerprintEnabled': False, 'fingerprintMatchStrictness': 0.9,
                      'fingerprintMinCoverage': 0.9, 'fingerprintGapToleranceS': 9.0,
                      'fingerprintMinAdLengthS': 20})
    assert app_client.post(f'{URL}/reset', headers=csrf(app_client)).status_code == 200
    body = _get(app_client)
    assert all(body[k]['isDefault'] for k in ALL_PAYLOAD_KEYS)
    assert body['fingerprintGapToleranceS']['value'] == pytest.approx(3.5)
    assert body['fingerprintEnabled']['value'] is True


def test_config_export_carries_the_five_keys(app_client):
    from api import get_database
    db = get_database()
    db.set_setting('fingerprint_min_coverage', '0.7', is_default=False)
    exported = config_transfer.export_config(db, '0.0.0-source')['settings']
    assert set(exported) >= {'fingerprint_enabled', *KEYS}
    assert exported['fingerprint_min_coverage'] == pytest.approx(0.7)
    assert exported['fingerprint_enabled'] is True
    assert json.dumps(exported)


def test_config_import_applies_and_validates_the_keys(app_client, monkeypatch):
    from api import get_database
    db = get_database()
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_a: [])
    document = config_transfer.export_config(db, '0.0.0-source')
    document['settings'].update({'fingerprint_min_coverage': 0.8, 'fingerprint_enabled': False})
    preview = config_transfer.build_preview(db, document, 'global')
    config_transfer.apply_config(db, document, 'global', None, preview['previewToken'])
    assert db.get_setting('fingerprint_min_coverage') == '0.8'
    assert db.get_setting('fingerprint_enabled') == 'false'
    bad = config_transfer.export_config(db, '0.0.0-source')
    bad['settings']['fingerprint_gap_tolerance_s'] = 99
    with pytest.raises(config_transfer.ConfigTransferError, match='fingerprintGapToleranceS'):
        config_transfer.build_preview(db, bad, 'global')


def test_settings_work_when_the_llm_provider_is_none(app_client):
    assert _put(app_client, {'llmProvider': 'none'}).status_code == 200
    try:
        body = _get(app_client)
        assert body['llmEnabled'] is False
        assert all(k in body for k in ALL_PAYLOAD_KEYS)
        assert _put(app_client, {'fingerprintMinCoverage': 0.6, 'fingerprintEnabled': False}).status_code == 200
        body = _get(app_client)
        assert body['fingerprintMinCoverage']['value'] == pytest.approx(0.6)
        assert body['fingerprintEnabled']['value'] is False
        assert app_client.post(f'{URL}/reset', headers=csrf(app_client)).status_code == 200
        assert _get(app_client)['fingerprintMinCoverage']['isDefault'] is True
    finally:
        _put(app_client, {'llmProvider': 'anthropic'})


def test_matcher_registry_and_api_ranges_agree(app_client, temp_db):
    from audio_fingerprinter import clamped_fingerprint_setting
    from database.settings import registry_default
    for key, field in KEYS.items():
        default, lo, hi = config.FINGERPRINT_SETTING_RANGES[key]
        assert float(registry_default(key)) == default
        for value, expect_ok in ((lo, True), (hi, True), (lo - 0.5, False), (hi + 0.5, False)):
            assert (_put(app_client, {field: value}).status_code == 200) is expect_ok
            temp_db.set_setting(key, str(value))
            assert clamped_fingerprint_setting(temp_db, key) == min(max(value, lo), hi)


def test_get_reports_the_limits_beside_each_value(app_client):
    body = _get(app_client)
    expected = {
        'fingerprintMatchStrictness': (0.65, 0.95, 0.05),
        'fingerprintMinCoverage': (0.1, 1.0, 0.05),
        'fingerprintGapToleranceS': (0.0, 15.0, 0.5),
        'fingerprintMinAdLengthS': (3.0, 30.0, 1.0),
    }
    for field, (lo, hi, step) in expected.items():
        assert (body[field]['min'], body[field]['max'], body[field]['step']) == (lo, hi, step)
    assert not {'min', 'max', 'options'} & set(body['fingerprintEnabled'])
