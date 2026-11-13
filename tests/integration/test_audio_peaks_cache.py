"""Peaks served from the cached file, built once under a cross-process lock."""
import fcntl
import os
import subprocess
import tempfile
import threading

import numpy as np
import pytest

import audio_peaks as ap

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='peaks-cache-test-'))

SLUG = 'example-podcast'
EP = 'a1b2c3d4e5f6'


def _gen_wav(path, seconds, freq=440):
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i',
                    f'sine=frequency={freq}:duration={seconds}',
                    '-ac', '1', '-ar', '44100', str(path)], check=True,
                   capture_output=True)


def _src(tmp_path, seconds, name=f'{EP}-original.wav'):
    src = tmp_path / name
    _gen_wav(src, seconds)
    return src


def _write(src, seconds):
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, ap.build_peaks_from_decode(src, seconds), src)
    return p


def test_file_served_matches_fresh_decode_whole_episode(tmp_path):
    src = _src(tmp_path, 5.0)
    p = _write(src, 5.0)
    served, res = ap.compute_peaks_from_file(p, src, 0.0, None, 100)
    decoded, dres = ap.compute_peaks(src, 0.0, None, 100)
    assert res == dres == 100
    assert len(served) == len(decoded)
    assert np.allclose(served, decoded, atol=1e-6)


def test_file_served_matches_fresh_decode_window(tmp_path):
    src = _src(tmp_path, 6.0)
    p = _write(src, 6.0)
    served, res = ap.compute_peaks_from_file(p, src, 2.0, 4.0, 100)
    decoded, dres = ap.compute_peaks(src, 2.0, 4.0, 100)
    assert res == dres
    assert len(served) == len(decoded) == 20
    assert np.allclose(served, decoded, atol=1e-6)


def test_file_served_matches_fresh_decode_at_10ms(tmp_path):
    src = _src(tmp_path, 3.0)
    p = _write(src, 3.0)
    served, _ = ap.compute_peaks_from_file(p, src, 0.5, 2.5, 10)
    decoded, _ = ap.compute_peaks(src, 0.5, 2.5, 10)
    assert len(served) == len(decoded) == 200
    assert np.allclose(served, decoded, atol=1e-6)


def test_off_grid_request_returns_none(tmp_path):
    src = _src(tmp_path, 2.0)
    p = _write(src, 2.0)
    assert ap.compute_peaks_from_file(p, src, 0.005, None, 100) is None
    assert ap.compute_peaks_from_file(p, src, 0.0, None, 55) is None


def test_invalid_window_raises_like_the_decode(tmp_path):
    src = _src(tmp_path, 2.0)
    p = _write(src, 2.0)
    for args in ((0.0, None, 0), (0.0, None, 1001), (1.0, 1.0, 100), (2.0, 1.0, 100)):
        with pytest.raises(ap.PeaksError):
            ap.compute_peaks_from_file(p, src, *args)
    with pytest.raises(ap.PeaksError):
        ap.compute_peaks_from_file(p, src, 50.0, None, 100)


def test_coarsened_width_rounds_up_to_10ms(tmp_path):
    src = _src(tmp_path, 2.0)
    ap.write_peaks_file(ap.peaks_file_path(src), np.ones(1_188_000, dtype='<u2'), src)
    peaks, res = ap.compute_peaks_from_file(ap.peaks_file_path(src), src, 0.0, 11880.0, 100)
    assert res == 200 and len(peaks) == 59_400


def test_full_six_hour_request_at_the_coarsest_width(tmp_path):
    src = _src(tmp_path, 2.0)
    buckets = np.zeros(2_160_000, dtype='<u2')
    buckets[1_000_000] = 16384
    ap.write_peaks_file(ap.peaks_file_path(src), buckets, src)
    peaks, res = ap.compute_peaks_from_file(ap.peaks_file_path(src), src, 0.0, None, 10)
    assert res == 360 and len(peaks) == 60_000
    assert max(peaks) == 0.5


def test_window_read_touches_only_the_window_bytes(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.arange(100_000, dtype='<u2'), src)
    reads = []
    real = ap._open_regular

    class Spy:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            self.f.__enter__()
            return self

        def __exit__(self, *a):
            return self.f.__exit__(*a)

        def read(self, n=-1):
            reads.append(n)
            return self.f.read(n)

        def __getattr__(self, name):
            return getattr(self.f, name)
    monkeypatch.setattr(ap, '_open_regular', lambda path: Spy(real(path)))
    data = ap.read_peaks_window(p, 5000, 5010)
    assert list(data.buckets) == list(range(5000, 5010))
    assert sum(reads) == 64 + 20


