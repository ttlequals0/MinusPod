"""Feed-relative low-ad-yield heuristic (#519), shared by the episode API
badge and the pipeline's low-ad-yield response policy."""

# Flagged when the feed averages 2+ minutes of ads over 3+ recent episodes and this
# one removed under 35% of that (unfilled DAI slots trip it, as would a real miss).
LOW_YIELD_MIN_AVG_SECONDS = 120
LOW_YIELD_MIN_SAMPLES = 3
LOW_YIELD_FRACTION = 0.35


def latest_completed_run(runs):
    """Most recent completed run from a processingRuns list, or None. The
    run that produced the currently served audio."""
    return next((r for r in reversed(runs)
                 if r.get('status') == 'completed'
                 and (r.get('stats') or {}).get('mode') != 'chapters'), None)


def _detection_suppressed(stats):
    """Runs that remove nothing by design: pass-through (#521) and
    skip-detection (#538). Stats reach here in API casing from the episode
    endpoint and in the pipeline's snake_case from the processing hook."""
    return bool(stats.get('mode') == 'passthrough'
                or stats.get('detectionSkipped')
                or stats.get('detection_skipped'))


def low_ad_yield(db, episode, runs):
    """Return the comparison dict when this episode removed far less ad time than the feed average (#519).

    Keys on the run that produced the served audio (latest completed), so a failed later
    attempt cannot un-suppress it.
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
