"""Input validation: tiered slug / episode ID validators and public-IP classification for lockout.

Strict validators gate write paths (feed creation, admin input). Permissive
``is_dangerous_*`` validators gate read paths so existing subscription URLs keep
working; they reject only traversal attempts.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from functools import wraps
from typing import Final

from flask import abort

_route_logger = logging.getLogger(__name__)

# Generous cap: slugs auto-generated from long titles often exceed 64 chars.
# The regex still refuses traversal characters; length only stops abuse.
SLUG_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
EPISODE_ID_RE: Final = re.compile(r"^[a-f0-9]{12}$")
LOCAL_EPISODE_ID_RE: Final = re.compile(r"^s\d{2,3}e\d{2,4}$")

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
        "opml",
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
    # Subscribed feeds mint 12-char MD5 hex prefixes; local feeds mint
    # sNNeNN ids. Both shapes are load-bearing for path containment.
    if not isinstance(value, str):
        return False
    return bool(EPISODE_ID_RE.match(value) or LOCAL_EPISODE_ID_RE.match(value))


def is_dangerous_slug(value: str) -> bool:
    """Returns True only for traversal attempts.

    Lets legacy subscription URLs that fail the strict regex (uppercase,
    underscores) through, blocking ``..``, slashes, backslashes, null bytes and
    URL-encoded variants.
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
    IPv4 CGNAT (100.64.0.0/10), the Tailscale IPv6 ULA and IPv6 discard prefixes.
    Malformed input returns False so bad headers never trigger lockout.
    """
    if not isinstance(addr, str) or not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False

    # Normalize IPv4-mapped IPv6 (::ffff:a.b.c.d) to the embedded IPv4 so a
    # public address presented in mapped form is classified on its real IPv4
    # properties and cannot evade login lockout (midsize-backend-1).
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped

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

    Rejects dangerous slugs with 404 (indistinguishable from not found) via
    :func:`is_dangerous_slug`, which keeps legacy URLs (uppercase, underscores, dots) working.
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

    Rejects Unicode lookalike episode IDs (``.isalnum()`` accepts Cyrillic, Greek,
    full-width digits); real IDs are strictly ``[0-9a-f]{12}``.
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


def nullable_bool(value):
    """DB int 1/0/None -> True/False/None; None keeps 'inherit' semantics."""
    return None if value is None else bool(value)
