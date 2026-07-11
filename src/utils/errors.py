"""Shared exception types.

Dependency-free so transcriber, llm_client, ad_detector, and the processing
pipeline can all import them without cycles.
"""


class ServiceUnavailableError(Exception):
    """A required external service (LLM provider or Whisper endpoint) is
    unreachable: connection refused, DNS failure, timeout, or persistent 5xx.

    Raised only at the terminal point where an episode would otherwise be
    marked failed, so the offline queue (#482) can defer it instead. Never
    raised for auth, rate-limit, or response-content errors -- those are real
    failures that deferral would only hide.
    """

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service  # 'llm' or 'whisper'


class AudioTooLargeError(Exception):
    """An episode enclosure exceeds the configured download size cap.

    Permanent for the episode: the file will not shrink on retry. The
    operator can raise MAX_AUDIO_DOWNLOAD_MB and reprocess (#493).
    """
    pass
