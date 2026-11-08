from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import fx_rates


@pytest.fixture(autouse=True)
def _reset_currency_cache(monkeypatch):
    monkeypatch.setattr(fx_rates, '_currencies_cache', None)


def test_usd_rate_normalizes_currency_code():
    with patch('fx_rates._get_json', return_value={
        'base': 'USD', 'quote': 'EUR', 'rate': 0.8, 'date': date.today().isoformat(),
    }):
        rate = fx_rates.get_usd_rate('eur')

    assert rate.currency == 'EUR'
    assert str(rate.local_per_usd) == '0.8'


def test_usd_rate_uses_utc_date_for_rate_freshness():
    utc_now = datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc)
    with patch('fx_rates.datetime') as mocked_datetime, patch('fx_rates._get_json', return_value={
        'base': 'USD', 'quote': 'EUR', 'rate': 0.8, 'date': '2026-09-11',
    }):
        mocked_datetime.now.return_value = utc_now
        rate = fx_rates.get_usd_rate('EUR')

    assert rate.source_date == '2026-09-11'
    mocked_datetime.now.assert_called_once_with(timezone.utc)


def test_usd_rate_rejects_a_future_utc_date():
    utc_now = datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc)
    with patch('fx_rates.datetime') as mocked_datetime, patch('fx_rates._get_json', return_value={
        'base': 'USD', 'quote': 'EUR', 'rate': 0.8, 'date': '2026-09-12',
    }):
        mocked_datetime.now.return_value = utc_now
        with pytest.raises(fx_rates.FxRateError):
            fx_rates.get_usd_rate('EUR')


@pytest.mark.parametrize('payload', [
    {'rate': 0.8, 'date': (date.today() - timedelta(days=8)).isoformat()},
    {'rate': 'NaN', 'date': date.today().isoformat()},
    {'rate': 0, 'date': date.today().isoformat()},
    {'rate': '1e-999999', 'date': date.today().isoformat()},
    {'date': date.today().isoformat()},
])
def test_usd_rate_rejects_stale_or_invalid_responses(payload):
    with patch('fx_rates._get_json', return_value={'base': 'USD', 'quote': 'EUR', **payload}):
        with pytest.raises(fx_rates.FxRateError):
            fx_rates.get_usd_rate('EUR')


def test_usd_rate_rejects_unknown_currency_code():
    with pytest.raises(fx_rates.FxRateError):
        fx_rates.get_usd_rate('EURO')


def test_currency_request_error_logs_type_without_upstream_details(caplog):
    secret = 'upstream-secret-value'
    with patch('fx_rates.get_capped', side_effect=RuntimeError(secret)):
        assert fx_rates.get_currencies() == [{'code': 'USD', 'name': 'US Dollar'}]

    assert 'RuntimeError' in caplog.text
    assert secret not in caplog.text


def test_currencies_offline_fallback_retries_after_a_minute():
    with patch('fx_rates.time.monotonic', return_value=1000.0), \
         patch('fx_rates.get_capped', side_effect=OSError('offline')) as upstream:
        fx_rates.get_currencies()
        fx_rates.get_currencies()
        assert upstream.call_count == 1
        with patch('fx_rates.time.monotonic', return_value=1061.0):
            fx_rates.get_currencies()
        assert upstream.call_count == 2


def test_currencies_offline_prefers_stale_cache(monkeypatch):
    stale = [{'code': 'EUR', 'name': 'Euro'}, {'code': 'USD', 'name': 'US Dollar'}]
    monkeypatch.setattr(fx_rates, '_currencies_cache', (-fx_rates._CACHE_SECONDS * 2, stale))
    with patch('fx_rates.get_capped', side_effect=OSError('offline')):
        assert fx_rates.get_currencies() == stale


def test_currencies_invalid_payload_still_raises():
    with patch('fx_rates._get_json', return_value=None):
        with pytest.raises(fx_rates.FxRateError):
            fx_rates.get_currencies()
