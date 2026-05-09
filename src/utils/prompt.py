"""Prompt placeholder substitution helpers.

Detection, verification, and reviewer prompts all use ``{placeholder}``
substitution rather than appending content unconditionally. ``str.replace``
(not ``str.format``) is intentional so literal ``{{...}}`` JSON examples in
prompt bodies stay intact.
"""

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


def format_sponsor_block(sponsor_list: str) -> str:
    """Wrap a non-empty sponsor list with the standard header.

    Empty list returns empty string so substitution does not produce a
    dangling header on prompts whose ``{sponsor_database}`` placeholder is
    left in place.
    """
    if not sponsor_list:
        return ""
    return SPONSOR_DATABASE_HEADER + sponsor_list
