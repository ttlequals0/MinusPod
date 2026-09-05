"""Tests for the pure cross-episode detection aggregation logic."""
import pytest

from config import SEGMENT_CATEGORIES, count_pending_review, is_pending_review
from detection_review import (
    filter_detections, flatten_detections, paginate, sort_detections,
    summarize_cut_detections, summarize_detections,
)
from utils.markers import collapse_duplicate_markers
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
# Left in by the feed's category action. Uncut and undecided like a reject,
# but no verdict can be recorded against it.
KEPT = {'start': 300.0, 'end': 330.0, 'confidence': 1.0, 'was_cut': False,
        'category': 'outro', 'action_applied': 'keep'}


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

    def test_boundary_adjustment_maps_to_confirmed(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'boundary_adjustment',
             'start': 100.0, 'end': 130.0},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'confirmed'

    def test_correction_missing_bounds_is_ignored(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'confirm'},
            {'episode_id': 'ep-1', 'correction_type': 'confirm',
             'start': None, 'end': None},
        ]
        items = flatten_detections([_row(markers=[REJECTED])], corrections)
        assert items[0]['resolution'] == 'unresolved'

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

    def test_marker_missing_start_is_skipped(self):
        assert flatten_detections([_row(markers=[{'end': 30.0}])], []) == []

    def test_marker_with_none_start_is_skipped(self):
        assert flatten_detections([_row(markers=[{'start': None, 'end': 30.0}])], []) == []

    def test_a_marker_reports_its_own_stored_action(self):
        items = flatten_detections([_row(markers=[REJECTED, KEPT])], [])
        by_start = {i['start']: i['actionApplied'] for i in items}
        assert by_start == {100.0: None, 300.0: 'keep'}

    def test_a_cut_or_legacy_marker_over_a_keep_span_is_not_read_as_kept(self):
        """Neither one folds into the keep marker, so neither may borrow its
        verdict: a cut span is not a span the feed policy left in the audio."""
        cut = {'start': 300.0, 'end': 330.0, 'confidence': 0.9, 'was_cut': True}
        legacy = {'start': 300.2, 'end': 329.8, 'confidence': 0.9}
        items = flatten_detections([_row(markers=[cut, legacy, KEPT])], [])
        assert [i['actionApplied'] for i in items] == [None, None, 'keep']

    def test_the_folded_keep_row_reads_as_kept_in_every_consumer(self):
        """An undecided copy of a kept span no longer reaches storage: the fold
        collapses the pair, so the listing, the badge count and the episode page
        all read one keep marker."""
        undecided = {'start': 882.9, 'end': 906.0, 'category': 'self_promo',
                     'held_for_review': True, 'was_cut': False}
        kept = {'start': 882.9, 'end': 906.0, 'was_cut': False,
                'category': 'self_promo', 'action_applied': 'keep'}
        markers, folded = collapse_duplicate_markers([undecided, kept])
        assert folded == 1

        items = flatten_detections([_row(markers=markers)], [])
        assert [i['actionApplied'] for i in items] == ['keep']
        assert filter_detections(items, status='needs_review') == []
        assert count_pending_review(markers) == 0
        assert is_pending_review(markers[0]) is False


class TestSummarize:
    def test_counts_by_status_and_resolution(self):
        corrections = [
            {'episode_id': 'ep-1', 'correction_type': 'false_positive',
             'start': 100.0, 'end': 130.0},
        ]
        items = flatten_detections(
            [_row(markers=[ACCEPTED, REJECTED, HELD])], corrections)
        counts = summarize_detections(items)
        assert counts == {
            'total': 3, 'needsReview': 1, 'pending': 1, 'rejected': 1,
            'accepted': 1, 'confirmed': 0, 'dismissed': 1,
        }

    def test_keeps_are_not_counted_as_needing_review(self):
        counts = summarize_detections(
            flatten_detections([_row(markers=[REJECTED, KEPT])], []))
        assert counts['needsReview'] == 1
        assert counts['rejected'] == 2

    def test_empty(self):
        assert summarize_detections([]) == {
            'total': 0, 'needsReview': 0, 'pending': 0, 'rejected': 0,
            'accepted': 0, 'confirmed': 0, 'dismissed': 0,
        }


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

    def test_needs_review_excludes_category_keeps(self):
        """A keep is settled by feed policy and the corrections endpoint
        refuses a verdict on it, so listing it as needing review would offer
        a decision nobody can make."""
        items = flatten_detections([_row(markers=[REJECTED, KEPT])], [])
        out = filter_detections(items, status='needs_review')
        assert [i['start'] for i in out] == [100.0]

    def test_kept_markers_still_listed_under_their_cut_status(self):
        items = flatten_detections([_row(markers=[KEPT])], [])
        assert [i['start'] for i in filter_detections(items, status='all')] == [300.0]
        assert [i['start'] for i in filter_detections(items, status='rejected')] == [300.0]

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

    def test_none_confidence_sorts_last_on_asc(self):
        items = self._items()
        items[0]['confidence'] = None
        out = sort_detections(items, sort='confidence', order='asc')
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

    def test_tied_confidence_breaks_ties_by_date_then_start(self):
        rows = [
            _row(episode_id='ep-1', published='2026-07-01T00:00:00Z',
                 markers=[{'start': 50.0, 'end': 60.0, 'confidence': 0.5},
                          {'start': 10.0, 'end': 20.0, 'confidence': 0.5}]),
            _row(slug='feed-b', title='B Feed', episode_id='ep-2',
                 published='2026-07-05T00:00:00Z',
                 markers=[{'start': 30.0, 'end': 40.0, 'confidence': 0.5}]),
        ]
        items = flatten_detections(rows, [])
        out = sort_detections(items, sort='confidence', order='asc')
        assert [(i['episodeId'], i['start']) for i in out] == [
            ('ep-1', 10.0), ('ep-1', 50.0), ('ep-2', 30.0)]


