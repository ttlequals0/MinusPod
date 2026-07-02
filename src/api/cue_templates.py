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
import io
import json
import logging
import os
import threading
import wave
import zipfile

from flask import abort, request, send_file

from api import (
    api, log_request, json_response, error_response,
    get_database, get_storage, _get_version,
)
from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ, N_COEFFS, compute_mfcc, decode_pcm_window,
    serialize_mfcc, pcm_to_int16_bytes, int16_bytes_to_pcm,
    pcm_to_flac, flac_to_wav,
)
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher
from audio_analysis.cue_candidates import (
    merge_cue_candidates, annotate_recurring_with_ad_affinity,
    count_ad_boundary_hits,
)
from audio_analysis.cue_speech_filter import is_likely_speech
from audio_analysis.cue_detector import AudioCueDetector
from audio_analysis.detected_cues import build_detected_cues
from audio_analysis.cue_threshold_suggest import suggest_cue_threshold
from audio_fingerprinter import AudioFingerprinter
from config import (
    AUDIO_CUE_CAPTURE_MIN_SECONDS, AUDIO_CUE_CAPTURE_MAX_SECONDS,
    AUDIO_CUE_CAPTURE_MAX_BY_TYPE, AUDIO_CUE_CAPTURE_WARN_AD_SECONDS,
    AUDIO_CUE_FREQ_MAX_HZ,
    AUDIO_CUE_SCAN_FREQ_MIN_HZ, AUDIO_CUE_SCAN_PROMINENCE_DB,
    AUDIO_CUE_SCAN_RELEASE_DB, AUDIO_CUE_SCAN_MAX_DURATION_SECONDS,
    AUDIO_CUE_FP_WINDOW_SECONDS,
    AUDIO_CUE_SPEECH_BAND_LO_HZ, AUDIO_CUE_SPEECH_BAND_HI_HZ,
    AUDIO_CUE_SPEECH_BAND_RATIO_MAX, AUDIO_CUE_SPEECH_FLATNESS_MIN,
    AUDIO_CUE_SPEECH_SUSTAINED_MAX,
    AUDIO_CUE_XEP_HEAD_SECONDS, AUDIO_CUE_XEP_TAIL_SECONDS,
    AUDIO_CUE_XEP_MAX_SIBLINGS, AUDIO_CUE_XEP_SIBLING_LOOKBACK,
    AUDIO_CUE_XEP_MIN_MATCHES,
    AUDIO_CUE_XEP_MIN_DURATION,
    AUDIO_CUE_XEP_MAX_PER_ZONE, AUDIO_CUE_XEP_SIMILARITY,
    AUDIO_CUE_RECURRENCE_SIMILARITY, AUDIO_CUE_RECURRENCE_MIN_COUNT,
    AUDIO_CUE_FORMANT_ATTEN_DB,
    AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS,
    AUDIO_CUE_TYPES, AUDIO_CUE_TYPE_DEFAULT, AUDIO_CUE_TYPE_SHOW_INTRO,
    AUDIO_CUE_TYPE_SHOW_OUTRO,
    AUDIO_CUE_ROLE_NON_AD, audio_cue_type_role,
    is_template_cue,
    AUDIO_CUE_SUGGEST_FLOOR, AUDIO_CUE_SUGGEST_MAX_EPISODES,
    AUDIO_CUE_EFFECT_FLOOR, AUDIO_CUE_SNAP_CONFIDENCE, AUDIO_CUE_PAIR_CONFIDENCE,
    AUDIO_CUE_TYPE_CONTENT_TRANSITION,
    AUDIO_CUE_AD_AFFINITY_TOLERANCE_SECONDS,
    AUDIO_CUE_AD_AFFINITY_MIN_FRACTION,
    AUDIO_CUE_AD_AFFINITY_PHASE_FRACTION,
    resolve_cue_template_score,
    resolve_cue_template_score_with_source,
)
from utils.constants import EpisodeStatus
from utils.validation import is_valid_episode_id

# Cap on loud-spot markers returned to the capture UI.
MAX_LOUD_SPOTS = 200

logger = logging.getLogger('podcast.api.cue_templates')

# Template export/import envelope schema version. v2 stores the cue audio as
# FLAC (cue.flac, lossless and ~half the size); v1 stored uncompressed WAV
# (cue.wav). Import accepts both so older packs keep working.
CUE_TEMPLATE_SCHEMA_VERSION = 2
# Hard cap on the audio entry pulled from an imported zip (WAV or FLAC). A 4 s
# 16 kHz mono int16 cue is ~128 KB and its FLAC roughly half that; 5 MB is
# generous headroom and a zip-bomb guard on top of the app-wide 10 MB limit.
MAX_IMPORT_WAV_BYTES = 5 * 1024 * 1024
# Bound the decoded duration of an imported FLAC (flac_to_wav also rejects
# non-mono / non-16kHz before decoding) so a long silent FLAC cannot expand to
# an oversized WAV. 120s is well past any real cue (60s intro/outro ceiling).
MAX_IMPORT_CUE_SECONDS = 120


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
        'cueType': row['cue_type'] if 'cue_type' in row.keys() else AUDIO_CUE_TYPE_DEFAULT,
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
        'hasAudio': bool(row.get('pcm_blob')) or bool(row.get('has_audio')),
    }


