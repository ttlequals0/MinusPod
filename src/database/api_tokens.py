"""Long-lived API tokens for scripts. Only a SHA-256 hash is stored."""

import hashlib
import logging
import re
import secrets

from utils.time import parse_iso_utc, utc_now_iso

logger = logging.getLogger(__name__)

TOKEN_PREFIX = 'mp_'
# token_urlsafe(32) yields 43 characters.
TOKEN_PATTERN = re.compile(r'mp_[A-Za-z0-9_-]{43}')
MAX_ACTIVE_TOKENS = 25
MAX_NAME_LENGTH = 64
LAST_USED_REFRESH_SECONDS = 300


class ApiTokenError(ValueError):
    """Creation refused; args[0] is a client-safe message."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class ApiTokenMixin:
    def create_api_token(self, name: str) -> dict:
        """Create a token; the plaintext is in the result and nowhere else."""
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        prefix = token[len(TOKEN_PREFIX):len(TOKEN_PREFIX) + 8]
        created_at = utc_now_iso()
        with self.transaction(immediate=True) as conn:
            active = conn.execute(
                "SELECT name FROM api_tokens WHERE revoked_at IS NULL").fetchall()
            if len(active) >= MAX_ACTIVE_TOKENS:
                raise ApiTokenError(
                    f'At most {MAX_ACTIVE_TOKENS} active API tokens are allowed; revoke one first')
            if any(row['name'].lower() == name.lower() for row in active):
                raise ApiTokenError('An active API token already has that name')
            cursor = conn.execute(
                """INSERT INTO api_tokens (name, token_hash, token_prefix, created_at)
                   VALUES (?, ?, ?, ?)""",
                (name, hash_token(token), prefix, created_at))
            token_id = cursor.lastrowid
        return {'id': token_id, 'name': name, 'prefix': prefix,
                'createdAt': created_at, 'lastUsedAt': None, 'token': token}

    def list_api_tokens(self) -> list[dict]:
        rows = self.get_connection().execute(
            """SELECT id, name, token_prefix, created_at, last_used_at
               FROM api_tokens WHERE revoked_at IS NULL ORDER BY created_at, id"""
        ).fetchall()
        return [{'id': r['id'], 'name': r['name'], 'prefix': r['token_prefix'],
                 'createdAt': r['created_at'], 'lastUsedAt': r['last_used_at']}
                for r in rows]

    def revoke_api_token(self, token_id: int) -> bool:
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (utc_now_iso(), token_id))
            return cursor.rowcount == 1

    def api_token_active(self, token_id: int) -> bool:
        return self.get_connection().execute(
            "SELECT 1 FROM api_tokens WHERE id = ? AND revoked_at IS NULL", (token_id,)
        ).fetchone() is not None

    def revoke_all_api_tokens(self) -> int:
        """Revoke every active token; returns how many were revoked."""
        with self.transaction(immediate=True) as conn:
            return conn.execute(
                "UPDATE api_tokens SET revoked_at = ? WHERE revoked_at IS NULL",
                (utc_now_iso(),)).rowcount

    def verify_api_token(self, token: str) -> int | None:
        """Id of the active token matching token, else None. Stamps last use at most every 5 minutes."""
        if not TOKEN_PATTERN.fullmatch(token):
            return None
        digest = hash_token(token)
        row = self.get_connection().execute(
            "SELECT id, last_used_at FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
            (digest,)).fetchone()
        if row is None:
            return None
        self._stamp_api_token(row['id'], row['last_used_at'])
        return row['id']

    def _stamp_api_token(self, token_id: int, last_used_at: str | None) -> None:
        now = utc_now_iso()
        last = parse_iso_utc(last_used_at)
        # Skip the write lock when the row we just read is still fresh; the SQL guard covers other workers
        if last is not None and (parse_iso_utc(now) - last).total_seconds() < LAST_USED_REFRESH_SECONDS:
            return
        try:
            with self.transaction(immediate=True) as conn:
                conn.execute(
                    """UPDATE api_tokens SET last_used_at = ?
                       WHERE id = ? AND (last_used_at IS NULL
                         OR unixepoch(last_used_at) <= unixepoch(?) - ?)""",
                    (now, token_id, now, LAST_USED_REFRESH_SECONDS))
        except Exception as exc:
            logger.warning("API token last-use stamp failed: %s", type(exc).__name__)
