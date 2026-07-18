"""Tests for is_transient_error: download 404s must be retryable."""

import pytest
import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('error_class_test_')
from main_app.processing import is_transient_error


@pytest.mark.parametrize("error, transient", [
    # Download 404: freshly published episodes 404 while the host provisions
    # the media URL, so retry. The retry cap still fails a genuinely dead link.
    (requests.exceptions.HTTPError(
        '404 Client Error: Not Found for url: https://cdn.example/media.mp3'), True),
    # Auth/request errors stay permanent: a retry sends the same bad request.
    (requests.exceptions.HTTPError('403 Client Error: Forbidden for url: x'), False),
    (requests.exceptions.HTTPError('401 Client Error: Unauthorized'), False),
    (Exception('Invalid audio: unsupported format'), False),
    # Network faults and unknown errors retry.
    (requests.exceptions.ConnectionError('reset'), True),
    (Exception('something weird happened'), True),
])
def test_is_transient_error_classification(error, transient):
    assert is_transient_error(error) is transient
