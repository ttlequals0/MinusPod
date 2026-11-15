"""Show-bound community segments: exporter, receiver sync, binding, pipeline hooks, states.
Off unless `community_show_segments` is on; markers are touched only through `annotate` and `enforce_holds`."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import requests

import community_segments_core as core
from community_export import find_foreign_sponsors
from config import (HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT, HOLD_REASON_SHOW_SEGMENT_SUGGESTION,
                    known_ad_evidence, normalize_segment_category,
                    resolve_community_sync_categories)
from database.podcasts import is_local_feed
from tools.show_segment_validator import LIBRARY_CATEGORIES, SHOW_BOUND_CATEGORIES, entry_problems
from utils.community_tags import COMMUNITY_PATTERN_BASE_URL, app_version
from utils.constants import SHOW_PROMO_CREATED_BY, SHOW_PROMO_DISABLE_REJECTS, is_show_promo_pattern
from utils.cron import is_due
from utils.feed_guid import compute_feed_guid
from utils.markers import ensure_hold_id
from utils.safe_http import (IncompleteResponseError, ResponseTooLargeError, URLTrust, read_response_capped,
                             safe_get)
from utils.short_asks import clamp_short_ask, feed_snips_short_asks, is_short_show_template
from utils.text import get_transcript_text_for_range, parse_transcript_segments
from utils.time import overlap_seconds, parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger('podcast.community_segments')

SETTING = 'community_show_segments'
LAST_SYNC = 'community_segments_last_run'
LAST_SUMMARY = 'community_segments_last_summary'
LAST_PROBE = 'community_segments_last_probe'
PROBE_INTERVAL = timedelta(hours=1)
SHOW_SEGMENT_CREATED_BY = 'show_segment'
SEGMENT_BASE_URL = COMMUNITY_PATTERN_BASE_URL.rsplit('community/', 1)[0] + 'show-segments/'
FILE_MAX_BYTES = 64 * 1024
HEAD_MAX_BYTES = 256 * 1024
HTTP_TIMEOUT = 20
IDENTITY_TTL = timedelta(days=7)

# Receiver rules (spec §4, §7).
BIND_MIN_TRANSCRIPTS = 2
BIND_SCAN_TRANSCRIPTS = 10
N_LOCAL = 3
UNRESOLVED_CAP = 3
SUGGESTIONS_PER_EPISODE = 2
BOUND_PER_CATEGORY = 6
BOUND_PER_FEED = 50
CONFIDENCE_BUMP = 0.10
OVERLAP = 0.5
MATCHING_STATES = ('candidate', 'validated')
# Exporter rules (spec §6).
EXPORT_MIN_CONFIRMS = 5
EXPORT_MIN_EPISODES = 5
EXPORT_MIN_SPAN_DAYS = 14
EXPORT_MAX_AGE_DAYS = 180
_TOKEN_QUERY = re.compile(r'(token|key|auth|secret|sig|signature|pass|password|session|uid|user|id)$', re.I)


def is_enabled(db) -> bool:
    return db.get_setting_bool(SETTING, default=False)


def imports_enabled(db) -> bool:
    return is_enabled(db) and db.get_setting_bool('community_sync_enabled', default=False)


def accepted_categories(db) -> set[str]:
    """communitySyncCategories, intersected with the show-bound categories."""
    return set(resolve_community_sync_categories(db.get_setting('community_sync_categories'))) & set(
        SHOW_BOUND_CATEGORIES)


# ---------------------------------------------------------------- store

def _rows(db, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in db.get_connection().execute(sql, params).fetchall()]


def _execute(db, sql: str, params: tuple = ()):
    conn = db.get_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


def _write(db, sql: str, params: tuple = ()) -> int:
    return _execute(db, sql, params).lastrowid


def body(entry: dict) -> dict:
    return json.loads(entry['body_json'])


def get_entries(db, **where) -> list[dict]:
    clauses = ' AND '.join(f'{k} = ?' for k in where) or '1'
    return _rows(db, f'SELECT * FROM community_segment_entries WHERE {clauses} ORDER BY id', tuple(where.values()))  # noqa: S608  # column names from internal callers, values bound


def get_entry(db, entry_id: int) -> dict | None:
    rows = get_entries(db, id=entry_id)
    return rows[0] if rows else None


def update_entry(db, entry_id: int, **fields) -> None:
    sets = ', '.join(f'{k} = ?' for k in fields)
    _write(db, f'UPDATE community_segment_entries SET {sets} WHERE id = ?', (*fields.values(), entry_id))  # noqa: S608  # column names from internal callers, values bound


def add_event(db, entry_id: int, podcast_id: int | None, episode_id: str, kind: str,
              hold_id: str | None = None, correction_id: int | None = None) -> bool:
    """True when the event is new; '' instead of NULL so the UNIQUE key dedupes events that carry no hold."""
    return _execute(db, 'INSERT OR IGNORE INTO community_segment_events '
               '(entry_id, podcast_id, episode_id, kind, hold_id, correction_id) VALUES (?, ?, ?, ?, ?, ?)',
           (entry_id, podcast_id, episode_id or '', kind, hold_id or '', correction_id)).rowcount > 0


def events(db, entry_id: int, *kinds: str) -> list[dict]:
    marks = ', '.join('?' * len(kinds))
    return _rows(db, f'SELECT * FROM community_segment_events WHERE entry_id = ? AND kind IN ({marks}) ORDER BY id',  # noqa: S608  # placeholders only, values bound
                 (entry_id, *kinds))


def delete_entries(db, **where) -> int:
    if not where:
        raise ValueError('delete_entries needs a condition')
    clauses = ' AND '.join(f'{k} = ?' for k in where)
    return _execute(db, f'DELETE FROM community_segment_entries WHERE {clauses}', tuple(where.values())).rowcount  # noqa: S608  # column names from internal callers, values bound


def event_counts(db, entry_ids: list[int]) -> dict[int, dict]:
    """Distinct episodes per event kind, for each of the given entries (one query)."""
    counts: dict[int, dict] = {i: {} for i in entry_ids}
    if entry_ids:
        marks = ', '.join('?' * len(entry_ids))
        for r in _rows(db, 'SELECT entry_id, kind, COUNT(DISTINCT episode_id) AS n FROM community_segment_events '  # noqa: S608  # placeholders only, values bound
                           f'WHERE entry_id IN ({marks}) GROUP BY entry_id, kind', tuple(entry_ids)):
            counts[r['entry_id']][r['kind']] = r['n']
    return counts


def summary(db) -> dict:
    counts = {r['state']: r['n'] for r in _rows(
        db, "SELECT state, COUNT(*) AS n FROM community_segment_entries WHERE origin = 'community' "
            "AND is_active = 1 GROUP BY state")}
    return {'enabled': is_enabled(db), 'counts': counts, 'lastRun': db.get_setting(LAST_SYNC) or None,
            'lastSummary': json.loads(db.get_setting(LAST_SUMMARY) or 'null')}


# ---------------------------------------------------------------- show identity

def parse_podcast_guid(head: bytes) -> str | None:
    """`<podcast:guid>` of the channel, read from the feed's head only (before the first item)."""
    text = head.decode('utf-8', errors='replace').split('<item', 1)[0]
    m = re.search(r'<podcast:guid>\s*([^<\s]+)\s*</podcast:guid>', text, re.I)
    return m.group(1).strip().lower() if m else None


