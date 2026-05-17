# Deployment Runbook

[< Docs index](README.md) | [Project README](../README.md)

---

This page covers running MinusPod in production: health monitoring, backups, updates, and the common operational issues. For first-time install see [Installation](installation.md); for the complete environment variable reference see [Environment Variables](environment-variables.md).

## Prerequisites

- Docker (NVIDIA runtime for GPU image; not required for the CPU image)
- 8 GB RAM minimum; 16 GB+ recommended for `medium` / `large-v3` Whisper or long episodes
- CUDA-capable GPU (GPU image only; CPU image runs without one)
- An LLM API key (Anthropic, OpenRouter, OpenAI-compatible, or an Ollama instance)

The GPU image is `ttlequals0/minuspod:<version>` and `:latest`. The CPU image is `ttlequals0/minuspod:<version>-cpu` and `:cpu`. See [Installation](installation.md) for variant selection.

## Minimum Production Environment

The full reference is in [Environment Variables](environment-variables.md). The four worth setting on day one:

| Variable | Why |
|----------|-----|
| `ANTHROPIC_API_KEY` (or other provider key) | Required for ad detection |
| `BASE_URL` | Public URL embedded in generated RSS feeds |
| `APP_PASSWORD` | Initial UI password; can also be set in Settings > Security |
| `MINUSPOD_MASTER_PASSPHRASE` | Encrypts provider keys at rest. Losing it makes stored keys unrecoverable (env fallback still works). |

If you are behind a reverse proxy or Cloudflare tunnel, also set `MINUSPOD_TRUSTED_PROXY_COUNT=1` (or higher for multi-hop chains) so login lockout and per-IP rate limits key on the real client IP.

## Health Monitoring

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
    "version": "2.4.9"
}
```

A non-200 response or `"status": "degraded"` means one of the checks failed; inspect the container logs to find which.

## Common Issues

### Episode Stuck in Processing

```bash
# Check current processing status
curl http://localhost:8000/api/v1/status

# Cancel stuck episode
curl -X POST http://localhost:8000/api/v1/feeds/{slug}/episodes/{id}/cancel

# Or restart container (graceful shutdown will complete current)
docker-compose restart
```

### Out of Memory

1. Reduce Whisper model size: `WHISPER_MODEL=small` or `WHISPER_MODEL=tiny`
2. Increase container memory limit
3. For long episodes (>2 hours), expect 16GB+ RAM usage

### Claude API Errors

- **Rate limited** - Built-in exponential backoff, wait 60s
- **Authentication** - Check ANTHROPIC_API_KEY is valid
- **Timeout** - Episode may be too long, try smaller segments

### GPU Not Detected

```bash
# Check NVIDIA runtime
docker info | grep -i nvidia

# Check GPU visibility in container
docker exec minuspod nvidia-smi
```

If GPU not available, set `WHISPER_DEVICE=cpu` (slower but works).

## Backup and Recovery

There is no scheduled automatic backup. Use one of the two paths below.

### On-Demand SQLite Backup (API)

```bash
# Authenticated download via the API. Rate-limited to 6 requests/hour.
curl -sS -b cookies.txt \
  -o minuspod-backup-$(date +%Y%m%d-%H%M%S).db.enc \
  http://localhost:8000/api/v1/system/backup
```

When `MINUSPOD_MASTER_PASSPHRASE` is set, the response is AES-GCM encrypted (filename ends `.db.enc`). Restoring it requires the same passphrase that created it; store the passphrase somewhere separate from the backup. Append `?encrypted=false` to download plaintext when you have another protection layer.

### Manual Filesystem Backup

```bash
# Stop the container to flush any in-flight writes
docker-compose stop

# Snapshot the data directory (database, processed audio, status file)
tar -czvf minuspod-backup-$(date +%Y%m%d).tar.gz data/

docker-compose start
```

### Restore

```bash
docker-compose stop

# Replace the database file with your backup
cp <your-backup>.db data/podcast.db

# Or, if restoring an AES-GCM-encrypted backup, decrypt it first using the
# same MINUSPOD_MASTER_PASSPHRASE that created it.

docker-compose start
```

Migrations run on startup and are forward-compatible; restoring an older snapshot into a newer image is supported.

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

## Resource Usage

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

## Cloudflare Tunnel (Optional)

For remote access without port forwarding:

```bash
# .env
TUNNEL_TOKEN=your-cloudflare-tunnel-token
MINUSPOD_TRUSTED_PROXY_COUNT=1   # required for correct client-IP attribution

docker-compose --profile tunnel up -d
```

Without `MINUSPOD_TRUSTED_PROXY_COUNT=1`, login lockout and per-IP rate limits will key on the tunnel sidecar's loopback address instead of the real client. Audit logs and auth-failure webhooks will also carry the wrong IP. Set the same flag when running behind nginx, Traefik, or any other reverse proxy.

## Security Notes

- Set `MINUSPOD_MASTER_PASSPHRASE` to encrypt provider API keys at rest. Without it they sit as plaintext in the SQLite DB. See [Security & Storage](security-and-storage.md).
- Set an `APP_PASSWORD` (or one via Settings > Security) before exposing the UI publicly.
- Use `SESSION_COOKIE_SECURE=true` whenever you serve over HTTPS. Default is `true`; set to `false` only for plain-HTTP localhost development.
- RSS feed URLs contain a slug but no auth, so podcast apps can fetch them. Treat slugs as semi-private.
- Cloudflare Tunnel or a VPN is recommended for remote access. Direct port-forwarding works but skips Cloudflare's WAF.

---

[< Docs index](README.md) | [Project README](../README.md)