def test_stale_file_is_not_served(tmp_path):
    src = _src(tmp_path, 3.0)
    p = _write(src, 3.0)
    _gen_wav(src, 4.0, freq=880)
    assert ap.compute_peaks_from_file(p, src, 0.0, None, 100) is None


def test_zero_bucket_file_is_not_served(tmp_path):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    ap.write_peaks_file(p, np.empty(0, dtype='<u2'), src)
    assert ap.compute_peaks_from_file(p, src, 0.0, None, 100) is None


def test_decode_does_not_seek_at_zero(tmp_path, monkeypatch):
    src = _src(tmp_path, 8.0)
    cmds = []
    real = ap.tracked_run

    def spy(cmd, **kw):
        cmds.append(cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(ap, 'tracked_run', spy)
    ap.compute_peaks(src, 0.0, None, 50)
    ap.compute_peaks(src, 0.5, None, 50)
    ap.compute_peaks(src, 4.99, None, 50)
    ap.compute_peaks(src, 5.5, None, 50)
    assert all('-ss' not in c for c in cmds[:3]) and '-ss' in cmds[3]


def test_first_buckets_of_aac_are_not_flat(tmp_path):
    src = tmp_path / f'{EP}-original.m4a'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
                    '-c:a', 'aac', str(src)], check=True, capture_output=True)
    peaks, _ = ap.compute_peaks(src, 0.0, None, 10)
    assert max(peaks[:5]) > 0.0


