"""In-process NVIDIA Parakeet TDT transcription through onnx-asr (#834)."""
import importlib.util
import logging
import os
import re
import string
import threading
import unicodedata
import wave

import numpy as np

from config import (
    PARAKEET_MODEL_NAME, PARAKEET_MODEL_REPO, PARAKEET_OVERLAP_S, PARAKEET_SEGMENT_MAX_S,
    PARAKEET_SEGMENT_PAUSE_S, PARAKEET_TOKEN_STEP_S, PARAKEET_WINDOW_S,
    PARAKEET_WORD_MAX_S, resolve_whisper_device,
)
from utils.errors import ModelLoadError
from utils.segments import normalize_segment, split_segments_into_sentences

logger = logging.getLogger('podcast.parakeet')

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


def _fetch_model_files(model_dir, quantization):
    """Download the model files into model_dir; fall back to a local copy when offline."""
    from huggingface_hub import snapshot_download
    suffix = f'?{quantization}' if quantization else ''
    patterns = ['config.json', 'config.yaml', 'vocab.txt']
    for name in ('encoder-model', 'decoder_joint-model'):
        patterns += [f'{name}{suffix}.onnx', f'{name}{suffix}.onnx?data']
    try:
        snapshot_download(PARAKEET_MODEL_REPO, local_dir=model_dir, allow_patterns=patterns)
    except Exception as e:
        encoder = os.path.join(model_dir, f'encoder-model{suffix.replace("?", ".")}.onnx')
        if not os.path.isfile(encoder):
            raise
        logger.warning("Parakeet model download failed, using the local copy: %s", e)


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
                _fetch_model_files(model_dir, quantization)
                model = onnx_asr.load_model(
                    PARAKEET_MODEL_NAME, path=model_dir, quantization=quantization,
                    providers=providers).with_timestamps()
            except Exception as e:
                raise ModelLoadError(str(e)) from e
            logger.info("Loaded Parakeet %s on %s (providers=%s)",
                        PARAKEET_MODEL_NAME, device, providers)
            cls._instance = model
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
        if _SPECIAL_TOKEN.match(tok.strip()):
            continue
        # onnx-asr turns the SentencePiece marker into a leading space, so accept both.
        starts_word = tok[:1] in (' ', WORD_MARKER)
        if starts_word or not groups:
            text = tok.lstrip(' ' + WORD_MARKER)
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


def group_segments(words: list[dict]):
    """Group words into segments split on sentence ends, long pauses and the length cap."""
    if not words:
        return []

    # create an initial segment encompassing all words
    the_segment = normalize_segment({
        'words': words,
        'start': 0, # will be filled
        'end': 0,   # will be filled
        'text': '', # will be filled
    }, normalize_from_words=True)

    # now split the segment into sentences
    return split_segments_into_sentences(
        [the_segment],
        max_word_gap=PARAKEET_SEGMENT_PAUSE_S,
        max_segment_duration=PARAKEET_SEGMENT_MAX_S,
    )


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
