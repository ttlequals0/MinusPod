"""Tests for is_transient_error: download 404s must be retryable."""
import atexit
import os
import shutil
import sys
import tempfile

import pytest
import requests

# Boot pattern (see test_history_ad_count.py): bind a temp DATA_DIR before
# importing main_app, which otherwise mkdirs /app/data at module load.
_test_data_dir = tempfile.mkdtemp(prefix='error_class_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

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
