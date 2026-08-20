"""HTTPAdapter that pins the DNS resolution used for the actual connect.

Closes the SSRF DNS-rebinding TOCTOU: validate_url vetted the addresses a
hostname resolved to, but requests resolved it again at connect time, so a
flipping record could pass validation with a public IP and connect to a
private one. Each request and redirect hop now resolves once, validates every
returned address, and connects to the validated addresses in order (falling
back to the next one only when a connect fails), while request.url keeps the
hostname and SNI plus certificate verification stay on it too.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.utils import select_proxy

from utils.url import check_resolved_ip, resolve_and_check


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class PinnedHTTPAdapter(HTTPAdapter):
    """Resolves once per hop, validates every address, connects to them in order."""

    def __init__(self, allow_private: bool, **kwargs):
        self._allow_private = allow_private
        # (hostname, ip) for the in-flight hop; sessions are per-call and hops
        # sequential, so plain attribute state is safe.
        self._pin: tuple[str, str] | None = None
        self._injected_host = False
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        self._pin = None
        if self._injected_host:
            # Drop the previous hop's Host before deciding on this one.
            request.headers.pop("Host", None)
            self._injected_host = False

        parsed = urlparse(request.url)
        host = parsed.hostname or ""
        scheme = parsed.scheme.lower()

        if select_proxy(request.url, kwargs.get("proxies")):
            # The proxy resolves the origin itself, so there is nothing to pin.
            return super().send(request, **kwargs)

        if _is_ip_literal(host):
            check_resolved_ip(host, allow_private=self._allow_private)
            return super().send(request, **kwargs)

        default_port = 443 if scheme == "https" else 80
        port = parsed.port or default_port
        addresses = self._resolve_validated(host, port)
        if "Host" not in request.headers:
            suffix = "" if port == default_port else f":{port}"
            request.headers["Host"] = f"{host}{suffix}"
            self._injected_host = True

        # Connect fallback: every address here already passed SSRF validation,
        # so a dead first address may fail over to the next instead of failing
        # the whole request. ConnectTimeout subclasses ConnectionError, and
        # neither is raised once a response has been received.
        first_error = None
        for ip in addresses:
            self._pin = (host, ip)
            try:
                return super().send(request, **kwargs)
            except RequestsConnectionError as exc:
                if first_error is None:
                    first_error = exc
        raise first_error

    def _resolve_validated(self, host: str, port: int) -> list[str]:
        # A name that does not resolve reaches nothing, so it is a network
        # failure and not an SSRF verdict: callers retry the former.
        try:
            infos = resolve_and_check(host, port, allow_private=self._allow_private)
        except socket.gaierror as exc:
            raise RequestsConnectionError(
                f"Cannot resolve hostname: {host!r}"
            ) from exc
        # Deduped so each distinct address is tried at most once.
        return list(dict.fromkeys(info[4][0] for info in infos))

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        """Swap the pool host for the pinned IP, keeping TLS on the hostname."""
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        if not self._pin:
            return host_params, pool_kwargs
        host, ip = self._pin
        if host_params.get("host") != host:
            raise RuntimeError(
                f"Pinned host {host!r} does not match request host "
                f"{host_params.get('host')!r}"
            )
        host_params["host"] = ip
        if host_params.get("scheme") == "https":
            # Keeps SNI and cert checks on the real hostname, and is part of
            # the urllib3 pool key so hosts sharing an IP get separate pools.
            pool_kwargs["server_hostname"] = host
        return host_params, pool_kwargs
