"""Unit tests for the learned positional prior (issue #360)."""
import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from positional_prior import (
    LearnedZone, PositionalPrior, AdDistribution, build_prior,
    build_distribution, compute_positional_prior, compute_ad_distribution,
    format_prior_hint
)
from config import POSITIONAL_PRIOR_HISTOGRAM_BUCKETS

DURATION = 1000.0


def _ep(eid, markers, duration=DURATION):
    return {'episode_id': eid, 'original_duration': duration, 'ad_markers': markers}


def _m(start, end=None, conf=0.95, stage='claude', was_cut=True):
    return {
        'start': start,
        'end': end if end is not None else start + 60.0,
        'confidence': conf,
        'detection_stage': stage,
        'was_cut': was_cut,
    }


class TestBuildDistribution:

    def test_buckets_sum_to_total_events(self):
        episodes = [_ep(f'e{i}', [_m(300.0), _m(750.0)]) for i in range(6)]
        dist = build_distribution('test', episodes)

        assert dist.bucket_count == POSITIONAL_PRIOR_HISTOGRAM_BUCKETS
        assert len(dist.buckets) == POSITIONAL_PRIOR_HISTOGRAM_BUCKETS
        assert sum(dist.buckets) == dist.total_events == 12

    def test_position_lands_in_expected_bucket(self):
        # 20 buckets of 5%: 0.30 -> bucket 6, 0.0 -> 0, end -> last
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        dist = build_distribution('test', episodes)

        assert dist.buckets[6] == 5
        assert sum(dist.buckets) == 5

    def test_position_at_end_clamps_to_last_bucket(self):
        episodes = [_ep(f'e{i}', [_m(1000.0)]) for i in range(5)]  # pos 1.0
        dist = build_distribution('test', episodes)

        assert dist.buckets[-1] == 5

    def test_zones_present_when_gate_met(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(6)]
        dist = build_distribution('test', episodes)

        assert len(dist.zones) == 1
        assert dist.zones[0].center == pytest.approx(0.30)
        assert dist.episodes_considered == 6
        assert dist.median_duration == pytest.approx(DURATION)

    def test_histogram_without_zones_below_episode_gate(self):
        # 4 episodes: under the 5-episode zone gate, but the histogram still fills
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(4)]
        dist = build_distribution('test', episodes)

        assert dist.zones == []
        assert dist.buckets[6] == 4
        assert dist.total_events == 4
        assert dist.episodes_considered == 4

    def test_empty_history_is_all_zero(self):
        dist = build_distribution('test', [])

        assert dist.episodes_considered == 0
        assert dist.total_events == 0
        assert dist.median_duration == 0
        assert sum(dist.buckets) == 0
        assert dist.zones == []


class TestComputeAdDistribution:

    SLUG = 'dist-pod'

    def _seed(self, db, count=6):
        db.create_podcast(self.SLUG, 'https://example.com/feed.xml', 'Dist Pod')
        for i in range(count):
            eid = f'ep-{i:03d}'
            db.upsert_episode(
                self.SLUG, eid, original_url=f'https://example.com/{eid}.mp3',
                title=f'Episode {i}', status='processed',
                original_duration=DURATION,
                published_at=f'2026-06-{i + 1:02d}T00:00:00+00:00')
            db.save_episode_details(self.SLUG, eid, ad_markers=[_m(300.0)])

    def test_setting_independent_returns_distribution(self, temp_db):
        self._seed(temp_db)
        # Toggle stays off; distribution must still compute.
        dist = compute_ad_distribution(temp_db, self.SLUG)

        assert isinstance(dist, AdDistribution)
        assert dist.episodes_considered == 6
        assert dist.buckets[6] == 6
        assert len(dist.zones) == 1

    def test_empty_feed_returns_zeroed_distribution(self, temp_db):
        temp_db.create_podcast(self.SLUG, 'https://example.com/feed.xml', 'Dist Pod')
        dist = compute_ad_distribution(temp_db, self.SLUG)

        assert dist.episodes_considered == 0
        assert sum(dist.buckets) == 0


