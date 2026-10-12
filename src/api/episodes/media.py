"""Audio, peaks and transcript routes."""

from flask import request, send_file, abort
from werkzeug.utils import secure_filename
from api import (
    api, log_request, json_response, error_response,
    get_database, get_storage, extract_transcript_segment, _resolve_original_audio,
)
from audio_peaks import compute_peaks, PeaksError
from split_planning import build_split_candidates, build_split_pieces
from utils.markers import find_marker_in_list
from utils.text import extract_timed_spans_in_range
from api.episodes import common


def _float_arg(name, default=None):
    """Float query arg: missing or empty returns default, junk aborts 400."""
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default
    try:
        return float(raw)
    except ValueError:
        abort(400, description=f"{name} must be a number")


@api.route('/feeds/<slug>/episodes/<episode_id>/transcript', methods=['GET'])
@log_request
def get_transcript(slug, episode_id):
    """Get episode transcript."""
    storage = get_storage()

    transcript = storage.get_transcript(slug, episode_id)
    if not transcript:
        return error_response('Transcript not found', 404)

    return json_response({
        'episodeId': episode_id,
        'transcript': transcript
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/original-transcript', methods=['GET'])
@log_request
def get_original_transcript(slug, episode_id):
    """Get original (pre-cut) transcript for an episode."""
    db = get_database()

    transcript = db.get_original_transcript(slug, episode_id)
    if not transcript:
        return error_response('Original transcript not found', 404)

    return json_response({
        'episodeId': episode_id,
        'originalTranscript': transcript
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/original-segments', methods=['GET'])
@log_request
def get_original_segments(slug, episode_id):
    """Get original (pre-cut) Whisper segments JSON for an episode."""
    db = get_database()

    segments = db.get_original_segments(slug, episode_id)
    if segments is None:
        return error_response('Original segments not found', 404)

    return json_response({
        'episodeId': episode_id,
        'segments': segments
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/final-segments', methods=['GET'])
@log_request
def get_final_segments(slug, episode_id):
    """Get final (post-cut) segments JSON for an episode."""
    db = get_database()

    segments = db.get_final_segments(slug, episode_id)
    if segments is None:
        return error_response('Final segments not found', 404)

    return json_response({
        'episodeId': episode_id,
        'segments': segments
    })


def _wants_download():
    return request.args.get('download') == '1'


def _download_name(title, episode_id, suffix):
    """`<Title_words>-<suffix>.mp3`, title capped so the name stays under
    filesystem limits; falls back to the episode id."""
    return f"{secure_filename(title or '')[:120] or episode_id}-{suffix}.mp3"


def _audio_response(path, download_name=None):
    """Stream an mp3, or send it as an attachment when a name is given."""
    response = send_file(path, mimetype='audio/mpeg', conditional=True,
                         as_attachment=download_name is not None,
                         download_name=download_name)
    # Advertise byte-range support so the wavesurfer-based AdEditor can seek
    # without re-downloading the file. Without this header some clients
    # download serially and refuse to seek past the buffered tail.
    response.headers['Accept-Ranges'] = 'bytes'
    return response


@api.route('/feeds/<slug>/episodes/<episode_id>/original.mp3', methods=['GET'])
@log_request
def serve_original_audio(slug, episode_id):
    """Serve the retained pre-cut audio for ad-editor Review mode.

    Returns 404 when the episode has no original retained (global setting
    off, old episode processed before the feature, or retention expired).
    """
    # Blueprint url_value_preprocessor validated `slug` and `episode_id`.
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    path, err = _resolve_original_audio(db, get_storage(), slug, episode_id,
                                        self_heal=True, episode=episode)
    if err is not None:
        return err
    name = _download_name(episode.get('title'), episode_id, 'original') if _wants_download() else None
    return _audio_response(path, name)


@api.route('/feeds/<slug>/episodes/<episode_id>/processed.mp3', methods=['GET'])
@log_request
def serve_processed_audio(slug, episode_id):
    """Serve the current cut under session auth; keyed on the file, not the
    status, so the last cut stays downloadable during a reprocess."""
    episode = get_database().get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    path = get_storage().get_episode_path(
        slug, episode_id, version=episode.get('processed_version') or 0)
    if not path.exists():
        return error_response('Processed audio not available for this episode', 404)
    name = _download_name(episode.get('title'), episode_id, 'cut') if _wants_download() else None
    return _audio_response(path, name)


@api.route('/feeds/<slug>/episodes/<episode_id>/peaks', methods=['GET'])
@log_request
def get_episode_peaks(slug, episode_id):
    """Return waveform peaks for the requested window of an episode.

    Query params:
        start    (float, default 0)         - window start in seconds
        end      (float, default duration)  - window end in seconds
        resolution_ms (int, default 50)     - peak bucket width

    Used by the AdEditor's wavesurfer view to render a waveform scoped to
    the current ad selection plus a user-selectable context window.
    """
    db = get_database()
    storage = get_storage()
    path, err = _resolve_original_audio(db, storage, slug, episode_id, self_heal=True)
    if err is not None:
        return err

    def _i(name, default):
        raw = request.args.get(name)
        if raw is None or raw == '':
            return default
        try:
            return int(raw)
        except ValueError:
            abort(400, description=f"{name} must be an integer")

    start_seconds = _float_arg('start', 0.0) or 0.0
    end_seconds = _float_arg('end')
    resolution_ms = _i('resolution_ms', 50)

    try:
        peaks, effective_resolution_ms = compute_peaks(
            path,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            resolution_ms=resolution_ms,
        )
    except PeaksError as e:
        return error_response(str(e), 400)

    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        # Echo the *effective* resolution. Server auto-coarsens for very
        # long windows so the JSON payload stays bounded; callers should
        # render based on this value, not the one they requested.
        'resolutionMs': effective_resolution_ms,
        'peaks': peaks,
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/transcript-span', methods=['GET'])
@log_request
def get_episode_transcript_span(slug, episode_id):
    """Return the transcript text spanning a [start, end] window.

    Used by the AdEditor create mode to auto-populate the text_template
    field from the currently selected window of the episode's VTT.
    """
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    start_seconds = _float_arg('start', 0.0) or 0.0
    end_seconds = _float_arg('end')
    if end_seconds is None:
        return error_response('end is required', 400)
    if start_seconds < 0 or end_seconds <= start_seconds:
        return error_response('require 0 <= start < end', 400)
    duration = episode.get('original_duration') or 0
    if duration and end_seconds > duration + 1:
        return error_response(
            f'end ({end_seconds}) exceeds episode duration ({duration})', 400
        )

    transcript = db.get_transcript_for_timestamps(slug, episode_id)
    if not transcript:
        return error_response('Transcript not available for this episode', 404)

    text = extract_transcript_segment(transcript, start_seconds, end_seconds)
    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        'text': text or '',
    })


@api.route('/feeds/<slug>/episodes/<episode_id>/split-candidates', methods=['GET'])
@log_request
def get_episode_split_candidates(slug, episode_id):
    """Propose divider points for a marker spanning several sponsors.

    A span with no transition phrase returns one piece and no candidates,
    a valid answer rather than an error (issue #563).
    """
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    start_seconds = _float_arg('start')
    end_seconds = _float_arg('end')
    if start_seconds is None or end_seconds is None:
        return error_response('start and end are required', 400)
    if start_seconds < 0 or end_seconds <= start_seconds:
        return error_response('require 0 <= start < end', 400)

    transcript = db.get_transcript_for_timestamps(slug, episode_id)
    spans = extract_timed_spans_in_range(
        transcript or '', start_seconds, end_seconds)
    # Same divider sources pattern learning splits on, so the editor never
    # offers a different set than the automatic split would have taken.
    marker = find_marker_in_list(
        common._markers_from_row(episode), start_seconds, end_seconds) or {}
    # main_app owns the long-lived services; a module-level import here would
    # be a cycle, and a per-request matcher reloads the sponsor registry.
    from main_app import pattern_service
    matcher = pattern_service.text_pattern_matcher()
    members, cuts, brands = matcher.split_sources(marker)
    patterns = matcher.brand_patterns()
    candidates = build_split_candidates(
        spans, start_seconds, end_seconds,
        members=members, brands=brands, cuts=cuts, compiled=patterns)
    pieces = build_split_pieces(
        spans, start_seconds, end_seconds, [c['time'] for c in candidates],
        brands=brands, compiled=patterns)
    return json_response({
        'episodeId': episode_id,
        'start': start_seconds,
        'end': end_seconds,
        'candidates': candidates,
        'pieces': pieces,
    })
