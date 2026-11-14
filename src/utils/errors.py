"""Shared exception types.

Dependency-free so any module can import them without cycles.
"""


class ServiceUnavailableError(Exception):
    """A required external service (LLM provider or Whisper endpoint) is unreachable.

    Covers connection refused, DNS failure, timeout and persistent 5xx. Raised only where an
    episode would otherwise be marked failed, so the offline queue (#482) can defer it. Never
    raised for auth, rate-limit or response-content errors: deferral would hide those.
    """

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service  # 'llm' or 'whisper'


class TranscriptionRejectedError(Exception):
    """The transcription API rejected a request or exhausted its throttle retry window."""

    def __init__(self, status: int, detail: str = ''):
        super().__init__(f"Whisper API returned {status}{(': ' + detail) if detail else ''}")
        self.status = status


class LocalTranscriptionUnavailableError(RuntimeError):
    """The local Whisper backend is selected but its packages are not installed."""


class ModelLoadError(RuntimeError):
    """The local Whisper model could not be allocated on the GPU at any precision."""


class AudioExtractionError(Exception):
    """ffmpeg could not extract or decode audio chunks from the source file.

    Distinct from transcription-API failures so the error points at the file, not the
    provider (#556: a malformed cover image aborted every chunk extract). Pass 2 treats it
    like an outage (skip verification, keep the episode); pass 1 lets it propagate.
    """


class AudioExtractionTimeout(AudioExtractionError):
    """ffmpeg ran out of clock on a chunk rather than failing to decode it.

    Separate from the decode case (#644) so the stored error does not blame a source file that is fine.
    """


class AudioTooLargeError(Exception):
    """An episode enclosure exceeds the configured download size cap.

    Permanent for the episode. The operator can raise MAX_AUDIO_DOWNLOAD_MB and reprocess (#493);
    the appended hint keeps that remedy in every stored error message.
    """

    _HINT = "; raise the Max episode download cap in Settings (or the MAX_AUDIO_DOWNLOAD_MB env default) to process it"

    def __init__(self, message: str):
        # Idempotent: copy/pickle reconstruction re-calls cls(*args) with the hinted message.
        if not message.endswith(self._HINT):
            message += self._HINT
        super().__init__(message)


class AudioNotReadyError(Exception):
    """The enclosure could not be fetched, so the run did no work at all."""
