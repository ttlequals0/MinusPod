"""Chapter list rendered into served descriptions (#720); never stored."""
import html
import json

from config import resolve_chapters_in_notes
from database.episodes import parse_applied_cuts
from description_chapters import has_chapter_timestamp, remap_description_timestamps


def chapter_notes_for(db, podcast, chapters: dict[str, str]) -> dict[str, str]:
    """The feed's {episode_id: chapters_json} when the list is enabled, else {}."""
    return chapters if resolve_chapters_in_notes(db, podcast) else {}


def _timestamp(seconds) -> str:
    """mm:ss, or h:mm:ss past an hour. Minutes stay zero-padded (show-notes
    convention), which is why utils.time.format_duration is not used."""
    total = int(max(0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_chapter_block(chapters_json) -> str:
    """`<p>Chapters</p><p>mm:ss Title<br>...</p>` from the stored chapters JSON
    string; empty when there are no usable chapters."""
    try:
        entries = (json.loads(chapters_json) or {}).get('chapters') or []
    except (TypeError, ValueError, AttributeError):
        return ''
    lines = []
    for ch in entries:
        if not isinstance(ch, dict) or not isinstance(ch.get('startTime'), (int, float)):
            continue
        # A topic chapter a break displaced stays in the stored list, unlisted.
        if ch.get('hidden'):
            continue
        title = html.escape(str(ch.get('title') or ''))
        lines.append(f"{_timestamp(ch['startTime'])} {title}")
    if not lines:
        return ''
    return '<p>Chapters</p><p>' + '<br>'.join(lines) + '</p>'


def append_chapters(description, chapters_json) -> str:
    # One blank line between the original description and the chapter list.
    parts = [description or '', format_chapter_block(chapters_json)]
    return '\n\n'.join(p for p in parts if p)


def compose_item_description(description, cuts_json, chapters_json, *, replacement_duration) -> str:
    """Remap description timestamps onto the post-cut timeline, then append the chapter list.

    cuts_json is the raw applied_cuts_json string, parsed only when a token could shift."""
    if cuts_json and has_chapter_timestamp(description):
        description = remap_description_timestamps(
            description, parse_applied_cuts(cuts_json), replacement_duration=replacement_duration)
    return append_chapters(description, chapters_json)