class TestClustering:

    def test_positions_within_gap_merge_into_one_zone(self):
        episodes = [_ep(f'e{i}', [_m(280.0 + i * 10)]) for i in range(6)]
        prior = build_prior('test', episodes)

        assert prior is not None
        assert len(prior.zones) == 1
        assert prior.zones[0].support == 6

    def test_support_counts_distinct_episodes(self):
        # e0 contributes three events in one cluster; support is per-episode
        episodes = [_ep('e0', [_m(280.0), _m(300.0), _m(320.0)])]
        episodes += [_ep(f'e{i}', [_m(300.0)]) for i in range(1, 5)]
        prior = build_prior('test', episodes)

        assert len(prior.zones) == 1
        assert prior.zones[0].support == 5

    def test_center_is_median_and_bounds_are_padded(self):
        # positions 0.26, 0.28, 0.30, 0.32, 0.34 -> one cluster
        episodes = [_ep(f'e{i}', [_m(260.0 + i * 20)]) for i in range(5)]
        prior = build_prior('test', episodes)

        zone = prior.zones[0]
        assert zone.center == pytest.approx(0.30)
        assert zone.low == pytest.approx(0.23)   # 0.26 - 0.03 margin
        assert zone.high == pytest.approx(0.37)  # 0.34 + 0.03 margin

    def test_bounds_clamped_to_unit_range(self):
        episodes = [_ep(f'e{i}', [_m(5.0)]) for i in range(5)]
        prior = build_prior('test', episodes)

        assert prior.zones[0].low == 0.0

    def test_distant_positions_form_separate_zones(self):
        episodes = [_ep(f'e{i}', [_m(50.0), _m(500.0)]) for i in range(5)]
        prior = build_prior('test', episodes)

        assert len(prior.zones) == 2

    def test_drifting_positions_do_not_chain_into_one_zone(self):
        # One break drifting 0.20 -> 0.56 in 0.04 steps: single-linkage would
        # chain all ten into one full-support mega-zone. The span cap breaks
        # the chain; no fragment reaches 60% support, so no prior.
        episodes = [_ep(f'e{i}', [_m(200.0 + i * 40.0)]) for i in range(10)]
        assert build_prior('test', episodes) is None

    def test_drift_within_span_cap_forms_single_zone(self):
        # Drift within the 0.10 span cap stays one zone with full support.
        episodes = [_ep(f'e{i}', [_m(280.0 + i * 16.0)]) for i in range(6)]
        prior = build_prior('test', episodes)

        assert len(prior.zones) == 1
        assert prior.zones[0].support == 6


class TestGating:

    def test_insufficient_episodes_returns_none(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(4)]
        assert build_prior('test', episodes) is None

    def test_low_support_zone_dropped(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(10)]
        for i in range(3):
            episodes[i]['ad_markers'].append(_m(700.0))
        prior = build_prior('test', episodes)

        assert len(prior.zones) == 1
        assert prior.zones[0].center == pytest.approx(0.30)

    def test_zero_ad_episodes_count_in_denominator(self):
        # 5 of 10 episodes have the ad -> 50% support -> below 60% gate
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        episodes += [_ep(f'e{i}', []) for i in range(5, 10)]
        assert build_prior('test', episodes) is None

    def test_max_zones_cap(self):
        starts = [50.0, 150.0, 250.0, 350.0, 450.0, 550.0, 650.0]
        episodes = [_ep(f'e{i}', [_m(s) for s in starts]) for i in range(5)]
        prior = build_prior('test', episodes)

        assert len(prior.zones) == 6

    def test_boost_scales_with_support(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(10)]
        for i in range(6):
            episodes[i]['ad_markers'].append(_m(700.0))
        prior = build_prior('test', episodes)

        by_center = {round(z.center, 2): z for z in prior.zones}
        assert by_center[0.30].boost == pytest.approx(0.10)
        assert by_center[0.70].boost == pytest.approx(0.05)


