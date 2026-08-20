"""Feed-relative low-ad-yield heuristic (#519), shared by the episode API
badge and the pipeline's low-ad-yield response policy."""

# A run is flagged only when the feed has an established ad load (average
# of at least 2 minutes over 3+ recent episodes) and this episode removed
# under 35% of it. DAI copies served with unfilled slots trip this; so
# would a real detection miss.
LOW_YIELD_MIN_AVG_SECONDS = 120
LOW_YIELD_MIN_SAMPLES = 3
LOW_YIELD_FRACTION = 0.35


def latest_completed_run(runs):
    """Most recent completed run from a processingRuns list, or None. The
    run that produced the currently served audio."""
    return next((r for r in reversed(runs) if r.get('status') == 'completed'),
                None)


def _detection_suppressed(stats):
    """Runs that remove nothing by design: pass-through (#521) and
    skip-detection (#538). Stats reach here in API casing from the episode
    endpoint and in the pipeline's snake_case from the processing hook."""
    return bool(stats.get('mode') == 'passthrough'
                or stats.get('detectionSkipped')
                or stats.get('detection_skipped'))


def low_ad_yield(db, episode, runs):
    """Compare this episode's removed ad time against the feed's recent
    average (#519). Returns the comparison dict when far below it.

    The check keys on the run that produced the served audio (the latest
    completed one), not merely the newest history row, so a failed later
    attempt cannot un-suppress it. Suppressed siblings in the baseline only
    drag the feed average down, which makes the flag less likely to fire,
    not more.
    """
    latest = latest_completed_run(runs) if runs else None
    latest_stats = ((latest or {}).get('stats')) or {}
    if _detection_suppressed(latest_stats):
        return None
    original = episode.get('original_duration')
    new = episode.get('new_duration')
    if not original or new is None:
        return None
    removed = original - new
    yields = db.get_recent_ad_yields(episode['podcast_id'],
                                     episode['episode_id'])
    if len(yields) < LOW_YIELD_MIN_SAMPLES:
        return None
    average = sum(yields) / len(yields)
    if average < LOW_YIELD_MIN_AVG_SECONDS or removed >= average * LOW_YIELD_FRACTION:
        return None
    return {
        'removedSeconds': round(removed, 1),
        'feedAverageSeconds': round(average, 1),
        'sampleSize': len(yields),
    }
