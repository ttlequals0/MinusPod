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
)
from audio_analysis.cue_template_matcher import (
    AudioCueTemplateMatcher, DEFAULT_MATCH_SCORE,
)
from audio_analysis.cue_detector import AudioCueDetector
from audio_analysis.detected_cues import build_detected_cues
from config import (
    AUDIO_CUE_CAPTURE_MIN_SECONDS, AUDIO_CUE_CAPTURE_MAX_SECONDS,
    AUDIO_CUE_CAPTURE_MAX_BY_TYPE,
    AUDIO_CUE_FREQ_MIN_HZ, AUDIO_CUE_FREQ_MAX_HZ, AUDIO_CUE_PROMINENCE_DB,
    AUDIO_CUE_TYPES, AUDIO_CUE_TYPE_DEFAULT,
)
from utils.validation import is_valid_episode_id

# Cap on loud-spot markers returned to the capture UI.
MAX_LOUD_SPOTS = 200

logger = logging.getLogger('podcast.api.cue_templates')

# Template export/import envelope schema version. Bumped only on a breaking
# change to the zip layout or manifest fields; for now import just checks the
# field is present and parses (no migration / gating).
CUE_TEMPLATE_SCHEMA_VERSION = 1
# Hard cap on the decompressed WAV pulled from an imported zip. A 4 s 16 kHz
# mono int16 cue is ~128 KB; 5 MB is generous headroom and a zip-bomb guard on
# top of the app-wide 10 MB request limit.
MAX_IMPORT_WAV_BYTES = 5 * 1024 * 1024


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
    cap_max = db.get_setting_float('audio_cue_capture_max_seconds', AUDIO_CUE_CAPTURE_MAX_SECONDS)
    # Intro/outro stingers may run longer than the ad-break ceiling; never below
    # the user's global setting for the ad-break types.
    type_max = AUDIO_CUE_CAPTURE_MAX_BY_TYPE.get(cue_type)
    if type_max is not None:
        cap_max = max(cap_max, type_max)
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
            'selection too short after framing; widen the selection or pick a louder cue',
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


@api.route('/cue-templates/<int:template_id>/export', methods=['GET'])
@log_request
def export_cue_template(template_id):
    """Export a template as a zip: a lossless WAV of the captured cue plus a
    JSON manifest. Round-trips between a user's own or trusted installs.
    """
    db = get_database()
    row = db.get_cue_template(template_id)
    if not row:
        return error_response('template not found', 404)
    pcm_blob = row.get('pcm_blob')
    if not pcm_blob:
        return error_response(
            'this template has no raw audio to export (created before raw-PCM storage)',
            422,
        )
    sample_rate = int(row.get('pcm_sample_rate') or SAMPLE_RATE_HZ)

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(pcm_blob))

    manifest = {
        'schemaVersion': CUE_TEMPLATE_SCHEMA_VERSION,
        'appVersion': _get_version(),
        'label': row['label'],
        'cueType': row['cue_type'] if 'cue_type' in row.keys() else AUDIO_CUE_TYPE_DEFAULT,
        'durationS': row['duration_s'],
        'sampleRate': sample_rate,
        'nCoeffs': row['n_coeffs'],
        'sourceOffsetS': row['source_offset_s'],
    }
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('cue.wav', wav_buf.getvalue())
        z.writestr('template.json', json.dumps(manifest, indent=2))
    zip_buf.seek(0)

    safe_label = ''.join(c if c.isalnum() else '-' for c in row['label'])[:40] or 'cue'
    return send_file(
        zip_buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'cue-{template_id}-{safe_label}.zip',
    )


