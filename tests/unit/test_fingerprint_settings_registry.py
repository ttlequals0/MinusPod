"""The audio fingerprinting settings: registry entries and one shared range table."""
from pathlib import Path

import pytest
import yaml

import audio_fingerprinter
import config
from database.settings import AD_RESET_SETTING_KEYS, SETTINGS_REGISTRY, registry_default

KEYS = [
    ('fingerprint_enabled', 'fingerprintEnabled', 'bool', 'true'),
    ('fingerprint_match_strictness', 'fingerprintMatchStrictness', 'float', '0.65'),
    ('fingerprint_min_coverage', 'fingerprintMinCoverage', 'float', '0.5'),
    ('fingerprint_gap_tolerance_s', 'fingerprintGapToleranceS', 'float', '3.5'),
    ('fingerprint_min_ad_length_s', 'fingerprintMinAdLengthS', 'float', '5.0'),
]
RANGES = {
    'fingerprint_match_strictness': (0.65, 0.95),
    'fingerprint_min_coverage': (0.10, 1.00),
    'fingerprint_gap_tolerance_s': (0.0, 15.0),
    'fingerprint_min_ad_length_s': (3.0, 30.0),
}


@pytest.mark.parametrize('key,payload_key,kind,default', KEYS)
def test_registered_with_payload_key_kind_default_and_reset(key, payload_key, kind, default):
    spec = SETTINGS_REGISTRY[key]
    assert (spec.payload_key, spec.payload_kind) == (payload_key, kind)
    assert key in AD_RESET_SETTING_KEYS
    assert registry_default(key) == default
    assert spec.resettable


def test_one_range_table_serves_the_matcher_and_the_registry():
    assert audio_fingerprinter.FINGERPRINT_SETTING_RANGES is config.FINGERPRINT_SETTING_RANGES
    assert set(config.FINGERPRINT_SETTING_RANGES) == set(RANGES)
    for key, (default, lo, hi) in config.FINGERPRINT_SETTING_RANGES.items():
        assert (lo, hi) == RANGES[key]
        assert float(registry_default(key)) == default
        assert lo <= default <= hi


def _put_body_properties():
    """Properties of the PUT settings body in openapi.yaml."""
    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / 'openapi.yaml').read_text())
    for item in spec['paths'].values():
        schema = (((item.get('put') or {}).get('requestBody') or {}).get('content', {})
                  .get('application/json', {}).get('schema', {}))
        if 'fingerprintEnabled' in schema.get('properties', {}):
            return schema['properties']
    raise AssertionError('no PUT body declares the fingerprint settings')


@pytest.mark.parametrize('key,payload_key,kind,default', KEYS)
def test_openapi_bounds_and_default_match_the_registry(key, payload_key, kind, default):
    prop = _put_body_properties()[payload_key]
    if kind == 'bool':
        assert prop['type'] == 'boolean' and prop['default'] is (default == 'true')
        return
    _, lo, hi = config.FINGERPRINT_SETTING_RANGES[key]
    assert (prop['minimum'], prop['maximum'], prop['default']) == (lo, hi, float(default))


@pytest.mark.parametrize('key,payload_key,kind,default', KEYS)
def test_openapi_get_settings_value_bounds_and_default_match_the_registry(key, payload_key, kind, default):
    spec = yaml.safe_load((Path(__file__).resolve().parents[2] / 'openapi.yaml').read_text())
    settings = spec['components']['schemas']['Settings']['properties']
    assert spec['paths']['/settings']['get']['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/Settings'}
    value = settings[payload_key]['properties']['value']
    if kind == 'bool':
        assert value['type'] == 'boolean' and value['default'] is (default == 'true')
        return
    _, lo, hi = config.FINGERPRINT_SETTING_RANGES[key]
    assert (value['minimum'], value['maximum'], value['default']) == (lo, hi, float(default))
