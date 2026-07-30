"""Prompt placeholder substitution helpers.

Detection, verification, and reviewer prompts all use ``{placeholder}``
substitution rather than appending content unconditionally. ``str.replace``
(not ``str.format``) is intentional so literal ``{{...}}`` JSON examples in
prompt bodies stay intact.
"""
import re

SPONSOR_DATABASE_HEADER = (
    "\n\nDYNAMIC SPONSOR DATABASE (current known sponsors - treat as high confidence):\n"
)


def render_prompt(prompt: str, **vars: str) -> str:
    """Substitute ``{name}`` placeholders in ``prompt`` with provided values.

    Variables without a corresponding placeholder are silently dropped: that
    is the supported way for a user to opt out of an injection by removing
    the placeholder from their customized prompt.
    """
    rendered = prompt
    for name, value in vars.items():
        rendered = rendered.replace('{' + name + '}', value)
    return rendered


def render_prompt_once(prompt: str, **vars: str) -> str:
    """Single-pass ``{name}`` substitution over the template only.

    Required when values carry untrusted text (transcripts, feed
    descriptions): a substituted value is never rescanned, so it cannot
    smuggle another placeholder into the prompt.
    """
    if not vars:
        return prompt
    pattern = re.compile('|'.join(r'\{' + re.escape(k) + r'\}' for k in vars))
    return pattern.sub(lambda m: vars[m.group(0)[1:-1]], prompt)


def format_sponsor_block(sponsor_list: str) -> str:
    """Wrap a non-empty sponsor list with the standard header.

    Empty list returns empty string so substitution does not produce a
    dangling header on prompts whose ``{sponsor_database}`` placeholder is
    left in place.
    """
    if not sponsor_list:
        return ""
    return SPONSOR_DATABASE_HEADER + sponsor_list


OVERRIDE_HEADER = "\n\nADDITIONAL INSTRUCTIONS (these take precedence):\n"


def apply_override(prompt: str, override: str) -> str:
    """Inject an optional per-pass override into an already-rendered prompt.

    Empty/None override leaves the prompt unchanged, so the built-in default
    prompts render byte-identically to today. When the prompt contains an
    ``{override}`` placeholder, the user's text is inserted there verbatim -- they
    control its placement and the wording around it. Otherwise the override is
    appended under a precedence header.
    """
    if not override or not override.strip():
        return prompt.replace('{override}', '') if '{override}' in prompt else prompt
    if '{override}' in prompt:
        return prompt.replace('{override}', override)
    return prompt + OVERRIDE_HEADER + override
