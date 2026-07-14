"""Native email notifications.

Sends per-event emails through an operator-configured SMTP server, sharing
the event stream (and the alert dedup window) with webhooks. One SMTP
connection per send, opened with a hard timeout, so calls are safe from the
webhook dispatch daemon threads. ``send_event_email`` never raises into its
caller: a broken mail server must not break episode processing.

Emails are multipart: a plain-text body in the reference mailer's
"Label:  value" style, plus an HTML alternative with the MinusPod logo
embedded via CID (no external image fetch, so it renders with images
blocked-by-default clients too once allowed).
"""
import functools
import html
import json
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path
from typing import Optional

from utils.url import validate_outbound_host

logger = logging.getLogger('podcast.email')

SMTP_TIMEOUT_SECONDS = 10
VALID_SECURITY = ('none', 'starttls', 'ssl')
DEFAULT_EVENTS = [
    'Episode Failed', 'Auth Failure', 'Limit Exceeded', 'Rate Limit Structural',
    'Feed Refresh Failed',
]
# Repo layout: <root>/src/email_service.py and <root>/static/ui/logo.png.
# Container layout: /app/src/email_service.py and /app/static/ui/logo.png.
# parent.parent resolves to the right root in both.
LOGO_PATH = Path(__file__).resolve().parent.parent / 'static' / 'ui' / 'logo.png'


@dataclass
class EmailConfig:
    enabled: bool
    events: list
    host: str
    port: int
    security: str
    username: str
    password: Optional[str]
    from_addr: str
    recipients: list


def parse_recipients(raw: str) -> list:
    """Split a comma-separated recipient string into a clean address list."""
    return [part.strip() for part in (raw or '').split(',') if part.strip()]


def load_email_config(db=None) -> EmailConfig:
    """Load the email notification settings; safe defaults for missing rows."""
    if db is None:
        from database import Database  # deferred to avoid circular imports
        db = Database()

    raw_events = db.get_setting('email_events')
    try:
        events = json.loads(raw_events) if raw_events else list(DEFAULT_EVENTS)
        if not isinstance(events, list):
            events = list(DEFAULT_EVENTS)
    except (ValueError, TypeError):
        events = list(DEFAULT_EVENTS)

    try:
        port = int(db.get_setting('email_smtp_port') or 587)
    except (ValueError, TypeError):
        port = 587

    security = (db.get_setting('email_smtp_security') or 'starttls').lower()
    if security not in VALID_SECURITY:
        security = 'starttls'

    return EmailConfig(
        enabled=(db.get_setting('email_enabled') or 'false') == 'true',
        events=events,
        host=(db.get_setting('email_smtp_host') or '').strip(),
        port=port,
        security=security,
        username=(db.get_setting('email_smtp_username') or '').strip(),
        password=db.get_secret('email_smtp_password'),
        from_addr=(db.get_setting('email_smtp_from') or '').strip(),
        recipients=parse_recipients(db.get_setting('email_recipients') or ''),
    )


def is_send_ready(cfg: EmailConfig) -> bool:
    return bool(cfg.enabled and cfg.host and cfg.from_addr and cfg.recipients)


def _value(v) -> str:
    return '-' if v is None or v == '' else str(v)


def _fmt_episode_processed(ctx):
    podcast = ctx.get('podcast', {})
    episode = ctx.get('episode', {})
    subject = (f"[MinusPod] Episode Processed: {podcast.get('name', 'unknown')}"
               f" - {episode.get('title', 'unknown')}")
    rows = [
        ('Podcast', _value(podcast.get('name'))),
        ('Episode', _value(episode.get('title'))),
        ('Ads removed', _value(episode.get('ads_removed'))),
        ('Time saved', _value(episode.get('time_saved'))),
        ('Processing time', _value(episode.get('processing_time'))),
        ('LLM cost', _value(episode.get('llm_cost_display'))),
        ('URL', _value(episode.get('url'))),
        ('Timestamp', _value(ctx.get('timestamp'))),
    ]
    return subject, rows, None