@api.route('/feeds/<slug>/cue-templates', methods=['GET'])
@log_request
def list_cue_templates(slug):
    """List a feed's cue templates, including network templates from siblings.

    A template promoted to network scope on any feed in the network is shown
    here too, flagged ``owned: false`` so the UI can render it read-only (it is
    managed on the feed that created it).
    """
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    rows = db.list_cue_templates_for_feed_ui(podcast['id'])
    templates = []
    for r in rows:
        meta = _template_to_meta_dict(r)
        meta['owned'] = r['podcast_id'] == podcast['id']
        templates.append(meta)
    return json_response({'templates': templates})


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
    cue_type = payload.get('cueType', AUDIO_CUE_TYPE_DEFAULT)
    try:
        start_s = float(payload['startS'])
        end_s = float(payload['endS'])
    except (KeyError, TypeError, ValueError):
        return error_response('startS and endS are required numbers', 400)
    if not episode_id or not is_valid_episode_id(episode_id):
        return error_response('episodeId is required and must be valid', 400)
    if cue_type not in AUDIO_CUE_TYPES:
        return error_response(
            'cueType must be one of: ' + ', '.join(sorted(AUDIO_CUE_TYPES)), 400)
    cap_min = db.get_setting_float('audio_cue_capture_min_seconds', AUDIO_CUE_CAPTURE_MIN_SECONDS)
    cap_max = _capture_ceiling(db, cue_type)
    if end_s - start_s < cap_min:
        return error_response(f'selection must be at least {cap_min:g} seconds', 400)
    if end_s - start_s > cap_max:
        return error_response(f'selection must be at most {cap_max:g} seconds', 400)

    # Optional scope (defaults to podcast). The UI creates podcast-scope and
    # promotes via PATCH (which surfaces the blast radius), but the API accepts
    # scope on create for programmatic clients.
    scope = payload.get('scope', 'podcast')
    if scope not in ('podcast', 'network'):
        return error_response("scope must be 'podcast' or 'network'", 400)
    network_id = None
    if scope == 'network':
        network_id = (payload.get('networkId') or '').strip()
        if not network_id:
            return error_response('networkId is required for network scope', 400)

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
            'selection too short; widen it or pick a louder cue',
            400,
        )

    template_id = db.create_cue_template(
        podcast_id=podcast['id'],
        cue_type=cue_type,
        source_episode_id=episode_id,
        source_offset_s=start_s,
        duration_s=round(end_s - start_s, 3),
        sample_rate=SAMPLE_RATE_HZ,
        n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=pcm_to_int16_bytes(pcm),
        pcm_sample_rate=SAMPLE_RATE_HZ,
        scope=scope,
        network_id=network_id,
    )
    logger.info(
        f"Cue template created: id={template_id} feed={slug} ep={episode_id} "
        f"window={start_s:.2f}-{end_s:.2f}s cue_type={cue_type!r}"
    )
    row = db.get_cue_template(template_id)
    meta = _template_to_meta_dict(row)

    # Weak-cue feedback: an ad-break cue that appears only once in its own
    # source episode will never bracket a break, so warn the user at save time.
    # Skip intro/outro (non_ad) cues -- they are meant to play once. Best-effort:
    # an fpcalc failure leaves selfMatchCount at 0 (treated as unknown, no warn).
    is_ad_role = audio_cue_type_role(cue_type) != AUDIO_CUE_ROLE_NON_AD
    self_match_count = 0
    if is_ad_role:
        try:
            similarity = db.get_setting_float(
                'audio_cue_recurrence_similarity', AUDIO_CUE_RECURRENCE_SIMILARITY)
            self_match_count = AudioFingerprinter().count_self_matches(
                audio_path, start_s, end_s, similarity=similarity)
        except Exception:
            logger.exception('cue self-match failed for template %s', template_id)
    meta['selfMatchCount'] = self_match_count
    meta['weakCue'] = self_match_count == 1

    # Long-capture nudge: ad-break captures longer than the warn threshold
    # degrade match quality (issue #350: 9.8s capture matched far worse than
    # 1.5-2.5s clips of the same cue). Non-ad roles are exempt -- long intro/
    # outro captures are expected and intentional.
    meta['longCapture'] = (
        is_ad_role and (end_s - start_s) > AUDIO_CUE_CAPTURE_WARN_AD_SECONDS)
    meta['captureWarnSeconds'] = AUDIO_CUE_CAPTURE_WARN_AD_SECONDS
    return json_response({'template': meta}, status=201)


