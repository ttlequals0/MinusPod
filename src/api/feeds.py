"""Feed routes: /feeds/* endpoints."""
import logging
import os
import time
import xml.etree.ElementTree as ET  # defusedxml has no SubElement/tostring, so keep ET for OPML export only
from typing import Optional

from flask import request, Response

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage,
    _serialize_auto_process, _deserialize_auto_process,
)
from utils.url import validate_url, SSRFError

logger = logging.getLogger('podcast.api')


# ========== Feed Endpoints ==========

@api.route('/feeds', methods=['GET'])
@log_request
def list_feeds():
    """List all podcast feeds with metadata."""
    db = get_database()
    storage = get_storage()

    podcasts = db.get_all_podcasts()

    feeds = []
    for podcast in podcasts:
        # Build feed URL
        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
        feed_url = f"{base_url}/{podcast['slug']}"

        feeds.append({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'sourceUrl': podcast['source_url'],
            'feedUrl': feed_url,
            'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
            'episodeCount': podcast.get('episode_count', 0),
            'processedCount': podcast.get('processed_count', 0),
            'lastRefreshed': podcast.get('last_checked_at'),
            'createdAt': podcast.get('created_at'),
            'lastEpisodeDate': podcast.get('last_episode_date'),
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform'),
            'maxEpisodes': podcast.get('max_episodes') or 300,
        })

    return json_response({'feeds': feeds})


@api.route('/feeds', methods=['POST'])
@limiter.limit("3 per minute")
@log_request
def add_feed():
    """Add a new podcast feed.

    OPML bulk-import lives on its own endpoint with its own limiter, so the
    feeds POST limit is tuned for interactive use.
    """
    data = request.get_json()

    if not data or 'sourceUrl' not in data:
        logger.warning("Missing sourceUrl in POST /feeds request")
        return error_response('sourceUrl is required', 400)

    source_url = data['sourceUrl'].strip()
    if not source_url:
        return error_response('sourceUrl cannot be empty', 400)

    # SSRF protection: validate URL before any outbound request
    try:
        validate_url(source_url)
    except SSRFError as e:
        logger.warning(f"SSRF blocked in add_feed: {e} (url={source_url})")
        return error_response(f'Invalid feed URL: {e}', 400)

    # Generate slug from podcast name or use provided slug
    slug = data.get('slug', '').strip()
    if not slug:
        from slugify import slugify as make_slug
        from rss_parser import RSSParser

        # Two attempts with a short gap: some hosts (e.g. Buzzsprout) 403 the
        # first fetch from a new client but serve the retry. Circuit breaker
        # inside fetch_feed still gates genuinely broken feeds.
        rss_parser = RSSParser()
        feed_content = None
        for attempt in (1, 2):
            feed_content = rss_parser.fetch_feed(source_url)
            if feed_content:
                break
            if attempt == 1:
                time.sleep(0.5)

        if feed_content:
            parsed_feed = rss_parser.parse_feed(feed_content)
            if parsed_feed and parsed_feed.feed:
                title = parsed_feed.feed.get('title', '')
                if title:
                    slug = make_slug(title)

    if not slug:
        return error_response(
            "Could not fetch podcast title from RSS. "
            "Please provide a 'slug' in the request.",
            400,
        )

    db = get_database()

    # Check if slug already exists
    existing = db.get_podcast_by_slug(slug)
    if existing:
        return error_response(f'Feed with slug "{slug}" already exists', 409)

    # Create podcast
    try:
        podcast_id = db.create_podcast(slug, source_url)
        logger.info(f"Created new feed: {slug} -> {source_url}")

        # Apply auto-process override if provided (before initial refresh)
        auto_process_override = data.get('autoProcessOverride')
        db_value = _serialize_auto_process(auto_process_override)
        if db_value is not None:
            db.update_podcast(slug, auto_process_override=db_value)

        # Apply max_episodes if provided
        max_ep = data.get('maxEpisodes')
        if max_ep is not None:
            max_ep = max(10, min(int(max_ep), 500))
            db.update_podcast(slug, max_episodes=max_ep)

        # Invalidate feed cache since we added a new feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Trigger initial refresh in background
        try:
            from main_app.feeds import refresh_rss_feed
            refresh_rss_feed(slug, source_url)
        except Exception as e:
            logger.warning(f"Initial refresh failed for {slug}: {e}")

        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')

        return json_response({
            'slug': slug,
            'sourceUrl': source_url,
            'feedUrl': f"{base_url}/{slug}",
            'message': 'Feed added successfully'
        }, 201)

    except Exception as e:
        logger.exception("Failed to add feed")
        return error_response('Failed to add feed', 500)


