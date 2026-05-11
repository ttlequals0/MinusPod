"""Live pricing fetcher for multi-provider LLM cost tracking.

Fetches model pricing from:
- OpenRouter API (GET /api/v1/models) for OpenRouter users
- pricepertoken.com HTML scraping for all other providers
- Falls back to DEFAULT_MODEL_PRICING if live fetch fails and table is empty
"""
import logging
import threading
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests
# bs4 is lazy-imported inside fetch_pricepertoken_pricing to keep this module's
# import-time graph minimal for consumers that only need fetch_litellm_pricing
# (e.g. the offline benchmark in benchmarks/llm/).

from config import (
    get_pricing_source,
    HTTP_MAX_REDIRECTS_API,
    HTTP_TIMEOUT_EXTERNAL,
    normalize_model_key,
    PRICING_CACHE_TTL,
)
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_get
from utils.time import parse_iso_datetime
from utils.url import SSRFError

logger = logging.getLogger(__name__)

_last_fetch: float = 0.0
_fetch_lock = threading.Lock()


def fetch_openrouter_pricing() -> List[Dict]:
    """Fetch pricing from OpenRouter's /api/v1/models endpoint.

    Returns list of dicts:
      [{match_key, raw_model_id, display_name,
        input_cost_per_mtok, output_cost_per_mtok}, ...]
    """
    try:
        resp = safe_get(
            'https://openrouter.ai/api/v1/models',
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_EXTERNAL,
            max_redirects=HTTP_MAX_REDIRECTS_API,
        )
        resp.raise_for_status()
    except (SSRFError, requests.RequestException) as exc:
        raise ConnectionError(f"Failed to fetch OpenRouter pricing: {exc}") from exc

    results = []
    for model in resp.json().get('data', []):
        pricing = model.get('pricing', {})
        try:
            input_per_mtok = float(pricing.get('prompt', '0')) * 1_000_000
            output_per_mtok = float(pricing.get('completion', '0')) * 1_000_000
        except (ValueError, TypeError):
            logger.debug(f"Skipping model with unparseable pricing: {model.get('id')}")
            continue

        raw_id = model.get('id', '')
        display_name = model.get('name', raw_id)
        key = normalize_model_key(raw_id)

        logger.debug(
            f"OpenRouter pricing: {raw_id} -> match_key={key} "
            f"in=${input_per_mtok:.4f}/Mtok out=${output_per_mtok:.4f}/Mtok"
        )

        results.append({
            'match_key': key,
            'raw_model_id': raw_id,
            'display_name': display_name,
            'input_cost_per_mtok': round(input_per_mtok, 4),
            'output_cost_per_mtok': round(output_per_mtok, 4),
        })

    return results


def _parse_price(text: str) -> Optional[float]:
    """Parse '$3.000' or '3.000' -> 3.0. Returns None for dashes/empty."""
    text = text.strip().lstrip('$').replace(',', '')
    if not text or text in ('-', '--', 'N/A', 'n/a'):
        return None
    if text.lower() == 'free':
        return 0.0
    try:
        return float(text)
    except ValueError:
        return None


