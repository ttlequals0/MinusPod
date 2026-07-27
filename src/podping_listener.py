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

FEED_MAP_REFRESH_SECONDS = 60
HOST_FLUSH_SECONDS = 60
NODE_BACKOFF_SCHEDULE = (5, 15, 60)


def _default_sleep_shutdown_aware(seconds):
    """Default sleep that is interruptible by shutdown_event.

    Used for node failure backoff in PodpingListener; imported lazily
    so this module remains dependency-free at load time.
    """
    import main_app.background as background_module
    background_module.shutdown_event.wait(timeout=seconds)


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


def feed_url_domain(url: str) -> str:
    """Lowercase host of a feed URL without port, or '' when unparseable."""
    if not isinstance(url, str) or not url:
        return ''
    try:
        return urlparse(url).hostname or ''
    except ValueError:
        return ''


def extract_podping_events(block: dict) -> list[dict]:
    """Extract podping events from a block.

    Filters on the operation id alone, which is what the reference watcher
    does. Authorization is per feed via <podcast:hiveAccount>, so the sending
    accounts ride along in 'auths' for the caller to check.

    Args:
        block: Block dict from condenser_api.get_block with shape
               {'transactions': [{'operations': [['custom_json', {...}]]}]}.

    Returns:
        List of dicts with 'iris', 'reason', and 'auths' keys.
    """
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

            auth_strs = {a.lower() for a in required_posting_auths
                         if isinstance(a, str)}

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

            events.append({'iris': iris, 'reason': reason, 'auths': auth_strs})

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
        self.sleep = sleep or _default_sleep_shutdown_aware

        self.node_index = 0
        self._backoff_step = 0

        self.feed_map = {}
        self.feed_rules = {}
        self.feed_map_fetched_at = 0.0

        self.current_block = None
        self.last_refresh = {}  # slug -> time.time() of last podping-triggered refresh

        self.host_buffer = {}
        self.host_flushed_at = 0.0

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

    def _refresh_feed_map(self):
        feed_map = {}
        for podcast in self.db.get_all_podcasts():
            source_url = podcast.get('source_url')
            if source_url:
                feed_map[normalize_feed_url(source_url)] = podcast['slug']
        self.feed_map = feed_map
        self.feed_rules = self.db.get_all_podping_declarations()
        self.feed_map_fetched_at = time.time()

    def _maybe_refresh_feed_map(self):
        now = time.time()
        if now - self.feed_map_fetched_at >= FEED_MAP_REFRESH_SECONDS:
            self._refresh_feed_map()

    def _buffer_hosts(self, iris):
        """Count the host of every IRI seen, matching a local feed or not."""
        for iri in iris:
            domain = feed_url_domain(iri)
            if domain:
                self.host_buffer[domain] = self.host_buffer.get(domain, 0) + 1

    def _flush_host_buffer(self):
        """Write buffered domain counts; keep the buffer on failure to retry."""
        if not self.host_buffer:
            return
        # Stamped before the write so a failing db backs off to the flush
        # interval instead of retrying, and logging, on every tick.
        self.host_flushed_at = time.time()
        try:
            self.db.record_podping_hosts(self.host_buffer)
        except Exception:
            logger.exception("Failed to record podping hosts; retrying next flush")
            return
        self.host_buffer = {}

    def _feed_accepts(self, slug, auths):
        """Whether this feed's own <podcast:podping> declaration allows a ping
        from these accounts. Undeclared feeds accept any sender: the spec gives
        nothing to check against, and polling stays the fallback either way.
        """
        rules = self.feed_rules.get(slug)
        if not rules:
            return True
        if rules.get('uses_podping') is False:
            logger.debug(
                "[%s] Podping ignored: feed declares usesPodping=false", slug)
            return False
        declared = rules.get('hive_accounts') or []
        if declared and not (set(declared) & set(auths or ())):
            logger.info(
                "[%s] Podping from %s ignored: not in the feed's hiveAccount "
                "list %s", slug, sorted(auths or ()), declared)
            return False
        return True

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
        else:
            logger.debug(
                "[%s] Podping received (reason=%s), skipping refresh: "
                "cooldown active (%.0fs remaining)",
                slug, reason, COOLDOWN_SECONDS - (now - last))

    def tick(self) -> None:
        """One polling iteration: refresh the feed map as needed, pull any new
        blocks, match podping events against known feeds."""
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

            for event in extract_podping_events(block):
                reason = event.get('reason')
                if reason is None or reason in ACTIONABLE_REASONS:
                    iris = event.get('iris') or []
                    self._buffer_hosts(iris)
                    auths = event.get('auths') or set()
                    for slug in match_iris(iris, self.feed_map):
                        if self._feed_accepts(slug, auths):
                            self._handle_match(slug, reason)

        if time.time() - self.host_flushed_at >= HOST_FLUSH_SECONDS:
            self._flush_host_buffer()


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
                background_module.shutdown_event.wait(timeout=3)
            else:
                background_module.shutdown_event.wait(timeout=30)
        except Exception:
            logger.exception("Podping listener loop iteration failed")
            background_module.shutdown_event.wait(timeout=60)