@api.route('/feeds/import-opml', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def import_opml():
    """Import podcast feeds from an OPML file.

    Accepts a multipart form upload with an 'opml' file field.
    Returns counts of successfully imported and failed feeds.
    """
    import defusedxml.ElementTree as ET

    if 'opml' not in request.files:
        return error_response('No OPML file provided', 400)

    opml_file = request.files['opml']
    if not opml_file.filename:
        return error_response('Empty file name', 400)

    # Check file extension
    if not opml_file.filename.lower().endswith(('.opml', '.xml')):
        return error_response('File must be .opml or .xml', 400)

    try:
        content = opml_file.read().decode('utf-8')
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.error(f"OPML parse error: {e}")
        return error_response('Invalid OPML file format', 400)
    except UnicodeDecodeError as e:
        logger.error(f"OPML encoding error: {e}")
        return error_response('File must be UTF-8 encoded', 400)

    # Find all outline elements with xmlUrl (RSS feeds)
    feeds_found = []
    for outline in root.iter('outline'):
        xml_url = outline.get('xmlUrl')
        if xml_url:
            title = outline.get('text') or outline.get('title') or ''
            feeds_found.append({'url': xml_url, 'title': title})

    if not feeds_found:
        return error_response('No RSS feeds found in OPML file', 400)

    # Import feeds
    db = get_database()
    from slugify import slugify as make_slug
    from rss_parser import RSSParser

    imported = []
    failed = []
    skipped = []

    for feed_info in feeds_found:
        source_url = feed_info['url'].strip()
        title = feed_info['title'].strip()

        # SSRF protection: validate each feed URL
        try:
            validate_url(source_url)
        except SSRFError as e:
            logger.warning(f"SSRF blocked in OPML import: {e} (url={source_url})")
            failed.append({'url': source_url, 'error': f'Invalid URL: {e}'})
            continue

        # Generate slug
        slug = make_slug(title) if title else None

        # If no title, try to fetch from RSS
        if not slug:
            rss_parser = RSSParser()
            try:
                feed_content = rss_parser.fetch_feed(source_url)
                if feed_content:
                    parsed_feed = rss_parser.parse_feed(feed_content)
                    if parsed_feed and parsed_feed.feed:
                        fetched_title = parsed_feed.feed.get('title', '')
                        if fetched_title:
                            slug = make_slug(fetched_title)
            except Exception:
                pass

        # Fallback to URL-based slug
        if not slug:
            from urllib.parse import urlparse
            parsed = urlparse(source_url)
            slug_base = parsed.path.strip('/').split('/')[-1] or parsed.netloc
            slug_base = slug_base.replace('.xml', '').replace('.rss', '')
            slug = make_slug(slug_base) if slug_base else None

        if not slug:
            failed.append({'url': source_url, 'error': 'Could not generate slug'})
            continue

        # Check if slug already exists
        existing = db.get_podcast_by_slug(slug)
        if existing:
            skipped.append({'url': source_url, 'slug': slug, 'reason': 'Already exists'})
            continue

        # Create podcast
        try:
            db.create_podcast(slug, source_url, title or None)
            imported.append({'url': source_url, 'slug': slug})
            logger.info(f"OPML import: Created feed {slug}")
        except Exception as e:
            failed.append({'url': source_url, 'error': 'import failed'})
            logger.error(f"OPML import failed for {source_url}: {e}")

    # Invalidate feed cache
    if imported:
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Trigger refresh for imported feeds
        try:
            from main_app.feeds import refresh_rss_feed
            for feed in imported[:5]:  # Limit to first 5 to avoid overload
                podcast = db.get_podcast_by_slug(feed['slug'])
                if podcast:
                    refresh_rss_feed(feed['slug'], podcast['source_url'])
        except Exception as e:
            logger.warning(f"OPML import: Failed to trigger refreshes: {e}")

    logger.info(
        f"OPML import complete: {len(imported)} imported, "
        f"{len(skipped)} skipped, {len(failed)} failed"
    )

    return json_response({
        'imported': len(imported),
        'skipped': len(skipped),
        'failed': len(failed),
        'feeds': {
            'imported': imported,
            'skipped': skipped,
            'failed': failed
        }
    }, 201 if imported else 200)


@api.route('/feeds/export-opml', methods=['GET'])
@log_request
def export_opml():
    """Export all podcast feeds as an OPML 2.0 file."""
    mode = request.args.get('mode', 'original')
    if mode not in ('original', 'modified'):
        return error_response('mode must be "original" or "modified"', 400)

    db = get_database()
    podcasts = db.get_all_podcasts()

    if mode == 'modified':
        base_url = os.environ.get('BASE_URL', 'http://localhost:8000').rstrip('/')

    opml = ET.Element('opml', version='2.0')
    head = ET.SubElement(opml, 'head')
    ET.SubElement(head, 'title').text = 'MinusPod Feeds'
    body = ET.SubElement(opml, 'body')

    for podcast in podcasts:
        title = podcast.get('title') or podcast.get('slug', '')
        if mode == 'modified':
            feed_url = f"{base_url}/{podcast['slug']}"
        else:
            feed_url = podcast.get('source_url', '')
        ET.SubElement(body, 'outline',
                      type='rss',
                      text=title,
                      title=title,
                      xmlUrl=feed_url)

    xml_bytes = ET.tostring(opml, encoding='unicode', xml_declaration=False)
    xml_output = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes

    filename = 'minuspod-feeds.opml' if mode == 'original' else 'minuspod-feeds-modified.opml'
    logger.info(f"Exported {len(podcasts)} feeds as OPML (mode={mode})")

    return Response(
        xml_output,
        mimetype='application/xml',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    )


@api.route('/feeds/<slug>', methods=['GET'])
@log_request
def get_feed(slug):
    """Get a single podcast feed by slug."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
    feed_url = f"{base_url}/{slug}"

    # Convert auto_process_override from string to boolean/null
    auto_process_override_result = _deserialize_auto_process(podcast.get('auto_process_override'))

    return json_response({
        'slug': podcast['slug'],
        'title': podcast['title'] or podcast['slug'],
        'description': podcast.get('description'),
        'sourceUrl': podcast['source_url'],
        'feedUrl': feed_url,
        'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
        'episodeCount': podcast.get('episode_count', 0),
        'processedCount': podcast.get('processed_count', 0),
        'lastRefreshed': podcast.get('last_checked_at'),
        'createdAt': podcast.get('created_at'),
        'networkId': podcast.get('network_id'),
        'daiPlatform': podcast.get('dai_platform'),
        'networkIdOverride': podcast.get('network_id_override'),
        'autoProcessOverride': auto_process_override_result,
        'maxEpisodes': podcast.get('max_episodes') or 300,
    })


@api.route('/feeds/<slug>', methods=['PATCH'])
@log_request
def update_feed(slug):
    """Update podcast feed settings (network, DAI platform, etc.)."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    # Map API field names to database field names
    field_map = {
        'networkId': 'network_id',
        'daiPlatform': 'dai_platform',
        'networkIdOverride': 'network_id_override',
        'title': 'title',
        'description': 'description'
    }

    updates = {}
    for api_field, db_field in field_map.items():
        if api_field in data:
            updates[db_field] = data[api_field]

    # Handle auto-process override specially (can be null, true, or false).
    # None passes through to DB as NULL (clears the override) -- unlike add_feed
    # which guards with `if db_value is not None` since there's nothing to clear yet.
    if 'autoProcessOverride' in data:
        updates['auto_process_override'] = _serialize_auto_process(data['autoProcessOverride'])

    # Handle maxEpisodes
    if 'maxEpisodes' in data:
        max_ep = data['maxEpisodes']
        if max_ep is not None:
            max_ep = max(10, min(int(max_ep), 500))
        updates['max_episodes'] = max_ep

    if not updates:
        return error_response('No valid fields to update', 400)

    try:
        db.update_podcast(slug, **updates)
        logger.info(f"Updated feed {slug}: {updates}")

        # Invalidate feed cache since we modified a feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Return updated feed data
        podcast = db.get_podcast_by_slug(slug)
        base_url = os.environ.get('BASE_URL', 'http://localhost:8000')

        # Trigger refresh if maxEpisodes changed (to regenerate modified RSS)
        if 'max_episodes' in updates:
            try:
                from main_app.feeds import refresh_rss_feed
                refresh_rss_feed(slug, podcast['source_url'])
            except Exception as e:
                logger.warning(f"Feed refresh after maxEpisodes change failed for {slug}: {e}")

        return json_response({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform'),
            'networkIdOverride': podcast.get('network_id_override'),
            'maxEpisodes': podcast.get('max_episodes') or 300,
            'feedUrl': f"{base_url}/{slug}"
        })
    except Exception as e:
        logger.exception(f"Failed to update feed {slug}")
        return error_response('Failed to update feed', 500)


@api.route('/feeds/<slug>', methods=['DELETE'])
@log_request
def delete_feed(slug):
    """Delete a podcast feed and all associated data."""
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    try:
        # Delete from database (cascade deletes episodes)
        db.delete_podcast(slug)

        # Invalidate feed cache since we deleted a feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Delete files
        storage.cleanup_podcast_dir(slug)

        logger.info(f"Deleted feed: {slug}")
        return json_response({'message': 'Feed deleted', 'slug': slug})

    except Exception as e:
        logger.exception(f"Failed to delete feed {slug}")
        return error_response('Failed to delete feed', 500)


@api.route('/feeds/<slug>/refresh', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def refresh_feed(slug):
    """Refresh a single podcast feed.

    Optional request body:
    {
        "force": true  // Force full refresh, bypassing conditional GET (304)
    }
    """
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    if not podcast.get('source_url'):
        return error_response('Feed has no source URL', 400)

    # Check for force parameter
    force = False
    data = request.get_json(silent=True)
    if data and data.get('force'):
        force = True
        # Clear ETag to force non-conditional fetch
        db.update_podcast_etag(slug, None, None)
        logger.info(f"Force refresh requested for {slug}, cleared ETag")

    try:
        from main_app.feeds import refresh_rss_feed
        refresh_rss_feed(slug, podcast['source_url'])

        # Get updated info
        podcast = db.get_podcast_by_slug(slug)
        episodes, total = db.get_episodes(slug)

        logger.info(f"Refreshed feed: {slug}")
        return json_response({
            'slug': slug,
            'message': 'Feed refreshed',
            'episodeCount': total,
            'lastRefreshed': podcast.get('last_checked_at')
        })

    except Exception as e:
        logger.exception(f"Failed to refresh feed {slug}")
        return error_response('Failed to refresh feed', 500)


@api.route('/feeds/refresh', methods=['POST'])
@limiter.limit("2 per minute")
@log_request
def refresh_all_feeds():
    """Refresh all podcast feeds.

    Optional request body:
    {
        "force": true  // Force full refresh for all feeds, bypassing conditional GET (304)
    }
    """
    try:
        db = get_database()

        # Check for force parameter
        data = request.get_json(silent=True)
        if data and data.get('force'):
            # Clear all ETags to force non-conditional fetch
            podcasts = db.get_all_podcasts()
            for podcast in podcasts:
                db.update_podcast_etag(podcast['slug'], None, None)
            logger.info(f"Force refresh requested, cleared ETags for {len(podcasts)} feeds")

        from main_app.feeds import refresh_all_feeds as do_refresh
        do_refresh()

        podcasts = db.get_all_podcasts()

        logger.info("Refreshed all feeds")
        return json_response({
            'message': 'All feeds refreshed',
            'feedCount': len(podcasts)
        })

    except Exception as e:
        logger.exception("Failed to refresh all feeds")
        return error_response('Failed to refresh feeds', 500)


def _extract_artwork_url_from_feed(source_url: str) -> Optional[str]:
    """Extract artwork URL from a podcast's RSS feed."""
    try:
        from rss_parser import RSSParser
        rss_parser = RSSParser()
        feed_content = rss_parser.fetch_feed(source_url)
        if not feed_content:
            return None
        parsed_feed = rss_parser.parse_feed(feed_content)
        return rss_parser.extract_podcast_artwork_url(parsed_feed)
    except Exception as e:
        logger.warning(f"Failed to extract artwork URL from feed: {e}")
    return None


@api.route('/feeds/<slug>/artwork', methods=['GET'])
@log_request
def get_artwork(slug):
    """Get cached artwork for a podcast."""
    storage = get_storage()

    artwork = storage.get_artwork(slug)
    if not artwork:
        # Artwork file missing on disk -- try to recover
        db = get_database()
        podcast = db.get_podcast_by_slug(slug)
        if podcast:
            # Clear stale artwork_cached flag so download_artwork won't short-circuit
            if podcast.get('artwork_cached'):
                db.update_podcast(slug, artwork_cached=0)

            artwork_url = podcast.get('artwork_url')
            # If artwork_url is NULL or empty string, (re-)extract from the source feed.
            # Previous empty-string sentinels may be stale (feed updated, extraction improved).
            if not artwork_url and podcast.get('source_url'):
                artwork_url = _extract_artwork_url_from_feed(podcast['source_url'])
                if artwork_url:
                    db.update_podcast(slug, artwork_url=artwork_url)
            if artwork_url:
                storage.download_artwork(slug, artwork_url)
                artwork = storage.get_artwork(slug)

    if not artwork:
        return error_response('Artwork not found', 404)

    image_data, content_type = artwork
    # content_type was validated by magic-number check on write; tell the
    # browser not to sniff it and deny any script loading from this
    # response even if a downstream intermediary rewrites the type.
    response = Response(image_data, mimetype=content_type)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'"
    return response
