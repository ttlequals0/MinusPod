"""System routes: /health, /system/* endpoints."""
import datetime
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
import uuid
from functools import lru_cache
from pathlib import Path

from flask import Response, abort, jsonify, request, send_file

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, _get_version, _start_time,
)
import transcriber
from config import WHISPER_BACKEND_API, resolve_whisper_device
from database.settings import registry_default
from podping_listener import (
    get_node_health_summary, DEGRADED_SETTING, DEGRADED_SINCE_SETTING,
    get_node_check_status, MONITOR_HEARTBEAT_SETTING, NODE_CHECK_SETTING,
)
from pricing_fetcher import force_refresh_pricing
from secrets_crypto import (
    count_plaintext_secrets,
    is_available as crypto_available,
    encrypt_backup_file,
)
from db_backup_service import backup_now, BackupInProgressError
from utils.http import safe_url_for_log
from utils.time import parse_iso_utc, utc_now, utc_now_iso

logger = logging.getLogger('podcast.api')


def _server_start_time() -> float:
    """Shared start time so both workers report one uptime, respawns included."""
    try:
        from status_service import StatusService
        shared = StatusService().get_server_start_time()
    except Exception:
        shared = None
    return shared if shared is not None else _start_time


# Repo root (same file layout as main_app.routes.ROOT_DIR): parents[2]
# resolves /app from /app/src/api/system.py on the shipped image, and
# the equivalent checkout root in dev.
_ROOT_DIR = Path(__file__).resolve().parents[2]


def _effective_whisper_config(db):
    """Transcription config actually in use: DB settings win over env, as in GET /settings."""
    def setting(key):
        return db.get_setting(key) or registry_default(key)

    backend = setting('whisper_backend')
    if backend == WHISPER_BACKEND_API:
        base_url = setting('whisper_api_base_url')
        return {
            'whisperBackend': backend,
            'whisperModel': setting('whisper_api_model'),
            # No local device is involved; reporting cuda here would imply GPU work that never runs.
            'whisperDevice': 'remote',
            # scheme://host only: the configured URL can carry credentials or a token.
            'whisperApiHost': safe_url_for_log(base_url) if base_url else None,
        }
    return {
        'whisperBackend': backend,
        'whisperModel': setting('whisper_model'),
        # Effective device, not the raw env value: an unrecognized setting
        # transcribes on CPU, and the UI should say so (#605).
        'whisperDevice': resolve_whisper_device(),
        'whisperApiHost': None,
    }


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

    # Visible but never gates readiness: an instance with no model
    # configured still serves already-processed feeds.
    try:
        checks['llm_model_configured'] = bool(db.get_setting('claude_model'))
    except Exception:
        checks['llm_model_configured'] = False

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
        'uptime': int(time.time() - _server_start_time()),
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
            **_effective_whisper_config(db),
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
        },
        'database': db.sqlite_diagnostics(),
        # Informational only, never gates readiness (see /health above): a
        # shared refresh outage degrades feed freshness, not the process.
        'feedRefresh': {
            'lastSuccessfulRefreshAt': db.get_feeds_last_successful_refresh_at(),
            'outageDegraded': db.get_setting('feeds_refresh_outage_active') == '1',
            'outageAffectedCount': int(db.get_setting('feeds_refresh_outage_affected_count') or 0),
            'nextRetryAt': db.get_setting('feeds_next_refresh_retry_at') or None,
        },
        # Informational only, never gates readiness (see /health above): the
        # RPC listener is one input path among several (RSS polling remains
        # the fallback), so an all-nodes-down Podping outage degrades ping
        # timeliness, not the process.
        'podping': {
            'listenerEnabled': db.get_setting_bool('podping_enabled', False),
            'allNodesDown': db.get_setting(DEGRADED_SETTING) == '1',
            'degradedSince': db.get_setting(DEGRADED_SINCE_SETTING) or None,
            'nodes': get_node_health_summary(db),
            'check': get_node_check_status(db),
        },
        # Informational only, never gates readiness (see /health above): a
        # GPU-OOM exhaustion on the local Whisper backend degrades
        # transcription, not the process (episodes re-queue as transient).
        # Reports the configured backend, since the local reading says
        # nothing when transcription runs on a remote API.
        'transcriber': transcriber.get_transcriber_health(),
    })


@api.route('/system/podping/check', methods=['POST'])
@log_request
def check_podping_nodes():
    """Request one leader-owned health check of every Podping node."""
    db = get_database()
    heartbeat_raw = db.get_setting(MONITOR_HEARTBEAT_SETTING)
    try:
        heartbeat_data = json.loads(heartbeat_raw) if heartbeat_raw else {}
    except (TypeError, ValueError):
        heartbeat_data = {}
    heartbeat = parse_iso_utc(
        heartbeat_data.get('observedAt') if isinstance(heartbeat_data, dict)
        else None)
    if (heartbeat is None
            or (utc_now() - heartbeat).total_seconds() > 45):
        return error_response('Podping monitor is not available', 503)

    created = False

    def create_or_reuse(raw):
        nonlocal created
        try:
            current = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            current = {}
        if isinstance(current, dict) and current.get('status') in {'pending', 'running'}:
            return json.dumps(current)
        created = True
        return json.dumps({
            'checkId': uuid.uuid4().hex,
            'status': 'pending',
            'requestedAt': utc_now_iso(),
            'startedAt': None,
            'completedAt': None,
        })

    db.merge_setting(NODE_CHECK_SETTING, create_or_reuse)
    return json_response({'accepted': created, **get_node_check_status(db)}, 202)


