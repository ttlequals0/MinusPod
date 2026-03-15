"""Outbound webhook dispatch with Jinja2 custom payload templates."""

import datetime
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from utils.time import utc_now_iso
from utils.url import validate_url, SSRFError

logger = logging.getLogger('podcast.webhooks')

EVENT_EPISODE_PROCESSED = 'Episode Processed'
EVENT_EPISODE_FAILED = 'Episode Failed'
VALID_EVENTS = {EVENT_EPISODE_PROCESSED, EVENT_EPISODE_FAILED}

_RETRY_ATTEMPTS = 2
_RETRY_DELAY_SECS = 2
_REQUEST_TIMEOUT_SECS = 5

_sandbox_env = SandboxedEnvironment()

def _format_duration(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    if seconds is None:
        return None
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_cost(cost):
    """Format cost as $X.XX."""
    if cost is None:
        return None
    return f"${cost:.2f}"


_DUMMY_CONTEXT = {
    'event': 'Episode Processed',
    'timestamp': '',  # overwritten at render time with current UTC
    'podcast': {
        'name': 'Example Podcast',
        'slug': 'example-podcast',
    },
    'episode': {
        'id': 'abc123',
        'title': 'Example Episode Title',
        'slug': 'example-podcast',
        'url': 'http://your-server:8000/ui/feeds/example-podcast/episodes/abc123',
        'ads_removed': 3,
        'processing_time_secs': 42.5,
        'processing_time': _format_duration(42.5),
        'llm_cost': 0.0035,
        'llm_cost_display': _format_cost(0.0035),
        'time_saved_secs': 187.0,
        'time_saved': _format_duration(187.0),
        'error_message': None,
    },
}


def _build_context(event, episode_id, slug, episode_title, processing_time,
                   llm_cost, ads_removed, error_message, original_duration,
                   new_duration, podcast_name=None):
    """Build the template/payload context dict for a webhook event."""
    ui_base_url = os.environ.get('UI_BASE_URL') or os.environ.get('BASE_URL', 'http://localhost:8000')
    episode_url = f"{ui_base_url}/ui/feeds/{slug}/episodes/{episode_id}"

    if original_duration is not None and new_duration is not None:
        time_saved_secs = round(original_duration - new_duration, 2)
    else:
        time_saved_secs = None

    rounded_processing = round(processing_time, 2) if processing_time is not None else None
    rounded_cost = round(llm_cost, 6) if llm_cost is not None else None

    return {
        'event': event,
        'timestamp': datetime.datetime.now(datetime.timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        ),
        'podcast': {
            'name': podcast_name or slug,
            'slug': slug,
        },
        'episode': {
            'id': episode_id,
            'title': episode_title,
            'slug': slug,
            'url': episode_url,
            'ads_removed': ads_removed,
            'processing_time_secs': rounded_processing,
            'processing_time': _format_duration(rounded_processing),
            'llm_cost': rounded_cost,
            'llm_cost_display': _format_cost(rounded_cost),
            'time_saved_secs': time_saved_secs,
            'time_saved': _format_duration(time_saved_secs),
            'error_message': error_message,
        },
    }


def _render_template(template_str, context):
    """Render a Jinja2 template in a sandboxed environment."""
    template = _sandbox_env.from_string(template_str)
    return template.render(**context)


def _dispatch_webhook(url, body_bytes, headers, max_attempts=_RETRY_ATTEMPTS):
    """POST body_bytes to url with retry logic. Fire-and-forget."""
    for attempt in range(1, max_attempts + 1):
        try:
            req = urllib.request.Request(
                url, data=body_bytes, headers=headers, method='POST'
            )
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECS) as resp:
                logger.info(
                    "Webhook delivered to %s (attempt %d, status %d)",
                    url, attempt, resp.status,
                )
                return resp.status
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.warning(
                "Webhook delivery to %s failed (attempt %d/%d): %s",
                url, attempt, max_attempts, exc,
            )
            if attempt < max_attempts:
                time.sleep(_RETRY_DELAY_SECS)
        except Exception:
            logger.exception(
                "Unexpected error dispatching webhook to %s (attempt %d/%d)",
                url, attempt, max_attempts,
            )
            if attempt < max_attempts:
                time.sleep(_RETRY_DELAY_SECS)
    return None


