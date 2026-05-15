"""Pattern routes: /patterns/* endpoints and corrections."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.time import utc_now_iso, parse_iso_datetime
from sponsor_normalize import get_or_create_known_sponsor

from flask import request

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage,
    extract_transcript_segment, extract_sponsor_from_text,
    _find_similar_pattern,
)

logger = logging.getLogger('podcast.api')


# ========== Pattern & Correction Endpoints ==========

@api.route('/patterns', methods=['GET'])
@log_request
def list_patterns():
    """List all ad patterns with optional filtering.

    Query params:
      scope, podcast_id, network_id, active (bool, default true),
      source (one of 'local', 'community', 'imported')
    """
    from utils.community_tags import PATTERN_SOURCES
    db = get_database()

    scope = request.args.get('scope')
    podcast_id = request.args.get('podcast_id')
    network_id = request.args.get('network_id')
    active_only = request.args.get('active', 'true').lower() == 'true'
    source = request.args.get('source')
    if source and source not in PATTERN_SOURCES:
        source = None  # ignore garbage values rather than 400; preserves prior behavior

    patterns = db.get_ad_patterns(
        scope=scope,
        podcast_id=podcast_id,
        network_id=network_id,
        active_only=active_only,
        source=source,
    )

    return json_response({'patterns': patterns})


@api.route('/patterns/stats', methods=['GET'])
@log_request
def get_pattern_stats():
    """Get pattern statistics for audit purposes."""
    db = get_database()
    patterns = db.get_ad_patterns(active_only=False)

    # Calculate stats
    stats = {
        'total': len(patterns),
        'active': 0,
        'inactive': 0,
        'by_scope': {'global': 0, 'network': 0, 'podcast': 0},
        'no_sponsor': 0,
        'never_matched': 0,
        'stale_count': 0,
        'high_false_positive_count': 0,
        'stale_patterns': [],
        'no_sponsor_patterns': [],
        'high_false_positive_patterns': [],
    }

    stale_threshold = datetime.now(timezone.utc) - timedelta(days=30)

    for p in patterns:
        # Active/inactive
        if p.get('is_active', True):
            stats['active'] += 1
        else:
            stats['inactive'] += 1

        # By scope
        scope = p.get('scope', 'podcast')
        if scope in stats['by_scope']:
            stats['by_scope'][scope] += 1

        # No sponsor (Unknown)
        if not p.get('sponsor'):
            stats['no_sponsor'] += 1
            stats['no_sponsor_patterns'].append({
                'id': p['id'],
                'scope': p.get('scope'),
                'podcast_name': p.get('podcast_name'),
                'created_at': p.get('created_at'),
                'text_preview': (p.get('text_template') or '')[:100]
            })

        # Never matched
        if p.get('confirmation_count', 0) == 0:
            stats['never_matched'] += 1

        # Stale (not matched in 30+ days)
        last_matched = p.get('last_matched_at')
        if last_matched:
            try:
                last_date = parse_iso_datetime(last_matched)
                if last_date < stale_threshold:
                    stats['stale_count'] += 1
                    stats['stale_patterns'].append({
                        'id': p['id'],
                        'sponsor': p.get('sponsor'),
                        'last_matched_at': last_matched,
                        'confirmation_count': p.get('confirmation_count', 0)
                    })
            except (ValueError, TypeError):
                pass

        # High false positives (more FPs than confirmations)
        fp_count = p.get('false_positive_count', 0)
        conf_count = p.get('confirmation_count', 0)
        if fp_count > 0 and fp_count >= conf_count:
            stats['high_false_positive_count'] += 1
            stats['high_false_positive_patterns'].append({
                'id': p['id'],
                'sponsor': p.get('sponsor'),
                'confirmation_count': conf_count,
                'false_positive_count': fp_count
            })

    # Limit list sizes for response
    stats['stale_patterns'] = stats['stale_patterns'][:20]
    stats['no_sponsor_patterns'] = stats['no_sponsor_patterns'][:20]
    stats['high_false_positive_patterns'] = stats['high_false_positive_patterns'][:20]

    return json_response(stats)


@api.route('/patterns/health', methods=['GET'])
@log_request
def get_pattern_health():
    """Check pattern health - identify contaminated/oversized patterns.

    Returns patterns with text templates that exceed reasonable lengths,
    indicating they likely contain multiple merged ads and will never match.
    """
    db = get_database()
    patterns = db.get_ad_patterns(active_only=True)

    # Thresholds for identifying problematic patterns
    OVERSIZED_THRESHOLD = 2500  # Chars - patterns this large rarely match
    VERY_OVERSIZED_THRESHOLD = 3500  # Chars - almost certainly contaminated

    issues = []
    for p in patterns:
        template = p.get('text_template', '')
        template_len = len(template) if template else 0

        if template_len > OVERSIZED_THRESHOLD:
            severity = 'critical' if template_len > VERY_OVERSIZED_THRESHOLD else 'warning'
            issues.append({
                'id': p['id'],
                'sponsor': p.get('sponsor'),
                'podcast_id': p.get('podcast_id'),
                'podcast_name': p.get('podcast_name'),
                'template_len': template_len,
                'confirmation_count': p.get('confirmation_count', 0),
                'severity': severity,
                'issue': 'oversized',
                'recommendation': 'delete' if severity == 'critical' else 'review'
            })

    # Sort by template_len descending (worst first)
    issues.sort(key=lambda x: x['template_len'], reverse=True)

    healthy_count = len(patterns) - len(issues)
    return json_response({
        'total_patterns': len(patterns),
        'healthy': healthy_count,
        'issues_count': len(issues),
        'critical_count': sum(1 for i in issues if i['severity'] == 'critical'),
        'warning_count': sum(1 for i in issues if i['severity'] == 'warning'),
        'issues': issues[:50]  # Limit response size
    })


@api.route('/patterns/contaminated', methods=['GET'])
@log_request
def get_contaminated_patterns():
    """Find all patterns that have multiple ad transitions and could be split.

    Returns patterns containing multiple ad transition phrases, indicating
    they may contain merged multi-sponsor ads that should be split.
    """
    from text_pattern_matcher import AD_TRANSITION_PHRASES

    db = get_database()
    patterns = db.get_ad_patterns(active_only=True)
    contaminated = []

    for pattern in patterns:
        text = (pattern.get('text_template') or '').lower()
        # Count ad transition phrases
        transition_count = sum(1 for phrase in AD_TRANSITION_PHRASES if phrase in text)

        if transition_count > 1:
            contaminated.append({
                'id': pattern['id'],
                'sponsor': pattern.get('sponsor'),
                'podcast_id': pattern.get('podcast_id'),
                'text_length': len(pattern.get('text_template', '')),
                'transition_count': transition_count,
                'scope': pattern.get('scope')
            })

    return json_response({
        'count': len(contaminated),
        'patterns': contaminated
    })


@api.route('/patterns/<int:pattern_id>/split', methods=['POST'])
@log_request
def split_pattern(pattern_id):
    """Split a contaminated multi-sponsor pattern into separate patterns.

    Uses the TextPatternMatcher.split_pattern() method to detect ad transition
    phrases and create individual single-sponsor patterns. The original pattern
    is disabled after successful split.
    """
    from text_pattern_matcher import TextPatternMatcher

    db = get_database()
    matcher = TextPatternMatcher(db=db)
    new_ids = matcher.split_pattern(pattern_id)

    if not new_ids:
        return error_response(
            f'Pattern {pattern_id} does not need splitting or was not found',
            400
        )

    return json_response({
        'success': True,
        'original_pattern_id': pattern_id,
        'new_pattern_ids': new_ids,
        'message': f'Split into {len(new_ids)} patterns'
    })


@api.route('/patterns/<int:pattern_id>', methods=['GET'])
@log_request
def get_pattern(pattern_id):
    """Get a single pattern by ID."""
    db = get_database()

    pattern = db.get_ad_pattern_by_id(pattern_id)

    if not pattern:
        return error_response('Pattern not found', 404)

    return json_response(pattern)


@api.route('/patterns/<int:pattern_id>', methods=['PUT'])
@log_request
def update_pattern(pattern_id):
    """Update a pattern."""
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('Pattern not found', 404)

    # Allowed fields. Clients still pass `sponsor` (text); we resolve to
    # sponsor_id via the helper so all sponsor writes flow through one place.
    allowed = {'text_template', 'sponsor', 'intro_variants', 'outro_variants',
               'is_active', 'disabled_reason', 'scope'}

    updates = {k: v for k, v in data.items() if k in allowed}

    if 'sponsor' in updates:
        sponsor_text = updates.pop('sponsor')
        if sponsor_text:
            sponsor_id = get_or_create_known_sponsor(db, sponsor_text)
            if sponsor_id is None:
                return error_response('Invalid sponsor name', 400)
            updates['sponsor_id'] = sponsor_id
        else:
            updates['sponsor_id'] = None

    if updates:
        # Auto-protect community patterns from being clobbered by the next
        # auto-sync when the user edits them in the UI.
        from utils.community_tags import PATTERN_SOURCE_COMMUNITY, PATTERN_SOURCE_LOCAL
        if (pattern.get('source') or PATTERN_SOURCE_LOCAL) == PATTERN_SOURCE_COMMUNITY:
            updates.setdefault('protected_from_sync', 1)
        db.update_ad_pattern(pattern_id, **updates)
        return json_response({'message': 'Pattern updated'})

    return error_response('No valid fields provided', 400)


@api.route('/patterns/<int:pattern_id>', methods=['DELETE'])
@log_request
def delete_pattern(pattern_id):
    """Delete a pattern."""
    db = get_database()

    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('Pattern not found', 404)

    db.delete_ad_pattern(pattern_id)
    return json_response({'message': 'Pattern deleted'})


@api.route('/patterns/deduplicate', methods=['POST'])
@log_request
def deduplicate_patterns():
    """Manually trigger pattern deduplication."""
    db = get_database()

    try:
        removed = db.deduplicate_patterns()
        return json_response({
            'message': f'Removed {removed} duplicate patterns',
            'removed_count': removed
        })
    except Exception as e:
        logger.exception("Deduplication failed")
        return error_response('Deduplication failed', 500)


@api.route('/patterns/merge', methods=['POST'])
@log_request
def merge_patterns():
    """Merge multiple patterns into one.

    Request body:
    {
        "keep_id": 123,  // Pattern to keep
        "merge_ids": [124, 125, ...]  // Patterns to merge into keep_id
    }
    """
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    keep_id = data.get('keep_id')
    merge_ids = data.get('merge_ids', [])

    if not keep_id or not merge_ids:
        return error_response('Missing keep_id or merge_ids', 400)

    # Validate patterns exist
    keep_pattern = db.get_ad_pattern_by_id(keep_id)
    if not keep_pattern:
        return error_response(f'Pattern {keep_id} not found', 404)

    for merge_id in merge_ids:
        if merge_id == keep_id:
            continue
        pattern = db.get_ad_pattern_by_id(merge_id)
        if not pattern:
            return error_response(f'Pattern {merge_id} not found', 404)

    try:
        conn = db.get_connection()

        # Sum up confirmation and false positive counts
        total_confirmations = keep_pattern.get('confirmation_count', 0)
        total_false_positives = keep_pattern.get('false_positive_count', 0)

        for merge_id in merge_ids:
            if merge_id == keep_id:
                continue
            pattern = db.get_ad_pattern_by_id(merge_id)
            total_confirmations += pattern.get('confirmation_count', 0)
            total_false_positives += pattern.get('false_positive_count', 0)

        # Update the kept pattern with merged stats
        db.update_ad_pattern(keep_id,
            confirmation_count=total_confirmations,
            false_positive_count=total_false_positives
        )

        # Move corrections to kept pattern
        placeholders = ','.join('?' * len(merge_ids))
        conn.execute(
            f'''UPDATE pattern_corrections
                SET pattern_id = ?
                WHERE pattern_id IN ({placeholders})''',
            [keep_id] + merge_ids
        )

        # Delete merged patterns
        conn.execute(
            f'''DELETE FROM ad_patterns WHERE id IN ({placeholders})''',
            merge_ids
        )
        conn.commit()

        return json_response({
            'message': f'Merged {len(merge_ids)} patterns into pattern {keep_id}',
            'kept_pattern_id': keep_id,
            'merged_count': len(merge_ids),
            'total_confirmations': total_confirmations,
            'total_false_positives': total_false_positives
        })
    except Exception as e:
        logger.exception("Pattern merge failed")
        return error_response('Merge failed', 500)


def _submit_correction_create(db, slug, episode_id, data):
    """Handle a `create` correction: user marked a brand-new ad on an
    episode the detector missed. Writes a marker to episode_details and
    creates a new ad_pattern with created_by='user'.
    """
    start = data.get('start')
    end = data.get('end')
    sponsor_text = (data.get('sponsor') or '').strip()
    text_template = (data.get('text_template') or '').strip()
    reason = data.get('reason') or ''
    scope = data.get('scope') or 'podcast'

    if start is None or end is None:
        return error_response('Missing start/end', 400)
    try:
        start = float(start)
        end = float(end)
    except (TypeError, ValueError):
        return error_response('start and end must be numbers', 400)
    if not (start >= 0 and end > start):
        return error_response('require 0 <= start < end', 400)
    if not sponsor_text:
        return error_response('Sponsor is required', 400)
    if len(text_template) < 50:
        return error_response('text_template must be at least 50 characters', 400)
    if scope not in ('podcast', 'global'):
        return error_response("scope must be 'podcast' or 'global'", 400)

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    duration = episode.get('original_duration') or 0
    if duration and end > duration + 1:
        return error_response(
            f'end ({end}) exceeds episode duration ({duration})', 400
        )

    sponsor_id = get_or_create_known_sponsor(db, sponsor_text)
    if sponsor_id is None:
        return error_response('Invalid sponsor name', 400)
    sponsor_row = db.get_known_sponsor_by_id(sponsor_id)
    canonical_sponsor_name = sponsor_row['name'] if sponsor_row else sponsor_text

    # Read existing markers, insert new marker (with pattern_id placeholder),
    # sort by start.
    markers = []
    raw_markers = episode.get('ad_markers_json')
    if raw_markers:
        try:
            markers = json.loads(raw_markers)
        except (TypeError, ValueError):
            markers = []
    # If the user left "Reason" blank, synthesize one so the EpisodeDetail
    # page row has something to render (it shows segment.reason for the
    # description line). Without this, manual markers appear as just a
    # time range + Manual badge with no sponsor or context visible.
    synthesized_reason = (
        reason.strip()
        if reason and reason.strip()
        else f"{canonical_sponsor_name}: manually added ad"
    )
    new_marker = {
        'start': start,
        'end': end,
        'sponsor': canonical_sponsor_name,
        'reason': synthesized_reason,
        'confidence': 1.0,
        'detection_stage': 'manual',
        'pattern_id': None,
    }
    markers.append(new_marker)
    markers.sort(key=lambda m: m.get('start', 0))

    # Create the pattern; figure out podcast scope params from the episode row.
    podcast_id_str = episode.get('slug') if scope == 'podcast' else None
    network_id = episode.get('network_id') if scope == 'global' else None
    new_pattern_id = db.create_ad_pattern(
        scope=scope,
        text_template=text_template,
        sponsor_id=sponsor_id,
        podcast_id=podcast_id_str,
        network_id=network_id,
        created_from_episode_id=episode_id,
        duration=end - start,
        created_by='user',
    )

    # Stamp the new pattern_id onto the just-inserted marker, then persist.
    for m in markers:
        if (m.get('start') == start and m.get('end') == end
                and m.get('detection_stage') == 'manual'
                and m.get('pattern_id') is None):
            m['pattern_id'] = new_pattern_id
            break
    db.save_episode_details(slug, episode_id, ad_markers=markers)

    db.create_pattern_correction(
        correction_type='create',
        pattern_id=new_pattern_id,
        episode_id=episode_id,
        original_bounds=None,
        corrected_bounds={'start': start, 'end': end},
        text_snippet=text_template[:500],
        sponsor_id=sponsor_id,
    )

    logger.info(
        f"CORRECTION: type=create, episode={slug}/{episode_id}, "
        f"pattern_id={new_pattern_id}, sponsor='{canonical_sponsor_name}', "
        f"start={start}, end={end}, scope={scope}"
    )
    return json_response({
        'message': 'New ad marker created',
        'pattern_id': new_pattern_id,
        'sponsor': canonical_sponsor_name,
    })


@api.route('/episodes/<slug>/<episode_id>/corrections', methods=['POST'])
@log_request
def submit_correction(slug, episode_id):
    """Submit a correction for a detected ad.

    Correction types:
    - confirm: Ad detection is correct (increases confirmation_count)
    - reject: Not actually an ad (increases false_positive_count)
    - adjust: Correct ad but with adjusted boundaries
    - create: User marks a brand-new ad on an episode the detector missed
    """
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    correction_type = data.get('type')
    if correction_type not in ('confirm', 'reject', 'adjust', 'create'):
        return error_response('Invalid correction type', 400)

    # Get pattern service for recording corrections
    from pattern_service import PatternService
    pattern_service = PatternService(db)

    # 'create' marks a brand-new ad on an episode the LLM missed. Boundaries
    # and metadata are top-level, not under `original_ad`. Branch out early
    # so the existing review-flow validation below stays simple.
    if correction_type == 'create':
        return _submit_correction_create(db, slug, episode_id, data)

    original_ad = data.get('original_ad', {})
    original_start = original_ad.get('start')
    original_end = original_ad.get('end')
    pattern_id = original_ad.get('pattern_id')

    if original_start is None or original_end is None:
        return error_response('Missing original ad boundaries', 400)

    if correction_type == 'confirm':
        logger.info(f"CORRECTION: type=confirm, episode={slug}/{episode_id}, pattern_id={pattern_id}, start={original_start}, end={original_end}")

        # Increment confirmation count on pattern
        if pattern_id:
            pattern_service.record_pattern_match(pattern_id, episode_id)
        else:
            # Create new pattern from Claude detection
            transcript = db.get_transcript_for_timestamps(slug, episode_id)
            if transcript:
                ad_text = extract_transcript_segment(transcript, original_start, original_end)

                if ad_text and len(ad_text) >= 50:  # Minimum for TF-IDF matching
                    # Get podcast info for scope
                    podcast = db.get_podcast_by_slug(slug)
                    podcast_id_str = slug if podcast else None

                    # Check for existing pattern with same text (deduplication)
                    existing_pattern = db.find_pattern_by_text(ad_text, podcast_id_str)

                    if existing_pattern:
                        # Use existing pattern instead of creating duplicate
                        pattern_id = existing_pattern['id']
                        pattern_service.record_pattern_match(pattern_id, episode_id)
                        logger.info(f"Linked to existing pattern {pattern_id} for confirmed ad in {slug}/{episode_id}")
                    else:
                        # Extract sponsor from original ad, reason text, or ad text
                        sponsor = original_ad.get('sponsor')
                        if not sponsor:
                            reason = original_ad.get('reason', '')
                            sponsor = extract_sponsor_from_text(reason)
                        if not sponsor:
                            sponsor = extract_sponsor_from_text(ad_text)

                        # Only create pattern if sponsor is known
                        if sponsor:
                            sponsor_id = get_or_create_known_sponsor(db, sponsor)
                            new_pattern_id = db.create_ad_pattern(
                                scope='podcast',
                                podcast_id=podcast_id_str,
                                text_template=ad_text,
                                sponsor_id=sponsor_id,
                                intro_variants=[ad_text[:200]] if len(ad_text) > 200 else [ad_text],
                                outro_variants=[ad_text[-150:]] if len(ad_text) > 150 else [],
                                created_from_episode_id=episode_id
                            )
                            pattern_id = new_pattern_id
                            logger.info(f"Created new pattern {pattern_id} (sponsor: {sponsor}) from confirmed ad in {slug}/{episode_id}")
                        else:
                            # Skip pattern creation - no sponsor detected
                            logger.info(f"Skipped pattern creation (no sponsor detected) for confirmed ad in {slug}/{episode_id}")

        # Delete any conflicting false_positive corrections for this segment
        deleted = db.delete_conflicting_corrections(episode_id, 'confirm', original_start, original_end)
        if deleted:
            logger.info(f"Deleted {deleted} conflicting false_positive correction(s) for {slug}/{episode_id}")

        db.create_pattern_correction(
            correction_type='confirm',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            text_snippet=data.get('notes')
        )

        return json_response({'message': 'Correction recorded', 'pattern_id': pattern_id})

    elif correction_type == 'reject':
        logger.info(f"CORRECTION: type=reject, episode={slug}/{episode_id}, pattern_id={pattern_id}, start={original_start}, end={original_end}")

        # Extract transcript text for cross-episode matching
        rejected_text = None
        transcript = db.get_transcript_for_timestamps(slug, episode_id)
        if transcript:
            rejected_text = extract_transcript_segment(transcript, original_start, original_end)
            if rejected_text:
                logger.debug(f"Extracted {len(rejected_text)} chars of rejected text for cross-episode matching")

        # Mark as false positive
        if pattern_id:
            pattern = db.get_ad_pattern_by_id(pattern_id)
            if pattern:
                new_count = pattern.get('false_positive_count', 0) + 1
                db.update_ad_pattern(pattern_id, false_positive_count=new_count)
                logger.info(f"Incremented false_positive_count to {new_count} for pattern {pattern_id}")

        # Delete any conflicting confirm corrections for this segment
        deleted = db.delete_conflicting_corrections(episode_id, 'false_positive', original_start, original_end)
        if deleted:
            logger.info(f"Deleted {deleted} conflicting confirm correction(s) for {slug}/{episode_id}")

        db.create_pattern_correction(
            correction_type='false_positive',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            text_snippet=rejected_text  # Store transcript text for cross-episode matching
        )

        return json_response({'message': 'False positive recorded'})

    elif correction_type == 'adjust':
        # Save adjusted boundaries
        adjusted_start = data.get('adjusted_start')
        adjusted_end = data.get('adjusted_end')

        if adjusted_start is None or adjusted_end is None:
            return error_response('Missing adjusted boundaries', 400)

        logger.info(f"CORRECTION: type=adjust, episode={slug}/{episode_id}, pattern_id={pattern_id}, "
                    f"original={original_start:.1f}-{original_end:.1f}, adjusted={adjusted_start:.1f}-{adjusted_end:.1f}")

        # Extract transcript text using ADJUSTED boundaries for pattern learning
        adjusted_text = None
        transcript = db.get_transcript_for_timestamps(slug, episode_id)
        if transcript:
            adjusted_text = extract_transcript_segment(transcript, adjusted_start, adjusted_end)

        # If we have a pattern, increment confirmation count
        if pattern_id:
            from pattern_service import PatternService
            pattern_service = PatternService(db)
            pattern_service.record_pattern_match(pattern_id, episode_id)
            logger.info(f"Recorded adjustment as confirmation for pattern {pattern_id}")

            # Reviewer-trim auto-update: when the reviewer narrows the bounds
            # by at least `min_trim_threshold` seconds AND settings allow it,
            # rewrite the pattern's text_template/variants from the new bounds.
            # Community patterns are never auto-rewritten (handled in
            # pattern_service.rewrite_pattern_from_bounds).
            try:
                narrowed = (
                    adjusted_start >= original_start
                    and adjusted_end <= original_end
                )
                trim_seconds = (
                    (adjusted_start - original_start) + (original_end - adjusted_end)
                )
                enabled = db.get_setting_bool(
                    'update_patterns_from_reviewer_adjustments', default=True
                )
                threshold = db.get_setting_float(
                    'min_trim_threshold', default=20.0
                )
                if enabled and narrowed and trim_seconds >= threshold and transcript:
                    rewritten = pattern_service.rewrite_pattern_from_bounds(
                        pattern_id, transcript, adjusted_start, adjusted_end
                    )
                    if rewritten:
                        logger.info(
                            f"Pattern {pattern_id} auto-trimmed by {trim_seconds:.1f}s "
                            f"(threshold={threshold:.1f}s)"
                        )
            except Exception as e:
                logger.warning(f"Reviewer-trim auto-update failed for pattern {pattern_id}: {e}")
        elif adjusted_text and len(adjusted_text) >= 50:
            # No pattern exists - create one from adjusted boundaries (like confirm does)
            podcast = db.get_podcast_by_slug(slug)
            podcast_id_str = slug if podcast else None

            # Check for existing pattern with same text
            existing_pattern = db.find_pattern_by_text(adjusted_text, podcast_id_str)

            if existing_pattern:
                pattern_id = existing_pattern['id']
                from pattern_service import PatternService
                pattern_service = PatternService(db)
                pattern_service.record_pattern_match(pattern_id, episode_id)
                logger.info(f"Linked adjustment to existing pattern {pattern_id}")
            else:
                # Extract sponsor
                sponsor = original_ad.get('sponsor')
                if not sponsor:
                    sponsor = extract_sponsor_from_text(adjusted_text)

                if sponsor:
                    sponsor_id = get_or_create_known_sponsor(db, sponsor)
                    new_pattern_id = db.create_ad_pattern(
                        scope='podcast',
                        podcast_id=podcast_id_str,
                        text_template=adjusted_text,
                        sponsor_id=sponsor_id,
                        intro_variants=[adjusted_text[:200]] if len(adjusted_text) > 200 else [adjusted_text],
                        outro_variants=[adjusted_text[-150:]] if len(adjusted_text) > 150 else [],
                        created_from_episode_id=episode_id
                    )
                    pattern_id = new_pattern_id
                    logger.info(f"Created new pattern {pattern_id} (sponsor: {sponsor}) from adjusted ad in {slug}/{episode_id}")
                else:
                    logger.info(f"Skipped pattern creation (no sponsor detected) for adjusted ad in {slug}/{episode_id}")

        # Record the correction with adjusted text for cross-episode learning
        db.create_pattern_correction(
            correction_type='boundary_adjustment',
            pattern_id=pattern_id,
            episode_id=episode_id,
            original_bounds={'start': original_start, 'end': original_end},
            corrected_bounds={'start': adjusted_start, 'end': adjusted_end},
            text_snippet=adjusted_text  # Store adjusted text for pattern learning
        )

        return json_response({'message': 'Adjustment recorded', 'pattern_id': pattern_id})


# ========== Import/Export Endpoints ==========

@api.route('/patterns/export', methods=['GET'])
@log_request
def export_patterns():
    """Export patterns as JSON for backup or sharing.

    Query params:
    - include_disabled: Include disabled patterns (default: false)
    - include_corrections: Include correction history (default: false)
    - ids: Optional comma-separated pattern ids. If set, only those rows
      are exported (intersected with the include_disabled filter).
    """
    db = get_database()

    include_disabled = request.args.get('include_disabled', 'false').lower() == 'true'
    include_corrections = request.args.get('include_corrections', 'false').lower() == 'true'
    ids_param = request.args.get('ids')

    # Get patterns
    patterns = db.get_ad_patterns(active_only=not include_disabled)

    if ids_param:
        try:
            wanted = {int(x) for x in ids_param.split(',') if x.strip()}
        except ValueError:
            return error_response('ids must be a comma-separated list of integers', 400)
        if wanted:
            patterns = [p for p in patterns if int(p['id']) in wanted]

    # Build export data
    export_data = {
        'version': '1.0',
        'exported_at': utc_now_iso(),
        'pattern_count': len(patterns),
        'patterns': []
    }

    for pattern in patterns:
        pattern_data = {
            'scope': pattern.get('scope'),
            'text_template': pattern.get('text_template'),
            'intro_variants': pattern.get('intro_variants'),
            'outro_variants': pattern.get('outro_variants'),
            'sponsor': pattern.get('sponsor'),
            'confirmation_count': pattern.get('confirmation_count', 0),
            'false_positive_count': pattern.get('false_positive_count', 0),
            'is_active': pattern.get('is_active', True),
            'created_at': pattern.get('created_at'),
        }

        # Include network/podcast IDs for scoped patterns
        if pattern.get('network_id'):
            pattern_data['network_id'] = pattern['network_id']
        if pattern.get('podcast_id'):
            pattern_data['podcast_id'] = pattern['podcast_id']
        if pattern.get('dai_platform'):
            pattern_data['dai_platform'] = pattern['dai_platform']

        # Optionally include corrections
        if include_corrections:
            corrections = db.get_pattern_corrections(pattern_id=pattern['id'])
            if corrections:
                pattern_data['corrections'] = corrections

        export_data['patterns'].append(pattern_data)

    return json_response(export_data)


@api.route('/patterns/import', methods=['POST'])
@limiter.limit("3 per hour")
@log_request
def import_patterns():
    """Import patterns from JSON.

    Body:
    - patterns: Array of pattern objects
    - mode: "merge" (default), "replace", or "supplement"
      - merge: Update existing patterns, add new ones
      - replace: Delete all existing patterns, import all
      - supplement: Only add patterns that don't exist
    """
    db = get_database()

    data = request.get_json()
    if not data or 'patterns' not in data:
        return error_response('No patterns provided', 400)

    patterns = data.get('patterns', [])
    mode = data.get('mode', 'merge')

    if mode not in ('merge', 'replace', 'supplement'):
        return error_response('Invalid mode. Use "merge", "replace", or "supplement"', 400)

    # Empty merge/supplement is a no-op, which is legitimate for a
    # round-trip on a fresh DB. Replace mode with an empty list would
    # wipe the table and is almost never what the caller meant, so that
    # case stays a 400.
    if not patterns:
        if mode == 'replace':
            return error_response(
                'Empty patterns array with mode=replace would wipe the table; '
                'pass mode=merge or mode=supplement for a round-trip',
                400,
            )
        return json_response({
            'mode': mode,
            'importedCount': 0,
            'updatedCount': 0,
            'skippedCount': 0,
            'message': 'No patterns in payload; nothing to do',
        })

    # Upfront validation so a malformed payload is rejected before any
    # write. Replace-mode import in particular must not half-apply:
    # deleting every existing pattern and then erroring out on the
    # first bad item would leave the operator with an empty pattern
    # table. All-or-nothing via explicit validation + a single
    # transaction closes that window.
    valid_patterns = []
    skipped_count = 0
    for idx, pattern_data in enumerate(patterns):
        if not isinstance(pattern_data, dict):
            return error_response(
                f'patterns[{idx}] is not an object',
                400,
            )
        scope = pattern_data.get('scope')
        if scope not in ('global', 'network', 'podcast', 'dai_platform'):
            return error_response(
                f'patterns[{idx}] has missing or invalid scope',
                400,
            )
        valid_patterns.append(pattern_data)

    imported_count = 0
    updated_count = 0

    conn = db.get_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')

        if mode == 'replace':
            existing = db.get_ad_patterns(active_only=False)
            for p in existing:
                db.delete_ad_pattern(p['id'])
            logger.info(f"Replace mode: deleted {len(existing)} existing patterns")

        for pattern_data in valid_patterns:
            existing = _find_similar_pattern(db, pattern_data)

            if existing:
                if mode == 'supplement':
                    skipped_count += 1
                    continue
                if mode in ('merge', 'replace'):
                    pd_sponsor = pattern_data.get('sponsor')
                    pd_sponsor_id = (
                        get_or_create_known_sponsor(db, pd_sponsor)
                        if pd_sponsor else None
                    )
                    updates = {
                        'text_template': pattern_data.get('text_template'),
                        'intro_variants': pattern_data.get('intro_variants'),
                        'outro_variants': pattern_data.get('outro_variants'),
                        'sponsor_id': pd_sponsor_id,
                    }
                    updates = {k: v for k, v in updates.items() if v is not None}
                    if updates:
                        db.update_ad_pattern(existing['id'], **updates)
                        updated_count += 1
                    else:
                        skipped_count += 1
                    continue

            bulk_sponsor = pattern_data.get('sponsor')
            bulk_sponsor_id = (
                get_or_create_known_sponsor(db, bulk_sponsor) if bulk_sponsor else None
            )
            db.create_ad_pattern(
                scope=pattern_data.get('scope'),
                text_template=pattern_data.get('text_template'),
                sponsor_id=bulk_sponsor_id,
                podcast_id=pattern_data.get('podcast_id'),
                network_id=pattern_data.get('network_id'),
                dai_platform=pattern_data.get('dai_platform'),
                intro_variants=pattern_data.get('intro_variants'),
                outro_variants=pattern_data.get('outro_variants')
            )
            imported_count += 1

        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Import failed; rolled back")
        return error_response('Import failed', 500)

    logger.info(f"Import complete: {imported_count} imported, {updated_count} updated, {skipped_count} skipped")
    return json_response({
        'message': 'Import complete',
        'imported': imported_count,
        'updated': updated_count,
        'skipped': skipped_count
    })


@api.route('/patterns/backfill-false-positives', methods=['POST'])
@log_request
def backfill_false_positive_texts():
    """Backfill transcript text for existing false positive corrections.

    Populates text_snippet field for corrections that don't have it.
    This enables cross-episode false positive matching.
    """
    db = get_database()
    conn = db.get_connection()

    # Get corrections without text
    cursor = conn.execute('''
        SELECT pc.id, pc.episode_id, pc.original_bounds, p.slug
        FROM pattern_corrections pc
        JOIN episodes e ON pc.episode_id = e.episode_id
        JOIN podcasts p ON e.podcast_id = p.id
        WHERE pc.correction_type = 'false_positive'
        AND (pc.text_snippet IS NULL OR pc.text_snippet = '')
    ''')

    rows = cursor.fetchall()
    logger.info(f"Found {len(rows)} false positive corrections to backfill")

    updated = 0
    skipped = 0
    for row in rows:
        transcript = db.get_transcript_for_timestamps(row['slug'], row['episode_id'])
        if not transcript:
            skipped += 1
            continue

        bounds_str = row['original_bounds']
        if not bounds_str:
            skipped += 1
            continue

        try:
            bounds = json.loads(bounds_str)
            start, end = bounds.get('start'), bounds.get('end')
            if start is None or end is None:
                skipped += 1
                continue

            # Extract text
            text = extract_transcript_segment(transcript, start, end)
            if text and len(text) >= 50:
                conn.execute(
                    'UPDATE pattern_corrections SET text_snippet = ? WHERE id = ?',
                    (text, row['id'])
                )
                updated += 1
            else:
                skipped += 1
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse bounds for correction {row['id']}: {e}")
            skipped += 1

    conn.commit()
    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped")

    return json_response({
        'message': 'Backfill complete',
        'updated': updated,
        'skipped': skipped
    })


# ========== Bulk operations + community ==========

def _resolve_bulk_target(db, data: dict, active_only_for_source: bool):
    """Shared validation for bulk-delete + bulk-disable.

    Returns (ids, error_response). ids is None when error_response is set.
    All user-supplied fields are coerced to their expected types before
    being reflected in any response or used in a SQL query.
    """
    from utils.community_tags import PATTERN_SOURCES
    if not data.get('confirm'):
        return None, error_response('confirm: true is required', 400)
    try:
        expected = int(data['expected_count'])
    except (KeyError, TypeError, ValueError):
        return None, error_response('expected_count must be an integer', 400)

    raw_ids = data.get('ids')
    source = data.get('source')
    if raw_ids is not None and not isinstance(raw_ids, list):
        return None, error_response('ids must be a list of integers', 400)
    if not raw_ids and source not in PATTERN_SOURCES:
        return None, error_response('Provide either ids or a valid source', 400)

    if raw_ids:
        try:
            ids = [int(x) for x in raw_ids]
        except (TypeError, ValueError):
            return None, error_response('ids must contain only integers', 400)
    else:
        rows = db.get_patterns_by_source(source, active_only=active_only_for_source)
        ids = [int(r['id']) for r in rows]

    if len(ids) != expected:
        return None, error_response(
            f'expected_count mismatch (expected {expected}, matched {len(ids)})',
            400,
        )
    return ids, None


@api.route('/patterns/bulk-delete', methods=['POST'])
@log_request
def bulk_delete_patterns():
    """Hard-delete patterns. Body: {ids?, source?, confirm: true, expected_count: N}.

    Either `ids` or `source` must be provided. `expected_count` MUST match
    the actual number of matched rows or the call is rejected with 400 —
    this is the fat-finger guard from the plan.
    """
    db = get_database()
    ids, err = _resolve_bulk_target(db, request.get_json() or {}, active_only_for_source=False)
    if err is not None:
        return err
    deleted = db.bulk_delete_patterns(ids)
    return json_response({'deleted': deleted, 'ids': ids})


@api.route('/patterns/bulk-disable', methods=['POST'])
@log_request
def bulk_disable_patterns():
    """Mark patterns is_active=0. Same shape and guards as bulk-delete."""
    db = get_database()
    ids, err = _resolve_bulk_target(db, request.get_json() or {}, active_only_for_source=True)
    if err is not None:
        return err
    disabled = db.bulk_disable_patterns(ids)
    return json_response({'disabled': disabled, 'ids': ids})


@api.route('/patterns/<int:pattern_id>/submit-to-community', methods=['POST'])
@log_request
def submit_pattern_to_community(pattern_id: int):
    """Run the community export pipeline for a single local pattern.

    Returns the JSON payload + a prefilled GitHub PR URL. When the encoded
    URL would exceed the GitHub limit (`too_large=True`), the frontend
    falls back to offering the payload as a downloadable file.
    """
    from community_export import run_export_pipeline, ExportError
    db = get_database()
    try:
        result = run_export_pipeline(pattern_id, db)
    except ExportError as e:
        return error_response({'message': 'Export failed', 'reasons': e.reasons}, 400)
    return json_response(result)


@api.route('/patterns/<int:pattern_id>/protect', methods=['POST'])
@log_request
def protect_pattern(pattern_id: int):
    """Set protected_from_sync=1 on a community pattern."""
    from utils.community_tags import PATTERN_SOURCE_COMMUNITY, PATTERN_SOURCE_LOCAL
    db = get_database()
    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('pattern not found', 404)
    if (pattern.get('source') or PATTERN_SOURCE_LOCAL) != PATTERN_SOURCE_COMMUNITY:
        return error_response('only community patterns can be protected', 400)
    db.set_pattern_protected(pattern_id, True)
    return json_response({'pattern_id': pattern_id, 'protected_from_sync': 1})


@api.route('/patterns/<int:pattern_id>/protect', methods=['DELETE'])
@log_request
def unprotect_pattern(pattern_id: int):
    """Set protected_from_sync=0 on a community pattern."""
    db = get_database()
    pattern = db.get_ad_pattern_by_id(pattern_id)
    if not pattern:
        return error_response('pattern not found', 404)
    db.set_pattern_protected(pattern_id, False)
    return json_response({'pattern_id': pattern_id, 'protected_from_sync': 0})