CROSS_PROMO = {'start': 300.0, 'end': 340.0, 'confidence': 0.8,
               'category': 'cross_promo', 'action_applied': 'remove',
               'sponsor': 'The Daily Tech Show'}
KEPT_OUTRO = {'start': 400.0, 'end': 420.0, 'confidence': 0.7,
              'category': 'outro', 'action_applied': 'keep', 'was_cut': False}


class TestCategoryFields:
    def test_flatten_carries_category_and_action(self):
        items = flatten_detections([_row(markers=[CROSS_PROMO])], [])
        assert items[0]['category'] == 'cross_promo'
        assert items[0]['actionApplied'] == 'remove'

    def test_unset_category_stays_none(self):
        items = flatten_detections([_row(markers=[ACCEPTED])], [])
        assert items[0]['category'] is None
        assert items[0]['actionApplied'] is None


class TestCategoryFilter:
    def _items(self):
        return flatten_detections(
            [_row(markers=[ACCEPTED, CROSS_PROMO, KEPT_OUTRO])], [])

    def test_matches_one_category_exactly(self):
        out = filter_detections(self._items(), status='all', category='cross_promo')
        assert [i['start'] for i in out] == [300.0]

    def test_none_matches_only_unset(self):
        out = filter_detections(self._items(), status='all', category='none')
        assert [i['start'] for i in out] == [10.0]

    def test_absent_category_is_a_no_op(self):
        assert len(filter_detections(self._items(), status='all')) == 3

    def test_category_composes_with_status(self):
        out = filter_detections(self._items(), status='accepted', category='cross_promo')
        assert [i['start'] for i in out] == [300.0]

    @pytest.mark.parametrize('category', list(SEGMENT_CATEGORIES) + ['none'])
    def test_every_category_value_selects_only_its_markers(self, category):
        markers = [dict(ACCEPTED, start=10.0 * (i + 1), end=10.0 * (i + 1) + 5.0,
                        category=cat)
                   for i, cat in enumerate(SEGMENT_CATEGORIES)]
        markers.append({'start': 500.0, 'end': 510.0, 'confidence': 0.9})
        items = flatten_detections([_row(markers=markers)], [])
        out = filter_detections(items, status='all', category=category)
        assert len(out) == 1
        assert (out[0]['category'] or 'none') == category


