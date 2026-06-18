"""REST endpoints for per-feed audio cue templates (#350).

Routes mounted under the ``/api/v1`` blueprint:

- ``GET    /feeds/<slug>/cue-templates``           - list templates for a feed
- ``POST   /feeds/<slug>/cue-templates``           - mark a new template
- ``PATCH  /cue-templates/<id>``                   - rename / toggle
- ``DELETE /cue-templates/<id>``                   - remove
- ``POST   /feeds/<slug>/episodes/<episode_id>/cue-scan``
                                                   - run every enabled template
- ``POST   /feeds/<slug>/episodes/<episode_id>/cue-template-preview``
                                                   - run one template
"""
import logging

from flask import abort, request

from api import (
    api, log_request, json_response, error_response,
    get_database, get_storage,
)
from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ, N_COEFFS, compute_mfcc, decode_pcm_window,
    serialize_mfcc, pcm_to_int16_bytes,
)
from audio_analysis.cue_template_matcher import (
    AudioCueTemplateMatcher, DEFAULT_MATCH_SCORE,
)
from utils.validation import is_valid_episode_id

logger = logging.getLogger('podcast.api.cue_templates')


def _resolve_original_audio(db, storage, slug, episode_id):
    """Resolve an episode's retained original audio path.

    Returns ``(audio_path, None)`` on success or ``(None, error_response)`` when
    the episode is unknown, has no retained original, or the file is missing.
    Shared by the create / cue-scan / preview routes, which all require the
    original (un-cut) audio because a cue can sit inside a removed ad.
    """
    episode = db.get_episode(slug, episode_id)
    if not episode or not episode.get('original_file'):
        return None, error_response('original audio not retained for this episode', 404)
    audio_path = storage.get_original_path(slug, episode_id)
    if not audio_path.exists():
        return None, error_response('original audio file missing', 404)
    return audio_path, None


def _template_to_meta_dict(row: dict) -> dict:
    """Strip the binary blobs for JSON responses."""
    return {
        'id': row['id'],
        'podcastId': row['podcast_id'],
        'label': row['label'],
        'sourceEpisodeId': row['source_episode_id'],
        'sourceOffsetS': row['source_offset_s'],
        'durationS': row['duration_s'],
        'sampleRate': row['sample_rate'],
        'nCoeffs': row['n_coeffs'],
        'scope': row['scope'] if 'scope' in row.keys() else 'podcast',
        'networkId': row['network_id'] if 'network_id' in row.keys() else None,
        'enabled': bool(row['enabled']),
        'createdAt': row['created_at'],
        'createdBy': row['created_by'] if 'created_by' in row.keys() else None,
    }


@api.route('/feeds/<slug>/cue-templates', methods=['GET'])
@log_request
def list_cue_templates(slug):
    """List all cue templates for a feed."""
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    rows = db.list_cue_templates_metadata(podcast['id'])
    return json_response({'templates': [_template_to_meta_dict(r) for r in rows]})


@api.route('/feeds/<slug>/cue-templates', methods=['POST'])
@log_request
def create_cue_template(slug):
    """Mark a new cue template from a window of an episode.

    Body:
        episodeId (str, required)
        startS    (float, required) - window start within episode (seconds)
        endS      (float, required) - window end within episode (seconds)
        label     (str, required)
    """
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)

    payload = request.get_json(silent=True) or {}
    episode_id = payload.get('episodeId')
    label = (payload.get('label') or '').strip()
    try:
        start_s = float(payload['startS'])
        end_s = float(payload['endS'])
    except (KeyError, TypeError, ValueError):
        return error_response('startS and endS are required numbers', 400)
    if not episode_id or not is_valid_episode_id(episode_id):
        return error_response('episodeId is required and must be valid', 400)
    if not label:
        return error_response('label is required', 400)
    if len(label) > 80:
        return error_response('label is too long (max 80 chars)', 400)
    if end_s - start_s < 0.2:
        return error_response('selection must be at least 0.2 seconds', 400)
    if end_s - start_s > 4.0:
        return error_response('selection must be at most 4 seconds', 400)

    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    if err:
        return err

    # Decode + extract MFCC for the marked window. Heavy work happens here, but
    # only the rare "mark a cue" action triggers it; matchers stream the same
    # function over an entire episode in tens of seconds, so this single window
    # is negligible.
    try:
        pcm = decode_pcm_window(audio_path, start_s, end_s, SAMPLE_RATE_HZ)
    except RuntimeError as e:
        return error_response(f'failed to decode window: {e}', 400)
    mfcc = compute_mfcc(pcm)
    if mfcc.shape[0] < 3:
        return error_response(
            'selection too short after framing; widen the selection or pick a louder cue',
            400,
        )

    template_id = db.create_cue_template(
        podcast_id=podcast['id'],
        label=label,
        source_episode_id=episode_id,
        source_offset_s=start_s,
        duration_s=round(end_s - start_s, 3),
        sample_rate=SAMPLE_RATE_HZ,
        n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(pcm),
        pcm_sample_rate=SAMPLE_RATE_HZ,
    )
    logger.info(
        f"Cue template created: id={template_id} feed={slug} ep={episode_id} "
        f"window={start_s:.2f}-{end_s:.2f}s label={label!r}"
    )
    row = db.get_cue_template(template_id)
    return json_response({'template': _template_to_meta_dict(row)}, status=201)


