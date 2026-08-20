"""Tests for the run-scoped JSONL log recorder (issue #660)."""
import json
import logging
import threading

import pytest

from run_log import RunLogRecorder, TRUNCATION_MARKER


@pytest.fixture
def rec(tmp_path):
    recorder = RunLogRecorder('my-feed', 'ep123', logging.INFO, tmp_path)
    yield recorder
    recorder.discard()


@pytest.fixture
def src_logger():
    log = logging.getLogger('podcast.test.runlog')
    log.setLevel(logging.DEBUG)
    log.propagate = True
    return log


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _temp_file(recorder):
    return recorder.temp_path


class TestKeepRule:
    def test_tagged_record_is_kept(self, rec, src_logger):
        rec.attach()
        src_logger.info('[my-feed:ep123] Starting')
        rec.detach()

        lines = _lines(_temp_file(rec))
        assert len(lines) == 1
        assert lines[0]['msg'] == '[my-feed:ep123] Starting'

    def test_untagged_record_from_unregistered_thread_is_dropped(self, rec, src_logger):
        rec.attach()
        src_logger.info('something unrelated')
        src_logger.info('[other-feed:ep999] not ours')
        rec.detach()

        assert not _temp_file(rec).exists()

    def test_untagged_record_from_registered_thread_is_kept(self, rec, src_logger):
        rec.attach()

        def worker():
            rec.register_thread()
            src_logger.info('worker line without a tag')

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        rec.detach()

        lines = _lines(_temp_file(rec))
        assert [entry['msg'] for entry in lines] == ['worker line without a tag']

    def test_registration_does_not_leak_to_other_threads(self, rec, src_logger):
        rec.attach()

        def worker():
            rec.register_thread()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        src_logger.info('main thread, untagged')
        rec.detach()

        assert not _temp_file(rec).exists()

    def test_level_below_minimum_is_dropped(self, rec, src_logger):
        rec.attach()
        src_logger.debug('[my-feed:ep123] too quiet')
        src_logger.warning('[my-feed:ep123] loud enough')
        rec.detach()

        lines = _lines(_temp_file(rec))
        assert [entry['level'] for entry in lines] == ['WARNING']

    def test_debug_minimum_keeps_debug(self, tmp_path, src_logger):
        recorder = RunLogRecorder('my-feed', 'ep123', logging.DEBUG, tmp_path)
        recorder.attach()
        src_logger.debug('[my-feed:ep123] detail')
        recorder.detach()

        lines = _lines(_temp_file(recorder))
        assert [entry['level'] for entry in lines] == ['DEBUG']
        recorder.discard()


class TestLineShape:
    def test_line_carries_exactly_the_four_keys(self, rec, src_logger):
        rec.attach()
        src_logger.info('[my-feed:ep123] hello %s', 'world')
        rec.detach()

        entry = _lines(_temp_file(rec))[0]
        assert set(entry) == {'ts', 'level', 'logger', 'msg'}
        assert entry['level'] == 'INFO'
        assert entry['logger'] == 'podcast.test.runlog'
        assert entry['msg'] == '[my-feed:ep123] hello world'
        assert entry['ts'].endswith('Z')
        assert entry['ts'][4] == '-' and entry['ts'][10] == 'T'

    def test_exception_text_rides_along_in_msg(self, rec, src_logger):
        rec.attach()
        try:
            raise ValueError('boom')
        except ValueError:
            src_logger.error('[my-feed:ep123] failed', exc_info=True)
        rec.detach()

        entry = _lines(_temp_file(rec))[0]
        assert 'ValueError: boom' in entry['msg']

    def test_tag_property(self, rec):
        assert rec.tag == '[my-feed:ep123]'


class TestSizeCap:
    def test_cap_stops_writes_and_appends_the_marker(self, tmp_path, src_logger):
        recorder = RunLogRecorder('my-feed', 'ep123', logging.INFO, tmp_path,
                                  size_cap_bytes=400)
        recorder.attach()
        for i in range(20):
            src_logger.info('[my-feed:ep123] %s', 'x' * 40)
        recorder.detach()

        lines = _lines(_temp_file(recorder))
        assert lines[-1]['msg'] == TRUNCATION_MARKER
        assert lines[-1]['level'] == 'WARNING'
        assert sum(1 for entry in lines if entry['msg'] == TRUNCATION_MARKER) == 1
        assert len(lines) < 21

        result = recorder.finalize(tmp_path / 'final' / 'run-7.jsonl')
        assert result['truncated'] is True
        assert result['bytes'] == (tmp_path / 'final' / 'run-7.jsonl').stat().st_size


