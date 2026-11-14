"""Prompt ``{placeholder}`` substitution helpers.

``str.replace``, not ``str.format``, so literal ``{...}`` JSON examples in
prompt bodies stay intact (and format-string injection is avoided).
"""
import re

from config import DETECTION_NOTES_MAX_CHARS, coerce_bool_setting

SPONSOR_DATABASE_HEADER = (
    "\n\nDYNAMIC SPONSOR DATABASE (current known sponsors - treat as high confidence):\n"
)


def render_prompt(prompt: str, **vars: str) -> str:
    """Substitute ``{name}`` placeholders in ``prompt``.

    Variables without a placeholder are dropped: removing a placeholder from a
    customized prompt is how a user opts out of that injection.
    """
    rendered = prompt
    for name, value in vars.items():
        rendered = rendered.replace('{' + name + '}', value)
    return rendered


def render_prompt_once(prompt: str, **vars: str) -> str:
    """Single-pass ``{name}`` substitution over the template only.

    For untrusted values (transcripts, feed descriptions): a substituted value
    is never rescanned, so it cannot smuggle in another placeholder.
    """
    if not vars:
        return prompt
    pattern = re.compile('|'.join(r'\{' + re.escape(k) + r'\}' for k in vars))
    return pattern.sub(lambda m: vars[m.group(0)[1:-1]], prompt)


def format_sponsor_block(sponsor_list: str) -> str:
    """Wrap a non-empty sponsor list with the standard header.

    An empty list returns "" so no dangling header is left behind.
    """
    if not sponsor_list:
        return ""
    return SPONSOR_DATABASE_HEADER + sponsor_list


def seed_sponsors_enabled(db, toggle_key: str, log) -> bool:
    """Whether the given seed-sponsors toggle is on. Fails open (True):
    a missing or unreadable setting must not silently strip the block."""
    try:
        value = db.get_setting(toggle_key)
    except Exception as e:
        log.warning(f"Could not read {toggle_key}: {e}")
        return True
    if value is None:
        return True
    return coerce_bool_setting(value)


OVERRIDE_HEADER = "\n\nADDITIONAL INSTRUCTIONS (these take precedence):\n"


def apply_override(prompt: str, override: str) -> str:
    """Inject an optional per-pass override into an already-rendered prompt.

    An empty override leaves the prompt unchanged. With an ``{override}``
    placeholder, the text is inserted there verbatim; otherwise it is appended
    under a precedence header.
    """
    if not override or not override.strip():
        return prompt.replace('{override}', '') if '{override}' in prompt else prompt
    if '{override}' in prompt:
        return prompt.replace('{override}', override)
    return prompt + OVERRIDE_HEADER + override

def strip_html(text: str|None) -> str:
    """Convert simple HTML to plain text for show-note timestamp parsing.

    Block-level tags become newlines so `_TIMESTAMP_PATTERNS` sees each
    timestamp on its own line; a bare tag-stripper (nh3) would collapse
    `<p>00:00 A</p><p>05:30 B</p>` into `00:00 A05:30 B`.
    """
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|li|div)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')):
        text = text.replace(entity, char)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def scrub_description(description: str|None, max_length: int|None = None, split_location: float = 1.0) -> str:
    """Strip HTML, timestamps, URL paths and extra whitespace, then truncate to
    `max_length` characters (None for no limit) on word boundaries.

    split_location is the fraction of `max_length` kept from the start; the
    rest comes from the end with an ellipsis between. Default 1.0 keeps the start.
    """
    if not description:
        return ""
    if max_length is None:
        max_length = 1 << 30 # effectively no limit
    description = strip_html(description)
    # replace timestamps with 'XX:XX' to avoid hallucinations
    description = re.sub(r'(?:\d+:)?\d{1,2}:\d{2}', 'XX:XX', description)
    # shorten urls (keep scheme and domain)
    description = re.sub(r'(https?://[^/\s]+)/\S+', r'\1/...', description)
    # remove trailing whitespace and empty lines
    description = re.sub(r'(?:\s*\n)+', '\n', description)
    if len(description) > max_length:
        # truncate to max_length at the specified split location
        if max_length <= 3:
            return "..."
        # calc split points, taking into account the ellipsis length (3)
        split_location = max(0.0, min(1.0, split_location))
        clip_begin = round((max_length - 3) * split_location)
        clip_end = len(description) - (max_length - 3 - clip_begin)
        # adjust to word boundaries...
        if description[clip_begin] != ' ':
            word_begin = description.rfind(' ', 0, clip_begin)
            if word_begin >= 0:
                clip_begin = word_begin + 1 # include space
        if description[clip_end - 1] != ' ':
            clip_end = max(clip_end, description.find(' ', clip_end))
        description = description[:clip_begin] + "..." + description[clip_end:]
    return description

def format_detection_notes_block(notes: str|None) -> str:
    """Prompt block for per-feed operator notes, or "" when there are none."""
    # Operator text is trusted: strip HTML only, so a cited time like 1:30 survives.
    notes = re.sub(r'(?:\s*\n)+', '\n', strip_html(notes or '')).strip()[:DETECTION_NOTES_MAX_CHARS]
    if not notes:
        return ""
    return (
        "Operator notes for this show (written by the person running this server; "
        "they describe how this show structures its ads and override the general "
        f"guidance above when they apply):\n{notes}\n\n"
    )

def strip_comments_from_prompt(prompt: str|None) -> str:
    """Remove HTML-style comments from the prompt, following markdown conventions.

    Multiline comments must start a line (up to three leading spaces) and take
    their trailing line break with them; single-line comments can appear
    anywhere. Comments do not nest.

    Examples:
        Text <!-- This is a single-line comment -->
        <!--
        This is a multiline comment
        -->
           <!-- This is a [multiline] comment (<=3 spaces after BOL) -->
            <!-- Not a comment (>3 spaces after BOL) -->
        Text <!--
            Not a comment (text before multiline comment)
            -->
    """
    if not prompt:
        return ""
    pattern = (r'^([ ]{0,3})<!--(?:.|\n)*?-->(?:\r?\n)?' # multiline (1 or more) comment
               r'|^([ ]{4,}<!--.*?-->)' # keep literal single-line comment
               r'|<!--.*?-->') # single-line comment (dot does NOT match newlines)
    return re.sub(pattern, r'\1\2', prompt, flags=re.MULTILINE)
