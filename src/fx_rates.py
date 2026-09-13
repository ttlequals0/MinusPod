"""Frankfurter exchange-rate lookups for provider-budget display values."""

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from config import HTTP_TIMEOUT_PROBE
from utils.safe_http import URLTrust, get_capped

_API = 'https://api.frankfurter.dev/v2'
_MAX_RESPONSE_BYTES = 128 * 1024
_CACHE_SECONDS = 6 * 60 * 60
_MAX_RATE_AGE = timedelta(days=7)
_currencies_cache: tuple[float, list[dict]] | None = None


class FxRateError(Exception):
    """Raised when a usable currency or rate is unavailable."""


@dataclass(frozen=True)
class FxRate:
    currency: str
    local_per_usd: Decimal
    source: str
    source_date: str | None


def get_currencies() -> list[dict]:
    global _currencies_cache
    now = time.monotonic()
    if _currencies_cache and now - _currencies_cache[0] < _CACHE_SECONDS:
        return _currencies_cache[1]
    try:
        payload = _get_json('/currencies')
        currencies = [
            {'code': item['iso_code'], 'name': item['name']}
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get('iso_code'), str)
            and len(item['iso_code']) == 3
            and item['iso_code'].isascii()
            and item['iso_code'].isupper()
            and item['iso_code'].isalpha()
            and isinstance(item.get('name'), str)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise FxRateError('Currency service returned an invalid currency list') from exc
    if not any(item['code'] == 'USD' for item in currencies):
        currencies.append({'code': 'USD', 'name': 'US Dollar'})
    currencies.sort(key=lambda item: item['code'])
    _currencies_cache = (now, currencies)
    return currencies


def get_usd_rate(currency: str) -> FxRate:
    currency = currency.upper()
    if currency == 'USD':
        return FxRate(currency='USD', local_per_usd=Decimal('1'), source='Identity', source_date=None)
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        raise FxRateError('Choose a supported currency')
    try:
        payload = _get_json(f'/rate/USD/{currency}')
        rate = Decimal(str(payload['rate']))
        source_date = date.fromisoformat(payload['date'])
        if payload['base'] != 'USD' or payload['quote'] != currency:
            raise ValueError('unexpected currency pair')
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise FxRateError('Currency service returned an invalid rate') from exc
    today = datetime.now(timezone.utc).date()
    if (not rate.is_finite() or rate <= 0 or not -18 <= rate.adjusted() <= 18
            or source_date > today or today - source_date > _MAX_RATE_AGE):
        raise FxRateError('Currency rate is unavailable or too old')
    return FxRate(currency=currency, local_per_usd=rate, source='Frankfurter', source_date=source_date.isoformat())


def _get_json(path: str):
    try:
        body = get_capped(
            f'{_API}{path}', URLTrust.FEED_CONTENT, _MAX_RESPONSE_BYTES,
            timeout=HTTP_TIMEOUT_PROBE,
        )
        return json.loads(body, parse_float=Decimal)
    except Exception as exc:
        raise FxRateError('Could not load currency data') from exc
