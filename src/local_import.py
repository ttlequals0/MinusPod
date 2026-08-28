"""Local (archive-import) feed import service.

This module has two halves. The top half is pure planning: parsing the
``sNNeNN`` naming scheme, validating sidecar JSON, synthesizing missing
publish dates, and building a dry-run import plan -- no DB, no storage, no
threads. The bottom half (this section) is the commit engine: it consumes a
plan built by the top half, moves files out of staging/the user-managed
import directory into permanent episode storage, extracts embedded
artwork, and runs the whole batch on a background thread tracked in a
module-level job registry.

Stdlib only (``re``, ``json``, ``hashlib``, ``datetime``, ``pathlib``,
``shutil``, ``threading``) for the planning half and job registry, plus
``utils.validation.is_valid_episode_id`` for a sanity assert on minted ids
and the handful of DB/storage/audio helpers the commit half calls through
their public interfaces (no direct SQL beyond the one queue-row delete that
mirrors ``database.episodes.delete_episode_rows``'s pattern).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import MIN_PRESERVED_CHAPTERS
from database.queue import compute_queue_priority
from embedded_chapters import probe_chapters
from storage import _detect_image_mime
from utils.audio import extract_embedded_artwork, get_audio_duration
from utils.time import utc_now_iso
from utils.validation import is_valid_episode_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Planning half -- pure functions only, see module docstring.
# ---------------------------------------------------------------------------

AUDIO_EXTS = {'.mp3'}
ARTWORK_EXTS = {'.jpg', '.jpeg', '.png'}
SIDECAR_KEYS = {'title', 'description', 'published_at', 'season', 'episode'}
FILENAME_RE = re.compile(r'^(s\d{2,3}e\d{2,4})(?: - (.+))?$', re.IGNORECASE)
SYNTH_STEP = timedelta(days=1)

# Common non-mp3 audio containers a user might drop into the import
# directory by mistake -- rejected with a hint to convert rather than
# silently ignored, since an unmatched title/basename would otherwise look
# like a plain naming-scheme mismatch.
AUDIO_LIKE_EXTS = {'.m4a', '.wav', '.flac', '.aac', '.ogg', '.wma', '.opus',
                    '.aiff', '.aif'}
DESCRIPTION_EXT = '.txt'
SIDECAR_EXT = '.json'

_TOKEN_RE = re.compile(r'^s(\d{2,3})e(\d{2,4})$', re.IGNORECASE)
_TEMP_SUFFIXES = {'.part', '.tmp'}
_ISO_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

FFMPEG_HINT = ("not a supported audio format (only .mp3 is imported); "
               "convert first, e.g. "
               "ffmpeg -i in.m4a -codec:a libmp3lame -q:a 2 out.mp3")


def parse_basename(stem: str) -> tuple[str, int, int, str | None] | None:
    """'S01E05 - The Beginning' -> ('s01e05', 1, 5, 'The Beginning');
    None when the stem does not match the scheme."""
    match = FILENAME_RE.match(stem)
    if not match:
        return None
    token, title = match.group(1), match.group(2)
    token = token.lower()
    token_match = _TOKEN_RE.match(token)
    if not token_match:
        return None
    season = int(token_match.group(1))
    episode = int(token_match.group(2))
    return (token, season, episode, title)


def validate_sidecar(data: object) -> tuple[dict | None, str | None]:
    """Fail-closed: returns (clean, None) or (None, 'error message').
    Unknown keys rejected; title 1-500 chars; published_at must parse as
    ISO 8601 WITH timezone; season int >= 0; episode int >= 1."""
    if not isinstance(data, dict):
        return None, 'sidecar must be a JSON object'

    unknown = set(data) - SIDECAR_KEYS
    if unknown:
        return None, f'unknown sidecar key(s): {", ".join(sorted(unknown))}'

    clean: dict = {}

    if 'title' in data:
        title = data['title']
        if not isinstance(title, str) or not (1 <= len(title) <= 500):
            return None, 'title must be a string of 1-500 characters'
        clean['title'] = title

    if 'description' in data:
        description = data['description']
        if not isinstance(description, str):
            return None, 'description must be a string'
        clean['description'] = description

    if 'published_at' in data:
        raw = data['published_at']
        if not isinstance(raw, str) or not raw.strip():
            return None, 'published_at must be an ISO 8601 datetime string'
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None, 'published_at must be a valid ISO 8601 datetime'
        if parsed.tzinfo is None:
            return None, 'published_at must include a timezone'
        clean['published_at'] = parsed.astimezone(timezone.utc).strftime(_ISO_FORMAT)

    if 'season' in data:
        season = data['season']
        if isinstance(season, bool) or not isinstance(season, int) or season < 0:
            return None, 'season must be an integer >= 0'
        clean['season'] = season

    if 'episode' in data:
        episode = data['episode']
        if isinstance(episode, bool) or not isinstance(episode, int) or episode < 1:
            return None, 'episode must be an integer >= 1'
        clean['episode'] = episode

    return clean, None


def _parse_aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def synthesize_published_at(entries: list[dict], now_iso: str) -> str | None:
    """entries sorted ascending by (season, episode); each has
    'published_at' (explicit ISO str or None). Fills gaps in place.
    Rules: explicit dates must be strictly increasing in entry order, else
    return an error string naming both offending episode_ids (hard error).
    The final entry, if unset, anchors at now_iso. Leading run before the
    first anchor: step back SYNTH_STEP per entry. Between two anchors:
    space evenly across the interval. Returns None on success."""
    if not entries:
        return None

    last = len(entries) - 1
    explicit_idx = [i for i, e in enumerate(entries) if e.get('published_at')]
    parsed = {i: _parse_aware(entries[i]['published_at']) for i in explicit_idx}

    # The final entry is always an anchor -- either its own explicit date,
    # or an implicit now_iso anchor when unset. Compute that anchor (without
    # writing it back yet) so the strict-increase check below sees the full
    # anchor chain, including a preceding explicit date that collides with
    # "now" -- not just pairs of originally-explicit dates.
    final_forced = entries[last].get('published_at') is None
    if final_forced:
        parsed[last] = _parse_aware(now_iso)

    anchors = sorted(parsed)

    for a, b in zip(anchors, anchors[1:]):
        if parsed[b] <= parsed[a]:
            return (f"publish dates out of order: {entries[a]['episode_id']} "
                     f"must be before {entries[b]['episode_id']}")

    if final_forced:
        entries[last]['published_at'] = now_iso

    # Leading run before the first anchor: step back SYNTH_STEP per entry.
    first = anchors[0]
    for i in range(first - 1, -1, -1):
        steps_back = first - i
        dt = parsed[first] - SYNTH_STEP * steps_back
        entries[i]['published_at'] = dt.strftime(_ISO_FORMAT)
        parsed[i] = dt

    # Even spacing between consecutive anchors.
    for a, b in zip(anchors, anchors[1:]):
        span = b - a
        if span <= 1:
            continue
        start, end = parsed[a], parsed[b]
        step = (end - start) / span
        for k in range(1, span):
            dt = start + step * k
            entries[a + k]['published_at'] = dt.strftime(_ISO_FORMAT)
            parsed[a + k] = dt

    return None


def plan_hash(sources: list[Path]) -> str:
    """sha256 over sorted (name, size, mtime_ns) tuples; commit refuses a
    stale hash (TOCTOU guard)."""
    tuples = sorted(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sources
    )
    payload = json.dumps(tuples, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def build_import_plan(slug: str, sources: list[Path], existing_ids: set[str],
                      *, overwrite: bool, now_iso: str) -> dict:
    """Returns the dry-run plan:
    {'slug', 'overwrite', 'planHash',
     'entries': [{'episodeId','season','episode','title','audioFile',
                  'descriptionFile','artworkFile','sidecarFile',
                  'publishedAt','publishedAtSource': 'explicit'|'synthesized',
                  'bytes', 'warnings': [], 'errors': []}],
     'rejected': [{'file', 'reason'}],
     'totals': {'importable': N, 'rejected': N, 'errors': N, 'bytes': N}}"""
    rejected: list[dict] = []
    groups: dict[str, list[Path]] = {}

    for path in sources:
        name = path.name
        if name.startswith('.'):
            rejected.append({'file': name, 'reason': 'hidden file (dotfile)'})
            continue
        if path.suffix.lower() in _TEMP_SUFFIXES:
            rejected.append({'file': name,
                              'reason': 'incomplete file (.part/.tmp)'})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            rejected.append({'file': name, 'reason': 'empty file (0 bytes)'})
            continue
        ext = path.suffix.lower()
        if ext in AUDIO_LIKE_EXTS:
            rejected.append({'file': name, 'reason': FFMPEG_HINT})
            continue
        groups.setdefault(path.stem, []).append(path)

    candidates: list[dict] = []

    for stem, files in sorted(groups.items()):
        files = sorted(files, key=lambda f: f.name)
        audio = next((f for f in files if f.suffix.lower() in AUDIO_EXTS), None)
        sidecar = next((f for f in files if f.suffix.lower() == SIDECAR_EXT), None)
        description = next((f for f in files if f.suffix.lower() == DESCRIPTION_EXT), None)
        artwork = next((f for f in files if f.suffix.lower() in ARTWORK_EXTS), None)

        if audio is None:
            if sidecar is not None:
                rejected.append({'file': sidecar.name,
                                  'reason': 'sidecar has no matching audio file'})
            continue

        parsed = parse_basename(stem)
        if parsed is None:
            rejected.append({'file': audio.name,
                              'reason': 'file name does not match the sNNeNN naming scheme'})
            continue

        token, season, episode, fname_title = parsed
        episode_id = token
        errors: list[str] = []
        title = fname_title
        published_at = None
        sidecar_name = sidecar.name if sidecar else None

        if sidecar is not None:
            try:
                raw = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                errors.append(f'invalid sidecar JSON: {exc}')
                raw = None
            if raw is not None:
                clean, err = validate_sidecar(raw)
                if err:
                    errors.append(f'invalid sidecar: {err}')
                else:
                    overridden = 'season' in clean or 'episode' in clean
                    if 'season' in clean:
                        season = clean['season']
                    if 'episode' in clean:
                        episode = clean['episode']
                    if overridden:
                        episode_id = f's{season:02d}e{episode:02d}'
                        if not is_valid_episode_id(episode_id):
                            errors.append(
                                f'season/episode override out of range: {episode_id}')
                    else:
                        assert is_valid_episode_id(episode_id)
                    if 'title' in clean:
                        title = clean['title']
                    if 'published_at' in clean:
                        published_at = clean['published_at']

        if not title:
            title = f'Episode {episode}'

        candidates.append({
            'episode_id': episode_id,
            'season': season,
            'episode': episode,
            'title': title,
            'audio_file': audio.name,
            'description_file': description.name if description else None,
            'artwork_file': artwork.name if artwork else None,
            'sidecar_file': sidecar_name,
            'published_at': published_at,
            'bytes': audio.stat().st_size,
            'errors': errors,
            'warnings': [],
        })

    # Duplicate episode ids within the batch: per-file error on both.
    by_id: dict[str, list[int]] = {}
    for i, c in enumerate(candidates):
        by_id.setdefault(c['episode_id'], []).append(i)
    for eid, idxs in by_id.items():
        if len(idxs) > 1:
            for i in idxs:
                others = [candidates[j]['audio_file'] for j in idxs if j != i]
                candidates[i]['errors'].append(
                    f'duplicate episode id {eid} also used by {", ".join(others)}')

    # Collisions against already-imported episodes.
    for c in candidates:
        if c['episode_id'] in existing_ids and not overwrite:
            c['errors'].append(f'episode {c["episode_id"]} already exists')

    candidates.sort(key=lambda c: (c['season'], c['episode']))

    for c in candidates:
        c['_explicit'] = c['published_at'] is not None

    # Entries that already carry an error (duplicate id, existing-id
    # collision, invalid sidecar) are never committed, so they must not act
    # as date-synthesis anchors -- a bogus sidecar date on a rejected entry
    # would otherwise backdate its clean siblings -- nor receive a
    # synthesized value themselves. Synthesis runs over the clean subset
    # only; an errored entry keeps whatever explicit date it already had
    # (or None), which the UI/commit engine ignore anyway since errors is
    # non-empty.
    clean = [c for c in candidates if not c['errors']]
    synth_err = synthesize_published_at(clean, now_iso)
    if synth_err:
        for c in clean:
            c['errors'].append(synth_err)
            if c['published_at'] is None:
                c['published_at'] = now_iso

    entries = []
    for c in candidates:
        entries.append({
            'episodeId': c['episode_id'],
            'season': c['season'],
            'episode': c['episode'],
            'title': c['title'],
            'audioFile': c['audio_file'],
            'descriptionFile': c['description_file'],
            'artworkFile': c['artwork_file'],
            'sidecarFile': c['sidecar_file'],
            'publishedAt': c['published_at'],
            'publishedAtSource': 'explicit' if c['_explicit'] else 'synthesized',
            'bytes': c['bytes'],
            'warnings': c['warnings'],
            'errors': c['errors'],
        })

    importable = sum(1 for e in entries if not e['errors'])
    errored = sum(1 for e in entries if e['errors'])
    total_bytes = sum(e['bytes'] for e in entries if not e['errors'])

    return {
        'slug': slug,
        'overwrite': overwrite,
        'planHash': plan_hash(sources),
        'entries': entries,
        'rejected': rejected,
        'totals': {
            'importable': importable,
            'rejected': len(rejected),
            'errors': errored,
            'bytes': total_bytes,
        },
    }


# ---------------------------------------------------------------------------
# Commit half -- staging/import-dir moves, embedded artwork, background job.
# ---------------------------------------------------------------------------

# Free-space margin required over the plan's total importable bytes before a
# commit is allowed to start (headroom for filesystem overhead and any
# concurrent write elsewhere on the volume).
_FREE_SPACE_MARGIN = 1.1

# Registry: one job entry per feed slug, guarded by _import_lock. Mirrors
# main_app/routes.py's _bg_refresh_inflight/_bg_refresh_lock pattern
# (routes.py:83-103), but keeps richer per-slug state (progress + report)
# rather than a bare "in flight" set, since get_import_status needs to read
# it back after the job finishes -- not just while it runs.
_import_jobs: dict[str, dict] = {}
_import_lock = threading.Lock()


def start_commit(slug: str, plan: dict, *, db, storage) -> tuple[bool, str]:
    """Start committing ``plan`` (built by ``build_import_plan``) for
    ``slug`` on a background daemon thread.

    Refuses to start (returning ``(False, reason)`` without registering
    anything) when: another import is already running for this feed, or
    the destination volume does not have at least ``_FREE_SPACE_MARGIN``
    times the plan's importable bytes free. Otherwise registers the job as
    running, captures whether the feed already had episodes (for the
    initial-import auto-process rule -- see ``_commit_entries``) BEFORE
    spawning the thread so a race with the thread's own inserts cannot
    flip it, and returns ``(True, 'started')``.
    """
    with _import_lock:
        job = _import_jobs.get(slug)
        if job is not None and job['state'] == 'running':
            return False, 'import already running'

        total_bytes = plan.get('totals', {}).get('bytes', 0)
        free_bytes = shutil.disk_usage(storage.data_dir).free
        if free_bytes <= total_bytes * _FREE_SPACE_MARGIN:
            return False, 'insufficient free disk space'

        _, existing_count = db.get_episodes(slug, status='all', limit=1)
        had_episodes = existing_count > 0

        _import_jobs[slug] = {
            'state': 'running',
            'processed': 0,
            'total': len(plan.get('entries', [])),
            'startedAt': utc_now_iso(),
            'report': None,
        }

    thread = threading.Thread(
        target=_run_commit,
        args=(slug, plan, db, storage, had_episodes),
        daemon=True,
    )
    thread.start()
    return True, 'started'


def get_import_status(slug: str) -> dict:
    """{'state': 'idle'|'running'|'done'|'error', 'processed': n,
    'total': n, 'startedAt': iso|None, 'report': {...} when done/error}.

    A slug with no job ever started reads as idle -- there is nothing to
    distinguish "never ran" from "finished long ago and was forgotten"
    here, but nothing in this module ever drops a completed entry either,
    so in practice idle only shows before the first commit.
    """
    with _import_lock:
        job = _import_jobs.get(slug)
        if job is None:
            return {'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None}
        status = {
            'state': job['state'],
            'processed': job['processed'],
            'total': job['total'],
            'startedAt': job['startedAt'],
        }
        if job['state'] in ('done', 'error'):
            status['report'] = job['report']
        return status


def _bump_processed(slug: str) -> None:
    with _import_lock:
        job = _import_jobs.get(slug)
        if job is not None:
            job['processed'] += 1


def _run_commit(slug: str, plan: dict, db, storage, had_episodes: bool) -> None:
    """Background-thread entry point: run the batch, then flip the job to
    'done' (with its report) or -- only on an unexpected exception escaping
    the whole batch, not a per-file failure -- 'error'."""
    try:
        report = _commit_entries(slug, plan, db, storage, had_episodes)
        with _import_lock:
            job = _import_jobs.get(slug)
            if job is not None:
                job['state'] = 'done'
                job['report'] = report
    except Exception as exc:
        logger.exception(f"[{slug}] import commit crashed")
        with _import_lock:
            job = _import_jobs.get(slug)
            if job is not None:
                job['state'] = 'error'
                job['report'] = {'error': str(exc)}


def _resolve_source_files(storage, slug: str) -> tuple[dict[str, Path], Path]:
    """filename -> Path across the user-managed import dir and the staging
    dir, staging winning on a name collision (it is the more recent write --
    a browser upload of the same filename after a manual drop). Also
    returns the staging dir path so callers can tell which files came from
    it (those get deleted post-commit; import-dir sidecars are left alone).
    """
    files: dict[str, Path] = {}
    import_dir = storage.import_source_dir(slug)
    if import_dir.exists():
        for path in import_dir.iterdir():
            if path.is_file():
                files[path.name] = path

    staging_dir = storage.import_staging_dir(slug)
    if staging_dir.exists():
        for path in staging_dir.iterdir():
            if path.is_file():
                files[path.name] = path

    return files, staging_dir


def _clear_queue_row(db, slug: str, episode_id: str) -> None:
    """Drop any auto_process_queue row for this episode before an overwrite
    reset (same SQL pattern as database.episodes.delete_episode_rows), so a
    stale queued/processing row from the previous import cannot resurrect
    against the freshly re-imported file."""
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?",
        (podcast['id'], episode_id),
    )
    conn.commit()


def _commit_entry(slug: str, entry: dict, db, storage,
                  source_files: dict[str, Path], staging_dir: Path) -> tuple[str, object]:
    """Commit one plan entry. Returns ('ok', result_dict) or
    ('error', message) -- never raises for an ordinary per-file problem, so
    the caller's loop can always move on to the next entry."""
    episode_id = entry['episodeId']

    audio_path = source_files.get(entry['audioFile'])
    if audio_path is None:
        return 'error', 'source audio file no longer present'
    try:
        current_size = audio_path.stat().st_size
    except OSError:
        return 'error', 'source audio file no longer present'
    if current_size != entry['bytes']:
        return 'error', 'file changed since scan'

    duration = get_audio_duration(str(audio_path))
    if duration is None:
        return 'error', 'not playable audio'

    is_overwrite = db.get_episode(slug, episode_id) is not None
    if is_overwrite:
        # Full reset (spec): wipe the previous file/details/ad-data/queue
        # row before the new audio lands, then reuse upsert_episode below --
        # the episodes row itself is never dropped and re-inserted.
        storage.cleanup_episode_files(slug, episode_id)
        db.clear_episode_details(slug, episode_id)
        db.clear_episode_ad_data(slug, episode_id)
        _clear_queue_row(db, slug, episode_id)

    final_path = storage.get_original_path(slug, episode_id)
    shutil.move(str(audio_path), str(final_path))

    description = None
    description_name = entry.get('descriptionFile')
    if description_name:
        description_path = source_files.get(description_name)
        if description_path is not None and description_path.exists():
            try:
                description = description_path.read_text(
                    encoding='utf-8', errors='replace')
            except OSError:
                description = None

    # Insert/update the row before any chapters/artwork side effects --
    # save_chapters_json and save_episode_artwork's DB write both no-op
    # silently when the episode row is not there yet (Task 8 pattern).
    db.upsert_episode(
        slug, episode_id,
        original_url=f'local://{episode_id}',
        status='discovered',
        title=entry['title'],
        description=description,
        published_at=entry['publishedAt'],
        episode_number=entry['episode'],
        season_number=entry['season'],
        original_duration=duration,
        p20_item_json=None,
    )

    chapters = probe_chapters(str(final_path))
    if chapters and len(chapters) >= MIN_PRESERVED_CHAPTERS:
        storage.save_chapters_json(slug, episode_id, {
            'version': '1.2.0',
            'chapters': [
                {'startTime': int(ch['start']), 'title': ch.get('title') or ''}
                for ch in chapters
            ],
        })

    artwork_name = entry.get('artworkFile')
    if artwork_name:
        artwork_path = source_files.get(artwork_name)
        if artwork_path is not None and artwork_path.exists():
            try:
                raw = artwork_path.read_bytes()
            except OSError:
                raw = None
            if raw:
                mime = _detect_image_mime(raw)
                if mime in ('image/jpeg', 'image/png'):
                    storage.save_episode_artwork(slug, episode_id, raw, mime,
                                                 evict=False)
    else:
        embedded = extract_embedded_artwork(str(final_path))
        if embedded:
            image_data, mime = embedded
            storage.save_episode_artwork(slug, episode_id, image_data, mime,
                                         evict=False)

    # Staging cleanup: the audio file is already gone (moved above). Any
    # OTHER file this entry references (sidecar json / description / cover)
    # that also lives in staging is deleted too -- staging is ephemeral and
    # MinusPod-managed. Checked per-file against its OWN parent (not just
    # "did the audio come from staging") so a sidecar in staging is cleaned
    # up even for an import-dir-sourced audio file. The user-managed import
    # dir's sidecars are left untouched regardless (only the audio there
    # was consumed by the move).
    for name in (description_name, artwork_name, entry.get('sidecarFile')):
        if not name:
            continue
        sidecar_path = source_files.get(name)
        if (sidecar_path is not None and sidecar_path.parent == staging_dir
                and sidecar_path.exists()):
            try:
                sidecar_path.unlink()
            except OSError:
                pass

    return 'ok', {
        'episodeId': episode_id,
        'title': entry['title'],
        'publishedAt': entry['publishedAt'],
        'description': description,
    }