@api.route('/cue-templates/<int:template_id>', methods=['PATCH'])
@log_request
def update_cue_template_route(template_id):
    """Rename / enable / disable a template.

    Addressed by global id with no feed scoping: a network template shared into
    a sibling feed renders read-only in that feed's panel (the UI `owned` flag),
    but the mutation itself is intentionally not feed-gated -- the app runs
    behind one shared operator password, so this guards against an accidental
    edit, not an adversary. Add a server-side ownership check here if per-feed
    auth is ever introduced.
    """
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    payload = request.get_json(silent=True) or {}
    new_cue_type = payload.get('cueType')
    enabled = payload.get('enabled')
    if new_cue_type is not None and new_cue_type not in AUDIO_CUE_TYPES:
        return error_response(
            'cueType must be one of: ' + ', '.join(sorted(AUDIO_CUE_TYPES)), 400)
    if enabled is not None and not isinstance(enabled, bool):
        return error_response('enabled must be true or false', 400)

    # Validate the optional scope change BEFORE any write so an invalid scope
    # cannot leave a half-applied label/enabled change behind.
    scope = None
    network_id = None
    if 'scope' in payload:
        scope = payload.get('scope')
        if scope not in ('podcast', 'network'):
            return error_response("scope must be 'podcast' or 'network'", 400)
        if scope == 'network':
            network_id = (payload.get('networkId') or '').strip()
            if not network_id:
                return error_response('networkId is required to promote to network scope', 400)

    db.update_cue_template(template_id, cue_type=new_cue_type, enabled=enabled)
    if scope is not None:
        db.promote_cue_template(template_id, scope, network_id)

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
        threshold_source = 'request'
    else:
        score, threshold_source = resolve_cue_template_score_with_source(db, podcast['id'])
    matcher = AudioCueTemplateMatcher(
        templates=templates, score_threshold=score,
        formant_atten_db=db.get_setting_float(
            'audio_cue_formant_atten_db', AUDIO_CUE_FORMANT_ATTEN_DB),
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
        'thresholdSource': threshold_source,
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

    score, _ = resolve_cue_template_score_with_source(db, podcast['id'])
    matcher = AudioCueTemplateMatcher(
        templates=[template], score_threshold=score,
        formant_atten_db=db.get_setting_float(
            'audio_cue_formant_atten_db', AUDIO_CUE_FORMANT_ATTEN_DB),
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


@api.route('/cue-templates/<int:template_id>/export', methods=['GET'])
@log_request
def export_cue_template(template_id):
    """Export a template as a zip: a lossless FLAC of the captured cue plus a
    JSON manifest. Round-trips between a user's own or trusted installs.
    """
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    pcm_blob = row.get('pcm_blob')
    if not pcm_blob:
        return error_response(
            'this template has no raw audio to export',
            422,
        )
    sample_rate = int(row.get('pcm_sample_rate') or SAMPLE_RATE_HZ)

    try:
        flac_bytes = pcm_to_flac(pcm_blob, sample_rate)
    except RuntimeError as e:
        return error_response(f'could not encode cue audio: {e}', 500)

    manifest = {
        'schemaVersion': CUE_TEMPLATE_SCHEMA_VERSION,
        'appVersion': _get_version(),
        'label': row['label'],
        'cueType': row['cue_type'] if 'cue_type' in row.keys() else AUDIO_CUE_TYPE_DEFAULT,
        'durationS': row['duration_s'],
        'sampleRate': sample_rate,
        'nCoeffs': row['n_coeffs'],
        'sourceOffsetS': row['source_offset_s'],
        'audioFile': 'cue.flac',
    }
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('cue.flac', flac_bytes)
        z.writestr('template.json', json.dumps(manifest, indent=2))
    zip_buf.seek(0)

    safe_label = ''.join(c if c.isalnum() else '-' for c in row['label'])[:40] or 'cue'
    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'cue-{template_id}-{safe_label}.zip',
    )


@api.route('/cue-templates/<int:template_id>/audio', methods=['GET'])
@log_request
def cue_template_audio(template_id):
    """Stream a template's stored cue audio as an inline WAV for in-app playback.

    Built from the retained int16 PCM blob (no ffmpeg; cues are short). Inline
    (not an attachment) so an <audio> element can play it directly.
    """
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    pcm_blob = row.get('pcm_blob')
    if not pcm_blob:
        return error_response('this template has no raw audio to play', 422)
    sample_rate = int(row.get('pcm_sample_rate') or SAMPLE_RATE_HZ)

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_blob)
    wav_buf.seek(0)
    return send_file(
        wav_buf,
        mimetype='audio/wav',
        as_attachment=False,
        download_name=f'cue-{template_id}.wav',
    )


@api.route('/feeds/<slug>/cue-templates/import', methods=['POST'])
@log_request
def import_cue_template(slug):
    """Import a template zip (FLAC or WAV audio + manifest) into a feed.

    The MFCC is recomputed from the decoded audio here -- a foreign MFCC blob is
    never trusted. v2 packs carry cue.flac; v1 packs carry cue.wav; both are
    accepted. Imports land as podcast scope; network scope is install-specific
    and is promoted explicitly after import. Sample-rate / channel mismatches
    are hard-rejected (no resampling).
    """
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)

    upload = request.files.get('file')
    if upload is None:
        return error_response('a zip file is required (multipart field "file")', 400)

    try:
        with zipfile.ZipFile(upload.stream) as z:
            names = set(z.namelist())
            if 'template.json' not in names:
                return error_response('zip must contain template.json', 400)
            audio_name = ('cue.flac' if 'cue.flac' in names
                          else 'cue.wav' if 'cue.wav' in names else None)
            if audio_name is None:
                return error_response('zip must contain cue.flac or cue.wav', 400)
            # Stream both entries with a hard cap, reading at most one byte past
            # the limit so a zip bomb cannot decompress beyond MAX_IMPORT_WAV_BYTES
            # regardless of what the central directory claims as the size.
            with z.open('template.json') as mf:
                manifest_bytes = mf.read(MAX_IMPORT_WAV_BYTES + 1)
            if len(manifest_bytes) > MAX_IMPORT_WAV_BYTES:
                return error_response('template.json is too large', 400)
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            with z.open(audio_name) as af:
                audio_bytes = af.read(MAX_IMPORT_WAV_BYTES + 1)
            if len(audio_bytes) > MAX_IMPORT_WAV_BYTES:
                return error_response(f'{audio_name} is too large', 400)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return error_response(f'could not read template zip: {e}', 400)

    if 'schemaVersion' not in manifest:
        return error_response('manifest is missing schemaVersion', 400)

    # Normalize to a 16-bit PCM WAV, then run the same validation for both
    # formats. FLAC is decoded preserving its source rate/channels so a mismatch
    # still fails the checks below rather than being silently resampled.
    if audio_name == 'cue.flac':
        try:
            wav_bytes = flac_to_wav(audio_bytes, MAX_IMPORT_CUE_SECONDS)
        except RuntimeError as e:
            return error_response(f'could not decode cue.flac: {e}', 400)
    else:
        wav_bytes = audio_bytes

    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
            if wf.getnchannels() != 1:
                return error_response(
                    f'cue audio must be mono (1 channel), got {wf.getnchannels()}', 400)
            if wf.getsampwidth() != 2:
                return error_response(
                    f'cue audio must be 16-bit PCM (2 bytes/sample), got {wf.getsampwidth()}', 400)
            sr = wf.getframerate()
            if sr != SAMPLE_RATE_HZ:
                return error_response(
                    f'cue audio sample rate must be {SAMPLE_RATE_HZ}, got {sr}', 400)
            frames = wf.readframes(wf.getnframes())
    except wave.Error as e:
        return error_response(f'cue audio is not a valid WAV file: {e}', 400)

    pcm = int16_bytes_to_pcm(frames)
    mfcc = compute_mfcc(pcm)
    if mfcc.shape[0] < 3:
        return error_response('cue audio is too short to import', 400)

    # Older exports (pre-cue-type) carry no cueType; fall back to the default
    # boundary type rather than rejecting a still-valid cue.
    cue_type = manifest.get('cueType', AUDIO_CUE_TYPE_DEFAULT)
    if cue_type not in AUDIO_CUE_TYPES:
        cue_type = AUDIO_CUE_TYPE_DEFAULT
    duration_s = round(len(pcm) / float(SAMPLE_RATE_HZ), 3)
    template_id = db.create_cue_template(
        podcast_id=podcast['id'],
        cue_type=cue_type,
        source_episode_id=None,
        source_offset_s=0.0,
        duration_s=duration_s,
        sample_rate=SAMPLE_RATE_HZ,
        n_coeffs=N_COEFFS,
        mfcc_blob=serialize_mfcc(mfcc),
        pcm_blob=frames,
        pcm_sample_rate=SAMPLE_RATE_HZ,
        created_by='import',
    )
    logger.info(f"Cue template imported: id={template_id} feed={slug} cue_type={cue_type!r}")
    row = db.get_cue_template(template_id)
    return json_response({'template': _template_to_meta_dict(row)}, status=201)


