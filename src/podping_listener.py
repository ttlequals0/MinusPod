"""Podping event parsing, feed matching, and the listener loop.

Note: this module deliberately has no import-time dependency on main_app
(so tests/unit/test_podping_parsing.py can import it standalone). The
listener loop resolves the shared db/shutdown_event from main_app.background
lazily, inside podping_listener_loop() itself, the same way main_app.background
resolves its own singletons -- see that module's docstring.
"""
import json
import logging
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlparse, urlunparse

import requests

from database.podcasts import has_upstream
from utils.time import ISO_FORMAT, parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger('podcast.podping')

PODPING_NODES = [
    'https://api.hive.blog',
    'https://api.openhive.network',
    'https://api.deathwing.me',
    'https://techcoderx.com',
]

ACTIONABLE_REASONS = {'update', 'live'}
COOLDOWN_SECONDS = 300
MAX_CATCHUP_BLOCKS = 100

FEED_MAP_REFRESH_SECONDS = 60
HOST_FLUSH_SECONDS = 60

# Where the last processed block is kept so a restart resumes instead of
# jumping to the chain head. Every deploy used to lose the pings sent while
# the container was down, and a podping is never resent.
LAST_BLOCK_SETTING = 'podping_last_block'

# Durable per-node health (JSON blob keyed by node URL) and the all-nodes-down
# degraded signal, both survive a restart via the settings table.
NODE_HEALTH_SETTING = 'podping_node_health'
SELECTED_NODE_SETTING = 'podping_selected_node'
DEGRADED_SETTING = 'podping_all_nodes_down'
DEGRADED_SINCE_SETTING = 'podping_degraded_since'
ACTIVE_CONNECTION_SETTING = 'podping_active_connection'
NODE_CHECK_SETTING = 'podping_node_check'
MONITOR_HEARTBEAT_SETTING = 'podping_monitor_heartbeat'

NODE_BACKOFF_BASE_SECONDS = 5
NODE_BACKOFF_MAX_SECONDS = 300
NODE_BACKOFF_JITTER_FRACTION = 0.2
NODE_BACKOFF_MAX_STEP = 6  # 5 * 2**6 = 320s, already past the 300s cap

# A healthy node succeeds every tick; persist its last-success time no more
# often than this so the status API stays current without writing per RPC.
NODE_SUCCESS_PERSIST_SECONDS = 60
NODE_HEALTH_PROBE_SECONDS = 300
NODE_HEALTH_PROBE_TIMEOUT_SECONDS = 10
ACTIVE_CONNECTION_STALE_SECONDS = 90
MONITOR_HEARTBEAT_SECONDS = 15
NODE_CHECK_LEASE_SECONDS = 60

_QUERY_STRING_RE = re.compile(r'(https?://[^\s?]*)\?\S+')


def _sanitize_failure_reason(message: str) -> str:
    """Short, credential-free reason for durable storage and logs: strips any
    URL query string (where a token would live) and caps the length."""
    text = _QUERY_STRING_RE.sub(r'\1?<redacted>', str(message))
    return text[:200]


def _new_node_health_entry() -> dict:
    return {
        'consecutive_failures': 0,
        'last_success_at': None,
        'next_retry_at': None,
        'last_failure_reason': None,
        'last_http_status': None,
        'last_outcome': None,
    }


