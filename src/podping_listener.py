"""Podping event parsing, feed matching, and the listener loop.

Note: this module deliberately has no import-time dependency on main_app
(so tests/unit/test_podping_parsing.py can import it standalone). The
listener loop resolves the shared db/shutdown_event from main_app.background
lazily, inside podping_listener_loop() itself, the same way main_app.background
resolves its own singletons -- see that module's docstring.
"""
import json
import logging
import time
from urllib.parse import urlparse, urlunparse

import requests

logger = logging.getLogger('podcast.podping')

PODPING_NODES = [
    'https://api.hive.blog',
    'https://api.openhive.network',
    'https://hived.emre.sh'
]

ACTIONABLE_REASONS = {'update', 'live'}
COOLDOWN_SECONDS = 300
MAX_CATCHUP_BLOCKS = 100

ALLOWED_ACCOUNTS_REFRESH_SECONDS = 3600
FEED_MAP_REFRESH_SECONDS = 60
NODE_BACKOFF_SCHEDULE = (5, 15, 60)


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


class PodpingListener:
    """Polls Hive nodes for podping custom_json ops and refreshes matching
    feeds. All external effects (RPC, db, feed refresh, backoff sleep) are
    injectable so tests never touch the network or a real clock sleep.
    """

    def __init__(self, rpc=None, db=None, refresh=None, sleep=None):
        self.rpc = rpc or self._default_rpc
        self.db = db
        self.refresh = refresh
        self.sleep = sleep or time.sleep

        self.node_index = 0
        self._backoff_step = 0

        self.allowed_accounts = set()
        self.allowed_accounts_fetched_at = 0.0

        self.feed_map = {}
        self.feed_map_fetched_at = 0.0

        self.current_block = None
        self.last_refresh = {}  # slug -> time.time() of last podping-triggered refresh

    def _default_rpc(self, method, params):
        """Default rpc: POST to the currently-selected node. Returns the
        unwrapped 'result' payload (dict or list depending on method)."""
        url = PODPING_NODES[self.node_index]
        response = requests.post(
            url,
            json={'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1},
            timeout=10,
        )
        if response.status_code != 200:
            raise requests.RequestException(
                f"HTTP {response.status_code} from {url}")
        payload = response.json()
        if not isinstance(payload, dict) or 'result' not in payload:
            raise ValueError(f"Malformed jsonrpc response from {url}")
        return payload['result']

    def _node_failure(self, message):
        """Log, rotate to the next node, and back off (5s/15s/60s, capped)."""
        node = PODPING_NODES[self.node_index]
        logger.warning("Podping node %s failed: %s", node, message)
        self.node_index = (self.node_index + 1) % len(PODPING_NODES)
        step = min(self._backoff_step, len(NODE_BACKOFF_SCHEDULE) - 1)
        self._backoff_step = min(self._backoff_step + 1, len(NODE_BACKOFF_SCHEDULE) - 1)
        self.sleep(NODE_BACKOFF_SCHEDULE[step])

    def _call_rpc(self, method, params, expected_type=dict):
        """Call self.rpc, validating the response shape. Any exception,
        timeout, or shape mismatch is treated as a node failure (logged,
        node rotated, backoff applied) and returns None."""
        try:
            result = self.rpc(method, params)
        except Exception as exc:
            self._node_failure(f"{method} failed: {exc}")
            return None
        if not isinstance(result, expected_type):
            self._node_failure(f"{method} returned an invalid response shape")
            return None
        self._backoff_step = 0
        return result

    def refresh_allowed_accounts(self) -> None:
        """Fetch the podping posting-authority allow-list: 'podping' itself
        plus every account_auths name on its posting authority. On failure,
        the previous allow-list (possibly empty, if never fetched) is kept.
        """
        accounts = self._call_rpc(
            'condenser_api.get_accounts', [['podping']], expected_type=list)
        if accounts is None:
            return

        allowed = {'podping'}
        if accounts and isinstance(accounts[0], dict):
            posting = accounts[0].get('posting')
            if isinstance(posting, dict):
                for auth in posting.get('account_auths', []):
                    if isinstance(auth, (list, tuple)) and auth:
                        allowed.add(auth[0])
                    elif isinstance(auth, str):
                        allowed.add(auth)

        self.allowed_accounts = allowed
        self.allowed_accounts_fetched_at = time.time()

    def _maybe_refresh_allowed_accounts(self):
        now = time.time()
        if now - self.allowed_accounts_fetched_at >= ALLOWED_ACCOUNTS_REFRESH_SECONDS:
            self.refresh_allowed_accounts()

    def _refresh_feed_map(self):
        feed_map = {}
        for podcast in self.db.get_all_podcasts():
            source_url = podcast.get('source_url')
            if source_url:
                feed_map[normalize_feed_url(source_url)] = podcast['slug']
        self.feed_map = feed_map
        self.feed_map_fetched_at = time.time()

    def _maybe_refresh_feed_map(self):
        now = time.time()
        if now - self.feed_map_fetched_at >= FEED_MAP_REFRESH_SECONDS:
            self._refresh_feed_map()

    def _handle_match(self, slug, reason):
        """Always stamp last_podping_at; refresh only outside the per-slug
        cooldown window."""
        self.db.set_last_podping_at(slug)
        now = time.time()
        last = self.last_refresh.get(slug, 0.0)
        if now - last > COOLDOWN_SECONDS:
            self.last_refresh[slug] = now
            logger.info(
                "[%s] Podping received (reason=%s), refreshing feed",
                slug, reason)
            if self.refresh is not None:
                self.refresh(slug)

    def tick(self) -> None:
        """One polling iteration: refresh allow-list/feed map as needed,
        pull any new blocks, match podping events against known feeds."""
        self._maybe_refresh_allowed_accounts()
        if not self.allowed_accounts:
            # Never successfully fetched (or fail-closed) -- nothing to do.
            return

        self._maybe_refresh_feed_map()

        props = self._call_rpc('condenser_api.get_dynamic_global_properties', [])
        if props is None:
            return
        head = props.get('head_block_number')
        if not isinstance(head, int):
            self._node_failure(
                "get_dynamic_global_properties missing head_block_number")
            return

        if self.current_block is None:
            self.current_block = head - 1

        if head - self.current_block > MAX_CATCHUP_BLOCKS:
            logger.info(
                "Podping listener is %d blocks behind; skipping catch-up to block %d",
                head - self.current_block, head - 1)
            self.current_block = head - 1

        while self.current_block < head:
            next_block_num = self.current_block + 1
            block = self._call_rpc('condenser_api.get_block', [next_block_num])
            if block is None:
                return  # Node failure already logged/rotated; retry next tick.
            self.current_block = next_block_num

            for event in extract_podping_events(block, self.allowed_accounts):
                reason = event.get('reason')
                if reason is None or reason in ACTIONABLE_REASONS:
                    for slug in match_iris(event.get('iris') or [], self.feed_map):
                        self._handle_match(slug, reason)


def podping_listener_loop():
    """Thread target. Checks the podping_enabled setting every iteration;
    waits 30s while disabled, otherwise runs one PodpingListener.tick().
    A top-level exception guard logs and backs off 60s -- this thread must
    never die, whatever the RPC nodes or the db throw at it.
    """
    import main_app.background as background_module
    from main_app.feeds import refresh_single_feed

    listener = PodpingListener(db=background_module.db, refresh=refresh_single_feed)
    was_enabled = False

    while not background_module.shutdown_event.is_set():
        # Guard point for issue #566 (see Database.rollback_open_transaction):
        # a prior iteration's set_last_podping_at/refresh write that swallowed
        # a failure may have left a transaction open.
        background_module.db.clear_leaked_transaction(logger, 'podping listener')
        try:
            enabled = background_module.db.get_setting_bool('podping_enabled', False)
            if enabled != was_enabled:
                logger.info(
                    "Podping listener %s", 'enabled' if enabled else 'disabled')
                was_enabled = enabled

            if enabled:
                listener.tick()
            else:
                background_module.shutdown_event.wait(timeout=30)
        except Exception:
            logger.exception("Podping listener loop iteration failed")
            background_module.shutdown_event.wait(timeout=60)
