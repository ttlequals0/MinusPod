"""System routes: /health, /system/* endpoints."""
import datetime
import logging
import os
import sqlite3
import tempfile
import time

from flask import jsonify, request, send_file

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, _get_version, _start_time,
)
from pricing_fetcher import force_refresh_pricing
from secrets_crypto import count_plaintext_secrets, is_available as crypto_available

logger = logging.getLogger('podcast.api')


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
        logger.info(f"Database backup created: {backup_size} bytes")

        timestamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        filename = f"minuspod-backup-{timestamp}.db"

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
