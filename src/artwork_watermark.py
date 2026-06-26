"""Composite the MinusPod badge onto podcast cover art.

Used when the global ``artwork_watermark_enabled`` setting is on so the served
feed's cover art is visually distinct from the original in a podcast app
(issue #420). The badge is the dark PWA waveform icon shipped with the frontend.
"""
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Repo/app root: src/artwork_watermark.py -> parents[1]. In the container that
# is /app, where the built frontend lives under static/ui.
_ROOT = Path(__file__).resolve().parents[1]

# Badge occupies this fraction of the cover's shorter side; padding is this
# fraction of the cover width, in from the bottom-right edges.
BADGE_SCALE = 0.18
BADGE_PADDING = 0.05


def badge_path() -> Optional[Path]:
    """First existing badge file: env override, the built static asset (runtime),
    then the frontend source (dev/tests). None if none exist."""
    for candidate in (
        os.environ.get('MINUSPOD_WATERMARK_BADGE'),
        _ROOT / 'static' / 'ui' / 'icon-512.png',
        _ROOT / 'frontend' / 'public' / 'icon-512.png',
    ):
        if candidate:
            path = Path(candidate)
            if path.is_file():
                return path
    return None


def composite_watermark(base_bytes: bytes) -> Optional[bytes]:
    """Overlay the badge on the bottom-right of the cover. Returns JPEG bytes, or
    None if the badge is unavailable or compositing fails (callers fall back to
    the unmodified cover)."""
    badge_file = badge_path()
    if not badge_file:
        logger.warning("watermark_badge_missing")
        return None
    try:
        with Image.open(BytesIO(base_bytes)) as base_img:
            base = base_img.convert('RGB')
        with Image.open(badge_file) as badge_img:
            badge = badge_img.convert('RGBA')

        w, h = base.size
        size = max(1, int(min(w, h) * BADGE_SCALE))
        badge = badge.resize((size, size), Image.LANCZOS)
        pad = int(w * BADGE_PADDING)
        # Paste using the badge's own alpha as the mask -- no RGBA round-trip on
        # the (opaque) cover, and the JPEG output needs RGB anyway.
        base.paste(badge, (max(0, w - size - pad), max(0, h - size - pad)), badge)

        out = BytesIO()
        base.save(out, format='JPEG', quality=90)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - any decode/encode failure -> fall back
        logger.warning("watermark_composite_failed err=%s", exc)
        return None
