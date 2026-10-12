"""Episode run serialization: stats, run list, spend and coverage shaping."""

import json
from decimal import Decimal
from ad_yield import latest_completed_run
from processing_queue import ProcessingQueue


def upstream_transcript_to_api(payload):
    """Shape the stored upstream transcript diff for the episode detail (or None)."""
    if not isinstance(payload, dict):
        return None
    return {
        'status': payload.get('status'),
        'coverage': payload.get('coverage'),
        'sourceType': payload.get('mime'),
        'spans': [{'start': span.get('start'), 'end': span.get('end'),
                   'offsetConfirmed': bool(span.get('offset_confirmed'))}
                  for span in payload.get('spans') or () if isinstance(span, dict)],
    }


def run_stats_to_api(stats):
    """Rename the pipeline's snake_case stats blob to API casing (or None)."""
    if not stats:
        return None
    stage_hits = stats.get('stage_hits')
    markers = stats.get('markers')
    timings = stats.get('timings')
    result = {
        'mode': stats.get('mode'),
        'detectionSkipped': stats.get('detection_skipped'),
        'verificationSkipped': stats.get('verification_skipped'),
        'cueOnly': stats.get('cue_only'),
        'transcriptionSkipped': stats.get('transcription_skipped'),
        'normalizationSkipped': stats.get('normalization_skipped'),
        'downloadedDuration': stats.get('downloaded_duration'),
        'transcriptSegments': stats.get('transcript_segments'),
        'windows': stats.get('windows'),
        'verificationWindows': stats.get('verification_windows'),
        'stageHits': {
            'fingerprint': stage_hits.get('fingerprint', 0),
            'textPattern': stage_hits.get('text_pattern', 0),
            'differential': stage_hits.get('differential', 0),
            'llm': stage_hits.get('llm', 0),
            'transcriptDifferential': stage_hits.get('transcript_differential', 0),
        } if stage_hits else None,
        'detected': stats.get('detected'),
        'markers': {
            'cut': markers.get('cut', 0),
            'held': markers.get('held', 0),
            'notCut': markers.get('not_cut', 0),
        } if markers else None,
        'verificationAdsCut': stats.get('verification_ads_cut'),
        'secondsRemoved': stats.get('seconds_removed'),
        'sourceSecondsRemoved': stats.get('source_seconds_removed'),
        'replacementSecondsAdded': stats.get('replacement_seconds_added'),
        'timings': {
            'downloadSeconds': timings.get('download'),
            'transcriptionSeconds': timings.get('transcription'),
            'transcriptDiffSeconds': timings.get('transcript_diff'),
            'differentialSeconds': timings.get('differential'),
            'audioAnalysisSeconds': timings.get('audio_analysis'),
            'detectionSeconds': timings.get('detection'),
            'refineValidateSeconds': timings.get('refine_validate'),
            'cutSeconds': timings.get('cut'),
            'verificationSeconds': timings.get('verification'),
            'normalizationSeconds': timings.get('normalization'),
            'assetsSeconds': timings.get('assets'),
            'chaptersSeconds': timings.get('chapters'),
            'finalizeSeconds': timings.get('finalize'),
            'ffmpegSeconds': timings.get('ffmpeg'),
        } if timings is not None else None,
    }
    if 'chapters_degraded' in stats:
        result['chaptersDegraded'] = stats['chapters_degraded']
    if 'chapters_degraded_reason' in stats:
        result['chaptersDegradedReason'] = stats['chapters_degraded_reason']
    transcription = stats.get('transcription')
    if transcription:
        result['transcription'] = {
            'outcome': transcription.get('outcome'),
            'batchSize': transcription.get('batch_size'),
            'retryCount': transcription.get('retry_count'),
            'retrySucceeded': transcription.get('retry_succeeded'),
            'device': transcription.get('device'),
            'gpuDeviceName': transcription.get('gpu_device_name'),
            'model': transcription.get('model'),
            'error': transcription.get('error'),
        }
    transcript_diff = stats.get('transcript_diff')
    if isinstance(transcript_diff, dict):
        result['transcriptDiff'] = {
            'status': transcript_diff.get('status'),
            'coverage': transcript_diff.get('coverage'),
            'spans': transcript_diff.get('spans', 0),
        }
    notices = stats.get('thinking_notices')
    if notices:
        result['thinkingNotices'] = [{
            'pass': notice.get('pass'),
            'provider': notice.get('provider'),
            'model': notice.get('model'),
            'requested': notice.get('requested'),
            'compatibility': notice.get('compatibility'),
            'fallback': {
                'maxTokens': (notice.get('fallback') or {}).get('max_tokens'),
                'temperature': (notice.get('fallback') or {}).get('temperature'),
                'reasoningEffort': (
                    notice.get('fallback') or {}).get('reasoning_effort'),
            },
        } for notice in notices]
    failover_usage = stats.get('failover')
    if isinstance(failover_usage, dict):
        slot_names = {'primary': 'llm-a', 'secondary': 'llm-b'}
        result['failover'] = {
            'llm': [slot_names[slot] for slot in failover_usage.get('llm') or ()
                    if slot in slot_names],
            'whisper': bool(failover_usage.get('whisper')),
        }
    return result


