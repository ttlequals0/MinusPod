"""Audio fingerprint routes of a pattern: source resolution and playback."""
import io
import logging
import math
import os
import tempfile

from flask import abort, request, send_file

from api import (
    api, log_request, error_response, coded_error_response,
    get_database, get_storage, _resolve_original_audio,
)
from config import FFMPEG_SHORT_TIMEOUT
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.api')

MAX_PREVIEW_SECONDS = 600
RETAINED_MATCH_CANDIDATES = 20


def no_audio_response():
    """404 for a pattern that has no fingerprint."""
    return coded_error_response('Pattern has no audio', 404, 'no_audio')


def original_not_retained_response():
    """404 for a pattern whose source episodes no longer keep their original."""
    return coded_error_response('Original audio not retained for this pattern', 404,
                                'original_not_retained')


def _usable_span(start, end):
    return (isinstance(start, (int, float)) and isinstance(end, (int, float))
            and math.isfinite(start) and math.isfinite(end) and end > start)


def resolve_audio_source(db, storage, pattern_id, match_id=None):
    """Pick the playable source span of a pattern's audio.

    Returns (source, None) with source {podcastSlug, episodeId, start, end, path}, or
    (None, error response). Order: the given match, else the newest non-rejected match
    whose original is retained, else the episode the fingerprint was built from.
    """
    fingerprint = db.get_audio_fingerprint(pattern_id)
    if fingerprint is None:
        return None, no_audio_response()
    if match_id is not None:
        match = db.get_fingerprint_match(match_id)
        if match is None or match['pattern_id'] != pattern_id:
            return None, error_response('Match not found', 404)
        candidates = [match]
    else:
        candidates = [
            {'podcast_slug': m['podcast_slug'], 'episode_id': m['episode_id'],
             'match_start': m['match_start'], 'match_end': m['match_end']}
            for m in db.get_retained_fingerprint_matches(pattern_id, RETAINED_MATCH_CANDIDATES)]
        candidates.append({
            'podcast_slug': fingerprint['source_podcast_slug'],
            'episode_id': fingerprint['source_episode_id'],
            'match_start': fingerprint['source_start'], 'match_end': fingerprint['source_end']})
    for cand in candidates:
        if not cand['podcast_slug'] or not cand['episode_id'] or \
                not _usable_span(cand['match_start'], cand['match_end']):
            continue
        path, err = _resolve_original_audio(db, storage, cand['podcast_slug'], cand['episode_id'])
        if err is None:
            return {'podcastSlug': cand['podcast_slug'], 'episodeId': cand['episode_id'],
                    'start': cand['match_start'], 'end': cand['match_end'], 'path': path}, None
    return None, original_not_retained_response()


def _match_id_arg():
    """The optional matchId query argument; digits only, else 400."""
    raw = request.args.get('matchId')
    if raw is None:
        return None
    if not (raw.isascii() and raw.isdigit()):
        abort(400, description='matchId must be an integer')
    return int(raw)


def _slice_to_mp3(path, start, end):
    """Encode [start, end] of an audio file as mp3 bytes; the temp file never outlives the call."""
    fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
    os.close(fd)
    try:
        tracked_run(
            ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-v', 'error',
             '-ss', f'{start:.3f}', '-i', str(path), '-t', f'{end - start:.3f}',
             '-vn', '-c:a', 'libmp3lame', '-q:a', '4', '-f', 'mp3', tmp_path],
            capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT, check=True)
        with open(tmp_path, 'rb') as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if not data:
        raise RuntimeError('ffmpeg produced no audio')
    return data


@api.route('/patterns/<int:pattern_id>/audio/preview', methods=['GET'])
@log_request
def preview_pattern_audio(pattern_id):
    """Stream the source span of a pattern's audio, or of one match, as mp3."""
    db = get_database()
    if not db.get_ad_pattern_by_id(pattern_id):
        return error_response('Pattern not found', 404)
    source, err = resolve_audio_source(db, get_storage(), pattern_id, _match_id_arg())
    if err:
        return err
    end = min(source['end'], source['start'] + MAX_PREVIEW_SECONDS)
    try:
        data = _slice_to_mp3(source['path'], source['start'], end)
    except Exception as e:
        logger.error(f"Audio preview failed for pattern {pattern_id}: {type(e).__name__}")
        return error_response('Could not build the audio preview', 500)
    response = send_file(io.BytesIO(data), mimetype='audio/mpeg', conditional=True,
                         max_age=0, etag=False)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-store'
    return response
