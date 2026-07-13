"""Composite the MinusPod badge onto podcast cover art.

Used when the global ``artwork_watermark_enabled`` setting is on so the served
feed's cover art is visually distinct from the original in a podcast app
(issue #420). The badge is the MinusPod waveform mark on a dark rounded chip
with a hulu-green ring and soft green halo, so it stays visible on light,
dark, and busy covers alike (the halo replaced a black drop shadow that
vanished on black art, issue #514).
"""
import hashlib
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

# Bump only for a code-only rendering change to the badge (chip color, layout)
# that does not swap the asset file -- an asset swap is picked up automatically
# by badge_fingerprint(). Both feed into cover_badge_salt(), which storage folds
# into the cover-art URL cache-bust token so downstream apps re-fetch a changed
# badge instead of serving the stale cache.
BADGE_REVISION = 2

# Repo/app root: src/artwork_watermark.py -> parents[1]. In the container that
# is /app, where the built frontend lives under static/ui.
_ROOT = Path(__file__).resolve().parents[1]

# Chip occupies this fraction of the cover's shorter side; padding is this
# fraction of the cover width, in from the bottom-right edges.
BADGE_SCALE = 0.18
BADGE_PADDING = 0.05

# Chip look. The waveform mark sits on a near-black rounded square with a
# hairline hulu-green ring; a soft green halo lifts it off the cover on both
# light and black art. Fractions are of the chip side unless noted.
HULU_GREEN = (28, 231, 131)         # #1CE783
CHIP_FILL = (15, 16, 22, 255)       # near-black backing
CHIP_RING = (*HULU_GREEN, 200)      # hairline ring for edge separation
RADIUS_FRAC = 0.26                  # corner radius
INNER_FRAC = 0.72                   # waveform size inside the chip
RING_FRAC = 0.022                   # ring width
HALO_MARGIN_FRAC = 0.16             # layer padding around the chip for the halo
HALO_EXPAND_FRAC = 0.08             # halo rect extends past the chip edge
HALO_BLUR_FRAC = 0.09               # halo blur radius
HALO_ALPHA = 220


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


_BADGE_FINGERPRINT: Optional[str] = None


def badge_fingerprint() -> str:
    """Short content hash of the active badge asset, memoized for the process.

    Folded into cover_badge_salt() so swapping the badge image (a new build or a
    MINUSPOD_WATERMARK_BADGE override) shifts every feed's artwork URL with no
    manual BADGE_REVISION bump. Empty string when no badge asset is available.
    """
    global _BADGE_FINGERPRINT
    if _BADGE_FINGERPRINT is None:
        path = badge_path()
        try:
            _BADGE_FINGERPRINT = hashlib.md5(
                path.read_bytes(), usedforsecurity=False).hexdigest()[:8] if path else ''
        except OSError:
            _BADGE_FINGERPRINT = ''
    return _BADGE_FINGERPRINT


def cover_badge_salt() -> str:
    """Badge-identity salt folded into the cover-art cache-bust token. Changes
    when the badge asset (badge_fingerprint) or the rendering revision changes."""
    return f"{BADGE_REVISION}:{badge_fingerprint()}"


def _build_badge(chip_side: int, waveform: Image.Image) -> Tuple[Image.Image, int]:
    """Render the badge: a soft green halo, a near-black rounded chip with a
    hairline green ring, and the waveform mark centered on it. Returns the RGBA
    layer and the margin between the layer edge and the visible chip (so the
    caller can keep the chip's inset constant even though the layer is larger
    for the halo)."""
    margin = max(1, int(chip_side * HALO_MARGIN_FRAC))
    canvas = chip_side + margin * 2
    radius = int(chip_side * RADIUS_FRAC)
    chip_box = (margin, margin, margin + chip_side, margin + chip_side)

    halo = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    expand = max(1, int(chip_side * HALO_EXPAND_FRAC))
    ImageDraw.Draw(halo).rounded_rectangle(
        (chip_box[0] - expand, chip_box[1] - expand,
         chip_box[2] + expand, chip_box[3] + expand),
        radius=radius + expand, fill=(*HULU_GREEN, HALO_ALPHA))
    # The blurred halo is the base layer; the chip and mark composite on top.
    layer = halo.filter(ImageFilter.GaussianBlur(int(chip_side * HALO_BLUR_FRAC)))

    chip = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle(
        chip_box, radius=radius, fill=CHIP_FILL,
        outline=CHIP_RING, width=max(1, int(chip_side * RING_FRAC)))
    layer.alpha_composite(chip)

    inner = max(1, int(chip_side * INNER_FRAC))
    mark = waveform.resize((inner, inner), Image.LANCZOS)
    pos = margin + (chip_side - inner) // 2
    layer.alpha_composite(mark, (pos, pos))
    return layer, margin


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
            waveform = badge_img.convert('RGBA')

        w, h = base.size
        chip_side = max(1, int(min(w, h) * BADGE_SCALE))
        badge, margin = _build_badge(chip_side, waveform)
        pad = int(w * BADGE_PADDING)
        # Inset the visible chip by `pad`; the layer is larger by `margin` on
        # every side (shadow room), so shift the paste out by that margin. Paste
        # using the badge's own alpha as the mask -- no RGBA round-trip on the
        # (opaque) cover, and the JPEG output needs RGB anyway.
        x = max(0, w - chip_side - pad - margin)
        y = max(0, h - chip_side - pad - margin)
        base.paste(badge, (x, y), badge)

        out = BytesIO()
        base.save(out, format='JPEG', quality=90)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - any decode/encode failure -> fall back
        logger.warning("watermark_composite_failed err=%s", exc)
        return None
