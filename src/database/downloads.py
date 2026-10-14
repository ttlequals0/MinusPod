"""Episode download history and the IP geo cache."""
import json
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode

from utils.text import escape_like
from utils.time import ISO_FORMAT, parse_iso_utc, utc_now_iso

DEDUPE_WINDOW = timedelta(minutes=15)
BURST_WINDOW = timedelta(seconds=60)
GEO_RETRY_AFTER = timedelta(hours=24)
GEO_BATCH_SIZE = 100

REQUEST_HEADERS = ('Range', 'Accept', 'Accept-Encoding', 'Referer', 'If-Range', 'If-None-Match')
REQUEST_LINE_MAX = 500
REQUEST_HEADER_VALUE_MAX = 300
REQUEST_HEADERS_JSON_MAX = 2048

SORT_COLUMNS = {
    'last_seen_at': 'd.last_seen_at',
    'podcast': 'LOWER(COALESCE(p.title_override, p.title, p.slug))',
    'client_ip': 'd.client_ip',
    'location': "LOWER(COALESCE(g.country, '') || ', ' || COALESCE(g.city, ''))",
    'subscriber': "LOWER(COALESCE(k.label, ''))",
}


def _redact_key(url: str) -> str:
    """Rewrite any `key` query value to [redacted]; other parameters are kept."""
    path, sep, query = url.partition('?')
    if not sep:
        return url
    pairs = [(k, '[redacted]' if k == 'key' else v)
             for k, v in parse_qsl(query, keep_blank_values=True)]
    return f"{path}?{urlencode(pairs, safe='[]')}" if pairs else path


def capture_request(method: str, path: str, query_string: str, headers) -> tuple[str, str | None]:
    """Build the stored request line and allowlisted headers JSON, feed key redacted."""
    target = f"{path}?{query_string}" if query_string else path
    line = f"{method} {_redact_key(target)}"[:REQUEST_LINE_MAX]
    picked = {}
    for name in REQUEST_HEADERS:
        value = headers.get(name)
        if value:
            picked[name] = (_redact_key(value) if name == 'Referer' else value)[:REQUEST_HEADER_VALUE_MAX]
    while picked and len(json.dumps(picked)) > REQUEST_HEADERS_JSON_MAX:
        picked.popitem()
    return line, json.dumps(picked) if picked else None


def _shift(stamp: str, delta: timedelta) -> str:
    return (parse_iso_utc(stamp) + delta).strftime(ISO_FORMAT)


def _recent_download(conn, podcast_id, episode_id, client_ip, user_agent, now):
    return conn.execute(
        """SELECT id, last_seen_at FROM episode_downloads
           WHERE podcast_id = ? AND episode_id = ? AND client_ip = ?
             AND user_agent = ? AND last_seen_at >= ?
           ORDER BY last_seen_at DESC LIMIT 1""",
        (podcast_id, episode_id, client_ip, user_agent, _shift(now, -DEDUPE_WINDOW)),
    ).fetchone()


