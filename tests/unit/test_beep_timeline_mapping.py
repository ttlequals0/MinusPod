"""Per-span replacement duration in timeline mapping (Task 5b).

A 'remove' cut span is replaced by the fixed beep clip (today's behavior).
A 'beep' cut span is instead padded to its own length, so it costs no
timeline shift -- unless the span is shorter than the clip itself, in which
case the clip (not the shorter span) is what plays.
audio_processor.compute_applied_cuts stamps this per span as
'replacement_duration'; every original<->processed mapper (utils.time.
adjust_timestamp, embedded_chapters.remap_chapters, verification_pass'
timestamp map, processing._unadjust_timestamp) reads it per span instead of
assuming one constant for every cut. A span with no such key (legacy
persisted applied_cuts_json, or a hand-built cut dict) falls back to the
mapper's scalar `replacement_duration` argument -- today's behavior.
"""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('beep_timeline_mapping_test_')

from audio_processor import AudioProcessor
from embedded_chapters import remap_chapters
from main_app import processing
from utils.time import adjust_timestamp, merge_cut_spans
from verification_pass import _build_timestamp_map, _map_correction_to_processed, _map_to_original


BEEP = 2.0

# One remove span (30s cut, 2s clip -> shifts later content by 28s) and one
# beep span (10s cut padded to its own 10s length -> shifts nothing), used
# consistently across the mapper tests below so their numbers cross-check.
MIXED_CUTS = [
    {'start': 100.0, 'end': 130.0, 'replacement_duration': 2.0},   # remove
    {'start': 200.0, 'end': 210.0, 'replacement_duration': 10.0},  # beep
]


class TestComputeAppliedCutsStampsReplacementDuration:
    """audio_processor.compute_applied_cuts is the source of truth: it
    stamps each applied span with the replacement length remove_ads will
    actually render it with."""

    def test_remove_span_gets_fixed_clip_length(self):
        processor = AudioProcessor()
        beep = processor.get_beep_duration()
        cuts = processor.compute_applied_cuts(
            [{'start': 200.0, 'end': 230.0}], 600.0)
        assert cuts[0]['replacement_duration'] == pytest.approx(beep)

    def test_beep_span_longer_than_clip_gets_own_length(self):
        processor = AudioProcessor()
        cuts = processor.compute_applied_cuts(
            [{'start': 100.0, 'end': 130.0, 'beep': True}], 600.0)
        assert cuts[0]['replacement_duration'] == pytest.approx(30.0)

    def test_beep_span_shorter_than_clip_gets_clip_length(self):
        # A trusted (fingerprint) span bypasses the 10s short-cut floor, so
        # this exercises a beep span shorter than the clip itself: the clip
        # still plays in full, so the filler is the clip length, not the
        # (shorter) span length.
        processor = AudioProcessor()
        beep = processor.get_beep_duration()
        short_span = beep / 2
        cuts = processor.compute_applied_cuts(
            [{'start': 50.0, 'end': 50.0 + short_span, 'beep': True,
              'confidence': 0.99, 'detection_stage': 'fingerprint'}],
            600.0,
        )
        assert len(cuts) == 1
        assert cuts[0]['replacement_duration'] == pytest.approx(beep)


class TestMergeCutSpansTotalReplacement:
    def test_total_replacement_sums_per_span_values(self):
        merged = merge_cut_spans(MIXED_CUTS)
        # Non-touching spans stay separate groups; each carries its own
        # span's replacement_duration as its group total.
        assert merged == [[100.0, 130.0, 1, 2.0], [200.0, 210.0, 1, 10.0]]

    def test_missing_key_falls_back_to_default_replacement(self):
        legacy = [{'start': 100.0, 'end': 130.0}]
        merged = merge_cut_spans(legacy, default_replacement=2.0)
        assert merged == [[100.0, 130.0, 1, 2.0]]


