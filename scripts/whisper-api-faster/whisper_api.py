#!/usr/bin/env python3
"""
Whisper Transcription API — OpenAI-compatible /v1/audio/transcriptions endpoint
backed by faster-whisper.

Designed to be deployed standalone (systemd unit, container, or `python3
whisper_api.py`) and pointed at by MinusPod via the `openai-api` whisper
backend. Returns OpenAI-shaped `verbose_json` (text + segments + per-segment
word timings), which MinusPod requires for precise ad-boundary detection.

Configuration via environment variables:
  WHISPER_MODEL         (default: large-v3)
  WHISPER_PORT          (default: 8090)
  WHISPER_HOST          (default: 0.0.0.0; set 127.0.0.1 to bind localhost only)
  WHISPER_DEVICE        (default: cuda)
  WHISPER_COMPUTE_TYPE  (default: float16; use int8 for CPU)
  WHISPER_API_KEY       (optional: when set, require `Authorization: Bearer <key>`)
"""

import hmac
import os
import tempfile
import threading
from pathlib import Path

from flask import Flask, request, jsonify
from faster_whisper import WhisperModel

app = Flask(__name__)

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3")
PORT = int(os.environ.get("WHISPER_PORT", 8090))
HOST = os.environ.get("WHISPER_HOST", "0.0.0.0")
DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")
# Optional bearer-token auth. Empty = open (trusted-network deployments keep
# working unchanged); set WHISPER_API_KEY to require a matching token. The
# service binds 0.0.0.0 by default, so set this whenever the port is reachable
# beyond the host.
API_KEY = os.environ.get("WHISPER_API_KEY", "")

print(f"Loading Whisper {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE})...", flush=True)
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
print("Model ready", flush=True)

# A single WhisperModel is not safe to drive from multiple threads at once
# (Flask is started with threaded=True). Serialize all inference so two
# concurrent requests can never decode through the same model simultaneously,
# which would otherwise spike GPU memory / OOM or corrupt output.
_MODEL_LOCK = threading.Lock()


def _auth_error():
    """When WHISPER_API_KEY is configured, require a matching Bearer token.
    Returns a 401 response tuple on failure, or None when the request is
    allowed. Uses a constant-time compare to avoid token-timing leaks."""
    if not API_KEY:
        return None
    auth = request.headers.get("Authorization", "")
    token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
    # Compare as bytes: hmac.compare_digest raises TypeError on a non-ASCII str,
    # so a non-ASCII Authorization header would otherwise 500 (a DoS vector).
    # encode('utf-8') makes the compare total over all inputs.
    if not hmac.compare_digest(token.encode("utf-8"), API_KEY.encode("utf-8")):
        return jsonify({"error": "Unauthorized"}), 401
    return None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": MODEL_SIZE})


@app.route("/v1/audio/transcriptions", methods=["POST"])
@app.route("/v1/audio/transcribe", methods=["POST"])
def transcribe():
    """
    OpenAI-compatible transcription endpoint.
    POST multipart/form-data with:
      - file: audio file (wav, mp3, m4a, flac, ogg, webm, mp4)
      - language: optional ISO 639-1 language code (e.g. "en")
      - response_format: json | verbose_json | text | srt | vtt (default: json)
      - temperature: float 0-1 (default: 0)
      - prompt: optional text prompt to guide transcription

    `verbose_json` is required by MinusPod and emits per-segment `words[]`
    timing alongside `segments[]` and top-level `text`.
    """
    auth_err = _auth_error()
    if auth_err:
        return auth_err

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    audio_file = request.files["file"]
    language = request.form.get("language", None)
    response_format = request.form.get("response_format", "json")
    try:
        temperature = float(request.form.get("temperature", 0.0))
    except (TypeError, ValueError):
        return jsonify({"error": "temperature must be a number"}), 400
    prompt = request.form.get("prompt", "")

    suffix = Path(audio_file.filename).suffix if audio_file.filename else ".wav"
    tmp_path = None
    try:
        # Assign the path BEFORE save() so a failed save still cleans up the
        # file created by delete=False (and doesn't raise UnboundLocalError in
        # the finally block, which would mask the original error).
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            audio_file.save(tmp)

        with _MODEL_LOCK:
            segments, info = model.transcribe(
                tmp_path,
                language=language,
                temperature=temperature,
                initial_prompt=prompt if prompt else None,
                beam_size=5,
                vad_filter=True,
                word_timestamps=True,
            )
            # Force the lazy generator to completion while holding the lock —
            # this is where the actual decode happens.
            segments = list(segments)

        if response_format in ("json", "verbose_json"):
            text_parts = []
            seg_list = []
            include_words = (response_format == "verbose_json")
            for seg in segments:
                text_parts.append(seg.text)
                entry = {
                    "id": len(seg_list),
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text,
                }
                if include_words and getattr(seg, "words", None):
                    entry["words"] = [
                        {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
                        for w in seg.words
                    ]
                seg_list.append(entry)

            full_text = "".join(text_parts).strip()
            return jsonify({
                "text": full_text,
                "segments": seg_list,
                "language": info.language,
            })

        elif response_format == "text":
            full_text = "".join([s.text for s in segments]).strip()
            return full_text, 200, {"Content-Type": "text/plain; charset=utf-8"}

        elif response_format == "srt":
            lines = []
            for i, seg in enumerate(segments, 1):
                start = _srt_time(seg.start)
                end = _srt_time(seg.end)
                lines.append(f"{i}\n{start} --> {end}\n{seg.text.strip()}\n")
            return "\n".join(lines), 200, {"Content-Type": "text/plain; charset=utf-8"}

        elif response_format == "vtt":
            lines = ["WEBVTT", ""]
            for seg in segments:
                start = _vtt_time(seg.start)
                end = _vtt_time(seg.end)
                lines.append(f"{start} --> {end}")
                lines.append(seg.text.strip())
                lines.append("")
            return "\n".join(lines), 200, {"Content-Type": "text/vtt; charset=utf-8"}

        else:
            return jsonify({"error": f"Unknown format: {response_format}"}), 400

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _srt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _vtt_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