def test_build_locked_busy_when_lock_held(tmp_path):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    fd = os.open(ap.peaks_lock_path(p), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_BUSY
        assert not p.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_OK
    assert p.exists()


def test_build_locked_uses_existing_valid_file_without_decoding(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = _write(src, 2.0)
    monkeypatch.setattr(ap, 'build_peaks_from_decode',
                        lambda *a: pytest.fail('decoded despite a valid file'))
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_OK


def test_two_simultaneous_builds_decode_once(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    started, release = threading.Event(), threading.Event()
    decodes = []
    real = ap.build_peaks_from_decode

    def slow(path, duration):
        decodes.append(1)
        started.set()
        release.wait(30)
        return real(path, duration)
    monkeypatch.setattr(ap, 'build_peaks_from_decode', slow)
    results = []
    first = threading.Thread(target=lambda: results.append(ap.build_peaks_file_locked(p, src, 2.0).status))
    first.start()
    assert started.wait(30)
    second = threading.Thread(
        target=lambda: results.append(ap.build_peaks_file_locked(p, src, 2.0, wait_seconds=30).status))
    second.start()
    release.set()
    first.join(30)
    second.join(30)
    assert results == [ap.BUILD_OK, ap.BUILD_OK]
    assert len(decodes) == 1
    assert ap.validate_or_restamp_peaks_file(p, src)


def test_build_discarded_when_source_replaced_during_decode(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    real = ap.build_peaks_from_decode

    def replace_then_return(path, duration):
        buckets = real(path, duration)
        _gen_wav(src, 3.0, freq=880)
        return buckets
    monkeypatch.setattr(ap, 'build_peaks_from_decode', replace_then_return)
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_FAILED
    assert not p.exists() and list(tmp_path.glob('*.tmp')) == []


def test_build_writes_nothing_when_source_deleted_during_decode(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    real = ap.build_peaks_from_decode

    def delete_then_return(path, duration):
        buckets = real(path, duration)
        src.unlink()
        return buckets
    monkeypatch.setattr(ap, 'build_peaks_from_decode', delete_then_return)
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_FAILED
    assert not p.exists()


def test_peaks_written_for_a_source_removed_just_after_are_dropped(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    real = ap.write_peaks_file

    def write_then_delete(path, buckets, source, **kw):
        real(path, buckets, source, **kw)
        source.unlink()
    monkeypatch.setattr(ap, 'write_peaks_file', write_then_delete)
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_FAILED
    assert not p.exists()


def test_build_never_raises_when_the_directory_is_gone(tmp_path):
    gone = tmp_path / 'gone'
    gone.mkdir()
    src = gone / f'{EP}-original.wav'
    _gen_wav(src, 1.0)
    p = ap.peaks_file_path(src)
    src.unlink()
    gone.rmdir()
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_FAILED


def test_build_of_undecodable_audio_fails_quietly(tmp_path):
    src = tmp_path / f'{EP}-original.mp3'
    src.write_bytes(b'not audio')
    assert ap.build_peaks_file_locked(ap.peaks_file_path(src), src, 1.0).status == ap.BUILD_FAILED


def test_background_build_is_single_flight_and_clears_when_done(tmp_path):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    hold, entered = threading.Event(), threading.Event()
    calls = []

    def runner(peaks_path, source_path, duration):
        calls.append((peaks_path, source_path, duration))
        entered.set()
        hold.wait(30)
        return ap.BUILD_OK
    t1 = ap.start_background_build(p, src, 9.0, runner=runner)
    assert entered.wait(30)
    assert ap.start_background_build(p, src, 9.0, runner=runner) is None
    hold.set()
    t1.join(30)
    assert t1.daemon and calls == [(p, src, 9.0)]
    t3 = ap.start_background_build(p, src, 9.0, runner=lambda *a: ap.BUILD_OK)
    t3.join(30)
    assert t3 is not None


def test_background_build_swallows_runner_errors(tmp_path):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)

    def boom(*a):
        raise RuntimeError('boom')
    t = ap.start_background_build(p, src, 1.0, runner=boom)
    t.join(30)
    assert not t.is_alive()
    assert ap.build_failed_recently(p, src) is True
    assert str(p) not in ap._inflight


def test_failure_memo_clears_when_the_source_changes(tmp_path, monkeypatch):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: None)
    t = ap.start_background_build(p, src, 1.0)
    t.join(30)
    assert ap.build_failed_recently(p, src) is True
    _gen_wav(src, 2.0)
    assert ap.build_failed_recently(p, src) is False


def test_failure_is_not_remembered_for_a_source_replaced_during_the_build(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    real = ap.build_peaks_from_decode

    def replace_then_return(path, duration):
        buckets = real(path, duration)
        _gen_wav(src, 3.0, freq=880)
        return buckets
    monkeypatch.setattr(ap, 'build_peaks_from_decode', replace_then_return)
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_FAILED
    assert ap.build_failed_recently(p, src) is False


def test_busy_background_build_keeps_an_existing_failure_memo(tmp_path, monkeypatch):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: None)
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_FAILED
    assert ap.build_failed_recently(p, src) is True
    ap.start_background_build(p, src, 1.0, runner=lambda *a: ap.BuildResult(ap.BUILD_BUSY)).join(30)
    assert ap.build_failed_recently(p, src) is True
    fd = os.open(ap.peaks_lock_path(p), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ap.start_background_build(p, src, 1.0).join(30)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert ap.build_failed_recently(p, src) is True


def test_maybe_write_ignores_an_empty_array(tmp_path):
    src = _src(tmp_path, 1.0)
    assert ap.maybe_write_peaks_file(src, np.empty(0, dtype='<u2')) is False
    assert not ap.peaks_file_path(src).exists()


# ---- route ----

class _FakeDB:
    def __init__(self, episode):
        self.episode = episode

    def get_episode(self, slug, episode_id):
        return self.episode

    def upsert_episode(self, *a, **k):
        pass


class _FakeStorage:
    def __init__(self, path):
        self.path = path

    def get_original_path(self, slug, episode_id, extension='.mp3'):
        return self.path


@pytest.fixture
def route(app_client, tmp_path, monkeypatch):
    src = _src(tmp_path, 4.0)
    episode = {'episode_id': EP, 'original_file': str(src), 'original_duration': 4.0}
    monkeypatch.setattr('api.episodes.media.get_database', lambda: _FakeDB(episode))
    monkeypatch.setattr('api.episodes.media.get_storage', lambda: _FakeStorage(src))

    def get(query='start=0&resolution_ms=100', client=None):
        return (client or app_client).get(f'/api/v1/feeds/{SLUG}/episodes/{EP}/peaks?{query}')
    get.new_client = app_client.application.test_client
    get.src = src
    get.episode = episode
    return get


def test_route_builds_on_first_open_then_serves_from_the_file(route, monkeypatch):
    p = ap.peaks_file_path(route.src)
    r1 = route()
    assert r1.status_code == 200 and p.exists()
    body = r1.get_json()
    assert body['resolutionMs'] == 100 and len(body['peaks']) == 40
    monkeypatch.setattr(ap, 'compute_peaks', lambda *a, **k: pytest.fail('decoded'))
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: pytest.fail('rebuilt'))
    assert route().get_json() == body


def test_route_response_matches_a_fresh_decode(route):
    body = route('start=1&end=3&resolution_ms=50').get_json()
    decoded, res = ap.compute_peaks(route.src, 1.0, 3.0, 50)
    assert body['resolutionMs'] == res == 50
    assert body['start'] == 1.0 and body['end'] == 3.0
    assert np.allclose(body['peaks'], decoded, atol=1e-6)


def test_route_reports_the_rounded_resolution(route, monkeypatch):
    monkeypatch.setattr(ap, 'MAX_PEAK_BUCKETS', 7)
    body = route('start=0&resolution_ms=10').get_json()
    assert body['resolutionMs'] % 10 == 0 and body['resolutionMs'] > 10
    assert len(body['peaks']) <= 7


def test_route_off_grid_request_decodes_and_builds_nothing(route):
    r = route('start=0.005&resolution_ms=100')
    assert r.status_code == 200
    assert not ap.peaks_file_path(route.src).exists()
    r = route('start=0&resolution_ms=55')
    assert r.status_code == 200 and r.get_json()['resolutionMs'] == 55


def test_route_rejects_bad_windows_with_400(route):
    assert route('start=3&end=3').status_code == 400
    assert route('resolution_ms=0').status_code == 400
    assert route('resolution_ms=5000').status_code == 400
    assert route('start=60').status_code == 400
    assert route('start=abc').status_code == 400


def test_route_long_episode_answers_503_then_200(route, monkeypatch):
    monkeypatch.setattr(ap, 'PEAKS_INLINE_MAX_SECONDS', 1.0)
    r1 = route()
    assert r1.status_code == 503
    assert r1.headers['Retry-After'] == '5'
    assert r1.get_json() == {'error': 'Waveform is being prepared', 'status': 503}
    for t in threading.enumerate():
        if t.name.startswith('peaks-build'):
            t.join(60)
    assert ap.peaks_file_path(route.src).exists()
    r2 = route()
    assert r2.status_code == 200 and len(r2.get_json()['peaks']) == 40


def test_route_long_episode_does_not_decode_in_the_request(route, monkeypatch):
    monkeypatch.setattr(ap, 'PEAKS_INLINE_MAX_SECONDS', 1.0)
    started = []
    monkeypatch.setattr(ap, 'start_background_build',
                        lambda *a, **k: started.append(a) or None)
    monkeypatch.setattr(ap, 'compute_peaks', lambda *a, **k: pytest.fail('decoded'))
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: pytest.fail('decoded'))
    assert route().status_code == 503
    assert len(started) == 1


def test_route_long_episode_with_failed_build_falls_back_to_the_decode(route, monkeypatch):
    monkeypatch.setattr(ap, 'PEAKS_INLINE_MAX_SECONDS', 1.0)
    monkeypatch.setattr(ap, 'build_failed_recently', lambda *a: True)
    assert route().status_code == 200


def test_route_503_when_another_process_holds_the_build_lock(route, monkeypatch):
    monkeypatch.setattr(ap, 'PEAKS_BUILD_WAIT_SECONDS', 0.0)
    p = ap.peaks_file_path(route.src)
    fd = os.open(ap.peaks_lock_path(p), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        r = route()
        assert r.status_code == 503 and r.headers['Retry-After'] == '5'
        assert not p.exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert route().status_code == 200


def test_route_rebuilds_a_stale_file(route):
    assert route().status_code == 200
    _gen_wav(route.src, 6.0, freq=880)
    body = route('start=0&end=6&resolution_ms=100').get_json()
    assert len(body['peaks']) == 60
    assert ap.validate_or_restamp_peaks_file(ap.peaks_file_path(route.src), route.src)


def test_route_rebuilds_a_corrupt_file(route):
    p = ap.peaks_file_path(route.src)
    p.write_bytes(b'garbage')
    assert route().status_code == 200
    assert ap.read_peaks_window(p) is not None


def test_route_survives_an_unwritable_peaks_file(route, monkeypatch):
    def refuse(*a, **k):
        raise OSError('read-only')
    monkeypatch.setattr(ap, '_write_atomic', refuse)
    r = route()
    assert r.status_code == 200 and len(r.get_json()['peaks']) == 40


# Full-decode slice helpers

def _full_decode_slice(src, start, count):
    full, _ = ap.compute_peaks(src, 0.0, None, 10)
    i = int(round(start * 100))
    return np.array(full[i:i + count])


@pytest.mark.parametrize('codec,ext', [('libmp3lame', 'mp3'), ('aac', 'm4a'), ('pcm_s16le', 'wav')])
@pytest.mark.parametrize('start', [12.34, 30.0])
def test_decode_window_above_zero_matches_full_decode_exactly(tmp_path, codec, ext, start):
    src = tmp_path / f'{EP}-original.{ext}'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=40',
                    '-ac', '1', '-c:a', codec, str(src)], check=True, capture_output=True)
    window, _ = ap.compute_peaks(src, start, start + 3, 10)
    assert len(window) == 300
    # measured: MP3 and WAV exact, AAC differs by one 16-bit step in one bucket
    assert np.allclose(window, _full_decode_slice(src, start, 300), rtol=0, atol=1 / 32768 + 1e-9)
    assert max(window[:7]) > 0.0


_NEAR_ZERO_CASES = (
    [(c, e, st) for c, e in (('libmp3lame', 'mp3'), ('aac', 'm4a'), ('pcm_s16le', 'wav'))
     for st in (0.5, 0.99, 1.0, 1.01, 1.99)]
    + [(c, e, st) for c, e in (('libmp3lame', 'mp3'), ('pcm_s16le', 'wav')) for st in (2.0, 2.01)])


@pytest.mark.parametrize('codec,ext,start', _NEAR_ZERO_CASES)
def test_decode_window_near_zero_equals_the_from_zero_decode(tmp_path, codec, ext, start):
    src = tmp_path / f'{EP}-original.{ext}'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anoisesrc=d=8:c=pink:r=44100:a=0.5:seed=1',
                    '-ac', '1', '-c:a', codec, str(src)], check=True, capture_output=True)
    window, _ = ap.compute_peaks(src, start, start + 3, 10)
    assert np.array_equal(np.array(window), _full_decode_slice(src, start, 300))


