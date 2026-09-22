"""Feed management: get_feed_map, invalidate_feed_cache, refresh_rss_feed, refresh_all_feeds."""
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from config import (
    FEED_REFRESH_FAILURE_ALERT_THRESHOLD,
    FEED_REFRESH_FAILURE_COUNT_INTERVAL,
    FEED_REFRESH_OUTAGE_FRACTION,
    FEED_REFRESH_OUTAGE_MIN_FEEDS,
    FEED_REFRESH_OUTAGE_RETRY_BASE_SECONDS,
    FEED_REFRESH_OUTAGE_RETRY_JITTER_SECONDS,
    title_matches_skip_patterns,
)

from database.episodes import normalize_published_at
from database.podcasts import is_local_feed, is_recents_feed, podping_declaration_columns
from database.queue import compute_queue_priority
from chapter_notes import chapter_notes_for
from local_feed_builder import rebuild_local_feed
from recents_feed import rebuild_recents_feed
from utils.http import safe_url_for_log
from utils.retry import calculate_backoff
from utils.time import parse_iso_utc, utc_now_iso

from slugify import slugify

from main_app.cache import TTLCache
# Singletons are created in main_app/__init__.py before the explicit
# `from main_app.feeds import ...` near the bottom of that module, so
# importing them here at module level is safe despite the surface-level
# circular shape. The previous _get_components() helper returned a
# positional 5-tuple; replacing it with direct imports removes the
# tuple-reorder footgun the audit flagged.
from main_app import db, rss_parser, storage, status_service, pattern_service
from main_app.feed_auth import active_feed_key
from main_app.shared_state import invalidate_episode_lookup_cache

import webhook_service

refresh_logger = logging.getLogger('podcast.refresh')
feed_logger = logging.getLogger('podcast.feed')

# Initialize caches for performance
_feed_cache = TTLCache(ttl_seconds=30)

# Coalesce back-to-back refreshes of the same feed. When a PocketCasts
# poll triggers serve_rss's on-demand refresh at the same moment the
# 15-min background loop hits the same feed, we see two "Starting RSS
# refresh" calls 3-5 seconds apart -- both conditional-GET, both hit
# upstream. Skip the second one. `force=True` (finalize hook, manual
# reprocess, API force-refresh) bypasses the skip but still stamps so
# subsequent non-force calls within the window coalesce.
_refresh_coalesce = TTLCache(ttl_seconds=30)

# Unparseable-body backoff: a body that never parses used to force a full
# refetch every cycle. Doubles per consecutive failure, cleared by a clean parse.
PARSE_FAILURE_BACKOFF_BASE_SECONDS = 1800
PARSE_FAILURE_BACKOFF_MAX_SECONDS = 6 * 3600


@dataclass(frozen=True)
class RefreshOutcome:
    success: bool
    status: str
    new_episodes: int = 0
    queued_episodes: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def __bool__(self) -> bool:
        return self.success


def _scrub_query_strings(text: str) -> str:
    """Drop query strings from any URL embedded in an error message --
    private-feed tokens live there, and this text is persisted, shown in
    the UI, and sent to webhooks/email."""
    return re.sub(r'(https?://[^\s?]*)\?\S+', r'\1?<redacted>', text)


def _record_refresh_failure(slug: str, error_message: str, podcast=None):
    """Persist per-feed failure state and alert once per outage.

    Only failures spaced at least FEED_REFRESH_FAILURE_COUNT_INTERVAL apart
    are counted, so client-poll-driven retries during a brief blip cannot
    reach the alert threshold in minutes; the notification fires only on
    the exact transition to the threshold, so a feed that stays broken does
    not re-alert. Callers that already hold the podcast row pass it to skip
    the re-read.
    """
    try:
        if podcast is None:
            podcast = db.get_podcast_by_slug(slug)
        if not podcast:
            return
        now = datetime.now(timezone.utc)
        last_counted = parse_iso_utc(podcast.get('last_refresh_failure_at'))
        if last_counted and (now - last_counted).total_seconds() < FEED_REFRESH_FAILURE_COUNT_INTERVAL:
            return
        count = (podcast.get('refresh_failure_count') or 0) + 1
        scrubbed_error = _scrub_query_strings(str(error_message))
        db.update_podcast(
            slug,
            refresh_failure_count=count,
            last_refresh_error=scrubbed_error[:500],
            # Stamp only the first failure of a run so the UI shows how
            # long the feed has been broken, not the latest attempt.
            last_refresh_error_at=(podcast.get('last_refresh_error_at')
                                   or utc_now_iso()),
            last_refresh_failure_at=utc_now_iso(),
        )
        if count == FEED_REFRESH_FAILURE_ALERT_THRESHOLD:
            sent = webhook_service.fire_feed_refresh_failed_event(
                slug=slug,
                podcast_name=podcast.get('title') or slug,
                feed_url=safe_url_for_log(podcast.get('source_url') or '',
                                          keep_path=True),
                error_message=scrubbed_error,
                failure_count=count,
            )
            if not sent:
                # Suppressed by the dedup/burst caps. Step the count back
                # so the next counted failure re-crosses the threshold and
                # retries -- otherwise this outage's one alert is lost.
                db.update_podcast(slug, refresh_failure_count=count - 1)
    except Exception:
        refresh_logger.exception(f"[{slug}] Failed to record refresh failure")