@api.route('/system/database/checkpoint', methods=['POST'])
@limiter.limit("6 per hour")
@log_request
def checkpoint_database():
    """Run a passive WAL checkpoint."""
    result = get_database().checkpoint_wal()
    logger.info(
        "SQLite checkpoint: busy=%s log_pages=%s checkpointed_pages=%s duration_ms=%s",
        result['busy'], result['logPages'], result['checkpointedPages'], result['durationMs'])
    return json_response(result, 409 if result['busy'] else 200)


@api.route('/system/updates', methods=['GET'])
@limiter.limit('60 per hour')
@log_request
def get_system_updates():
    """Latest stable/edge release info from GitHub, cached 6h in-process."""
    import update_checker
    db = get_database()
    force = request.args.get('refresh') == 'true'
    try:
        return json_response(update_checker.get_update_status(db, force=force))
    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return error_response('Update check failed; GitHub may be unreachable', 502)


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
        'deferred': db.count_deferred_episodes(),
        'total': queue_stats.get('total', 0),
        'items': [
            {
                'id': item['id'],
                'episodeId': item['episode_id'],
                'podcastSlug': item['podcast_slug'],
                'status': item['status'],
                'priority': item['priority'],
                'createdAt': item['created_at'],
            }
            for item in queue_stats.get('items', [])
        ],
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

    encrypt_requested = request.args.get('encrypted', 'true').lower() != 'false'
    passphrase = os.environ.get('MINUSPOD_MASTER_PASSPHRASE')
    if encrypt_requested and not passphrase:
        logger.warning(
            "Encrypted database backup refused: encryption is unavailable ip=%s",
            request.remote_addr,
        )
        return error_response('backup_encryption_unavailable', 409)

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

        encrypted_on_disk = False
        if encrypt_requested:
            try:
                encrypted_path = f'{tmp_path}.enc'
                try:
                    encrypt_backup_file(
                        tmp_path, encrypted_path,
                        passphrase,
                    )
                    os.replace(encrypted_path, tmp_path)
                finally:
                    Path(encrypted_path).unlink(missing_ok=True)
                filename = f"minuspod-backup-{timestamp}.db.enc"
                encrypted_on_disk = True
                logger.info(
                    "Database backup encrypted: %s -> %s bytes (AES-GCM)",
                    backup_size, os.path.getsize(tmp_path),
                )
            except Exception:
                logger.exception("Backup encryption failed; aborting download")
                return error_response('Backup encryption failed', 500)
        else:
            filename = f"minuspod-backup-{timestamp}.db"
            logger.warning(
                "Plaintext database backup explicitly requested: ip=%s",
                request.remote_addr,
            )

        # WARN-level audit log so backup downloads are visible in
        # operator dashboards filtering WARN-and-above. Records the
        # caller IP and whether the download was AES-GCM-wrapped.
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
    except Exception:
        logger.exception("Database backup failed")
        return error_response('Backup failed', 500)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@api.route('/system/config-export', methods=['GET'])
@limiter.limit("6 per hour")
@log_request
def export_config():
    """Download instance settings and feed configuration as redacted JSON."""
    import platform
    from urllib.parse import urlsplit

    from api.feeds import get_feeds_export_list
    from api.settings import _build_settings_payload
    from utils.config_export import build_domain_identity, redact_config
    from utils.gpu import get_gpu_device_name
    from webhook_service import load_webhooks

    base_host = (urlsplit(os.environ.get('BASE_URL', 'http://localhost:8000')).hostname or '').lower()
    instance_hosts = frozenset({h for h in (base_host, 'localhost') if h})
    domain_identity = build_domain_identity(base_host)

    db = get_database()
    whisper = _effective_whisper_config(db)
    document = {
        'settings': _build_settings_payload(),
        'feeds': get_feeds_export_list(db),
        'webhooks': load_webhooks(db),
        'system': {
            'version': _get_version(),
            'exportedAt': utc_now_iso(),
            'whisperBackend': whisper['whisperBackend'],
            'whisperModel': whisper['whisperModel'],
            'whisperDevice': whisper['whisperDevice'],
            'gpuName': get_gpu_device_name(),
            'platform': platform.machine(),
        },
    }
    redacted = redact_config(document, instance_hosts=instance_hosts, domain_identity=domain_identity)

    timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = f"minuspod-config-{timestamp}.json"
    body = json.dumps(redacted, indent=2, sort_keys=True)

    logger.warning(
        "Configuration export downloaded: feeds=%d ip=%s",
        len(redacted.get('feeds', [])), request.remote_addr,
    )

    response = Response(body, mimetype='application/json')
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    return response


@api.route('/system/db-backup/run', methods=['POST'])
@limiter.limit('6/hour')
@log_request
def run_db_backup():
    """Force a scheduled-style DB backup now, ignoring the enabled flag.

    Distinct from GET /system/backup (which streams a one-off download);
    this writes to the configured backup destination with rotation.
    """
    db = get_database()
    try:
        summary = backup_now(db)
    except BackupInProgressError:
        return error_response('a backup is already in progress', 409)
    except Exception as e:
        # backup_now already stamped db_backup_last_error (surfaced via GET);
        # keep the client body a flat Error-schema string with no raw str(e).
        return error_response('Backup failed', 500, details=str(e))
    return json_response(summary)


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
        from utils.app_version import APP_VERSION
        content = _render_openapi_yaml(str(openapi_path), APP_VERSION)
        return Response(content, mimetype='application/x-yaml')
    except Exception:
        return send_file(openapi_path, mimetype='application/x-yaml')