def fetch_pricepertoken_pricing(url: str) -> List[Dict]:
    """Scrape model pricing from a pricepertoken.com provider/endpoint page.

    Dynamically detects column layout from <th> headers:
      Provider pages: Model | Context | Input | Output | ...
      Endpoint pages: Provider | Model | Context | Speed | Input/1M | Output/1M | ...

    Returns list of dicts:
      [{match_key, raw_model_id, display_name,
        input_cost_per_mtok, output_cost_per_mtok}, ...]
    """
    try:
        resp = safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_EXTERNAL,
            max_redirects=HTTP_MAX_REDIRECTS_API,
        )
        resp.raise_for_status()
    except (SSRFError, requests.RequestException) as exc:
        raise ConnectionError(f"Failed to fetch pricing from {url}: {exc}") from exc

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, 'html.parser')

    results = []
    table = soup.find('table')
    if not table:
        logger.warning(f"No pricing table found at {safe_url_for_log(url)}")
        return results

    rows = table.find_all('tr')
    if not rows:
        return results

    # Detect column positions from header row
    header_cells = rows[0].find_all('th')
    if not header_cells:
        logger.warning(f"No header row found in pricing table at {safe_url_for_log(url)}")
        return results

    headers = [th.get_text(strip=True).lower() for th in header_cells]

    try:
        model_col = next(i for i, h in enumerate(headers) if 'model' in h)
        input_col = next(i for i, h in enumerate(headers) if 'input' in h)
        output_col = next(i for i, h in enumerate(headers) if 'output' in h)
    except StopIteration:
        logger.warning(f"Could not find model/input/output columns in headers: {headers}")
        return results

    logger.debug(
        f"pricepertoken columns: model={model_col} input={input_col} output={output_col} "
        f"(headers: {headers})"
    )

    for row in rows[1:]:  # skip header row
        cells = row.find_all('td')
        if len(cells) <= max(model_col, input_col, output_col):
            continue

        # Model name (may contain a link)
        model_cell = cells[model_col]
        model_link = model_cell.find('a')
        display_name = (
            model_link.get_text(strip=True)
            if model_link
            else model_cell.get_text(strip=True)
        )

        if not display_name:
            continue

        input_cost = _parse_price(cells[input_col].get_text(strip=True))
        output_cost = _parse_price(cells[output_col].get_text(strip=True))

        if input_cost is None or output_cost is None:
            continue

        key = normalize_model_key(display_name)

        logger.debug(
            f"pricepertoken pricing: '{display_name}' -> match_key={key} "
            f"in=${input_cost:.4f}/Mtok out=${output_cost:.4f}/Mtok"
        )

        results.append({
            'match_key': key,
            'raw_model_id': display_name,  # best we have from scraping
            'display_name': display_name,
            'input_cost_per_mtok': input_cost,
            'output_cost_per_mtok': output_cost,
        })

    return results


LITELLM_PRICING_URL = (
    'https://raw.githubusercontent.com/BerriAI/litellm/main/'
    'model_prices_and_context_window.json'
)


def fetch_litellm_pricing(provider_filter: Optional[str] = None) -> List[Dict]:
    """Fetch pricing from the LiteLLM community pricing JSON.

    This is a fallback source when the primary provider fetch returns nothing
    (e.g. pricepertoken page missing, unknown provider domain). The JSON is
    maintained by the LiteLLM project and updated frequently.

    Args:
        provider_filter: Optional ``litellm_provider`` value to filter on
            ('anthropic', 'openai', 'bedrock', etc.). None returns all providers
            that report per-token input/output costs.

    Returns list of dicts in the same shape as fetch_openrouter_pricing.
    """
    try:
        resp = safe_get(
            LITELLM_PRICING_URL,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_EXTERNAL,
            max_redirects=HTTP_MAX_REDIRECTS_API,
        )
        resp.raise_for_status()
    except (SSRFError, requests.RequestException) as exc:
        raise ConnectionError(f"Failed to fetch LiteLLM pricing: {exc}") from exc

    try:
        raw = resp.json()
    except ValueError as exc:
        raise ConnectionError(f"LiteLLM pricing response was not valid JSON: {exc}") from exc

    results: List[Dict] = []
    for raw_id, spec in raw.items():
        if raw_id == 'sample_spec' or not isinstance(spec, dict):
            continue
        try:
            input_cost = spec.get('input_cost_per_token')
            output_cost = spec.get('output_cost_per_token')
            if input_cost is None or output_cost is None:
                continue
            if provider_filter and spec.get('litellm_provider') != provider_filter:
                continue
            input_per_mtok = float(input_cost) * 1_000_000
            output_per_mtok = float(output_cost) * 1_000_000
        except (ValueError, TypeError):
            logger.debug(f"Skipping LiteLLM entry with unparseable pricing: {raw_id}")
            continue

        key = normalize_model_key(raw_id)
        results.append({
            'match_key': key,
            'raw_model_id': raw_id,
            'display_name': raw_id,
            'input_cost_per_mtok': round(input_per_mtok, 4),
            'output_cost_per_mtok': round(output_per_mtok, 4),
        })

    return results