def _rss_cache_stale(slug: str, podcast) -> bool:
    """True when the served RSS is missing an episode we have already processed."""
    cached_rss = storage.get_rss(slug)
    return not cached_rss or any(
        ep['episode_id'] not in cached_rss
        for ep in db.get_processed_episodes_for_feed(podcast['id'])
    )


def _parse_backoff_seconds(failure_count: int) -> float:
    """Exponential backoff for an unparseable feed body, capped."""
    if failure_count <= 0:
        return 0.0
    return calculate_backoff(
        failure_count - 1, base_delay=PARSE_FAILURE_BACKOFF_BASE_SECONDS,
        max_delay=PARSE_FAILURE_BACKOFF_MAX_SECONDS, jitter=False)


def _parse_backoff_remaining(podcast) -> float:
    """Seconds left before an unparseable feed may be fully refetched again."""
    count = (podcast or {}).get('parse_failure_count') or 0
    last_at = parse_iso_utc((podcast or {}).get('last_parse_failure_at'))
    if count <= 0 or last_at is None:
        return 0.0
    elapsed = (datetime.now(timezone.utc) - last_at).total_seconds()
    return max(0.0, _parse_backoff_seconds(count) - elapsed)


def _parse_backoff_skip(slug: str, backoff: float) -> RefreshOutcome:
    """Hold a full refetch of a body that has not parsed, and say for how long."""
    refresh_logger.info(
        f"[{slug}] Last feed body did not parse; holding the full fetch "
        f"for another {backoff / 60:.0f} min")
    db.update_podcast(slug, last_checked_at=utc_now_iso())
    status_service.complete_feed_refresh(slug, 0)
    return RefreshOutcome(
        False, 'parse_backoff',
        error=f'Feed body did not parse; backing off for another '
              f'{backoff / 60:.0f} min')


def _record_parse_failure(slug: str, podcast=None) -> float:
    """Bump the feed's consecutive unparseable-body counter and return the backoff now in effect.
    Separate from the alerting refresh-failure counter: a fetch that never reached
    the parser must not extend this one."""
    try:
        count = ((podcast or {}).get('parse_failure_count') or 0) + 1
        db.update_podcast(slug, parse_failure_count=count,
                          last_parse_failure_at=utc_now_iso())
        backoff = _parse_backoff_seconds(count)
        refresh_logger.warning(
            f"[{slug}] Feed body unparseable {count} time(s) in a row; "
            f"next forced full fetch in {backoff / 60:.0f} min")
        return backoff
    except Exception:
        refresh_logger.exception(f"[{slug}] Failed to record parse failure")
        return 0.0


def _record_refresh_success(slug: str):
    """Clear failure state after a successful refresh (no-op when clean)."""
    try:
        db.clear_refresh_failure_state(slug)
    except Exception:
        refresh_logger.exception(f"[{slug}] Failed to clear refresh failure state")


def get_feed_map():
    """Get feed map from database, with TTL caching."""
    cached = _feed_cache.get('all_feeds')
    if cached is not None:
        return cached

    feeds = db.get_feeds_config()
    result = {slugify(feed['out'].strip('/')): feed for feed in feeds}
    _feed_cache.set('all_feeds', result)
    return result


def invalidate_feed_cache():
    """Invalidate feed cache after any feed modification."""
    _feed_cache.invalidate('all_feeds')


