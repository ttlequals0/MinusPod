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

def strip_html(text: str|None) -> str|None:
    """Convert simple HTML to plain text for show-note timestamp parsing.

    Block-level tags must be turned into newlines (not just stripped) so the
    downstream `_TIMESTAMP_PATTERNS` regex sees each timestamp on its own line.
    A bare tag-stripper like nh3 would collapse `<p>00:00 A</p><p>05:30 B</p>`
    into `00:00 A05:30 B` and miss every anchor after the first.
    """
    if not text:
        return text
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|li|div)>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&quot;', '"'), ('&#39;', "'"), ('&nbsp;', ' ')):
        text = text.replace(entity, char)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()

def scrub_description(description: str|None, max_length: int = 800) -> str:
    """Scrub the description of HTML, timestamps, URLs, excessive whitespace,
    and then truncate to the first `max_length` characters, splitting on a
    word boundary and appending ellipsis if needed.
    """
    if not description:
        return ""
    if max_length <= 0:
        return "..."
    description = strip_html(description)
    # replace timestamps with 'XX:XX' to avoid hallucinations
    description = re.sub(r'(?:\d+:)?\d{1,2}:\d{2}', 'XX:XX', description)
    # shorten urls (keep scheme and domain)
    description = re.sub(r'(https?://[^/\s]+)/\S+', r'\1/...', description)
    # remove trailing whitespace and empty lines
    description = re.sub(r'(?:\s*\n)+', '\n', description)
    if len(description) > max_length:
        description = description[:max_length]
        last_space = description.rfind(' ')
        if last_space > 0:
            description = description[:last_space]
        description += "..."
    return description

def strip_comments_from_prompt(prompt: str|None) -> str|None:
    """Remove HTML-style comments from the prompt following markdown
    conventions.

    Multiline comments can start at the beginning of a line (up to three
    leading spaces allowed), and single-line comments can appear anywhere.
    If a ML comment ends with a line break, the line break is also removed.

    HTML-style comments cannot be nested (the nested comment's end will
    terminate the outer comment).

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
