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

import fcntl
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
from storage import _detect_image_mime, _safe_join_under
from utils.atomic_json import write_json_atomic
from utils.audio import extract_embedded_artwork, get_audio_duration
from utils.time import utc_now_iso
from utils.validation import is_dangerous_slug, is_valid_episode_id

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
    None when the stem does not match the scheme.

    The returned id is canonicalized to minimal zero-padded width
    (``f's{season:02d}e{episode:02d}'`` -- 2 digits minimum, wider only
    when the number itself needs it), even though the filename token that
    produced it may be wider: 's01e0001 - Title' and 's01e01 - Title' both
    mint id 's01e01'. This is the exact formula build_import_plan already
    uses to mint an id from a sidecar season/episode override, so a
    token-derived id and a sidecar-derived id for the same numbers are now
    always identical -- required for duplicate/collision detection (by id)
    to actually catch a wide-token file and its sidecar-overridden sibling
    as the same episode instead of two different ones. Filenames on disk
    keep accepting any width the naming scheme allows; only the minted id
    normalizes.
    """
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
    episode_id = f's{season:02d}e{episode:02d}'
    return (episode_id, season, episode, title)


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
    return an error string naming both offending episode_ids (hard error)
    -- and mark exactly those two entries (``entries[a]['_batch_error'] =
    True``) so a caller can attribute the error to the offending pair
    instead of the whole batch. The final entry, if unset, anchors at
    now_iso. Leading run before the first anchor: step back SYNTH_STEP per
    entry. Between two anchors: space evenly across the interval. Returns
    None on success."""
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

    # strict=False: this is the pairwise idiom -- anchors[1:] is always
    # exactly one element shorter than anchors by construction, so the
    # "unequal lengths" strict=True guards against can never fire here.
    for a, b in zip(anchors, anchors[1:], strict=False):
        if parsed[b] <= parsed[a]:
            entries[a]['_batch_error'] = True
            entries[b]['_batch_error'] = True
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
    # strict=False: same pairwise idiom as above.
    for a, b in zip(anchors, anchors[1:], strict=False):
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


