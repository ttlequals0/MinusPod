"""_apply_ad_merge_fields must reject non-finite values (NaN/inf) with 400."""
import os
import tempfile
from unittest.mock import MagicMock

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='ad_merge_fields_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from api.settings import _apply_ad_merge_fields


def _call(value):
    from main_app import app
    db = MagicMock()
    with app.test_request_context():
        resp = _apply_ad_merge_fields(db, {'minContentBetweenAdsSeconds': value})
    return resp, db


def test_nan_rejected():
    resp, db = _call(float('nan'))
    assert resp is not None
    assert resp.status_code == 400
    db.set_setting.assert_not_called()


def test_inf_rejected():
    resp, db = _call(float('inf'))
    assert resp is not None
    assert resp.status_code == 400
    db.set_setting.assert_not_called()


def test_valid_value_persists():
    resp, db = _call(15.0)
    assert resp is None
    db.set_setting.assert_called_once()


def test_out_of_range_rejected():
    resp, db = _call(61.0)
    assert resp is not None
    assert resp.status_code == 400
    db.set_setting.assert_not_called()
