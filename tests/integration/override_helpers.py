"""Helper shared by the per-feed override endpoint tests (fixtures live in conftest.py)."""
from tests.app_bootstrap import authenticate_test_client


def _csrf(app_client):
    return authenticate_test_client(app_client)
