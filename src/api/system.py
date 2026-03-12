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

logger = logging.getLogger('podcast.api')


# ========== System Endpoints ==========

@api.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring.

    Returns 200 if healthy, 503 if unhealthy.
    Does not require authentication.
    """
    db = get_database()
    storage = get_storage()

    checks = {}

    # Database check
    try:
        conn = db.get_connection()
        conn.execute('SELECT 1')
        checks['database'] = True
    except Exception:
        checks['database'] = False

    # Storage check - verify data directory is writable
    try:
        storage_path = storage.data_dir
        checks['storage'] = os.access(storage_path, os.W_OK)
    except Exception:
        checks['storage'] = False

    # Processing queue check
    try:
        from processing_queue import ProcessingQueue
        queue = ProcessingQueue()
        checks['queue_available'] = not queue.is_busy()
    except Exception:
        checks['queue_available'] = False

    # Determine overall status - database and storage are critical
    critical_checks = [checks['database'], checks['storage']]
    status = 'healthy' if all(critical_checks) else 'unhealthy'

    response_data = {
        'status': status,
        'checks': checks,
        'version': _get_version()
    }

    return jsonify(response_data), 200 if status == 'healthy' else 503


@api.route('/system/status', methods=['GET'])
@log_request
def get_system_status():
    """Get system status and statistics."""
    db = get_database()
    storage = get_storage()

    stats = db.get_stats()
    storage_stats = storage.get_storage_stats()

    retention_days = int(db.get_setting('retention_days') or '30')

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
    """Get all known model pricing rates."""
    db = get_database()
    return json_response({'models': db.get_model_pricing()})


@api.route('/system/cleanup', methods=['POST'])
@log_request
def trigger_cleanup():
    """Reset ALL processed episodes to discovered (ignores retention period)."""
    db = get_database()
    storage = get_storage()

    reset_count, freed_mb = db.cleanup_old_episodes(force_all=True, storage=storage)

    logger.info(f"Manual cleanup: {reset_count} episodes reset, {freed_mb:.1f} MB freed")
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
@log_request
def clear_queue():
    """Clear all pending items from the auto-process queue."""
    db = get_database()
    deleted = db.clear_pending_queue_items()
    logger.info(f"Cleared {deleted} pending items from auto-process queue")
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
        logger.error(f"Database backup failed: {e}")
        return error_response(f"Backup failed: {e}", 500)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
