"""Tests for the Ad Inbox API helpers.

Focus on the status-derivation logic that maps each ad in
``episode_details.ad_markers_json`` to a user-facing status by joining
against ``pattern_corrections``.
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


def _seed_episode_with_ads(db, slug, episode_id, ads):
    """Create a podcast + episode + ad_markers row in one shot."""
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, f'https://{slug}.example/feed.xml',
                          slug.replace('-', ' ').title())
    db.upsert_episode(
        slug, episode_id,
        original_url=f'https://{slug}.example/{episode_id}.mp3',
        title=f'Episode {episode_id}',
        status='processed',
        processed_file=f'episodes/{episode_id}.mp3',
        published_at='2026-05-08T10:00:00Z',
    )
    db.save_episode_details(slug, episode_id, ad_markers=ads)


class TestBoundsOverlap:
    def test_overlap_above_threshold(self):
        from ad_inbox_service import bounds_overlap_50 as _bounds_overlap_50
        # Same range
        assert _bounds_overlap_50(10.0, 30.0, 10.0, 30.0) is True
        # 75% overlap of the shorter
        assert _bounds_overlap_50(10.0, 30.0, 15.0, 30.0) is True

    def test_no_overlap(self):
        from ad_inbox_service import bounds_overlap_50 as _bounds_overlap_50
        assert _bounds_overlap_50(10.0, 30.0, 40.0, 60.0) is False
        # Adjacent, not overlapping
        assert _bounds_overlap_50(10.0, 30.0, 30.0, 50.0) is False

    def test_below_threshold(self):
        from ad_inbox_service import bounds_overlap_50 as _bounds_overlap_50
        # 25% overlap of the shorter — under the 50% bar
        assert _bounds_overlap_50(10.0, 30.0, 25.0, 50.0) is False

    def test_zero_length_returns_false(self):
        from ad_inbox_service import bounds_overlap_50 as _bounds_overlap_50
        assert _bounds_overlap_50(10.0, 10.0, 10.0, 30.0) is False


class TestEnumerateInbox:
    def test_yields_one_item_per_ad_with_pending_default(self, temp_db):
        from ad_inbox_service import enumerate_inbox_items as _enumerate_inbox_items
        _seed_episode_with_ads(temp_db, 'science-friday', 'e1', [
            {'start': 30.0, 'end': 60.0, 'sponsor': 'Progressive',
             'reason': 'Insurance ad', 'confidence': 0.9,
             'detection_stage': 'llm', 'pattern_id': None},
            {'start': 800.0, 'end': 850.0, 'sponsor': 'Squarespace',
             'reason': 'web hosting', 'confidence': 0.85,
             'detection_stage': 'llm', 'pattern_id': None},
        ])

        items = list(_enumerate_inbox_items(temp_db))

        assert len(items) == 2
        assert all(i['status'] == 'pending' for i in items)
        assert items[0]['adIndex'] == 0
        assert items[0]['sponsor'] == 'Progressive'
        assert items[1]['adIndex'] == 1
        assert items[0]['podcastTitle'] == 'Science Friday'

    def test_correction_maps_status(self, temp_db):
        from ad_inbox_service import enumerate_inbox_items as _enumerate_inbox_items
        _seed_episode_with_ads(temp_db, 'pod-a', 'e2', [
            {'start': 100.0, 'end': 130.0, 'sponsor': 'BetterHelp',
             'reason': 'therapy', 'confidence': 0.9,
             'detection_stage': 'llm', 'pattern_id': None},
            {'start': 500.0, 'end': 540.0, 'sponsor': 'AthleticGreens',
             'reason': 'greens', 'confidence': 0.85,
             'detection_stage': 'llm', 'pattern_id': None},
            {'start': 900.0, 'end': 920.0, 'sponsor': 'NordVPN',
             'reason': 'vpn', 'confidence': 0.88,
             'detection_stage': 'llm', 'pattern_id': None},
        ])

        # User confirmed first ad, rejected second, adjusted third
        temp_db.create_pattern_correction(
            correction_type='confirm', episode_id='e2',
            original_bounds={'start': 100.0, 'end': 130.0})
        temp_db.create_pattern_correction(
            correction_type='false_positive', episode_id='e2',
            original_bounds={'start': 500.0, 'end': 540.0})
        temp_db.create_pattern_correction(
            correction_type='boundary_adjustment', episode_id='e2',
            original_bounds={'start': 900.0, 'end': 920.0},
            corrected_bounds={'start': 905.0, 'end': 918.0})

        items = list(_enumerate_inbox_items(temp_db))
        statuses = {i['adIndex']: i['status'] for i in items}

        assert statuses == {0: 'confirmed', 1: 'rejected', 2: 'adjusted'}
        # Adjusted ad surfaces corrected bounds for the UI to show diff
        adjusted = [i for i in items if i['adIndex'] == 2][0]
        assert adjusted['correctedBounds'] == {'start': 905.0, 'end': 918.0}

    def test_partial_overlap_below_50_pct_stays_pending(self, temp_db):
        from ad_inbox_service import enumerate_inbox_items as _enumerate_inbox_items
        _seed_episode_with_ads(temp_db, 'pod-b', 'e3', [
            {'start': 100.0, 'end': 130.0, 'sponsor': 'Casper',
             'reason': 'mattress', 'confidence': 0.9,
             'detection_stage': 'llm', 'pattern_id': None},
        ])
        # Correction overlaps only 5s of the 30s ad — well under 50%
        temp_db.create_pattern_correction(
            correction_type='confirm', episode_id='e3',
            original_bounds={'start': 125.0, 'end': 200.0})

        items = list(_enumerate_inbox_items(temp_db))
        assert items[0]['status'] == 'pending'

    def test_skips_episodes_without_markers(self, temp_db):
        from ad_inbox_service import enumerate_inbox_items as _enumerate_inbox_items
        # Episode with no ad_markers_json
        temp_db.create_podcast('pod-c', 'https://c.example/feed.xml', 'Pod C')
        temp_db.upsert_episode(
            'pod-c', 'e4', original_url='https://c.example/e4.mp3',
            status='processed', processed_file='episodes/e4.mp3')
        # Episode with empty list
        _seed_episode_with_ads(temp_db, 'pod-d', 'e5', [])

        assert list(_enumerate_inbox_items(temp_db)) == []
