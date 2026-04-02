"""Feed management: get_feed_map, invalidate_feed_cache, refresh_rss_feed, refresh_all_feeds."""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from database.episodes import normalize_published_at
from utils.time import utc_now_iso

from slugify import slugify

from main_app.cache import TTLCache

refresh_logger = logging.getLogger('podcast.refresh')
feed_logger = logging.getLogger('podcast.feed')

# Initialize caches for performance
_feed_cache = TTLCache(ttl_seconds=30)
_parsed_feeds_cache = TTLCache(ttl_seconds=60)


def _get_components():
    """Late import to avoid circular imports at module level."""
    from main_app import db, rss_parser, storage, status_service, pattern_service
    return db, rss_parser, storage, status_service, pattern_service


def get_feed_map():
    """Get feed map from database, with TTL caching."""
    cached = _feed_cache.get('all_feeds')
    if cached is not None:
        return cached

    db, _, _, _, _ = _get_components()
    feeds = db.get_feeds_config()
    result = {slugify(feed['out'].strip('/')): feed for feed in feeds}
    _feed_cache.set('all_feeds', result)
    return result


def invalidate_feed_cache():
    """Invalidate feed cache after any feed modification."""
    _feed_cache.invalidate('all_feeds')


def get_parsed_feed(slug: str, source_url: str):
    """Get cached parsed feed or parse fresh.

    Reduces redundant RSS fetching and parsing by caching parsed feeds
    for 60 seconds.
    """
    cached = _parsed_feeds_cache.get(slug)
    if cached is not None:
        refresh_logger.debug(f"[{slug}] Using cached parsed feed")
        return cached

    _, rss_parser, _, _, _ = _get_components()
    feed_content = rss_parser.fetch_feed(source_url)
    if feed_content:
        parsed = rss_parser.parse_feed(feed_content)
        _parsed_feeds_cache.set(slug, parsed)
        return parsed
    return None


def refresh_rss_feed(slug: str, feed_url: str, force: bool = False):
    """Refresh RSS feed for a podcast.

    Args:
        slug: Podcast slug
        feed_url: URL of the original RSS feed
        force: If True, bypass conditional GET (ETag/Last-Modified) to force full fetch.
               Use this when the RSS cache was deleted and needs regeneration.
    """
    db, rss_parser, storage, status_service, pattern_service = _get_components()
    try:
        # Get podcast name and etag for conditional fetch
        podcast = db.get_podcast_by_slug(slug)
        podcast_name = podcast.get('title', slug) if podcast else slug

        # Track feed refresh in status service
        status_service.start_feed_refresh(slug, podcast_name)

        refresh_logger.info(f"[{slug}] Starting RSS refresh from: {feed_url}")

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
                        refresh_logger.info(f"[{slug}] Feed unchanged (304), skipping refresh")
                        db.update_podcast(slug, last_checked_at=utc_now_iso())
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
            status_service.complete_feed_refresh(slug, 0)
            return False

        # Parse feed to extract metadata
        parsed_feed = rss_parser.parse_feed(feed_content)
        if parsed_feed and parsed_feed.feed:
            title = parsed_feed.feed.get('title')
            description = parsed_feed.feed.get('description', '')[:500]

            # Extract artwork URL
            artwork_url = rss_parser.extract_podcast_artwork_url(parsed_feed)

            # Update podcast metadata (and ETag if available) in a single DB call
            update_kwargs = dict(
                title=title,
                description=description,
                artwork_url=artwork_url,
                last_checked_at=utc_now_iso()
            )
            if new_etag or new_last_modified:
                update_kwargs['etag'] = new_etag
                update_kwargs['last_modified_header'] = new_last_modified
            db.update_podcast(slug, **update_kwargs)

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
                refresh_logger.info(
                    f"[{slug}] Detected: platform={network_info.get('dai_platform')}, "
                    f"network={network_info.get('network_id')}"
                )

            # Download artwork if available
            if artwork_url:
                storage.download_artwork(slug, artwork_url)

        # Discover all episodes from the feed (upsert as 'discovered')
        all_episodes = rss_parser.extract_episodes(feed_content)
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

        # Modify feed URLs (pass storage to include Podcasting 2.0 tags)
        feed_cap = podcast.get('max_episodes') or 300
        extra_episodes = db.get_processed_episodes_for_feed(podcast['id'])
        modified_rss = rss_parser.modify_feed(feed_content, slug, storage=storage,
                                               max_episodes=feed_cap,
                                               extra_episodes=extra_episodes)

        # Save modified RSS
        storage.save_rss(slug, modified_rss)

        # Update last_checked timestamp
        db.update_podcast(slug, last_checked_at=utc_now_iso())

        refresh_logger.info(f"[{slug}] RSS refresh complete")
        status_service.complete_feed_refresh(slug, 0)
        return True
    except Exception as e:
        refresh_logger.error(f"[{slug}] RSS refresh failed: {e}")
        status_service.remove_feed_refresh(slug)
        return False


def refresh_all_feeds():
    """Refresh all RSS feeds in parallel."""
    try:
        refresh_logger.info("Refreshing all RSS feeds")

        feed_map = get_feed_map()

        # Parallelize feed refresh with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(refresh_rss_feed, slug, feed_info['in']): slug
                for slug, feed_info in feed_map.items()
            }
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    future.result()
                except Exception as e:
                    refresh_logger.error(f"[{slug}] Feed refresh failed: {e}")

        refresh_logger.info(f"RSS refresh complete for {len(feed_map)} feeds")
        return True
    except Exception as e:
        refresh_logger.error(f"RSS refresh failed: {e}")
        return False
