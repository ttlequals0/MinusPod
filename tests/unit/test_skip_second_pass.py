"""Tests for issue #599: per-feed skip of the pass-2 verification scan.

Pass 1 still runs and cuts; the second detection sweep over the cut audio does
not, which roughly halves the per-episode ad-detection LLM spend on feeds that
do not need it.
"""
import os
import sqlite3
import sys
import tempfile
from contextlib import ExitStack

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='skip2nd_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from types import SimpleNamespace
from unittest.mock import patch

import main_app.processing as processing
from config import resolve_skip_second_pass

SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'start': 5.0, 'end': 10.0, 'text': 'world'}]


def _run_pipeline(skip_second_pass):
    podcast_row = {'id': 1, 'slug': 'skip2-feed', 'description': None,
                   'tags': None, 'dai_platform': None,
                   'passthrough_enabled': None, 'skip_ad_detection': None,
                   'skip_second_pass': skip_second_pass}
    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))
        db = p(processing, 'db')
        p(processing, 'status_service')
        storage = p(processing, 'storage')
        audio_processor = p(processing, 'audio_processor')
        p(processing, 'start_episode_token_tracking')
        p(processing, 'get_available_memory_gb', return_value=None)
        p(processing, 'get_min_cut_confidence', return_value=0.8)
        p(processing, '_download_and_transcribe',
          return_value=('/tmp/skip2.mp3', SEGMENTS))
        p(processing, '_run_differential_fetch', return_value=None)
        p(processing, '_run_audio_analysis', return_value=None)
        p(processing, 'load_positional_prior', return_value=None)
        p(processing, '_detect_ads_first_pass', return_value=([], 0, None))
        p(processing, '_refine_and_validate', return_value=([], []))
        p(processing, '_run_ad_reviewer', return_value=([], []))
        p(processing, '_snap_terminal_starts', return_value=[])
        p(processing, '_complete_cut_tails', return_value=[])
        local_ap_cls = p(processing, 'AudioProcessor')
        # Mirrors the real skip path: verification_ok is False when the
        # pass did not run, so the caller records no verification stat.
        verify = p(processing, '_run_verification_pass',
                   return_value=(0, [], [], [], '/tmp/cut.mp3', 0,
                                 not skip_second_pass, 0))
        p(processing, '_generate_assets')
        finalize = p(processing, '_finalize_episode')
        p(processing.shutil, 'move')
        p(processing.os, 'unlink')
        p(processing.os.path, 'exists', return_value=False)

        db.get_episode.return_value = {}
        db.get_podcast_by_slug.return_value = podcast_row
        db.get_setting.return_value = 'false'
        db.get_all_settings.return_value = {}
        audio_processor.get_audio_duration.return_value = 100.0
        local_ap = local_ap_cls.return_value
        local_ap.process_episode.return_value = ('/tmp/cut.mp3', [])
        local_ap.get_audio_duration.return_value = 100.0
        storage.get_episode_path.return_value = '/tmp/final.mp3'
        result = processing.process_episode(
            'skip2-feed', 'ep1', 'https://example.com/ep1.mp3')
    return {'result': result, 'verify': verify, 'finalize': finalize}


class TestResolveSkipSecondPass:
    def test_null_and_zero_run_pass_two(self):
        assert resolve_skip_second_pass(None) is False
        assert resolve_skip_second_pass({}) is False
        assert resolve_skip_second_pass({'skip_second_pass': None}) is False
        assert resolve_skip_second_pass({'skip_second_pass': 0}) is False

    def test_one_skips(self):
        assert resolve_skip_second_pass({'skip_second_pass': 1}) is True


class TestSkipSecondPass:
    def test_flag_forwarded_and_stat_suppressed(self):
        m = _run_pipeline(skip_second_pass=1)

        assert m['result'] is True
        assert m['verify'].call_args.kwargs['skip_verification'] is True
        run_stats = m['finalize'].call_args.kwargs['run_stats']
        # A skipped scan must not read as a clean one; 0 would be
        # indistinguishable from a pass that ran and found nothing.
        assert 'verification_ads_cut' not in run_stats
        # The stat that tells a skipped run apart from a crashed one.
        assert run_stats['verification_skipped'] is True

    def test_flag_off_runs_pass_two(self):
        m = _run_pipeline(skip_second_pass=None)

        assert m['result'] is True
        assert m['verify'].call_args.kwargs['skip_verification'] is False
        run_stats = m['finalize'].call_args.kwargs['run_stats']
        assert run_stats['verification_ads_cut'] == 0
        assert 'verification_skipped' not in run_stats

    def test_verification_pass_early_return(self):
        # Short-circuits before VerificationPass is constructed, so no
        # transcription and no LLM call; processed_path passes through.
        ctx = SimpleNamespace(slug='skip2-feed', episode_id='ep1')
        with patch('verification_pass.VerificationPass') as verifier_cls:
            result = processing._run_verification_pass(
                ctx, '/tmp/skip2-cut.mp3', [], False, 0.8, None, None,
                skip_verification=True)
        assert result == (0, [], [], [], '/tmp/skip2-cut.mp3', 0, False, 0)
        verifier_cls.assert_not_called()


@pytest.fixture
def legacy_db_path(tmp_path):
    """A podcasts table carrying skip_second_pass = 1 from the old column."""
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT,
            skip_second_pass INTEGER DEFAULT 0
        )
    """)
    conn.executemany(
        "INSERT INTO podcasts (slug, source_url, title, skip_second_pass) "
        "VALUES (?, ?, ?, ?)",
        [('legacy-on', 'https://example.com/a.xml', 'Legacy On', 1),
         ('legacy-off', 'https://example.com/b.xml', 'Legacy Off', 0),
         ('legacy-null', 'https://example.com/c.xml', 'Legacy Null', None)],
    )
    conn.commit()
    conn.close()
    return path


def _skip_by_slug(conn):
    rows = conn.execute(
        "SELECT slug, skip_second_pass FROM podcasts WHERE slug LIKE 'legacy-%'"
    ).fetchall()
    return {r['slug']: r['skip_second_pass'] for r in rows}


class TestLegacyValueReset:
    """The column shipped in 0.1.165 and was orphaned in 0.1.242; #599 reuses
    it, so a value left over from that era must not silently disable pass 2."""

    def test_legacy_one_is_cleared_and_reset_runs_once(self, legacy_db_path):
        from database import Database

        Database._instance = None
        try:
            db = Database(data_dir=str(legacy_db_path.parent))
            conn = db.get_connection()
            assert _skip_by_slug(conn) == {'legacy-on': 0, 'legacy-off': 0,
                                           'legacy-null': None}

            # Idempotent: the schema_migrations gate means a later opt-in
            # through the new toggle survives a re-run.
            conn.execute(
                "UPDATE podcasts SET skip_second_pass = 1 WHERE slug = 'legacy-on'")
            conn.commit()
            db._run_schema_migrations()
            assert _skip_by_slug(db.get_connection())['legacy-on'] == 1
        finally:
            Database._instance = None
