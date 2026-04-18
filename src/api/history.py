"""History routes: /history/* endpoints."""
import csv
import io
import json
import logging
import math

from flask import request, Response

from api import (
    api, limiter, log_request, json_response,
    get_database,
)

logger = logging.getLogger('podcast.api')


# ========== Processing History Endpoints ==========

@api.route('/history', methods=['GET'])
@log_request
def get_processing_history():
    """Get processing history with pagination and filtering."""
    db = get_database()

    # Parse query params
    limit = min(max(1, request.args.get('limit', 50, type=int)), 100)
    explicit_page = request.args.get('page', None, type=int)
    if explicit_page is not None:
        page = max(1, explicit_page)
        offset = (page - 1) * limit
    else:
        offset = max(0, request.args.get('offset', 0, type=int))
        page = (offset // limit) + 1
    status_filter = request.args.get('status')  # 'completed' or 'failed'
    podcast_slug = request.args.get('podcast_slug')
    sort_by = request.args.get('sort_by', 'processed_at')
    sort_dir = request.args.get('sort_dir', 'desc')

    entries, total_count = db.get_processing_history(
        limit=limit,
        offset=offset,
        status_filter=status_filter,
        podcast_slug=podcast_slug,
        sort_by=sort_by,
        sort_dir=sort_dir
    )

    # Transform for API response
    history = []
    for entry in entries:
        history.append({
            'id': entry['id'],
            'podcastSlug': entry['podcast_slug'],
            'podcastTitle': entry['podcast_title'],
            'episodeId': entry['episode_id'],
            'episodeTitle': entry['episode_title'],
            'processedAt': entry['processed_at'],
            'processingDurationSeconds': entry['processing_duration_seconds'],
            'status': entry['status'],
            'adsDetected': entry['ads_detected'],
            'errorMessage': entry['error_message'],
            'reprocessNumber': entry['reprocess_number'],
            'inputTokens': entry.get('input_tokens', 0) or 0,
            'outputTokens': entry.get('output_tokens', 0) or 0,
            'llmCost': round(entry.get('llm_cost', 0.0) or 0.0, 6),
        })

    return json_response({
        'history': history,
        'total': total_count,
        'totalPages': math.ceil(total_count / limit) if total_count > 0 else 1,
        'page': page,
        'limit': limit,
        'offset': offset
    })


@api.route('/history/stats', methods=['GET'])
@log_request
def get_processing_history_stats():
    """Get aggregate statistics from processing history."""
    db = get_database()
    stats = db.get_processing_history_stats()

    return json_response({
        'totalProcessed': stats['total_processed'],
        'completedCount': stats['completed_count'],
        'failedCount': stats['failed_count'],
        'avgProcessingTimeSeconds': stats['avg_processing_time_seconds'],
        'totalAdsDetected': stats['total_ads_detected'],
        'reprocessCount': stats['reprocess_count'],
        'uniqueEpisodes': stats['unique_episodes'],
        'totalInputTokens': stats.get('total_input_tokens', 0),
        'totalOutputTokens': stats.get('total_output_tokens', 0),
        'totalLlmCost': stats.get('total_llm_cost', 0.0),
    })


_EXPORT_CSV_FIELDS = [
    'id', 'podcast_slug', 'podcast_title', 'episode_id',
    'episode_title', 'processed_at', 'processing_duration_seconds',
    'status', 'ads_detected', 'error_message', 'reprocess_number',
    'input_tokens', 'output_tokens', 'llm_cost',
]


def _row_to_json_entry(entry: dict) -> dict:
    return {
        'id': entry['id'],
        'podcastSlug': entry['podcast_slug'],
        'podcastTitle': entry['podcast_title'],
        'episodeId': entry['episode_id'],
        'episodeTitle': entry['episode_title'],
        'processedAt': entry['processed_at'],
        'processingDurationSeconds': entry['processing_duration_seconds'],
        'status': entry['status'],
        'adsDetected': entry['ads_detected'],
        'errorMessage': entry['error_message'],
        'reprocessNumber': entry['reprocess_number'],
        'inputTokens': entry.get('input_tokens', 0) or 0,
        'outputTokens': entry.get('output_tokens', 0) or 0,
        'llmCost': round(entry.get('llm_cost', 0.0) or 0.0, 6),
    }


@api.route('/history/export', methods=['GET'])
@limiter.limit("5 per hour")
@log_request
def export_processing_history():
    """Export processing history as CSV or JSON.

    Streams rows directly from the SQLite cursor so the worker's memory
    footprint stays flat regardless of row count. Kept rate-limited to
    bound cursor wall-time under concurrent exports.
    """
    db = get_database()

    export_format = request.args.get('format', 'json').lower()
    status_filter = request.args.get('status')
    podcast_slug = request.args.get('podcast_slug')

    rows = db.iter_processing_history_rows(
        status_filter=status_filter,
        podcast_slug=podcast_slug,
    )

    if export_format == 'csv':
        def generate_csv():
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=_EXPORT_CSV_FIELDS, extrasaction='ignore')
            writer.writeheader()
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
            for row in rows:
                writer.writerow(row)
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()

        return Response(
            generate_csv(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=processing_history.csv'},
        )

    def generate_json():
        yield '{"history":['
        first = True
        for row in rows:
            sep = '' if first else ','
            first = False
            yield sep + json.dumps(_row_to_json_entry(row))
        yield ']}'

    return Response(
        generate_json(),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=processing_history.json'},
    )
