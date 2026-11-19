"""Review and rate links appended to served descriptions of processed episodes."""
import html

from utils.episode_paths import episode_ui_url


def format_review_links(slug, episode_id) -> str:
    url = episode_ui_url(slug, episode_id)
    review = html.escape(url, quote=True)
    up = html.escape(f"{url}?rate=up", quote=True)
    down = html.escape(f"{url}?rate=down", quote=True)
    return (f'<p><a href="{review}">Review in MinusPod</a> | '
            f'<a href="{up}">Thumbs up</a> | <a href="{down}">Thumbs down</a></p>')


def append_review_links(description: str | None, slug, episode_id) -> str:
    parts = [description or '', format_review_links(slug, episode_id)]
    return '\n\n'.join(p for p in parts if p)
