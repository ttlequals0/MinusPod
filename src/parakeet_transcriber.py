"""In-process NVIDIA Parakeet TDT transcription through onnx-asr (#834)."""
import contextlib
import errno
import fcntl
import fnmatch
import glob
import importlib.util
import json
import logging
import os
import re
import shutil
import string
import threading
import time
import unicodedata
import wave

import numpy as np

from config import (
    PARAKEET_MODEL_DISK_MARGIN_BYTES, PARAKEET_MODEL_DOWNLOAD_WARN_SECONDS, PARAKEET_MODEL_NAME, PARAKEET_MODEL_PROBE_TIMEOUT_SECONDS,
    PARAKEET_MODEL_RECHECK_SECONDS, PARAKEET_MODEL_REPO, PARAKEET_MODEL_RETRY_SECONDS,
    PARAKEET_OVERLAP_S, PARAKEET_SEGMENT_MAX_S, PARAKEET_SEGMENT_PAUSE_S, PARAKEET_TOKEN_STEP_S,
    PARAKEET_WINDOW_S, PARAKEET_WORD_MAX_S, resolve_whisper_device,
)
from utils.errors import ModelLoadError

logger = logging.getLogger('podcast.parakeet')

_clock = time.time
_last_failed_check = {}
_rechecks_running = set()
_updates_off = set()
_downloads_started = {}
_recheck_guard = threading.Lock()
os.register_at_fork(after_in_child=_rechecks_running.clear)


def _free_bytes(path):
    return shutil.disk_usage(path).free


def _start_thread(work):
    thread = threading.Thread(target=work, name='parakeet-model-recheck', daemon=True)
    thread.start()
    return thread


_recheck_runner = _start_thread

WORD_MARKER = '\u2581'
_SPECIAL_TOKEN = re.compile(r'^(<\|.*\|>|<unk>|<pad>|<blk>)$')
_SENTENCE_END = ('.', '?', '!')
_ENGLISH = ('en', 'english', '', None)


def parakeet_available() -> bool:
    """True when the onnx_asr package is importable."""
    return importlib.util.find_spec('onnx_asr') is not None


def parakeet_unavailable_message() -> str:
    return ("Parakeet transcription needs the onnx-asr package, which is not installed; "
            "install it or set WHISPER_BACKEND=local or openai-api")


def _model_patterns(quantization):
    """(required, optional) file globs the loader reads; '?' also matches the '.' before a suffix."""
    suffix = f'?{quantization}' if quantization else ''
    required = ['vocab.txt']
    optional = ['config.json', 'config.yaml']
    for name in ('encoder-model', 'decoder_joint-model'):
        required.append(f'{name}{suffix}.onnx')
        optional.append(f'{name}{suffix}.onnx?data')
    return required, optional


def _record_path(model_dir, quantization):
    return os.path.join(model_dir, f'.minuspod-complete-{quantization or "fp32"}.json')


def _matches(model_dir, pattern):
    return [p for p in glob.glob(os.path.join(glob.escape(model_dir), pattern)) if os.path.isfile(p)]


def _read_record(model_dir, quantization):
    """The parsed completion record, or None when it is missing or unreadable."""
    try:
        with open(_record_path(model_dir, quantization), encoding='utf-8') as f:
            record = json.load(f)
        return record if isinstance(record, dict) else None
    except (OSError, ValueError):
        return None


def _model_files_complete(model_dir, quantization, record):
    """True only if a finished fetch recorded these files and each is still present at its recorded size."""
    try:
        if record is None or record['repo'] != PARAKEET_MODEL_REPO:
            return False
        required, _ = _model_patterns(quantization)
        recorded = {os.path.join(model_dir, name): size for name, size in record['files'].items()}
        return (all(any(p in recorded for p in _matches(model_dir, pat)) for pat in required)
                and all(os.path.isfile(p) and os.path.getsize(p) == size for p, size in recorded.items()))
    except (OSError, AttributeError, TypeError, KeyError):
        return False


def _checked_at(record):
    """Epoch seconds of the last successful online check, or None when unrecorded or unusable."""
    value = (record or {}).get('checked_at')
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _recheck_due(record, now):
    checked = _checked_at(record)
    return checked is None or checked > now or now - checked >= PARAKEET_MODEL_RECHECK_SECONDS