def refresh_rss_feed(slug: str, feed_url: str, force: bool = False,
                      record_failure: bool = True):
    """Refresh RSS feed for a podcast.

    Args:
        slug: Podcast slug
        feed_url: URL of the original RSS feed
        force: If True, bypass conditional GET (ETag/Last-Modified) to force full fetch.
               Use this when the RSS cache was deleted and needs regeneration.
               Also bypasses the refresh-attempt throttle.
        record_failure: If False, an origin fetch/parse failure is returned
               without touching refresh_failure_count or firing the alert.
               refresh_all_feeds passes False and applies counting itself
               once it knows whether the whole batch looks like a shared
               outage.
    """
    podcast = db.get_podcast_row(slug)
    if is_local_feed(podcast):
        success = bool(rebuild_local_feed(slug, podcast))
        return RefreshOutcome(success, 'refreshed' if success else 'failed')
    if is_recents_feed(podcast):
        success = bool(rebuild_recents_feed(podcast))
        return RefreshOutcome(success, 'refreshed' if success else 'failed')

    if not force and _refresh_coalesce.get(slug) is not None:
        refresh_logger.debug(f"[{slug}] Skipping refresh (recent attempt within coalesce window)")
        return RefreshOutcome(True, 'coalesced')
    _refresh_coalesce.set(slug, True)
    # Stamp the attempt up front, before any fetch can fail, so the staggered
    # scheduler retries this feed on the interval instead of re-selecting it
    # every tick while it keeps failing (last_checked_at is success-only).
    db.update_podcast(slug, last_refresh_attempt_at=utc_now_iso())

    try:
        # Get podcast name and etag for conditional fetch
        podcast = db.get_podcast_row(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # A body that never parses yields no discovery, so hold the fetches
        # that re-download it whole. A conditional GET still runs: its 304
        # costs nothing, and a changed body may parse. force is user-driven.
        backoff = 0.0 if force else _parse_backoff_remaining(podcast)

        # Track feed refresh in status service
        status_service.start_feed_refresh(slug, podcast_name)

        # INFO so the pulled URL is visible in default logs for troubleshooting
        # upstream fetch failures (#484). Query string is deliberately dropped:
        # private-feed tokens live there.
        refresh_logger.info(
            f"[{slug}] Starting RSS refresh from: {safe_url_for_log(feed_url, keep_path=True)}")

        # Fetch original RSS with conditional GET (ETag/Last-Modified)
        # Skip conditional GET if force=True (cache was deleted, need full content)
        existing_etag = None if force else (podcast.get('etag') if podcast else None)
        existing_last_modified = None if force else (podcast.get('last_modified_header') if podcast else None)
        if backoff > 0 and not existing_etag and not existing_last_modified:
            return _parse_backoff_skip(slug, backoff)

        feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
            feed_url,
            etag=existing_etag,
            last_modified=existing_last_modified
        )

        # Handle 304 Not Modified - feed hasn't changed
        if feed_content is None and (new_etag or new_last_modified):
            # If no episodes exist yet (pre-v1.0.41 feed), force full fetch for initial
            # discovery; a fully-processed feed still has rows here, of any status.
            if db.has_episodes(slug):
                # Even on 304, ensure artwork is cached (may be missing after DB restore)
                podcast = db.get_podcast_row(slug)
                # A 304 carries no body, so a steady-state feed would never
                # have its <podcast:podping> tag ingested (#579). Stamped only
                # on a successful fetch, so a failing feed retries each cycle.
                forced_reason = None
                if podcast and not podcast.get('artwork_cached'):
                    forced_reason = 'artwork missing'
                elif podcast and not podcast.get('podping_checked_at'):
                    forced_reason = 'podping declaration never read'
                elif podcast and not podcast.get('channel_metadata_at'):
                    # Rows written before the raw-XML read can hold a live
                    # item's description or link (#596).
                    forced_reason = 'channel metadata never read from raw XML'
                elif _rss_cache_stale(slug, podcast):
                    forced_reason = 'RSS cache stale'
                if forced_reason and backoff > 0:
                    return _parse_backoff_skip(slug, backoff)
                if forced_reason:
                    refresh_logger.info(
                        f"[{slug}] Feed unchanged (304) but {forced_reason}, forcing full fetch")
                    feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
                        feed_url, etag=None, last_modified=None
                    )
                else:
                    refresh_logger.debug(f"[{slug}] Feed unchanged (304), skipping refresh")
                    db.update_podcast(slug, last_checked_at=utc_now_iso())
                    _record_refresh_success(slug)
                    status_service.complete_feed_refresh(slug, 0)
                    return RefreshOutcome(True, 'not_modified')
            elif backoff > 0:
                return _parse_backoff_skip(slug, backoff)
            else:
                refresh_logger.info(
                    f"[{slug}] Feed unchanged (304) but no episodes discovered yet, "
                    f"forcing full fetch for initial discovery"
                )
                feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
                    feed_url, etag=None, last_modified=None
                )

        if not feed_content:
            refresh_logger.error(f"[{slug}] Failed to fetch RSS feed")
            if record_failure:
                _record_refresh_failure(
                    slug, 'Failed to fetch RSS feed (unreachable, invalid '
                          'response, or blocked)', podcast=podcast)
            status_service.complete_feed_refresh(slug, 0)
            _refresh_coalesce.invalidate(slug)
            return RefreshOutcome(False, 'fetch_failed', error='Failed to fetch RSS feed')

        # Parse feed to extract metadata. A body that yields neither channel
        # metadata nor entries AND tripped the parser (bozo) is an origin
        # failure (error page served with an RSS content type), not a
        # success -- treating it as success would reset the failure counter
        # mid-outage. A clean parse of an empty placeholder feed still
        # counts as success.
        parsed_feed = rss_parser.parse_feed(feed_content, source=slug)
        if parsed_feed is None:
            # One immediate refetch usually clears a body cut in transfer before falling back to backoff.
            refresh_logger.info(f"[{slug}] Feed body failed to parse; refetching once")
            feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
                feed_url, etag=None, last_modified=None
            )
            if feed_content:
                parsed_feed = rss_parser.parse_feed(feed_content, source=slug)
        if not parsed_feed or (not parsed_feed.feed and not parsed_feed.entries
                               and getattr(parsed_feed, 'bozo', False)):
            refresh_logger.error(f"[{slug}] Fetched feed could not be parsed as RSS")
            parse_error = ('Fetched feed could not be parsed as RSS (the URL '
                           'may be returning an error page)')
            if record_failure:
                backoff = _record_parse_failure(slug, podcast=podcast)
                parse_error += f'; retrying a full fetch in {backoff / 60:.0f} min'
                _record_refresh_failure(slug, parse_error, podcast=podcast)
            status_service.complete_feed_refresh(slug, 0)
            _refresh_coalesce.invalidate(slug)
            return RefreshOutcome(False, 'parse_failed', error=parse_error)
        # The body parsed, which is the only event that ends the backoff.
        try:
            db.clear_parse_failure_state(slug)
        except Exception:
            refresh_logger.exception(f"[{slug}] Failed to clear parse failure state")
        if parsed_feed and parsed_feed.feed:
            # feedparser flattens <podcast:liveItem> into the channel dict,
            # so read the raw children and fall back per field (#596).
            channel_elem = rss_parser.find_channel_element(feed_content)
            fields = rss_parser.resolve_channel_fields(
                feed_content, parsed_feed=parsed_feed, channel=channel_elem)

            title = fields['title'] or None
            # 10k bounds a pathological feed without visibly truncating real
            # descriptions (#596; the old 500 cap surfaced once the UI
            # stopped line-clamping).
            description = fields['description'][:10000]

            # Extract ordered artwork candidates from RAW xml (feedparser
            # corrupts the channel image with the last per-episode
            # itunes:image it sees): itunes:image first, then <image><url>.
            artwork_candidates = rss_parser.extract_podcast_artwork_url(
                feed_content, channel=channel_elem)

            # Channel-level <link> is the show's website (#521); only keep
            # real http(s) URLs. Refreshed with the rest of the metadata.
            website_url = fields['link'].strip()
            if not website_url.startswith(('http://', 'https://')):
                website_url = None

            # Upstream <podcast:podping> declaration (#579): who may podping
            # this feed, and whether it opts out entirely.
            podping = rss_parser.extract_podping_declaration(
                feed_content, channel=channel_elem)

            prev = podcast or {}
            # "Changed" means the resolved cover has dropped out of the
            # feed's current candidate set entirely, not just that the
            # preferred candidate differs from it: otherwise a
            # persistently-broken itunes:image (while the RSS <image> keeps
            # resolving fine) would look "changed" on every single refresh
            # and force a redownload attempt of the known-bad URL each time.
            artwork_source_changed = (bool(artwork_candidates)
                                      and prev.get('artwork_url') not in artwork_candidates)
            changed = [
                name for name, before, after in (
                    ('title', prev.get('title'), title),
                    ('description', prev.get('description'), description),
                    ('website', prev.get('website_url'), website_url),
                )
                if after and before != after
            ]
            if artwork_source_changed:
                changed.append('artwork')
            if changed:
                refresh_logger.info(
                    f"[{slug}] Feed metadata changed upstream: {', '.join(changed)}")

            # Update podcast metadata (and ETag if available) in a single DB
            # call. artwork_url/artwork_cached are deliberately NOT written
            # here: storage.download_artwork is the sole writer of those two
            # fields (via save_artwork), so a failed candidate never clears
            # or overwrites a still-valid cached cover.
            update_kwargs = dict(
                title=title,
                description=description,
                website_url=website_url,
                **podping_declaration_columns(
                    podping.get('uses_podping'), podping.get('hive_accounts')),
                channel_metadata_at=utc_now_iso(),
            )
            db.update_podcast(slug, **update_kwargs)

            # Map iTunes categories to MinusPod vocabulary tags, then refresh the
            # RSS layer of the podcast's tags. set_podcast_tags also folds in
            # episode-level tags and the user_tags layer.
            try:
                from utils.community_tags import map_itunes_category
                raw_cats = fields['categories']
                rss_tags = sorted({
                    tag for cat in raw_cats
                    if (tag := map_itunes_category(cat))
                })
                db.set_podcast_tags(slug, rss_tags=rss_tags)
            except Exception as e:
                refresh_logger.warning(f"[{slug}] iTunes category mapping failed: {e}")

            # Detect DAI platform and network from feed metadata
            feed_author = fields['author']
            network_info = pattern_service.update_podcast_metadata(
                podcast_id=slug,
                feed_url=feed_url,
                feed_content=feed_content,
                feed_title=title,
                feed_description=description,
                feed_author=feed_author
            )
            if network_info.get('dai_platform') or network_info.get('network_id'):
                refresh_logger.debug(
                    f"[{slug}] Detected: platform={network_info.get('dai_platform')}, "
                    f"network={network_info.get('network_id')}"
                )

            # A changed source forces the fetch past the "already cached"
            # guard; otherwise the durable per-URL backoff throttles retries
            # of a still-broken candidate while a working one stays served.
            if artwork_candidates:
                # Automatic refresh: bypass the cached-guard for a changed
                # source, but keep the per-URL backoff so a persistently broken
                # replacement is not refetched on every refresh.
                storage.download_artwork(slug, artwork_candidates,
                                         force=artwork_source_changed,
                                         bypass_backoff=False)

        # Discover all episodes from the feed (upsert as 'discovered').
        # Pass parsed_feed so extract_episodes does not re-parse the same
        # XML we already parsed above.
        all_episodes = rss_parser.extract_episodes(
            feed_content, parsed_feed=parsed_feed, source=slug)
        discovery = db.bulk_upsert_discovered_episodes(
            slug, all_episodes, return_state=True)
        if isinstance(discovery, tuple):
            inserted, ep_statuses, title_date_map = discovery
        else:
            inserted = discovery
            ep_statuses, title_date_map = db.get_episode_statuses_for_podcast(slug)
        if inserted > 0:
            refresh_logger.info(f"[{slug}] Discovered {inserted} new episode(s)")

        # Queue new episodes for auto-processing if enabled
        # Only queue episodes published within the last 48 hours to avoid processing entire backlog
        queued_count = 0
        if db.is_auto_process_enabled_for_podcast(slug, podcast=podcast):
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=48)
            # Read once per refresh, not per episode.
            fresh_boost_enabled = db.get_setting_bool('process_new_episodes_first', True)

            queue_candidates = []
            for ep in all_episodes:
                # Check if episode already exists in database with a non-discovered status
                existing_status = ep_statuses.get(ep['id'])
                if existing_status is None or existing_status == 'discovered':
                    # Also check by title+pubDate to catch ID changes (Megaphone feeds, etc.)
                    # This prevents duplicate processing when RSS GUID changes
                    iso_published = normalize_published_at(ep.get('published', '')) or None

                    if iso_published and ep.get('title'):
                        existing_id = title_date_map.get((ep.get('title'), iso_published))
                        if existing_id and existing_id != ep['id']:
                            refresh_logger.debug(
                                f"[{slug}] Episode ID updated: {existing_id} -> {ep['id']}, "
                                f"title: {ep.get('title')}"
                            )
                            continue  # Skip - episode already exists with different ID

                    # Parse publish date to check if recent
                    is_recent = False
                    if iso_published:
                        try:
                            pub_date = datetime.fromisoformat(iso_published.replace('Z', '+00:00'))
                            is_recent = pub_date >= cutoff_time
                        except (ValueError, TypeError):
                            # If we can't parse the date, skip this episode for auto-process
                            refresh_logger.debug(f"[{slug}] Could not parse date for episode: {ep.get('title')}")
                            is_recent = False

                    if is_recent:
                        if title_matches_skip_patterns(
                                ep.get('title'), podcast.get('title_skip_patterns') if podcast else None):
                            refresh_logger.info(f"[{slug}] Skipping title-blacklisted episode: {ep.get('title')}")
                            continue
                        # New recent episode - queue for processing
                        # iso_published already calculated above for deduplication check
                        feed_priority = podcast.get('queue_priority') if podcast else None
                        priority = compute_queue_priority(
                            feed_priority, iso_published, manual=False,
                            apply_fresh_boost=fresh_boost_enabled)
                        queue_candidates.append({
                            'episode_id': ep['id'], 'original_url': ep['url'],
                            'title': ep.get('title'), 'published_at': iso_published,
                            'description': ep.get('description'), 'priority': priority,
                        })

            queued_ids = db.queue_episodes_for_processing(
                slug, queue_candidates, podcast=podcast)
            queued_count = len(queued_ids)

            if queued_count > 0:
                refresh_logger.info(f"[{slug}] Queued {queued_count} new episode(s) for auto-processing")

        # Rebuild and persist the served RSS for the current feed/output settings.
        _build_and_save_served_rss(slug, feed_content, parsed_feed, podcast)
        completed = {'last_checked_at': utc_now_iso()}
        if new_etag or new_last_modified or force:
            completed['etag'] = new_etag
            completed['last_modified_header'] = new_last_modified
        db.update_podcast(slug, **completed)

        refresh_logger.debug(f"[{slug}] RSS refresh complete")
        _record_refresh_success(slug)
        status_service.complete_feed_refresh(slug, 0)
        return RefreshOutcome(True, 'refreshed', inserted, queued_count)
    except Exception as e:
        # Deliberately NOT recorded as a feed failure: exceptions here are
        # internal faults (DB locked, disk full, downstream bugs), and the
        # Feed Refresh Failed alert blames the publisher's feed. Origin
        # failures are recorded at the fetch/parse boundaries above.
        refresh_logger.error(
            f"[{slug}] RSS refresh failed: {_scrub_query_strings(str(e))}")
        status_service.remove_feed_refresh(slug)
        _refresh_coalesce.invalidate(slug)
        return RefreshOutcome(False, 'internal_error', error='Internal refresh error')


