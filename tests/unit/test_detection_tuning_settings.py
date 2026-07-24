"""API validation tests for the six detection-tuning settings (2.76.0)."""
import json

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('det_tuning_test_')
from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


class TestDetectionTuningSettings:
    def _put(self, client, payload):
        return client.put(
            '/api/v1/settings/ad-detection',
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_defaults_in_get(self, client):
        r = client.get('/api/v1/settings')
        s = r.get_json()
        assert s['verificationMissHoldMinConfidence']['value'] == 0.6
        assert s['verificationMissAutocutMinConfidence']['value'] == 0.0
        assert s['learningMinConfidence']['value'] == 0.85
        assert s['learningMinConfidenceLong']['value'] == 0.92
        assert s['differentialMeasuredCorrMax']['value'] == 0.6
        assert s['differentialHoldMinSeconds']['value'] == 10.0

    def test_put_roundtrip(self, client):
        r = self._put(client, {'verificationMissHoldMinConfidence': 0.7,
                               'differentialHoldMinSeconds': 20})
        assert r.status_code == 200, r.data
        s = client.get('/api/v1/settings').get_json()
        assert s['verificationMissHoldMinConfidence']['value'] == 0.7
        assert s['differentialHoldMinSeconds']['value'] == 20.0

    def test_autocut_zero_disables_and_midrange_rejected(self, client):
        assert self._put(client, {'verificationMissAutocutMinConfidence': 0}).status_code == 200
        assert self._put(client, {'verificationMissAutocutMinConfidence': 0.9}).status_code == 200
        assert self._put(client, {'verificationMissAutocutMinConfidence': 0.3}).status_code == 400

    def test_out_of_range_rejected(self, client):
        assert self._put(client, {'learningMinConfidence': 1.5}).status_code == 400
        assert self._put(client, {'differentialHoldMinSeconds': 500}).status_code == 400
