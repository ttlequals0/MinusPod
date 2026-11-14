"""The status service singleton must not be built before a test data dir is bootstrapped."""
import os

from utils.paths import DEFAULT_DATA_DIR


def test_status_service_file_is_not_in_the_default_data_dir(app_client):
    from api import get_status_service
    assert os.path.dirname(get_status_service().status_file) != DEFAULT_DATA_DIR
