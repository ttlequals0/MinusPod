"""Pattern routes: /patterns/* endpoints and corrections."""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils.time import utc_now_iso

from flask import request

from api import (
    api, log_request, json_response, error_response,
    get_database, get_storage,
    extract_transcript_segment, extract_sponsor_from_text,
    _find_similar_pattern,
)

logger = logging.getLogger('podcast.api')


# ========== Pattern & Correction Endpoints ==========

@api.route('/patterns', methods=['GET'])
@log_request
def list_patterns():
    """List all ad patterns with optional filtering."""
    db = get_database()

    scope = request.args.get('scope')
    podcast_id = request.args.get('podcast_id')
    network_id = request.args.get('network_id')
    active_only = request.args.get('active', 'true').lower() == 'true'

    patterns = db.get_ad_patterns(
        scope=scope,
        podcast_id=podcast_id,
        network_id=network_id,
        active_only=active_only
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
                last_date = datetime.fromisoformat(last_matched.replace('Z', '+00:00'))
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

    # Allowed fields
    allowed = {'text_template', 'sponsor', 'intro_variants', 'outro_variants',
               'is_active', 'disabled_reason', 'scope'}

    updates = {k: v for k, v in data.items() if k in allowed}

    if updates:
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
        logger.error(f"Deduplication failed: {e}")
        return error_response(f'Deduplication failed: {str(e)}', 500)


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
        logger.error(f"Pattern merge failed: {e}")
        return error_response(f'Merge failed: {str(e)}', 500)


@api.route('/episodes/<slug>/<episode_id>/corrections', methods=['POST'])
@log_request
def submit_correction(slug, episode_id):
    """Submit a correction for a detected ad.

    Correction types:
    - confirm: Ad detection is correct (increases confirmation_count)
    - reject: Not actually an ad (increases false_positive_count)
    - adjust: Correct ad but with adjusted boundaries
    """
    db = get_database()

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    correction_type = data.get('type')
    if correction_type not in ('confirm', 'reject', 'adjust'):
        return error_response('Invalid correction type', 400)

    original_ad = data.get('original_ad', {})
    original_start = original_ad.get('start')
    original_end = original_ad.get('end')
    pattern_id = original_ad.get('pattern_id')

    if original_start is None or original_end is None:
        return error_response('Missing original ad boundaries', 400)

    # Get pattern service for recording corrections
    from pattern_service import PatternService
    pattern_service = PatternService(db)

    if correction_type == 'confirm':
        logger.info(f"CORRECTION: type=confirm, episode={slug}/{episode_id}, pattern_id={pattern_id}, start={original_start}, end={original_end}")

        # Increment confirmation count on pattern
        if pattern_id:
            pattern_service.record_pattern_match(pattern_id, episode_id)
        else:
            # Create new pattern from Claude detection
            episode = db.get_episode(slug, episode_id)
            if episode:
                transcript = episode.get('transcript_text', '')

                # Extract ad text from transcript using timestamps
                ad_text = extract_transcript_segment(transcript, original_start, original_end)

                if ad_text and len(ad_text) >= 50:  # Minimum for TF-IDF matching
                    # Get podcast info for scope
                    podcast = db.get_podcast_by_slug(slug)
                    podcast_id_str = str(podcast['id']) if podcast else None

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
                            new_pattern_id = db.create_ad_pattern(
                                scope='podcast',
                                podcast_id=podcast_id_str,
                                text_template=ad_text,
                                sponsor=sponsor,
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
        episode = db.get_episode(slug, episode_id)
        if episode:
            transcript = episode.get('transcript_text', '')
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
        episode = db.get_episode(slug, episode_id)
        if episode:
            transcript = episode.get('transcript_text', '')
            if transcript:
                adjusted_text = extract_transcript_segment(transcript, adjusted_start, adjusted_end)

        # If we have a pattern, increment confirmation count
        if pattern_id:
            from pattern_service import PatternService
            pattern_service = PatternService(db)
            pattern_service.record_pattern_match(pattern_id, episode_id)
            logger.info(f"Recorded adjustment as confirmation for pattern {pattern_id}")
        elif adjusted_text and len(adjusted_text) >= 50:
            # No pattern exists - create one from adjusted boundaries (like confirm does)
            podcast = db.get_podcast_by_slug(slug)
            podcast_id_str = str(podcast['id']) if podcast else None

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
                    new_pattern_id = db.create_ad_pattern(
                        scope='podcast',
                        podcast_id=podcast_id_str,
                        text_template=adjusted_text,
                        sponsor=sponsor,
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
    """
    db = get_database()

    include_disabled = request.args.get('include_disabled', 'false').lower() == 'true'
    include_corrections = request.args.get('include_corrections', 'false').lower() == 'true'

    # Get patterns
    patterns = db.get_ad_patterns(active_only=not include_disabled)

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

    if not patterns:
        return error_response('Empty patterns array', 400)

    imported_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        # Replace mode: delete all existing patterns first
        if mode == 'replace':
            existing = db.get_ad_patterns(active_only=False)
            for p in existing:
                db.delete_ad_pattern(p['id'])
            logger.info(f"Replace mode: deleted {len(existing)} existing patterns")

        for pattern_data in patterns:
            # Validate required fields
            if not pattern_data.get('scope'):
                skipped_count += 1
                continue

            # Check for existing similar pattern
            existing = _find_similar_pattern(db, pattern_data)

            if existing:
                if mode == 'supplement':
                    # Don't update existing patterns
                    skipped_count += 1
                    continue
                elif mode in ('merge', 'replace'):
                    # Update existing pattern
                    updates = {
                        'text_template': pattern_data.get('text_template'),
                        'intro_variants': pattern_data.get('intro_variants'),
                        'outro_variants': pattern_data.get('outro_variants'),
                        'sponsor': pattern_data.get('sponsor'),
                    }
                    updates = {k: v for k, v in updates.items() if v is not None}
                    if updates:
                        db.update_ad_pattern(existing['id'], **updates)
                        updated_count += 1
                    else:
                        skipped_count += 1
                    continue

            # Create new pattern
            db.create_ad_pattern(
                scope=pattern_data.get('scope'),
                text_template=pattern_data.get('text_template'),
                sponsor=pattern_data.get('sponsor'),
                podcast_id=pattern_data.get('podcast_id'),
                network_id=pattern_data.get('network_id'),
                dai_platform=pattern_data.get('dai_platform'),
                intro_variants=pattern_data.get('intro_variants'),
                outro_variants=pattern_data.get('outro_variants')
            )
            imported_count += 1

        logger.info(f"Import complete: {imported_count} imported, {updated_count} updated, {skipped_count} skipped")

        return json_response({
            'message': 'Import complete',
            'imported': imported_count,
            'updated': updated_count,
            'skipped': skipped_count
        })

    except Exception as e:
        logger.error(f"Import failed: {e}")
        return error_response(f'Import failed: {str(e)}', 500)


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
        # Get episode transcript
        episode = db.get_episode(row['slug'], row['episode_id'])
        if not episode or not episode.get('transcript_text'):
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
            text = extract_transcript_segment(episode['transcript_text'], start, end)
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