def _fmt_episode_failed(ctx):
    podcast = ctx.get('podcast', {})
    episode = ctx.get('episode', {})
    subject = (f"[MinusPod] Episode Failed: {podcast.get('name', 'unknown')}"
               f" - {episode.get('title', 'unknown')}")
    rows = [
        ('Podcast', f"{_value(podcast.get('name'))} ({_value(podcast.get('slug'))})"),
        ('Episode', _value(episode.get('title'))),
        ('Episode ID', _value(episode.get('id'))),
        ('URL', _value(episode.get('url'))),
        ('Error', _value(episode.get('error_message'))),
        ('Processing time', _value(episode.get('processing_time'))),
        ('LLM cost', _value(episode.get('llm_cost_display'))),
        ('Timestamp', _value(ctx.get('timestamp'))),
    ]
    return subject, rows, None


def _provider_alert_rows(ctx):
    return [
        ('Provider', _value(ctx.get('provider'))),
        ('Model', _value(ctx.get('model'))),
        ('Status code', _value(ctx.get('status_code'))),
        ('Error', _value(ctx.get('error_message'))),
        ('Timestamp', _value(ctx.get('timestamp'))),
    ]


def _fmt_auth_failure(ctx):
    subject = (f"[MinusPod] Auth Failure: {ctx.get('provider', 'unknown')}"
               f" / {ctx.get('model', 'unknown')}")
    return subject, _provider_alert_rows(ctx), \
        'Check or rotate the API key for this provider.'


def _fmt_limit_exceeded(ctx):
    subject = (f"[MinusPod] Limit Exceeded: {ctx.get('provider', 'unknown')}"
               f" / {ctx.get('model', 'unknown')}")
    return subject, _provider_alert_rows(ctx), \
        ('Add credits or raise the limit, then reprocess the '
         'affected episode manually (it will not auto-retry).')


def _fmt_rate_limit_structural(ctx):
    subject = (f"[MinusPod] Structural Rate Limit: {ctx.get('provider', 'unknown')}"
               f" / {ctx.get('model', 'unknown')}")
    rows = [
        ('Provider', _value(ctx.get('provider'))),
        ('Model', _value(ctx.get('model'))),
        ('Per-minute cap', _value(ctx.get('limit'))),
        ('Used this minute', _value(ctx.get('used'))),
        ('Requested', _value(ctx.get('requested'))),
        ('Error', _value(ctx.get('error_message'))),
        ('Timestamp', _value(ctx.get('timestamp'))),
    ]
    return subject, rows, ('Retrying will not help. Shrink the detection window '
                           'or move to a higher provider tier.')


def _fmt_feed_refresh_failed(ctx):
    subject = f"[MinusPod] Feed Refresh Failed: {ctx.get('podcast_name', 'unknown')}"
    rows = [
        ('Podcast', f"{_value(ctx.get('podcast_name'))} ({_value(ctx.get('slug'))})"),
        ('Feed URL', _value(ctx.get('feed_url'))),
        ('Consecutive failures', _value(ctx.get('failure_count'))),
        ('Error', _value(ctx.get('error_message'))),
        ('Timestamp', _value(ctx.get('timestamp'))),
    ]
    return subject, rows, ('Check the podcast feed URL in the feed settings. '
                           'The publisher may have moved the feed, or their '
                           'server may be temporarily down. If the URL loads '
                           'fine, check the MinusPod logs for the error '
                           'detail.')


FORMATTERS = {
    'Episode Processed': _fmt_episode_processed,
    'Episode Failed': _fmt_episode_failed,
    'Auth Failure': _fmt_auth_failure,
    'Limit Exceeded': _fmt_limit_exceeded,
    'Rate Limit Structural': _fmt_rate_limit_structural,
    'Feed Refresh Failed': _fmt_feed_refresh_failed,
}


def _render_plain(rows, action_hint) -> str:
    width = max(len(label) for label, _ in rows) + 1
    lines = [f"{label + ':':<{width}} {value}" for label, value in rows]
    if action_hint:
        lines += ['', f"Action: {action_hint}"]
    return '\n'.join(lines) + '\n'


def _render_html(rows, action_hint, logo_cid: Optional[str]) -> str:
    parts = ['<div style="font-family: sans-serif; max-width: 600px;">']
    if logo_cid:
        parts.append(
            f'<img src="cid:{logo_cid}" alt="MinusPod" height="48" '
            f'style="margin: 0 0 16px 0;">'
        )
    parts.append('<table style="border-collapse: collapse; font-size: 14px;">')
    for label, value in rows:
        parts.append(
            '<tr>'
            f'<td style="padding: 4px 16px 4px 0; color: #666; '
            f'vertical-align: top; white-space: nowrap;">{html.escape(label)}</td>'
            f'<td style="padding: 4px 0;">{html.escape(value)}</td>'
            '</tr>'
        )
    parts.append('</table>')
    if action_hint:
        parts.append(
            f'<p style="font-size: 14px; margin-top: 16px;">'
            f'<strong>Action:</strong> {html.escape(action_hint)}</p>'
        )
    parts.append('</div>')
    return ''.join(parts)