def get_node_health_summary(db) -> list[dict]:
    """Per-node durable health for API/status display, one entry per node in
    PODPING_NODES order (unseen nodes default to a blank healthy record)."""
    try:
        raw = db.get_setting(NODE_HEALTH_SETTING)
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        selected_node = db.get_setting(SELECTED_NODE_SETTING)
    except Exception:
        selected_node = None
    active_node = None
    try:
        active_raw = db.get_setting(ACTIVE_CONNECTION_SETTING)
        active = json.loads(active_raw) if active_raw else {}
        observed_at = parse_iso_utc(active.get('observedAt'))
        if (isinstance(active, dict) and active.get('node') in PODPING_NODES
                and observed_at is not None
                and (utc_now() - observed_at).total_seconds()
                <= ACTIVE_CONNECTION_STALE_SECONDS):
            active_node = active['node']
    except (AttributeError, TypeError, ValueError):
        pass

    summary = []
    for node in PODPING_NODES:
        entry = data.get(node) or {}
        summary.append({
            'node': node,
            'consecutiveFailures': int(entry.get('consecutive_failures') or 0),
            'lastSuccessAt': entry.get('last_success_at'),
            'nextRetryAt': entry.get('next_retry_at'),
            'lastFailureReason': entry.get('last_failure_reason'),
            'httpStatus': entry.get('last_http_status'),
            'outcome': entry.get('last_outcome'),
            'selected': node == selected_node,
            'active': node == active_node,
        })
    return summary


