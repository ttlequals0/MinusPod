"""Tests for the pure cross-episode detection aggregation logic."""
from detection_review import (
    filter_detections, flatten_detections, paginate, sort_detections,
)
import json


def _row(slug='feed-a', title='Feed A', episode_id='ep-1',
         episode_title='Ep 1', published='2026-07-01T00:00:00Z',
         original_file='orig.mp3', markers=None):
    return {
        'feed_slug': slug, 'feed_title': title,
        'episode_id': episode_id, 'episode_title': episode_title,
        'published_at': published, 'created_at': '2026-06-30T00:00:00Z',
        'original_file': original_file,
        'ad_markers_json': json.dumps(markers if markers is not None else []),
    }


ACCEPTED = {'start': 10.0, 'end': 40.0, 'confidence': 0.9,
            'sponsor': 'Acme', 'reason': 'sponsor read'}
REJECTED = {'start': 100.0, 'end': 130.0, 'confidence': 0.4, 'was_cut': False,
            'validation': {'decision': 'REJECT'}}
HELD = {'start': 200.0, 'end': 230.0, 'confidence': 0.6,
        'held_for_review': True, 'was_cut': False}


class TestFlatten:
    def test_status_buckets_match_episode_endpoint(self):
        items = flatten_detections([_row(markers=[ACCEPTED, REJECTED, HELD])], [])
        by_start = {i['start']: i for i in items}
        assert by_start[10.0]['status'] == 'accepted'
        assert by_start[100.0]['status'] == 'rejected'
        assert by_start[200.0]['status'] == 'pending'

    def test_uncut_marker_without_decision_is_rejected(self):
        marker = {'start': 5.0, 'end': 15.0, 'was_cut': False}
        items = flatten_detections([_row(markers=[marker])], [])
        assert items[0]['status'] == 'rejected'

    def test_resolution_matches_corrections_within_tolerance(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'confirm',
             'start': 100.4, 'end': 130.3},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'confirmed'

    def test_resolution_outside_tolerance_is_unresolved(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'confirm',
             'start': 101.0, 'end': 130.0},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'unresolved'

    def test_resolution_requires_same_episode(self):
        corrections = [
            {'episode_id': 'other-ep', 'correction_type': 'false_positive',
             'start': 100.0, 'end': 130.0},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'unresolved'

    def test_false_positive_maps_to_dismissed(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'false_positive',
             'start': 100.0, 'end': 130.0},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'dismissed'

    def test_output_field_mapping(self):
        items = flatten_detections([_row(markers=[ACCEPTED])], [])
        item = items[0]
        assert item['feedSlug'] == 'feed-a'
        assert item['feedTitle'] == 'Feed A'
        assert item['episodeId'] == 'ep-1'
        assert item['episodeTitle'] == 'Ep 1'
        assert item['publishDate'] == '2026-07-01T00:00:00Z'
        assert item['hasOriginalAudio'] is True
        assert item['sponsor'] == 'Acme'

    def test_publish_date_falls_back_to_created_at(self):
        items = flatten_detections([_row(published=None, markers=[ACCEPTED])], [])
        assert items[0]['publishDate'] == '2026-06-30T00:00:00Z'

    def test_malformed_marker_json_is_skipped(self):
        row = _row(markers=[ACCEPTED])
        row['ad_markers_json'] = '{not json'
        assert flatten_detections([row], []) == []


class TestFilter:
    def _items(self):
        return flatten_detections([_row(markers=[ACCEPTED, REJECTED, HELD])], [])

    def test_needs_review_excludes_accepted(self):
        out = filter_detections(self._items(), status='needs_review')
        assert {i['status'] for i in out} == {'rejected', 'pending'}

    def test_needs_review_excludes_resolved(self):
        corrections = [{'episode_id': 'ep-1', 'correction_type': 'confirm',
                        'start': 100.0, 'end': 130.0}]
        items = flatten_detections(
            [_row(markers=[REJECTED, HELD])], corrections)
        out = filter_detections(items, status='needs_review')
        assert [i['start'] for i in out] == [200.0]

    def test_single_status_filters(self):
        out = filter_detections(self._items(), status='accepted')
        assert [i['start'] for i in out] == [10.0]

    def test_all_returns_everything(self):
        assert len(filter_detections(self._items(), status='all')) == 3

    def test_feed_filter(self):
        items = flatten_detections(
            [_row(markers=[ACCEPTED]),
             _row(slug='feed-b', title='Feed B', episode_id='ep-2',
                  markers=[ACCEPTED])], [])
        out = filter_detections(items, status='all', feed='feed-b')
        assert [i['feedSlug'] for i in out] == ['feed-b']

    def test_text_search_matches_sponsor_and_reason_case_insensitive(self):
        items = flatten_detections([_row(markers=[ACCEPTED, REJECTED])], [])
        assert len(filter_detections(items, status='all', q='ACME')) == 1
        assert len(filter_detections(items, status='all', q='sponsor read')) == 1


class TestSortAndPaginate:
    def _items(self):
        return flatten_detections(
            [_row(markers=[ACCEPTED]),
             _row(slug='feed-b', title='B Feed', episode_id='ep-2',
                  published='2026-07-05T00:00:00Z', markers=[REJECTED])], [])

    def test_date_desc_default(self):
        out = sort_detections(self._items())
        assert out[0]['episodeId'] == 'ep-2'

    def test_confidence_asc(self):
        out = sort_detections(self._items(), sort='confidence', order='asc')
        assert out[0]['confidence'] == 0.4

    def test_none_confidence_sorts_last_on_desc(self):
        items = self._items()
        items[0]['confidence'] = None
        out = sort_detections(items, sort='confidence', order='desc')
        assert out[-1]['confidence'] is None

    def test_podcast_sort(self):
        out = sort_detections(self._items(), sort='podcast', order='asc')
        assert out[0]['feedTitle'] == 'B Feed'

    def test_paginate_math(self):
        items = list(range(45))
        page_items, total, total_pages, page = paginate(items, page=3, limit=20)
        assert (total, total_pages, page) == (45, 3, 3)
        assert page_items == list(range(40, 45))

    def test_paginate_clamps_page_beyond_end(self):
        page_items, total, total_pages, page = paginate([1, 2], page=9, limit=20)
        assert page == 1
        assert page_items == [1, 2]

    def test_paginate_empty(self):
        page_items, total, total_pages, page = paginate([], page=1, limit=20)
        assert (page_items, total, total_pages, page) == ([], 0, 1, 1)