@functools.lru_cache(maxsize=1)
def _logo_bytes() -> Optional[bytes]:
    try:
        return LOGO_PATH.read_bytes()
    except OSError:
        logger.warning("Email logo not found at %s; sending without it", LOGO_PATH)
        return None


def build_message(subject, rows, action_hint, from_addr, recipients) -> EmailMessage:
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = from_addr
    msg['To'] = ', '.join(recipients)
    msg.set_content(_render_plain(rows, action_hint))

    logo = _logo_bytes()
    logo_cid = make_msgid() if logo else None
    msg.add_alternative(
        _render_html(rows, action_hint,
                     logo_cid.strip('<>') if logo_cid else None),
        subtype='html',
    )
    if logo:
        # The html alternative is the last payload part; attach the logo as a
        # related resource so <img src="cid:..."> resolves offline.
        msg.get_payload()[-1].add_related(
            logo, maintype='image', subtype='png', cid=logo_cid)
    return msg


def _send(cfg: EmailConfig, msg: EmailMessage) -> None:
    """Deliver one message; raises on failure. One connection per call.

    Revalidates the host at connect time (not only at save time) so a DNS
    rebind cannot point a saved hostname at a blocked target, matching the
    per-dispatch revalidation webhooks get from safe_post.
    """
    validate_outbound_host(cfg.host, cfg.port)
    context = ssl.create_default_context()
    if cfg.security == 'ssl':
        smtp = smtplib.SMTP_SSL(cfg.host, cfg.port,
                                timeout=SMTP_TIMEOUT_SECONDS, context=context)
    else:
        smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=SMTP_TIMEOUT_SECONDS)
    with smtp:
        if cfg.security == 'starttls':
            smtp.starttls(context=context)
            smtp.ehlo()
        if cfg.username and cfg.password:
            smtp.login(cfg.username, cfg.password)
        elif cfg.username:
            logger.warning(
                "SMTP username is set but no password is available "
                "(missing or not decryptable); connecting without login")
        smtp.send_message(msg)


def send_event_email(event: str, context: dict) -> None:
    """Send a notification email for an event. Never raises."""
    try:
        cfg = load_email_config()
        if not is_send_ready(cfg) or event not in cfg.events:
            return
        formatter = FORMATTERS.get(event)
        if formatter is None:
            return
        subject, rows, action_hint = formatter(context)
        msg = build_message(subject, rows, action_hint,
                            cfg.from_addr, cfg.recipients)
        _send(cfg, msg)
        logger.info("Notification email sent (%s) to %d recipient(s)",
                    event, len(cfg.recipients))
    except Exception as e:
        logger.warning("Notification email failed (%s): %s", event, e)


def send_test_email(db=None):
    """Send a test email using the saved settings.

    Returns (success, message) for the settings test endpoint.
    """
    if db is None:
        from database import Database  # deferred to avoid circular imports
        db = Database()
    cfg = load_email_config(db)
    if not is_send_ready(cfg):
        return False, ('Email notifications are not configured: save an SMTP '
                       'host, from address, and at least one recipient, and '
                       'turn them on first')
    if cfg.username and not cfg.password and db.get_setting('email_smtp_password'):
        return False, ('A password is stored but cannot be decrypted; check '
                       'MINUSPOD_MASTER_PASSPHRASE, then save the password again')
    rows = [
        ('What', 'Test email from MinusPod'),
        ('SMTP server', f"{cfg.host}:{cfg.port} ({cfg.security})"),
        ('Recipients', ', '.join(cfg.recipients)),
    ]
    try:
        msg = build_message('[MinusPod] Test Email', rows,
                            'No action needed. Your email notifications work.',
                            cfg.from_addr, cfg.recipients)
        _send(cfg, msg)
        return True, f"Test email sent to {len(cfg.recipients)} recipient(s)"
    except Exception as e:
        logger.warning("Test email failed: %s", e)
        return False, f"Sending failed: {e}"