def _throttled(key, now):
    """True within the retry interval after this process's last failed check."""
    failed_at = _last_failed_check.get(key)
    return failed_at is not None and 0 <= now - failed_at < PARAKEET_MODEL_RETRY_SECONDS


def _required_files_present(model_dir, quantization):
    required, _ = _model_patterns(quantization)
    return all(_matches(model_dir, pat) for pat in required)


def _expected_files_present(model_dir, quantization, info):
    """Required files plus every repo file the patterns select, per the probe's file list."""
    required, optional = _model_patterns(quantization)
    names = [s.rfilename for s in (getattr(info, 'siblings', None) or [])]
    expected = [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in required + optional)]
    return (_required_files_present(model_dir, quantization)
            and all(os.path.isfile(os.path.join(model_dir, n)) for n in expected))


def _write_record(model_dir, quantization, content):
    """Atomically replace the completion record; False when it could not be written."""
    record = _record_path(model_dir, quantization)
    tmp = f'{record}.{os.getpid()}.{threading.get_ident()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(content, f)
        os.replace(tmp, record)
        return True
    except OSError as e:
        logger.debug("Could not record Parakeet model files: %s", e)
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return False


def _record_model_files(model_dir, quantization, sha, checked_at):
    """Record the repo, commit, file sizes and last online check so a later load can skip the network."""
    required, optional = _model_patterns(quantization)
    if not _required_files_present(model_dir, quantization):
        return False
    try:
        sizes = {os.path.relpath(p, model_dir): os.path.getsize(p)
                 for pat in required + optional for p in _matches(model_dir, pat)}
    except OSError as e:
        logger.debug("Could not record Parakeet model files: %s", e)
        return False
    content = {'repo': PARAKEET_MODEL_REPO, 'files': sizes}
    if sha:
        content['sha'] = sha
    if checked_at is not None:
        content['checked_at'] = checked_at
    return _write_record(model_dir, quantization, content)


def _locks_unsupported(model_dir, reason, error):
    """Turn updates off for this directory, once per process, when it cannot hold a file lock."""
    logger.debug("Parakeet lock unavailable: %s", error)
    if model_dir not in _updates_off:
        _updates_off.add(model_dir)
        logger.info("Parakeet model update checks are off until the process restarts: %s", reason)


@contextlib.contextmanager
def _flock(model_dir, suffix, mode):
    """Hold an fcntl lock on model_dir + suffix; yields False when a non-blocking request is refused."""
    path = model_dir + suffix
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        _locks_unsupported(model_dir, 'the lock file cannot be created next to the model directory', e)
        # Without a lock a blocking (load) caller proceeds unprotected and a re-check is refused.
        yield not mode & fcntl.LOCK_NB
        return
    try:
        try:
            fcntl.flock(fd, mode)
        except BlockingIOError:
            yield False
            return
        except OSError as e:
            _locks_unsupported(model_dir, 'the model volume does not support file locks', e)
            yield not mode & fcntl.LOCK_NB
            return
        yield True
    finally:
        os.close(fd)


def _restore_backup(model_dir):
    """Put the previous copy back when an update stopped between its two renames."""
    backup = model_dir + '.old'
    if os.path.isdir(model_dir) or not os.path.isdir(backup):
        return
    with _flock(model_dir, '.lock', fcntl.LOCK_EX):
        if not os.path.isdir(model_dir) and os.path.isdir(backup):
            os.rename(backup, model_dir)
            logger.warning("Restored the Parakeet model directory after an interrupted update")


def _offline_mode():
    return os.environ.get('HF_HUB_OFFLINE', '').strip().lower() in ('1', 'on', 'yes', 'true')


def _update_blocker(model_dir):
    """Why the live directory cannot be swapped by rename, or None."""
    if _offline_mode():
        return 'HF_HUB_OFFLINE is set (offline mode)'
    if os.path.islink(model_dir):
        return 'the model directory is a symlink'
    if os.path.ismount(model_dir):
        return 'the model directory is a mount point'
    try:
        if os.stat(model_dir).st_dev != os.stat(os.path.dirname(model_dir)).st_dev:
            return 'the model directory is on another filesystem than its parent'
    except OSError:
        return None
    return None


