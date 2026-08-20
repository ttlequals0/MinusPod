"""IP-pinning transport: resolve once, validate, connect to the pinned IP."""
import datetime
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from utils import safe_http
from utils.pinned_transport import PinnedHTTPAdapter
from utils.safe_http import URLTrust, safe_get
from utils.url import SSRFError


def _addrinfo(ip, port):
    fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
    addr = (ip, port, 0, 0) if fam == socket.AF_INET6 else (ip, port)
    return [(fam, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", addr)]


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        _OkHandler.seen_host = self.headers.get("Host")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_rebind_between_validate_and_connect_is_blocked(monkeypatch):
    # First resolution (validate_url) sees a public IP, second (the
    # adapter's pin) sees a private one: the TOCTOU the audit flagged.
    calls = {"n": 0}
    real = socket.getaddrinfo

    def flip(host, port, *a, **kw):
        if host == "rebind.example":
            calls["n"] += 1
            ip = "93.184.216.34" if calls["n"] == 1 else "10.0.0.1"
            return _addrinfo(ip, port or 80)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", flip)
    with pytest.raises(SSRFError):
        safe_get("http://rebind.example/x", trust=URLTrust.FEED_CONTENT,
                 timeout=2)


def test_pinned_connect_hits_resolved_ip_and_preserves_host_header(monkeypatch):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers.get("Host")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        real = socket.getaddrinfo

        def fake(host, p, *a, **kw):
            if host == "pinned.example":
                return _addrinfo("127.0.0.1", p or port)
            return real(host, p, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        # OPERATOR tier allows loopback, so the pin itself is what is
        # under test here.
        resp = safe_get(f"http://pinned.example:{port}/x",
                        trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
        assert resp.status_code == 200
        assert seen["host"] == f"pinned.example:{port}"
        assert urlparse(resp.url).hostname == "pinned.example"
    finally:
        srv.shutdown()


def test_ip_literal_url_still_validated(monkeypatch):
    with pytest.raises(SSRFError):
        safe_get("http://169.254.169.254/latest", trust=URLTrust.FEED_CONTENT,
                 timeout=2)


def test_kill_switch_reverts_to_stock_adapter(monkeypatch):
    monkeypatch.setenv("SSRF_IP_PINNING", "false")
    s = safe_http._RevalidatingSession(URLTrust.FEED_CONTENT, 3)
    assert not isinstance(s.get_adapter("https://x"), PinnedHTTPAdapter)


def test_pinning_mounted_by_default():
    s = safe_http._RevalidatingSession(URLTrust.FEED_CONTENT, 3)
    assert isinstance(s.get_adapter("https://x"), PinnedHTTPAdapter)


def test_all_resolved_ips_validated_not_only_the_pinned_one(monkeypatch):
    # Public first, private second: the second must still block the fetch.
    real = socket.getaddrinfo

    def multi(host, port, *a, **kw):
        if host == "multi.example":
            return (_addrinfo("93.184.216.34", port or 80)
                    + _addrinfo("192.168.1.5", port or 80))
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", multi)
    with pytest.raises(SSRFError):
        safe_get("http://multi.example/x", trust=URLTrust.FEED_CONTENT,
                 timeout=2)


def _pool_for(adapter, url, monkeypatch):
    """Send url through the adapter, capturing the urllib3 pool it selects."""
    captured = {}

    def fake_parent_send(self, request, **kwargs):
        captured["pool"] = adapter.get_connection_with_tls_context(request, True)
        return "sentinel"

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_parent_send)
    req = requests.Request("GET", url).prepare()
    adapter.send(req)
    return req, captured["pool"]


def test_ipv6_resolution_is_pinned_on_the_pool(monkeypatch):

    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host == "v6.example":
            return _addrinfo("::1", port or 80)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    adapter = PinnedHTTPAdapter(allow_private=True)
    req, pool = _pool_for(adapter, "http://v6.example:8080/x", monkeypatch)
    assert pool.host == "::1"
    assert pool.port == 8080
    assert req.headers["Host"] == "v6.example:8080"


def test_https_pool_carries_server_hostname_of_the_original_host(monkeypatch):

    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host == "tls.example":
            return _addrinfo("93.184.216.34", port or 443)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    adapter = PinnedHTTPAdapter(allow_private=False)
    req, pool = _pool_for(adapter, "https://tls.example/x", monkeypatch)
    assert pool.host == "93.184.216.34"
    assert pool.conn_kw["server_hostname"] == "tls.example"
    assert req.headers["Host"] == "tls.example"


def test_proxy_configured_skips_pinning(monkeypatch):

    adapter = PinnedHTTPAdapter(allow_private=False)
    resolved = []
    real = socket.getaddrinfo

    def counting(host, port, *a, **kw):
        resolved.append(host)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    sent = {}

    def fake_parent_send(self, request, **kwargs):
        sent["pin"] = adapter._pin
        sent["host_header"] = request.headers.get("Host")
        return "sentinel"

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        fake_parent_send)
    req = requests.Request("GET", "http://proxied.example/x").prepare()
    result = adapter.send(req, proxies={"http": "http://proxy.invalid:3128"})
    assert result == "sentinel"
    assert sent["pin"] is None
    assert sent["host_header"] is None
    assert resolved == []


def test_stale_host_header_dropped_when_a_later_hop_is_not_pinned(monkeypatch):

    adapter = PinnedHTTPAdapter(allow_private=True)
    real = socket.getaddrinfo

    def fake(host, port, *a, **kw):
        if host == "hop1.example":
            return _addrinfo("127.0.0.1", port or 80)
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    seen = []

    def fake_parent_send(self, request, **kwargs):
        seen.append(request.headers.get("Host"))
        return "sentinel"

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send",
                        fake_parent_send)
    req = requests.Request("GET", "http://hop1.example/a").prepare()
    adapter.send(req)
    # Same PreparedRequest object reused for the next hop, as requests does
    # when it copies headers forward across a redirect.
    req.url = "http://127.0.0.1:8080/b"
    adapter.send(req)
    assert seen == ["hop1.example", None]


def _issue_cert(tmp_path, hostname):
    """Self-signed CA plus a leaf for hostname. Returns (ca_pem, cert_pem, key_pem)."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pin-test-ca")])
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_pem = tmp_path / f"ca-{hostname}.pem"
    ca_pem.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_pem = tmp_path / f"leaf-{hostname}.pem"
    cert_pem.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_pem = tmp_path / f"leaf-{hostname}.key"
    key_pem.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_pem, cert_pem, key_pem


def _https_server(cert_pem, key_pem, sni_seen):
    srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_pem), str(key_pem))

    def record_sni(sock, server_name, context):
        sni_seen.append(server_name)

    ctx.sni_callback = record_sni
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


def _record_pool_hosts(monkeypatch):
    """Record (pool host, server_hostname) for every connection the pin builds."""

    seen = []
    original = PinnedHTTPAdapter.build_connection_pool_key_attributes

    def spy(self, request, verify, cert=None):
        host_params, pool_kwargs = original(self, request, verify, cert)
        seen.append((host_params["host"], pool_kwargs.get("server_hostname")))
        return host_params, pool_kwargs

    monkeypatch.setattr(
        PinnedHTTPAdapter, "build_connection_pool_key_attributes", spy
    )
    return seen


def test_https_pin_keeps_sni_and_cert_verification_on_hostname(
    monkeypatch, tmp_path
):
    ca_pem, cert_pem, key_pem = _issue_cert(tmp_path, "tls.example")
    sni_seen = []
    srv = _https_server(cert_pem, key_pem, sni_seen)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        real = socket.getaddrinfo

        def fake(host, p, *a, **kw):
            if host == "tls.example":
                return _addrinfo("127.0.0.1", p or port)
            return real(host, p, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_pem))
        pools = _record_pool_hosts(monkeypatch)
        resp = safe_get(f"https://tls.example:{port}/x",
                        trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
        assert resp.status_code == 200
        # Connected to the pinned IP, yet SNI carried the hostname and the
        # handshake only verifies because the cert matches that hostname.
        assert pools == [("127.0.0.1", "tls.example")]
        assert sni_seen == ["tls.example"]
    finally:
        srv.shutdown()


def test_https_pin_rejects_cert_for_a_different_hostname(monkeypatch, tmp_path):
    ca_pem, cert_pem, key_pem = _issue_cert(tmp_path, "other.example")
    sni_seen = []
    srv = _https_server(cert_pem, key_pem, sni_seen)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        real = socket.getaddrinfo

        def fake(host, p, *a, **kw):
            if host == "tls.example":
                return _addrinfo("127.0.0.1", p or port)
            return real(host, p, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_pem))
        with pytest.raises(requests.exceptions.SSLError):
            safe_get(f"https://tls.example:{port}/x",
                     trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
    finally:
        srv.shutdown()


def test_redirect_hop_repins_and_rewrites_the_host_header(monkeypatch):
    hosts = []

    class Final(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    final = HTTPServer(("127.0.0.1", 0), Final)
    final_port = final.server_address[1]

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            hosts.append(self.headers.get("Host"))
            self.send_response(302)
            self.send_header(
                "Location", f"http://beta.example:{final_port}/final"
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    first = HTTPServer(("127.0.0.1", 0), Redirector)
    first_port = first.server_address[1]
    threading.Thread(target=first.serve_forever, daemon=True).start()
    threading.Thread(target=final.serve_forever, daemon=True).start()
    try:
        real = socket.getaddrinfo

        def fake(host, p, *a, **kw):
            if host in ("alpha.example", "beta.example"):
                return _addrinfo("127.0.0.1", p or 80)
            return real(host, p, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        resp = safe_get(f"http://alpha.example:{first_port}/start",
                        trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
        assert resp.status_code == 200
        assert hosts == [f"alpha.example:{first_port}",
                         f"beta.example:{final_port}"]
        assert resp.url == f"http://beta.example:{final_port}/final"
    finally:
        first.shutdown()
        final.shutdown()


def test_unresolvable_operator_host_is_a_connection_error(monkeypatch):
    # validate_outbound_host lets unresolvable names through, so the adapter
    # must report a network failure, not a policy block: callers retry one and
    # give up on the other.
    real = socket.getaddrinfo

    def fail(host, port, *a, **kw):
        if host == "nxdomain.example":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(requests.exceptions.ConnectionError) as excinfo:
        safe_get("http://nxdomain.example/x",
                 trust=URLTrust.OPERATOR_CONFIGURED, timeout=2)
    assert not isinstance(excinfo.value, SSRFError)


def test_unresolvable_feed_host_still_blocked_by_the_pre_check(monkeypatch):
    real = socket.getaddrinfo

    def fail(host, port, *a, **kw):
        if host == "nxdomain.example":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(SSRFError):
        safe_get("http://nxdomain.example/x", trust=URLTrust.FEED_CONTENT,
                 timeout=2)


def test_empty_resolution_is_a_connection_error(monkeypatch):

    real = socket.getaddrinfo

    def empty(host, port, *a, **kw):
        if host == "empty.example":
            return []
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", empty)
    adapter = PinnedHTTPAdapter(allow_private=True)
    req = requests.Request("GET", "http://empty.example/x").prepare()
    with pytest.raises(requests.exceptions.ConnectionError):
        adapter.send(req)


def test_adapter_validates_every_address_the_second_lookup_returns(monkeypatch):
    # The pre-check sees one public address; the adapter's own lookup returns
    # that address plus a private one. Only the adapter's validate-all loop
    # catches this.
    calls = {"n": 0}
    real = socket.getaddrinfo

    def widen(host, port, *a, **kw):
        if host == "widen.example":
            calls["n"] += 1
            if calls["n"] == 1:
                return _addrinfo("93.184.216.34", port or 80)
            return (_addrinfo("93.184.216.34", port or 80)
                    + _addrinfo("10.1.2.3", port or 80))
        return real(host, port, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", widen)
    with pytest.raises(SSRFError):
        safe_get("http://widen.example/x", trust=URLTrust.FEED_CONTENT,
                 timeout=2)


def test_pin_that_does_not_match_the_request_host_raises():

    adapter = PinnedHTTPAdapter(allow_private=True)
    adapter._pin = ("stale.example", "127.0.0.1")
    req = requests.Request("GET", "http://other.example/x").prepare()
    with pytest.raises(RuntimeError):
        adapter.build_connection_pool_key_attributes(req, True)


@pytest.mark.parametrize("value", ["false", "FALSE", " off ", "0", "no"])
def test_kill_switch_accepts_the_usual_falsey_spellings(monkeypatch, value):
    monkeypatch.setenv("SSRF_IP_PINNING", value)
    s = safe_http._RevalidatingSession(URLTrust.FEED_CONTENT, 3)
    assert not isinstance(s.get_adapter("https://x"), PinnedHTTPAdapter)


def test_connect_failure_falls_back_to_the_next_validated_address(monkeypatch):
    # First validated address is a dead loopback IP, second is the live
    # server: the request must succeed via the second.
    srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        real = socket.getaddrinfo

        def fake(host, p, *a, **kw):
            if host == "failover.example":
                return (_addrinfo("127.0.0.2", p or port)
                        + _addrinfo("127.0.0.1", p or port))
            return real(host, p, *a, **kw)

        monkeypatch.setattr(socket, "getaddrinfo", fake)
        resp = safe_get(f"http://failover.example:{port}/x",
                        trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
        assert resp.status_code == 200
        assert resp.content == b"ok"
        assert _OkHandler.seen_host == f"failover.example:{port}"
    finally:
        srv.shutdown()


def test_all_validated_addresses_dead_raises_connection_error(monkeypatch):
    # Nothing is listening on either address; the original failure surfaces.
    srv = HTTPServer(("127.0.0.1", 0), _OkHandler)
    port = srv.server_address[1]
    srv.server_close()
    real = socket.getaddrinfo

    def fake(host, p, *a, **kw):
        if host == "alldead.example":
            return (_addrinfo("127.0.0.2", p or port)
                    + _addrinfo("127.0.0.3", p or port))
        return real(host, p, *a, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(requests.exceptions.ConnectionError):
        safe_get(f"http://alldead.example:{port}/x",
                 trust=URLTrust.OPERATOR_CONFIGURED, timeout=5)