class TestExclusions:

    def test_false_positive_overlap_excluded(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        corrections = [{'episode_id': 'e0', 'correction_type': 'false_positive',
                        'start': 300.0, 'end': 360.0}]
        prior = build_prior('test', episodes, corrections)

        assert prior.zones[0].support == 4

    def test_low_confidence_claude_excluded_fingerprint_included(self):
        episodes = [_ep(f'e{i}', [_m(300.0, conf=0.78, stage='fingerprint')])
                    for i in range(4)]
        episodes.append(_ep('e4', [_m(300.0, conf=0.78, stage='claude')]))
        prior = build_prior('test', episodes)

        assert prior.zones[0].support == 4

    def test_high_confidence_claude_included(self):
        episodes = [_ep(f'e{i}', [_m(300.0, conf=0.90, stage='claude')])
                    for i in range(5)]
        prior = build_prior('test', episodes)

        assert prior.zones[0].support == 5

    def test_untrusted_stage_low_confidence_excluded(self):
        # 'first_pass' (storage.py legacy stamp) and missing stages are not
        # trusted: the confidence floor must apply to them too.
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(3)]
        episodes.append(_ep('e3', [_m(300.0, conf=0.78, stage='first_pass')]))
        legacy = _m(300.0, conf=0.78, stage=None)
        del legacy['detection_stage']
        episodes.append(_ep('e4', [legacy]))
        prior = build_prior('test', episodes)

        assert prior.zones[0].support == 3

    def test_pre_gating_marker_set_skips_episode(self):
        # Markers without any was_cut key predate confidence gating (e.g. the
        # retry-ad-detection endpoint overwrites ad_markers_json with raw
        # detection output); such episodes must not poison the denominator.
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        raw = {'start': 300.0, 'end': 360.0, 'confidence': 0.95,
               'detection_stage': 'first_pass'}
        episodes.append(_ep('retried', [raw]))
        prior = build_prior('test', episodes)

        assert prior.episodes_considered == 5

    def test_was_cut_false_ignored(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        episodes[0]['ad_markers'][0]['was_cut'] = False
        prior = build_prior('test', episodes)

        assert prior.zones[0].support == 4


class TestCorrections:

    def test_create_and_confirm_become_events(self):
        episodes = [_ep(f'e{i}', []) for i in range(5)]
        corrections = [
            {'episode_id': f'e{i}', 'correction_type': 'create',
             'start': 300.0, 'end': 360.0} for i in range(3)
        ] + [
            {'episode_id': f'e{i}', 'correction_type': 'confirm',
             'start': 300.0, 'end': 360.0} for i in range(3, 5)
        ]
        prior = build_prior('test', episodes, corrections)

        assert prior is not None
        assert prior.zones[0].support == 5

    def test_boundary_adjustment_overrides_marker_start(self):
        episodes = [_ep(f'e{i}', [_m(300.0, end=400.0)]) for i in range(5)]
        corrections = [{'episode_id': f'e{i}', 'correction_type': 'boundary_adjustment',
                        'start': 380.0, 'end': 480.0} for i in range(5)]
        prior = build_prior('test', episodes, corrections)

        assert prior.zones[0].center == pytest.approx(0.38)

    def test_disjoint_boundary_adjustment_learns_corrected_position(self):
        # The user moved the break entirely; matching must use the original
        # bounds (which identify the marker), learning the corrected start.
        episodes = [_ep(f'e{i}', [_m(300.0, end=400.0)]) for i in range(5)]
        corrections = [{'episode_id': f'e{i}', 'correction_type': 'boundary_adjustment',
                        'start': 500.0, 'end': 600.0,
                        'orig_start': 300.0, 'orig_end': 400.0} for i in range(5)]
        prior = build_prior('test', episodes, corrections)

        assert len(prior.zones) == 1
        assert prior.zones[0].center == pytest.approx(0.50)


class TestDegenerates:

    def test_zero_duration_episode_skipped_entirely(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(5)]
        episodes.append(_ep('bad', [_m(300.0)], duration=0.0))
        prior = build_prior('test', episodes)

        assert prior.episodes_considered == 5

    def test_skipped_episodes_do_not_satisfy_min_gate(self):
        episodes = [_ep(f'e{i}', [_m(300.0)]) for i in range(4)]
        episodes.append(_ep('bad', [_m(300.0)], duration=None))
        assert build_prior('test', episodes) is None


class TestAppliesTo:

    def _prior(self, median=1000.0):
        return PositionalPrior(
            episodes_considered=10, median_duration=median,
            zones=[LearnedZone(center=0.3, low=0.25, high=0.35,
                               support=8, boost=0.075)])

    def test_comparable_duration_applies(self):
        prior = self._prior()
        assert prior.applies_to(1000.0)
        assert prior.applies_to(2000.0)  # exactly 2x median
        assert prior.applies_to(500.0)   # exactly half

    def test_outlier_duration_does_not_apply(self):
        prior = self._prior()
        assert not prior.applies_to(2001.0)
        assert not prior.applies_to(499.0)
        assert not prior.applies_to(0.0)


class TestFormatPriorHint:

    def _prior(self, centers, episodes_considered=10):
        zones = [LearnedZone(center=c, low=max(0.0, c - 0.05),
                             high=min(1.0, c + 0.05), support=8, boost=0.075)
                 for c in centers]
        return PositionalPrior(episodes_considered=episodes_considered,
                               median_duration=3600.0, zones=zones)

    def test_renders_absolute_times_for_episode_duration(self):
        hint = format_prior_hint(self._prior([0.25, 0.50]), 3600.0)

        assert '15:00' in hint
        assert '30:00' in hint
        assert 'learned from 10 previous episodes' in hint

    def test_hour_plus_times_use_hms(self):
        hint = format_prior_hint(self._prior([0.5]), 7200.0)

        assert '1:00:00' in hint

    def test_contains_guardrail_phrasing(self):
        hint = format_prior_hint(self._prior([0.5]), 3600.0)

        assert 'do NOT report an ad' in hint
        assert 'do NOT ignore ads found elsewhere' in hint

    def test_empty_for_none_prior_or_bad_duration(self):
        assert format_prior_hint(None, 3600.0) == ''
        assert format_prior_hint(self._prior([0.5]), 0.0) == ''
        assert format_prior_hint(self._prior([]), 3600.0) == ''


class TestDatabaseIntegration:

    SLUG = 'prior-pod'

    def _seed(self, db, count=5, status='processed', duration=DURATION):
        db.create_podcast(self.SLUG, 'https://example.com/feed.xml', 'Prior Pod')
        for i in range(count):
            eid = f'ep-{i:03d}'
            db.upsert_episode(
                self.SLUG, eid,
                original_url=f'https://example.com/{eid}.mp3',
                title=f'Episode {i}',
                status=status,
                original_duration=duration,
                published_at=f'2026-06-{i + 1:02d}T00:00:00+00:00',
            )
            db.save_episode_details(self.SLUG, eid, ad_markers=[_m(300.0)])

    def test_history_query_returns_processed_episodes(self, temp_db):
        self._seed(temp_db)
        rows = temp_db.get_recent_episode_ad_history(self.SLUG)

        assert len(rows) == 5
        assert rows[0]['episode_id'] == 'ep-004'  # newest first
        markers = json.loads(rows[0]['ad_markers_json'])
        assert markers[0]['start'] == 300.0

    def test_history_query_respects_limit_and_exclusion(self, temp_db):
        self._seed(temp_db, count=6)
        rows = temp_db.get_recent_episode_ad_history(self.SLUG, limit=5)
        assert len(rows) == 5
        assert all(r['episode_id'] != 'ep-000' for r in rows)  # oldest dropped

        rows = temp_db.get_recent_episode_ad_history(
            self.SLUG, exclude_episode_id='ep-005')
        assert all(r['episode_id'] != 'ep-005' for r in rows)

    def test_history_query_skips_unprocessed_and_short(self, temp_db):
        self._seed(temp_db)
        temp_db.upsert_episode(self.SLUG, 'pending-ep',
                               original_url='https://example.com/p.mp3',
                               title='Pending', status='pending',
                               original_duration=DURATION)
        temp_db.save_episode_details(self.SLUG, 'pending-ep', ad_markers=[_m(300.0)])
        temp_db.upsert_episode(self.SLUG, 'short-ep',
                               original_url='https://example.com/s.mp3',
                               title='Short', status='processed',
                               original_duration=30.0)
        temp_db.save_episode_details(self.SLUG, 'short-ep', ad_markers=[_m(10.0)])

        rows = temp_db.get_recent_episode_ad_history(self.SLUG)
        ids = {r['episode_id'] for r in rows}
        assert 'pending-ep' not in ids
        assert 'short-ep' not in ids

    def test_corrections_query_scoped_to_podcast(self, temp_db):
        self._seed(temp_db)
        temp_db.create_podcast('other-pod', 'https://example.com/o.xml', 'Other')
        temp_db.upsert_episode('other-pod', 'other-ep',
                               original_url='https://example.com/o.mp3',
                               title='Other', status='processed',
                               original_duration=DURATION)
        temp_db.create_pattern_correction(
            'false_positive', episode_id='ep-000',
            original_bounds={'start': 300.0, 'end': 360.0})
        temp_db.create_pattern_correction(
            'create', episode_id='ep-001',
            corrected_bounds={'start': 500.0, 'end': 560.0})
        temp_db.create_pattern_correction(
            'boundary_adjustment', episode_id='ep-002',
            original_bounds={'start': 300.0, 'end': 360.0},
            corrected_bounds={'start': 450.0, 'end': 510.0})
        temp_db.create_pattern_correction(
            'false_positive', episode_id='other-ep',
            original_bounds={'start': 100.0, 'end': 160.0})

        all_ids = [f'ep-{i:03d}' for i in range(5)] + ['other-ep']
        rows = temp_db.get_podcast_corrections_for_prior(self.SLUG, all_ids)

        assert len(rows) == 3
        by_type = {r['correction_type']: r for r in rows}
        assert by_type['false_positive']['start'] == 300.0
        assert by_type['create']['start'] == 500.0
        # boundary_adjustment carries corrected bounds plus the original
        # bounds used to match the marker it adjusted
        adj = by_type['boundary_adjustment']
        assert adj['start'] == 450.0
        assert adj['orig_start'] == 300.0
        assert adj['orig_end'] == 360.0

    def test_corrections_query_filters_by_episode_ids(self, temp_db):
        self._seed(temp_db)
        temp_db.create_pattern_correction(
            'create', episode_id='ep-000',
            corrected_bounds={'start': 500.0, 'end': 560.0})
        temp_db.create_pattern_correction(
            'create', episode_id='ep-001',
            corrected_bounds={'start': 500.0, 'end': 560.0})

        rows = temp_db.get_podcast_corrections_for_prior(self.SLUG, ['ep-000'])
        assert len(rows) == 1
        assert rows[0]['episode_id'] == 'ep-000'

        assert temp_db.get_podcast_corrections_for_prior(self.SLUG, []) == []

    def test_compute_end_to_end(self, temp_db):
        self._seed(temp_db)
        prior = compute_positional_prior(temp_db, self.SLUG)

        assert prior is not None
        assert prior.episodes_considered == 5
        assert len(prior.zones) == 1
        assert prior.zones[0].center == pytest.approx(0.30)
        assert prior.median_duration == pytest.approx(DURATION)

    def test_compute_excludes_current_episode(self, temp_db):
        self._seed(temp_db)
        prior = compute_positional_prior(temp_db, self.SLUG,
                                         exclude_episode_id='ep-004')
        assert prior is None  # only 4 learnable episodes remain

    def test_malformed_markers_json_skipped(self, temp_db):
        self._seed(temp_db)
        conn = temp_db.get_connection()
        conn.execute(
            """UPDATE episode_details SET ad_markers_json = 'not json'
               WHERE episode_id = (
                   SELECT e.id FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE p.slug = ? AND e.episode_id = 'ep-000')""",
            (self.SLUG,))
        conn.commit()

        prior = compute_positional_prior(temp_db, self.SLUG)
        assert prior is None  # 4 parseable episodes < min gate
