"""Live pricing fetcher for multi-provider LLM cost tracking.

Fetches model pricing from:
- OpenRouter API (GET /api/v1/models) for OpenRouter users
- pricepertoken.com HTML scraping for all other providers
- Falls back to DEFAULT_MODEL_PRICING if live fetch fails and table is empty
"""
import logging
import threading
import time
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

from config import (
    get_pricing_source,
    normalize_model_key,
    PRICING_CACHE_TTL,
)

logger = logging.getLogger(__name__)

_last_fetch: float = 0.0
_fetch_lock = threading.Lock()


def fetch_openrouter_pricing() -> List[Dict]:
    """Fetch pricing from OpenRouter's /api/v1/models endpoint.

    Returns list of dicts:
      [{match_key, raw_model_id, display_name,
        input_cost_per_mtok, output_cost_per_mtok}, ...]
    """
    resp = requests.get('https://openrouter.ai/api/v1/models', timeout=15)
    resp.raise_for_status()

    results = []
    for model in resp.json().get('data', []):
        pricing = model.get('pricing', {})
        try:
            input_per_mtok = float(pricing.get('prompt', '0')) * 1_000_000
            output_per_mtok = float(pricing.get('completion', '0')) * 1_000_000
        except (ValueError, TypeError):
            logger.debug(f"Skipping model with unparseable pricing: {model.get('id')}")
            continue

        if input_per_mtok == 0 and output_per_mtok == 0:
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
    if not text or text in ('-', '--', 'N/A', 'n/a', 'free', 'Free'):
        return None
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
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')

    results = []
    table = soup.find('table')
    if not table:
        logger.warning(f"No pricing table found at {url}")
        return results

    rows = table.find_all('tr')
    if not rows:
        return results

    # Detect column positions from header row
    header_cells = rows[0].find_all('th')
    if not header_cells:
        logger.warning(f"No header row found in pricing table at {url}")
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


def fetch_pricing(source: dict) -> List[Dict]:
    """Fetch pricing based on resolved source config."""
    source_type = source.get('type')

    if source_type == 'free':
        logger.debug("Provider is local/free -- no pricing to fetch")
        return []

    if source_type == 'unknown':
        logger.warning(
            f"Unknown provider domain '{source.get('domain')}' -- "
            f"no pricing source available, costs will record as $0"
        )
        return []

    url = source.get('url', '')
    logger.info(f"Fetching pricing from {source_type}: {url}")

    try:
        if source_type == 'openrouter_api':
            results = fetch_openrouter_pricing()
        elif source_type == 'pricepertoken':
            results = fetch_pricepertoken_pricing(url)
        else:
            return []

        logger.info(f"Fetched pricing for {len(results)} models from {source_type}")
        return results

    except Exception as e:
        logger.warning(f"Failed to fetch pricing from {url}: {e}")
        return []


def refresh_pricing_if_stale():
    """Fetch and persist pricing if cache has expired. Thread-safe.

    If live fetch fails and model_pricing is empty, seeds from DEFAULT_MODEL_PRICING.

    Note: Each gunicorn worker has its own _last_fetch, so multiple workers may
    independently fetch pricing. With a 24h TTL this is acceptable.
    """
    global _last_fetch
    with _fetch_lock:
        if time.monotonic() - _last_fetch < PRICING_CACHE_TTL:
            return
        # Claim slot with short retry window to prevent concurrent fetches.
        # If fetch fails, allows retry in 5 minutes instead of waiting full TTL.
        _last_fetch = time.monotonic() - PRICING_CACHE_TTL + 300

    # Deferred imports to avoid circular dependency:
    # pricing_fetcher -> llm_client -> database -> ... -> pricing_fetcher
    from llm_client import get_effective_provider, get_effective_base_url

    provider = get_effective_provider()
    base_url = get_effective_base_url()
    source = get_pricing_source(provider, base_url)

    logger.info(f"Pricing refresh: provider={provider} source_type={source.get('type')}")

    models = fetch_pricing(source)

    try:
        # Deferred to avoid circular import at module level
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
    global _last_fetch
    with _fetch_lock:
        _last_fetch = 0.0
    refresh_pricing_if_stale()


def invalidate_pricing_cache():
    """Reset TTL so next stale check triggers a fetch. Called on provider change."""
    global _last_fetch
    with _fetch_lock:
        _last_fetch = 0.0
    logger.info("Pricing cache invalidated (provider change)")