def _scan_loud_spots(db, audio_path, max_duration=AUDIO_CUE_SCAN_MAX_DURATION_SECONDS):
    """Band-pass energy pass over original audio -> loud-spot dicts.

    Uses the generous discovery profile (config.AUDIO_CUE_SCAN_*), not the
    precise live-detection band: it reaches lower in frequency, triggers on a
    smaller rise, captures each burst's full attack/decay via the release
    threshold, and allows long sustained sounds. This surfaces the sustained,
    bass/broadband musical stings real ad breaks use, which the live band misses.
    The recurrence filter downstream keeps false positives down.

    ``max_duration`` is the longest a single burst may span. The capture-UI
    loud-spots endpoint uses the default; the cue-candidate scan passes the
    longer per-type cap so a full-length intro/outro surfaces as one spot.

    Surfaces every burst (min_confidence=0.0). Each dict is
    {start, end, prominenceDb}. Raises on decode failure; the caller decides
    whether that is fatal.
    """
    detector = AudioCueDetector(
        freq_min_hz=AUDIO_CUE_SCAN_FREQ_MIN_HZ,
        freq_max_hz=AUDIO_CUE_FREQ_MAX_HZ,
        prominence_db=AUDIO_CUE_SCAN_PROMINENCE_DB,
        min_confidence=0.0,
        max_duration=max_duration,
        release_db=AUDIO_CUE_SCAN_RELEASE_DB,
    )
    # Cap by prominence (strongest first), not by start time, so a recurring
    # sting late in the episode is not crowded out of the cap by early chatter;
    # return the survivors in time order for the UI.
    spots = sorted(
        detector.detect(str(audio_path)),
        key=lambda s: (s.details or {}).get('prominence_db') or 0.0,
        reverse=True,
    )[:MAX_LOUD_SPOTS]
    return [
        {'start': s.start, 'end': s.end,
         'prominenceDb': (s.details or {}).get('prominence_db')}
        for s in sorted(spots, key=lambda s: s.start)
    ]


