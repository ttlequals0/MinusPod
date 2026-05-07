"""MinusPod cookie auth + cached session."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

import httpx

from .config import MinusPodConfig, secret

logger = logging.getLogger(__name__)


SESSION_TTL_SECONDS = 23 * 60 * 60


@dataclass
class Session:
    cookies: dict[str, str]
    created_at: float

    def is_fresh(self) -> bool:
        return time.time() - self.created_at < SESSION_TTL_SECONDS


class AuthError(RuntimeError):
    pass


def acquire(cfg: MinusPodConfig, *, force_login: bool = False) -> Session:
    cache = cfg.session_cache_path
    if not force_login and cache.is_file():
        try:
            sess = _read_cache(cache)
            if sess.is_fresh() and _probe(cfg.base_url, sess.cookies):
                return sess
        except Exception as e:
            logger.warning("Session cache invalid (%s); re-logging in", e)

    sess = _login(cfg)
    _write_cache(cache, sess)
    return sess


def _read_cache(path: Path) -> Session:
    data = json.loads(path.read_text())
    return Session(cookies=data["cookies"], created_at=float(data["created_at"]))


def _write_cache(path: Path, sess: Session) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookies": sess.cookies, "created_at": sess.created_at}))
    path.chmod(0o600)


def _probe(base_url: str, cookies: dict[str, str]) -> bool:
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/v1/feeds", cookies=cookies, timeout=10)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _login(cfg: MinusPodConfig) -> Session:
    password = secret(cfg.password_env)
    r = httpx.post(
        f"{cfg.base_url.rstrip('/')}/api/v1/auth/login",
        json={"password": password},
        timeout=15,
    )
    if r.status_code == 429:
        raise AuthError(
            "Rate-limited by /auth/login (3/min, 10/hour). Wait before retrying — auto-retry risks lockout."
        )
    if r.status_code != 200:
        raise AuthError(f"Login failed: HTTP {r.status_code} {r.text[:200]}")
    cookies = dict(r.cookies)
    if not cookies:
        raise AuthError("Login returned 200 but set no cookies")
    return Session(cookies=cookies, created_at=time.time())
