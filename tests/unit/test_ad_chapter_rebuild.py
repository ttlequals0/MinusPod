"""Chapter-only rebuild for decisions that leave the audio alone."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('ad_chapter_rebuild_test_')

from ad_chapters import AdChapterConfig  # noqa: E402
from main_app import app, processing  # noqa: E402

CFG = AdChapterConfig(
    enabled=True, categories={'sponsor': True}, include_held=True,
    title_format='[mp:{category}]', held_title_format='[mp:{category}?]',
    resume_title='Show', min_confidence=0.9)


def _wire(monkeypatch, stored, path_exists=True, podcast_reads=None):
    saved, embedded, refreshed = [], [], []
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: {'status': 'processed', 'processed_version': 2})
    monkeypatch.setattr(
        processing.db, 'get_podcast_by_slug',
        lambda s: (podcast_reads.append(s) if podcast_reads is not None else None)
        or {'chapters_mode': 'auto'})
    monkeypatch.setattr(processing, 'resolve_ad_chapter_config', lambda db, row, slug=None: CFG)
    monkeypatch.setattr(processing.storage, 'get_chapters_json', lambda s, e: stored)
    monkeypatch.setattr(processing.storage, 'get_applied_cuts', lambda s, e: [])
    monkeypatch.setattr(processing.storage, 'save_chapters_json',
                        lambda s, e, cj: saved.append(cj))
    path = MagicMock()
    path.exists.return_value = path_exists
    monkeypatch.setattr(processing.storage, 'get_episode_path', lambda *a, **k: path)
    monkeypatch.setattr(processing, 'get_audio_duration', lambda p: 3600.0)
    monkeypatch.setattr(processing, 'embed_chapters',
                        lambda p, chapters, duration=None: embedded.append(chapters) or True)
    monkeypatch.setattr(processing, '_refresh_rss_for_slug', lambda s, e: refreshed.append(e))
    return saved, embedded, refreshed


def test_rejected_held_marker_drops_its_chapter(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [
        {'startTime': 1, 'title': 'Intro'},
        {'startTime': 900, 'title': '[mp:sponsor?]', 'kind': 'ad', 'category': 'sponsor', 'held': True},
        {'startTime': 960, 'title': 'Show', 'kind': 'resume'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    # After reject the marker is no longer pending review.
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'remove',
                'held_for_review': False, 'was_cut': False, 'category': 'sponsor'}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is True
    assert saved[0]['chapters'] == [{'startTime': 1, 'title': 'Intro'}]
    assert embedded == [[{'startTime': 1, 'title': 'Intro'}]]
    assert refreshed == ['a1b2c3d4e5f6']


def test_no_change_means_no_write(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', []) is False
    assert not saved and not embedded and not refreshed


def test_embed_failure_leaves_json_untouched(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    monkeypatch.setattr(processing, 'embed_chapters', lambda *a, **k: False)
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
                'was_cut': False, 'category': 'sponsor', 'confidence': 0.95}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is False
    assert not saved


def test_empty_rebuilt_set_clears_the_stored_ad_chapters(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [
        {'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad', 'category': 'sponsor'},
        {'startTime': 960, 'title': 'Show', 'kind': 'resume'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'remove',
                'held_for_review': False, 'was_cut': True, 'category': 'sponsor'}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is True
    assert saved[0]['chapters'] == []
    assert embedded == [[]]


def test_unknown_applied_cuts_leave_the_chapters_alone(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    monkeypatch.setattr(processing.storage, 'get_applied_cuts', lambda s, e: None)
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
                'was_cut': False, 'category': 'sponsor', 'confidence': 0.95}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is False
    assert not saved and not embedded


def test_nothing_to_rebuild_skips_the_podcast_row_read(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    podcast_reads = []
    saved, embedded, refreshed = _wire(monkeypatch, stored, podcast_reads=podcast_reads)
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'remove',
                'held_for_review': False, 'was_cut': True, 'category': 'sponsor'}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is False
    assert podcast_reads == []
    assert not saved


def test_caller_supplied_episode_row_is_not_reloaded(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    reads = []
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda s, e: reads.append(e) or {'status': 'processed'})
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
                'was_cut': False, 'category': 'sponsor', 'confidence': 0.95}]
    assert processing.rebuild_ad_chapters(
        'example-podcast', 'a1b2c3d4e5f6', markers,
        episode={'status': 'processed', 'processed_version': 2}) is True
    assert reads == []


def test_missing_file_still_updates_json(monkeypatch):
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored, path_exists=False)
    markers = [{'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
                'was_cut': False, 'category': 'sponsor', 'confidence': 0.95}]
    assert processing.rebuild_ad_chapters('example-podcast', 'a1b2c3d4e5f6', markers) is True
    assert [c['startTime'] for c in saved[0]['chapters']] == [1, 900, 960]
    assert not embedded


KEEP_MARKER = {'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
               'was_cut': False, 'category': 'sponsor', 'confidence': 0.95}


def test_two_rebuilds_of_one_episode_do_not_remux_concurrently(monkeypatch):
    """The apply pass and a pipeline run can reach the same episode at once."""
    stored = {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    saved, embedded, refreshed = _wire(monkeypatch, stored)
    counter_lock = threading.Lock()
    state = {'active': 0, 'peak': 0}

    def _tracked_embed(p, chapters, duration=None):
        with counter_lock:
            state['active'] += 1
            state['peak'] = max(state['peak'], state['active'])
        time.sleep(0.05)
        with counter_lock:
            state['active'] -= 1
        embedded.append(chapters)
        return True

    monkeypatch.setattr(processing, 'embed_chapters', _tracked_embed)
    threads = [threading.Thread(
        target=processing.rebuild_ad_chapters,
        args=('example-podcast', 'a1b2c3d4e5f6', [KEEP_MARKER])) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(saved) == 2
    assert state['peak'] == 1
    assert not processing._embed_locks


SLUG = 'ad-chapter-rebuild-test'
EPISODE_ID = 'a1b2c3d4e5f6'
HELD_AD = {'start': 900.0, 'end': 960.0, 'confidence': 0.95, 'category': 'sponsor',
           'reason': 'sponsor read', 'was_cut': False, 'action_applied': 'remove',
           'held_for_review': True, 'hold_reason': 'low_confidence'}


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Ad Chapter Rebuild Test')
    temp_db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                           original_url='https://example.com/ep.mp3',
                           title='Episode One', original_duration=3600.0)
    temp_db.save_episode_details(SLUG, EPISODE_ID, ad_markers=[dict(HELD_AD)],
                                 pending_review_count=1)
    return temp_db


@pytest.fixture
def rebuilds(monkeypatch):
    calls = []
    monkeypatch.setattr(
        processing, 'rebuild_ad_chapters',
        lambda slug, episode_id, markers, episode=None:
            calls.append(markers) or True)
    return calls


def _correct(client, payload):
    return client.post(f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections', json=payload)


def test_reject_stamps_the_episode_without_touching_the_audio(client, seeded, rebuilds):
    r = _correct(client, {'type': 'reject',
                          'original_ad': {'start': 900.0, 'end': 960.0}})
    assert r.status_code == 200
    assert seeded.count_episodes_pending_recut() == 1
    # No remux in the request: the apply pass rebuilds the chapters.
    assert rebuilds == []


def test_confirm_stamps_the_episode_too(client, seeded, rebuilds):
    r = _correct(client, {'type': 'confirm',
                          'original_ad': {'start': 900.0, 'end': 960.0}})
    assert r.status_code == 200
    assert seeded.count_episodes_pending_recut() == 1
    assert rebuilds == []