def refresh_all_feeds(force: bool = False):
    """Refresh every subscribed feed in parallel.

    Args:
        force: If True, bypass each feed's ETag and 30s refresh-coalesce window
               so every feed is fully re-fetched. Used by the UI Force Refresh
               All action; the background scheduler staggers instead (see
               refresh_due_feeds).
    """
    return _run_refresh_batch(force, label=f"all RSS feeds (force={force})")


def refresh_due_feeds(batch_size: int, interval_seconds: float):
    """Refresh only the subscribed feeds whose last refresh is older than
    interval_seconds, oldest first, capped at batch_size. The scheduler calls
    this on a short tick so feed writes spread across the interval instead of
    contending in one whole-corpus burst."""
    due = db.get_due_feed_slugs(interval_seconds, batch_size)
    if not due:
        return {'success': True, 'succeeded': 0, 'failed': 0, 'outcomes': {},
                'outage': {'detected': False, 'affectedCount': 0, 'nextRetryAt': None}}
    return _run_refresh_batch(False, slugs=due, label=f"{len(due)} due feed(s)")


def _run_refresh_batch(force, slugs=None, label='RSS feeds'):
    """Refresh a set of feeds in parallel and judge a shared outage over the
    batch. slugs=None means every feed in the map (the force sweep); a list
    restricts it to those slugs (the staggered tick)."""
    try:
        refresh_logger.info(f"Refreshing {label}")

        # The force sweep enumerates every configured feed; the staggered tick
        # passes the due slugs. Either way the fetch URL and feed_type come from
        # the podcast row, so a due slug never has to round-trip through the
        # slugify-keyed feed map.
        candidates = slugs if slugs is not None else list(get_feed_map().keys())

        # Parallelize feed refresh with ThreadPoolExecutor. record_failure=False:
        # per-feed failure counting is deferred until the batch fraction is
        # known below, so a shared outage never marks healthy feeds broken.
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {}
            for slug in candidates:
                # get_podcast_row, not get_podcast_by_slug: only feed_type and
                # source_url are read, so the episode aggregation is wasted.
                row = db.get_podcast_row(slug)
                if not row or is_local_feed(row) or is_recents_feed(row):
                    continue
                source_url = row.get('source_url')
                if not source_url:
                    continue
                futures[executor.submit(
                    refresh_rss_feed, slug, source_url, force, False)] = slug
            outcomes = {}
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    outcome = future.result()
                    outcomes[slug] = outcome
                except Exception as e:
                    refresh_logger.error(
                        f"[{slug}] Feed refresh failed: {_scrub_query_strings(str(e))}")
                    outcomes[slug] = RefreshOutcome(
                        False, 'internal_error', error='Internal refresh error')

        # A parse_backoff is a deliberate skip, not a failure: counting it
        # would, on a small instance, read as a shared outage.
        skipped = sum(1 for outcome in outcomes.values()
                      if not outcome.success and outcome.status == 'parse_backoff')
        succeeded = sum(1 for outcome in outcomes.values() if outcome.success)
        total = len(outcomes)
        failed = total - succeeded - skipped
        refresh_logger.info(
            f"RSS refresh complete: {succeeded} succeeded, {failed} failed, "
            f"{skipped} in parse backoff")

        # Judged over the feeds actually attempted: a batch mostly in parse
        # backoff has no opinion on whether the network path is up.
        attempted = total - skipped
        outage_detected = (
            failed > 0 and attempted >= FEED_REFRESH_OUTAGE_MIN_FEEDS
            and (failed / attempted) >= FEED_REFRESH_OUTAGE_FRACTION
        )
        outage_info = {'detected': False, 'affectedCount': failed, 'nextRetryAt': None}

        if outage_detected:
            # Shared outage: origin feeds did not individually break, the
            # network path did. Cached feed data and conditional-fetch
            # validators (etag/last-modified) are untouched by a failed
            # refresh_rss_feed call, so nothing needs preserving here beyond
            # not counting the failures. One jittered retry avoids every
            # instance hammering the same upstream host back-to-back.
            refresh_logger.warning(
                f"RSS refresh: shared outage detected ({failed}/{attempted} feeds "
                "failed together) - skipping per-feed failure counting, "
                "scheduling one jittered retry")
            retry_delay = (FEED_REFRESH_OUTAGE_RETRY_BASE_SECONDS
                           + random.uniform(0, FEED_REFRESH_OUTAGE_RETRY_JITTER_SECONDS))
            next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
            next_retry_iso = next_retry_at.isoformat()
            db.set_setting('feeds_refresh_outage_active', '1')
            db.set_setting('feeds_refresh_outage_affected_count', str(failed))
            db.set_setting('feeds_refresh_outage_detected_at', utc_now_iso())
            db.set_setting('feeds_next_refresh_retry_at', next_retry_iso)
            outage_info = {
                'detected': True, 'affectedCount': failed, 'nextRetryAt': next_retry_iso,
            }
        else:
            # Not an outage (or too few feeds to judge): count failures the
            # same way refresh_rss_feed would have inline.
            for slug, outcome in outcomes.items():
                if outcome.status == 'parse_failed':
                    _record_parse_failure(slug, podcast=db.get_podcast_row(slug))
                if outcome.status in ('fetch_failed', 'parse_failed'):
                    _record_refresh_failure(slug, outcome.error or 'RSS refresh failed')
            if db.get_setting('feeds_refresh_outage_active') == '1':
                db.set_setting('feeds_refresh_outage_active', '0')
                db.set_setting('feeds_next_refresh_retry_at', '')

        # The dashboard "all feeds fresh as of T" indicator is computed on read
        # from MIN(last_checked_at); no sweep-completion timestamp is written.
        return {
            'success': failed == 0,
            'succeeded': succeeded,
            'failed': failed,
            'outcomes': {slug: outcome.to_dict() for slug, outcome in outcomes.items()},
            'outage': outage_info,
        }
    except Exception as e:
        refresh_logger.error(f"RSS refresh failed: {_scrub_query_strings(str(e))}")
        return {'success': False, 'succeeded': 0, 'failed': 0, 'outcomes': {},
                'outage': {'detected': False, 'affectedCount': 0, 'nextRetryAt': None}}