def test_decode_window_near_start_uses_the_shorter_lead_in(tmp_path):
    src = _src(tmp_path, 3.0)
    window, _ = ap.compute_peaks(src, 0.25, 1.25, 10)
    assert np.array_equal(np.array(window), _full_decode_slice(src, 0.25, 100))


def _undecodable(tmp_path):
    src = tmp_path / f'{EP}-original.mp3'
    src.write_bytes(b'not audio')
    return src


def test_failure_memory_expires_after_300_seconds(tmp_path, monkeypatch):
    src = _undecodable(tmp_path)
    p = ap.peaks_file_path(src)
    now = [1000.0]
    monkeypatch.setattr(ap, '_clock', lambda: now[0])
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_FAILED
    now[0] += 299
    assert ap.build_failed_recently(p, src) is True
    now[0] += 2
    assert ap.build_failed_recently(p, src) is False


def test_inline_failure_is_remembered_and_cleared_by_a_success(tmp_path, monkeypatch):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    monkeypatch.setattr(ap, '_write_atomic', lambda *a, **k: (_ for _ in ()).throw(OSError('ro')))
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_FAILED
    assert ap.build_failed_recently(p, src) is True
    monkeypatch.undo()
    assert ap.build_peaks_file_locked(p, src, 1.0).status == ap.BUILD_OK
    assert ap.build_failed_recently(p, src) is False


