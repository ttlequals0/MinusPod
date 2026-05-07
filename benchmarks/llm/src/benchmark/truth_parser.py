"""Parse and validate truth.txt ground-truth files.

Format:
    # comment lines start with '#'
    start: 0:45
    end: 1:52
    text: ad text continues
    until next start: or ---
    ---
    start: 20:40
    end: 21:45
    text: ...

A no-ad episode uses a marker:
    # Verified: no ads in this episode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

NO_ADS_MARKER_RE = re.compile(r"^#\s*Verified:\s*no ads", re.IGNORECASE)
LABEL_RE = re.compile(r"^(start|end|text)\s*:\s*(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Ad:
    start: float
    end: float
    text: str


@dataclass
class Truth:
    ads: list[Ad]
    is_no_ad_episode: bool

    @property
    def empty(self) -> bool:
        return not self.ads and not self.is_no_ad_episode


class TruthParseError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        self.line = line
        super().__init__(f"line {line}: {message}" if line else message)


def parse(path: str | Path) -> Truth:
    p = Path(path)
    if not p.is_file():
        raise TruthParseError(f"truth.txt not found at {p}")
    return parse_text(p.read_text())


def parse_text(text: str) -> Truth:
    raw_lines = text.splitlines()
    has_no_ad_marker = any(NO_ADS_MARKER_RE.match(ln) for ln in raw_lines)

    blocks = _split_blocks(raw_lines)
    if not blocks:
        if has_no_ad_marker:
            return Truth(ads=[], is_no_ad_episode=True)
        raise TruthParseError(
            "truth.txt is empty -- either verify the episode and add the no-ads marker "
            "(# Verified: no ads in this episode.), or add ad blocks."
        )

    if has_no_ad_marker and blocks:
        raise TruthParseError(
            "no-ads marker present but ad blocks also defined; remove one"
        )

    ads = [_parse_block(b) for b in blocks]
    return Truth(ads=ads, is_no_ad_episode=False)


def validate_logical(truth: Truth, *, episode_duration: float | None = None) -> None:
    if truth.is_no_ad_episode:
        return
    prev_end = -1.0
    for i, ad in enumerate(truth.ads):
        if ad.start >= ad.end:
            raise TruthParseError(f"ad #{i + 1}: start ({ad.start}) >= end ({ad.end})")
        if ad.start < prev_end:
            raise TruthParseError(
                f"ad #{i + 1}: starts at {ad.start} before previous ad ends at {prev_end} "
                f"(ads must be ordered by start, no overlaps)"
            )
        if episode_duration is not None and ad.end > episode_duration:
            raise TruthParseError(
                f"ad #{i + 1}: end ({ad.end}) exceeds episode duration ({episode_duration})"
            )
        prev_end = ad.end


def validate_cross_reference(
    truth: Truth,
    segments: list[dict],
    *,
    similarity_threshold: float = 85.0,
) -> None:
    if truth.is_no_ad_episode:
        return
    for i, ad in enumerate(truth.ads):
        covering = [s for s in segments if not (s["end"] <= ad.start or s["start"] >= ad.end)]
        if not covering:
            raise TruthParseError(
                f"ad #{i + 1}: no segments cover [{ad.start}, {ad.end}]"
            )
        actual_text = " ".join(s.get("text", "").strip() for s in covering)
        score = fuzz.partial_ratio(ad.text.strip(), actual_text)
        if score < similarity_threshold:
            raise TruthParseError(
                f"ad #{i + 1}: text fuzzy-match {score:.1f}% < {similarity_threshold}% threshold "
                f"against segments [{ad.start}, {ad.end}] -- check boundaries or text"
            )


def _split_blocks(lines: list[str]) -> list[list[tuple[int, str]]]:
    blocks: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    for lineno, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append((lineno, raw))
    if current:
        blocks.append(current)
    return blocks


def _parse_block(block: list[tuple[int, str]]) -> Ad:
    fields: dict[str, tuple[int, str]] = {}
    current_field: str | None = None
    text_lines: list[str] = []
    text_first_line: int | None = None

    for lineno, line in block:
        match = LABEL_RE.match(line.strip())
        if match:
            label = match.group(1).lower()
            value = match.group(2).strip()
            if current_field == "text":
                fields["text"] = (text_first_line or lineno, "\n".join(text_lines).strip())
                text_lines = []
                text_first_line = None
            if label == "text":
                current_field = "text"
                text_first_line = lineno
                if value:
                    text_lines.append(value)
            else:
                fields[label] = (lineno, value)
                current_field = None
        else:
            if current_field == "text":
                text_lines.append(line.strip())
            else:
                raise TruthParseError(f"unexpected line outside any field: {line!r}", line=lineno)

    if current_field == "text":
        fields["text"] = (text_first_line or block[0][0], "\n".join(text_lines).strip())

    for required in ("start", "end", "text"):
        if required not in fields:
            raise TruthParseError(f"block missing required field {required!r}", line=block[0][0])

    start_line, start_str = fields["start"]
    end_line, end_str = fields["end"]
    text_line, text_value = fields["text"]
    if not text_value:
        raise TruthParseError("text field is empty", line=text_line)

    return Ad(
        start=parse_timestamp(start_str, line=start_line),
        end=parse_timestamp(end_str, line=end_line),
        text=text_value,
    )


def parse_timestamp(value: str, *, line: int | None = None) -> float:
    parts = value.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise TruthParseError(f"invalid timestamp {value!r}", line=line)
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    raise TruthParseError(f"invalid timestamp {value!r}", line=line)