class DownloadHistoryMixin:
    def record_download(self, slug: str, episode_id: str, client_ip: str,
                        user_agent: str, subscriber_key_id: str | None = None,
                        now: str | None = None, request_line: str | None = None,
                        request_headers: str | None = None) -> None:
        """Count a download, merging repeats from one client inside 15 minutes.

        Reads happen outside any transaction; a repeat under 60 s old needs no write.
        Raises on a database error; the serving route swallows it.
        """
        now = now or utc_now_iso()
        conn = self.get_connection()
        podcast = conn.execute(
            "SELECT id FROM podcasts WHERE slug = ?", (slug,)).fetchone()
        if not podcast:
            return
        key = (podcast['id'], episode_id, client_ip, user_agent, now)
        existing = _recent_download(conn, *key)
        if existing and existing['last_seen_at'] >= _shift(now, -BURST_WINDOW):
            return
        with self.transaction(immediate=True) as conn:
            # Re-check under the write lock so concurrent first requests share one row.
            existing = _recent_download(conn, *key)
            if existing:
                conn.execute(
                    """UPDATE episode_downloads
                       SET request_count = request_count + 1, last_seen_at = ?,
                           last_request_line = ?, last_request_headers = ?
                       WHERE id = ?""",
                    (now, request_line, request_headers, existing['id']))
            else:
                conn.execute(
                    """INSERT INTO episode_downloads
                       (podcast_id, episode_id, subscriber_key_id, client_ip,
                        user_agent, first_seen_at, last_seen_at,
                        last_request_line, last_request_headers)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (podcast['id'], episode_id, subscriber_key_id, client_ip,
                     user_agent, now, now, request_line, request_headers))

    def list_downloads(self, page: int, limit: int, sort_by: str, sort_dir: str,
                       podcast_slug: str | None = None,
                       subscriber_key_id: str | None = None,
                       since: str | None = None, until: str | None = None,
                       search: str | None = None) -> dict:
        where, params = [], []
        if podcast_slug:
            where.append("p.slug = ?")
            params.append(podcast_slug)
        if subscriber_key_id:
            where.append("d.subscriber_key_id = ?")
            params.append(subscriber_key_id)
        if since:
            where.append("substr(d.last_seen_at, 1, 10) >= ?")
            params.append(since[:10])
        if until:
            where.append("substr(d.last_seen_at, 1, 10) <= ?")
            params.append(until[:10])
        if search:
            escaped = escape_like(search)
            where.append("(d.client_ip LIKE ? ESCAPE '\\' OR d.user_agent LIKE ? ESCAPE '\\')")
            params.extend([f'%{escaped}%'] * 2)
        clause = f"WHERE {' AND '.join(where)}" if where else ''
        order = SORT_COLUMNS.get(sort_by, SORT_COLUMNS['last_seen_at'])
        direction = 'ASC' if str(sort_dir).lower() == 'asc' else 'DESC'
        join = """FROM episode_downloads d
                  JOIN podcasts p ON p.id = d.podcast_id
                  LEFT JOIN episodes e
                    ON e.podcast_id = d.podcast_id AND e.episode_id = d.episode_id
                  LEFT JOIN feed_subscriber_keys k ON k.id = d.subscriber_key_id
                  LEFT JOIN ip_geo_cache g ON g.ip = d.client_ip"""
        conn = self.get_connection()
        total = conn.execute(
            f"SELECT COUNT(*) FROM episode_downloads d "  # noqa: S608
            f"JOIN podcasts p ON p.id = d.podcast_id {clause}",
            params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT d.id, d.episode_id, d.client_ip, d.user_agent,
                       d.first_seen_at, d.last_seen_at, d.request_count,
                       p.slug AS podcast_slug,
                       COALESCE(p.title_override, p.title, p.slug) AS podcast_title,
                       e.title AS episode_title, k.label AS subscriber_label,
                       d.subscriber_key_id, k.revoked_at AS subscriber_revoked_at,
                       d.last_request_line, d.last_request_headers,
                       g.country, g.region, g.city, g.status AS geo_status
                {join} {clause}
                ORDER BY {order} {direction}, d.id DESC LIMIT ? OFFSET ?""",
            params + [limit, (max(page, 1) - 1) * limit],
        ).fetchall()
        return {'downloads': [{
            'id': r['id'],
            'episodeId': r['episode_id'],
            'episodeTitle': r['episode_title'],
            'podcastSlug': r['podcast_slug'],
            'podcastTitle': r['podcast_title'],
            'clientIp': r['client_ip'],
            'userAgent': r['user_agent'],
            'subscriberLabel': r['subscriber_label'],
            'subscriberKeyId': r['subscriber_key_id'],
            'subscriberRevoked': r['subscriber_revoked_at'] is not None,
            'country': r['country'],
            'region': r['region'],
            'city': r['city'],
            'geoStatus': r['geo_status'],
            'firstSeenAt': r['first_seen_at'],
            'lastSeenAt': r['last_seen_at'],
            'requestCount': r['request_count'],
            'lastRequestLine': r['last_request_line'],
            'lastRequestHeaders': (json.loads(r['last_request_headers'])
                                   if r['last_request_headers'] else None),
        } for r in rows], 'total': total}

    def prune_downloads(self, retention_days: int, now: str | None = None) -> int:
        """Delete download rows last seen before the cutoff, and geo rows no longer referenced."""
        cutoff = _shift(now or utc_now_iso(), -timedelta(days=retention_days))
        with self.transaction(immediate=True) as conn:
            deleted = conn.execute(
                "DELETE FROM episode_downloads WHERE last_seen_at < ?", (cutoff,)
            ).rowcount
            if deleted:
                conn.execute(
                    "DELETE FROM ip_geo_cache "
                    "WHERE ip NOT IN (SELECT client_ip FROM episode_downloads)")
            return deleted

    def pending_geo_ips(self, now: str | None = None) -> list[str]:
        """IPs with no geo row, or a failed row old enough to retry."""
        retry_before = _shift(now or utc_now_iso(), -GEO_RETRY_AFTER)
        rows = self.get_connection().execute(
            """SELECT DISTINCT d.client_ip FROM episode_downloads d
               LEFT JOIN ip_geo_cache g ON g.ip = d.client_ip
               WHERE g.ip IS NULL OR (g.status = 'failed' AND g.looked_up_at < ?)
               LIMIT ?""",
            (retry_before, GEO_BATCH_SIZE)).fetchall()
        return [r['client_ip'] for r in rows]

    def store_geo_many(self, rows: list[tuple], now: str | None = None) -> None:
        """Store (ip, status, country, region, city) tuples in one transaction."""
        stamp = now or utc_now_iso()
        with self.transaction(immediate=True) as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO ip_geo_cache
                   (ip, country, region, city, looked_up_at, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(ip, country, region, city, stamp, status)
                 for ip, status, country, region, city in rows])