class TestAdjustTimestampMixedRemoveBeep:
    """The HARD invariant target: a beeped span shifts nothing, a removed
    span shifts by (span length - clip length)."""

    def test_beeped_span_contributes_zero_shift(self):
        # Bracket only the beep span [200, 210): no shift across it.
        before = adjust_timestamp(195.0, MIXED_CUTS)
        after = adjust_timestamp(215.0, MIXED_CUTS)
        assert after - before == pytest.approx(20.0)  # unchanged from original

    def test_removed_span_shifts_by_span_minus_clip(self):
        # Bracket only the remove span [100, 130): shifts by 30 - 2 = 28.
        before = adjust_timestamp(95.0, MIXED_CUTS)
        after = adjust_timestamp(135.0, MIXED_CUTS)
        assert after - before == pytest.approx(12.0)  # 40 - 28

    def test_content_after_both_spans(self):
        assert adjust_timestamp(250.0, MIXED_CUTS) == pytest.approx(222.0)

    def test_per_span_value_overrides_scalar_default(self):
        # A non-zero scalar default must not leak into spans that specify
        # their own replacement_duration.
        assert adjust_timestamp(250.0, MIXED_CUTS, replacement_duration=999.0) \
            == pytest.approx(222.0)


class TestEmbeddedChaptersRemapMixed:
    def test_chapter_remap_correct_for_mixed_cut_list(self):
        chapters = [
            {'start': 0.0, 'end': 100.0, 'title': 'Ch1'},
            {'start': 130.0, 'end': 200.0, 'title': 'Ch2'},
            {'start': 210.0, 'end': 400.0, 'title': 'Ch3'},
        ]
        # total 400s, cuts remove 30+10=40s, replacements add back 2+10=12s.
        new_duration = 400.0 - 40.0 + 12.0
        out = remap_chapters(
            chapters, MIXED_CUTS, replacement_duration=BEEP,
            new_duration=new_duration,
        )
        assert out == [
            {'start': 0.0, 'title': 'Ch1', 'end': 102.0},
            {'start': 102.0, 'title': 'Ch2', 'end': 182.0},
            {'start': 182.0, 'title': 'Ch3', 'end': 372.0},
        ]
        # Ch2 and Ch3 shift by the same 28s (only the remove span precedes
        # both); the beep span between them contributes nothing extra.
        assert 130.0 - out[1]['start'] == pytest.approx(28.0)
        assert 210.0 - out[2]['start'] == pytest.approx(28.0)


class TestVerificationPassMappingMixedCutList:
    """Correction mapping roundtrip across a beeped span, and the pass-2
    original<->processed roundtrip _build_timestamp_map feeds."""

    def test_correction_roundtrip_across_beeped_span(self):
        ts_map = _build_timestamp_map(MIXED_CUTS)
        proc = _map_correction_to_processed(195.0, 215.0, ts_map)
        assert proc == pytest.approx((167.0, 187.0))
        # Round-trips back through _map_to_original.
        assert _map_to_original(proc[0], ts_map) == pytest.approx(195.0)
        assert _map_to_original(proc[1], ts_map) == pytest.approx(215.0)

    def test_pass2_cuts_in_original_maps_through_mixed_pass1_cuts(self):
        # Pass-2 found a rendered-audio cut at [150, 160) on the pass-1
        # output; that region sits between the beep-shifted remove span and
        # the zero-shift beep span, so it maps back with the remove span's
        # 28s shift only.
        recut = [{'start': 150.0, 'end': 160.0}]
        out = processing._pass2_cuts_in_original(recut, MIXED_CUTS)
        assert out == [{'start': 178.0, 'end': 188.0,
                        'detection_stage': 'verification',
                        'replacement_duration': None}]


class TestUnadjustTimestampMixedRecut:
    """processing._unadjust_timestamp (recut chapter remap's inverse) must
    also honor per-span replacement duration, not one constant for every
    cut in the list."""

    def test_roundtrip_outside_cuts(self):
        for t in (50.0, 167.0, 187.0, 300.0):
            p = adjust_timestamp(t, MIXED_CUTS)
            assert processing._unadjust_timestamp(p, MIXED_CUTS) == pytest.approx(t)

    def test_inside_beeped_span_maps_to_its_start(self):
        # 1s into the second (beep) span's replacement audio.
        p = adjust_timestamp(200.0, MIXED_CUTS) + 1.0
        assert processing._unadjust_timestamp(p, MIXED_CUTS) == 200.0