def refresh_single_feed(slug: str) -> bool:
    """Refresh one feed by slug. Used by the podping listener; the background
    scheduler staggers feeds through refresh_due_feeds instead."""
    podcast = db.get_podcast_by_slug(slug)
    if not podcast or not podcast.get('source_url'):
        return False
    if is_local_feed(podcast):
        return False
    try:
        outcome = refresh_rss_feed(slug, podcast['source_url'])
        if not isinstance(outcome, RefreshOutcome):
            return bool(outcome)
        # A held full fetch is the backoff working as intended; the caller
        # must not log it or count it as a failed refresh.
        return outcome.success or outcome.status == 'parse_backoff'
    except Exception as e:
        refresh_logger.error(
            f"[{slug}] Single-feed refresh failed: {_scrub_query_strings(str(e))}")
        return False


def _build_and_save_served_rss(slug, feed_content, parsed_feed, podcast):
    """Run modify_feed for the current feed/output settings and persist the
    served RSS. Both feed_cap and processed_only resolve per-feed override ->
    global default -> hard fallback via the database mixin.
    """
    feed_cap = db.get_max_episodes_for_podcast(slug, podcast=podcast)
    extra_episodes = db.get_processed_episodes_for_feed(podcast['id'])

    # When the resolved value is True, hide upstream entries that have not
    # finished processing so auto-downloading clients don't hit 503.
    processed_only = db.is_only_expose_processed_for_podcast(slug, podcast=podcast)
    processed_ids = None
    if processed_only:
        statuses, _ = db.get_episode_statuses_for_podcast(slug)
        processed_ids = {eid for eid, status in statuses.items()
                         if status == 'processed'}

    watermark_artwork = db.get_setting_bool('artwork_watermark_enabled', False)
    # None while feed auth is disabled, so serving reverts to keyless URLs
    # even though the stored key is retained for re-enable.
    feed_auth_key = active_feed_key(db)
    chapter_notes = chapter_notes_for(db, podcast)
    # 'hide' drops title-blacklisted episodes from the served feed entirely;
    # 'serve_original'/NULL (the default) leaves them in place.
    hide_title_patterns = None
    if (podcast or {}).get('title_skip_action') == 'hide':
        hide_title_patterns = podcast.get('title_skip_patterns')
    modified_rss = rss_parser.modify_feed(feed_content, slug, storage=storage,
                                          max_episodes=feed_cap,
                                          extra_episodes=extra_episodes,
                                          processed_only=processed_only,
                                          processed_episode_ids=processed_ids,
                                          parsed_feed=parsed_feed,
                                          title_override=(podcast or {}).get('title_override'),
                                          watermark_artwork=watermark_artwork,
                                          feed_auth_key=feed_auth_key,
                                          own_episode_guids=(podcast or {}).get('own_episode_guids'),
                                          hide_title_patterns=hide_title_patterns,
                                          chapter_notes=chapter_notes)
    storage.save_rss(slug, modified_rss)
    db.update_podcast(slug, last_checked_at=utc_now_iso())
    # A re-render means the upstream feed moved, so any episode lookups
    # pinned from the old copy (URL, title, artwork) are now suspect.
    invalidate_episode_lookup_cache(slug)