def _prepare_and_dispatch(webhook_config, context, add_test_flag=False,
                          max_attempts=_RETRY_ATTEMPTS):
    """Render payload and dispatch to a single webhook. Returns HTTP status or None."""
    url = webhook_config.get('url')
    if not url:
        return None

    # Re-validate URL at dispatch time to guard against stored URLs that
    # predate SSRF validation or DNS changes since creation.
    try:
        validate_url(url)
    except SSRFError as exc:
        logger.warning("Webhook URL blocked by SSRF check at dispatch time: %s (%s)", url, exc)
        return None

    if add_test_flag:
        context = dict(context)
        context['test'] = True

    content_type = webhook_config.get('contentType', 'application/json')
    template_str = webhook_config.get('payloadTemplate')

    if template_str:
        try:
            body_str = _render_template(template_str, context)
        except TemplateError as exc:
            logger.error("Jinja2 render error for webhook %s, skipping: %s", url, exc)
            return None
    else:
        payload = dict(context)
        body_str = json.dumps(payload)

    body_bytes = body_str.encode('utf-8')

    headers = {'Content-Type': content_type}
    secret = webhook_config.get('secret')
    if secret:
        sig = hmac.new(
            secret.encode('utf-8'), body_bytes, hashlib.sha256
        ).hexdigest()
        headers['X-MinusPod-Signature'] = f"sha256={sig}"

    return _dispatch_webhook(url, body_bytes, headers, max_attempts=max_attempts)


def load_webhooks(db=None):
    """Load webhooks list from DB settings."""
    if db is None:
        from database import Database  # deferred to avoid circular imports
        db = Database()
    raw = db.get_setting('webhooks')
    if not raw:
        return []
    try:
        webhooks = json.loads(raw)
        return webhooks if isinstance(webhooks, list) else []
    except (json.JSONDecodeError, TypeError):
        logger.error("Failed to parse webhooks setting from DB")
        return []


def _fire_event_sync(event, episode_id, slug, episode_title, processing_time,
                     llm_cost, ads_removed, error_message, original_duration,
                     new_duration, podcast_name=None):
    """Synchronous webhook dispatch -- called in a daemon thread by fire_event."""
    webhooks = load_webhooks()
    if not webhooks:
        return

    context = _build_context(
        event, episode_id, slug, episode_title, processing_time,
        llm_cost, ads_removed, error_message, original_duration, new_duration,
        podcast_name=podcast_name,
    )

    for wh in webhooks:
        if not wh.get('enabled', False):
            continue
        if event not in wh.get('events', []):
            continue
        try:
            _prepare_and_dispatch(wh, context)
        except Exception:
            logger.exception("Unexpected error dispatching webhook to %s", wh.get('url'))


def fire_event(event, episode_id, slug, episode_title, processing_time,
               llm_cost, ads_removed=0, error_message=None,
               original_duration=None, new_duration=None,
               podcast_name=None):
    """Load webhooks from DB and dispatch to all matching subscribers.

    Dispatches in a daemon thread so the processing pipeline is never blocked.
    """
    if event not in VALID_EVENTS:
        logger.error("Invalid webhook event: %s", event)
        return

    thread = threading.Thread(
        target=_fire_event_sync,
        args=(event, episode_id, slug, episode_title, processing_time,
              llm_cost, ads_removed, error_message, original_duration,
              new_duration, podcast_name),
        daemon=True,
    )
    thread.start()


def render_template_preview(template_string):
    """Render a Jinja2 template with dummy data for validation/preview.

    Returns the rendered string. Raises jinja2.TemplateError on invalid
    templates so callers can surface the error to the user.
    """
    context = dict(_DUMMY_CONTEXT)
    context['timestamp'] = utc_now_iso()
    return _render_template(template_string, context)


def fire_test_event(webhook_config):
    """Fire a test payload to a single webhook config dict.

    Attempts to load real data from the most recent completed
    processing_history entry. Falls back to synthetic placeholder data.

    Returns True on HTTP 2xx, False otherwise.
    """
    from database import Database  # deferred to avoid circular imports

    db = Database()
    context = None

    try:
        row = db.get_latest_completed_processing()
        if row:
            context = _build_context(
                event=EVENT_EPISODE_PROCESSED,
                episode_id=row['episode_id'],
                slug=row['podcast_slug'],
                episode_title=row['episode_title'],
                processing_time=row['processing_duration_seconds'],
                llm_cost=row['llm_cost'],
                ads_removed=row['ads_detected'],
                error_message=None,
                original_duration=row['original_duration'],
                new_duration=row['new_duration'],
                podcast_name=row.get('podcast_title'),
            )
    except Exception:
        logger.debug("Could not load real data for test webhook, using placeholders")

    if context is None:
        context = dict(_DUMMY_CONTEXT)
        context['timestamp'] = utc_now_iso()

    status = _prepare_and_dispatch(webhook_config, context, add_test_flag=True, max_attempts=1)
    if status is not None and 200 <= status < 300:
        return True
    return False