@api.route('/feeds/<slug>/cue-templates/import', methods=['POST'])
@log_request
def import_cue_template(slug):
    """Import a template zip (WAV + manifest) into a feed.

    The MFCC is recomputed from the WAV here -- a foreign MFCC blob is never
    trusted. Imports land as podcast scope; network scope is install-specific
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
            names = z.namelist()
            if 'template.json' not in names or 'cue.wav' not in names:
                return error_response('zip must contain template.json and cue.wav', 400)
            # Stream both entries with a hard cap, reading at most one byte past
            # the limit so a zip bomb cannot decompress beyond MAX_IMPORT_WAV_BYTES
            # regardless of what the central directory claims as the size.
            with z.open('template.json') as mf:
                manifest_bytes = mf.read(MAX_IMPORT_WAV_BYTES + 1)
            if len(manifest_bytes) > MAX_IMPORT_WAV_BYTES:
                return error_response('template.json is too large', 400)
            manifest = json.loads(manifest_bytes.decode('utf-8'))
            with z.open('cue.wav') as wf:
                wav_bytes = wf.read(MAX_IMPORT_WAV_BYTES + 1)
            if len(wav_bytes) > MAX_IMPORT_WAV_BYTES:
                return error_response('cue.wav is too large', 400)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as e:
        return error_response(f'could not read template zip: {e}', 400)

    if 'schemaVersion' not in manifest:
        return error_response('manifest is missing schemaVersion', 400)

    try:
        with wave.open(io.BytesIO(wav_bytes), 'rb') as wf:
            if wf.getnchannels() != 1:
                return error_response(
                    f'cue.wav must be mono (1 channel), got {wf.getnchannels()}', 400)
            if wf.getsampwidth() != 2:
                return error_response(
                    f'cue.wav must be 16-bit PCM (2 bytes/sample), got {wf.getsampwidth()}', 400)
            sr = wf.getframerate()
            if sr != SAMPLE_RATE_HZ:
                return error_response(
                    f'cue.wav sample rate must be {SAMPLE_RATE_HZ}, got {sr}', 400)
            frames = wf.readframes(wf.getnframes())
    except wave.Error as e:
        return error_response(f'cue.wav is not a valid WAV file: {e}', 400)

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


def _scan_loud_spots(db, audio_path):
    """Band-pass energy pass over original audio -> loud-spot dicts.

    Surfaces every burst (min_confidence=0.0), sorted by start and capped at
    MAX_LOUD_SPOTS. Each dict is {start, end, prominenceDb}. Raises on decode
    failure; the caller decides whether that is fatal.
    """
    detector = AudioCueDetector(
        freq_min_hz=db.get_setting_float('audio_cue_freq_min_hz', AUDIO_CUE_FREQ_MIN_HZ),
        freq_max_hz=db.get_setting_float('audio_cue_freq_max_hz', AUDIO_CUE_FREQ_MAX_HZ),
        prominence_db=db.get_setting_float('audio_cue_prominence_db', AUDIO_CUE_PROMINENCE_DB),
        min_confidence=0.0,
    )
    signals = sorted(detector.detect(str(audio_path)), key=lambda s: s.start)[:MAX_LOUD_SPOTS]
    return [
        {'start': s.start, 'end': s.end,
         'prominenceDb': (s.details or {}).get('prominence_db')}
        for s in signals
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
    """Audio cues the analysis already found on an episode -- persisted template
    and spectral cues plus template-free loud spots -- as candidates the user can
    promote into a per-feed cue template. Advisory only; nothing here cuts.
    """
    if not is_valid_episode_id(episode_id):
        abort(400)
    db = get_database()
    storage = get_storage()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('feed not found', 404)

    # Persisted audio_cue signals from the episode's saved analysis (free -- we
    # already analyze the whole episode during processing).
    cue_signals = []
    raw = db.get_episode_audio_analysis(slug, episode_id)
    if raw:
        try:
            data = json.loads(raw)
            cue_signals = [s for s in (data.get('signals') or [])
                           if s.get('signal_type') == 'audio_cue']
        except (ValueError, TypeError):
            cue_signals = []

    # Template-free loud spots over the original audio. Needs retained audio; a
    # template can only be cut from the original, so without it there is nothing
    # to promote.
    loud_spots = []
    audio_path, err = _resolve_original_audio(db, storage, slug, episode_id)
    has_original_audio = err is None
    if has_original_audio:
        try:
            loud_spots = _scan_loud_spots(db, audio_path)
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] detected-cues loud-spot scan failed: {e}")

    return json_response({
        'episodeId': episode_id,
        'hasOriginalAudio': has_original_audio,
        'detectedCues': build_detected_cues(cue_signals, loud_spots),
    })
