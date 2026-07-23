"""Cross-fetch differential fetcher (Layer 3).

Re-fetches an episode enclosure with a different podcast-client User-Agent
after transcription and diffs the two files. Audio that differs across
fetches is dynamically inserted by definition; identical audio is content
or a baked-in ad.

The primary download uses config.BROWSER_USER_AGENT (see
transcriber.download_audio); the refetch always presents a different,
realistic podcast-client string because ad decisioning keys on the request
fingerprint and UA + natural time spacing is the only variation available.
"""

import logging
import os
import random

import numpy as np

from audio_analysis.silence_detector import SilenceDetector
from config import BROWSER_USER_AGENT, HTTP_MAX_REDIRECTS_FEED
from utils.audio import get_audio_duration
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_get, stream_to_file_capped
from utils.subprocess_registry import tracked_run

# Realistic podcast-client UA strings for the refetch pool.
REFETCH_USER_AGENTS = (
    # Apple Podcasts on iOS
    'Podcasts/1650.1 CFNetwork/1494.0.7 Darwin/23.4.0',
    # Overcast
    'Overcast/3.0 (+http://overcast.fm/; iOS podcast app)',
    # Pocket Casts
    'PocketCasts/7.61 (+https://pocketcasts.com/)',
    # AntennaPod
    'AntennaPod/3.4.0',
    # Castro
    'Castro/2024.11 (iPhone; iOS 17.5)',
)

# DAI hosting / analytics-prefix domains. Prefix services chain the
# downstream hosts inside the URL path (e.g. pdst.fm/e/chrt.fm/track/...),
# so substring-matching one enclosure URL covers the whole redirect chain.
DAI_URL_DOMAINS = (
    'pdst.fm',
    'pscrb.fm',
    'mgln.ai',
    'megaphone.fm',
    'podtrac.com',
    'chrt.fm',
    'arttrk.com',
    'clrtpod.com',
    'dts.podtrac.com',
)


def pick_refetch_user_agent(first_ua: str | None) -> str:
    """Pick a refetch User-Agent from the pool, never equal to first_ua."""
    pool = [ua for ua in REFETCH_USER_AGENTS if ua != first_ua]
    return random.choice(pool)


def is_likely_dai_feed(enclosure_urls) -> bool:
    """True when any enclosure URL matches a known DAI/prefix domain."""
    for url in enclosure_urls or []:
        lowered = (url or '').lower()
        if any(domain in lowered for domain in DAI_URL_DOMAINS):
            return True
    return False


# --- Alignment engine -------------------------------------------------------

# 8kHz mono PCM is the shared analysis timebase for alignment.
PCM_RATE = 8000
# silencedetect fingerprint parameters (spec Layer 3.2: -35 dB / 0.2 s).
SILENCE_NOISE_DB = -35.0
SILENCE_MIN_S = 0.2
# Keep only the longest silences on very chatty files so the DP stays small.
MAX_SILENCE_MARKS = 400
# Interval tolerance for duration-matched chaining.
CHAIN_TOLERANCE_S = 0.5
# Normalized cross-correlation: reference length and search radius.
XCORR_REF_S = 4.0
XCORR_SEARCH_S = 2.0
# Minimum peak correlation to call a block identical across fetches.
XCORR_MIN_CORR = 0.75
# Differential regions shorter than this are alignment noise, not fills.
MIN_REGION_S = 1.0
# Whole-file re-encode guard (#541): a CDN re-encode on the refetch stops
# identical content from correlating, so nearly the whole run falls to the
# differential complement. Discard only when BOTH >70% reads as differing
# (a real show is almost never >70% ads) AND identical coverage is <15%
# (the aligner never locked on) -- requiring both keeps a genuinely
# ad-heavy but correctly-aligned episode's discrete ads.
DIFFERENTIAL_MAX_FRACTION = 0.7
IDENTICAL_MIN_FRACTION = 0.15
# ffmpeg decode guard.
DECODE_TIMEOUT_S = 600

logger = logging.getLogger('podcast.differential_fetcher')


