"""Per-feed revocable subscriber credentials."""

import hashlib
import secrets

from utils.time import utc_now_iso


class FeedSubscriberMixin:
    def create_feed_subscriber_key(self, slug: str, label: str = '') -> dict:
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            raise ValueError('podcast not found')
        key_id = secrets.token_hex(8)
        secret = secrets.token_hex(32)
        digest = hashlib.sha256(secret.encode('ascii')).hexdigest()
        created_at = utc_now_iso()
        with self.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO feed_subscriber_keys
                   (id, podcast_id, secret_hash, label, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (key_id, podcast['id'], digest, label.strip()[:100], created_at),
            )
        return {
            'id': key_id, 'token': f'{key_id}.{secret}',
            'label': label.strip()[:100], 'created_at': created_at,
        }

    def verify_feed_subscriber_key(self, slug: str, token: str) -> bool:
        try:
            key_id, secret = token.split('.', 1)
        except (AttributeError, ValueError):
            return False
        if (len(key_id) != 16 or len(secret) != 64
                or any(char not in '0123456789abcdef' for char in key_id + secret)):
            return False
        supplied = hashlib.sha256(secret.encode('ascii')).hexdigest()
        conn = self.get_connection()
        row = conn.execute(
            """SELECT k.secret_hash FROM feed_subscriber_keys k
               JOIN podcasts p ON p.id = k.podcast_id
               WHERE p.slug = ? AND k.id = ? AND k.revoked_at IS NULL""",
            (slug, key_id),
        ).fetchone()
        valid = bool(row and secrets.compare_digest(supplied, row['secret_hash']))
        if valid:
            now = utc_now_iso()
            with self.transaction(immediate=True) as write_conn:
                write_conn.execute(
                    """UPDATE feed_subscriber_keys SET last_used_at = ?
                       WHERE id = ? AND (last_used_at IS NULL OR
                         unixepoch(last_used_at) <= unixepoch(?) - 86400)""",
                    (now, key_id, now),
                )
        return valid

    def list_feed_subscriber_keys(self, slug: str) -> list[dict]:
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT k.id, k.label, k.created_at, k.last_used_at, k.revoked_at
               FROM feed_subscriber_keys k
               JOIN podcasts p ON p.id = k.podcast_id
               WHERE p.slug = ? ORDER BY k.created_at, k.id""",
            (slug,),
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_feed_subscriber_key(self, slug: str, key_id: str) -> bool:
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE feed_subscriber_keys SET revoked_at = ?
                   WHERE id = ? AND revoked_at IS NULL AND podcast_id =
                     (SELECT id FROM podcasts WHERE slug = ?)""",
                (utc_now_iso(), key_id, slug),
            )
            return cursor.rowcount == 1
