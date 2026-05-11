import json
from pathlib import Path

import pytest

from benchmark import corpus
from benchmark.corpus import (
    CorpusError,
    EpisodeMetadata,
    Window,
    compute_windows,
    hash_segments,
    list_episodes,
    load_episode,
    write_metadata,
    write_windows,
)


SEGMENTS = [
    {"start": 0.0, "end": 5.0, "text": "This episode is brought to you by BetterHelp"},
    {"start": 5.0, "end": 30.0, "text": "BetterHelp is online therapy that fits"},
    {"start": 30.0, "end": 60.0, "text": "Now back to the show"},
    {"start": 60.0, "end": 100.0, "text": "Welcome back everyone"},
]


def write_corpus_episode(tmp_path, ep_id="ep-test-001", segments=None, truth_text=None):
    segments = segments or SEGMENTS
    ep_dir = tmp_path / ep_id
    ep_dir.mkdir(parents=True)
    seg_hash = hash_segments(segments)

    metadata = EpisodeMetadata(
        ep_id=ep_id,
        podcast_slug="test-show",
        podcast_name="Test Show",
        episode_id="abc123",
        title="Test Episode",
        duration=segments[-1]["end"],
        segments_hash=seg_hash,
        description="A test episode.",
    )
    write_metadata(ep_dir, metadata)
    (ep_dir / "segments.json").write_text(json.dumps(segments))
    (ep_dir / "truth.txt").write_text(truth_text or """
start: 0
end: 30
text: This episode is brought to you by BetterHelp BetterHelp is online therapy
""")
    write_windows(ep_dir, [Window(index=0, start=0.0, end=100.0, transcript_lines=["[0.0s - 5.0s] x"])])
    return ep_dir


def test_hash_is_stable_and_unique():
    h1 = hash_segments(SEGMENTS)
    h2 = hash_segments(SEGMENTS)
    h3 = hash_segments(SEGMENTS + [{"start": 100.0, "end": 110.0, "text": "extra"}])
    assert h1 == h2
    assert h1 != h3
    assert h1.startswith("sha256:")


def test_load_episode_round_trip(tmp_path):
    ep_dir = write_corpus_episode(tmp_path)
    ep = load_episode(ep_dir)
    assert ep.ep_id == "ep-test-001"
    assert ep.metadata.podcast_slug == "test-show"
    assert len(ep.segments) == 4
    assert len(ep.truth.ads) == 1
    assert len(ep.windows) == 1


def test_load_episode_missing_directory(tmp_path):
    with pytest.raises(CorpusError, match="not found"):
        load_episode(tmp_path / "missing")


def test_load_episode_segments_hash_mismatch(tmp_path):
    ep_dir = write_corpus_episode(tmp_path)
    (ep_dir / "segments.json").write_text(json.dumps(SEGMENTS + [{"start": 999, "end": 1000, "text": "z"}]))
    with pytest.raises(CorpusError, match="hash mismatch"):
        load_episode(ep_dir)


def test_load_episode_metadata_missing_field(tmp_path):
    ep_dir = write_corpus_episode(tmp_path)
    (ep_dir / "metadata.toml").write_text('ep_id = "x"\n')
    with pytest.raises(CorpusError, match="required field"):
        load_episode(ep_dir)


def test_list_episodes_filters_to_dirs_with_metadata(tmp_path):
    (tmp_path / "ep-1").mkdir()
    (tmp_path / "ep-1" / "metadata.toml").write_text("")
    (tmp_path / "ep-2").mkdir()
    (tmp_path / "loose-file.txt").write_text("")
    assert list_episodes(tmp_path) == ["ep-1"]


def test_list_episodes_missing_dir():
    assert list_episodes(Path("/nonexistent/path/x")) == []


def test_compute_windows_uses_minuspod_logic():
    big = [{"start": float(i), "end": float(i + 1), "text": f"seg{i}"} for i in range(0, 1500)]
    windows = compute_windows(big)
    assert len(windows) >= 2
    assert windows[0].start == 0.0
    assert windows[0].transcript_lines[0].startswith("[0.0s - 1.0s] seg0")
    assert all(w.end > w.start for w in windows)


def test_compute_windows_empty_segments():
    assert compute_windows([]) == []