def fetch_pricing(source: dict, provider_for_fallback: Optional[str] = None) -> List[Dict]:
    """Fetch pricing based on resolved source config.

    Falls back to the LiteLLM community JSON when the primary source is
    unavailable or returns nothing, or when the provider domain is unknown.
    """
    source_type = source.get('type')

    if source_type == 'free':
        logger.debug("Provider is local/free -- no pricing to fetch")
        return []

    if source_type == 'unknown':
        logger.info(
            f"Unknown provider domain '{source.get('domain')}' -- "
            f"attempting LiteLLM fallback"
        )
        return _try_litellm_fallback(provider_for_fallback)

    url = source.get('url', '')
    logger.info(f"Fetching pricing from {source_type}: {safe_url_for_log(url)}")

    primary_error: Optional[Exception] = None
    results: List[Dict] = []
    try:
        if source_type == 'openrouter_api':
            results = fetch_openrouter_pricing()
        elif source_type == 'pricepertoken':
            results = fetch_pricepertoken_pricing(url)
    except Exception as e:
        primary_error = e
        logger.warning(f"Failed to fetch pricing from {safe_url_for_log(url)}: {e}")

    if results:
        logger.info(f"Fetched pricing for {len(results)} models from {source_type}")
        return results

    if primary_error is not None:
        logger.warning(f"{source_type} fetch errored, trying LiteLLM fallback")
    else:
        logger.info(f"{source_type} returned no rows, trying LiteLLM fallback")
    return _try_litellm_fallback(provider_for_fallback)


def _try_litellm_fallback(provider_filter: Optional[str]) -> List[Dict]:
    """Attempt LiteLLM fallback and swallow fetch errors."""
    try:
        results = fetch_litellm_pricing(provider_filter=provider_filter)
        if results:
            logger.info(
                f"Fetched pricing for {len(results)} models from litellm "
                f"(filter={provider_filter or 'none'})"
            )
        else:
            logger.warning(
                f"LiteLLM fallback returned no rows "
                f"(filter={provider_filter or 'none'}); costs may record as $0"
            )
        return results
    except Exception as e:
        logger.warning(f"LiteLLM fallback failed: {e}")
        return []


def refresh_pricing_if_stale(force: bool = False):
    """Fetch and persist pricing if cache has expired. Thread-safe.

    If live fetch fails and model_pricing is empty, seeds from DEFAULT_MODEL_PRICING.

    Args:
        force: Skip both in-memory and DB TTL checks (used by force_refresh_pricing).
    """
    global _last_fetch
    if not force:
        with _fetch_lock:
            if time.monotonic() - _last_fetch < PRICING_CACHE_TTL:
                return

        # Check if another worker already fetched recently (DB-level coordination)
        try:
            from database import Database
            db = Database()
            last_updated = db.get_pricing_last_updated()
            if last_updated:
                updated_dt = parse_iso_datetime(last_updated)
                age_seconds = (datetime.now(timezone.utc) - updated_dt).total_seconds()
                if age_seconds < PRICING_CACHE_TTL:
                    with _fetch_lock:
                        _last_fetch = time.monotonic()
                    logger.debug(f"Pricing fresh in DB ({age_seconds:.0f}s old, fetched by another worker)")
                    return
        except Exception as e:
            logger.debug(f"Cross-worker pricing check failed, proceeding: {e}")

    # Claim slot with short retry window to prevent concurrent fetches.
    # If fetch fails, allows retry in 5 minutes instead of waiting full TTL.
    with _fetch_lock:
        _last_fetch = time.monotonic() - PRICING_CACHE_TTL + 300

    # Deferred imports to avoid circular dependency:
    # pricing_fetcher -> llm_client -> database -> ... -> pricing_fetcher
    from llm_client import get_effective_provider, get_effective_base_url

    provider = get_effective_provider()
    base_url = get_effective_base_url()
    source = get_pricing_source(provider, base_url)

    logger.info(f"Pricing refresh: provider={provider} source_type={source.get('type')}")

    models = fetch_pricing(source, provider_for_fallback=provider)

    try:
        from database import Database
        db = Database()

        if models:
            db.upsert_fetched_pricing(models, source=source['type'])
            logger.info(f"Stored pricing for {len(models)} models (source={source['type']})")
            # Success -- set full TTL
            with _fetch_lock:
                _last_fetch = time.monotonic()
        else:
            # Live fetch failed or returned nothing -- seed defaults if table is empty
            existing = db.get_model_pricing()
            if not existing:
                db.seed_default_pricing()
                logger.info("Live pricing unavailable, seeded from DEFAULT_MODEL_PRICING")
    except Exception as e:
        logger.warning(f"Failed to persist pricing: {e}")


def force_refresh_pricing():
    """Force a pricing refresh regardless of TTL. Called by manual API endpoint."""
    refresh_pricing_if_stale(force=True)