@api.route('/feeds/<slug>/episodes/<episode_id>/cue-loud-spots', methods=['GET'])
@log_request
def episode_loud_spots(slug, episode_id):
    """Template-free energy pass over an episode's original audio for the capture
    UI. Returns candidate "loud spots" (band-passed bursts) as jump-to markers
    so the user can find a cue to bracket. These are NOT detected cues -- before
    a template there is nothing to match against -- just loud spots to hunt in.
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
    try:
        loud_spots = _scan_loud_spots(db, audio_path)
    except Exception as e:
        return error_response(f'loud-spot scan failed: {e}', 500)
    return json_response({'episodeId': episode_id, 'loudSpots': loud_spots})


@api.route('/feeds/<slug>/episodes/<episode_id>/detected-cues', methods=['GET'])
@log_request
def episode_detected_cues(slug, episode_id):
    """Template cue matches already found on this episode (instant, advisory).

    Spectral bursts are intentionally excluded: an episode has dozens of one-off
    loud spikes and they are too noisy to suggest as templates. Use the
    cue-candidates endpoint to find sounds that actually recur.
    """
    if not is_valid_episode_id(episode_id):
        abort(400)
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)

    cue_signals = _episode_template_cue_signals(db, slug, episode_id)

    # Cheap existence check (no decode) -- gates whether a template can be cut.
    _, err = _resolve_original_audio(db, storage, slug, episode_id)
    has_original_audio = err is None

    return json_response({
        'episodeId': episode_id,
        'hasOriginalAudio': has_original_audio,
        'detectedCues': build_detected_cues(cue_signals, []),
    })


def _episode_template_cue_signals(db, slug, episode_id):
    """Persisted template-match audio-cue signals for an episode (no decode)."""
    raw = db.get_episode_audio_analysis(slug, episode_id)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [s for s in (data.get('signals') or [])
                if s.get('signal_type') == 'audio_cue' and is_template_cue(s.get('details'))]
    except (ValueError, TypeError):
        return []


def _templated_cue_spans(db, podcast_id, slug, episode_id):
    """Time spans on this episode already covered by a cue template (no decode), so
    the candidate scan can skip cues the user has already captured. Combines two
    sources: persisted template MATCHES on this episode, and enabled templates whose
    source is this episode (a match signal is only stored on reprocessing, so a
    just-captured template would otherwise reappear). Returns [(start, end), ...]."""
    spans = []
    for s in _episode_template_cue_signals(db, slug, episode_id):
        start, end = s.get('start'), s.get('end')
        if isinstance(start, (int, float)) and isinstance(end, (int, float)):
            spans.append((float(start), float(end)))
    for t in db.list_cue_templates_metadata(podcast_id):
        if t.get('source_episode_id') == episode_id and t.get('enabled'):
            off, dur = t.get('source_offset_s'), t.get('duration_s')
            if off is not None and dur:
                spans.append((off, off + dur))
    return spans


def _completed_sibling_audio_paths(db, storage, slug, episode_id):
    """Up to AUDIO_CUE_XEP_MAX_SIBLINGS recent COMPLETED episodes (other than this
    one) that still have retained original audio, as resolvable file paths.

    Completed-only matters: a finished episode's column value is
    EpisodeStatus.PROCESSED ('processed'), which the API displays as 'completed'.
    This excludes discovered/pending/processing/failed episodes from the
    cross-episode comparison.
    """
    episodes, _ = db.get_episodes(
        slug, status=EpisodeStatus.PROCESSED.value,
        limit=AUDIO_CUE_XEP_SIBLING_LOOKBACK)
    paths = []
    for ep in episodes:
        eid = ep.get('episode_id')
        if eid == episode_id or not ep.get('original_file'):
            continue
        path = storage.get_original_path(slug, eid)
        if path.exists():
            paths.append(str(path))
        if len(paths) >= AUDIO_CUE_XEP_MAX_SIBLINGS:
            break
    return paths


def _parse_ad_markers(raw):
    """Parse ad_markers_json into only the markers that were actually cut.

    Mirrors positional_prior's was_cut defense so affinity typing never treats a
    reviewer-rejected marker as a boundary: a raw pass-1 set (no marker carries
    was_cut) is untrusted and yields nothing; otherwise only was_cut markers
    count. Tolerates None/bad JSON (returns []).
    """
    try:
        parsed = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning('cue_templates: unparseable ad_markers_json')
        return []
    if not isinstance(parsed, list):
        return []
    if parsed and not any(isinstance(m, dict) and 'was_cut' in m for m in parsed):
        return []  # raw pass-1 output, never confidence-gated -- untrusted
    return [m for m in parsed if isinstance(m, dict) and m.get('was_cut')]


# Number of top recurring candidates to run sibling matching against.
_AFFINITY_SIBLING_TOP_N = 5
# Max siblings to pull ad history from for sibling-fallback affinity.
_AFFINITY_SIBLING_MAX = 2
# Bounds per-row episode lookups; raise with sibling lookback if ever needed.
_AFFINITY_HISTORY_SCAN_MAX = 8


def _sibling_affinity_fallback(recurring, slug, episode_id, db, storage, audio_path, podcast_id=None):
    """Affinity from up to 2 recent siblings with ad markers + retained audio, when the episode has no ad history."""
    if not recurring:
        return recurring
    for c in recurring:
        c.pop('occurrences', None)
        c['adBoundaryHits'] = None
        c['boundaryAffinity'] = None
        c['affinitySource'] = None

    # Recent siblings with BOTH stored ad markers and retained original audio.
    sibling_rows = db.get_recent_episode_ad_history(
        slug, exclude_episode_id=episode_id,
        limit=AUDIO_CUE_XEP_SIBLING_LOOKBACK)
    usable_siblings = []
    for _i, row in enumerate(sibling_rows):
        if _i >= _AFFINITY_HISTORY_SCAN_MAX:
            break
        sib_eid = row['episode_id']
        sib_ep = db.get_episode(slug, sib_eid)
        if not sib_ep or not sib_ep.get('original_file'):
            continue
        sib_path = str(storage.get_original_path(slug, sib_eid))
        if not os.path.exists(sib_path):
            continue
        ad_spans = _parse_ad_markers(row.get('ad_markers_json'))
        if not ad_spans:
            continue
        usable_siblings.append({'path': sib_path, 'ad_spans': ad_spans})
        if len(usable_siblings) >= _AFFINITY_SIBLING_MAX:
            break
    if not usable_siblings:
        return recurring

    top = recurring[:_AFFINITY_SIBLING_TOP_N]
    rest = recurring[_AFFINITY_SIBLING_TOP_N:]

    # One ephemeral template row per top candidate; row id = index into `top`
    # so matcher signals map back via details['template_id'].
    rows = []
    for idx, c in enumerate(top):
        try:
            pcm = decode_pcm_window(audio_path, c['start'], c['end'], SAMPLE_RATE_HZ)
            mfcc = compute_mfcc(pcm)
        except Exception:
            logger.debug('sibling affinity: failed to decode candidate [%s-%s]',
                         c.get('start'), c.get('end'))
            continue
        if mfcc.shape[0] < 3:
            continue
        rows.append({
            'id': idx,
            'label': f"candidate-{c['start']}-{c['end']}",
            'cue_type': 'ad_break_boundary',
            'duration_s': c['end'] - c['start'],
            'sample_rate': SAMPLE_RATE_HZ,
            'n_coeffs': N_COEFFS,
            'mfcc_blob': serialize_mfcc(mfcc),
            'pcm_blob': None,
        })
    if not rows:
        return recurring

    score_threshold = resolve_cue_template_score(db, podcast_id)
    matcher = AudioCueTemplateMatcher(rows, score_threshold=score_threshold)
    pooled_hits = {r['id']: 0 for r in rows}
    pooled_count = {r['id']: 0 for r in rows}
    if matcher.is_usable:
        for sib in usable_siblings:
            try:
                signals = matcher.detect(sib['path'])
            except Exception:
                logger.debug('sibling affinity: matcher failed for sibling %s',
                             sib['path'])
                continue
            positions = {r['id']: [] for r in rows}
            for s in signals:
                idx = (s.details or {}).get('template_id')
                if idx in positions:
                    positions[idx].append(s.start)
            # Same hit definition as the within-episode annotator, applied to
            # this sibling's match positions vs its own ad spans, then pooled.
            for idx, pos in positions.items():
                if not pos:
                    continue
                hits, _, _ = count_ad_boundary_hits(
                    pos, sib['ad_spans'], AUDIO_CUE_AD_AFFINITY_TOLERANCE_SECONDS)
                pooled_hits[idx] += hits
                pooled_count[idx] += len(pos)

    for idx, c in enumerate(top):
        count = pooled_count.get(idx, 0)
        if count <= 0:
            continue  # no sibling evidence for this candidate; leave untyped
        hits = pooled_hits[idx]
        affinity = hits / count
        c['adBoundaryHits'] = hits
        c['boundaryAffinity'] = round(affinity, 3)
        c['affinitySource'] = 'siblings'
        if hits >= 2 and affinity >= AUDIO_CUE_AD_AFFINITY_MIN_FRACTION:
            # No per-occurrence start/end phase data when pooling across
            # siblings, so the typed result is always the two-sided boundary.
            c['suggestedType'] = 'ad_break_boundary'
        else:
            c['suggestedType'] = AUDIO_CUE_TYPE_CONTENT_TRANSITION

    top.sort(key=lambda c: (-(c.get('boundaryAffinity') or 0), -(c.get('count') or 0)))
    return top + rest


def _drop_speechlike_recurring(recurring, audio_path):
    """Drop recurring candidates that are plainly speech (common phrases), #350.

    Decodes each candidate's audio span and applies the music/speech
    discriminator; only confident speech is removed, so musical stings (even with
    voiceover) survive. On any decode error the candidate is kept. Cross-episode
    intro/outro candidates are NOT passed here -- those are often legitimately
    spoken.
    """
    kept, dropped = [], 0
    for c in recurring:
        try:
            pcm = decode_pcm_window(audio_path, c['start'], c['end'], SAMPLE_RATE_HZ)
            if is_likely_speech(
                pcm, SAMPLE_RATE_HZ,
                lo_hz=AUDIO_CUE_SPEECH_BAND_LO_HZ, hi_hz=AUDIO_CUE_SPEECH_BAND_HI_HZ,
                ratio_max=AUDIO_CUE_SPEECH_BAND_RATIO_MAX,
                flatness_min=AUDIO_CUE_SPEECH_FLATNESS_MIN,
                sustained_max=AUDIO_CUE_SPEECH_SUSTAINED_MAX,
            ):
                dropped += 1
                continue
        except Exception:
            logger.debug('speech filter: decode failed for %s [%s-%s], keeping',
                         audio_path, c.get('start'), c.get('end'))
        kept.append(c)
    if dropped:
        logger.info('cue candidate scan: dropped %d speech-like recurring candidate(s)', dropped)
    return kept


def _capture_ceiling(db, cue_type):
    """Return the DB-configured max capture duration (s) for ``cue_type``.

    For show_intro and show_outro the per-type DB key is checked first and
    the result is never less than the global ad-break ceiling.
    """
    cap_max = db.get_setting_float('audio_cue_capture_max_seconds', AUDIO_CUE_CAPTURE_MAX_SECONDS)
    if cue_type in AUDIO_CUE_CAPTURE_MAX_BY_TYPE:
        type_db_key = (
            'audio_cue_capture_max_intro_seconds'
            if cue_type == AUDIO_CUE_TYPE_SHOW_INTRO
            else 'audio_cue_capture_max_outro_seconds'
        )
        cap_max = max(cap_max, db.get_setting_float(
            type_db_key, AUDIO_CUE_CAPTURE_MAX_BY_TYPE[cue_type]))
    return cap_max


def _run_cue_candidate_scan(podcast_id, episode_id, slug, audio_path,
                            similarity, min_count):
    """Background worker: find cue-template candidates, then persist them.

    Two passes: within-episode recurrence (ad-break stings that repeat, found via
    fingerprint self-match) and cross-episode intro/outro (head/tail segments that
    recur across recent completed siblings -- real intros/outros play once per
    episode so recurrence cannot see them). Runs off the request thread because
    decoding can exceed the reverse-proxy timeout; uses its own thread-local DB
    connection.
    """
    db = get_database()
    storage = get_storage()
    try:
        fp = AudioFingerprinter()
        # Fingerprint the target once and share it across both passes. If fpcalc
        # is present but the decode fails, both passes would fail too -- surface
        # the error instead of re-decoding three times.
        target_fp = None
        if fp.is_available():
            target_fp = fp._generate_full_fingerprint(audio_path)
            if target_fp is None:
                raise RuntimeError(f'fingerprint decode failed for {audio_path}')
        recurring = fp.discover_recurring_spots(
            audio_path, similarity=similarity, min_count=min_count,
            target_fingerprint=target_fp)
        # Drop within-episode candidates that are just common spoken phrases (#350);
        # the cross-episode intro/outro pass below is exempt (intros can be spoken).
        recurring = _drop_speechlike_recurring(recurring, audio_path)
        # Phase 4: ad-affinity typing -- annotate recurring candidates with
        # suggestedType based on proximity to known ad boundaries.
        episode_row = db.get_episode(slug, episode_id)
        episode_ad_spans = _parse_ad_markers(
            (episode_row or {}).get('ad_markers_json')
        )
        if episode_ad_spans:
            recurring = annotate_recurring_with_ad_affinity(
                recurring, episode_ad_spans,
                tolerance_s=AUDIO_CUE_AD_AFFINITY_TOLERANCE_SECONDS,
                min_fraction=AUDIO_CUE_AD_AFFINITY_MIN_FRACTION,
                phase_fraction=AUDIO_CUE_AD_AFFINITY_PHASE_FRACTION,
            )
            for c in recurring:
                if c.get('affinitySource') is None and c.get('adBoundaryHits') is not None:
                    c['affinitySource'] = 'episode'
        else:
            # Sibling fallback: only when scanned episode has no ad history.
            recurring = _sibling_affinity_fallback(
                recurring, slug, episode_id, db, storage, audio_path,
                podcast_id=podcast_id)
        try:
            siblings = _completed_sibling_audio_paths(db, storage, slug, episode_id)
            cross_episode = fp.discover_cross_episode_cues(
                audio_path, siblings,
                head_seconds=AUDIO_CUE_XEP_HEAD_SECONDS,
                tail_seconds=AUDIO_CUE_XEP_TAIL_SECONDS,
                window_seconds=AUDIO_CUE_FP_WINDOW_SECONDS,
                similarity=AUDIO_CUE_XEP_SIMILARITY,
                min_matches=AUDIO_CUE_XEP_MIN_MATCHES,
                min_duration=AUDIO_CUE_XEP_MIN_DURATION,
                intro_max_duration=_capture_ceiling(db, AUDIO_CUE_TYPE_SHOW_INTRO),
                outro_max_duration=_capture_ceiling(db, AUDIO_CUE_TYPE_SHOW_OUTRO),
                max_per_zone=AUDIO_CUE_XEP_MAX_PER_ZONE,
                target_fingerprint=target_fp)
        except Exception:
            logger.exception(
                'cross-episode pass failed for %s/%s; using recurrence only',
                slug, episode_id)
            cross_episode = []
        templated = _templated_cue_spans(db, podcast_id, slug, episode_id)
        candidates = merge_cue_candidates(recurring, cross_episode, templated)
        # Stamp the schema version so caches produced before the speech filter
        # (#350 4A) read as stale and get rescanned once (the filter applies
        # retroactively).
        for c in candidates:
            c['sv'] = CUE_CANDIDATE_SCHEMA_VERSION
        db.save_cue_candidate_scan_result(podcast_id, episode_id, candidates)
    except Exception as e:
        logger.exception('cue candidate scan failed for %s/%s', podcast_id, episode_id)
        db.save_cue_candidate_scan_error(podcast_id, episode_id, str(e))


# Bumped when the candidate set's meaning changes so old caches are rescanned.
# 2: the within-episode speech filter (#350 4A) -- a 2.28.0 cache can still hold
# speech-like recurring candidates the filter now drops, so force a rescan.
# 3: per-zone intro/outro caps (#350 Phase 3) -- old caches used a shared
#    AUDIO_CUE_XEP_MAX_DURATION=30s cap for both zones; the new per-DB-setting
#    caps may produce longer suggestions, so old caches are stale.
# 4: ad-affinity typing (#350 Phase 4) -- recurring candidates now carry
#    suggestedType, adBoundaryHits, boundaryAffinity, affinitySource; old
#    caches lack these fields, so force a rescan to populate them.
CUE_CANDIDATE_SCHEMA_VERSION = 4


def _candidates_are_current(candidates):
    """Stale-cache guard. A scan from before 2.27.2 used kinds/fields this version
    no longer emits (one_off / prominenceDb); a scan from before 2.29.0 predates
    the recurring speech filter. Both lack the current schema version stamp, so
    they are treated as stale and rescanned rather than rendered. An empty result
    is current (nothing stale to show)."""
    return all(
        c.get('kind') in ('recurring', 'intro', 'outro')
        and 'prominenceDb' not in c
        and c.get('sv') == CUE_CANDIDATE_SCHEMA_VERSION
        for c in candidates
    )


@api.route('/feeds/<slug>/episodes/<episode_id>/cue-candidates', methods=['GET'])
@log_request
def episode_cue_candidates(slug, episode_id):
    """Find cue-template candidates in an episode (on-demand).

    Combines within-episode recurring sounds (fingerprint self-repeat, for ad-break
    stings) with cross-episode intro/outro segments (head/tail audio shared across
    recent completed siblings), so candidates cover all cue types. Each is tagged
    with a kind ('recurring'|'intro'|'outro') and a cue-type hint. Decoding can
    exceed the proxy timeout, so the work runs in a background thread and this
    endpoint returns a status the UI polls: 'scanning', 'ready', or 'error'.
    Pass ?rescan=1 to force a fresh scan. Pass ?peek=1 for a read-only check that
    returns a cached result or 'idle' without ever starting a scan.
    """
    if not is_valid_episode_id(episode_id):
        abort(400)
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    podcast_id = podcast['id']
    force = request.args.get('rescan') in ('1', 'true', 'yes')
    peek = request.args.get('peek') in ('1', 'true', 'yes')

    if peek:
        # Read-only: return a cached current result, or 'idle'. Never starts a
        # scan, so opening the capture tool to view/tweak a template costs nothing.
        row = db.get_cue_candidate_scan(podcast_id, episode_id)
        if row and row.get('status') == 'ready':
            candidates = json.loads(row.get('candidates_json') or '[]')
            if _candidates_are_current(candidates):
                return json_response({
                    'episodeId': episode_id, 'status': 'ready', 'candidates': candidates,
                })
        return json_response({'episodeId': episode_id, 'status': 'idle', 'candidates': []})

    state = db.claim_cue_candidate_scan(
        podcast_id, episode_id, AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS, force=force)

    if state == 'ready':
        row = db.get_cue_candidate_scan(podcast_id, episode_id)
        candidates = json.loads((row or {}).get('candidates_json') or '[]')
        if _candidates_are_current(candidates):
            return json_response({
                'episodeId': episode_id, 'status': 'ready', 'candidates': candidates,
            })
        # Cached under an older candidate schema (pre-2.27.2 one_off/prominenceDb);
        # discard and rescan so the UI never renders a stale shape.
        state = db.claim_cue_candidate_scan(
            podcast_id, episode_id, AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS, force=True)
    if state == 'scanning':
        return json_response({'episodeId': episode_id, 'status': 'scanning', 'candidates': []})
    if state == 'error':
        row = db.get_cue_candidate_scan(podcast_id, episode_id)
        return json_response({
            'episodeId': episode_id, 'status': 'error',
            'error': (row or {}).get('error') or 'cue candidate scan failed',
            'candidates': [],
        })

    # state == 'started': resolve the audio and run the scan in the background.
    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    if err:
        db.save_cue_candidate_scan_error(
            podcast_id, episode_id, 'original audio not retained for this episode')
        return err
    similarity = db.get_setting_float(
        'audio_cue_recurrence_similarity', AUDIO_CUE_RECURRENCE_SIMILARITY)
    min_count = int(db.get_setting_float(
        'audio_cue_recurrence_min_count', AUDIO_CUE_RECURRENCE_MIN_COUNT))
    threading.Thread(
        target=_run_cue_candidate_scan,
        args=(podcast_id, episode_id, slug, audio_path, similarity, min_count),
        daemon=True,
        name=f'cue-candidates-{episode_id}',
    ).start()
    return json_response({'episodeId': episode_id, 'status': 'scanning', 'candidates': []})


def _live_effect_floor(db):
    """The lowest confidence at which a cue can affect a cut on this install:
    the hardcoded LLM prompt floor (0.80) and the DB-settable snap floor, plus
    the pair floor only when cue-pair synthesis is enabled."""
    floor = AUDIO_CUE_EFFECT_FLOOR
    floor = min(floor, db.get_setting_float('audio_cue_snap_confidence', AUDIO_CUE_SNAP_CONFIDENCE))
    if db.get_setting_bool('audio_cue_create_from_pairs', default=False):
        floor = min(floor, db.get_setting_float('audio_cue_pair_confidence', AUDIO_CUE_PAIR_CONFIDENCE))
    return floor


def _run_cue_threshold_scan(podcast_id, episode_id, slug, audio_paths,
                            templates, formant_atten, effect_floor):
    """Sweep every template across the given episode audio paths at a low floor,
    gather occurrence scores, and store a suggested global threshold."""
    db = get_database()
    try:
        scores = []
        peaks = {}
        # The matcher is stateless across episodes (audio is decoded inside
        # detect_with_debug), so build it once instead of per episode.
        matcher = AudioCueTemplateMatcher(
            templates,
            score_threshold=AUDIO_CUE_SUGGEST_FLOOR,
            max_matches_per_template=200,
            formant_atten_db=formant_atten,
        )
        if not matcher.is_usable:
            db.save_cue_threshold_scan_error(
                podcast_id, episode_id, 'cue templates could not be loaded')
            return
        for path in audio_paths:
            signals, debug = matcher.detect_with_debug(path)
            for s in signals:
                scores.append((s.details or {}).get('score', s.confidence))
            for t in debug.get('templates', []):
                peaks[t['id']] = max(peaks.get(t['id'], 0.0), t.get('peak_score', 0.0))
        suggestion = suggest_cue_threshold(scores, effect_floor=effect_floor)
        per_feed_val = db.get_podcast_cue_score_override(podcast_id)
        current_threshold = resolve_cue_template_score(db, podcast_id)
        db.save_cue_threshold_scan_result(podcast_id, episode_id, {
            'suggestion': suggestion,
            'sampleEpisodes': len(audio_paths),
            'floorUsed': AUDIO_CUE_SUGGEST_FLOOR,
            'perTemplate': peaks,
            'currentThreshold': current_threshold,
            'scope': 'feed' if per_feed_val is not None else 'global',
        })
    except Exception as e:
        logger.warning(f"Cue threshold scan failed for {slug}:{episode_id}: {e}")
        db.save_cue_threshold_scan_error(podcast_id, episode_id, str(e))


@api.route('/feeds/<slug>/cue-threshold-suggest', methods=['POST'])
@log_request
def cue_threshold_suggest(slug):
    """Suggest a global cue match threshold by sweeping this episode and recent
    siblings at a low floor and gap-finding between noise and signal. Backgrounded
    (multi-episode decode); the client polls status 'scanning'|'ready'|'error'."""
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)
    data = request.get_json(silent=True) or {}
    episode_id = data.get('episodeId')
    if not episode_id or not is_valid_episode_id(episode_id):
        return error_response('a valid episodeId is required', 400)
    podcast_id = podcast['id']
    force = bool(data.get('rescan'))

    state = db.claim_cue_threshold_scan(
        podcast_id, episode_id, AUDIO_CUE_CANDIDATE_SCAN_STALE_SECONDS, force=force)
    if state == 'ready':
        row = db.get_cue_threshold_scan(podcast_id, episode_id)
        result = json.loads((row or {}).get('result_json') or '{}')
        return json_response({'episodeId': episode_id, 'status': 'ready', **result})
    elif state == 'scanning':
        return json_response({'episodeId': episode_id, 'status': 'scanning'})
    elif state == 'error':
        row = db.get_cue_threshold_scan(podcast_id, episode_id)
        return json_response({'episodeId': episode_id, 'status': 'error',
                              'error': (row or {}).get('error') or 'threshold scan failed'})

    # state == 'started': resolve audio, load templates, sweep in background.
    templates = db.list_active_cue_templates_for_feed(podcast_id)  # same loader as cue-scan
    if not templates:
        db.save_cue_threshold_scan_error(podcast_id, episode_id, 'no cue templates on this feed')
        return error_response('mark at least one cue first', 400)
    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    if err:
        db.save_cue_threshold_scan_error(podcast_id, episode_id, 'original audio not retained for this episode')
        return err
    siblings = _completed_sibling_audio_paths(db, storage, slug, episode_id)
    audio_paths = [str(audio_path)] + siblings[:AUDIO_CUE_SUGGEST_MAX_EPISODES - 1]
    formant_atten = db.get_setting_float('audio_cue_formant_atten_db', AUDIO_CUE_FORMANT_ATTEN_DB)
    effect_floor = _live_effect_floor(db)
    threading.Thread(
        target=_run_cue_threshold_scan,
        args=(podcast_id, episode_id, slug, audio_paths, templates, formant_atten, effect_floor),
        daemon=True,
        name=f'cue-threshold-{episode_id}',
    ).start()
    return json_response({'episodeId': episode_id, 'status': 'scanning'})
