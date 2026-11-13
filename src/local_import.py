"""Local (archive-import) feed import service.

Two halves. The top half is pure planning (no DB, storage or threads): parsing the ``sNNeNN`` naming scheme,
validating sidecar JSON, synthesizing missing publish dates and building a dry-run import plan. The bottom half is
the commit engine: it consumes a plan, moves files from staging or the user-managed import directory into
permanent episode storage, extracts embedded artwork, and runs the batch on a background thread tracked in a
module-level job registry. Planning and the registry use the stdlib only; the commit half calls DB/storage/audio
helpers through public interfaces (no direct SQL beyond one queue-row delete mirroring
``database.episodes.delete_episode_rows``).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import MIN_PRESERVED_CHAPTERS
from database.fingerprint_cleanup import delete_matches_for_episode
from database.queue import compute_queue_priority
from embedded_chapters import probe_chapters
from id3_chapters import ChapterTagError
import local_feed_builder
from storage import _detect_image_mime, _safe_join_under
from utils.atomic_json import write_json_atomic
from utils.audio import extract_embedded_artwork, get_audio_duration
from utils.episode_paths import peaks_file_path, remove_peaks_artifacts
from utils.http import redact_exc
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

# Common non-mp3 audio containers a user might drop into the import directory by mistake: rejected with a hint
# to convert, since an unmatched title/basename would otherwise look like a plain naming-scheme mismatch.
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
    """'S01E05 - The Beginning' -> ('s01e05', 1, 5, 'The Beginning'); None when the stem does not match.

    The id is canonicalized to minimal zero-padded width (``f's{season:02d}e{episode:02d}'``), so 's01e0001 - Title'
    and 's01e01 - Title' both mint 's01e01', matching build_import_plan's sidecar overrides so duplicate/collision
    detection (by id) sees them as one episode. Filenames on disk accept any width; only the minted id normalizes.
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
    """entries sorted ascending by (season, episode); each has 'published_at' (explicit ISO str or None). Fills gaps in place.

    Explicit dates must be strictly increasing, else return an error string naming both offending episode_ids and
    mark exactly those two entries (``entries[a]['_batch_error'] = True``) so a caller can attribute it to the pair.
    An unset final entry anchors at now_iso; a leading run steps back SYNTH_STEP per entry; between two anchors,
    space evenly. Returns None on success.
    """
    if not entries:
        return None

    last = len(entries) - 1
    explicit_idx = [i for i, e in enumerate(entries) if e.get('published_at')]
    parsed = {i: _parse_aware(entries[i]['published_at']) for i in explicit_idx}

    # The final entry is always an anchor: its own explicit date, or an implicit now_iso when unset. Compute it
    # (without writing back yet) so the strict-increase check below sees the full anchor chain, including a
    # preceding explicit date that collides with "now".
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
    """sha256 over sorted (name, size, mtime_ns) tuples plus the overwrite flag; commit refuses a stale hash (TOCTOU guard).

    A source that vanishes between listing and stat (e.g. a running commit's shutil.move racing a scan) is skipped, as
    in the plan-building loop; dropping it changes the hash, which is the staleness a caller must detect. overwrite is
    hashed because it changes which entries error vs. commit, so a mismatching commit must 409 as stale. It defaults to
    False only for callers that ignore it (tests); build_import_plan always passes it.
    """
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
    """Normalize an episode id to minimal zero-padded width (sNNeNN), the same formula parse_basename uses.

    A wide id like 's01e0006' (imported before id canonicalization) becomes 's01e06' so it equals a freshly-scanned
    candidate; otherwise collision detection never fires for it. A non-sNNeNN id passes through unchanged.
    """
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

    ``source`` ('staging'|'directory'|'both', as in the scan/commit API request) rides on the plan only for the commit
    engine: it gates whether ``_commit_entries`` may sweep staging, so a 'directory' plan never touches it. It plays no
    role in plan_hash or in resolving entry files (those come from each entry's resolved *Path key).

    batchErrors holds whole-batch errors (currently an out-of-order explicit-date pair, see synthesize_published_at).
    The two offending entries also get the message in their own ``errors``; every other entry stays clean even though
    the batch cannot commit, so commit must check batchErrors explicitly.

    replacesExisting is true whenever the entry's episodeId already exists in the feed, regardless of overwrite: a
    collision marker, not an outcome (an error when overwrite=False, a committable overwrite when True). To count what
    a commit will replace, use replacesExisting with empty errors.

    replacesExistingId is the actual existing row's episode_id when replacesExisting is true, else None. Normally equal
    to episodeId, but a pre-canonicalization row can keep its wide spelling (e.g. 's01e0006' for a candidate minting
    's01e06'); an overwrite must replace that row, not leave it behind beside a second under the canonical id.

    The *Path keys and mtimeNs are for the commit engine: it reads each file by its exact resolved path (not a bare
    filename re-resolved against staging/import directories) and re-stats bytes+mtimeNs as a TOCTOU guard.
    """
    # Canonicalize once up front so every membership test below compares like with like (see _canonical_episode_id),
    # keeping the mapping back to the original (possibly wide-spelled) id so an overwrite replaces that exact row.
    #
    # existing_ids is a set, so if both a wide-spelled row (s01e0006) and its canonical sibling (s01e06) exist after a
    # partial manual cleanup, a plain setdefault would pick one nondeterministically. Preferring the exact-spelled
    # match (canon == eid) makes the in-place reset path win over delete-and-reinsert, the safer outcome.
    # Canonicalize once up front so every membership test below compares like with like (see
    # _canonical_episode_id), keeping the mapping back to the original (possibly wide-spelled) id so an
    # overwrite replaces that exact row.
    #
    # existing_ids is a set (no iteration order), so if both a wide-spelled row (s01e0006) and its canonical
    # sibling (s01e06) exist after a partial manual cleanup, a plain setdefault would pick one
    # nondeterministically. Preferring the exact-spelled match (canon == eid) makes the in-place reset path
    # win deterministically over delete-and-reinsert, the safer outcome to pick blind.
    existing_by_canonical: dict[str, str] = {}
    for eid in existing_ids:
        canon = _canonical_episode_id(eid)
        if canon == eid or canon not in existing_by_canonical:
            existing_by_canonical[canon] = eid
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

    # Collisions against already-imported episodes. Recorded on every colliding candidate (replaces_existing),
    # overwrite or not, so the client can count how many entries an overwrite=True commit will replace; errors
    # gates whether it does (a collision only becomes an error when overwrite is off).
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

    # Entries that already carry an error (duplicate id, collision, invalid sidecar) are never committed, so they must
    # not anchor date synthesis (a bogus sidecar date would backdate clean siblings) nor receive a synthesized value.
    # Synthesis runs over the clean subset; an errored entry keeps its explicit date (or None), which is ignored.
    clean = [c for c in candidates if not c['errors']]
    synth_err = synthesize_published_at(clean, now_iso)
    batch_errors: list[str] = []
    if synth_err:
        batch_errors.append(synth_err)
        for c in clean:
            # Only the pair synthesize_published_at flagged gets the error on its own entry; other clean candidates are
            # unrelated. The batch still cannot commit (see batchErrors above); that gate is enforced separately.
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
            # Resolved paths, not just basenames: the commit engine reads these directly, so a same-named file
            # in more than one scanned directory (staging vs. the user-managed import dir) cannot cause the
            # wrong one to be committed.
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

# Cross-worker job registry: gunicorn runs multiple worker processes, each with its own heap, so an in-memory dict
# would be visible only to the worker that ran the commit (a status poll elsewhere sees no job, and the "one import per
# feed" guard would let two workers race on the same staging files). State is per-feed on disk under
# ``<data_dir>/.import-jobs/<slug>.json`` plus a per-feed flock lockfile (``<slug>.lock``) held for the commit thread's
# duration as the running-guard (same fcntl.flock idiom as processing_queue.py's ProcessingQueue, one lock per slug).

def _jobs_dir_path(storage) -> Path:
    """The per-feed import-job-state directory, without creating it.

    Read paths (status polls, lock probes) must not conjure a directory nothing has written to; only write
    paths create it, via ``_jobs_dir``.
    """
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


def _staging_generation_path(storage, slug: str, *, create: bool = False) -> Path:
    base = _jobs_dir(storage) if create else _jobs_dir_path(storage)
    return _safe_join_under(base, f'{slug}.generation.json')


def read_staging_generation(storage, slug: str) -> int:
    try:
        raw = json.loads(_staging_generation_path(storage, slug).read_text())
        return max(0, int(raw.get('generation', 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def bump_staging_generation(storage, slug: str) -> int:
    generation = read_staging_generation(storage, slug) + 1
    if not write_json_atomic(
            _staging_generation_path(storage, slug, create=True),
            {'generation': generation}):
        raise OSError('could not persist staging generation')
    return generation


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

    Returns the open file object (lock held; the caller must pass it to ``_release_import_lock``) on success,
    or ``None`` when another holder (any process, including this one via a different open file description)
    has it.
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
    """Non-blocking probe: is any process currently holding this feed's import lock?

    Mirrors ProcessingQueue._pid_alive. Try-acquiring on a separate open file description does not disturb a real
    holder (flock locks are scoped to the open file description), so this is safe mid-commit. Opened 'a', not 'w':
    'w' truncates on open before flock is attempted.
    """
    lock_path = _job_lock_path(storage, slug)
    if not lock_path.exists():
        return False
    try:
        with open(lock_path, 'a') as probe_fd:
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
    """Start committing ``plan`` (built by ``build_import_plan``) for ``slug`` on a background daemon thread.

    Returns ``(False, reason)`` without registering anything when the plan was built for a different slug (stale plan
    against the wrong feed), another import is running for this feed (per-feed flock held by any worker), or the
    destination volume lacks ``_FREE_SPACE_MARGIN`` times the plan's importable bytes. Otherwise writes the job state
    as running, captures whether the feed already had episodes (for the initial-import auto-process rule, see
    ``_commit_entries``) before spawning the thread so its own inserts cannot flip it, and returns ``(True, 'started')``.
    The flock is acquired here and held by the thread for the commit; ``_run_commit``'s ``finally`` releases it.
    """
    if plan.get('slug') != slug:
        return False, 'plan slug mismatch'
    if is_dangerous_slug(slug):
        return False, 'invalid slug'

    lock_fh = _try_acquire_import_lock(storage, slug)
    if lock_fh is None:
        return False, 'import already running'

    try:
        expected_generation = plan.get('stagingGeneration')
        if (expected_generation is not None
                and read_staging_generation(storage, slug) != expected_generation):
            _release_import_lock(lock_fh)
            return False, 'staged files changed; re-run scan'
        total_bytes = plan.get('totals', {}).get('bytes', 0)
        free_bytes = shutil.disk_usage(storage.data_dir).free
        if free_bytes <= total_bytes * _FREE_SPACE_MARGIN:
            _release_import_lock(lock_fh)
            return False, 'insufficient free disk space'

        _, existing_count = db.get_episodes(slug, status='all', limit=1)
        had_episodes = existing_count > 0

        importable_ids = [
            entry['episodeId'] for entry in plan.get('entries', [])
            if not entry.get('errors')
        ]
        reservation_result = db.reserve_episode_uploads(
            slug, importable_ids, operation='import',
            overwrite=bool(plan.get('overwrite')),
        )
        if reservation_result['conflicts']:
            _release_import_lock(lock_fh)
            return False, 'episode changed or is already uploading; re-run scan'
        reservations = reservation_result['reserved']

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
        args=(slug, plan, db, storage, had_episodes, lock_fh, reservations),
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        # thread.start() failing (e.g. the OS refuses a new thread) must not
        # leave a phantom 'running' job that can never finish and blocks
        # every future start_commit for this feed.
        _clear_job_state(storage, slug)
        for reservation_id in reservations.values():
            db.fail_upload_reservation(reservation_id)
        _release_import_lock(lock_fh)
        raise
    return True, 'started'


def get_import_status(slug: str, storage=None) -> dict:
    """{'state': 'idle'|'running'|'done'|'error', 'processed': n, 'total': n, 'startedAt': iso|None, 'report': {...} when done/error}.

    Reads straight off the per-feed state file (no in-process registry), so any worker sees the same state. A slug with
    no state file reads as idle. Self-heals a state file stuck on 'running' whose lock nobody holds (a worker crashed
    mid-commit, e.g. OOM-killed): reports 'error' with an 'import interrupted' report and persists that correction.
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


def _run_commit(slug: str, plan: dict, db, storage, had_episodes: bool, lock_fh,
                reservations: dict[str, str]) -> None:
    """Background-thread entry point: run the batch, then flip the job to 'done' (with its report) or, only on an exception
    escaping the whole batch (not a per-file failure), 'error'. Always releases the lock handed over by ``start_commit``.

    ``report`` is built here and mutated in place by ``_commit_entries``, so even if something outside the per-entry loop
    raises (or the call is interrupted: caught as ``BaseException`` so the ``finally`` always leaves a terminal state),
    every outcome recorded so far is visible in the stored report alongside the 'error' key.
    """
    report: dict = {'committed': [], 'skipped': [], 'failed': [], 'queued': []}
    error: BaseException | None = None
    try:
        _commit_entries(slug, plan, db, storage, had_episodes, report,
                        reservations=reservations)
    except BaseException as exc:
        logger.exception(f"[{slug}] import commit crashed")
        error = exc
    finally:
        try:
            job = _read_job_state(storage, slug) or {}
            if error is not None:
                job['state'] = 'error'
                job['report'] = {**report, 'error': redact_exc(error)}
            else:
                job['state'] = 'done'
                job['report'] = report
            _write_job_state(storage, slug, job)
        finally:
            for reservation_id in reservations.values():
                db.fail_upload_reservation(reservation_id)
            _release_import_lock(lock_fh)


def _clear_queue_row(db, slug: str, episode_id: str,
                     *, commit: bool = True) -> None:
    """Drop any auto_process_queue row for this episode before an overwrite reset (same SQL pattern as
    database.episodes.delete_episode_rows), so a stale queued/processing row cannot resurrect against the
    re-imported file.
    """
    podcast = db.get_podcast_row(slug)
    if not podcast:
        return
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?",
        (podcast['id'], episode_id),
    )
    if commit:
        conn.commit()


