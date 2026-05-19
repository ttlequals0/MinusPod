# Whisper Transcription API (faster-whisper)

A standalone OpenAI-compatible `/v1/audio/transcriptions` server backed by
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). Designed to be
run on a separate GPU host and pointed at by MinusPod via the `openai-api`
whisper backend.

## Why this exists (vs. `docker-compose.whisper.yml`)

The repo already ships `docker-compose.whisper.yml`, which runs **whisper.cpp**
inside Docker. This script is an alternative for hosts that already have a
Python/CUDA toolchain and want a thin Flask wrapper around faster-whisper —
typically a beefy LXC or VM with NVIDIA passthrough that serves multiple
consumers.

## What MinusPod requires

MinusPod's transcriber posts with `response_format=verbose_json` and expects
the response to contain `segments[]` with per-segment timing, plus
`words[]` inside each segment for precise ad-boundary detection. This server
returns exactly that shape.

If you're writing your own Whisper backend, the minimum response is:

```json
{
  "text": "full transcript",
  "language": "en",
  "segments": [
    {
      "id": 0,
      "start": 1.81,
      "end": 4.97,
      "text": " A-Cast powers the world's best podcasts.",
      "words": [
        { "word": " A-Cast", "start": 1.81, "end": 2.10 },
        ...
      ]
    }
  ]
}
```

Returning `200 OK` with an empty `segments[]` (or rejecting `verbose_json`
with `400`) will cause MinusPod's transcriber to silently abort the chunk
without a useful error message.

## Install

On a host with an NVIDIA GPU and CUDA drivers:

```bash
sudo mkdir -p /opt/whisper-api
sudo cp whisper_api.py requirements.txt /opt/whisper-api/
sudo python3 -m venv /opt/whisper-api/venv
sudo /opt/whisper-api/venv/bin/pip install --upgrade pip
sudo /opt/whisper-api/venv/bin/pip install -r /opt/whisper-api/requirements.txt
sudo cp whisper-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now whisper-api
```

Verify:

```bash
curl http://localhost:8090/health
# {"status":"ok","model":"large-v3"}
```

## Configuration

Environment variables (set in the unit file or shell):

| Variable | Default | Notes |
|---|---|---|
| `WHISPER_MODEL` | `large-v3` | Any faster-whisper model id |
| `WHISPER_PORT` | `8090` | Listen port |
| `WHISPER_DEVICE` | `cuda` | Set to `cpu` for CPU-only hosts |
| `WHISPER_COMPUTE_TYPE` | `float16` | Use `int8` for CPU, `float16`/`int8_float16` for GPU |

## Point MinusPod at it

In MinusPod's `.env`:

```
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=http://<host>:8090/v1
WHISPER_API_MODEL=whisper-large-v3
WHISPER_API_KEY=
```

MinusPod's `model` field is ignored by this server — it always uses
`WHISPER_MODEL` from the environment. Set `WHISPER_API_MODEL` to anything;
it's just included in the multipart form for OpenAI-API compatibility.

## Troubleshooting

- **Transcription chunks fail with no log detail in MinusPod**: hit the
  endpoint directly with `response_format=verbose_json` and confirm the
  shape includes `segments[]` and `words[]`. MinusPod swallows 4xx
  responses without logging the body (see `_transcribe_via_api` in
  `src/transcriber.py`).
- **CUDA OOM at startup**: switch to `WHISPER_COMPUTE_TYPE=int8_float16` or
  a smaller model.
- **CPU-only host**: set `WHISPER_DEVICE=cpu` and `WHISPER_COMPUTE_TYPE=int8`.
  Throughput will be ~10× slower than GPU; consider `large-v3-turbo` instead.
