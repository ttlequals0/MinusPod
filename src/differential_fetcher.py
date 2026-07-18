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
# Consecutive matched blocks within this offset delta share one offset group.
OFFSET_GROUP_TOL_S = 0.25
# Normalized cross-correlation: reference length and search radius.
XCORR_REF_S = 4.0
XCORR_SEARCH_S = 2.0
# Minimum peak correlation to call a block identical across fetches.
XCORR_MIN_CORR = 0.75
# Differential regions shorter than this are alignment noise, not fills.
MIN_REGION_S = 1.0
# Whole-file re-encode / unreliable-alignment guard (#541). A real DAI show --
# even an ad-heavy one -- inserts discrete breaks the aligner still locks onto,
# so it keeps meaningful confirmed-identical coverage. The failure case is the
# CDN re-encoding the WHOLE file on the refetch: identical content no longer
# cross-correlates, the aligner locks onto almost nothing, and nearly the whole
# run falls to the differential complement. Discard the differential only when
# BOTH hold: (a) more than DIFFERENTIAL_MAX_FRACTION of the run reads as
# differing -- a real show is almost never >70% ads; and (b) confirmed-identical
# coverage is below IDENTICAL_MIN_FRACTION -- the aligner essentially never
# locked on. Requiring both avoids nuking a short or genuinely ad-heavy but
# correctly-aligned episode (high differential but decent identical coverage,
# which keeps its discrete ads).
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
                       coarse_offset: float):
    """Peak normalized cross-correlation of one run block against the refetch.

    Correlates a XCORR_REF_S reference from the run file at run_t against
    the refetch file within +-XCORR_SEARCH_S of the coarse silence-chain
    offset. FFT-based so a 4s reference over an 8s search span stays cheap.
    Returns the peak NCC in [-1, 1], or None when a window falls outside
    either file or the reference is silent.

    Only the correlation confidence is consumed: the coarse silence-midpoint
    boundaries already meet the 0.5s region tolerance and the +-3s downstream
    corroboration, so the sub-sample peak lag is not fed back into region
    boundaries and is not returned.
    """
    ref_len = int(XCORR_REF_S * PCM_RATE)
    a0 = int(run_t * PCM_RATE)
    template = run_pcm[a0:a0 + ref_len].astype(np.float64)
    if len(template) < ref_len:
        return None
    b0 = max(0, int((run_t + coarse_offset - XCORR_SEARCH_S) * PCM_RATE))
    b1 = int((run_t + coarse_offset + XCORR_SEARCH_S) * PCM_RATE) + ref_len
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


def align_and_diff(run_file: str, refetch_file: str, work_dir: str) -> dict:
    """Align two fetches of one episode and diff them.

    Pure over file paths (ffmpeg + numpy, no network). Returns
    {'status': 'ok' | 'no_differential' | 'unreliable_reencode',
    'regions': [...]} with regions in RUN-file coordinates. kind 'identical'
    means the audio matches across fetches (content or baked-in ad);
    'differential' means it differs (dynamically inserted). no_differential
    means "no differential found", NOT "no DAI" -- same-fill re-rolls are a
    known false negative. unreliable_reencode means alignment failed wholesale
    (e.g. the CDN re-encoded the whole file on refetch); regions is empty so
    downstream treats it as no differential (#541).
    """
    run_pcm = _decode_pcm(run_file, work_dir, 'run')
    ref_pcm = _decode_pcm(refetch_file, work_dir, 'refetch')
    run_dur = len(run_pcm) / PCM_RATE

    run_marks = _silence_marks(run_file, run_dur)
    ref_marks = _silence_marks(refetch_file, len(ref_pcm) / PCM_RATE)
    pairs = _chain_marks(run_marks, ref_marks)

    # Collapse contiguous matched blocks into piecewise-constant offset groups.
    groups = []
    for i, j in pairs:
        start, end = run_marks[i], run_marks[i + 1]
        offset = ref_marks[j] - run_marks[i]
        if (groups
                and abs(groups[-1]['offset'] - offset) <= OFFSET_GROUP_TOL_S
                and abs(groups[-1]['end'] - start) <= OFFSET_GROUP_TOL_S):
            groups[-1]['end'] = end
            groups[-1]['blocks'].append((start, end))
        else:
            groups.append({'start': start, 'end': end, 'offset': offset,
                           'blocks': [(start, end)]})

    # One correlation probe per group, inside its longest block.
    identical = []
    for group in groups:
        longest = max(group['blocks'], key=lambda b: b[1] - b[0])
        if longest[1] - longest[0] < XCORR_REF_S + 1.0:
            continue
        corr = _block_correlation(
            run_pcm, ref_pcm, longest[0] + 0.5, group['offset'])
        if corr is None or corr < XCORR_MIN_CORR:
            continue
        identical.append({
            'start_s': round(group['start'], 2),
            'end_s': round(group['end'], 2),
            'kind': 'identical',
            'corr': round(corr, 3),
        })

    # Differential regions are the complement of identical coverage.
    regions = list(identical)
    cursor = 0.0
    for region in identical:
        if region['start_s'] - cursor >= MIN_REGION_S:
            regions.append({'start_s': round(cursor, 2),
                            'end_s': region['start_s'],
                            'kind': 'differential', 'corr': 0.0})
        cursor = max(cursor, region['end_s'])
    if run_dur - cursor >= MIN_REGION_S:
        regions.append({'start_s': round(cursor, 2),
                        'end_s': round(run_dur, 2),
                        'kind': 'differential', 'corr': 0.0})
    regions.sort(key=lambda r: r['start_s'])

    # Whole-file re-encode / unreliable-alignment guard (#541). Compute how much
    # of the run reads as differing versus confirmed-identical. Discard the
    # differential only when the run is BOTH mostly differential AND barely
    # locked on -- the wholesale-misalignment signature of a CDN re-encode where
    # real content no longer correlates. A discrete-ad episode, even an ad-heavy
    # one, keeps decent identical coverage and so is never discarded here; the
    # AND is what stops this guard from nuking a legitimately ad-heavy but
    # correctly-aligned episode.
    run_dur_safe = run_dur if run_dur > 0 else 1.0
    differential_dur = sum(r['end_s'] - r['start_s']
                           for r in regions if r['kind'] == 'differential')
    identical_dur = sum(r['end_s'] - r['start_s']
                        for r in regions if r['kind'] == 'identical')
    differential_fraction = differential_dur / run_dur_safe
    identical_fraction = identical_dur / run_dur_safe
    if (differential_fraction > DIFFERENTIAL_MAX_FRACTION
            and identical_fraction < IDENTICAL_MIN_FRACTION):
        logger.warning(
            'Differential alignment unreliable for %s: differential_fraction'
            '=%.2f (max %.2f), identical_fraction=%.2f (min %.2f); discarding '
            'regions as likely whole-file re-encode',
            os.path.basename(run_file), differential_fraction,
            DIFFERENTIAL_MAX_FRACTION, identical_fraction, IDENTICAL_MIN_FRACTION)
        return {'status': 'unreliable_reencode', 'regions': []}

    has_diff = any(r['kind'] == 'differential' for r in regions)
    return {'status': 'ok' if has_diff else 'no_differential',
            'regions': regions}


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
