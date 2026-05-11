"""Capture an episode from a MinusPod UI URL into data/candidates/."""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx

from utils.time import format_time

from .auth import Session
from .corpus import (
    CorpusError,
    EpisodeMetadata,
    compute_windows,
    hash_segments,
    load_metadata,
    load_segments,
    write_metadata,
    write_windows,
)
from .truth_parser import (
    TruthParseError,
    parse as parse_truth,
    validate_cross_reference,
    validate_logical,
)


UI_URL_RE = re.compile(r"/ui/feeds/(?P<slug>[^/]+)/episodes/(?P<episode_id>[^/?#]+)")

# Whisper occasionally emits these phrases on silent or musical regions of audio.
# Production ad-detection sometimes accepts a marker around such a region; the
# capture template auto-rejects markers whose entire transcript is one of these.
_HALLUCINATION_PHRASES: frozenset[str] = frozenset({
    "thank you for watching.",
    "thanks for watching.",
    "thank you.",
    "thanks.",
    "subtitles by the amara.org community",
})

# Strong ad signals. A marker with no signal in this set is rejected as a
# likely false positive (e.g. main-content discussion mistakenly flagged).
_AD_SIGNALS_RE = re.compile(
    r"(?:brought to you by|sponsor(?:ed|ship)?\b|"
    r"\.com\s*(?:slash|/)|"
    r"promo\s*code|use\s*code|discount\s*code|"
    r"free\s*trial|sign\s*up\s*at|get\s*started\s*at|listen\s*at|visit\s*\w)",
    re.IGNORECASE,
)


def _classify_marker(marker: dict, segments: list[dict]) -> tuple[bool, str | None]:
    """Return (accepted, rejection_reason).

    Heuristic auto-rejects markers that look like false positives so the human
    reviewer sees them in the commented "rejected" section instead of having to
    delete them from the active list.
    """
    start = float(marker.get("start", marker.get("startTime", 0)))
    end = float(marker.get("end", marker.get("endTime", 0)))
    duration = max(end - start, 0.1)
    covering = [s for s in segments if not (s["end"] <= start or s["start"] >= end)]
    text = " ".join(s.get("text", "").strip() for s in covering).strip()

    if not text:
        return False, "no transcript in range"

    norm = text.lower().rstrip(".").strip() + "."
    if norm in _HALLUCINATION_PHRASES:
        return False, "Whisper hallucination only"

    density = len(text) / duration
    if density < 3.0:
        return False, f"low text density ({density:.1f} chars/sec)"

    if not _AD_SIGNALS_RE.search(text):
        return False, "no ad signals (brought-to-you-by / .com / promo code)"

    return True, None


@dataclass(frozen=True)
class CaptureTarget:
    base_url: str
    slug: str
    episode_id: str

    @property
    def ep_id(self) -> str:
        return f"ep-{self.slug}-{self.episode_id}"


class CaptureError(RuntimeError):
    pass


def parse_episode_url(url: str) -> tuple[str, str]:
    m = UI_URL_RE.search(url)
    if not m:
        raise CaptureError(f"Could not parse slug + episode_id from URL: {url}")
    return m.group("slug"), m.group("episode_id")


def capture(
    *,
    base_url: str,
    episode_url: str,
    session: Session,
    candidates_dir: Path,
    corpus_dir: Path,
) -> Path:
    slug, episode_id = parse_episode_url(episode_url)
    target = CaptureTarget(base_url=base_url, slug=slug, episode_id=episode_id)

    candidate_dir = candidates_dir / target.ep_id
    if candidate_dir.exists():
        raise CaptureError(f"Candidate already exists: {candidate_dir}; remove it or pick a different episode")
    if (corpus_dir / target.ep_id).exists():
        raise CaptureError(f"Episode already in corpus: {corpus_dir / target.ep_id}")

    with httpx.Client(cookies=session.cookies, timeout=60) as client:
        episode_data = _get(client, f"{target.base_url.rstrip('/')}/api/v1/feeds/{slug}/episodes/{episode_id}")
        segments_data = _get(
            client,
            f"{target.base_url.rstrip('/')}/api/v1/feeds/{slug}/episodes/{episode_id}/original-segments",
        )

    segments = segments_data.get("segments")
    if not segments:
        raise CaptureError("original-segments returned empty; episode may need reprocessing on v2.0.26+")

    candidate_dir.mkdir(parents=True)
    (candidate_dir / "segments.json").write_text(json.dumps(segments, indent=2))

    seg_hash = hash_segments(segments)
    metadata = EpisodeMetadata(
        ep_id=target.ep_id,
        podcast_slug=slug,
        podcast_name=episode_data.get("podcastName") or episode_data.get("podcast_name") or slug,
        episode_id=episode_id,
        title=episode_data.get("title") or "",
        duration=float(segments[-1]["end"]),
        segments_hash=seg_hash,
        description=episode_data.get("description") or "",
        source_url=episode_data.get("originalUrl") or episode_data.get("original_url"),
    )
    write_metadata(candidate_dir, metadata)

    truth_lines = _build_truth_template(episode_data, segments)
    (candidate_dir / "truth.txt").write_text(truth_lines)

    return candidate_dir


