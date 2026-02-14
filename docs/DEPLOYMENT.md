# Deployment Runbook

## Prerequisites

- Docker with NVIDIA runtime (for GPU acceleration)
- 8 GB RAM minimum (16 GB+ for audio analysis)
- CUDA-capable GPU (optional but recommended)
- Anthropic API key

## Quick Start

```bash
# Clone repository
git clone https://github.com/ttlequals0/minuspod.git
cd minuspod

# Create .env file
echo "ANTHROPIC_API_KEY=your-key-here" > .env

# Start container
docker-compose up -d

# View logs
docker logs -f minuspod
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | Yes | - | Claude API key for ad detection |
| `BASE_URL` | No | http://localhost:8000 | Public URL for RSS feeds |
| `WHISPER_MODEL` | No | small | Whisper model size (tiny/small/medium/large-v3) |
| `WHISPER_DEVICE` | No | cuda | Device for Whisper (cuda/cpu) |
| `RETENTION_PERIOD` | No | 1440 | Minutes to keep processed episodes |
| `LOG_FORMAT` | No | text | Log format (text/json) |
| `LOG_LEVEL` | No | INFO | Log level (DEBUG/INFO/WARNING/ERROR) |
| `SECRET_KEY` | No | auto-generated | Flask secret key for sessions |
| `SESSION_LIFETIME_HOURS` | No | 24 | Session expiry in hours |
| `SESSION_COOKIE_SECURE` | No | false | Require HTTPS for cookies |

## Health Monitoring

```bash
# Check health
curl http://localhost:8000/api/v1/health

# Expected response
{
    "status": "healthy",
    "checks": {
        "database": true,
        "storage": true,
        "queue_available": true
    }
}
```

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
2. Disable audio analysis in Settings
3. Increase container memory limit
4. For long episodes (>2 hours), expect 16GB+ RAM usage

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

### Automatic Backups

Database is backed up automatically every 24 hours to `data/backups/`.
Last 7 backups are retained.

### Manual Backup

```bash
# Stop container
docker-compose stop

# Backup data directory
tar -czvf podcast-backup-$(date +%Y%m%d).tar.gz data/

# Restart
docker-compose start
```

### Restore from Backup

```bash
# Stop container
docker-compose stop

# Restore database
cp data/backups/podcast_YYYYMMDD_HHMMSS.db data/podcast.db

# Restart
docker-compose start
```

## Updating

```bash
# Pull latest image
docker pull ttlequals0/minuspod:latest

# Recreate container
docker-compose up -d

# Database migrations run automatically on startup
```

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
| Speaker diarization | Medium | 4 GB | 2 GB |

## Cloudflare Tunnel (Optional)

For remote access without port forwarding:

```bash
# Set tunnel token in .env
TUNNEL_TOKEN=your-cloudflare-tunnel-token

# Start with tunnel profile
docker-compose --profile tunnel up -d
```

## Security Notes

- Set a password in Settings if exposing publicly
- Use `SESSION_COOKIE_SECURE=true` with HTTPS
- RSS feed URLs include slugs but no auth (by design for podcast apps)
- Consider Cloudflare Tunnel or VPN for remote access
