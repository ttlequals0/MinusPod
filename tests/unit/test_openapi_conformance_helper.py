"""The conformance helper validates real payloads against openapi.yaml."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tests.integration.openapi_conformance import (  # noqa: E402
    assert_response_conforms, response_schema,
)

EPISODE_PATH = '/feeds/{slug}/episodes/{episode_id}'


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def get_json(self):
        return self._payload

    def get_data(self, as_text=False):
        return ''


def test_response_schema_resolves_template_and_status():
    schema = response_schema('get', '/feeds', 200)
    assert schema['type'] == 'object'


def test_unknown_route_fails_loudly():
    with pytest.raises(KeyError, match='/no/such/route'):
        response_schema('get', '/no/such/route', 200)


def test_helper_resolves_nested_refs():
    # EpisodeDetail -> allOf -> adMarkers -> AdMarker: a bad nested field must surface.
    bad = {'episodeId': 'a1b2c3d4e5f6', 'title': 'x', 'status': 'completed',
           'adMarkers': [{'start': 'not-a-number'}]}
    with pytest.raises(AssertionError, match='adMarkers'):
        assert_response_conforms('get', EPISODE_PATH, _Resp(bad))


def test_helper_rejects_wrong_shape():
    with pytest.raises(AssertionError):
        assert_response_conforms('get', '/feeds', _Resp([]))


def test_helper_accepts_json_schema_null():
    # OpenAPI 3.1 nullable fields are type: [x, "null"].
    ok = {'episodeId': 'a1b2c3d4e5f6', 'processedAt': None, 'newDuration': None,
          'navigation': {'previous': None, 'next': None}}
    assert_response_conforms('get', EPISODE_PATH, _Resp(ok))