def _decode_pcm(audio_path: str, work_dir: str, tag: str) -> np.ndarray:
    """Decode audio to 8kHz mono float32 in [-1, 1]."""
    pcm_path = os.path.join(work_dir, f'diff_{tag}.pcm')
    try:
        tracked_run(
            ['ffmpeg', '-y', '-i', audio_path, '-ac', '1', '-ar', str(PCM_RATE),
             '-f', 's16le', '-acodec', 'pcm_s16le', pcm_path],
            check=True, capture_output=True, timeout=DECODE_TIMEOUT_S)
        data = np.fromfile(pcm_path, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        logger.error('PCM decode/read failed for %s (tag=%s)', audio_path, tag)
        raise
    finally:
        if os.path.exists(pcm_path):
            os.unlink(pcm_path)
    return data


def _silence_marks(audio_path: str, duration_s: float) -> list:
    """Silence-midpoint fingerprints bracketed by virtual file-edge marks."""
    spans = SilenceDetector(
        noise_db=SILENCE_NOISE_DB, min_silence_s=SILENCE_MIN_S).detect(audio_path)
    if len(spans) > MAX_SILENCE_MARKS:
        spans = sorted(spans, key=lambda s: s['duration'],
                       reverse=True)[:MAX_SILENCE_MARKS]
        spans.sort(key=lambda s: s['start'])
    marks = [(s['start'] + s['end']) / 2.0 for s in spans]
    return [0.0] + marks + [duration_s]


def _chain_marks(run_marks: list, ref_marks: list) -> list:
    """Duration-matched chaining: LCS-style DP over inter-mark intervals.

    Returns matched (i, j) index pairs meaning run block
    [run_marks[i], run_marks[i+1]] aligns with refetch block
    [ref_marks[j], ref_marks[j+1]].
    """
    run_iv = [run_marks[i + 1] - run_marks[i] for i in range(len(run_marks) - 1)]
    ref_iv = [ref_marks[j + 1] - ref_marks[j] for j in range(len(ref_marks) - 1)]
    n, m = len(run_iv), len(ref_iv)
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if abs(run_iv[i - 1] - ref_iv[j - 1]) <= CHAIN_TOLERANCE_S:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    pairs = []
    i, j = n, m
    while i > 0 and j > 0:
        if (abs(run_iv[i - 1] - ref_iv[j - 1]) <= CHAIN_TOLERANCE_S
                and dp[i][j] == dp[i - 1][j - 1] + 1):
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def _block_correlation(run_pcm: np.ndarray, ref_pcm: np.ndarray, run_t: float,
                       coarse_offset: float, *, ref_s: float = XCORR_REF_S,
                       search_s: float = XCORR_SEARCH_S):
    """Peak normalized cross-correlation of one run block against the refetch.

    Correlates a ref_s reference from the run file at run_t against the
    refetch file within +-search_s of the coarse silence-chain offset.
    FFT-based so a 4s reference over an 8s search span stays cheap.
    Returns the peak NCC in [-1, 1], or None when a window falls outside
    either file or the reference is silent.

    Only the correlation confidence is consumed: the coarse silence-midpoint
    boundaries already meet the 0.5s region tolerance and the +-3s downstream
    corroboration, so the sub-sample peak lag is not fed back into region
    boundaries and is not returned.
    """
    ref_len = int(ref_s * PCM_RATE)
    a0 = int(run_t * PCM_RATE)
    template = run_pcm[a0:a0 + ref_len].astype(np.float64)
    if len(template) < ref_len:
        return None
    b0 = max(0, int((run_t + coarse_offset - search_s) * PCM_RATE))
    b1 = int((run_t + coarse_offset + search_s) * PCM_RATE) + ref_len
    haystack = ref_pcm[b0:b1].astype(np.float64)
    if len(haystack) < ref_len:
        return None

    template = template - template.mean()
    haystack = haystack - haystack.mean()
    t_norm = np.sqrt(np.sum(template ** 2))
    if t_norm < 1e-9:
        return None

    n_lags = len(haystack) - ref_len + 1
    nfft = 1
    while nfft < len(haystack) + ref_len:
        nfft <<= 1
    corr = np.fft.irfft(
        np.fft.rfft(haystack, nfft) * np.conj(np.fft.rfft(template, nfft)),
        nfft)[:n_lags]
    # Sliding L2 norm of every haystack window. The haystack mean is removed
    # once globally rather than per-window: an approximation that holds for
    # AC-coupled audio and keeps normalization O(n).
    sq = np.concatenate(([0.0], np.cumsum(haystack ** 2)))
    win_norm = np.sqrt(np.maximum(sq[ref_len:] - sq[:-ref_len], 1e-12))
    ncc = corr / (t_norm * win_norm)
    return float(np.max(ncc))


def _probe_block(run_pcm: np.ndarray, ref_pcm: np.ndarray, start: float,
                 end: float, offset: float):
    """Measure one silence-delimited run block against the refetch.

    Returns (kind, corr): ('identical'|'differential', float) for a usable
    probe, ('unknown', None) when the block is too short to probe or the
    probe window is unusable (silent template, window outside either file).

    A block scoring below XCORR_MIN_CORR gets ONE retry with a doubled
    search window (drift re-probe): a coarse offset that is stale by more
    than XCORR_SEARCH_S -- e.g. silencedetect missed a mark on the refetch
    -- would otherwise mislabel identical audio as differential. The
    measured corr is the best of the probes.
    """
    block_len = end - start
    if block_len < MIN_REGION_S:
        return 'unknown', None
    ref_s = min(XCORR_REF_S, block_len)
    # Lead past the half-silence at the block edge when there is room.
    run_t = start + min(0.5, block_len - ref_s)
    corr = _block_correlation(run_pcm, ref_pcm, run_t, offset, ref_s=ref_s)
    if corr is None:
        return 'unknown', None
    if corr < XCORR_MIN_CORR:
        retry = _block_correlation(run_pcm, ref_pcm, run_t, offset,
                                   ref_s=ref_s, search_s=XCORR_SEARCH_S * 2)
        if retry is not None:
            corr = max(corr, retry)
    kind = 'identical' if corr >= XCORR_MIN_CORR else 'differential'
    return kind, corr


def _align_and_diff_pcm(run_pcm: np.ndarray, ref_pcm: np.ndarray,
                        run_marks: list, ref_marks: list) -> dict:
    """Diff two decoded fetches given their silence marks (see align_and_diff).

    Probes EVERY silence-delimited run block, so every emitted region
    carries a measured corr: 'identical' and 'differential' from the peak
    NCC of the block's own probe, 'unknown' (corr None) when the block
    could not be measured. Unmatched blocks (no duration-matched refetch
    counterpart -- typically DAI fills of differing length) are probed at
    the nearest matched block's offset.
    """
    pairs = _chain_marks(run_marks, ref_marks)
    offsets = {i: ref_marks[j] - run_marks[i] for i, j in pairs}
    n_blocks = len(run_marks) - 1

    # Nearest matched offset for unmatched blocks: prefer the previous
    # matched block (same piecewise-constant offset segment), fall back to
    # the next one at the file head.
    next_offset = [None] * n_blocks
    upcoming = None
    for i in range(n_blocks - 1, -1, -1):
        if i in offsets:
            upcoming = offsets[i]
        next_offset[i] = upcoming

    blocks = []
    last_offset = None
    for i in range(n_blocks):
        start, end = run_marks[i], run_marks[i + 1]
        if i in offsets:
            last_offset = offsets[i]
            offset = last_offset
        else:
            offset = last_offset if last_offset is not None else next_offset[i]
        if offset is None:
            kind, corr = 'unknown', None
        else:
            kind, corr = _probe_block(run_pcm, ref_pcm, start, end, offset)
        blocks.append({'start_s': start, 'end_s': end,
                       'kind': kind, 'corr': corr})

    # Blocks tile the run file, so adjacent same-kind blocks merge into one
    # region. A multi-ad break spanning several silence-delimited blocks
    # must come out as ONE differential region or each sub-block could fall
    # under the downstream hold duration floor. Merged corr: max for
    # differential (the least-different member gates candidacy), min for
    # identical (the weakest member still cleared XCORR_MIN_CORR).
    merged = []
    for block in blocks:
        if merged and merged[-1]['kind'] == block['kind']:
            prev = merged[-1]
            prev['end_s'] = block['end_s']
            if block['corr'] is not None:
                pick = max if block['kind'] == 'differential' else min
                prev['corr'] = pick(prev['corr'], block['corr'])
        else:
            merged.append(dict(block))
    regions = [{'start_s': round(r['start_s'], 2),
                'end_s': round(r['end_s'], 2),
                'kind': r['kind'],
                'corr': None if r['corr'] is None else round(r['corr'], 3)}
               for r in merged]

    # Re-encode guard (#541): see the constants above for the rationale.
    # Fractions are computed over the MEASURED duration (identical +
    # differential): 'unknown' regions are unmeasured, so counting them in
    # the denominator would dilute the differential fraction and let a
    # whole-file re-encode slip past the guard on a chatty file.
    differential_dur = sum(r['end_s'] - r['start_s']
                           for r in regions if r['kind'] == 'differential')
    identical_dur = sum(r['end_s'] - r['start_s']
                        for r in regions if r['kind'] == 'identical')
    measured_dur = differential_dur + identical_dur
    if measured_dur > 0:
        differential_fraction = differential_dur / measured_dur
        identical_fraction = identical_dur / measured_dur
        if (differential_fraction > DIFFERENTIAL_MAX_FRACTION
                and identical_fraction < IDENTICAL_MIN_FRACTION):
            logger.warning(
                'Differential alignment unreliable: differential_fraction'
                '=%.2f (max %.2f), identical_fraction=%.2f (min %.2f) over '
                '%.0fs measured; discarding regions as likely whole-file '
                're-encode',
                differential_fraction, DIFFERENTIAL_MAX_FRACTION,
                identical_fraction, IDENTICAL_MIN_FRACTION, measured_dur)
            return {'status': 'unreliable_reencode', 'regions': []}

    has_diff = any(r['kind'] == 'differential' for r in regions)
    return {'status': 'ok' if has_diff else 'no_differential',
            'regions': regions}


def align_and_diff(run_file: str, refetch_file: str, work_dir: str) -> dict:
    """Align two fetches of one episode and diff them.

    Pure over file paths (ffmpeg + numpy, no network). Returns
    {'status': 'ok' | 'no_differential' | 'unreliable_reencode',
    'regions': [...]} with regions in RUN-file coordinates. kind 'identical'
    means the audio matches across fetches (content or baked-in ad);
    'differential' means it differs (dynamically inserted); 'unknown' means
    the block could not be measured (corr None) and is never a candidate.
    no_differential means "no differential found", NOT "no DAI" -- same-fill
    re-rolls are a known false negative. unreliable_reencode means alignment
    failed wholesale (e.g. the CDN re-encoded the whole file on refetch);
    regions is empty so downstream treats it as no differential (#541).
    """
    run_pcm = _decode_pcm(run_file, work_dir, 'run')
    ref_pcm = _decode_pcm(refetch_file, work_dir, 'refetch')

    run_marks = _silence_marks(run_file, len(run_pcm) / PCM_RATE)
    ref_marks = _silence_marks(refetch_file, len(ref_pcm) / PCM_RATE)
    result = _align_and_diff_pcm(run_pcm, ref_pcm, run_marks, ref_marks)
    if result['status'] == 'unreliable_reencode':
        logger.warning('Discarded differential regions for %s',
                       os.path.basename(run_file))
    return result


# --- Refetch ---------------------------------------------------------------

# Refetch may not exceed this multiple of the primary file's size.
REFETCH_SIZE_FACTOR = 1.5


def fetch_and_diff(enclosure_url: str, run_file_path: str, work_dir: str,
                   timeout_s: int = 300) -> dict:
    """Refetch the enclosure with a rotated podcast-client UA and diff it
    against the run file.

    Never raises: every failure returns status 'error' so the pipeline can
    record it and continue. timeout_s is the per-read timeout passed to
    safe_get; matched to the primary download's 300s cap.
    """
    ua = pick_refetch_user_agent(BROWSER_USER_AGENT)
    meta = {'ua': ua, 'size': None, 'duration': None}
    refetch_path = os.path.join(work_dir, 'refetch_audio')
    try:
        run_size = os.path.getsize(run_file_path)
        max_bytes = int(run_size * REFETCH_SIZE_FACTOR)
        response = safe_get(
            enclosure_url,
            trust=URLTrust.FEED_CONTENT,
            timeout=(10, timeout_s),
            max_redirects=HTTP_MAX_REDIRECTS_FEED,
            stream=True,
            headers={'User-Agent': ua, 'Accept': '*/*'},
        )
        try:
            response.raise_for_status()
            with open(refetch_path, 'wb') as fh:
                stream_to_file_capped(response, fh, max_bytes)
        finally:
            response.close()
        meta['size'] = os.path.getsize(refetch_path)
        meta['duration'] = get_audio_duration(refetch_path)
        aligned = align_and_diff(run_file_path, refetch_path, work_dir)
        return {'status': aligned['status'], 'regions': aligned['regions'],
                'refetch_meta': meta, 'error': None}
    except Exception as e:
        # Never-raises boundary: any failure (network, decode, numpy internals)
        # degrades to 'error' so the pipeline records it and continues.
        logger.warning(
            f"Differential refetch failed for "
            f"{safe_url_for_log(enclosure_url)}: {e}")
        return {'status': 'error', 'regions': [], 'refetch_meta': meta,
                'error': str(e)}
    finally:
        try:
            if os.path.exists(refetch_path):
                os.unlink(refetch_path)
        except OSError:
            pass
