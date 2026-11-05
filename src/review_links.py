"""Review and rate links appended to served descriptions of processed episodes."""
import html

from utils.episode_paths import episode_ui_url


def format_review_links(slug, episode_id) -> str:
    url = episode_ui_url(slug, episode_id)
    review = html.escape(url, quote=True)
    rate = html.escape(f"{url}?rate=5", quote=True)
    return (f'<p><a href="{review}">Review in MinusPod</a> | '
            f'<a href="{rate}">Rate 5 stars</a></p>')


def append_review_links(description, slug, episode_id, enabled: bool) -> str:
    if not enabled:
        return description
    parts = [description or '', format_review_links(slug, episode_id)]
    return '\n\n'.join(p for p in parts if p)