def _updates_blocked(model_dir):
    """True when updates cannot run for this directory; the reason is logged once per process."""
    if model_dir in _updates_off:
        return True
    reason = _update_blocker(model_dir)
    if reason:
        _updates_off.add(model_dir)
        logger.info("Parakeet model update checks are off: %s", reason)
    return reason is not None


def _swap_in(model_dir, staging):
    """Replace the live directory with staging under the exclusive lock; False when it cannot be renamed."""
    backup = model_dir + '.old'
    with _flock(model_dir, '.lock', fcntl.LOCK_EX):
        try:
            os.rename(model_dir, backup)
        except OSError as e:
            if e.errno not in (errno.EBUSY, errno.EXDEV, errno.ENOTDIR, errno.EPERM):
                raise
            _updates_off.add(model_dir)
            logger.warning("Parakeet model updates are off: the model directory cannot be renamed (%s)",
                           errno.errorcode[e.errno])
            return False
        try:
            os.rename(staging, model_dir)
        except OSError:
            os.rename(backup, model_dir)
            raise
    shutil.rmtree(backup, ignore_errors=True)
    return True


def _probe(hf_api):
    return hf_api().model_info(PARAKEET_MODEL_REPO, timeout=PARAKEET_MODEL_PROBE_TIMEOUT_SECONDS)


class IncompleteModelDownload(Exception):
    """A download returned without every model file the repo lists."""


