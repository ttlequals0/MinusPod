"""Community pattern auto-pull / sync.

Fetches https://raw.githubusercontent.com/ttlequals0/MinusPod/main/patterns/community/index.json
on a configurable cron schedule. Applies INSERT / UPDATE / DELETE semantics
against ad_patterns rows tagged `source='community'`, respecting the
`protected_from_sync` flag.

Settings keys (in the `settings` table):

  - community_sync_enabled (bool, default false)
  - community_sync_cron    (str cron expression, default '0 3 * * 0')
  - community_sync_last_run, community_sync_last_error,
    community_sync_manifest_version, community_sync_last_summary
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from utils.community_tags import (
    COMMUNITY_MANIFEST_URL,
    COMMUNITY_PATTERN_BASE_URL,
    VOCABULARY_VERSION,
)
from utils.cron import is_due
from utils.safe_http import (
    ResponseTooLargeError,
    URLTrust,
    read_response_capped,
    safe_get,
)
from utils.time import parse_iso_datetime, utc_now_iso

logger = logging.getLogger('podcast.community_sync')
HTTP_TIMEOUT = 20
# Cap for the JSON manifest body before deserialisation (an SSRF/DoS guard,
# since the body is buffered in memory). The manifest still embeds every
# pattern inline and so grows with the catalog -- it was at ~74% of the old
# 256 KB cap, so this raises the ceiling to 1 MB for headroom. This is an
# interim measure; the durable fix is a thin index plus incremental per-pattern
# fetch (tracked separately), which keeps the manifest small regardless of
# catalog size.
MANIFEST_MAX_BYTES = 1024 * 1024
# Cap for a single per-pattern file in the thin-index (#400) incremental fetch.
# A pattern is a few KB (text_template is gated at 3500 chars); 256 KB is ample.
PATTERN_FILE_MAX_BYTES = 256 * 1024
DEFAULT_CRON = '0 3 * * 0'  # Sunday 3am UTC


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = parse_iso_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fetch_manifest(url: str = COMMUNITY_MANIFEST_URL) -> Dict[str, Any]:
    """Fetch the manifest. Raises requests.RequestException on failure.

    Routed through ``safe_http.safe_get`` with the hardcoded raw.githubusercontent
    URL so SSRF / private-range checks and per-hop redirect revalidation
    apply. Body is streamed through ``read_response_capped`` to refuse a
    manifest larger than ``MANIFEST_MAX_BYTES`` before deserialisation.
    """
    resp = safe_get(
        url,
        trust=URLTrust.OPERATOR_CONFIGURED,
        timeout=HTTP_TIMEOUT,
        stream=True,
    )
    try:
        resp.raise_for_status()
        try:
            body = read_response_capped(resp, MANIFEST_MAX_BYTES)
        except ResponseTooLargeError as e:
            raise requests.RequestException(f'manifest exceeded size cap: {e}') from e
    finally:
        resp.close()
    return json.loads(body.decode('utf-8'))


def _fetch_pattern_file(path: str) -> Dict[str, Any]:
    """Fetch one per-pattern file referenced by a thin-index entry's `path`.

    `path` is a bare filename from the trusted manifest; reject anything with a
    directory component so a malformed/compromised manifest can't pull an
    arbitrary path off the host. Routed through the same SSRF-checked, size-
    capped path as the manifest fetch.
    """
    if not path or '/' in path or '\\' in path or '..' in path or not path.endswith('.json'):
        raise ValueError(f'unsafe pattern path: {path!r}')
    resp = safe_get(
        COMMUNITY_PATTERN_BASE_URL + path,
        trust=URLTrust.OPERATOR_CONFIGURED,
        timeout=HTTP_TIMEOUT,
        stream=True,
    )
    try:
        resp.raise_for_status()
        try:
            body = read_response_capped(resp, PATTERN_FILE_MAX_BYTES)
        except ResponseTooLargeError as e:
            raise requests.RequestException(f'pattern file exceeded size cap: {e}') from e
    finally:
        resp.close()
    return json.loads(body.decode('utf-8'))


def _validate_manifest(manifest: Dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise ValueError('manifest is not a JSON object')
    if 'manifest_version' not in manifest:
        raise ValueError('manifest_version missing')
    if 'patterns' not in manifest or not isinstance(manifest['patterns'], list):
        raise ValueError('patterns array missing')


def apply_manifest(db, manifest: Dict[str, Any]) -> Dict[str, int]:
    """Apply manifest entries against ad_patterns. Returns summary counts.

    Handles both manifest shapes:
    - v2 thin index (#400): entries carry `content_hash` + `path`. Diff the hash
      against the row's stored content_hash; fetch the per-pattern file only when
      it differs or is new. Version stops being the update control.
    - v1 inline: entries carry `data`. Kept for backward compatibility during
      rollout (the published manifest may still be v1); applied via the old
      version-greater-than gate.

    Reconcile: a community_id missing from the manifest is deleted unless
    protected_from_sync=1 or the anti-mass-delete guard trips.
    """
    from pattern_service import PatternService

    pattern_service = PatternService(db)
    inserts = updates = deletes = skips = errors = 0

    # Pre-collect manifest community_ids so we can batch the existence lookup
    # instead of running one SELECT per entry.
    incoming_ids: set = set()
    valid_entries = []
    for entry in manifest['patterns']:
        if not isinstance(entry, dict):
            errors += 1
            continue
        community_id = entry.get('community_id')
        data = entry.get('data')
        content_hash = entry.get('content_hash')
        path = entry.get('path')
        # A thin entry needs content_hash + path; an inline entry needs data.
        if not community_id or (not data and not (content_hash and path)):
            errors += 1
            continue
        incoming_ids.add(community_id)
        valid_entries.append((community_id, data, content_hash, path, entry.get('version', 1)))

    existing_by_cid = db.find_patterns_by_community_ids(list(incoming_ids))

    for community_id, data, content_hash, path, manifest_version in valid_entries:
        existing = existing_by_cid.get(community_id)
        try:
            if data is not None:
                # v1 inline: apply the embedded body with the version gate.
                data_with_version = dict(data)
                data_with_version['community_id'] = community_id
                data_with_version['version'] = manifest_version
                if existing is None:
                    pattern_service.import_community_pattern(data_with_version)
                    inserts += 1
                elif existing.get('protected_from_sync'):
                    skips += 1
                elif int(manifest_version) > int(existing.get('version') or 1):
                    pattern_service.import_community_pattern(data_with_version)
                    updates += 1
                else:
                    skips += 1
                continue

            # v2 thin: skip protected and hash-unchanged rows WITHOUT fetching.
            if existing is not None:
                if existing.get('protected_from_sync'):
                    skips += 1
                    continue
                if existing.get('content_hash') == content_hash:
                    skips += 1
                    continue

            fetched = _fetch_pattern_file(path)
            fetched['community_id'] = community_id
            fetched['version'] = manifest_version
            # Store the index's content_hash on the row; the equivalence test
            # pins it to content_hash_for_bytes of the published file, so an
            # unchanged file reads as unchanged next sync.
            fetched['content_hash'] = content_hash
            pattern_service.import_community_pattern(fetched)
            if existing is None:
                inserts += 1
            else:
                updates += 1
        except Exception as e:
            errors += 1
            logger.warning(f"community_sync: failed to apply {community_id}: {e}")

    # Reconcile deletes: existing community patterns absent from the manifest.
    # Guard against an empty / truncated / mis-regenerated upstream manifest
    # mass-deleting the catalog: if the manifest carried no applicable entries,
    # or the reconcile would remove more than half of the non-protected
    # community rows, skip the delete phase entirely rather than wipe.
    community_rows = db.get_patterns_by_source('community', active_only=False)
    non_protected_absent = [
        r for r in community_rows
        if r.get('community_id') and r['community_id'] not in incoming_ids
        and not r.get('protected_from_sync')
    ]
    non_protected_total = sum(
        1 for r in community_rows
        if r.get('community_id') and not r.get('protected_from_sync')
    )
    allow_deletes = True
    if non_protected_absent and (
        not incoming_ids
        or (non_protected_total
            and len(non_protected_absent) / non_protected_total > 0.5)
    ):
        allow_deletes = False
        logger.warning(
            "community_sync: refusing to delete %d/%d non-protected community "
            "patterns (incoming_ids=%d); manifest looks empty or truncated. "
            "Skipping delete reconciliation.",
            len(non_protected_absent), non_protected_total, len(incoming_ids),
        )

    for row in community_rows:
        cid = row.get('community_id')
        if not cid or cid in incoming_ids:
            continue
        if row.get('protected_from_sync'):
            skips += 1
            continue
        if not allow_deletes:
            skips += 1
            continue
        try:
            db.delete_ad_pattern(row['id'])
            deletes += 1
        except Exception as e:
            errors += 1
            logger.warning(f"community_sync: failed to delete {cid}: {e}")

    return {
        'inserted': inserts,
        'updated': updates,
        'deleted': deletes,
        'skipped': skips,
        'errors': errors,
    }


def sync_now(db, manifest_url: str = COMMUNITY_MANIFEST_URL) -> Dict[str, Any]:
    """Force a sync regardless of schedule. Returns a summary dict.

    On any failure the function raises so the caller can surface the error
    to the user. The settings table is updated either way to record the
    attempt timestamp / last error.
    """
    started_at = utc_now_iso()
    db.set_setting('community_sync_last_run', started_at)

    try:
        manifest = _fetch_manifest(manifest_url)
        _validate_manifest(manifest)
    except requests.HTTPError as e:
        # 404 = upstream hasn't published a manifest yet (e.g. the feature
        # branch hasn't been merged to main). Treat as a non-issue; log at
        # info-level so the every-15-min tick doesn't spam WARN.
        status = e.response.status_code if e.response is not None else None
        msg = f'{status} fetching manifest' if status else str(e)
        db.set_setting('community_sync_last_error', msg)
        if status == 404:
            logger.info(
                f'community_sync: no manifest at {manifest_url} (404). '
                f'Either upstream has not published one yet or sync is '
                f'pointed at the wrong URL.'
            )
        else:
            logger.warning(f'community_sync: manifest fetch failed: {msg}')
        raise
    except Exception as e:
        msg = str(e)
        db.set_setting('community_sync_last_error', msg)
        logger.warning(f'community_sync: manifest fetch/validate failed: {msg}')
        raise

    summary = apply_manifest(db, manifest)
    summary['manifest_version'] = manifest.get('manifest_version')
    summary['fetched_at'] = started_at

    # Compare the manifest's vocabulary_version against the value this app
    # was built with. A mismatch means the upstream patterns may carry tags
    # the local validator doesn't know about -- surface a warning so the
    # operator knows their image is behind. The vocabulary itself stays
    # baked into the app code, so this is informational only.
    manifest_vocab = manifest.get('vocabulary_version')
    summary['vocabulary_version'] = manifest_vocab
    if manifest_vocab is not None:
        try:
            if int(manifest_vocab) > VOCABULARY_VERSION:
                warning = (
                    f'manifest vocabulary_version={manifest_vocab} is newer '
                    f'than this app (vocab={VOCABULARY_VERSION}); upgrade '
                    f'to pick up new tags'
                )
                logger.warning(f'community_sync: {warning}')
                summary['vocabulary_warning'] = warning
        except (TypeError, ValueError):
            logger.warning(
                f'community_sync: manifest vocabulary_version is not an int: '
                f'{manifest_vocab!r}'
            )

    db.set_setting('community_sync_last_error', '')
    db.set_setting('community_sync_manifest_version', str(manifest.get('manifest_version')))
    db.set_setting('community_sync_last_summary', json.dumps(summary))
    logger.info(f'community_sync: {summary}')
    return summary


def community_pattern_sync_tick(db, force: bool = False) -> Optional[Dict[str, Any]]:
    """Run sync if due (or forced). Returns the summary dict, or None if skipped."""
    enabled = db.get_setting_bool('community_sync_enabled', default=False)
    if not enabled and not force:
        return None

    cron = db.get_setting('community_sync_cron') or DEFAULT_CRON
    last_run = _parse_iso(db.get_setting('community_sync_last_run'))
    now = _utc_now()

    if not force and last_run is not None and not is_due(cron, last_run, now):
        return None

    try:
        return sync_now(db)
    except Exception:
        # sync_now already logged + stamped settings.
        return None
