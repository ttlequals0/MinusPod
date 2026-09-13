from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import fx_rates


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