class TestLegacyMetadataDefaultsToAllRemove:
    """Applied cuts persisted before this feature (no 'replacement_duration'
    key) must map exactly as today: every span the fixed beep clip length."""

    LEGACY_CUTS = [{'start': 100.0, 'end': 130.0}, {'start': 200.0, 'end': 230.0}]

    def test_adjust_timestamp_uses_scalar_for_every_span(self):
        # Both spans fall back to the scalar default -- the pre-5b model.
        assert adjust_timestamp(250.0, self.LEGACY_CUTS, replacement_duration=BEEP) \
            == pytest.approx(250.0 - (30.0 - BEEP) - (30.0 - BEEP))

    def test_unadjust_timestamp_uses_scalar_for_every_span(self):
        p = adjust_timestamp(250.0, self.LEGACY_CUTS, replacement_duration=BEEP)
        assert processing._unadjust_timestamp(p, self.LEGACY_CUTS, BEEP) \
            == pytest.approx(250.0)

    def test_persisted_legacy_cuts_round_trip_without_the_key(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']
        temp_db.save_applied_cuts(slug, ep_id, self.LEGACY_CUTS)
        reloaded = temp_db.get_applied_cuts(slug, ep_id)
        assert reloaded == self.LEGACY_CUTS
        assert all('replacement_duration' not in c for c in reloaded)
        # And the reloaded (legacy-shaped) list still maps as all-remove.
        assert adjust_timestamp(250.0, reloaded, replacement_duration=BEEP) \
            == pytest.approx(250.0 - (30.0 - BEEP) - (30.0 - BEEP))


class TestPersistedReplacementDurationRoundTrips:
    """New-format applied cuts carry replacement_duration additively; a
    recut's timeline mapping reads it back after a save/load cycle."""

    def test_replacement_duration_persists_and_is_honored_downstream(
            self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']
        temp_db.save_applied_cuts(slug, ep_id, MIXED_CUTS)
        reloaded = temp_db.get_applied_cuts(slug, ep_id)
        assert reloaded == MIXED_CUTS
        # Same mapping as the in-memory MIXED_CUTS case above: the beep span
        # contributes zero shift even after a DB round trip.
        before = adjust_timestamp(195.0, reloaded)
        after = adjust_timestamp(215.0, reloaded)
        assert after - before == pytest.approx(20.0)


class TestAllRemoveRegression:
    """HARD invariant: with no beep-action spans, mapper output is
    bit-identical to a hand-built legacy (no replacement_duration) cut
    list -- compute_applied_cuts stamping the fixed clip length on every
    remove span must not change any mapper's output."""

    def test_compute_applied_cuts_output_maps_identically_to_legacy_shape(self):
        processor = AudioProcessor()
        beep = processor.get_beep_duration()
        requested = [{'start': 100.0, 'end': 130.0},
                     {'start': 300.0, 'end': 340.0}]
        applied = processor.compute_applied_cuts(requested, 600.0)
        legacy_shape = [{'start': c['start'], 'end': c['end']} for c in applied]

        for t in (50.0, 150.0, 320.0, 400.0):
            assert adjust_timestamp(t, applied) == pytest.approx(
                adjust_timestamp(t, legacy_shape, replacement_duration=beep))

    def test_remap_chapters_identical_all_remove(self):
        processor = AudioProcessor()
        beep = processor.get_beep_duration()
        applied = processor.compute_applied_cuts(
            [{'start': 100.0, 'end': 130.0}, {'start': 300.0, 'end': 340.0}],
            600.0,
        )
        legacy_shape = [{'start': c['start'], 'end': c['end']} for c in applied]
        chapters = [{'start': 0.0, 'end': 200.0, 'title': 'A'},
                    {'start': 250.0, 'end': 600.0, 'title': 'B'}]
        new_duration = 600.0 - 70.0 + 2 * beep

        with_stamp = remap_chapters(chapters, applied,
                                    replacement_duration=beep,
                                    new_duration=new_duration)
        without_stamp = remap_chapters(chapters, legacy_shape,
                                       replacement_duration=beep,
                                       new_duration=new_duration)
        assert with_stamp == without_stamp
