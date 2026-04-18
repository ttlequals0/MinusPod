"""System routes: /health, /system/* endpoints."""
import datetime
import logging
import os
import re
import sqlite3
import tempfile
import time
from functools import lru_cache
from pathlib import Path

from flask import Response, abort, jsonify, request, send_file

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, _get_version, _start_time,
)
from pricing_fetcher import force_refresh_pricing
from secrets_crypto import (
    count_plaintext_secrets,
    is_available as crypto_available,
    encrypt_bytes as _encrypt_bytes,
)

logger = logging.getLogger('podcast.api')

# Repo root (same file layout as main_app.routes.ROOT_DIR): parents[2]
# resolves /app from /app/src/api/system.py on the shipped image, and
# the equivalent checkout root in dev.
_ROOT_DIR = Path(__file__).resolve().parents[2]


# ========== System Endpoints ==========

@api.route('/health/live', methods=['GET'])
def health_live():
    """Liveness probe: answers 200 if the process is running. No side effects.

    Safe for per-second polling by Kubernetes-style liveness probes and for
    health checks on shared hosts where the full readiness check is too
    heavy. Does not require authentication.
    """
    return jsonify({'status': 'ok'}), 200


@api.route('/health', methods=['GET'])
def health_check():
    """Readiness probe: verifies DB and storage are reachable.

    Returns 200 if healthy, 503 if unhealthy. Does not require authentication.
    Dropped the ProcessingQueue instantiation that previously ran on every
    call; a busy queue is not an ill-health signal, and the construction
    opened a file lock that showed up in profiles. Existing Docker / Portainer
    healthchecks that poll /health continue to receive the same 200/503 shape.
    """
    db = get_database()
    storage = get_storage()

    checks = {}

    try:
        conn = db.get_connection()
        conn.execute('SELECT 1')
        checks['database'] = True
    except Exception:
        checks['database'] = False

    try:
        storage_path = storage.data_dir
        checks['storage'] = os.access(storage_path, os.W_OK)
    except Exception:
        checks['storage'] = False

    status = 'healthy' if all(checks.values()) else 'unhealthy'

    return jsonify({
        'status': status,
        'checks': checks,
        'version': _get_version()
    }), 200 if status == 'healthy' else 503


@api.route('/system/status', methods=['GET'])
@log_request
def get_system_status():
    """Get system status and statistics."""
    db = get_database()
    storage = get_storage()

    stats = db.get_stats()
    storage_stats = storage.get_storage_stats()

    retention_days = int(db.get_setting('retention_days') or '30')
    plaintext_secrets = count_plaintext_secrets(db)

    return json_response({
        'status': 'running',
        'version': _get_version(),
        'uptime': int(time.time() - _start_time),
        'feeds': {
            'total': stats['podcast_count']
        },
        'episodes': {
            'total': stats['episode_count'],
            'byStatus': stats['episodes_by_status']
        },
        'storage': {
            'usedMb': storage_stats['total_size_mb'],
            'fileCount': storage_stats['file_count']
        },
        'settings': {
            'retentionDays': retention_days,
            'whisperModel': os.environ.get('WHISPER_MODEL', 'small'),
            'whisperDevice': os.environ.get('WHISPER_DEVICE', 'cuda'),
            'baseUrl': os.environ.get('BASE_URL', 'http://localhost:8000')
        },
        'stats': {
            'totalTimeSaved': db.get_total_time_saved(),
            'totalInputTokens': int(db.get_stat('total_input_tokens')),
            'totalOutputTokens': int(db.get_stat('total_output_tokens')),
            'totalLlmCost': round(db.get_stat('total_llm_cost'), 2),
        },
        'security': {
            'cryptoReady': crypto_available(),
            'plaintextSecretsCount': plaintext_secrets,
        }
    })


@api.route('/system/token-usage', methods=['GET'])
@log_request
def get_token_usage():
    """Get LLM token usage summary with per-model breakdown."""
    db = get_database()
    return json_response(db.get_token_usage_summary())


