# Installation & Upgrading

[< Docs index](README.md) | [Project README](../README.md)

---

## Requirements

- Docker with NVIDIA GPU support (for local Whisper), **or** a [remote Whisper backend](transcription.md) (no GPU needed)
- Anthropic API key, [OpenRouter](https://openrouter.ai) API key, [Ollama](https://ollama.com) for local inference, **or** any OpenAI-compatible endpoint

### Memory Requirements

**GPU VRAM:**

| Whisper Model | VRAM Required |
|---------------|---------------|
| tiny | ~1 GB |
| base | ~1 GB |
| small | ~2 GB |
| medium | ~4 GB |
| large-v3 | ~5-6 GB |
| turbo | ~5 GB |

**System RAM:**

| Episode Length | RAM Required |
|----------------|-------------|
| < 1 hour | 8 GB |
| 1-2 hours | 8 GB |
| 2-4 hours | 12 GB |
| > 4 hours | 16 GB |

## Quick Start

```bash
# 1. Create environment file
cat > .env << EOF
ANTHROPIC_API_KEY=your-key-here
BASE_URL=http://localhost:8000
APP_PASSWORD=your-password
MINUSPOD_MASTER_PASSPHRASE=long-random-string-you-will-not-lose
EOF

# 2. Create data directory
mkdir -p data

# 3. Run
docker-compose up -d
```

Access the web UI at `http://localhost:8000/ui/` to add and manage feeds.

`MINUSPOD_MASTER_PASSPHRASE` is strongly recommended for production. Without it, provider API keys go into the database as plaintext. Setting it later migrates existing plaintext rows to `enc:v1:` encrypted storage on the next boot, with a mandatory pre-migration SQLite snapshot in `data/backups/`. Restoring a backup requires the same passphrase that created it, so pick a long random value and keep it somewhere separate from the database.

### CPU-only image (no GPU)

No NVIDIA GPU? Pull the CPU variant. It drops the CUDA runtime layer and the bundled NVIDIA Python wheels; the image is around 3 GB instead of ~16 GB.

Reuse the same `.env` and `data/` directory as the Quick Start, then:

```bash
docker compose -f docker-compose.cpu.yml up -d
```

That pulls `ttlequals0/minuspod:cpu` (the floating CPU tag). To pin a specific release, set `MINUSPOD_VERSION=2.0.21-cpu` in your `.env`. The `:latest` tag always points at the GPU image; CPU users should track `:cpu` or a versioned `-cpu` tag.

Local CPU transcription with `faster-whisper` is slow. For anything beyond a quick test, offload Whisper to a remote API in your `.env`:

```
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=https://api.groq.com/openai/v1
WHISPER_API_KEY=gsk_your_key_here
WHISPER_API_MODEL=whisper-large-v3-turbo
```

Groq, OpenAI, or a self-hosted whisper.cpp server (see `docker-compose.whisper.yml`) all work here.

<details>
<summary>Build the CPU image locally</summary>

If you are modifying `Dockerfile.cpu` or want to compile from source, uncomment the `build:` block in `docker-compose.cpu.yml` and run with `--build`:

```bash
docker compose -f docker-compose.cpu.yml up -d --build
```

</details>

## Upgrading to 2.0.0+

2.0.0 is a security hardening release. A `docker pull && restart` on a 1.x data volume boots without config changes, but several defaults tightened so a few setups need env-var tweaks. Full detail in [`CHANGELOG.md`](../CHANGELOG.md).

**Likely to bite you if you do nothing:**

- **Plain HTTP:** `SESSION_COOKIE_SECURE` now defaults to `true`, so browsers drop the session cookie. Login looks like it works then the next request is anonymous. Set `SESSION_COOKIE_SECURE=false` if you're not on HTTPS.
- **Behind a reverse proxy (Cloudflare, cloudflared, nginx, Traefik):** set `MINUSPOD_TRUSTED_PROXY_COUNT=1`. Without it, login lockout and per-IP rate limits silently never fire. The container sees the proxy hop instead of the client. A startup WARN flags it. Full impact in `Remote Access / Security > Client IP for login lockout`.
- **External API clients** (cron scripts, homegrown tools, third-party integrations): every `POST` / `PUT` / `DELETE` on `/api/v1/*` now needs an `X-CSRF-Token` header matching the `minuspod_csrf` cookie. The built-in UI handles it; raw curl scripts have to echo the cookie.
- **`/docs` and `/openapi.yaml` bookmarks / health checks:** moved to `/api/v1/docs` and `/api/v1/openapi.yaml`. The old paths return 404.
- **OpenAI-compatible provider relying on the `ANTHROPIC_API_KEY` fallback:** that fallback is gone. Set `OPENAI_API_KEY` explicitly or ad detection 401s. A startup WARN fires when the old shape is detected.

**Quieter changes worth knowing:**

- `SESSION_COOKIE_SAMESITE` now `Strict`. Flip to `Lax` only if a specific cross-site integration breaks.
- Frontend and API must share an origin (`flask-cors` was removed). Put them behind the same reverse proxy.
- Password minimum is now 12 characters. Existing hashes verify fine; the next password change picks up the new minimum.
- Saving provider keys via `PUT /api/v1/settings/ad-detection` returns 409 unless `MINUSPOD_MASTER_PASSPHRASE` is set. Existing plaintext keys keep working; the setting endpoint refuses to write a new secret in cleartext.
- Container runs as UID 1000. First boot chowns the data volume in place. Override with `APP_UID` / `APP_GID` or bypass with `docker run --user <N>` if the host volume belongs to a different UID.

---

[< Docs index](README.md) | [Project README](../README.md)
