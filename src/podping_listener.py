"""Podping event parsing and feed matching."""
import json
from urllib.parse import urlparse, urlunparse

PODPING_NODES = [
    'https://api.hive.blog',
    'https://api.openhive.network',
    'https://hived.emre.sh'
]

ACTIONABLE_REASONS = {'update', 'live'}
COOLDOWN_SECONDS = 300
MAX_CATCHUP_BLOCKS = 100


def normalize_feed_url(url: str) -> str:
    """Normalize a feed URL: lowercase scheme+host, strip one trailing slash, preserve path case/query.

    Args:
        url: The URL to normalize.

    Returns:
        Normalized URL string.
    """
    parsed = urlparse(url)
    normalized_scheme = parsed.scheme.lower()
    normalized_netloc = parsed.netloc.lower()

    path = parsed.path
    if path.endswith('/') and path != '/':
        path = path[:-1]

    result = urlunparse((
        normalized_scheme,
        normalized_netloc,
        path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))

    return result


def extract_podping_events(block: dict, allowed_accounts: set[str]) -> list[dict]:
    """Extract podping events from a block.

    Args:
        block: Block dict from condenser_api.get_block with shape
               {'transactions': [{'operations': [['custom_json', {...}]]}]}.
        allowed_accounts: Set of accounts allowed to post podping events.
                         Empty set rejects all events (fail closed).

    Returns:
        List of dicts with 'iris' and 'reason' keys.
    """
    if not allowed_accounts:
        return []

    events = []
    transactions = block.get('transactions')
    if not isinstance(transactions, list):
        return []

    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        operations = tx.get('operations')
        if not isinstance(operations, list):
            continue

        for op in operations:
            if not isinstance(op, list) or len(op) < 2:
                continue

            op_type = op[0]
            op_data = op[1]

            if op_type != 'custom_json' or not isinstance(op_data, dict):
                continue

            op_id = op_data.get('id')
            if not (op_id == 'podping' or (isinstance(op_id, str) and op_id.startswith('pp_'))):
                continue

            required_posting_auths = op_data.get('required_posting_auths', [])
            if not isinstance(required_posting_auths, list):
                continue
            if not required_posting_auths:
                continue

            auth_strs = {a for a in required_posting_auths if isinstance(a, str)}
            if not (auth_strs & allowed_accounts):
                continue

            json_string = op_data.get('json', '')
            if not json_string:
                continue

            try:
                payload = json.loads(json_string)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue

            if not isinstance(payload, dict):
                continue

            iris = None
            reason = None

            version = payload.get('version')
            if isinstance(version, str) and version.startswith('1.'):
                iris = payload.get('iris')
                reason = payload.get('reason')
            else:
                urls = payload.get('urls')
                if urls and isinstance(urls, list) and urls:
                    iris = urls
                else:
                    url = payload.get('url')
                    if url and isinstance(url, str):
                        iris = [url]

            if not iris or not isinstance(iris, list):
                continue

            events.append({'iris': iris, 'reason': reason})

    return events


def match_iris(iris: list[str], feed_map: dict[str, str]) -> list[str]:
    """Match IRIs to feeds and return deduplicated slugs.

    Args:
        iris: List of feed URLs (IRIs).
        feed_map: Dict mapping normalized source_url to slug.

    Returns:
        List of matched slugs, deduplicated.
    """
    matched_slugs = set()

    for iri in iris:
        normalized = normalize_feed_url(iri)
        if normalized in feed_map:
            matched_slugs.add(feed_map[normalized])

    return sorted(matched_slugs)