@api.route('/system/model-pricing', methods=['GET'])
@log_request
def get_model_pricing():
    """Get known model pricing rates, optionally filtered by source."""
    db = get_database()
    source = request.args.get('source')
    return json_response({'models': db.get_model_pricing(source=source)})


@api.route('/system/model-pricing/refresh', methods=['POST'])
@limiter.limit("6 per hour")
@log_request
def refresh_model_pricing():
    """Force refresh pricing data from provider's pricing source."""
    try:
        force_refresh_pricing()
        db = get_database()
        pricing = db.get_model_pricing()
        return json_response({
            'status': 'ok',
            'modelsUpdated': len(pricing),
        })
    except Exception as e:
        logger.error(f"Manual pricing refresh failed: {e}")
        return error_response('Pricing refresh failed, check server logs', 502)


@api.route('/system/cleanup', methods=['POST'])
@limiter.limit("1 per hour")
@log_request
def trigger_cleanup():
    """Reset ALL processed episodes to discovered (ignores retention period).

    Rate-limited to one invocation per hour and audit-logged at WARN so the
    destructive reset shows up in operator dashboards even when the request
    completes successfully. The API-only threat model assumes deliberate
    intent; the limit is a brake on runaway scripts rather than on people.
    """
    db = get_database()
    storage = get_storage()

    logger.warning(
        "Destructive cleanup triggered: all episodes will be reset to discovered ip=%s",
        request.remote_addr,
    )
    reset_count, freed_mb = db.cleanup_old_episodes(force_all=True, storage=storage)

    logger.warning(
        "Destructive cleanup complete: %d episodes reset, %.1f MB freed ip=%s",
        reset_count, freed_mb, request.remote_addr,
    )
    return json_response({
        'message': 'All episodes reset to discovered',
        'episodesRemoved': reset_count,
        'spaceFreedMb': round(freed_mb, 2)
    })


@api.route('/system/vacuum', methods=['POST'])
@limiter.limit("1 per hour")
@log_request
def trigger_vacuum():
    """Trigger SQLite VACUUM to reclaim disk space."""
    db = get_database()
    logger.info("Starting VACUUM...")
    duration_ms = db.vacuum()

    return json_response({
        'status': 'ok',
        'message': 'VACUUM complete',
        'durationMs': duration_ms,
    })


@api.route('/system/queue', methods=['GET'])
@log_request
def get_queue_status():
    """Get auto-process queue status."""
    db = get_database()
    queue_stats = db.get_queue_status()

    return json_response({
        'pending': queue_stats.get('pending', 0),
        'processing': queue_stats.get('processing', 0),
        'completed': queue_stats.get('completed', 0),
        'failed': queue_stats.get('failed', 0),
        'total': queue_stats.get('total', 0)
    })


@api.route('/system/queue', methods=['DELETE'])
@limiter.limit("6 per hour")
@log_request
def clear_queue():
    """Clear all pending items from the auto-process queue."""
    db = get_database()
    deleted = db.clear_pending_queue_items()
    logger.warning(
        "Auto-process queue cleared: %d pending items removed ip=%s",
        deleted, request.remote_addr,
    )
    return json_response({
        'message': f'Cleared {deleted} pending items from queue',
        'deleted': deleted
    })


