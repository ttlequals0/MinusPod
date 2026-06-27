"""Composite the MinusPod badge onto podcast cover art.

Used when the global ``artwork_watermark_enabled`` setting is on so the served
feed's cover art is visually distinct from the original in a podcast app
(issue #420). The badge is the MinusPod waveform mark on a dark rounded chip
(with a hairline ring and soft shadow) so it stays visible on light, dark, and
busy covers alike.
"""
import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

# Repo/app root: src/artwork_watermark.py -> parents[1]. In the container that
# is /app, where the built frontend lives under static/ui.
_ROOT = Path(__file__).resolve().parents[1]

# Chip occupies this fraction of the cover's shorter side; padding is this
# fraction of the cover width, in from the bottom-right edges.
BADGE_SCALE = 0.18
BADGE_PADDING = 0.05

# Chip look. The waveform mark sits on a near-black rounded square with a
# hairline light ring; a soft drop shadow lifts it off the cover. Fractions are
# of the chip side unless noted.
CHIP_FILL = (15, 16, 22, 255)       # near-black backing
CHIP_RING = (255, 255, 255, 70)     # hairline ring for edge separation
RADIUS_FRAC = 0.26                  # corner radius
INNER_FRAC = 0.72                   # waveform size inside the chip
RING_FRAC = 0.022                   # ring width
SHADOW_MARGIN_FRAC = 0.16           # layer padding around the chip for the shadow
SHADOW_OFFSET_FRAC = 0.05           # shadow drop
SHADOW_BLUR_FRAC = 0.06             # shadow blur radius
SHADOW_ALPHA = 150


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


def _build_badge(chip_side: int, waveform: Image.Image) -> Tuple[Image.Image, int]:
    """Render the badge: a soft shadow, a near-black rounded chip with a hairline
    ring, and the waveform mark centered on it. Returns the RGBA layer and the
    margin between the layer edge and the visible chip (so the caller can keep
    the chip's inset constant even though the layer is larger for the shadow)."""
    margin = max(1, int(chip_side * SHADOW_MARGIN_FRAC))
    canvas = chip_side + margin * 2
    radius = int(chip_side * RADIUS_FRAC)
    chip_box = (margin, margin, margin + chip_side, margin + chip_side)

    shadow = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    offset = int(chip_side * SHADOW_OFFSET_FRAC)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (chip_box[0], chip_box[1] + offset, chip_box[2], chip_box[3] + offset),
        radius=radius, fill=(0, 0, 0, SHADOW_ALPHA))
    # The blurred shadow is the base layer; the chip and mark composite on top.
    layer = shadow.filter(ImageFilter.GaussianBlur(int(chip_side * SHADOW_BLUR_FRAC)))

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
