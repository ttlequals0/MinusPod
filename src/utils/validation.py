"""Input validation primitives: tiered slug / episode ID validators and
public-IP classification for login lockout.

Tiering is deliberate. Strict validators gate write paths (feed creation,
admin input). Permissive ``is_dangerous_*`` validators gate read paths so
existing podcast-app subscription URLs keep working; they reject only inputs
that are actually traversal attempts.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from functools import wraps
from typing import Final

from flask import abort

_route_logger = logging.getLogger(__name__)

# Cap is generous — real-world podcast slugs auto-generated from long
# titles routinely exceed the prior 64-char limit. The strict regex still
# refuses path-traversal characters; length is bounded only to stop
# obvious abuse, not to gate legitimate slugs.
SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
EPISODE_ID_RE: Final = re.compile(r"^[a-f0-9]{12}$")

RESERVED_SLUGS: Final = frozenset(
    {
        "api",
        "ui",
        "docs",
        "openapi.yaml",
        "health",
        "static",
        "assets",
        "favicon.ico",
        "apple-touch-icon.png",
        "apple-touch-icon-precomposed.png",
        "robots.txt",
        "admin",
        "auth",
        "login",
        "logout",
        "settings",
        "episodes",
    }
)

_DANGEROUS_SLUG_RE: Final = re.compile(r"(?:\.\.|[/\\]|\x00|%2f|%5c|%00)", re.IGNORECASE)


def is_valid_slug(value: str) -> bool:
    if not isinstance(value, str):
        return False
    if value in RESERVED_SLUGS:
        return False
    return bool(SLUG_RE.match(value))


def is_valid_episode_id(value: str) -> bool:
    # Real episode IDs are 12-char MD5 hex prefixes; the shape is load-bearing.
    if not isinstance(value, str):
        return False
    return bool(EPISODE_ID_RE.match(value))


def is_dangerous_slug(value: str) -> bool:
    """Returns True only for traversal attempts.

    Preserves legacy podcast-app subscription URLs that may not pass the
    strict regex (uppercase, underscores, etc.) while still blocking ``..``,
    slashes, backslashes, null bytes, and common URL-encoded variants.
    """
    if not isinstance(value, str) or not value:
        return True
    return bool(_DANGEROUS_SLUG_RE.search(value))


_IPV6_DISCARD = ipaddress.ip_network("100::/64")
_IPV6_TAILSCALE = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_IPV4_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_public_ip_for_lockout(addr: str) -> bool:
    """True only if ``addr`` is internet-routable for login-lockout purposes.

    Excludes RFC1918, loopback, link-local, multicast, reserved, unspecified,
    IPv4 CGNAT (100.64.0.0/10), the Tailscale IPv6 ULA prefix, and the IPv6
    discard prefix. Malformed input returns False so lockout never fires on
    bad headers.
    """
    if not isinstance(addr, str) or not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False

    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_private
    ):
        return False

    if isinstance(ip, ipaddress.IPv4Address) and ip in _IPV4_CGNAT:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        if ip in _IPV6_DISCARD or ip in _IPV6_TAILSCALE:
            return False

    return True


def validate_slug_param(f):
    """Route decorator for app-level ``<slug>`` routes.

    Rejects dangerous slugs with 404 so probing looks the same as "not
    found". Preserves legacy podcast-app subscription URLs (uppercase,
    underscores, embedded dots) by delegating to :func:`is_dangerous_slug`.
    """
    @wraps(f)
    def wrapper(slug, *args, **kwargs):
        if is_dangerous_slug(slug):
            _route_logger.warning("Dangerous slug rejected: %r", slug)
            abort(404)
        return f(slug, *args, **kwargs)
    return wrapper


def validate_slug_and_episode_params(f):
    """Route decorator for app-level routes with both ``<slug>`` and
    ``<episode_id>`` path parameters.

    Rejects Unicode lookalike episode IDs (``.isalnum()`` accepts
    Cyrillic, Greek, full-width digits, etc. -- real MD5-hex episode IDs
    are strictly ``[0-9a-f]{12}``).
    """
    @wraps(f)
    def wrapper(slug, episode_id, *args, **kwargs):
        if is_dangerous_slug(slug):
            _route_logger.warning("Dangerous slug rejected: %r", slug)
            abort(404)
        if not is_valid_episode_id(episode_id):
            _route_logger.warning(
                "Invalid episode ID rejected: slug=%r id=%r", slug, episode_id
            )
            abort(404)
        return f(slug, episode_id, *args, **kwargs)
    return wrapper