def plan_hash(sources: list[Path], overwrite: bool = False) -> str:
    """sha256 over sorted (name, size, mtime_ns) tuples plus the overwrite
    flag; commit refuses a stale hash (TOCTOU guard).

    A source that vanishes between listing and stat (e.g. a running
    commit's shutil.move racing a concurrent scan) is skipped rather than
    raising -- same guard as the main plan-building loop below. Dropping
    it from the hash is the correct outcome anyway: a commit re-scan that
    no longer sees the file will compute a different hash than the
    original scan, which is exactly the staleness a caller needs to
    detect.

    overwrite rides in the hash for the same reason: it changes which
    entries error out vs. commit (a collision is an error when overwrite
    is False, a clean overwrite when True), so a commit whose overwrite
    doesn't match the reviewed scan must 409 as stale too -- otherwise the
    operator could review a plan built one way and have commit silently
    rebuild and act on it the other way. overwrite defaults to False only
    so callers that don't care about it (existing tests fixturing sources
    alone) don't have to pass it; build_import_plan below always passes
    its own overwrite explicitly."""
    tuples = []
    for p in sources:
        try:
            stat_result = p.stat()
        except OSError:
            continue
        tuples.append((p.name, stat_result.st_size, stat_result.st_mtime_ns))
    tuples.sort()
    payload = json.dumps((tuples, overwrite), sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def _canonical_episode_id(episode_id: str) -> str:
    """Normalize an episode id to minimal zero-padded width (sNNeNN), the
    same formula parse_basename/sidecar-override id minting already use.

    A pre-fix wide id like 's01e0006' (imported before ac2d1eb3's id
    canonicalization) normalizes to 's01e06', so it compares equal to a
    freshly-scanned candidate that always mints the minimal-width id for the
    same season/episode -- without this, an old wide-id row and a rescan of
    the same episode look like two different ids and collision detection
    (replacesExisting / the overwrite-required error) never fires for it.
    An id that doesn't match the sNNeNN shape (should not occur for a local
    feed) passes through unchanged rather than raising."""
    match = _TOKEN_RE.match(episode_id)
    if not match:
        return episode_id
    season = int(match.group(1))
    episode = int(match.group(2))
    return f's{season:02d}e{episode:02d}'


def build_import_plan(slug: str, sources: list[Path], existing_ids: set[str],
                      *, overwrite: bool, now_iso: str, source: str = 'both') -> dict:
    """Returns the dry-run plan:
    {'slug', 'overwrite', 'source', 'planHash',
     'entries': [{'episodeId','season','episode','title','audioFile',
                  'audioPath','descriptionFile','descriptionPath',
                  'artworkFile','artworkPath','sidecarFile','sidecarPath',
                  'publishedAt','publishedAtSource': 'explicit'|'synthesized',
                  'bytes','mtimeNs', 'warnings': [], 'errors': [],
                  'replacesExisting': bool, 'replacesExistingId': str | None}],
     'rejected': [{'file', 'reason'}],
     'batchErrors': [msg, ...],
     'totals': {'importable': N, 'rejected': N, 'errors': N, 'bytes': N}}

    ``source`` ('staging'|'directory'|'both', matching the scan/commit API
    request) rides along on the plan purely for the commit engine: it gates
    whether ``_commit_entries`` is allowed to sweep the staging directory at
    all -- a plan scanned/committed with source='directory' must never touch
    staging, even if something else left files there. It plays no role in
    plan_hash or in resolving any entry's files (those already come from
    each entry's own resolved *Path key, regardless of source).

    batchErrors holds errors that apply to the whole batch rather than one
    entry -- currently just an out-of-order explicit-date pair (see
    synthesize_published_at). The two offending entries also get the same
    message appended to their own ``errors`` (so they render as errored
    rows); every other entry stays clean (``errors: []``) even though the
    batch as a whole cannot commit -- commit must check batchErrors
    explicitly rather than relying on every entry's errors being non-empty.

    replacesExisting is true whenever the entry's episodeId already exists
    in the feed, regardless of overwrite -- it's a collision marker, not an
    outcome. Whether that collision becomes an error (overwrite=False) or a
    clean, committable overwrite (overwrite=True) is entirely down to
    errors being empty or not; a caller counting "how many will this commit
    actually replace" wants entries where replacesExisting is true AND
    errors is empty.

    replacesExistingId is the ACTUAL existing row's episode_id when
    replacesExisting is true, else None -- normally identical to episodeId,
    but for a row imported before ac2d1eb3's id canonicalization it can be
    the row's original wide-spelled id (e.g. 's01e0006' for a candidate
    that mints 's01e06'). The commit engine needs this: an overwrite has to
    replace THAT row, not leave it behind under its old id while inserting
    a second row under the canonical one.

    The *Path keys (audioPath/descriptionPath/artworkPath/sidecarPath) and
    mtimeNs exist for the commit engine: it reads a file by its exact
    resolved path from here rather than re-resolving a bare filename
    against the staging/import directories, and re-stats bytes+mtimeNs
    against the live file as a TOCTOU guard before committing it."""
    # Canonicalize once up front so every membership test below compares
    # like with like -- see _canonical_episode_id. Keeps the mapping back
    # to the ORIGINAL (possibly pre-fix wide-spelled) id too: the commit
    # engine needs to know which actual row a canonical-id collision
    # resolves to, since an overwrite has to replace THAT row rather than
    # leave it behind while inserting a second one under the canonical id.
    existing_by_canonical: dict[str, str] = {}
    for eid in existing_ids:
        existing_by_canonical.setdefault(_canonical_episode_id(eid), eid)
    existing_ids = set(existing_by_canonical.keys())

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

        try:
            audio_stat = audio.stat()
        except OSError:
            # File vanished between listing and stat (e.g. a concurrent
            # move/delete) -- same guard as the size-filter loop above and
            # plan_hash: skip this candidate rather than 500ing the whole scan.
            continue
        candidates.append({
            'episode_id': episode_id,
            'season': season,
            'episode': episode,
            'title': title,
            'audio_file': audio.name,
            'audio_path': str(audio),
            'description_file': description.name if description else None,
            'description_path': str(description) if description else None,
            'artwork_file': artwork.name if artwork else None,
            'artwork_path': str(artwork) if artwork else None,
            'sidecar_file': sidecar_name,
            'sidecar_path': str(sidecar) if sidecar else None,
            'published_at': published_at,
            'bytes': audio_stat.st_size,
            'mtime_ns': audio_stat.st_mtime_ns,
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

    # Collisions against already-imported episodes. Recorded on every
    # candidate that collides (replaces_existing), overwrite or not, so the
    # client can count exactly how many entries an overwrite=True commit
    # will actually replace -- errors is what gates whether it does; a
    # colliding id only turns into an error when overwrite is off.
    for c in candidates:
        c['replaces_existing'] = c['episode_id'] in existing_ids
        c['replaces_existing_id'] = (
            existing_by_canonical.get(c['episode_id']) if c['replaces_existing'] else None
        )
        if c['replaces_existing'] and not overwrite:
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
    batch_errors: list[str] = []
    if synth_err:
        batch_errors.append(synth_err)
        for c in clean:
            # Only the pair synthesize_published_at flagged gets the error
            # on its own entry -- every other clean candidate is unrelated
            # and must not be stamped with an error naming two ids it has
            # nothing to do with. The batch as a whole still can't commit
            # (see batchErrors above); that gate is enforced separately.
            if c.pop('_batch_error', False):
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
            # Resolved paths, not just basenames: the commit engine reads
            # these directly rather than re-resolving by basename, so a
            # same-named file in more than one scanned directory (staging
            # vs. the user-managed import dir) can never cause a commit to
            # pick up the wrong one.
            'audioPath': c['audio_path'],
            'descriptionFile': c['description_file'],
            'descriptionPath': c['description_path'],
            'artworkFile': c['artwork_file'],
            'artworkPath': c['artwork_path'],
            'sidecarFile': c['sidecar_file'],
            'sidecarPath': c['sidecar_path'],
            'publishedAt': c['published_at'],
            'publishedAtSource': 'explicit' if c['_explicit'] else 'synthesized',
            'bytes': c['bytes'],
            'mtimeNs': c['mtime_ns'],
            'warnings': c['warnings'],
            'errors': c['errors'],
            'replacesExisting': c['replaces_existing'],
            'replacesExistingId': c['replaces_existing_id'],
        })

    importable = sum(1 for e in entries if not e['errors'])
    errored = sum(1 for e in entries if e['errors'])
    total_bytes = sum(e['bytes'] for e in entries if not e['errors'])

    return {
        'slug': slug,
        'overwrite': overwrite,
        'source': source,
        'planHash': plan_hash(sources, overwrite),
        'entries': entries,
        'rejected': rejected,
        'batchErrors': batch_errors,
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

# Cross-worker job registry: gunicorn runs multiple worker processes, each
# with its own Python heap, so an in-memory dict (the previous design) is
# only visible to whichever worker happened to run the commit -- a status
# poll landing on a different worker sees no job at all, and worse, the
# "one import per feed at a time" guard reading the same per-process dict
# lets two different workers both start a commit for the same feed and race
# on the same staging files.
#
# Replaced with per-feed state on disk under
# ``<data_dir>/.import-jobs/<slug>.json`` (read/written by every worker) plus
# a per-feed flock lockfile (``<slug>.lock``) held for the duration of the
# commit thread as the running-guard -- same fcntl.flock cross-process
# mechanism and stale-lock probe idiom as processing_queue.py's
# ProcessingQueue, just one lock per feed slug instead of one global lock.

def _jobs_dir_path(storage) -> Path:
    """The per-feed import-job-state directory, WITHOUT creating it.

    Read paths (status polls, lock probes) must not have the side effect of
    conjuring a directory that nothing has ever written to -- only a write
    path (state write, lock acquire) may create it, via ``_jobs_dir``
    below."""
    return storage.data_dir / '.import-jobs'


def _jobs_dir(storage) -> Path:
    """Same directory as ``_jobs_dir_path``, created if missing. Only call
    this from a write path."""
    d = _jobs_dir_path(storage)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _job_state_path(storage, slug: str, *, create: bool = False) -> Path:
    base = _jobs_dir(storage) if create else _jobs_dir_path(storage)
    return _safe_join_under(base, f'{slug}.json')


def _job_lock_path(storage, slug: str, *, create: bool = False) -> Path:
    base = _jobs_dir(storage) if create else _jobs_dir_path(storage)
    return _safe_join_under(base, f'{slug}.lock')


def _read_job_state(storage, slug: str) -> dict | None:
    path = _job_state_path(storage, slug)
    try:
        if path.exists():
            content = path.read_text()
            if content.strip():
                return json.loads(content)
    except (OSError, ValueError) as e:
        logger.debug(f"[{slug}] could not read import job state: {e}")
    return None


def _write_job_state(storage, slug: str, state: dict) -> None:
    if not write_json_atomic(_job_state_path(storage, slug, create=True), state):
        logger.warning(f"[{slug}] could not write import job state")


def _clear_job_state(storage, slug: str) -> None:
    try:
        _job_state_path(storage, slug).unlink()
    except OSError:
        pass


def clear_job_files(storage, slug: str) -> None:
    """Best-effort removal of this feed's import job state + lock files
    (e.g. on feed delete), so a slug reused later never resurrects the
    deleted feed's stale import report or an unremovable phantom lock."""
    _clear_job_state(storage, slug)
    try:
        _job_lock_path(storage, slug).unlink()
    except OSError:
        pass


def _try_acquire_import_lock(storage, slug: str):
    """Non-blocking exclusive acquire of the per-feed import lock.

    Returns the open file object (lock held -- caller must eventually pass
    it to ``_release_import_lock``) on success, or ``None`` when another
    holder (any process, including this one via a different open file
    description) already has it.
    """
    fh = open(_job_lock_path(storage, slug, create=True), 'w')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    except OSError:
        fh.close()
        raise
    return fh


def _release_import_lock(fh) -> None:
    try:
        fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError as e:
        logger.warning(f"Error releasing import lock: {e}")
    finally:
        fh.close()


def _lock_is_held(storage, slug: str) -> bool:
    """Non-blocking probe: is ANY process currently holding this feed's
    import lock? Mirrors ProcessingQueue._clear_stale_state's orphan probe
    -- opening a separate file description to try-acquire the same lock
    file does not disturb a real holder's lock (flock locks are scoped to
    the open file description that set them, not the process or fd
    number), so this is safe to call while a commit may be in flight."""
    lock_path = _job_lock_path(storage, slug)
    if not lock_path.exists():
        return False
    try:
        with open(lock_path, 'w') as probe_fd:
            try:
                fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe_fd, fcntl.LOCK_UN)
                return False  # acquired it -> nobody was holding it
            except BlockingIOError:
                return True  # someone holds it right now
    except OSError as e:
        logger.debug(f"[{slug}] could not probe import lock: {e}")
        return False


def start_commit(slug: str, plan: dict, *, db, storage) -> tuple[bool, str]:
    """Start committing ``plan`` (built by ``build_import_plan``) for
    ``slug`` on a background daemon thread.

    Refuses to start (returning ``(False, reason)`` without registering
    anything) when: the plan was built for a different slug (a stale plan
    object reused against the wrong feed), another import is already
    running for this feed (the per-feed flock is already held -- by this
    worker or any other), or the destination volume does not have at least
    ``_FREE_SPACE_MARGIN`` times the plan's importable bytes free. Otherwise
    writes the job state as running, captures whether the feed already had
    episodes (for the initial-import auto-process rule -- see
    ``_commit_entries``) BEFORE spawning the thread so a race with the
    thread's own inserts cannot flip it, and returns ``(True, 'started')``.

    The flock is acquired here and held by the spawned thread for the
    duration of the commit (passed through as an open file object); the
    thread releases it in ``_run_commit``'s ``finally``.
    """
    if plan.get('slug') != slug:
        return False, 'plan slug mismatch'
    if is_dangerous_slug(slug):
        return False, 'invalid slug'

    lock_fh = _try_acquire_import_lock(storage, slug)
    if lock_fh is None:
        return False, 'import already running'

    try:
        total_bytes = plan.get('totals', {}).get('bytes', 0)
        free_bytes = shutil.disk_usage(storage.data_dir).free
        if free_bytes <= total_bytes * _FREE_SPACE_MARGIN:
            _release_import_lock(lock_fh)
            return False, 'insufficient free disk space'

        _, existing_count = db.get_episodes(slug, status='all', limit=1)
        had_episodes = existing_count > 0

        _write_job_state(storage, slug, {
            'state': 'running',
            'processed': 0,
            'total': len(plan.get('entries', [])),
            'startedAt': utc_now_iso(),
            'report': None,
        })
    except BaseException:
        _release_import_lock(lock_fh)
        raise

    thread = threading.Thread(
        target=_run_commit,
        args=(slug, plan, db, storage, had_episodes, lock_fh),
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        # thread.start() failing (e.g. the OS refuses a new thread) must not
        # leave a phantom 'running' job that can never finish and blocks
        # every future start_commit for this feed.
        _clear_job_state(storage, slug)
        _release_import_lock(lock_fh)
        raise
    return True, 'started'


def get_import_status(slug: str, storage=None) -> dict:
    """{'state': 'idle'|'running'|'done'|'error', 'processed': n,
    'total': n, 'startedAt': iso|None, 'report': {...} when done/error}.

    Reads straight off the per-feed state file -- no in-process registry
    backs this, so a fresh worker (or a fresh module import) that never
    called ``start_commit`` itself sees exactly what a worker that did
    would see. A slug with no state file ever written reads as idle.

    Self-heals a state file stuck on 'running' whose lock nobody actually
    holds (a worker that crashed mid-commit, e.g. OOM-killed, leaves the
    lock released by the OS but the state file never flipped to a terminal
    state): reports 'error' with an 'import interrupted' report instead of
    claiming the import is still going forever, and persists that
    correction so subsequent reads don't have to re-derive it.
    """
    if storage is None:
        from storage import Storage
        storage = Storage()
    if is_dangerous_slug(slug):
        return {'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None}

    job = _read_job_state(storage, slug)
    if job is None:
        return {'state': 'idle', 'processed': 0, 'total': 0, 'startedAt': None}

    if job.get('state') == 'running' and not _lock_is_held(storage, slug):
        job = dict(job)
        job['state'] = 'error'
        job['report'] = {'committed': [], 'skipped': [], 'failed': [], 'queued': [],
                         'error': 'import interrupted'}
        _write_job_state(storage, slug, job)

    status = {
        'state': job['state'],
        'processed': job.get('processed', 0),
        'total': job.get('total', 0),
        'startedAt': job.get('startedAt'),
    }
    if job['state'] in ('done', 'error'):
        status['report'] = job.get('report')
    return status


def _bump_processed(slug: str, storage) -> None:
    job = _read_job_state(storage, slug)
    if job is not None:
        job['processed'] = job.get('processed', 0) + 1
        _write_job_state(storage, slug, job)


def _run_commit(slug: str, plan: dict, db, storage, had_episodes: bool, lock_fh) -> None:
    """Background-thread entry point: run the batch, then flip the job to
    'done' (with its report) or -- only on an exception escaping the whole
    batch, not a per-file failure -- 'error'. Always releases the per-feed
    import lock this thread was handed by ``start_commit``.

    ``report`` is built here and handed to ``_commit_entries`` to mutate in
    place, so that even if something outside the per-entry loop raises (or
    the whole call is interrupted -- caught as ``BaseException`` so the
    ``finally`` below always leaves the job state in a terminal state
    rather than stuck 'running' forever), every outcome recorded up to that
    point is still visible in the stored report alongside the 'error' key.
    """
    report: dict = {'committed': [], 'skipped': [], 'failed': [], 'queued': []}
    error: BaseException | None = None
    try:
        _commit_entries(slug, plan, db, storage, had_episodes, report)
    except BaseException as exc:
        logger.exception(f"[{slug}] import commit crashed")
        error = exc
    finally:
        try:
            job = _read_job_state(storage, slug) or {}
            if error is not None:
                job['state'] = 'error'
                job['report'] = {**report, 'error': str(error)}
            else:
                job['state'] = 'done'
                job['report'] = report
            _write_job_state(storage, slug, job)
        finally:
            _release_import_lock(lock_fh)


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
                  overwrite: bool) -> tuple[str, object]:
    """Commit one plan entry. Returns ('ok', result_dict) or
    ('error', message) -- never raises for an ordinary per-file problem, so
    the caller's loop can always move on to the next entry.

    Reads the plan's own resolved paths (``audioPath``/``descriptionPath``/
    ``artworkPath``/``sidecarPath``) rather than re-resolving files by
    basename against the staging/import directories: a same-named file
    sitting in the OTHER scanned directory must never cause the wrong file
    to be committed.
    """
    episode_id = entry['episodeId']

    audio_path_str = entry.get('audioPath')
    if not audio_path_str:
        return 'error', 'source audio file no longer present'
    audio_path = Path(audio_path_str)
    try:
        stat_result = audio_path.stat()
    except OSError:
        return 'error', 'source audio file no longer present'
    # TOCTOU guard: both size and mtime must still match what the plan
    # snapshotted at scan time -- size alone would miss an in-place rewrite
    # that happens to land on the same byte count.
    if (stat_result.st_size != entry.get('bytes')
            or stat_result.st_mtime_ns != entry.get('mtimeNs')):
        return 'error', 'file changed since scan'

    # Duration is probed before any destructive action below, so a bad
    # re-upload can never wipe out a good existing episode.
    duration = get_audio_duration(str(audio_path))
    if duration is None:
        return 'error', 'not playable audio'

    # The plan's own matched-collision id, when it found one at scan time
    # (see build_import_plan's replacesExistingId) -- normally identical to
    # episode_id, but for a row imported before ac2d1eb3's id
    # canonicalization it can be that row's original wide-spelled id (e.g.
    # 's01e0006' for a candidate that mints 's01e06'). Falls back to
    # episode_id itself so the race-condition case (a row created under the
    # canonical id AFTER the plan was scanned, so the plan never saw it) is
    # still caught below exactly as before.
    existing_id = entry.get('replacesExistingId') or episode_id
    existing = db.get_episode(slug, existing_id)
    if existing is not None:
        # Authorization, not just existence: a plan built with
        # overwrite=False never clobbers, even if the episode was created
        # by something else after the plan was built and before commit ran.
        if not overwrite:
            return 'error', f'episode {episode_id} already exists'
        if existing.get('status') == 'processing':
            return 'error', 'episode is processing'

        if existing_id != episode_id:
            # Wide-spelled pre-fix row: overwrite means REPLACING it, not
            # leaving it (and its files) behind under its old id while a
            # second row gets inserted under the canonical one.
            # delete_episode_rows removes its files, cached artwork, queue
            # row, and the row itself (episode_details cascades via FK ON
            # DELETE CASCADE) -- the canonical id becomes the one true id
            # for this episode from here on; upsert_episode below inserts
            # fresh under it since no row exists there yet.
            db.delete_episode_rows(slug, [existing_id], storage)
        else:
            # Full reset (spec): wipe files, cached artwork, DB processing
            # state, episode_details, and any stale queue row before the
            # new audio lands, then reuse upsert_episode below -- the
            # episodes row itself is never dropped and re-inserted.
            # batch_reset_episodes_to_discovered nulls processed_file/
            # processed_at/original_duration/new_duration/ads_removed*/
            # error_message/ad_detection_status but NOT processed_version,
            # so the upsert below explicitly zeroes that too.
            storage.cleanup_episode_files(slug, episode_id)
            storage.remove_episode_artwork(slug, episode_id)
            db.clear_episode_details(slug, episode_id)
            db.clear_episode_ad_data(slug, episode_id)
            db.batch_reset_episodes_to_discovered(slug, [episode_id])
            _clear_queue_row(db, slug, episode_id)

    final_path = storage.get_original_path(slug, episode_id)
    shutil.move(str(audio_path), str(final_path))

    description = None
    description_path_str = entry.get('descriptionPath')
    if description_path_str:
        description_path = Path(description_path_str)
        if description_path.exists():
            try:
                description = description_path.read_text(
                    encoding='utf-8', errors='replace')
            except OSError:
                description = None

    # Insert/update the row before any chapters/artwork side effects --
    # save_chapters_json and save_episode_artwork's DB write both no-op
    # silently when the episode row is not there yet (Task 8 pattern).
    # processed_version=0 always: a no-op on a fresh INSERT (not in that
    # column list) and the one processing column
    # batch_reset_episodes_to_discovered above does NOT reset on an
    # overwrite.
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
        processed_version=0,
        p20_item_json=None,
        # The retained original IS the audio just moved into place above --
        # without this, hasOriginalAudio stays false and the /original.mp3
        # route 404s until a processing run happens to write this column.
        # Same relative-path form main_app/processing.py's
        # _persist_episode_state uses ('episodes/{id}-original.mp3'), for
        # anything that ever treats the column as a path rather than a
        # bare truthiness flag.
        original_file=f'episodes/{episode_id}-original.mp3',
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

    warnings: list[str] = []
    artwork_saved = False
    artwork_name = entry.get('artworkFile')
    artwork_path_str = entry.get('artworkPath')
    if artwork_path_str:
        artwork_path = Path(artwork_path_str)
        raw = None
        if artwork_path.exists():
            try:
                raw = artwork_path.read_bytes()
            except OSError:
                raw = None
        mime = _detect_image_mime(raw) if raw else None
        if raw and mime in ('image/jpeg', 'image/png'):
            storage.save_episode_artwork(slug, episode_id, raw, mime, evict=False)
            artwork_saved = True
        else:
            # Sidecar artwork present but unreadable/not a jpeg-or-png:
            # fall back to embedded extraction below instead of silently
            # ending up with no artwork at all, and say so.
            warnings.append(
                f"sidecar artwork {artwork_name!r} is invalid; "
                f"fell back to embedded artwork")

    if not artwork_saved:
        embedded = extract_embedded_artwork(str(final_path))
        if embedded:
            image_data, mime = embedded
            storage.save_episode_artwork(slug, episode_id, image_data, mime,
                                         evict=False)

    # Sidecar cleanup: the audio file is already gone (moved above). Any
    # OTHER file this entry references (sidecar json / description / cover)
    # is deleted too, whether it came from staging or the user-managed
    # import directory -- a successfully committed entry's sidecars are
    # consumed on commit regardless of source (operator ruling: only a
    # rejected/errored entry's sidecars are left for the operator to fix).
    # This only runs after a successful commit, so a rejected entry's files
    # are never touched here. A removal failure is logged and otherwise
    # non-fatal: the audio already moved, so the commit itself must not be
    # undone over a leftover sidecar file.
    for path_str in (description_path_str, artwork_path_str, entry.get('sidecarPath')):
        if not path_str:
            continue
        path = Path(path_str)
        if path.exists():
            try:
                path.unlink()
            except OSError as e:
                logger.warning(f"[{slug}:{episode_id}] could not remove sidecar {path}: {e}")

    return 'ok', {
        'episodeId': episode_id,
        'title': entry['title'],
        'publishedAt': entry['publishedAt'],
        'description': description,
        'warnings': warnings,
    }


def _commit_entries(slug: str, plan: dict, db, storage, had_episodes: bool,
                    report: dict) -> None:
    """Commit every entry in ``plan['entries']`` into ``report`` in place
    (idempotent -- a per-file failure is recorded and the batch continues),
    then queue newly imported episodes for auto-process when applicable,
    rebuild the served RSS once, and clean up an emptied staging dir.

    Mutating the caller-owned ``report`` dict (rather than building and
    returning a fresh one) is what lets ``_run_commit`` still expose every
    outcome recorded so far even if something below raises.
    """
    entries = plan.get('entries', [])
    overwrite = bool(plan.get('overwrite'))
    # 'directory'-sourced commit never scanned staging, so it must never
    # touch it either -- gates the sweep in the finally block below.
    source = plan.get('source', 'both')
    staging_dir = storage.import_staging_dir(slug)

    # Keeps title/publishedAt/description/warnings for the queueing step
    # below; the report's own 'committed' list only exposes episodeId/
    # audioFile/warnings (see the docstring on the outer report shape).
    committed_internal: list[dict] = []

    for entry in entries:
        episode_id = entry['episodeId']
        audio_file = entry.get('audioFile')

        # Any entry the planning half already flagged as non-committable
        # (duplicate id, existing-id collision without overwrite, invalid
        # sidecar, out-of-order publish dates) is skipped outright.
        if entry.get('errors'):
            report['skipped'].append({
                'episodeId': episode_id, 'audioFile': audio_file,
                'errors': entry['errors'],
            })
            _bump_processed(slug, storage)
            continue

        try:
            status, result = _commit_entry(slug, entry, db, storage, overwrite)
        except Exception as exc:
            # Per-file failure must never abort the batch -- an unexpected
            # exception (disk-full mid-move, a DB error) is caught here the
            # same as the ordinary error paths _commit_entry returns, so
            # every remaining entry still gets a chance to commit.
            logger.exception(f"[{slug}:{episode_id}] import commit entry crashed")
            status, result = 'error', str(exc)

        if status == 'ok':
            committed_internal.append(result)
            report['committed'].append({
                'episodeId': episode_id, 'audioFile': audio_file,
                'warnings': result.get('warnings', []),
            })
        else:
            report['failed'].append({
                'episodeId': episode_id, 'audioFile': audio_file, 'error': result,
            })
        _bump_processed(slug, storage)

    try:
        podcast = db.get_podcast_by_slug(slug)
        # Initial-import rule: an empty feed's very first batch never
        # auto-queues (there is nothing to compare "fresh" against and a
        # first-ever archive import is a bulk backfill, not new content), so
        # had_episodes -- captured before this batch's rows were inserted --
        # gates queueing regardless of what the feed looks like now.
        if (had_episodes and committed_internal and podcast is not None
                and db.is_auto_process_enabled_for_podcast(slug, podcast=podcast)):
            apply_fresh_boost = db.get_setting_bool('process_new_episodes_first', True)
            feed_priority = podcast.get('queue_priority')
            for item in committed_internal:
                priority = compute_queue_priority(
                    feed_priority, item['publishedAt'], manual=False,
                    apply_fresh_boost=apply_fresh_boost)
                queue_id = db.queue_episode_for_processing(
                    slug, item['episodeId'], f"local://{item['episodeId']}",
                    item['title'], item['publishedAt'], item['description'],
                    priority=priority)
                if queue_id is not None:
                    report['queued'].append(item['episodeId'])

        # Local import inside main_app; imported lazily like
        # api/local_episodes.py's _rebuild -- local_feed_builder pulls in
        # main_app at module level, which imports the api package (and this
        # module) before it finishes initializing, so a module-level import
        # here would be circular.
        from local_feed_builder import rebuild_local_feed
        rebuild_local_feed(slug)
    finally:
        # Staging is MinusPod-managed: once a commit finishes -- clean or
        # with per-entry failures, and even if the queueing/rebuild step
        # above raised -- sweep whatever is left (rejected files, skipped
        # entries' sidecars, anything staged but never scanned into this
        # plan) and remove the directory itself. Per-entry removal (not a
        # single rmtree of the whole dir) mirrors clear_import_staging: a
        # busy bind-mounted staging dir can refuse to remove ITSELF while
        # still allowing its contents to go one at a time.
        #
        # Scoped to source in ('staging', 'both'): a plan scanned/committed
        # with source='directory' never looked at staging at all, so it
        # must never sweep it either -- an operator mid-upload for a
        # SEPARATE directory-sourced commit must not have their in-progress
        # staging batch wiped out from under them.
        if source in ('staging', 'both') and staging_dir.exists():
            for child in staging_dir.iterdir():
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    logger.warning(f"[{slug}] could not remove leftover staged file {child}")
            try:
                staging_dir.rmdir()
            except OSError:
                pass