def processing_runs(db, episode, run_totals=None):
    """Serialize run history, retaining totals for runs without ledger rows."""
    podcast_id = episode['podcast_id']
    episode_id = episode['episode_id']
    phase_usage = db.get_episode_phase_usage(podcast_id, episode_id)
    if run_totals is None:
        run_totals = db.get_episode_run_usage_totals(podcast_id, episode_id)

    runs = []
    for row in db.get_episode_processing_runs(podcast_id, episode_id):
        stats = None
        if row.get('processing_stats_json'):
            try:
                stats = json.loads(row['processing_stats_json'])
            except (json.JSONDecodeError, TypeError):
                pass
        run_id = row.get('run_id')
        phases = phase_usage.get(run_id, []) if run_id else []
        # Run-level totals come from the ledger (the same source as the header
        # and phase breakdown) when the run has ledger rows, so the run row can
        # never disagree with them; legacy runs keep their history counters.
        totals = run_totals.get(run_id) if run_id else None
        if phases and totals:
            input_tokens = totals['input_tokens']
            output_tokens = totals['output_tokens']
            llm_cost = round(float(totals['cost_usd']), 6)
        else:
            input_tokens = row.get('input_tokens') or 0
            output_tokens = row.get('output_tokens') or 0
            llm_cost = round(row.get('llm_cost') or 0.0, 6)
        runs.append({
            'runNumber': row.get('reprocess_number'),
            'runId': run_id,
            'processedAt': row.get('processed_at'),
            'status': row.get('status'),
            'adsDetected': row.get('ads_detected'),
            'processingDurationSeconds': row.get('processing_duration_seconds'),
            'errorMessage': row.get('error_message'),
            'inputTokens': input_tokens,
            'outputTokens': output_tokens,
            'llmCost': llm_cost,
            'hasLog': bool(row.get('log_file')),
            'stats': run_stats_to_api(stats),
            'phases': phases,
            'breakdownAvailable': bool(phases),
        })
    return runs


def ledger_spend(run_id, totals):
    """Ledger subtotal for one run in the shared spend shape."""
    return {
        'runId': run_id,
        'inputTokens': totals['input_tokens'],
        'outputTokens': totals['output_tokens'],
        'costUsd': totals['cost_usd'],
        'breakdownAvailable': True,
        'hasUnknownCost': totals['has_unknown_cost'],
    }


def run_spend(run, totals):
    """One finished run's spend as {runId, inputTokens, outputTokens, costUsd,
    breakdownAvailable, hasUnknownCost}. Uses the ledger subtotal (the same
    source phase rows reconcile to) when available, else the run's known
    processing_history total. ``totals`` is this run's entry from
    get_episode_run_usage_totals, so no second per-run query is needed."""
    if run['breakdownAvailable'] and totals:
        return ledger_spend(run['runId'], totals)
    return {
        'runId': run['runId'],
        'inputTokens': run['inputTokens'],
        'outputTokens': run['outputTokens'],
        'costUsd': str(Decimal(str(run['llmCost']))),
        'breakdownAvailable': False,
        # A pre-ledger run reports only its recorded total; there are no
        # unknown-cost rows to flag because there are no rows at all.
        'hasUnknownCost': False,
    }


def active_run_spend(db, slug, episode_id):
    """Live ledger spend of the run that currently owns this episode, or None
    when no run does. Reads the ledger directly: the history row that
    run_spend needs is only written when a run finishes."""
    run_id = ProcessingQueue().active_run_id(slug, episode_id)
    if not run_id or run_id == '__database_unavailable__':
        return None
    return ledger_spend(run_id, db.get_run_usage_totals(run_id))


def partial_detection(episode, runs):
    """Degraded pass-1 completion (episodes.detection_degraded). Window
    counts come from the run that produced the served audio (the latest
    completed one, same lookup low_ad_yield uses), when available."""
    reason = episode.get('detection_degraded')
    if not reason:
        return None
    latest_completed = latest_completed_run(runs) if runs else None
    latest_stats = ((latest_completed or {}).get('stats')) or {}
    windows = latest_stats.get('windows') or {}
    return {
        'reason': reason,
        'windowsFailed': windows.get('failed'),
        'windowsTotal': windows.get('total'),
    }


def incomplete_coverage(runs):
    """Windows the latest completed run lost, per pass. Independent of
    detection_degraded: a run that answered most windows still completes, so
    the skipped stretches were never examined for ads."""
    latest_stats = ((latest_completed_run(runs) if runs else None) or {}).get('stats') or {}
    coverage = {}
    for key, pass_name in (('windows', 'detection'),
                           ('verificationWindows', 'verification')):
        counts = latest_stats.get(key) or {}
        failed = counts.get('failed') or 0
        if failed > 0:
            coverage[pass_name] = {'failed': failed, 'total': counts.get('total')}
            if counts.get('failureClasses'):
                coverage[pass_name]['failureClasses'] = counts['failureClasses']
    return coverage or None