def get_node_check_status(db) -> dict:
    try:
        raw = db.get_setting(NODE_CHECK_SETTING)
        status = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        status = {}
    if not isinstance(status, dict):
        status = {}
    state = status.get('status')
    return {
        'checkId': status.get('checkId'),
        'status': state if state in {'pending', 'running', 'completed', 'error'} else 'idle',
        'requestedAt': status.get('requestedAt'),
        'startedAt': status.get('startedAt'),
        'completedAt': status.get('completedAt'),
        'healthyNodes': status.get('healthyNodes'),
        'totalNodes': status.get('totalNodes'),
        'message': status.get('message'),
    }


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

            # Podping signs with posting authority by convention, but an op
            # signed with active authority is still a valid sender.
            auth_lists = [op_data.get('required_posting_auths', []),
                          op_data.get('required_auths', [])]
            if any(not isinstance(a, list) for a in auth_lists):
                continue

            auth_strs = {a.lower() for auths in auth_lists for a in auths
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

    def __init__(self, rpc=None, db=None, refresh=None, sleep=None, rand=None,
                 now=None, node_probe=None, monotonic=None):
        self.rpc = rpc or self._default_rpc
        self.db = db
        self.refresh = refresh
        self.sleep = sleep or _default_sleep_shutdown_aware
        self._service_backoff = sleep is None
        # Injectable so backoff-jitter tests can assert exact values instead
        # of a range; defaults to real jitter in production.
        self.rand = rand or random.uniform
        # Injectable clock so backoff-deadline tests need no real sleeping.
        self.now = now or utc_now
        self.node_probe = node_probe or self._default_node_probe
        self.monotonic = monotonic or time.monotonic

        self.node_index = 0
        self._backoff_step = 0
        # Nodes that have failed since the last success, so a node that stays
        # down logs once instead of once per backoff cycle.
        self._failed_nodes = set()
        # Durable per-node health, loaded once at start so a restart resumes
        # each node's failure streak instead of re-escalating from zero.
        self._node_health = self._load_node_health()
        self._selected_node = self._load_selected_node()
        # Node -> time its last success was written, for the persist cadence.
        self._success_persisted_at = {}
        self._last_rpc_status_code = None
        self._last_health_probe_at = None
        self._node_health_revision = {node: 0 for node in PODPING_NODES}
        self._probe_executor = ThreadPoolExecutor(
            max_workers=len(PODPING_NODES), thread_name_prefix='podping-health')
        self._probe_futures = {}
        self._probe_revisions = {}
        self._probe_discard = False
        self._probe_manual_id = None
        self._probe_claim_id = None
        self._monitoring_enabled = False
        self._owner_id = uuid.uuid4().hex
        self._active_node = None
        self._active_persisted_at = None
        self._active_raw = None
        self._heartbeat_persisted_at = None
        self._heartbeat_raw = None

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
        self._last_rpc_status_code = response.status_code
        if response.status_code != 200:
            raise requests.RequestException(
                f"HTTP {response.status_code} from {url}")
        payload = response.json()
        if not isinstance(payload, dict) or 'result' not in payload:
            raise ValueError(f"Malformed jsonrpc response from {url}")
        return payload['result']

    def _probe_request(self, node, method, params):
        response = requests.post(
            node,
            json={'jsonrpc': '2.0', 'method': method, 'params': params, 'id': 1},
            timeout=NODE_HEALTH_PROBE_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return response.status_code, None, 'http_error', f"HTTP {response.status_code}"
        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError) as exc:
            return response.status_code, None, 'invalid_response', str(exc)
        if not isinstance(payload, dict) or 'result' not in payload:
            return response.status_code, None, 'invalid_response', 'Malformed jsonrpc response'
        return response.status_code, payload['result'], 'healthy', None

    def _default_node_probe(self, node):
        """Validate both RPC operations required by the listener."""
        status, props, outcome, reason = self._probe_request(
            node, 'condenser_api.get_dynamic_global_properties', [])
        if outcome != 'healthy':
            return status, outcome, reason
        head = props.get('head_block_number') if isinstance(props, dict) else None
        if not isinstance(head, int):
            return status, 'invalid_response', 'Head response is missing head_block_number'
        status, block, outcome, reason = self._probe_request(
            node, 'condenser_api.get_block', [max(1, head - 1)])
        if outcome != 'healthy':
            return status, outcome, reason
        if not isinstance(block, dict) or not isinstance(block.get('transactions'), list):
            return status, 'invalid_response', 'Block response is missing transactions'
        return status, 'healthy', None

    def _apply_node_probe(self, node, status_code, outcome, reason):
        entry = self._node_health.setdefault(node, _new_node_health_entry())
        entry['last_http_status'] = status_code
        entry['last_outcome'] = outcome
        if outcome == 'healthy':
            entry['consecutive_failures'] = 0
            entry['last_success_at'] = self.now().strftime(ISO_FORMAT)
            entry['next_retry_at'] = None
            entry['last_failure_reason'] = None
            self._node_health_revision[node] += 1
            return
        entry['consecutive_failures'] = entry.get('consecutive_failures', 0) + 1
        entry['last_failure_reason'] = _sanitize_failure_reason(reason)
        self._node_health_revision[node] += 1

    def _start_node_probes(self, manual_id=None, claim_id=None):
        self._last_health_probe_at = self.monotonic()
        self._probe_revisions = dict(self._node_health_revision)
        self._probe_manual_id = manual_id
        self._probe_claim_id = claim_id
        self._probe_discard = False
        self._probe_futures = {
            node: self._probe_executor.submit(self.node_probe, node)
            for node in PODPING_NODES
        }

    def _finish_node_probes(self):
        if not self._probe_futures or not all(
                future.done() for future in self._probe_futures.values()):
            return
        error = None
        healthy_nodes = 0
        try:
            if not self._probe_discard:
                for node, future in self._probe_futures.items():
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = (None, 'unreachable', str(exc))
                    if result[1] == 'healthy':
                        healthy_nodes += 1
                    if self._node_health_revision[node] != self._probe_revisions[node]:
                        continue
                    self._apply_node_probe(node, *result)
                if not self._persist_node_health():
                    error = 'Node health results could not be saved.'
        except Exception:
            error = 'Node health results could not be saved.'
            logger.exception("Podping node health check could not save results")
        if self._probe_manual_id is not None and self.db is not None:
            raw, current = self._manual_check_request()
            if (current is not None
                    and current.get('checkId') == self._probe_manual_id
                    and current.get('claimId') == self._probe_claim_id
                    and current.get('status') == 'running'):
                completed = dict(current)
                completed['status'] = 'error' if error else 'completed'
                completed['completedAt'] = utc_now_iso()
                completed['healthyNodes'] = healthy_nodes
                completed['totalNodes'] = len(PODPING_NODES)
                completed.pop('leaseUntil', None)
                completed.pop('claimId', None)
                if error:
                    completed['message'] = error
                self.db.replace_setting_if_equal(
                    NODE_CHECK_SETTING, raw, json.dumps(completed))
        self._probe_futures = {}
        self._probe_manual_id = None
        self._probe_claim_id = None
        self._probe_discard = False

    def _manual_check_request(self):
        if self.db is None:
            return None, None
        try:
            raw = self.db.get_setting(NODE_CHECK_SETTING)
            request = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return None, None
        if not isinstance(request, dict) or request.get('status') not in {'pending', 'running'}:
            return raw, None
        return raw, request

    def _claim_manual_check(self, raw, request):
        if request.get('status') == 'running':
            lease_until = parse_iso_utc(request.get('leaseUntil'))
            if lease_until is not None and lease_until > self.now():
                if (request.get('checkId') == self._probe_manual_id
                        and request.get('claimId') == self._probe_claim_id):
                    return request
                return None
        claimed = dict(request)
        claimed['status'] = 'running'
        claimed['startedAt'] = claimed.get('startedAt') or utc_now_iso()
        claimed['claimId'] = uuid.uuid4().hex
        claimed['leaseUntil'] = (
            self.now() + timedelta(seconds=NODE_CHECK_LEASE_SECONDS)).strftime(ISO_FORMAT)
        encoded = json.dumps(claimed)
        stored = self.db.replace_setting_if_equal(NODE_CHECK_SETTING, raw, encoded)
        return claimed if stored == encoded else None

    def _renew_manual_check(self, raw, request):
        if (request.get('checkId') != self._probe_manual_id
                or request.get('claimId') != self._probe_claim_id):
            return
        renewed = dict(request)
        renewed['leaseUntil'] = (
            self.now() + timedelta(seconds=NODE_CHECK_LEASE_SECONDS)).strftime(ISO_FORMAT)
        self.db.replace_setting_if_equal(
            NODE_CHECK_SETTING, raw, json.dumps(renewed))

    def update_node_probes(self, enabled):
        """Harvest probes and schedule enabled or explicitly requested checks."""
        if enabled != self._monitoring_enabled:
            self._monitoring_enabled = enabled
            if enabled:
                self._last_health_probe_at = None
            else:
                self._clear_active_connection()
                if self._probe_manual_id is None:
                    self._probe_discard = True
                    for future in self._probe_futures.values():
                        future.cancel()
        self._finish_node_probes()
        raw, manual = self._manual_check_request()
        manual_id = manual.get('checkId') if manual else None
        if manual_id is not None and self._probe_futures and not self._probe_discard:
            claimed = self._claim_manual_check(raw, manual)
            if claimed is not None:
                self._probe_manual_id = manual_id
                self._probe_claim_id = claimed.get('claimId')
                self._renew_manual_check(json.dumps(claimed), claimed)
        if self._probe_futures:
            return
        if manual_id is not None:
            claimed = self._claim_manual_check(raw, manual)
            if claimed is not None:
                self._start_node_probes(manual_id, claimed.get('claimId'))
            return
        now = self.monotonic()
        if (enabled and (self._last_health_probe_at is None
                         or now - self._last_health_probe_at >= NODE_HEALTH_PROBE_SECONDS)):
            self._start_node_probes()

    def close(self):
        self._probe_discard = True
        for future in self._probe_futures.values():
            future.cancel()
        self._probe_executor.shutdown(wait=False, cancel_futures=True)
        self._clear_active_connection()
        if self.db is not None and self._active_raw is not None:
            self.db.clear_setting_if_equal(
                ACTIVE_CONNECTION_SETTING, self._active_raw)
        if self.db is not None and self._heartbeat_raw is not None:
            self.db.clear_setting_if_equal(
                MONITOR_HEARTBEAT_SETTING, self._heartbeat_raw)

    def wait_with_monitor(self, seconds):
        """Keep leader commands responsive during production backoff."""
        if not self._service_backoff:
            self.sleep(seconds)
            return
        import main_app.background as background_module
        remaining = seconds
        while remaining > 0 and not background_module.shutdown_event.is_set():
            chunk = min(3, remaining)
            self.sleep(chunk)
            remaining -= chunk
            try:
                self.persist_monitor_heartbeat()
                self.update_node_probes(self._monitoring_enabled)
            except Exception:
                logger.exception("Podping monitor service failed during backoff")

    def _load_node_health(self) -> dict:
        if self.db is None:
            return {}
        try:
            raw = self.db.get_setting(NODE_HEALTH_SETTING)
        except Exception:
            return {}
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _persist_node_health(self):
        if self.db is None:
            return True
        try:
            self.db.set_setting(NODE_HEALTH_SETTING, json.dumps(self._node_health))
        except Exception as exc:
            logger.debug("Could not persist podping node health: %s", exc)
            return False
        return True

    def _persist_active_connection(self, node):
        if self.db is None:
            return
        now = self.now()
        if (self._active_node == node and self._active_persisted_at is not None
                and (now - self._active_persisted_at).total_seconds() < 30):
            return
        try:
            encoded = json.dumps({
                'node': node,
                'observedAt': now.strftime(ISO_FORMAT),
                'owner': self._owner_id,
            })
            if self._active_raw is None:
                self.db.set_setting(ACTIVE_CONNECTION_SETTING, encoded)
            else:
                stored = self.db.replace_setting_if_equal(
                    ACTIVE_CONNECTION_SETTING, self._active_raw, encoded)
                if stored != encoded:
                    return
            self._active_raw = encoded
            self._active_node = node
            self._active_persisted_at = now
        except Exception as exc:
            logger.debug("Could not persist active podping connection: %s", exc)

    def _clear_active_connection(self):
        self._active_node = None
        self._active_persisted_at = None
        if self.db is None:
            return
        try:
            encoded = json.dumps({
                'node': None,
                'observedAt': self.now().strftime(ISO_FORMAT),
                'owner': self._owner_id,
            })
            if self._active_raw is None:
                self.db.set_setting(ACTIVE_CONNECTION_SETTING, encoded)
            else:
                stored = self.db.replace_setting_if_equal(
                    ACTIVE_CONNECTION_SETTING, self._active_raw, encoded)
                if stored != encoded:
                    return
            self._active_raw = encoded
        except Exception as exc:
            logger.debug("Could not clear active podping connection: %s", exc)

    def persist_monitor_heartbeat(self):
        now = self.now()
        if (self._heartbeat_persisted_at is not None
                and (now - self._heartbeat_persisted_at).total_seconds()
                < MONITOR_HEARTBEAT_SECONDS):
            return
        encoded = json.dumps({
            'owner': self._owner_id,
            'observedAt': now.strftime(ISO_FORMAT),
        })
        if self._heartbeat_raw is None:
            self.db.set_setting(MONITOR_HEARTBEAT_SETTING, encoded)
        else:
            stored = self.db.replace_setting_if_equal(
                MONITOR_HEARTBEAT_SETTING, self._heartbeat_raw, encoded)
            if stored != encoded:
                return
        self._heartbeat_raw = encoded
        self._heartbeat_persisted_at = now

    def _load_selected_node(self):
        if self.db is None:
            return None
        try:
            return self.db.get_setting(SELECTED_NODE_SETTING) or None
        except Exception:
            return None

    def _set_degraded(self, active: bool):
        """Non-fatal all-nodes-down signal for /system/status; never touches
        /health or feed refresh, which stay independent of Podping."""
        if self.db is None:
            return
        try:
            currently_active = self.db.get_setting(DEGRADED_SETTING) == '1'
        except Exception:
            return
        if active == currently_active:
            return
        try:
            self.db.set_setting(DEGRADED_SETTING, '1' if active else '0')
            self.db.set_setting(DEGRADED_SINCE_SETTING, utc_now_iso() if active else '')
        except Exception as exc:
            logger.debug("Could not persist podping degraded flag: %s", exc)

    def _backoff_seconds(self, step: int) -> float:
        """Exponential backoff (base 5s, doubling, capped at 300s) with a
        +/-20% jitter so multiple listeners retrying together don't align."""
        base = min(NODE_BACKOFF_BASE_SECONDS * (2 ** step), NODE_BACKOFF_MAX_SECONDS)
        jitter = base * NODE_BACKOFF_JITTER_FRACTION
        return max(0.0, base + self.rand(-jitter, jitter))

    def _record_node_failure(self, node, message, outcome='unreachable'):
        entry = self._node_health.setdefault(node, _new_node_health_entry())
        entry['consecutive_failures'] = entry.get('consecutive_failures', 0) + 1
        entry['last_failure_reason'] = _sanitize_failure_reason(message)
        entry['last_http_status'] = self._last_rpc_status_code
        entry['last_outcome'] = outcome
        node_step = min(entry['consecutive_failures'] - 1, NODE_BACKOFF_MAX_STEP)
        retry_at = self.now() + timedelta(
            seconds=self._backoff_seconds(node_step))
        entry['next_retry_at'] = retry_at.isoformat()
        self._node_health_revision[node] += 1
        self._persist_node_health()

    def _record_node_success(self, node):
        """Update in-memory health every time; persist on a transition (first
        record, or recovery) and otherwise at most once per cadence window, so
        the status API's last-success time advances without a write per RPC."""
        entry = self._node_health.get(node)
        is_transition = entry is None or bool(entry.get('consecutive_failures'))
        if entry is None:
            entry = _new_node_health_entry()
            self._node_health[node] = entry
        now = self.now()
        entry['consecutive_failures'] = 0
        entry['last_success_at'] = now.strftime(ISO_FORMAT)
        entry['next_retry_at'] = None
        entry['last_http_status'] = self._last_rpc_status_code
        entry['last_outcome'] = 'healthy'
        self._node_health_revision[node] += 1
        written_at = self._success_persisted_at.get(node)
        due = (written_at is None
               or (now - written_at).total_seconds() >= NODE_SUCCESS_PERSIST_SECONDS)
        if is_transition or due:
            self._persist_node_health()
            self._success_persisted_at[node] = now
        if self.db is not None and self._selected_node != node:
            try:
                self.db.set_setting(SELECTED_NODE_SETTING, node)
                self._selected_node = node
            except Exception as exc:
                logger.debug("Could not persist selected podping node: %s", exc)
        self._persist_active_connection(node)

    def _log_outage_recovery(self, node):
        """Correlate a recovery with how long every node was down, so an
        external Hive-node outage reads distinctly from an internal scheduler
        failure (that path logs 'Podping listener loop iteration failed')."""
        duration_s = None
        if self.db is not None:
            try:
                since_raw = self.db.get_setting(DEGRADED_SINCE_SETTING)
            except Exception:
                since_raw = None
            since_dt = parse_iso_utc(since_raw) if since_raw else None
            if since_dt is not None:
                duration_s = (self.now() - since_dt).total_seconds()
        logger.info(
            "Podping recovered via %s after all nodes were unavailable "
            "(outage_duration_s=%s); missed pings are not redelivered, "
            "normal feed refresh catches up any stale feeds",
            node, f"{duration_s:.0f}" if duration_s is not None else 'unknown')

    def _node_failure(self, message, outcome='unreachable'):
        """Log, rotate to the next node, back off (exponential + jitter), and
        record durable per-node health.

        A node warns on its first failure, repeats go to DEBUG, and only the
        transition into losing every node (when pings are missed) is an ERROR;
        a success clears the state so the next total outage escalates again.
        """
        node = PODPING_NODES[self.node_index]
        if self._active_node == node:
            self._clear_active_connection()
        first_failure = node not in self._failed_nodes
        self._failed_nodes.add(node)
        all_down = len(self._failed_nodes) >= len(PODPING_NODES)
        if first_failure and all_down:
            logger.error(
                "All %d podping nodes failed; pings are being missed. Last: %s: %s",
                len(PODPING_NODES), node, message)
        elif first_failure:
            logger.warning("Podping node %s failed: %s", node, message)
        else:
            logger.debug("Podping node %s failed again: %s", node, message)

        self._record_node_failure(node, message, outcome)
        if all_down:
            self._set_degraded(True)

        self.node_index = (self.node_index + 1) % len(PODPING_NODES)
        step = self._backoff_step
        self._backoff_step = min(self._backoff_step + 1, NODE_BACKOFF_MAX_STEP)
        self.wait_with_monitor(self._backoff_seconds(step))

    def _node_retry_at(self, node):
        """Persisted backoff deadline for a node, or None when it has none."""
        entry = self._node_health.get(node) or {}
        return parse_iso_utc(entry.get('next_retry_at'))

    def _select_node(self) -> int:
        """Index of the node to call next: the first from the current position
        whose persisted backoff deadline has passed, else the one due soonest.
        Reading the stored deadline is what keeps backoff across a restart,
        where the in-memory rotation starts over at node 0."""
        now = self.now()
        soonest_index = self.node_index
        soonest_at = None
        for offset in range(len(PODPING_NODES)):
            index = (self.node_index + offset) % len(PODPING_NODES)
            retry_at = self._node_retry_at(PODPING_NODES[index])
            if retry_at is None or retry_at <= now:
                return index
            if soonest_at is None or retry_at < soonest_at:
                soonest_at = retry_at
                soonest_index = index
        return soonest_index

    def _call_rpc(self, method, params, expected_type=dict):
        """Call self.rpc, validating the response shape. Any exception,
        timeout, or shape mismatch is treated as a node failure (logged,
        node rotated, backoff applied) and returns None."""
        self.node_index = self._select_node()
        node = PODPING_NODES[self.node_index]
        self._last_rpc_status_code = None
        try:
            result = self.rpc(method, params)
        except Exception as exc:
            if self._last_rpc_status_code is None:
                outcome = 'unreachable'
            elif self._last_rpc_status_code == 200:
                outcome = 'invalid_response'
            else:
                outcome = 'http_error'
            self._node_failure(f"{method} failed: {exc}", outcome)
            return None
        if not isinstance(result, expected_type):
            self._node_failure(
                f"{method} returned an invalid response shape", 'invalid_response')
            return None
        self._backoff_step = 0
        was_all_down = len(self._failed_nodes) >= len(PODPING_NODES)
        self._failed_nodes.clear()
        self._record_node_success(node)
        if was_all_down:
            self._log_outage_recovery(node)
        self._set_degraded(False)
        return result

    def _refresh_feed_map(self):
        feed_map = {}
        for podcast in self.db.get_podcast_feed_urls():
            if not has_upstream(podcast):
                continue
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

    def _resume_block(self, head):
        """Block to start from: the last one processed before a restart, or the
        head when there is nothing stored or the gap is too wide to catch up."""
        try:
            stored = self.db.get_setting(LAST_BLOCK_SETTING)
            last = int(stored) if stored else 0
        except (TypeError, ValueError):
            last = 0
        if last and 0 <= head - last <= MAX_CATCHUP_BLOCKS:
            if head > last:
                logger.info("Podping listener resuming at block %d (%d behind head)",
                            last + 1, head - last)
            return last
        return head - 1

    def _buffer_hosts(self, iris):
        """Count the host of every IRI seen, matching a local feed or not."""
        for iri in iris:
            domain = feed_url_domain(iri)
            if domain:
                self.host_buffer[domain] = self.host_buffer.get(domain, 0) + 1

    def _persist_block(self):
        """Record progress so a restart resumes here. Written on the flush
        cadence, so a crash replays at most that many blocks; the per-feed
        refresh cooldown absorbs a repeat."""
        if self.current_block is None:
            return
        try:
            self.db.set_setting(LAST_BLOCK_SETTING, str(self.current_block))
        except Exception as exc:
            logger.debug("Could not persist podping block: %s", exc)

    def _flush_host_buffer(self) -> bool:
        """Write buffered domain counts; keep the buffer on failure to retry.

        Stamped before the write so a failing db backs off to the flush
        interval instead of retrying, and logging, on every tick."""
        self.host_flushed_at = time.time()
        if not self.host_buffer:
            return True
        try:
            self.db.record_podping_hosts(self.host_buffer)
        except Exception:
            logger.exception("Failed to record podping hosts; retrying next flush")
            return False
        self.host_buffer = {}
        return True

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
        """Stamp last_podping_at and refresh, both outside the per-slug
        cooldown window. A burst therefore leaves the displayed last-ping time
        up to one cooldown behind the newest ping."""
        now = time.time()
        last = self.last_refresh.get(slug, 0.0)
        if now - last > COOLDOWN_SECONDS:
            self.last_refresh[slug] = now
            self.db.set_last_podping_at(slug)
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
            self.current_block = self._resume_block(head)

        if head - self.current_block > MAX_CATCHUP_BLOCKS:
            logger.warning(
                "Podping listener is %d blocks behind (over the %d cap); skipping "
                "to block %d. Pings in the gap are lost, they are never resent.",
                head - self.current_block, MAX_CATCHUP_BLOCKS, head - 1)
            self.current_block = head - 1

        while self.current_block < head:
            self.persist_monitor_heartbeat()
            self.update_node_probes(self._monitoring_enabled)
            next_block_num = self.current_block + 1
            block = self._call_rpc('condenser_api.get_block', [next_block_num])
            if block is None:
                return  # Node failure already logged/rotated; retry next tick.
            self.current_block = next_block_num

            for event in extract_podping_events(block):
                iris = event.get('iris') or []
                # Count the host whatever the reason, so coverage reflects all
                # traffic and an unhandled reason cannot make a sender invisible.
                self._buffer_hosts(iris)
                reason = event.get('reason')
                if reason is None or reason in ACTIONABLE_REASONS:
                    auths = event.get('auths') or set()
                    for slug in match_iris(iris, self.feed_map):
                        if self._feed_accepts(slug, auths):
                            self._handle_match(slug, reason)

        if time.time() - self.host_flushed_at >= HOST_FLUSH_SECONDS:
            # Block progress is delivery correctness and host counts are a
            # statistics side table, so a failed flush must not hold the
            # cursor back: a podping is never resent, a count is re-flushed.
            self._flush_host_buffer()
            self._persist_block()

    def final_flush(self):
        """Write buffered counts and block progress on shutdown; without it
        every clean deploy replayed the blocks since the last flush."""
        self._flush_host_buffer()
        self._persist_block()


def podping_listener_loop():
    """Thread target. Checks the podping_enabled setting every iteration;
    waits 30s while disabled, otherwise runs one PodpingListener.tick().
    A top-level exception guard logs and backs off 60s -- this thread must
    never die, whatever the RPC nodes or the db throw at it.
    """
    import main_app.background as background_module
    from main_app.feeds import refresh_single_feed

    listener = PodpingListener(db=background_module.db, refresh=refresh_single_feed)
    listener._clear_active_connection()
    was_enabled = False

    while not background_module.shutdown_event.is_set():
        # Guard point for issue #566 (see Database.rollback_open_transaction):
        # a prior iteration's set_last_podping_at/refresh write that swallowed
        # a failure may have left a transaction open.
        background_module.db.clear_leaked_transaction(logger, 'podping listener')
        try:
            listener.persist_monitor_heartbeat()
            enabled = background_module.db.get_setting_bool('podping_enabled', False)
            listener.update_node_probes(enabled)
            if enabled != was_enabled:
                logger.info(
                    "Podping listener %s", 'enabled' if enabled else 'disabled')
                was_enabled = enabled

            if enabled:
                listener.tick()
                background_module.shutdown_event.wait(timeout=3)
            else:
                background_module.shutdown_event.wait(timeout=5)
        except Exception:
            logger.exception("Podping listener loop iteration failed")
            listener.wait_with_monitor(60)

    try:
        listener.final_flush()
    except Exception:
        logger.exception("Podping listener shutdown flush failed")
    listener.close()