def fetch_origin_guid(source_url: str) -> str | None:
    """Stream the upstream feed until its first item (at most 256 KB) and read the channel guid."""
    resp = safe_get(source_url, URLTrust.FEED_CONTENT, timeout=HTTP_TIMEOUT, stream=True)
    try:
        resp.raise_for_status()
        buf = bytearray()
        for chunk in resp.iter_content(chunk_size=16384):
            buf += chunk
            if b'<item' in buf or len(buf) >= HEAD_MAX_BYTES:
                break
    finally:
        resp.close()
    return parse_podcast_guid(bytes(buf[:HEAD_MAX_BYTES]))


def refresh_identity(db, podcast: dict, fetch=fetch_origin_guid, now: datetime | None = None) -> dict:
    """The feed's identity row, re-read from the origin at most weekly."""
    now = now or utc_now()
    rows = _rows(db, 'SELECT * FROM community_segment_identity WHERE podcast_id = ?', (podcast['id'],))
    if rows and (parse_iso_utc(rows[0]['checked_at']) or now - IDENTITY_TTL * 2) > now - IDENTITY_TTL:
        return rows[0]
    origin = rows[0]['origin_podcast_guid'] if rows else None
    try:
        # A head without the guid (or past the read cap) keeps the last known one, like network trouble.
        origin = fetch(podcast['source_url']) or origin
    except Exception as e:
        logger.info(f"[{podcast['slug']}] show identity not refreshed: {e}")
    row = {'podcast_id': podcast['id'], 'origin_podcast_guid': origin,
           'url_guid': compute_feed_guid(podcast['source_url']), 'checked_at': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
    _write(db, 'INSERT OR REPLACE INTO community_segment_identity (podcast_id, origin_podcast_guid, url_guid, '
               'checked_at) VALUES (:podcast_id, :origin_podcast_guid, :url_guid, :checked_at)', row)
    return row


def show_keys(db, podcast: dict) -> list[tuple[str, str]]:
    """(key, key_kind) candidates of a feed: the origin guid when known, and always the URL-derived guid."""
    rows = _rows(db, 'SELECT * FROM community_segment_identity WHERE podcast_id = ?', (podcast['id'],))
    keys = []
    if rows and rows[0]['origin_podcast_guid']:
        keys.append((rows[0]['origin_podcast_guid'], 'podcast_guid'))
    url_guid = compute_feed_guid(podcast.get('source_url'))
    if url_guid and url_guid not in {k for k, _ in keys}:
        keys.append((url_guid, 'feed_url_guid'))
    return keys


def private_feed(podcast: dict) -> bool:
    """A feed whose URL carries credentials, or that is not a plain subscription, is never exported."""
    if is_local_feed(podcast) or (podcast.get('feed_type') or 'subscribed') != 'subscribed':
        return True
    parts = urlsplit(podcast.get('source_url') or '')
    if parts.username or parts.password:
        return True
    return any(_TOKEN_QUERY.search(k) or len(v) >= 24 for k, v in parse_qsl(parts.query))


# ---------------------------------------------------------------- transcripts

def _corrections(db, pattern_id: int) -> list[dict]:
    return _rows(db, 'SELECT * FROM pattern_corrections WHERE pattern_id = ? ORDER BY created_at', (pattern_id,))


def _bounds(row: dict) -> tuple[float, float] | None:
    try:
        b = json.loads(row.get('original_bounds') or 'null')
        return float(b['start']), float(b['end'])
    except (TypeError, ValueError, KeyError):
        return None


def _stored_segments(db, slug: str, episode_id: str) -> list[dict]:
    return parse_transcript_segments(db.get_transcript_for_timestamps(slug, episode_id) or '')


def recent_transcripts(db, podcast: dict, limit: int = BIND_SCAN_TRANSCRIPTS) -> list[tuple[str, list[dict]]]:
    rows = _rows(db, """SELECT e.episode_id, COALESCE(ed.original_transcript_text, ed.transcript_text) AS t
                        FROM episodes e JOIN episode_details ed ON ed.episode_id = e.id
                        WHERE e.podcast_id = ? AND COALESCE(ed.original_transcript_text, ed.transcript_text) != ''
                        ORDER BY e.id DESC LIMIT ?""", (podcast['id'], limit))
    return [(r['episode_id'], parse_transcript_segments(r['t'])) for r in rows]


# ---------------------------------------------------------------- export

def _local_row_problems(pattern: dict) -> list[str]:
    if pattern.get('sponsor_id') is not None or pattern.get('category') == 'sponsor':
        return ['names a known sponsor; sponsor stays sponsor']
    is_segment_row = (pattern.get('created_by') == SHOW_SEGMENT_CREATED_BY and pattern.get('scope') == 'podcast'
                      and pattern.get('category') in core.SHOW_FAMILY)
    if not (is_show_promo_pattern(pattern) or is_segment_row):
        return ['not a show segment (only learned show patterns can be exported)']
    if pattern.get('category') not in LIBRARY_CATEGORIES:
        return [f"{pattern.get('category')} is not exported in v1"]
    if not pattern.get('is_active'):
        return ['pattern is disabled']
    return []


def export_entry(db, pattern: dict, sponsors: list[dict], now: datetime | None = None) -> tuple[dict | None, list]:
    """(entry body, []) when the local show pattern may be published, else (None, reasons)."""
    now = now or utc_now()
    reasons = _local_row_problems(pattern)
    if reasons:
        return None, reasons
    if find_foreign_sponsors(pattern.get('text_template') or '', set(), sponsors):
        return None, ['names a known sponsor; sponsor stays sponsor']
    podcast = db.get_podcast_row(pattern.get('podcast_id'))
    if not podcast or private_feed(podcast):
        return None, ['private or local feed']
    corrections = _corrections(db, pattern['id'])
    if (pattern.get('false_positive_count') or 0) > 0 or any(
            c['correction_type'] == 'false_positive' for c in corrections):
        return None, ['has a false-positive reject']
    confirms = [c for c in corrections if c['correction_type'] == 'confirm' and _bounds(c)]
    episodes = {c['episode_id'] for c in confirms}
    stamps = [parse_iso_utc(c['created_at']) for c in confirms if parse_iso_utc(c['created_at'])]
    span_days = (max(stamps) - min(stamps)).days if stamps else 0
    if (len(confirms) < EXPORT_MIN_CONFIRMS or len(episodes) < EXPORT_MIN_EPISODES
            or span_days < EXPORT_MIN_SPAN_DAYS):
        return None, [f'needs {EXPORT_MIN_CONFIRMS} confirms on {EXPORT_MIN_EPISODES} episodes over '
                      f'{EXPORT_MIN_SPAN_DAYS} days (has {len(confirms)} on {len(episodes)} over {span_days})']
    if now - max(stamps) > timedelta(days=EXPORT_MAX_AGE_DAYS):
        return None, [f'last confirm is older than {EXPORT_MAX_AGE_DAYS} days']
    texts, durations = [], []
    for c in {c['episode_id']: c for c in confirms}.values():
        start, end = _bounds(c)
        text = get_transcript_text_for_range(_stored_segments(db, podcast['slug'], c['episode_id']), start, end)
        if text:
            texts.append(text)
            durations.append(end - start)
    category = pattern['category']
    short_ask = is_short_show_template(category, pattern.get('text_template'))
    stable = core.stable_core(texts, short_ask=short_ask)
    shape = core.shape_for(short_ask)
    if not shape.core_min <= len(stable) <= shape.core_max:
        return None, [f'core has {len(stable)} shingles; needs {shape.core_min}-{shape.core_max}']
    key, key_kind = show_keys(db, podcast)[0]
    existing = get_entries(db, origin='local_export', local_pattern_id=pattern['id'])
    monday = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%dT00:00:00Z')
    entry = core.entry_body(
        stable, key, key_kind, category=category, short_ask=short_ask, durations=durations,
        language=pattern.get('source_language'), confirms=len(confirms), episodes=len(episodes),
        span_days=span_days, community_id=existing[0]['community_id'] if existing else str(uuid.uuid4()),
        version=existing[0]['version'] + 1 if existing else 1, submitted_at=monday, app_version=app_version())
    return entry, []


def record_export(db, pattern_id: int, entry: dict) -> None:
    """Remember the community_id of a local export so a re-export updates the same entry."""
    _write(db, """INSERT INTO community_segment_entries (community_id, origin, version, category, source_language,
                      bucket, key_kind, body_json, state, local_pattern_id, is_active)
                  VALUES (?, 'local_export', ?, ?, ?, ?, ?, ?, 'unbound', ?, 0)
                  ON CONFLICT(community_id) DO UPDATE SET version = excluded.version, body_json = excluded.body_json""",
           (entry['community_id'], entry['version'], entry['category'], entry['source_language'], entry['bucket'],
            entry['key_kind'], json.dumps(entry), pattern_id))


def build_segment_bundle(db, pattern_ids: list[int], now: datetime | None = None) -> tuple[dict, list[dict]]:
    """The export envelope of community_export.build_bundle, carrying show-segment entries."""
    from community_export import BUNDLE_FORMAT, BUNDLE_VERSION
    sponsors = db.get_known_sponsors(active_only=False)
    ready, rejected = [], []
    for pid in pattern_ids:
        pattern = db.get_ad_pattern_by_id(pid)
        entry, reasons = export_entry(db, pattern, sponsors, now) if pattern else (None, ['pattern not found'])
        if entry is None:
            rejected.append({'id': pid, 'reasons': reasons})
            continue
        record_export(db, pid, entry)
        ready.append(entry)
    return {'format': BUNDLE_FORMAT, 'bundle_version': BUNDLE_VERSION, 'kind': core.KIND,
            'submitted_at': (now or utc_now()).strftime('%Y-%m-%dT00:00:00Z'), 'submitted_app_version': app_version(),
            'pattern_count': len(ready), 'patterns': ready}, rejected


# ---------------------------------------------------------------- receiver sync

def _fetch_segment_file(path: str) -> tuple[dict, bytes]:
    """One entry file named by the trusted manifest; the same path and size guards as sponsor files."""
    if not path or '/' in path or '\\' in path or '..' in path or not path.endswith('.json'):
        raise ValueError(f'unsafe segment path: {path!r}')
    resp = safe_get(SEGMENT_BASE_URL + path, trust=URLTrust.OPERATOR_CONFIGURED, timeout=HTTP_TIMEOUT, stream=True)
    try:
        resp.raise_for_status()
        try:
            raw = read_response_capped(resp, FILE_MAX_BYTES)
        except (ResponseTooLargeError, IncompleteResponseError) as e:
            raise requests.RequestException(f'segment file rejected: {e}') from e
    finally:
        resp.close()
    return json.loads(raw.decode('utf-8')), raw


def _filter_category(db, row: dict, allowed: set[str]) -> bool:
    """Deactivate a row outside the accepted categories (restored once allowed); True when it is filtered."""
    if normalize_segment_category(row['category']) not in allowed:
        if row['is_active']:
            update_entry(db, row['id'], is_active=0, disabled_reason='category_filtered', disabled_at=utc_now_iso())
        return True
    if not row['is_active'] and row['disabled_reason'] == 'category_filtered':
        update_entry(db, row['id'], is_active=1, disabled_reason=None, disabled_at=None)
    return False


def _revive(db, row: dict) -> dict:
    """A revoked entry back in the manifest: an adoption stands, anything else restarts unbound."""
    fields = {'state': 'adopted' if row['adopted_pattern_id'] else 'unbound', 'is_active': 1,
              'disabled_reason': None, 'disabled_at': None}
    if fields['state'] == 'unbound':
        fields['bound_podcast_id'] = None
    update_entry(db, row['id'], **fields)
    return {**row, **fields}


def apply_segment_manifest(db, manifest: dict, fetch=_fetch_segment_file) -> dict:
    """Insert, update (by content_hash), revoke and filter imported entries; never touches ad_patterns."""
    inserted = updated = revoked = skipped = errors = filtered = 0
    allowed = accepted_categories(db)
    items = manifest.get('show_segments') or []
    incoming = {}
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and item.get('community_id') and item.get('content_hash') and item.get('path'):
            incoming[item['community_id']] = item
        else:
            errors += 1
    existing = {r['community_id']: r for r in get_entries(db, origin='community')}
    # This install's own published entries: its local show pattern already does the matching.
    own = {r['community_id'] for r in _rows(
        db, "SELECT community_id FROM community_segment_entries WHERE origin = 'local_export'")}
    for cid, item in incoming.items():
        row = existing.get(cid)
        if cid in own:
            skipped += 1
            continue
        try:
            revive = row is not None and row['disabled_reason'] == 'removed_upstream'
            if row is not None and (row['protected_from_sync'] or row['content_hash'] == item['content_hash']):
                row = _revive(db, row) if revive else row
                if _filter_category(db, row, allowed):
                    filtered += 1
                else:
                    skipped += 1
                continue
            data, raw = fetch(item['path'])
            problems = entry_problems(data, library=False)
            if problems or data.get('community_id') != cid or hashlib.sha256(raw).hexdigest() != item['content_hash']:
                raise ValueError('; '.join(problems) or 'community_id or content_hash mismatch')
            # Revived only once its new content validated; filtered by the incoming category, not the stored one.
            row = _revive(db, row) if revive else row
            if (data['category'] not in allowed if row is None
                    else _filter_category(db, {**row, 'category': data['category']}, allowed)):
                filtered += 1
                continue
            fields = (data['version'], item['content_hash'], data['category'], data.get('source_language'),
                      data['bucket'], data['key_kind'], json.dumps(data))
            if row is None:
                _write(db, """INSERT INTO community_segment_entries (version, content_hash, category, source_language,
                                  bucket, key_kind, body_json, community_id, origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                                  'community')""", (*fields, cid))
                inserted += 1
            else:
                # New content restarts validation (the old binding described other hashes), except that an
                # operator's suspension and an adoption into a local pattern stand.
                state = row['state'] if row['state'] in ('suspended', 'adopted') else 'unbound'
                _write(db, """UPDATE community_segment_entries SET version = ?, content_hash = ?, category = ?,
                                  source_language = ?, bucket = ?, key_kind = ?, body_json = ?, state = ?,
                                  bound_podcast_id = CASE WHEN ? = 'unbound' THEN NULL ELSE bound_podcast_id END
                                  WHERE id = ?""", (*fields, state, state, row['id']))
                updated += 1
        except Exception as e:
            errors += 1
            logger.warning(f'community_segments: failed to apply {cid}: {e}')
    live = [r for r in existing.values() if r['state'] != 'revoked' and not r['protected_from_sync']]
    absent = [r for r in live if r['community_id'] not in incoming]
    if absent and (not incoming or len(absent) / len(live) > 0.5):
        logger.warning(f'community_segments: refusing to revoke {len(absent)}/{len(live)} entries; '
                       'the manifest looks empty or truncated')
        skipped += len(absent)
    else:
        for r in absent:
            update_entry(db, r['id'], state='revoked', is_active=0, disabled_reason='removed_upstream',
                         disabled_at=utc_now_iso())
            revoked += 1
    skipped += sum(1 for r in existing.values()
                   if r['protected_from_sync'] and r['community_id'] not in incoming)
    return {'inserted': inserted, 'updated': updated, 'revoked': revoked, 'skipped': skipped, 'errors': errors,
            'filtered': filtered}


# ---------------------------------------------------------------- binding

def _feed_gates(db, podcast: dict, actions: dict | None = None) -> dict:
    return {'actions': actions or db.resolve_segment_actions(podcast['slug'], podcast=podcast),
            'snip': feed_snips_short_asks(db, podcast['slug']),
            'show': db.resolve_feed_bool(podcast['slug'], 'detect_show_segments', 'detect_show_segments',
                                         podcast=podcast)}


def _acts_on_feed(data: dict, gates: dict) -> bool:
    """keep is not scanned; short asks need snip on; intro/outro/recap need show-segment detection on."""
    if gates['actions'].get(data['category']) == 'keep':
        return False
    if data.get('short_ask') and not gates['snip']:
        return False
    return gates['show'] or data['category'] not in core.SHOW_FAMILY


def bind_feed(db, podcast: dict, transcripts: list[tuple[str, list[dict]]] | None = None) -> int:
    """Bind unbound entries of the feed's buckets whose body matches >= 2 of its last 10 stored transcripts."""
    keys = show_keys(db, podcast)
    buckets = {core.bucket_for(k): k for k, _kind in keys}
    allowed = accepted_categories(db)
    candidates = [e for e in get_entries(db, origin='community', state='unbound', is_active=1)
                  if e['bucket'] in buckets and e['category'] in allowed]
    if not candidates:
        return 0
    bound = get_entries(db, bound_podcast_id=podcast['id'])
    per_category = {}
    for e in bound:
        if e['state'] not in ('revoked', 'adopted'):
            per_category[e['category']] = per_category.get(e['category'], 0) + 1
    total = sum(per_category.values())
    transcripts = recent_transcripts(db, podcast) if transcripts is None else transcripts
    candidates.sort(key=lambda e: -sum(body(e).get('evidence', {}).values()))
    hashed: dict[int, list] = {}  # one HMAC pass per bucket over the transcripts, shared by its entries
    n = 0
    for entry in candidates:
        if total >= BOUND_PER_FEED or per_category.get(entry['category'], 0) >= BOUND_PER_CATEGORY:
            continue
        data, secret = body(entry), core.show_secret(buckets[entry['bucket']])
        if entry['bucket'] not in hashed:
            hashed[entry['bucket']] = [core.hashed_shingles(secret, segs) for _eid, segs in transcripts]
        hits = [eid for (eid, segs), h in zip(transcripts, hashed[entry['bucket']], strict=True)
                if core.find_matches(data, secret, segs, None, h)]
        if len(hits) < BIND_MIN_TRANSCRIPTS:
            continue
        update_entry(db, entry['id'], state='candidate', bound_podcast_id=podcast['id'])
        for eid in hits:
            add_event(db, entry['id'], podcast['id'], eid, 'bind')
        per_category[entry['category']] = per_category.get(entry['category'], 0) + 1
        total += 1
        n += 1
    return n


# ---------------------------------------------------------------- pipeline hooks (H2, H3/H4)

def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    shorter = min(a_end - a_start, b_end - b_start)
    return shorter > 0 and overlap_seconds(a_start, a_end, b_start, b_end) / shorter >= OVERLAP


def _family(category) -> frozenset | None:
    for family in (core.PROMO_FAMILY, core.SHOW_FAMILY):
        if category in family:
            return family
    return None


def _local_show_row(db, pattern_id, slug) -> dict | None:
    if pattern_id is None:
        return None
    row = db.get_ad_pattern_by_id(pattern_id)
    if row and row.get('podcast_id') == slug and row.get('created_by') in (SHOW_PROMO_CREATED_BY,
                                                                         SHOW_SEGMENT_CREATED_BY):
        return row
    return None


def _annotation(entry: dict, match: core.Match, hold: str | None = None) -> dict:
    note = {'entry': entry['id'], 'state': entry['state'], 'c_body': match.c_body,
            'span': [round(match.start, 2), round(match.end, 2)]}
    if hold:
        note['hold'] = hold
    return note


def _corroborate(ad: dict, entry: dict, data: dict, match: core.Match, segments: list[dict]) -> None:
    cap = core.SHORT_ASK_EDGE_CAP_S if ad['end'] - ad['start'] < core.SHORT_SPAN_S else core.EDGE_CAP_S
    if ad['start'] < match.start - cap and ad['end'] > match.end + cap:
        # The segment sits inside a wider detection: confirm it without reshaping or relabelling the marker.
        ad['show_segment'] = _annotation(entry, match)
        return
    hold = None
    for edge, value, anchored in (('start', match.start, match.anchored_start), ('end', match.end, match.anchored_end)):
        if not anchored:
            continue
        if abs(ad[edge] - value) <= cap:
            ad[edge] = value
        else:
            hold = HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT
    ad['category'] = data['category']
    ad['confidence'] = min(0.99, float(ad.get('confidence') or 0.0) + CONFIDENCE_BUMP)
    ad['show_segment'] = _annotation(entry, match, hold)
    _stamp_short_ask(ad, data, match, segments)


def _stamp_short_ask(ad: dict, data: dict, match: core.Match, segments: list[dict]) -> None:
    """A short-ask entry's marker goes through the existing short-ask clamp, its origin the match span."""
    if data.get('short_ask'):
        ad['short_ask'] = True
        ad['short_ask_origin'] = [match.start, match.end]
        clamp_short_ask(ad, segments)


def _suggestion(entry: dict, data: dict, match: core.Match, segments: list[dict]) -> dict:
    hold = HOLD_REASON_SHOW_SEGMENT_SUGGESTION if entry['state'] == 'candidate' else None
    ad = {'start': match.start, 'end': match.end, 'category': data['category'], 'sponsor': None,
          'confidence': 0.75, 'detection_stage': 'show_segment',
          'reason': f"Show segment (community, {entry['state']})", 'show_segment': _annotation(entry, match, hold)}
    _stamp_short_ask(ad, data, match, segments)
    return ad


def _hold(db, ad: dict, entry_id: int, episode_id: str, used: set) -> None:
    """Hold the marker; a reprocessed episode reuses an unresolved hold id of this entry's earlier holds there
    (suggest or disagree, each id once per pass), so one marker stays one review item."""
    if not ad.get('hold_id') and (ad.get('show_segment') or {}).get('hold'):
        prior = [e['hold_id'] for e in events(db, entry_id, 'suggest', 'disagree')
                 if e['episode_id'] == episode_id and e['hold_id'] and e['hold_id'] not in used]
        if prior:  # a first-time episode skips the resolved lookup
            resolved = {e['hold_id'] for e in events(db, entry_id, 'confirm', 'reject')}
            reuse = next((h for h in prior if h not in resolved), None)
            if reuse:
                ad['hold_id'] = reuse
    enforce_holds([ad])
    if ad.get('hold_id'):
        used.add(ad['hold_id'])


def _unresolved(db, entry_id: int) -> int:
    held = {e['hold_id'] for e in events(db, entry_id, 'suggest', 'disagree') if e['hold_id']}
    resolved = {e['hold_id'] for e in events(db, entry_id, 'confirm', 'reject') if e['hold_id']}
    return len(held - resolved)


def annotate(db, slug: str, episode_id: str, segments: list[dict], all_ads: list[dict],
             episode_duration: float | None, segment_actions: dict | None = None,
             keep_ads: list[dict] | None = None) -> list[dict]:
    """Corroborate, flag or suggest show-bound segments; returns the same list object when nothing applies.
    `keep_ads` (markers already kept by their action) are never touched but still block a suggestion."""
    if not is_enabled(db) or not slug:
        return all_ads
    try:
        podcast = db.get_podcast_row(slug)
        entries = [e for e in get_entries(db, bound_podcast_id=podcast['id'], is_active=1)
                   if e['state'] in MATCHING_STATES] if podcast else []
        if not entries:
            return all_ads
        gates = _feed_gates(db, podcast, segment_actions)
        entries = [e for e in entries if _acts_on_feed(body(e), gates)]
        if not entries:
            return all_ads
        return _annotate(db, podcast, episode_id, segments, list(all_ads), episode_duration, entries,
                         keep_ads or [])
    except core.SegmentKeyError as e:
        # Loud, but an opt-in validator never stops an episode from processing.
        logger.error(f'[{slug}:{episode_id}] show segments skipped, the show key could not be derived: {e}')
        return all_ads
    except Exception as e:
        logger.warning(f'[{slug}:{episode_id}] show segments skipped: {e}')
        return all_ads


def _annotate(db, podcast, episode_id, segments, ads, episode_duration, entries, keep_ads) -> list[dict]:
    secrets = {core.bucket_for(k): core.show_secret(k) for k, _ in show_keys(db, podcast)}
    hashed = {}
    suggestions = []
    used_holds: set = set()
    # A description-chapter trim is appended late so nothing reshapes it: it only blocks suggestions.
    frozen = [*keep_ads, *(a for a in ads if a.get('detection_stage') == 'description_chapter')]
    ads_open = [a for a in ads if a.get('detection_stage') != 'description_chapter']
    for entry in entries:
        data = body(entry)
        secret = secrets.get(entry['bucket'])
        if secret is None:
            continue
        if entry['bucket'] not in hashed:
            hashed[entry['bucket']] = core.hashed_shingles(secret, segments)
        for match in core.find_matches(data, secret, segments, episode_duration, hashed[entry['bucket']]):
            update_entry(db, entry['id'], last_matched_at=utc_now_iso())
            over = [a for a in ads_open if _overlaps(match.start, match.end, a['start'], a['end'])]
            if not over:
                if not any(_overlaps(match.start, match.end, k['start'], k['end']) for k in frozen):
                    suggestions.append((match.c_body, entry, data, match))
                continue
            for ad in over:
                _apply_to_marker(db, podcast, episode_id, ad, entry, data, match, segments, used_holds)
    taken = set()
    for _c, entry, data, match in sorted(suggestions, key=lambda s: -s[0]):
        if len(taken) >= SUGGESTIONS_PER_EPISODE or entry['id'] in taken:
            continue
        if entry['state'] == 'candidate' and _unresolved(db, entry['id']) >= UNRESOLVED_CAP:
            update_entry(db, entry['id'], state='dormant')
            continue
        ad = _suggestion(entry, data, match, segments)
        _hold(db, ad, entry['id'], episode_id, used_holds)
        add_event(db, entry['id'], podcast['id'], episode_id, 'suggest', ad.get('hold_id'))
        ads.append(ad)
        taken.add(entry['id'])
    return sorted(ads, key=lambda a: a['start'])


def _apply_to_marker(db, podcast, episode_id, ad, entry, data, match, segments, used_holds: set) -> None:
    category = ad.get('category')
    local = _local_show_row(db, ad.get('pattern_id'), podcast['slug'])
    if local is not None:
        update_entry(db, entry['id'], state='adopted', adopted_pattern_id=local['id'])
        return
    if category == 'sponsor' or known_ad_evidence(ad):
        add_event(db, entry['id'], podcast['id'], episode_id, 'disagree')
        return
    if _family(category) is _family(data['category']) and _family(category) is not None:
        _corroborate(ad, entry, data, match, segments)
        kind = 'disagree' if ad['show_segment'].get('hold') else 'corroborate'
    else:
        ad['show_segment'] = _annotation(entry, match, HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT)
        kind = 'disagree'
    _hold(db, ad, entry['id'], episode_id, used_holds)
    add_event(db, entry['id'], podcast['id'], episode_id, kind, ad.get('hold_id'))


def enforce_holds(ads: list[dict]) -> list[dict]:
    """Re-apply show-segment holds after the validator, turning its ACCEPT into REVIEW so the gate cannot cut it."""
    for ad in ads:
        hold = (ad.get('show_segment') or {}).get('hold') if isinstance(ad, dict) else None
        validation = ad.get('validation') or {} if hold else {}
        # A human confirm restores the span and a reject keeps it uncut: neither is held again.
        if hold and not validation.get('user_confirmed') and validation.get('decision') != 'REJECT':
            ad['held_for_review'] = True
            ad['hold_reason'] = hold
            ensure_hold_id(ad)
            if validation.get('decision') == 'ACCEPT':
                validation['decision'] = 'REVIEW'
                validation.setdefault('flags', []).append(f'INFO: Held for review ({hold})')
    return ads


# ---------------------------------------------------------------- states (H1 tick)

def _corrections_for_entry(db, entry: dict, podcast: dict) -> list[dict]:
    """Confirms and rejects of markers this entry annotated: by hold id, else by overlap with the stored marker."""
    episode_ids = {e['episode_id'] for e in events(db, entry['id'], 'corroborate', 'suggest', 'disagree')}
    hold_ids = {e['hold_id'] for e in events(db, entry['id'], 'suggest', 'disagree') if e['hold_id']}
    out = []
    for episode_id in episode_ids:
        rows = _rows(db, "SELECT * FROM pattern_corrections WHERE podcast_id = ? AND episode_id = ? AND "
                         "correction_type IN ('confirm', 'false_positive')", (podcast['id'], episode_id))
        if not rows:
            continue
        episode = db.get_episode(podcast['slug'], episode_id) or {}
        spans = [m.get('show_segment', {}).get('span') or (m['start'], m['end'])
                 for m in json.loads(episode.get('ad_markers_json') or '[]')
                 if (m.get('show_segment') or {}).get('entry') == entry['id']]
        for row in rows:
            bounds = _bounds(row)
            if (row.get('hold_id') and row['hold_id'] in hold_ids) or (bounds and any(
                    _overlaps(bounds[0], bounds[1], s[0], s[1]) for s in spans)):
                out.append(row)
    return out


def sweep(db, podcast: dict) -> None:
    """Advance the states of a feed's bound entries from the human corrections of their markers."""
    for entry in get_entries(db, bound_podcast_id=podcast['id'], is_active=1):
        if entry['state'] not in ('candidate', 'validated', 'dormant'):
            continue
        confirms, rejects, new_rejects = set(), set(), set()
        for row in _corrections_for_entry(db, entry, podcast):
            kind = 'confirm' if row['correction_type'] == 'confirm' else 'reject'
            # A reject the ledger already holds (same episode and hold) is old evidence, never new.
            if add_event(db, entry['id'], podcast['id'], row['episode_id'], kind, row.get('hold_id'), row['id']) \
                    and kind == 'reject':
                new_rejects.add(row['episode_id'])
            local = _local_show_row(db, row.get('pattern_id'), podcast['slug'])
            if kind == 'confirm' and local is not None:
                update_entry(db, entry['id'], state='adopted', adopted_pattern_id=local['id'])
                break
            (confirms if kind == 'confirm' else rejects).add(row['episode_id'])
        else:
            _advance(db, entry, confirms, rejects, new_rejects)


def _advance(db, entry: dict, confirms: set, rejects: set, new_rejects: set) -> None:
    """Only a reject not seen before suspends a candidate, so an operator's reactivate sticks."""
    state = entry['state']
    if state in ('candidate', 'dormant') and new_rejects:
        update_entry(db, entry['id'], state='suspended')
    elif state == 'validated':
        matched = {e['episode_id'] for e in events(db, entry['id'], 'corroborate', 'suggest', 'disagree')}
        if len(rejects) >= SHOW_PROMO_DISABLE_REJECTS and 2 * len(rejects) > len(matched):
            update_entry(db, entry['id'], state='suspended')
    elif len(confirms) >= N_LOCAL:
        update_entry(db, entry['id'], state='validated')
    elif state == 'dormant' and _unresolved(db, entry['id']) < UNRESOLVED_CAP:
        update_entry(db, entry['id'], state='candidate')


def set_state(db, entry_id: int, action: str) -> dict | None:
    """Operator actions: suspend, reactivate (suspended/dormant to candidate), protect, unprotect."""
    entry = get_entry(db, entry_id)
    if entry is None or entry['origin'] != 'community':
        return None
    if action == 'suspend':
        update_entry(db, entry_id, state='suspended')
    elif action == 'reactivate' and entry['state'] in ('suspended', 'dormant'):
        update_entry(db, entry_id, state='candidate' if entry['bound_podcast_id'] else 'unbound')
    elif action in ('protect', 'unprotect'):
        update_entry(db, entry_id, protected_from_sync=int(action == 'protect'))
    return get_entry(db, entry_id)


def community_segments_tick(db, force: bool = False, fetch_manifest=None, fetch_file=_fetch_segment_file,
                            fetch_guid=fetch_origin_guid) -> dict | None:
    """Maintenance tick: sync when due, refresh identities, bind and advance states. None when off."""
    if not imports_enabled(db):
        return None
    from community_sync import DEFAULT_CRON, _fetch_manifest, _validate_manifest
    result: dict = {}
    last = parse_iso_utc(db.get_setting(LAST_SYNC))
    cron = db.get_setting('community_sync_cron') or DEFAULT_CRON
    if force or last is None or is_due(cron, last, utc_now()):
        db.set_setting(LAST_SYNC, utc_now_iso())
        try:
            manifest = (fetch_manifest or _fetch_manifest)()
            _validate_manifest(manifest)
            result = apply_segment_manifest(db, manifest, fetch_file)
            db.set_setting(LAST_SUMMARY, json.dumps(result))
        except Exception as e:
            logger.warning(f'community_segments: sync failed: {e}')
    last_probe = parse_iso_utc(db.get_setting(LAST_PROBE))
    if not force and last_probe is not None and utc_now() - last_probe < PROBE_INTERVAL:
        return result
    db.set_setting(LAST_PROBE, utc_now_iso())
    for podcast in db.get_all_podcasts():
        if is_local_feed(podcast):
            continue
        try:
            refresh_identity(db, podcast, fetch_guid)
            bind_feed(db, podcast)
            sweep(db, podcast)
        except Exception as e:  # a key failure is loud; the other feeds still run
            log = logger.error if isinstance(e, core.SegmentKeyError) else logger.warning
            log(f"[{podcast.get('slug')}] show segments tick failed: {e}")
    return result