def probe_and_save_chapters(storage, slug: str, episode_id: str, final_path) -> None:
    """Read embedded chapters from the published file and save them if enough survive."""
    try:
        chapters = probe_chapters(str(final_path))
    except ChapterTagError as error:
        logger.warning('Publisher chapter read failed: %s', error)
        chapters = None
    if chapters and len(chapters) >= MIN_PRESERVED_CHAPTERS:
        storage.save_chapters_json(slug, episode_id, {
            'version': '1.2.0',
            'chapters': [
                {'startTime': int(ch['start']), 'title': ch.get('title') or ''}
                for ch in chapters
            ],
        })


def _commit_entry(slug: str, entry: dict, db, storage,
                  overwrite: bool, upserted: list,
                  reservation_id: str | None = None) -> tuple[str, object]:
    """Commit one plan entry. Returns ('ok', result_dict) or ('error', message); never raises for an ordinary per-file problem.

    Appends the episode id to ``upserted`` as soon as the row exists, so the caller's batched indexing still covers it
    if a later step raises. Uses the plan's resolved paths rather than re-resolving by basename: a same-named file in
    the other scanned directory must never cause the wrong file to be committed.
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

    # The plan's matched-collision id from scan time (see build_import_plan's replacesExistingId): normally episode_id,
    # but a pre-canonicalization row can keep its wide spelling (e.g. 's01e0006' for a candidate minting 's01e06').
    # Falls back to episode_id so a row created under the canonical id after the scan is still caught below.
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

        if existing_id != episode_id and reservation_id is None:
            db.delete_episode_rows(slug, [existing_id], storage)
    final_path = storage.get_original_path(slug, episode_id)
    replaced_path = (storage.get_original_path(slug, existing_id)
                     if existing is not None else final_path)
    backup_path = (final_path.parent / f'.upload-{reservation_id}.backup'
                   if reservation_id is not None else None)
    if reservation_id is not None:
        try:
            source_relative = str(audio_path.resolve().relative_to(
                storage.data_dir.resolve()))
            backup_relative = str(backup_path.resolve().relative_to(
                storage.data_dir.resolve()))
            backup_target_relative = str(replaced_path.resolve().relative_to(
                storage.data_dir.resolve()))
        except ValueError:
            return 'error', 'source audio path is outside the data directory'
        if not db.prepare_upload_reservation(reservation_id, source_relative):
            return 'error', 'upload reservation was lost'
        if not db.begin_upload_publication(
                reservation_id, source_relative, backup_relative,
                backup_target_relative):
            return 'error', 'upload reservation was lost'
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

    # Insert/update the row before any chapters/artwork side effects: save_chapters_json and save_episode_artwork's DB
    # write no-op silently when the row is missing. processed_version=0 always: a no-op on a fresh INSERT and the one
    # processing column batch_reset_episodes_to_discovered above does not reset on an overwrite.
    def write_episode(*, commit: bool) -> None:
        if existing is not None and existing_id == episode_id:
            storage.delete_processed_file(slug, episode_id, keep_original=True)
            storage.remove_episode_artwork(slug, episode_id)
            db.clear_episode_details(slug, episode_id, commit=commit)
            db.batch_reset_episodes_to_discovered(
                slug, [episode_id], commit=commit)
            _clear_queue_row(db, slug, episode_id, commit=commit)
        elif existing is not None:
            podcast = db.get_podcast_row(slug)
            conn = db.get_connection()
            conn.execute(
                "DELETE FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?",
                (podcast['id'], existing_id),
            )
            delete_matches_for_episode(conn, slug, existing_id)
            conn.execute("DELETE FROM episodes WHERE id = ?", (existing['id'],))
        db.upsert_episode(
            slug, episode_id,
            defer_index=True,
            commit=commit,
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
            original_file=f'episodes/{episode_id}-original.mp3',
        )

    if reservation_id is None:
        shutil.move(str(audio_path), str(final_path))
        write_episode(commit=True)
    else:
        moved = False
        try:
            with db.transaction(immediate=True):
                if not db.owns_upload_reservation(reservation_id, 'publishing'):
                    raise RuntimeError('upload reservation was lost during publication')
                if replaced_path.exists():
                    os.replace(replaced_path, backup_path)
                os.replace(audio_path, final_path)
                moved = True
                write_episode(commit=False)
                if not db.finish_upload_reservation(
                        reservation_id, 'published', commit=False):
                    raise RuntimeError('upload reservation was lost during publication')
        except BaseException:
            if moved and final_path.exists():
                os.replace(final_path, audio_path)
                remove_peaks_artifacts(peaks_file_path(final_path))
            if backup_path.exists():
                os.replace(backup_path, replaced_path)
            raise
    if backup_path is not None and backup_path.exists():
        try:
            backup_path.unlink()
        except OSError:
            logger.warning("Could not remove import backup %s", backup_path)
    if existing is not None and existing_id != episode_id:
        storage.cleanup_episode_files(slug, existing_id)
        storage.remove_episode_artwork(slug, existing_id)
    # Recorded before the chapter/artwork steps below, any of which can raise and
    # leave a committed row the batched index pass would otherwise skip.
    upserted.append(episode_id)

    probe_and_save_chapters(storage, slug, episode_id, final_path)

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

    # Sidecar cleanup: the audio is already moved. Any other file this entry references (sidecar json / description /
    # cover) is deleted too, from staging or the import directory: a committed entry's sidecars are consumed
    # regardless of source (a rejected/errored entry never reaches here). A removal failure is logged and non-fatal;
    # the commit must not be undone over a leftover sidecar.
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


def _staging_leftover_name(path_str: str | None, staging_dir: Path) -> str | None:
    """The basename of ``path_str`` when it names a file directly inside ``staging_dir``, else None.

    Builds the sweep's preserve-list from a skipped/errored entry's ``*Path`` fields, which can point into staging or
    the import directory (source='both'). The sweep only touches staging, so a directory-sourced path must not enter
    the preserve set (basename-only matching could misfire on a same-named staging file).
    """
    if not path_str:
        return None
    path = Path(path_str)
    return path.name if path.parent == staging_dir else None


def _preserve_entry_files(entry: dict, staging_dir: Path, preserved: set[str]) -> None:
    """Add a skipped/errored entry's audio + sidecar staging filenames to
    ``preserved`` so the post-commit sweep leaves them for the operator to
    fix and rescan, rather than deleting them alongside true rejects."""
    for key in ('audioPath', 'descriptionPath', 'artworkPath', 'sidecarPath'):
        name = _staging_leftover_name(entry.get(key), staging_dir)
        if name:
            preserved.add(name)


def _commit_entries(slug: str, plan: dict, db, storage, had_episodes: bool,
                    report: dict,
                    reservations: dict[str, str] | None = None) -> None:
    """Commit every entry in ``plan['entries']`` into ``report`` in place (a per-file failure is recorded and the batch
    continues), then queue newly imported episodes for auto-process when applicable, rebuild the served RSS once, and
    clean up an emptied staging dir.

    Mutating the caller-owned ``report`` lets ``_run_commit`` expose every outcome recorded so far even if something raises.
    """
    entries = plan.get('entries', [])
    overwrite = bool(plan.get('overwrite'))
    reservations = reservations or {}
    # 'directory'-sourced commit never scanned staging, so it must never
    # touch it either -- gates the sweep in the finally block below.
    source = plan.get('source', 'both')
    staging_dir = storage.import_staging_dir(slug)

    # Keeps title/publishedAt/description/warnings for the queueing step
    # below; the report's own 'committed' list only exposes episodeId/
    # audioFile/warnings (see the docstring on the outer report shape).
    committed_internal: list[dict] = []
    # Every episode id whose row reached the database, committed or not: what the one
    # batched index pass after the loop covers.
    upserted_ids: list[str] = []
    # Staging filenames of an entry that ended in skip or error: a fixable problem (bad sidecar JSON, stale
    # collision), not junk. The sweep in the finally block leaves them so fixing the sidecar and rescanning
    # doesn't require re-uploading the audio.
    preserved_leftovers: set[str] = set()

    try:
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
                _preserve_entry_files(entry, staging_dir, preserved_leftovers)
                _bump_processed(slug, storage)
                continue

            try:
                status, result = _commit_entry(slug, entry, db, storage, overwrite,
                                               upserted_ids,
                                               reservations.get(episode_id))
            except Exception as exc:
                # A per-file failure must never abort the batch: an unexpected exception (disk-full mid-move,
                # a DB error) is caught here like the ordinary error paths _commit_entry returns, so every
                # remaining entry still gets a chance to commit.
                logger.exception(f"[{slug}:{episode_id}] import commit entry crashed")
                status, result = 'error', redact_exc(exc)

            if status == 'ok':
                committed_internal.append(result)
                report['committed'].append({
                    'episodeId': episode_id, 'audioFile': audio_file,
                    'warnings': result.get('warnings', []),
                })
            else:
                reservation_id = reservations.get(episode_id)
                if reservation_id is not None:
                    db.fail_upload_reservation(reservation_id)
                report['failed'].append({
                    'episodeId': episode_id, 'audioFile': audio_file, 'error': result,
                })
                # _commit_entry never returns 'error' after moving the audio
                # (every error return in it happens before shutil.move), so the
                # audio is still sitting at its original staging path here.
                _preserve_entry_files(entry, staging_dir, preserved_leftovers)
            _bump_processed(slug, storage)
    finally:
        # One index pass for the batch on every way out of the loop: the rows are
        # committed by now, and _commit_entry deferred each one's own index write.
        if upserted_ids:
            try:
                db.index_episodes([(ep_id, slug) for ep_id in upserted_ids])
            except Exception:
                # Logged, never raised: this runs in a finally, and a failed index pass
                # must not replace whatever exception is already on its way out.
                logger.exception(f"[{slug}] batched search index pass failed")

    try:
        podcast = db.get_podcast_by_slug(slug)
        # Initial-import rule: an empty feed's first batch never auto-queues (nothing to compare "fresh"
        # against; a first-ever archive import is a bulk backfill), so had_episodes, captured before this
        # batch's rows were inserted, gates queueing regardless of the feed's current state.
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

        local_feed_builder.rebuild_local_feed(slug)
    finally:
        # Staging is MinusPod-managed: once a commit finishes (even if queueing/rebuild above raised), sweep what is left.
        # Committed entries' leftovers are already gone (_commit_entry moves the audio and deletes sidecars), so what
        # remains is (1) files the plan rejected outright (a name that never matched sNNeNN, an empty file, a stray
        # .part), which this sweep removes, and (2) a skipped or errored entry's audio + sidecars, which it keeps
        # (preserved_leftovers) so an operator can fix the problem and rescan without re-uploading. The directory is
        # removed only once every non-preserved file is gone. Per-entry removal (not one rmtree) mirrors
        # clear_import_staging: a busy bind-mounted staging dir can refuse to remove itself but let contents go.
        # Scoped to source in ('staging', 'both'): a 'directory' plan never looked at staging, so it must not sweep it
        # (an operator mid-upload for a separate directory-sourced commit would lose their staging batch).
        if source in ('staging', 'both') and staging_dir.exists():
            all_removed = True
            for child in staging_dir.iterdir():
                if child.name in preserved_leftovers:
                    all_removed = False
                    continue
                try:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                except OSError:
                    logger.warning(f"[{slug}] could not remove leftover staged file {child}")
                    all_removed = False
            if all_removed:
                try:
                    staging_dir.rmdir()
                except OSError:
                    pass
            bump_staging_generation(storage, slug)