def rebuild_served_rss(slug, podcast=None):
    """Re-render one feed's served RSS with the current URL settings (feed
    auth key, cover badge, BASE_URL). Fetches the upstream source feed for
    fresh content but never re-discovers or queues episodes, so it cannot
    trigger processing or touch episode rows/stats. Returns True on success.
    """
    podcast = podcast or db.get_podcast_by_slug(slug)
    if is_local_feed(podcast):
        return rebuild_local_feed(slug, podcast)
    if is_recents_feed(podcast):
        return rebuild_recents_feed(podcast)
    if not podcast or not podcast.get('source_url'):
        return False
    try:
        feed_content = rss_parser.fetch_feed(podcast['source_url'])
        if not feed_content:
            return False
        parsed_feed = rss_parser.parse_feed(feed_content, source=slug)
        _build_and_save_served_rss(slug, feed_content, parsed_feed, podcast)
        return True
    except Exception as e:
        refresh_logger.warning(f"[{slug}] served RSS rebuild failed: {e}")
        return False


def refresh_feed_artwork(slug, podcast=None):
    """Re-pull a feed's cover art and rebuild its served RSS so the cover-art
    badge setting (issue #420) takes effect -- without re-discovering or queuing
    episodes (so it never triggers processing). Returns True on success.
    """
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast or not podcast.get('source_url'):
        return False
    if is_local_feed(podcast):
        # Local artwork is uploaded by the operator, never fetched upstream.
        return False
    try:
        # Re-derive candidates from the live feed so a manual refresh can
        # recover from a preferred candidate that has started 404ing, not
        # just re-fetch the URL already on the row. Falls back to the
        # stored URL if the feed can't be fetched.
        candidates = []
        try:
            feed_content = rss_parser.fetch_feed(podcast['source_url'])
            if feed_content:
                candidates = rss_parser.extract_podcast_artwork_url(feed_content)
        except Exception as e:
            refresh_logger.warning(f"[{slug}] artwork candidate fetch failed: {e}")
        if not candidates and podcast.get('artwork_url'):
            candidates = [podcast['artwork_url']]
        # force, because the guard reads the same URL back off the row and
        # would always match; without it this only cleared the badge variant.
        # Then drop the cached badge so it recomposites with the current
        # badge rendering even when the cover itself has not changed.
        if candidates:
            storage.download_artwork(slug, candidates, force=True)
        storage.clear_watermark_cache(slug)
    except Exception as e:
        refresh_logger.warning(f"[{slug}] artwork refresh failed: {e}")
        return False
    return rebuild_served_rss(slug, podcast)


def refresh_all_artwork():
    """Re-pull every feed's cover and rebuild its served RSS so the cover-art
    badge setting applies. Returns the number of feeds refreshed.
    """
    feed_map = get_feed_map()
    count = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(refresh_feed_artwork, slug): slug
                   for slug in feed_map}
        for future in as_completed(futures):
            try:
                if future.result():
                    count += 1
            except Exception as e:
                refresh_logger.error(f"[{futures[future]}] artwork refresh failed: {e}")
    return count


def rebuild_all_served_feeds():
    """Re-render every feed's served RSS with the current URL settings (feed
    auth key, cover badge, BASE_URL). Same no-processing guarantee as
    rebuild_served_rss. Returns the number of feeds rebuilt.
    """
    feed_map = get_feed_map()
    count = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(rebuild_served_rss, slug): slug
                   for slug in feed_map}
        for future in as_completed(futures):
            try:
                if future.result():
                    count += 1
            except Exception as e:
                refresh_logger.error(f"[{futures[future]}] served RSS rebuild failed: {e}")
    return count