def _commit_entries(slug: str, plan: dict, db, storage, had_episodes: bool) -> dict:
    """Commit every entry in ``plan['entries']`` (idempotent -- a per-file
    failure is recorded and the batch continues), then queue newly
    imported episodes for auto-process when applicable, rebuild the served
    RSS once, and clean up an emptied staging dir. Returns the report
    stored against this job in ``_import_jobs``."""
    entries = plan.get('entries', [])
    source_files, staging_dir = _resolve_source_files(storage, slug)

    committed: list[dict] = []
    skipped: list[dict] = []
    failed: list[dict] = []

    for entry in entries:
        episode_id = entry['episodeId']
        # Any entry the planning half already flagged as non-committable
        # (duplicate id, existing-id collision without overwrite, invalid
        # sidecar, out-of-order publish dates) is skipped outright.
        if entry.get('errors'):
            skipped.append({'episodeId': episode_id, 'errors': entry['errors']})
            _bump_processed(slug)
            continue

        try:
            status, result = _commit_entry(slug, entry, db, storage,
                                           source_files, staging_dir)
        except Exception as exc:
            # Per-file failure must never abort the batch -- an unexpected
            # exception (disk-full mid-move, a DB error) is caught here the
            # same as the ordinary error paths _commit_entry returns, so
            # every remaining entry still gets a chance to commit.
            logger.exception(f"[{slug}:{episode_id}] import commit entry crashed")
            status, result = 'error', str(exc)
        if status == 'ok':
            committed.append(result)
        else:
            failed.append({'episodeId': episode_id, 'error': result})
        _bump_processed(slug)

    queued: list[str] = []
    podcast = db.get_podcast_by_slug(slug)
    # Initial-import rule: an empty feed's very first batch never
    # auto-queues (there is nothing to compare "fresh" against and a
    # first-ever archive import is a bulk backfill, not new content), so
    # had_episodes -- captured before this batch's rows were inserted --
    # gates queueing regardless of what the feed looks like now.
    if (had_episodes and committed and podcast is not None
            and db.is_auto_process_enabled_for_podcast(slug, podcast=podcast)):
        apply_fresh_boost = db.get_setting_bool('process_new_episodes_first', True)
        feed_priority = podcast.get('queue_priority')
        for item in committed:
            priority = compute_queue_priority(
                feed_priority, item['publishedAt'], manual=False,
                apply_fresh_boost=apply_fresh_boost)
            queue_id = db.queue_episode_for_processing(
                slug, item['episodeId'], f"local://{item['episodeId']}",
                item['title'], item['publishedAt'], item['description'],
                priority=priority)
            if queue_id is not None:
                queued.append(item['episodeId'])

    # Local import inside main_app; imported lazily like api/local_episodes.py's
    # _rebuild -- local_feed_builder pulls in main_app at module level, which
    # imports the api package (and this module) before it finishes
    # initializing, so a module-level import here would be circular.
    from local_feed_builder import rebuild_local_feed
    rebuild_local_feed(slug)

    if staging_dir.exists():
        try:
            if not any(staging_dir.iterdir()):
                staging_dir.rmdir()
        except OSError:
            pass

    return {
        'committed': [item['episodeId'] for item in committed],
        'skipped': skipped,
        'failed': failed,
        'queued': queued,
    }
