"""Feed management: get_feed_map, invalidate_feed_cache, refresh_rss_feed, refresh_all_feeds."""
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from config import (
    FEED_REFRESH_FAILURE_ALERT_THRESHOLD,
    FEED_REFRESH_FAILURE_COUNT_INTERVAL,
)

from database.episodes import normalize_published_at
from utils.http import safe_url_for_log
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


def refresh_rss_feed(slug: str, feed_url: str, force: bool = False):
    """Refresh RSS feed for a podcast.

    Args:
        slug: Podcast slug
        feed_url: URL of the original RSS feed
        force: If True, bypass conditional GET (ETag/Last-Modified) to force full fetch.
               Use this when the RSS cache was deleted and needs regeneration.
               Also bypasses the refresh-attempt throttle.
    """
    if not force and _refresh_coalesce.get(slug) is not None:
        refresh_logger.debug(f"[{slug}] Skipping refresh (recent attempt within coalesce window)")
        return None
    _refresh_coalesce.set(slug, True)

    try:
        # Get podcast name and etag for conditional fetch
        podcast = db.get_podcast_by_slug(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

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

        feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
            feed_url,
            etag=existing_etag,
            last_modified=existing_last_modified
        )

        # Handle 304 Not Modified - feed hasn't changed
        if feed_content is None and (new_etag or new_last_modified):
            # If no episodes exist yet (pre-v1.0.41 feed), force full fetch for initial discovery
            _, discovered_count = db.get_episodes(slug, status='discovered', limit=1)
            if discovered_count > 0:
                # Even on 304, ensure artwork is cached (may be missing after DB restore)
                podcast = db.get_podcast_by_slug(slug)
                if podcast and not podcast.get('artwork_cached'):
                    refresh_logger.info(f"[{slug}] Feed unchanged (304) but artwork missing, forcing full fetch")
                    feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
                        feed_url, etag=None, last_modified=None
                    )
                else:
                    cached_rss = storage.get_rss(slug)
                    rss_stale = not cached_rss or any(
                        ep['episode_id'] not in cached_rss
                        for ep in db.get_processed_episodes_for_feed(podcast['id'])
                    )
                    if rss_stale:
                        refresh_logger.info(
                            f"[{slug}] Feed unchanged (304) but RSS cache stale, forcing full fetch"
                        )
                        feed_content, new_etag, new_last_modified = rss_parser.fetch_feed_conditional(
                            feed_url, etag=None, last_modified=None
                        )
                    else:
                        refresh_logger.debug(f"[{slug}] Feed unchanged (304), skipping refresh")
                        db.update_podcast(slug, last_checked_at=utc_now_iso())
                        _record_refresh_success(slug)
                        status_service.complete_feed_refresh(slug, 0)
                        return True
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
            _record_refresh_failure(
                slug, 'Failed to fetch RSS feed (unreachable, invalid '
                      'response, or blocked)', podcast=podcast)
            status_service.complete_feed_refresh(slug, 0)
            return False

        # Parse feed to extract metadata. A body that yields neither channel
        # metadata nor entries AND tripped the parser (bozo) is an origin
        # failure (error page served with an RSS content type), not a
        # success -- treating it as success would reset the failure counter
        # mid-outage. A clean parse of an empty placeholder feed still
        # counts as success.
        parsed_feed = rss_parser.parse_feed(feed_content)
        if not parsed_feed or (not parsed_feed.feed and not parsed_feed.entries
                               and getattr(parsed_feed, 'bozo', False)):
            refresh_logger.error(f"[{slug}] Fetched feed could not be parsed as RSS")
            _record_refresh_failure(
                slug, 'Fetched feed could not be parsed as RSS (the URL may '
                      'be returning an error page)', podcast=podcast)
            status_service.complete_feed_refresh(slug, 0)
            return False
        if parsed_feed and parsed_feed.feed:
            title = parsed_feed.feed.get('title')
            description = parsed_feed.feed.get('description', '')[:500]

            # Extract artwork URL from RAW xml (feedparser corrupts the
            # channel image with the last per-episode itunes:image it sees).
            artwork_url = rss_parser.extract_podcast_artwork_url(feed_content)

            # Update podcast metadata (and ETag if available) in a single DB call
            update_kwargs = dict(
                title=title,
                description=description,
                artwork_url=artwork_url,
                last_checked_at=utc_now_iso()
            )
            # On force=True, always overwrite the stored ETag/Last-Modified --
            # even with None -- so a server that drops the header on this
            # response can't cause the next conditional GET to send a stale
            # validator and get a false 304.
            if new_etag or new_last_modified or force:
                update_kwargs['etag'] = new_etag
                update_kwargs['last_modified_header'] = new_last_modified
            db.update_podcast(slug, **update_kwargs)

            # Map iTunes categories to MinusPod vocabulary tags, then refresh the
            # RSS layer of the podcast's tags. set_podcast_tags also folds in
            # episode-level tags and the user_tags layer.
            try:
                from utils.community_tags import map_itunes_category
                raw_cats = rss_parser.extract_podcast_categories(parsed_feed)
                rss_tags = sorted({
                    tag for cat in raw_cats
                    if (tag := map_itunes_category(cat))
                })
                db.set_podcast_tags(slug, rss_tags=rss_tags)
            except Exception as e:
                refresh_logger.warning(f"[{slug}] iTunes category mapping failed: {e}")

            # Detect DAI platform and network from feed metadata
            feed_author = parsed_feed.feed.get('author', '')
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

            # Download artwork if available
            if artwork_url:
                storage.download_artwork(slug, artwork_url)

        # Discover all episodes from the feed (upsert as 'discovered').
        # Pass parsed_feed so extract_episodes does not re-parse the same
        # XML we already parsed above.
        all_episodes = rss_parser.extract_episodes(feed_content, parsed_feed=parsed_feed)
        inserted = db.bulk_upsert_discovered_episodes(slug, all_episodes)
        if inserted > 0:
            refresh_logger.info(f"[{slug}] Discovered {inserted} new episode(s)")

        # Queue new episodes for auto-processing if enabled
        # Only queue episodes published within the last 48 hours to avoid processing entire backlog
        if db.is_auto_process_enabled_for_podcast(slug):
            queued_count = 0
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=48)

            # Bulk-load episode statuses to avoid N+1 queries
            ep_statuses, title_date_map = db.get_episode_statuses_for_podcast(slug)

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
                    published_str = ep.get('published', '')
                    if published_str:
                        try:
                            # RSS dates are typically RFC 2822 format
                            pub_date = parsedate_to_datetime(published_str)
                            # Ensure timezone-aware for comparison
                            if pub_date.tzinfo is None:
                                pub_date = pub_date.replace(tzinfo=timezone.utc)
                            is_recent = pub_date >= cutoff_time
                        except (ValueError, TypeError):
                            # If we can't parse the date, skip this episode for auto-process
                            refresh_logger.debug(f"[{slug}] Could not parse date for episode: {ep.get('title')}")
                            is_recent = False

                    if is_recent:
                        # New recent episode - queue for processing
                        # iso_published already calculated above for deduplication check
                        queue_id = db.queue_episode_for_processing(
                            slug, ep['id'], ep['url'], ep.get('title'), iso_published,
                            ep.get('description')
                        )
                        if queue_id:
                            queued_count += 1
                            refresh_logger.debug(f"[{slug}] Queued recent episode: {ep.get('title')}")

            if queued_count > 0:
                refresh_logger.info(f"[{slug}] Queued {queued_count} new episode(s) for auto-processing")

        # Rebuild and persist the served RSS for the current feed/output settings.
        _build_and_save_served_rss(slug, feed_content, parsed_feed, podcast)

        refresh_logger.debug(f"[{slug}] RSS refresh complete")
        _record_refresh_success(slug)
        status_service.complete_feed_refresh(slug, 0)
        return True
    except Exception as e:
        # Deliberately NOT recorded as a feed failure: exceptions here are
        # internal faults (DB locked, disk full, downstream bugs), and the
        # Feed Refresh Failed alert blames the publisher's feed. Origin
        # failures are recorded at the fetch/parse boundaries above.
        refresh_logger.error(f"[{slug}] RSS refresh failed: {e}")
        status_service.remove_feed_refresh(slug)
        return False