@api.route('/system/backup', methods=['GET'])
@limiter.limit("6 per hour")
@log_request
def backup_database():
    """Create and download a backup of the SQLite database."""
    from flask import after_this_request

    db = get_database()
    tmp_path = None
    try:
        # Create a temp file for the backup
        tmp_file = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()

        # Use SQLite backup API with the app's existing connection for consistency
        src_conn = db.get_connection()
        dst_conn = sqlite3.connect(tmp_path)
        src_conn.backup(dst_conn)
        dst_conn.close()

        backup_size = os.path.getsize(tmp_path)
        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

        # If MINUSPOD_MASTER_PASSPHRASE is set, encrypt the backup so an
        # exported copy doesn't leak plaintext provider secrets that
        # predate the 2.0 crypto migration. Operators can opt out with
        # ?encrypted=false when they have another protection layer (e.g.
        # per-download GPG wrap).
        encrypt_param = request.args.get('encrypted', 'true').lower() != 'false'
        if encrypt_param and crypto_available():
            try:
                with open(tmp_path, 'rb') as f:
                    blob = f.read()
                enc_blob = _encrypt_bytes(get_database(), blob)
                with open(tmp_path, 'wb') as f:
                    f.write(enc_blob)
                filename = f"minuspod-backup-{timestamp}.db.enc"
                logger.info(
                    "Database backup encrypted: %s -> %s bytes (AES-GCM)",
                    backup_size, len(enc_blob),
                )
            except Exception:
                logger.exception("Backup encryption failed; aborting download")
                return error_response('Backup encryption failed', 500)
        else:
            filename = f"minuspod-backup-{timestamp}.db"
            if encrypt_param and not crypto_available():
                logger.warning(
                    "Database backup downloaded UNENCRYPTED: "
                    "set MINUSPOD_MASTER_PASSPHRASE to enable AES-GCM wrap"
                )

        # WARN-level audit log so backup downloads are visible in
        # operator dashboards filtering WARN-and-above. Records the
        # caller IP and whether the download was AES-GCM-wrapped.
        encrypted_on_disk = encrypt_param and crypto_available()
        logger.warning(
            "Database backup downloaded: size=%d bytes ip=%s encrypted=%s",
            backup_size,
            request.remote_addr,
            encrypted_on_disk,
        )

        # Clean up temp file after response is sent (stream from disk, not memory)
        cleanup_path = tmp_path
        tmp_path = None  # prevent finally block from deleting before send

        @after_this_request
        def _cleanup(response):
            try:
                os.unlink(cleanup_path)
            except OSError:
                pass
            return response

        return send_file(
            cleanup_path,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        logger.exception("Database backup failed")
        return error_response('Backup failed', 500)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ========== API Documentation ==========
#
# Registered on the blueprint so the same ``check_auth`` gate that guards
# every other /api/v1/* route applies. The route lived at the app level
# previously, which skipped the gate -- anyone inside the trust boundary
# could read the OpenAPI spec without logging in.

# All scripts are served as static assets (no inline <script> blocks)
# so the ``script-src 'self'`` CSP applies without an `unsafe-inline`
# exception. `/ui/swagger-init.js` ships from the frontend bundle
# (see `frontend/public/swagger-init.js`).
_SWAGGER_HTML = '''<!DOCTYPE html>
<html>
<head>
    <title>MinusPod API</title>
    <link rel="stylesheet" type="text/css" href="/ui/swagger/swagger-ui.css">
</head>
<body>
    <div id="swagger-ui"></div>
    <script src="/ui/swagger/swagger-ui-bundle.js"></script>
    <script src="/ui/swagger-init.js"></script>
</body>
</html>'''


@api.route('/docs', methods=['GET'])
@api.route('/docs/', methods=['GET'])
def swagger_ui():
    """Serve Swagger UI for API documentation (assets bundled locally)."""
    return _SWAGGER_HTML


@lru_cache(maxsize=1)
def _render_openapi_yaml(openapi_path_str: str, version: str) -> str:
    """Cache the version-substituted OpenAPI document for the lifetime of
    the worker. Both key components are stable within a process, so the
    cache invalidates naturally on container restart (when a version
    bump or file change takes effect).
    """
    content = Path(openapi_path_str).read_text()
    return re.sub(
        r'^(\s*version:\s*).*$',
        rf'\g<1>{version}',
        content,
        count=1,
        flags=re.MULTILINE,
    )


@api.route('/openapi.yaml', methods=['GET'])
def serve_openapi():
    """Serve OpenAPI specification with dynamic version."""
    openapi_path = _ROOT_DIR / 'openapi.yaml'
    if not openapi_path.exists():
        abort(404)
    try:
        from version import __version__
        content = _render_openapi_yaml(str(openapi_path), __version__)
        return Response(content, mimetype='application/x-yaml')
    except Exception:
        return send_file(openapi_path, mimetype='application/x-yaml')