def verify(
    ep_id: str,
    *,
    candidates_dir: Path,
    corpus_dir: Path,
) -> Path:
    candidate_dir = candidates_dir / ep_id
    if not candidate_dir.is_dir():
        raise CaptureError(f"Candidate not found: {candidate_dir}")

    target_dir = corpus_dir / ep_id
    if target_dir.exists():
        raise CaptureError(f"Already in corpus: {target_dir}")

    for required in ("metadata.toml", "segments.json", "truth.txt"):
        if not (candidate_dir / required).is_file():
            raise CaptureError(f"Missing {required} in {candidate_dir}")

    metadata = load_metadata(candidate_dir / "metadata.toml")
    segments = load_segments(candidate_dir / "segments.json", expected_hash=metadata.segments_hash)
    truth = parse_truth(candidate_dir / "truth.txt")
    validate_logical(truth, episode_duration=metadata.duration)
    validate_cross_reference(truth, segments)

    windows = compute_windows(segments)
    if not windows:
        raise CaptureError("compute_windows returned empty list")
    write_windows(candidate_dir, windows)

    corpus_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(candidate_dir), str(target_dir))
    return target_dir


def regenerate_windows(ep_id: str, *, corpus_dir: Path) -> int:
    ep_dir = corpus_dir / ep_id
    if not ep_dir.is_dir():
        raise CaptureError(f"Corpus episode not found: {ep_dir}")
    metadata = load_metadata(ep_dir / "metadata.toml")
    segments = load_segments(ep_dir / "segments.json", expected_hash=metadata.segments_hash)
    windows = compute_windows(segments)
    write_windows(ep_dir, windows)
    return len(windows)


def _get(client: httpx.Client, url: str) -> dict:
    r = client.get(url)
    if r.status_code == 404:
        raise CaptureError(f"404 from {url}; episode may need reprocess or wrong slug/id")
    if r.status_code != 200:
        raise CaptureError(f"GET {url} returned HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _build_truth_template(episode_data: dict, segments: list[dict]) -> str:
    lines: list[str] = [
        "# Pre-populated from MinusPod production ad markers.",
        "# Verify each ad: check boundaries, confirm text matches transcript.",
        "# Use the no-ads marker (uncomment) if this episode has no ads:",
        "# # Verified: no ads in this episode.",
        "",
    ]
    raw_accepted = episode_data.get("adMarkers") or episode_data.get("ad_markers") or []
    raw_rejected = episode_data.get("rejectedAdMarkers") or episode_data.get("rejected_ad_markers") or []

    if not raw_accepted and not raw_rejected:
        return "\n".join(lines + [
            "# No ad markers from production. Edit this file:",
            "# - if the episode has no ads, uncomment the marker line above",
            "# - if it does, add blocks below using the format:",
            "#   start: m:ss",
            "#   end:   m:ss",
            "#   text:  the ad text",
            "#   ---",
        ]) + "\n"

    # Build a unified, chronologically sorted list. Rejected blocks (auto or
    # production) sit in their natural time slot, commented out. When the
    # reviewer uncomments a block, validate_logical's ordering check passes
    # without further reshuffling.
    items: list[tuple[float, dict, bool, str | None, str | None]] = []
    for marker in raw_accepted:
        ok, reason = _classify_marker(marker, segments)
        source = None if ok else "auto"
        items.append((float(marker.get("start", marker.get("startTime", 0))), marker, ok, reason, source))
    for marker in raw_rejected:
        items.append((float(marker.get("start", marker.get("startTime", 0))), marker, False, None, "production"))
    items.sort(key=lambda x: x[0])

    if any(not ok for _, _, ok, _, _ in items):
        lines.append("# Lines starting with '#' are ignored. To accept a rejected block,")
        lines.append("# remove the '# ' prefix from its start/end/text lines.")
        lines.append("")

    for i, (_, marker, ok, reason, source) in enumerate(items):
        if i > 0:
            lines.append("---")
        if ok:
            lines.extend(_format_ad_block(marker, segments, commented=False))
        else:
            if source == "auto":
                lines.append(f"# auto-rejected: {reason}")
            else:
                lines.append("# rejected by production")
            lines.extend(_format_ad_block(marker, segments, commented=True))

    return "\n".join(lines) + "\n"


def _format_ad_block(marker: dict, segments: list[dict], *, commented: bool) -> list[str]:
    start = float(marker.get("start", marker.get("startTime", 0)))
    end = float(marker.get("end", marker.get("endTime", 0)))
    covering = [s for s in segments if not (s["end"] <= start or s["start"] >= end)]
    text = " ".join(s.get("text", "").strip() for s in covering).strip() or "(transcript unavailable for this range)"
    prefix = "# " if commented else ""
    return [
        f"{prefix}start: {format_time(start)}",
        f"{prefix}end:   {format_time(end)}",
        f"{prefix}text:  {text}",
    ]