def refresh_all_feeds(force: bool = False):
    """Refresh all RSS feeds in parallel.

    Args:
        force: If True, bypass each feed's ETag and 30s refresh-coalesce window
               so every feed is fully re-fetched. Used by the UI Force Refresh
               All action; the 15-minute background scheduler always calls with
               force=False.
    """
    try:
        refresh_logger.info(f"Refreshing all RSS feeds (force={force})")

        feed_map = get_feed_map()

        # Parallelize feed refresh with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(refresh_rss_feed, slug, feed_info['in'], force): slug
                for slug, feed_info in feed_map.items()
            }
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    future.result()
                except Exception as e:
                    refresh_logger.error(f"[{slug}] Feed refresh failed: {e}")

        refresh_logger.info(f"RSS refresh complete for {len(feed_map)} feeds")
        # Stamp when the all-feeds pass finished; the dashboard shows this
        # as the global "Updated" time.
        db.set_setting('feeds_last_refresh_completed_at', utc_now_iso())
        return True
    except Exception as e:
        refresh_logger.error(f"RSS refresh failed: {e}")
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
    modified_rss = rss_parser.modify_feed(feed_content, slug, storage=storage,
                                          max_episodes=feed_cap,
                                          extra_episodes=extra_episodes,
                                          processed_only=processed_only,
                                          processed_episode_ids=processed_ids,
                                          parsed_feed=parsed_feed,
                                          title_override=(podcast or {}).get('title_override'),
                                          watermark_artwork=watermark_artwork,
                                          feed_auth_key=feed_auth_key)
    storage.save_rss(slug, modified_rss)
    db.update_podcast(slug, last_checked_at=utc_now_iso())


def rebuild_served_rss(slug, podcast=None):
    """Re-render one feed's served RSS with the current URL settings (feed
    auth key, cover badge, BASE_URL). Fetches the upstream source feed for
    fresh content but never re-discovers or queues episodes, so it cannot
    trigger processing or touch episode rows/stats. Returns True on success.
    """
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast or not podcast.get('source_url'):
        return False
    try:
        feed_content = rss_parser.fetch_feed(podcast['source_url'])
        if not feed_content:
            return False
        parsed_feed = rss_parser.parse_feed(feed_content)
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
    try:
        # Re-pull the source cover (a no-op when it is already cached, which
        # skips save_artwork), then drop the cached badge variant explicitly so
        # it recomposites with the current badge rendering and toggle even when
        # the upstream cover itself has not changed.
        if podcast.get('artwork_url'):
            storage.download_artwork(slug, podcast['artwork_url'])
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
