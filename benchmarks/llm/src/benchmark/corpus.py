"""Corpus loading and window precomputation.

windows.json stores per-window boundaries and transcript_lines (the
``[start s - end s] text`` strings production builds for each segment).
The runner assembles the full LLM prompt at call time by calling MinusPod's
``format_window_prompt`` with episode metadata + these transcript_lines.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .truth_parser import Truth, parse as parse_truth, validate_cross_reference, validate_logical


@dataclass(frozen=True)
class EpisodeMetadata:
    ep_id: str
    podcast_slug: str
    podcast_name: str
    episode_id: str
    title: str
    duration: float
    segments_hash: str
    description: str = ""
    source_url: str | None = None


@dataclass(frozen=True)
class Window:
    index: int
    start: float
    end: float
    transcript_lines: list[str]


@dataclass
class Episode:
    ep_id: str
    metadata: EpisodeMetadata
    segments: list[dict]
    truth: Truth
    windows: list[Window]


class CorpusError(ValueError):
    pass


def load_episode(ep_dir: Path) -> Episode:
    if not ep_dir.is_dir():
        raise CorpusError(f"Episode directory not found: {ep_dir}")

    metadata = load_metadata(ep_dir / "metadata.toml")
    segments = load_segments(ep_dir / "segments.json", expected_hash=metadata.segments_hash)
    truth = parse_truth(ep_dir / "truth.txt")
    validate_logical(truth, episode_duration=metadata.duration)
    validate_cross_reference(truth, segments)
    windows = _load_windows(ep_dir / "windows.json")

    return Episode(ep_id=ep_dir.name, metadata=metadata, segments=segments, truth=truth, windows=windows)


def list_episodes(corpus_dir: Path) -> list[str]:
    if not corpus_dir.is_dir():
        return []
    return sorted(p.name for p in corpus_dir.iterdir() if p.is_dir() and (p / "metadata.toml").is_file())


def compute_windows(segments: list[dict]) -> list[Window]:
    from ad_detector import create_windows

    raw = create_windows(segments)
    out: list[Window] = []
    for i, w in enumerate(raw):
        lines = [
            f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text']}"
            for seg in w["segments"]
        ]
        out.append(Window(index=i, start=w["start"], end=w["end"], transcript_lines=lines))
    return out


def write_windows(ep_dir: Path, windows: list[Window]) -> None:
    payload = [
        {"index": w.index, "start": w.start, "end": w.end, "transcript_lines": w.transcript_lines}
        for w in windows
    ]
    (ep_dir / "windows.json").write_text(json.dumps(payload, indent=2))


def write_metadata(ep_dir: Path, metadata: EpisodeMetadata) -> None:
    lines = [
        f'ep_id = "{metadata.ep_id}"',
        f'podcast_slug = "{metadata.podcast_slug}"',
        f'podcast_name = "{_toml_escape(metadata.podcast_name)}"',
        f'episode_id = "{metadata.episode_id}"',
        f'title = "{_toml_escape(metadata.title)}"',
        f'duration = {metadata.duration}',
        f'segments_hash = "{metadata.segments_hash}"',
    ]
    if metadata.description:
        lines.append(f'description = "{_toml_escape(metadata.description)}"')
    if metadata.source_url:
        lines.append(f'source_url = "{metadata.source_url}"')
    (ep_dir / "metadata.toml").write_text("\n".join(lines) + "\n")


def hash_segments(segments: list[dict]) -> str:
    canonical = json.dumps(segments, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _toml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def load_metadata(path: Path) -> EpisodeMetadata:
    if not path.is_file():
        raise CorpusError(f"metadata.toml not found at {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    try:
        return EpisodeMetadata(
            ep_id=data["ep_id"],
            podcast_slug=data["podcast_slug"],
            podcast_name=data["podcast_name"],
            episode_id=data["episode_id"],
            title=data["title"],
            duration=float(data["duration"]),
            segments_hash=data["segments_hash"],
            description=data.get("description", ""),
            source_url=data.get("source_url"),
        )
    except KeyError as e:
        raise CorpusError(f"metadata.toml missing required field: {e}") from e


def load_segments(path: Path, *, expected_hash: str) -> list[dict]:
    if not path.is_file():
        raise CorpusError(f"segments.json not found at {path}")
    segments = json.loads(path.read_text())
    actual = hash_segments(segments)
    if actual != expected_hash:
        raise CorpusError(
            f"segments.json hash mismatch: file={actual}, metadata={expected_hash} -- "
            "segments file changed since capture; rerun capture or update metadata"
        )
    return segments


def _load_windows(path: Path) -> list[Window]:
    if not path.is_file():
        raise CorpusError(f"windows.json not found at {path}")
    raw = json.loads(path.read_text())
    return [
        Window(index=w["index"], start=w["start"], end=w["end"], transcript_lines=w["transcript_lines"])
        for w in raw
    ]