def test_unwritable_build_hands_back_the_decoded_buckets(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    monkeypatch.setattr(ap, '_write_atomic', lambda *a, **k: (_ for _ in ()).throw(OSError('ro')))
    result = ap.build_peaks_file_locked(ap.peaks_file_path(src), src, 2.0)
    assert result.status == ap.BUILD_FAILED and len(result.buckets) == 200


def test_lock_wait_ends_busy_after_the_deadline_without_sleeping(tmp_path, monkeypatch):
    src = _src(tmp_path, 1.0)
    p = ap.peaks_file_path(src)
    ticks = iter(range(0, 1000, 10))
    pauses = []
    monkeypatch.setattr(ap, '_clock', lambda: float(next(ticks)))
    monkeypatch.setattr(ap, '_pause', pauses.append)
    fd = os.open(ap.peaks_lock_path(p), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert ap.build_peaks_file_locked(p, src, 1.0, wait_seconds=25).status == ap.BUILD_BUSY
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert len(pauses) >= 2


class _HeldBuild:
    def __init__(self):
        self.started, self.blocked, self.release = (threading.Event() for _ in range(3))
        self.decodes = []
        self.threads = []

    def spawn(self, fn):
        t = threading.Thread(target=fn)
        self.threads.append(t)
        t.start()
        return t


@pytest.fixture
def held_build(monkeypatch):
    """Block the first decode until released; a waiter's _pause signals it is blocked."""
    held = _HeldBuild()
    real_decode = ap.build_peaks_from_decode

    def slow(path, duration):
        held.decodes.append(1)
        held.started.set()
        held.release.wait(30)
        return real_decode(path, duration)

    def pause(seconds):
        held.blocked.set()
        held.release.wait(30)
    monkeypatch.setattr(ap, 'build_peaks_from_decode', slow)
    monkeypatch.setattr(ap, '_pause', pause)
    try:
        yield held
    finally:
        held.release.set()
        for t in held.threads:
            t.join(30)


def test_second_build_waits_for_the_first_and_decodes_nothing(tmp_path, held_build):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    results = []
    t1 = held_build.spawn(lambda: results.append(ap.build_peaks_file_locked(p, src, 2.0).status))
    assert held_build.started.wait(30)
    t2 = held_build.spawn(
        lambda: results.append(ap.build_peaks_file_locked(p, src, 2.0, wait_seconds=60).status))
    assert held_build.blocked.wait(30)
    assert not p.exists()
    held_build.release.set()
    t1.join(30)
    t2.join(30)
    assert results == [ap.BUILD_OK, ap.BUILD_OK] and len(held_build.decodes) == 1


def test_route_two_overlapping_requests_build_once(route, held_build):
    codes = []
    t1 = held_build.spawn(lambda: codes.append(route(client=route.new_client()).status_code))
    assert held_build.started.wait(30)
    t2 = held_build.spawn(lambda: codes.append(route(client=route.new_client()).status_code))
    assert held_build.blocked.wait(30)
    held_build.release.set()
    t1.join(30)
    t2.join(30)
    assert codes == [200, 200] and len(held_build.decodes) == 1


def test_route_unwritable_directory_decodes_once_then_remembers(route, monkeypatch):
    monkeypatch.setattr(ap, '_write_atomic', lambda *a, **k: (_ for _ in ()).throw(OSError('ro')))
    builds, windows = [], []
    real_build, real_compute = ap.build_peaks_from_decode, ap.compute_peaks
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: builds.append(1) or real_build(*a))
    monkeypatch.setattr(ap, 'compute_peaks', lambda *a, **k: windows.append(1) or real_compute(*a, **k))
    first = route()
    assert first.status_code == 200 and len(first.get_json()['peaks']) == 40
    assert (len(builds), len(windows)) == (1, 0)
    second = route()
    assert second.status_code == 200 and second.get_json() == first.get_json()
    assert (len(builds), len(windows)) == (1, 1)


def test_route_undecodable_original_decodes_once_per_request_only(route, monkeypatch):
    route.src.write_bytes(b'not audio')
    builds = []
    real_build = ap.build_peaks_from_decode
    monkeypatch.setattr(ap, 'build_peaks_from_decode', lambda *a: builds.append(1) or real_build(*a))
    assert route().status_code == 400
    assert route().status_code == 400
    assert len(builds) == 1


def test_probe_failure_is_cached_briefly(route, monkeypatch):
    from api.episodes import media
    route.episode['original_duration'] = None
    probes = []
    now = [0.0]
    monkeypatch.setattr(media, 'get_audio_duration', lambda p: probes.append(p) or None)
    monkeypatch.setattr(media, '_clock', lambda: now[0])
    media._probe_failures.clear()
    for _ in range(3):
        assert route().status_code == 200
    assert len(probes) == 1
    now[0] += media.PEAKS_PROBE_RETRY_SECONDS + 1
    ap.peaks_file_path(route.src).unlink()
    route()
    assert len(probes) == 2


def test_probe_failure_memo_is_dropped_when_the_original_is_replaced(route, monkeypatch):
    from api.episodes import media
    route.episode['original_duration'] = None
    probes = []
    monkeypatch.setattr(media, 'get_audio_duration', lambda p: probes.append(p) or None)
    monkeypatch.setattr(media, '_clock', lambda: 0.0)
    media._probe_failures.clear()
    assert route().status_code == 200
    ap.peaks_file_path(route.src).unlink()
    _gen_wav(route.src, 5.0, freq=880)
    assert route().status_code == 200
    assert len(probes) == 2


def test_background_builds_are_capped_and_a_slot_frees(tmp_path):
    srcs = [_src(tmp_path, 1.0, name=f'b{i}-original.wav') for i in range(3)]
    hold, entered = threading.Event(), threading.Semaphore(0)

    def runner(*a):
        entered.release()
        hold.wait(30)
        return ap.BUILD_OK
    threads = [ap.start_background_build(ap.peaks_file_path(s), s, 9.0, runner=runner)
               for s in srcs[:2]]
    assert all(threads)
    entered.acquire(timeout=30)
    entered.acquire(timeout=30)
    third = srcs[2]
    assert ap.start_background_build(ap.peaks_file_path(third), third, 9.0, runner=runner) is None
    hold.set()
    for t in threads:
        t.join(30)
    t3 = ap.start_background_build(ap.peaks_file_path(third), third, 9.0, runner=runner)
    assert t3 is not None
    t3.join(30)


def test_the_inline_cap_is_45_minutes():
    assert ap.PEAKS_INLINE_MAX_SECONDS == 45 * 60


def test_route_builds_inline_at_44_minutes(route, monkeypatch):
    route.episode['original_duration'] = 44 * 60
    started, built = [], []
    monkeypatch.setattr(ap, 'start_background_build', lambda *a, **k: started.append(a) or None)
    real = ap.build_peaks_file_locked
    monkeypatch.setattr(ap, 'build_peaks_file_locked', lambda *a, **k: built.append(a) or real(*a, **k))
    assert route().status_code == 200
    assert len(built) == 1 and not started


def test_route_starts_a_background_build_at_46_minutes(route, monkeypatch):
    route.episode['original_duration'] = 46 * 60
    started = []
    monkeypatch.setattr(ap, 'start_background_build', lambda *a, **k: started.append(a) or None)
    monkeypatch.setattr(ap, 'build_peaks_file_locked', lambda *a, **k: pytest.fail('decoded inline'))
    monkeypatch.setattr(ap, 'compute_peaks', lambda *a, **k: pytest.fail('decoded inline'))
    r = route()
    assert r.status_code == 503 and r.headers['Retry-After'] == '5'
    assert len(started) == 1


def test_a_stored_duration_means_no_probe_in_the_request(route, monkeypatch):
    monkeypatch.setattr('api.episodes.media.get_audio_duration', lambda *_: pytest.fail('probed'))
    assert route().status_code == 200


def test_an_old_row_without_a_duration_is_probed_once(route, monkeypatch):
    route.episode['original_duration'] = None
    probes = []
    monkeypatch.setattr('api.episodes.media.get_audio_duration', lambda path: probes.append(path) or 4.0)
    assert route().status_code == 200
    assert len(probes) == 1


def test_a_replaced_original_is_rebuilt_by_the_stamp_alone(route):
    assert route().status_code == 200
    p = ap.peaks_file_path(route.src)
    before = p.read_bytes()
    _gen_wav(route.src, 6.0, freq=880)
    body = route('start=0&end=6&resolution_ms=100').get_json()
    assert len(body['peaks']) == 60 and p.read_bytes() != before


def test_a_build_of_a_removed_original_leaves_no_peaks_file(tmp_path, monkeypatch):
    src = _src(tmp_path, 2.0)
    p = ap.peaks_file_path(src)
    real = ap.write_peaks_file

    def write_then_remove(path, buckets, source, **kwargs):
        real(path, buckets, source, **kwargs)
        assert path.exists()
        source.unlink()
    monkeypatch.setattr(ap, 'write_peaks_file', write_then_remove)
    assert ap.build_peaks_file_locked(p, src, 2.0).status == ap.BUILD_FAILED
    assert not p.exists()