class TestCutSummary:
    def test_totals(self):
        items = flatten_detections(
            [_row(markers=[ACCEPTED, CROSS_PROMO]),
             _row(slug='feed-b', episode_id='ep-2', markers=[CROSS_PROMO])], [])
        cut = [i for i in items if i['status'] == 'accepted']
        s = summarize_cut_detections(cut)
        assert s['count'] == 3
        assert s['durationSeconds'] == 110.0
        assert s['byCategory']['cross_promo'] == 2
        assert s['byCategory']['none'] == 1
        assert s['distinctPodcasts'] == 2

    def test_sponsor_count_is_case_insensitive_and_ignores_blanks(self):
        items = [
            {'start': 0, 'end': 10, 'sponsor': 'Acme', 'feedSlug': 'a'},
            {'start': 0, 'end': 10, 'sponsor': 'acme  ', 'feedSlug': 'a'},
            {'start': 0, 'end': 10, 'sponsor': '   ', 'feedSlug': 'a'},
            {'start': 0, 'end': 10, 'sponsor': None, 'feedSlug': 'a'},
        ]
        assert summarize_cut_detections(items)['distinctSponsors'] == 1

    def test_every_category_has_a_bucket_when_empty(self):
        s = summarize_cut_detections([])
        assert s['count'] == 0
        assert s['durationSeconds'] == 0
        assert s['byCategory']['none'] == 0
        for cat in SEGMENT_CATEGORIES:
            assert s['byCategory'][cat] == 0

    def test_inverted_and_missing_bounds_contribute_no_duration(self):
        items = [{'start': 50, 'end': 10, 'feedSlug': 'a'},
                 {'start': None, 'end': None, 'feedSlug': 'a'}]
        assert summarize_cut_detections(items)['durationSeconds'] == 0

    def test_unknown_category_value_still_counted(self):
        """A category added backend-side before this list knows about it must not
        raise or vanish from the totals."""
        s = summarize_cut_detections([{'start': 0, 'end': 5, 'category': 'brand_new'}])
        assert s['byCategory']['brand_new'] == 1
        assert s['count'] == 1


class TestReviewerFields:
    ADJUSTED = dict(ACCEPTED, reviewer_verdict='adjust',
                    reviewer_original_start=8.0, reviewer_original_end=41.0)

    def test_flatten_exposes_reviewer_fields(self):
        item = flatten_detections([_row(markers=[self.ADJUSTED])], [])[0]
        assert item['reviewerVerdict'] == 'adjust'
        assert item['reviewerOriginalStart'] == 8.0
        assert item['reviewerOriginalEnd'] == 41.0

    def test_unreviewed_marker_has_null_reviewer_fields(self):
        item = flatten_detections([_row(markers=[ACCEPTED])], [])[0]
        assert item['reviewerVerdict'] is None
        assert item['reviewerOriginalStart'] is None
        assert item['reviewerOriginalEnd'] is None
        assert item['reviewerMoved'] is False

    def test_legacy_marker_without_flag_infers_moved(self):
        """No reviewer_moved field (written before it existed): adjust verdict
        plus recorded originals is inferred as moved."""
        item = flatten_detections([_row(markers=[self.ADJUSTED])], [])[0]
        assert item['reviewerMoved'] is True

    def test_human_approved_marker_flag_false_overrides_inference(self):
        """_mark_held_marker_approved stamps reviewer_moved=False even when
        it also writes originals, so the explicit flag must win over the
        legacy heuristic (which would otherwise say True here)."""
        marker = dict(self.ADJUSTED, reviewer_moved=False)
        item = flatten_detections([_row(markers=[marker])], [])[0]
        assert item['reviewerMoved'] is False

    def test_reviewer_stamped_marker_flag_true_is_moved(self):
        marker = dict(ACCEPTED, reviewer_moved=True)
        item = flatten_detections([_row(markers=[marker])], [])[0]
        assert item['reviewerMoved'] is True

    def _items(self):
        base = {'status': 'accepted', 'feedSlug': 'a', 'sponsor': '', 'reason': '',
                'reviewerOriginalStart': None, 'reviewerOriginalEnd': None}
        return [
            dict(base, reviewerVerdict='adjust', reviewerOriginalStart=8.0,
                 reviewerOriginalEnd=41.0, reviewerMoved=True),
            # Held contradiction or split piece: verdict kept, no move recorded.
            dict(base, reviewerVerdict='adjust', reviewerMoved=False),
            dict(base, reviewerVerdict='confirmed', reviewerMoved=False),
            dict(base, reviewerVerdict='reject', reviewerMoved=False),
            dict(base, reviewerVerdict='failure', reviewerMoved=False),
            dict(base, reviewerVerdict=None, reviewerMoved=False),
        ]

    def test_adjusted_requires_verdict_and_original_span(self):
        out = filter_detections(self._items(), status='all', reviewer='adjusted')
        assert [i['reviewerOriginalStart'] for i in out] == [8.0]

    def test_unadjusted_keeps_adjust_without_span_and_other_verdicts(self):
        out = filter_detections(self._items(), status='all', reviewer='unadjusted')
        assert [i['reviewerVerdict'] for i in out] == [
            'adjust', 'confirmed', 'reject', 'failure', None]

    def test_empty_and_absent_reviewer_are_no_ops(self):
        assert len(filter_detections(self._items(), status='all', reviewer='')) == 6
        assert len(filter_detections(self._items(), status='all')) == 6
