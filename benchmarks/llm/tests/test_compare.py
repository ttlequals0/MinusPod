"""The `benchmark compare` report: one row per model present in >=2 of the
four (prompt_variant, addressing_mode) cells, paired delta/p-value between
segmentation/segment_ids and detection/timestamps.
"""
from __future__ import annotations

from benchmark import corpus
from benchmark.report import compare as compare_mod
from benchmark.storage import append_call

from tests.test_addressing_mode import CALL_TEMPLATE, SEGMENTS


def test_compare_with_no_data_at_all(tmp_path, minimal_cfg, pricing_snapshot):
    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    assert "No benchmark data yet" in out.read_text()


def test_compare_pairs_segmentation_segment_ids_against_detection_timestamps(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c2", "episode_id": ep.ep_id,
        "prompt_variant": "segmentation", "addressing_mode": "segment_ids",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })
    # Present in only one cell: must not appear in the comparison at all.
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c3", "episode_id": ep.ep_id,
        "model": "solo-model", "prompt_variant": "segmentation", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    text = out.read_text()

    assert "detection/timestamps" in text
    assert "segmentation/segment_ids" in text
    assert "solo-model" not in text

    m1_rows = [line for line in text.splitlines() if line.startswith("| `m1`")]
    assert len(m1_rows) == 1, text
    # Only one shared episode between the two paired cells -> p-value n/a,
    # but a delta is still printed.
    assert "n/a" in m1_rows[0]

    assert "## Episode sets per cell" in text
    assert ep.ep_id in text


def test_compare_model_in_only_one_cell_is_omitted(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    text = out.read_text()
    assert "`m1`" not in text


def _row_cells(text: str, model: str) -> list[str]:
    [row] = [line for line in text.splitlines() if line.startswith(f"| `{model}`")]
    return [c.strip() for c in row.strip().strip("|").split("|")]


def test_compare_cost_per_episode_is_divided_by_cell_episode_count(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    """A cell scored over 2 episodes and one scored over 1 must report the
    same dollars/episode for identical per-call token costs, not a total
    that grows with how many episodes that cell happened to cover."""
    ep1 = corpus.load_episode(write_corpus_episode(tmp_path / "corpus", ep_id="ep-1", segments=SEGMENTS))
    ep2 = corpus.load_episode(write_corpus_episode(tmp_path / "corpus", ep_id="ep-2", segments=SEGMENTS))
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "det-1", "episode_id": ep1.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "det-2", "episode_id": ep2.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "seg-1", "episode_id": ep1.ep_id,
        "prompt_variant": "segmentation", "addressing_mode": "segment_ids",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep1, ep2], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    cells = _row_cells(out.read_text(), "m1")
    detection_cost = cells[5]   # detection/timestamps cost/ep (2 episodes)
    segmentation_cost = cells[13]  # segmentation/segment_ids cost/ep (1 episode)
    assert detection_cost == segmentation_cost == "$0.0045"


def test_compare_header_notes_cost_per_episode_divergence_from_per_cell_report(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps",
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c2", "episode_id": ep.ep_id,
        "prompt_variant": "segmentation", "addressing_mode": "segment_ids",
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    text = out.read_text()
    assert "corpus-wide total" in text
    assert "not directly comparable" in text


def test_compare_header_notes_max_tokens_per_cell(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    """Segmentation ran with a larger max_tokens budget than detection; the
    comparison header must surface that split per cell, not the config's
    single value."""
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c1", "episode_id": ep.ep_id,
        "prompt_variant": "detection", "addressing_mode": "timestamps", "max_tokens": 4096,
        "parsed_ads": [{"start_time": 0.0, "end_time": 30.0}],
    })
    append_call(tmp_path, {
        **CALL_TEMPLATE, "call_id": "c2", "episode_id": ep.ep_id,
        "prompt_variant": "segmentation", "addressing_mode": "segment_ids", "max_tokens": 16384,
        "parsed_ads": [{"start": 0.0, "end": 30.0}],
    })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    text = out.read_text()
    assert "detection/timestamps 4096" in text
    assert "segmentation/segment_ids 16384" in text


def test_compare_excludes_deprecated_models(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep_dir = write_corpus_episode(tmp_path / "corpus", segments=SEGMENTS)
    ep = corpus.load_episode(ep_dir)
    for call_id, variant, mode, ads in (
        ("d1", "detection", "timestamps", [{"start_time": 0.0, "end_time": 30.0}]),
        ("d2", "segmentation", "segment_ids", [{"start": 0.0, "end": 30.0}]),
    ):
        append_call(tmp_path, {
            **CALL_TEMPLATE, "call_id": call_id, "model": "m-old", "episode_id": ep.ep_id,
            "prompt_variant": variant, "addressing_mode": mode, "parsed_ads": ads,
        })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    assert "m-old" not in out.read_text()


def test_compare_renders_numeric_delta_and_pvalue_with_two_shared_episodes(
    tmp_path, minimal_cfg, pricing_snapshot, write_corpus_episode,
):
    ep1 = corpus.load_episode(write_corpus_episode(tmp_path / "corpus", ep_id="ep-1", segments=SEGMENTS))
    ep2 = corpus.load_episode(write_corpus_episode(tmp_path / "corpus", ep_id="ep-2", segments=SEGMENTS))
    for ep, det_ads in ((ep1, [{"start_time": 0.0, "end_time": 30.0}]), (ep2, [{"start_time": 0.0, "end_time": 5.0}])):
        append_call(tmp_path, {
            **CALL_TEMPLATE, "call_id": f"det-{ep.ep_id}", "episode_id": ep.ep_id,
            "prompt_variant": "detection", "addressing_mode": "timestamps",
            "parsed_ads": det_ads,
        })
        append_call(tmp_path, {
            **CALL_TEMPLATE, "call_id": f"seg-{ep.ep_id}", "episode_id": ep.ep_id,
            "prompt_variant": "segmentation", "addressing_mode": "segment_ids",
            "parsed_ads": [{"start": 0.0, "end": 30.0}],
        })

    out = tmp_path / "comparison.md"
    compare_mod.render(
        cfg=minimal_cfg, episodes=[ep1, ep2], raw_dir=tmp_path,
        pricing_snapshot=pricing_snapshot, output_path=out,
    )
    delta_cell, p_cell = _row_cells(out.read_text(), "m1")[-2:]
    assert delta_cell != "n/a"
    assert p_cell != "n/a"