class TestCrashSafety:
    def test_write_failure_disables_the_recorder_without_raising(self, rec, src_logger):
        rec.attach()
        src_logger.info('[my-feed:ep123] first')
        rec._stream.write = _raise_oserror
        src_logger.info('[my-feed:ep123] second')
        rec.detach()

        assert rec.disabled is True
        assert [entry['msg'] for entry in _lines(_temp_file(rec))] == ['[my-feed:ep123] first']

    def test_disabled_recorder_ignores_later_records(self, rec, src_logger):
        rec.attach()
        src_logger.info('[my-feed:ep123] first')
        rec._stream.write = _raise_oserror
        src_logger.info('[my-feed:ep123] second')
        src_logger.info('[my-feed:ep123] third')
        rec.detach()

        assert rec.disabled is True
        assert rec._stream is None
        assert [entry['msg'] for entry in _lines(_temp_file(rec))] == ['[my-feed:ep123] first']

    def test_disabled_recorder_finalizes_to_nothing(self, rec, src_logger, tmp_path):
        rec.attach()
        src_logger.info('[my-feed:ep123] first')
        rec._stream.write = _raise_oserror
        src_logger.info('[my-feed:ep123] second')
        rec.detach()

        assert rec.finalize(tmp_path / 'final' / 'run-1.jsonl') is None
        assert not (tmp_path / 'final' / 'run-1.jsonl').exists()

    def test_unwritable_temp_dir_never_raises(self, tmp_path, src_logger):
        recorder = RunLogRecorder('my-feed', 'ep123', logging.INFO,
                                  tmp_path / 'missing' / 'deeper' / 'nope')
        recorder.attach()
        recorder.temp_path.parent.mkdir(parents=True, exist_ok=True)
        recorder.temp_path.parent.chmod(0o500)
        try:
            src_logger.info('[my-feed:ep123] first')
        finally:
            recorder.temp_path.parent.chmod(0o700)
            recorder.detach()

        assert recorder.disabled is True
        assert recorder.finalize(tmp_path / 'final.jsonl') is None


def _raise_oserror(*args, **kwargs):
    raise OSError('disk gone')


class TestFinalizeAndDiscard:
    def test_finalize_renames_and_reports_bytes(self, rec, src_logger, tmp_path):
        rec.attach()
        src_logger.info('[my-feed:ep123] one')
        src_logger.info('[my-feed:ep123] two')
        rec.detach()
        temp = _temp_file(rec)

        final = tmp_path / 'logs' / 'episodes' / 'my-feed' / 'ep123' / 'run-4.jsonl'
        result = rec.finalize(final)

        assert result == {'bytes': final.stat().st_size, 'truncated': False}
        assert not temp.exists()
        assert len(_lines(final)) == 2

    def test_finalize_without_any_record_returns_none(self, rec, tmp_path):
        rec.attach()
        rec.detach()

        assert rec.finalize(tmp_path / 'final' / 'run-1.jsonl') is None
        assert not (tmp_path / 'final' / 'run-1.jsonl').exists()

    def test_discard_removes_the_temp_file(self, rec, src_logger):
        rec.attach()
        src_logger.info('[my-feed:ep123] one')
        rec.detach()
        temp = _temp_file(rec)
        assert temp.exists()

        rec.discard()

        assert not temp.exists()

    def test_discard_after_finalize_is_a_no_op(self, rec, src_logger, tmp_path):
        rec.attach()
        src_logger.info('[my-feed:ep123] one')
        rec.detach()
        final = tmp_path / 'final' / 'run-2.jsonl'
        rec.finalize(final)

        rec.discard()

        assert final.exists()


class TestAttachDetach:
    def test_attach_and_detach_are_idempotent(self, rec, src_logger):
        root = logging.getLogger()
        rec.attach()
        rec.attach()
        assert root.handlers.count(rec) == 1

        src_logger.info('[my-feed:ep123] once only')
        rec.detach()
        rec.detach()

        assert rec not in root.handlers
        assert len(_lines(_temp_file(rec))) == 1

    def test_detach_stops_capture(self, rec, src_logger):
        rec.attach()
        rec.detach()
        src_logger.info('[my-feed:ep123] after detach')

        assert not _temp_file(rec).exists()


class TestCurrentRecorder:
    def test_current_recorder_tracks_attach_and_detach(self, rec):
        from run_log import current_recorder

        assert current_recorder() is None
        rec.attach()
        assert current_recorder() is rec
        rec.detach()
        assert current_recorder() is None

    def test_current_recorder_is_visible_from_worker_threads(self, rec):
        from run_log import current_recorder

        rec.attach()
        seen = []
        t = threading.Thread(target=lambda: seen.append(current_recorder()))
        t.start()
        t.join()
        rec.detach()

        assert seen == [rec]
