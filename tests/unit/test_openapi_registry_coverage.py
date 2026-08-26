"""Every payload key exposed by SETTINGS_REGISTRY must be documented in
openapi.yaml, so the API spec never silently drifts from the settings
actually returned by GET /api/v1/settings."""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database.settings import SETTINGS_REGISTRY

OPENAPI_PATH = Path(__file__).resolve().parents[2] / 'openapi.yaml'


def test_all_registry_payload_keys_appear_in_openapi():
    openapi_text = OPENAPI_PATH.read_text()
    payload_keys = sorted(
        spec.payload_key for spec in SETTINGS_REGISTRY.values()
        if spec.payload_key is not None
    )
    missing = [key for key in payload_keys if key not in openapi_text]
    assert not missing, f"payload_key(s) missing from openapi.yaml: {missing}"
