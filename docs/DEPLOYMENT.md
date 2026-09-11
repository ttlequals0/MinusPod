# Deployment Runbook

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [Prerequisites](#prerequisites)
- [Minimum production environment](#minimum-production-environment)
- [Health monitoring](#health-monitoring)
- [Common issues](#common-issues)
- [Backup and recovery](#backup-and-recovery)
- [Updating](#updating)
- [Logs](#logs)
- [Resource usage](#resource-usage)
- [Cloudflare tunnel](#cloudflare-tunnel-optional)
- [Security notes](#security-notes)

This page covers running MinusPod in production: health monitoring, backups, updates, and the common operational issues. For first-time install see [Installation](installation.md); for the complete environment variable reference see [Environment Variables](environment-variables.md).

## Prerequisites

- Docker (NVIDIA runtime for GPU image; not required for the CPU image)
- 8 GB RAM minimum; 16 GB+ recommended for `medium` / `large-v3` Whisper or long episodes
- CUDA-capable GPU with NVIDIA driver 525 or newer (GPU image only; CPU image runs without one). The image does not gate on driver version at startup; an older driver surfaces as a CUDA init error on the first transcription. PyTorch ships a CUDA 12.9 build covering Turing (sm_75) through Blackwell (sm_120). Older cards (Maxwell, Pascal, Volta) still transcribe, since CTranslate2 is compiled separately, but PyTorch prints an unsupported-architecture warning and VRAM-based chunk sizing may fall back to defaults.
- An LLM API key (Anthropic, OpenRouter, OpenAI-compatible, or an Ollama instance)

The GPU image is `ttlequals0/minuspod:<version>` and `:latest`. The CPU image is `ttlequals0/minuspod:<version>-cpu` and `:cpu`. See [Installation](installation.md) for variant selection.

## Minimum production environment

The full reference is in [Environment Variables](environment-variables.md). The three worth setting on day one:

| Variable | Why |
|----------|-----|
| `ANTHROPIC_API_KEY` (or other provider key) | Required for ad detection |
| `BASE_URL` | Public URL embedded in generated RSS feeds |
| `MINUSPOD_MASTER_PASSPHRASE` | Encrypts provider keys and downloadable backups at rest. Store it separately from the backup. |

If you are behind a reverse proxy or Cloudflare tunnel, also set `MINUSPOD_TRUSTED_PROXY_COUNT=1` (or higher for multi-hop chains) so login lockout and per-IP rate limits key on the real client IP.

## Health monitoring

```bash
# Check health (no auth required)
curl http://localhost:8000/api/v1/health

# Expected response
{
    "status": "healthy",
    "checks": {
        "database": true,
        "storage": true
    },
    "version": "2.29.1"
}
```

A non-200 response or `"status": "degraded"` means one of the checks failed; inspect the container logs to find which.

## Common issues

### Episode stuck in processing

```bash
# Check current processing status
curl http://localhost:8000/api/v1/status

# Cancel stuck episode
curl -X POST http://localhost:8000/api/v1/feeds/{slug}/episodes/{id}/cancel

# Or restart container (graceful shutdown will complete current)
docker-compose restart
```

### Out of memory

1. Reduce Whisper model size: `WHISPER_MODEL=small` or `WHISPER_MODEL=tiny`
2. Increase container memory limit
3. For long episodes (>2 hours), expect 16GB+ RAM usage

A worker the kernel OOM-kills mid-run is recovered automatically at the
next restart or maintenance pass, and since 2.94.0 a killed full/LLM
reprocess leaves the episode's previous results intact. Size the
container for the peak, though: transcription of a long episode holds
the audio plus the Whisper model in RAM at once, which is where the
16GB+ figure in point 3 comes from.

### Claude API errors

- **Rate limited** - Built-in exponential backoff, wait 60s
- **Authentication** - Check ANTHROPIC_API_KEY is valid
- **Timeout** - Episode may be too long, try smaller segments

### GPU not detected

```bash
# Check NVIDIA runtime
docker info | grep -i nvidia

# Check GPU visibility in container
docker exec minuspod nvidia-smi
```

If GPU not available, set `WHISPER_DEVICE=cpu` (slower but works).

## Backup and recovery

Scheduled backups are available under Settings > Data & Security and are off by default. They create private, plaintext SQLite snapshots. The API path below creates an encrypted envelope when a master passphrase is configured.

### On-demand SQLite backup (API)

```bash
# Authenticated download via the API. Rate-limited to 6 requests/hour.
curl -sS -b cookies.txt \
  -o minuspod-backup-$(date +%Y%m%d-%H%M%S).db.enc \
  http://localhost:8000/api/v1/system/backup
```

When `MINUSPOD_MASTER_PASSPHRASE` is set, new downloads use the self-contained, authenticated `MPBK02` envelope and require the passphrase that created them. Legacy `MPBK01` downloads also require an intact database from the same instance for its KDF salt. See the authoritative [restore and passphrase rotation procedure](security-and-storage.md#restore-and-passphrase-rotation). Append `?encrypted=false` only when another protection layer covers the plaintext file.

### Manual filesystem backup

```bash
# Stop the container to flush any in-flight writes
docker-compose stop

# Snapshot the data directory (database, processed audio, status file)
tar -czvf minuspod-backup-$(date +%Y%m%d).tar.gz data/

docker-compose start
```

### Restore

Stop the service and prepare a new database artifact without overwriting the current database:

```bash
docker-compose stop
MINUSPOD_MASTER_PASSPHRASE=your-passphrase \
  python scripts/restore_backup.py backup.db.enc data/podcast-restored.db
```

The command verifies SQLite integrity and refuses an existing destination or stale WAL sidecars. Keep the current database until the prepared file passes review. Move the old `podcast.db`, `podcast.db-wal`, and `podcast.db-shm` out of the data directory, then rename the prepared file to `podcast.db` and start the service. Migrations run on startup and support an older snapshot.

## Updating

```bash
# GPU image
docker pull ttlequals0/minuspod:latest
docker-compose up -d

# CPU image
docker pull ttlequals0/minuspod:cpu
docker-compose -f docker-compose.cpu.yml up -d
```

Database migrations run automatically on startup. Take a backup (see above) before pulling a major version.

## Logs

```bash
# View all logs
docker logs minuspod

# Follow logs
docker logs -f minuspod

# Last 100 lines
docker logs --tail 100 minuspod
```

## Resource usage

| Component | CPU | RAM | GPU VRAM |
|-----------|-----|-----|----------|
| Flask API | Low | 100 MB | - |
| Whisper (tiny) | High | 1 GB | 1 GB |
| Whisper (small) | High | 2 GB | 2 GB |
| Whisper (medium) | High | 4 GB | 3 GB |
| Whisper (large-v3) | High | 6 GB | 5 GB |
| Claude API | Low | 100 MB | - |
| Audio processing | High | 500 MB | - |
| Transition detection | Low | 100 MB | - |

## Cloudflare tunnel (optional)

For remote access without port forwarding:

```bash
# .env
TUNNEL_TOKEN=your-cloudflare-tunnel-token
MINUSPOD_TRUSTED_PROXY_COUNT=1   # required for correct client-IP attribution

docker-compose --profile tunnel up -d
```

Without `MINUSPOD_TRUSTED_PROXY_COUNT=1`, login lockout and per-IP rate limits will key on the tunnel sidecar's loopback address instead of the real client. Audit logs and auth-failure webhooks will also carry the wrong IP. Set the same flag when running behind nginx, Traefik, or any other reverse proxy.

## Security notes

- Set `MINUSPOD_MASTER_PASSPHRASE` to encrypt provider API keys at rest. Without it they sit as plaintext in the SQLite DB. See [Security & Storage](security-and-storage.md).
- Compose refuses normal API use until an application password is set. Complete setup from loopback or set a one-time `MINUSPOD_SETUP_TOKEN`, send it as `X-MinusPod-Setup-Token` when setting the first password, then remove it.
- Bind `MINUSPOD_BIND_ADDRESS=127.0.0.1` when a local proxy, VPN, or wrapper is the only intended entry point.
- Compose blocks unauthenticated requests from starting paid processing. Set `MINUSPOD_ALLOW_PUBLIC_PROCESSING=true` only when that behavior is intended, or enable Authenticated Feeds.
- `SESSION_COOKIE_SECURE=auto` follows `BASE_URL`. Set it explicitly only for an unusual proxy setup.
- RSS URLs are public by default. Authenticated Feeds adds an existing global credential or a revocable key scoped to one feed. Treat every credential-bearing URL like a password.
- Cloudflare Tunnel or a VPN is recommended for remote access. Direct port-forwarding works but skips Cloudflare's WAF.
- The compose file runs the container with `no-new-privileges` and `cap_drop: ALL`, then adds back only the capabilities the entrypoint needs to drop root and fix volume ownership (`SETUID`, `SETGID`, `CHOWN`, `DAC_OVERRIDE`, `FOWNER`). Keep the `cap_add` block as-is: removing it leaves a bare `cap_drop: ALL`, which crash-loops the container before gunicorn starts. The same block is mirrored in `docker-compose.cpu.yml`.

---

[< Docs index](README.md) | [Project README](../README.md)