@api.route('/cue-templates/<int:template_id>', methods=['PATCH'])
@log_request
def update_cue_template_route(template_id):
    """Rename / enable / disable a template."""
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    payload = request.get_json(silent=True) or {}
    new_label = payload.get('label')
    enabled = payload.get('enabled')
    if new_label is not None:
        new_label = str(new_label).strip()
        if not new_label:
            return error_response('label cannot be empty', 400)
        if len(new_label) > 80:
            return error_response('label is too long (max 80 chars)', 400)
    if enabled is not None and not isinstance(enabled, bool):
        return error_response('enabled must be true or false', 400)
    db.update_cue_template(template_id, label=new_label, enabled=enabled)
    row = db.get_cue_template(template_id)
    return json_response({'template': _template_to_meta_dict(row)})


@api.route('/cue-templates/<int:template_id>', methods=['DELETE'])
@log_request
def delete_cue_template_route(template_id):
    """Remove a template."""
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    db.delete_cue_template(template_id)
    return json_response({'deleted': True, 'id': template_id})


@api.route(
    '/feeds/<slug>/episodes/<episode_id>/cue-scan',
    methods=['POST'],
)
@log_request
def cue_scan_episode(slug, episode_id):
    """Run every enabled cue template for the feed against an episode.

    Test-mode endpoint: returns matches per template AND each template's peak
    correlation against the episode (even when below the threshold) so the
    user can see how close a non-matching template came. Optional body field
    ``scoreThreshold`` overrides the global threshold for this run only --
    handy for sweeping values without re-saving settings.
    """
    if not is_valid_episode_id(episode_id):
        abort(400)
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    if err:
        return err

    templates = db.list_active_cue_templates_for_feed(podcast['id'])
    if not templates:
        return error_response(
            'this feed has no enabled cue templates', 400,
        )

    payload = request.get_json(silent=True) or {}
    override = payload.get('scoreThreshold')
    if override is not None:
        try:
            score = max(0.0, min(0.99, float(override)))
        except (TypeError, ValueError):
            return error_response('scoreThreshold must be a number', 400)
    else:
        score = db.get_setting_float('audio_cue_template_score', DEFAULT_MATCH_SCORE)
    matcher = AudioCueTemplateMatcher(
        templates=templates, score_threshold=score,
    )
    if not matcher.is_usable:
        return error_response('templates could not be loaded', 500)
    signals, debug = matcher.detect_with_debug(str(audio_path))
    # Group matches by template_id so the UI can render one row per template.
    by_template: dict = {t['id']: [] for t in templates}
    for s in signals:
        tid = (s.details or {}).get('template_id')
        if tid in by_template:
            by_template[tid].append({
                'start': s.start,
                'end': s.end,
                'confidence': s.confidence,
                'score': (s.details or {}).get('score', s.confidence),
            })
    return json_response({
        'episodeId': episode_id,
        'thresholdUsed': debug['threshold'],
        'elapsedSeconds': debug['elapsed_s'],
        'templates': [
            {
                'id': t['id'],
                'label': t['label'],
                'durationS': t['duration_s'],
                'peakScore': next(
                    (d['peak_score'] for d in debug['templates']
                     if d['id'] == t['id']),
                    0.0,
                ),
                'matchCount': len(by_template.get(t['id'], [])),
                'matches': by_template.get(t['id'], []),
            }
            for t in templates
        ],
    })


@api.route(
    '/feeds/<slug>/episodes/<episode_id>/cue-template-preview',
    methods=['POST'],
)
@log_request
def preview_cue_template(slug, episode_id):
    """Run a single template against an episode and return its matches.

    Body:
        templateId (int, required)

    The matcher is the same one the audio analysis pipeline uses, so the
    preview shows exactly what would appear in production.
    """
    if not is_valid_episode_id(episode_id):
        abort(400)
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    payload = request.get_json(silent=True) or {}
    try:
        template_id = int(payload['templateId'])
    except (KeyError, TypeError, ValueError):
        return error_response('templateId is required', 400)
    template = db.get_cue_template(template_id)
    if not template or template['podcast_id'] != podcast['id']:
        return error_response('template not found for this feed', 404)
    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    if err:
        return err

    score = db.get_setting_float('audio_cue_template_score', DEFAULT_MATCH_SCORE)
    matcher = AudioCueTemplateMatcher(
        templates=[template], score_threshold=score,
    )
    if not matcher.is_usable:
        return error_response('template could not be loaded', 500)
    signals, debug = matcher.detect_with_debug(str(audio_path))
    return json_response({
        'templateId': template_id,
        'thresholdUsed': debug['threshold'],
        'peakScore': next(
            (d['peak_score'] for d in debug['templates'] if d['id'] == template_id),
            0.0,
        ),
        'matches': [
            {
                'start': s.start,
                'end': s.end,
                'confidence': s.confidence,
                'score': (s.details or {}).get('score', s.confidence),
            }
            for s in signals
        ],
    })
