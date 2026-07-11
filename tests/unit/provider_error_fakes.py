"""Shared fake provider errors for LLM classifier and retry-loop tests."""

from utils.llm_call import call_llm_for_window


class FakeResponse:
    def __init__(self, text=None, headers=None):
        self.text = text
        self.headers = headers or {}


class FakeProviderError(Exception):
    """Mimics an SDK provider error: carries .status_code, .body, .response."""
    def __init__(self, message="", status_code=None, body=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.response = response


def call_window(client, max_retries=5):
    """Invoke call_llm_for_window with test defaults."""
    return call_llm_for_window(
        llm_client=client,
        model="test-model",
        system_prompt="sys",
        prompt="user",
        llm_timeout=1.0,
        max_retries=max_retries,
        max_tokens=4096,
        slug="t",
        episode_id="e",
        window_label="w",
    )