def _warn_on_stalled_download(key, now):
    """One warning per download that has run past the warn limit; the library sets no overall timeout."""
    started = _downloads_started.get(key)
    if started is not None and now - started >= PARAKEET_MODEL_DOWNLOAD_WARN_SECONDS:
        _downloads_started[key] = None
        logger.warning("Parakeet model update download still running after %d minutes; the local copy stays in use",
                       (now - started) // 60)


_MODEL_FILE_ERRORS = ('NoSuchFile', 'InvalidProtobuf', 'InvalidGraph', 'ModelLoadingError')
_MODEL_FILE_MARKERS = ('External initializer', 'cannot get file size')


def _model_file_error(e):
    """True when a load failure points at missing or unreadable model files, not a transient runtime error."""
    if isinstance(e, (FileNotFoundError, NotADirectoryError)):
        return True
    names = {c.__name__ for c in type(e).__mro__}
    if names & set(_MODEL_FILE_ERRORS):
        return True
    return bool(names & {'Fail', 'RuntimeException'}) and any(m in str(e) for m in _MODEL_FILE_MARKERS)


def _discard_record(model_dir, quantization):
    with contextlib.suppress(OSError):
        os.unlink(_record_path(model_dir, quantization))
    logger.warning("Parakeet model did not load from the recorded copy; it is fetched again after the retry interval")


def _fetch_model_files(model_dir, quantization):
    """Make sure model files exist; returns (recorded complete, start the background re-check)."""
    now = _clock()
    key = (model_dir, quantization)
    _warn_on_stalled_download(key, now)
    record = _read_record(model_dir, quantization)
    if _model_files_complete(model_dir, quantization, record):
        if _throttled(key, now) or _updates_blocked(model_dir):
            return True, False
        leftovers = any(os.path.lexists(model_dir + s) for s in ('.staging', '.old'))
        return True, leftovers or _recheck_due(record, now)
    if _throttled(key, now) and _required_files_present(model_dir, quantization):
        return False, False
    required, optional = _model_patterns(quantization)
    try:
        # Imported here so a load from a complete local copy never needs the hub package.
        from huggingface_hub import HfApi, snapshot_download
        try:
            info = _probe(HfApi)
        except Exception as e:
            if not _required_files_present(model_dir, quantization):
                info = None
            else:
                # Unverified but whole: record without sha so the background check verifies it by a staged download.
                _last_failed_check[key] = now
                logger.warning("Parakeet model check failed (%s), using the local files", type(e).__name__)
                return _record_model_files(model_dir, quantization, None, None), False
        sha = getattr(info, 'sha', None)
        snapshot_download(PARAKEET_MODEL_REPO, revision=sha, local_dir=model_dir, allow_patterns=required + optional)
    except Exception as e:
        if not _required_files_present(model_dir, quantization):
            raise
        _last_failed_check[key] = now
        logger.warning("Parakeet model download failed (%s), using the local files", type(e).__name__)
        return False, False
    if not sha or not _expected_files_present(model_dir, quantization, info):
        _last_failed_check[key] = now
        logger.warning("Parakeet model download could not be verified complete; the files are not recorded")
        return False, False
    _last_failed_check.pop(key, None)
    return _record_model_files(model_dir, quantization, sha, now), False


def _recheck(model_dir, quantization):
    """Clean leftovers, then probe and swap in a new commit when due; True on success, False when skipped."""
    now = _clock()
    key = (model_dir, quantization)
    staging = model_dir + '.staging'
    _restore_backup(model_dir)
    for suffix, role in (('.staging', 'staging'), ('.old', 'backup')):
        shutil.rmtree(model_dir + suffix, ignore_errors=True)
        if os.path.lexists(model_dir + suffix):
            _last_failed_check[key] = now
            logger.warning("Could not remove the leftover Parakeet %s directory %s; retrying in an hour",
                           role, os.path.basename(model_dir + suffix))
            return False
    # Re-read under the re-check lock: another worker may have just finished.
    record = _read_record(model_dir, quantization)
    if (not _model_files_complete(model_dir, quantization, record) or not _recheck_due(record, now)
            or _updates_blocked(model_dir)):
        return False
    # Imported here so a broken hub install cannot fail a load from a complete local copy.
    from huggingface_hub import HfApi, snapshot_download
    info = _probe(HfApi)
    sha = info.sha
    if not sha:
        raise ValueError('model info carried no commit sha')
    if record.get('sha') == sha:
        if not _write_record(model_dir, quantization, {**record, 'checked_at': now}):
            raise OSError('could not write the model record')
        return True
    needed = sum(record['files'].values()) + PARAKEET_MODEL_DISK_MARGIN_BYTES
    free = _free_bytes(os.path.dirname(model_dir))
    if free < needed:
        _last_failed_check[key] = now
        logger.warning("Parakeet model update skipped: %d MiB free, %d MiB needed; using the local copy",
                       free // 2**20, needed // 2**20)
        return False
    required, optional = _model_patterns(quantization)
    _downloads_started[key] = now
    try:
        snapshot_download(PARAKEET_MODEL_REPO, revision=sha, local_dir=staging, allow_patterns=required + optional)
        if not _expected_files_present(staging, quantization, info):
            raise IncompleteModelDownload()
        if not _record_model_files(staging, quantization, sha, now):
            raise OSError('could not write the model record')
        if not _swap_in(model_dir, staging):
            return False
    finally:
        _downloads_started.pop(key, None)
        shutil.rmtree(staging, ignore_errors=True)
    logger.info("Parakeet model updated to revision %s; the next load uses it", sha[:12])
    return True


def _run_recheck(model_dir, quantization):
    """Re-check body; never raises, and any failure leaves the local copy in use."""
    key = (model_dir, quantization)
    try:
        with _flock(model_dir, '.recheck.lock', fcntl.LOCK_EX | fcntl.LOCK_NB) as held:
            if held and _recheck(model_dir, quantization):
                _last_failed_check.pop(key, None)
    except Exception as e:
        _last_failed_check[key] = _clock()
        logger.warning("Parakeet model check failed (%s), using the local copy", type(e).__name__)
    finally:
        with _recheck_guard:
            _rechecks_running.discard(key)


def _start_recheck(model_dir, quantization):
    """Run at most one re-check per model in this process, off the load path."""
    key = (model_dir, quantization)
    with _recheck_guard:
        if key in _rechecks_running:
            return
        _rechecks_running.add(key)
    try:
        _recheck_runner(lambda: _run_recheck(model_dir, quantization))
    except Exception as e:
        with _recheck_guard:
            _rechecks_running.discard(key)
        logger.warning("Could not start the Parakeet model check (%s)", type(e).__name__)


def _warn_on_cpu_fallback(model, device):
    """Warn when a cuda load runs on the CPU provider; severity 3 hides ORT's own fallback warning."""
    if device != 'cuda':
        return
    try:
        providers = model.asr._encoder.get_providers()
        if 'CUDAExecutionProvider' not in providers:
            logger.warning("Parakeet is running on %s, not CUDAExecutionProvider; transcription will be slow",
                           ", ".join(providers))
    except Exception as e:
        logger.debug("Could not read the Parakeet execution providers: %s", e)


class ParakeetModelSingleton:
    """Process-wide onnx-asr model, loaded lazily."""
    _instance = None
    _lock = threading.Lock()
    _needs_reload = False

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is not None and not cls._needs_reload:
                return cls._instance
            cls._instance = None
            cls._needs_reload = False
            try:
                import onnx_asr
                import onnxruntime
                device = resolve_whisper_device()
                if device == 'cuda':
                    if hasattr(onnxruntime, 'preload_dlls'):
                        onnxruntime.preload_dlls()
                    providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
                    quantization = None
                else:
                    providers = ['CPUExecutionProvider']
                    quantization = 'int8'
                # Plain directory: ORT 1.24 rejects external weights behind hub-cache symlinks.
                model_dir = os.path.join(
                    os.environ.get('HF_HOME', '/app/data/.cache'), 'parakeet', PARAKEET_MODEL_NAME)
                _restore_backup(model_dir)
                # Shared lock: an update swaps the directory only while no process is reading it.
                with _flock(model_dir, '.lock', fcntl.LOCK_SH):
                    recorded, recheck = _fetch_model_files(model_dir, quantization)
                    # ERROR keeps real failures, drops session warnings; the import-time discovery line stays.
                    if hasattr(onnxruntime, 'set_default_logger_severity'):
                        onnxruntime.set_default_logger_severity(3)
                    try:
                        model = onnx_asr.load_model(
                            PARAKEET_MODEL_NAME, path=model_dir, quantization=quantization,
                            providers=providers).with_timestamps()
                    except Exception as e:
                        if recorded and _model_file_error(e):
                            _discard_record(model_dir, quantization)
                        raise
                _warn_on_cpu_fallback(model, device)
            except Exception as e:
                raise ModelLoadError(str(e)) from e
            logger.info("Loaded Parakeet %s on %s (providers=%s)",
                        PARAKEET_MODEL_NAME, device, providers)
            cls._instance = model
            if recheck:
                _start_recheck(model_dir, quantization)
            return model

    @classmethod
    def unload_model(cls):
        with cls._lock:
            cls._instance = None
            cls._needs_reload = False

    @classmethod
    def is_loaded(cls) -> bool:
        return cls._instance is not None

    @classmethod
    def mark_for_reload(cls):
        cls._needs_reload = True


def plan_windows(total_s, window_s, overlap_s):
    """Overlapping windows as (start, end, core_lo, core_hi); cores tile [0, total_s]."""
    if total_s <= 0:
        return []
    step = window_s - overlap_s
    half = overlap_s / 2
    plan = []
    start = 0.0
    while True:
        end = min(start + window_s, total_s)
        last = end >= total_s
        plan.append((start, end,
                     0.0 if start == 0 else start + half,
                     total_s if last else end - half))
        if last:
            return plan
        start += step


def words_from_tokens(tokens, timestamps, offset_s=0.0):
    """Join SentencePiece tokens into words; ends are approximated from the next word start."""
    if len(tokens) != len(timestamps):
        logger.warning("Parakeet returned %d tokens but %d timestamps; using the shorter",
                       len(tokens), len(timestamps))
    groups = []
    for tok, ts in zip(tokens, timestamps, strict=False):
        text = tok.lstrip(' ' + WORD_MARKER)
        # onnx-asr turns the SentencePiece marker into a leading space, so accept both.
        starts_word = tok[:1] in (' ', WORD_MARKER)
        if _SPECIAL_TOKEN.match(tok.strip()):
            continue
        if starts_word or not groups:
            groups.append({'text': text, 'start': ts, 'last': ts})
        else:
            groups[-1]['text'] += tok
            groups[-1]['last'] = ts
    # A bare marker opens a word that unmarked pieces (digits) fill; still-empty ones are dropped.
    groups = [g for g in groups if g['text']]
    words = []
    for i, g in enumerate(groups):
        start = g['start'] + offset_s
        if i + 1 < len(groups):
            end = min(groups[i + 1]['start'] + offset_s, start + PARAKEET_WORD_MAX_S)
        else:
            end = g['last'] + offset_s + PARAKEET_TOKEN_STEP_S
        words.append({'word': g['text'], 'start': start, 'end': end})
    return words


def _norm_word(text):
    """Casefolded word without edge punctuation, for boundary dedupe."""
    return text.casefold().strip(string.punctuation)


def _is_latin(ch):
    return ch.isascii() or unicodedata.name(ch, '').startswith('LATIN')


def drop_non_latin_words(words):
    """Drop words whose letters are mostly non-Latin; returns (kept, dropped_count)."""
    kept = []
    for w in words:
        letters = [c for c in w['word'] if c.isalpha()]
        foreign = sum(1 for c in letters if not _is_latin(c))
        if letters and foreign * 2 > len(letters):
            continue
        kept.append(w)
    return kept, len(words) - len(kept)


def group_segments(words):
    """Group words into segments split on sentence ends, long pauses and the length cap."""
    segments = []
    cur = []

    def flush():
        if cur:
            segments.append({
                'start': cur[0]['start'], 'end': cur[-1]['end'],
                'text': ' '.join(w['word'] for w in cur).strip(), 'words': list(cur),
            })
            cur.clear()

    for w in words:
        if cur and (w['start'] - cur[-1]['end'] > PARAKEET_SEGMENT_PAUSE_S
                    or w['end'] - cur[0]['start'] > PARAKEET_SEGMENT_MAX_S):
            flush()
        cur.append(w)
        if w['word'].endswith(_SENTENCE_END):
            flush()
    flush()
    return segments


def read_wav_float32(path):
    """Read a 16-bit PCM WAV as mono float32; returns (samples, sample_rate)."""
    with wave.open(str(path), 'rb') as wf:
        if wf.getsampwidth() != 2:
            raise ValueError(f"Unsupported WAV sample width {wf.getsampwidth()} bytes (need 16-bit PCM)")
        channels = wf.getnchannels()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    data = np.frombuffer(raw, '<i2').astype(np.float32)
    data /= 32768.0
    if channels > 1:
        data = data[:len(data) // channels * channels].reshape(-1, channels).mean(axis=1)
    return data, rate


# Kept words interleave in time at a seam, so the lookback is bounded by count.
_SEAM_LOOKBACK = 12


def _recent_words(words, floor):
    """Kept words near the seam whose start is at or after floor."""
    return [k for k in words[-_SEAM_LOOKBACK:] if k['start'] >= floor]


def _merge_windows(decoded):
    """Merge per-window words, dropping the overlap re-decode by text and then by time."""
    # Token times are quantized per window, so cores get two token steps of slack.
    slack = PARAKEET_TOKEN_STEP_S * 2
    words = []
    for core_lo, core_hi, window_words in decoded:
        cands = [w for w in window_words if core_lo - slack <= w['start'] < core_hi + slack]
        tail = [_norm_word(k['word']) for k in words[-6:]]
        head = [_norm_word(w['word']) for w in cands[:6]]
        # Drop the longest leading run of the window that repeats the kept tail (start times can drift past slack).
        for n in range(min(len(tail), len(head)), 0, -1):
            if (head[:n] == tail[-n:]
                    and cands[n - 1]['start'] <= core_lo + PARAKEET_OVERLAP_S + slack):
                cands = cands[n:]
                break
        for w in cands:
            if any(_norm_word(k['word']) == _norm_word(w['word']) and k['start'] <= w['start'] + slack
                   for k in _recent_words(words, w['start'] - slack)):
                continue
            words.append(w)
    return words


def decode(wav_path, language):
    """Transcribe a preprocessed WAV into segments shaped like faster-whisper output."""
    samples, rate = read_wav_float32(wav_path)
    total_s = len(samples) / rate
    model = ParakeetModelSingleton.get_instance()
    decoded = []
    for start, end, core_lo, core_hi in plan_windows(total_s, PARAKEET_WINDOW_S, PARAKEET_OVERLAP_S):
        chunk = samples[int(start * rate):int(end * rate)]
        result = model.recognize(chunk, sample_rate=rate)
        decoded.append((core_lo, core_hi, words_from_tokens(result.tokens, result.timestamps, start)))
    words = _merge_windows(decoded)
    if language in _ENGLISH:
        words, dropped = drop_non_latin_words(words)
        if dropped:
            logger.info("Parakeet dropped %d non-Latin words", dropped)
    return group_segments(words)
