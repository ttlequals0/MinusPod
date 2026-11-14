"""Context-window primitives; top level and dependency-free."""

# Rough chars-per-token for prompt size estimates.
CHARS_PER_TOKEN = 4


class ContextOverflowError(Exception):
    """A prompt exceeded the model's context window; terminal, the same prompt overflows again."""
