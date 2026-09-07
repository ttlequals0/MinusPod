"""Transcriber uses the Whisper pool for admission and sizing."""
from unittest.mock import MagicMock, patch

import pytest

import transcriber
from whisper_pool import WhisperPool


def _settings(enabled=True, max_requests=2):
    state = {'enabled': enabled, 'backend': 'openai-api',
             'max_requests': max_requests, 'max_episodes': 1}
    return lambda: dict(state)


@pytest.fixture
def pool(monkeypatch):
    p = WhisperPool(_settings())
    monkeypatch.setattr(transcriber, 'get_pool', lambda: p)
    return p


def _whisper_settings():
    return {'backend': 'openai-api', 'api_base_url': 'https://whisper.example.com/v1',
            'api_key': '', 'api_model': 'whisper-1', 'language': 'en',
            'skip_flac_compression': True, 'api_timeout': 30}


def test_api_call_runs_inside_a_pool_slot(pool, tmp_path):
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'0' * 4096)
    seen = {}
    def fake_post(*a, **k):
        seen['in_flight'] = pool.snapshot()['inFlight']
        r = MagicMock(); r.status_code = 200
        r.json.return_value = {'segments': [{'start': 0, 'end': 1, 'text': 'hi'}]}
        return r
    with patch('transcriber.safe_post', side_effect=fake_post):
        segs = transcriber.Transcriber()._transcribe_via_api(str(audio), _whisper_settings(), preprocessed=True)
    assert segs and seen['in_flight'] == 1
    assert pool.snapshot()['inFlight'] == 0


def test_429_waits_and_retries_while_active(pool, tmp_path, monkeypatch):
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'0' * 4096)
    sleeps = []
    monkeypatch.setattr(transcriber.time, 'sleep', lambda s: sleeps.append(s))
    responses = []
    def fake_post(*a, **k):
        r = MagicMock()
        if not responses:
            r.status_code = 429; r.headers = {'Retry-After': '2'}; r.text = 'busy'
        else:
            r.status_code = 200; r.headers = {}
            r.json.return_value = {'segments': [{'start': 0, 'end': 1, 'text': 'hi'}]}
        responses.append(r)
        return r
    with patch('transcriber.safe_post', side_effect=fake_post):
        segs = transcriber.Transcriber()._transcribe_via_api(str(audio), _whisper_settings(), preprocessed=True)
    assert segs and len(responses) == 2 and sleeps == [2.0]


def test_429_deadline_bounds_a_zero_retry_after_loop(pool, tmp_path, monkeypatch):
    """Retry-After: 0 must not spin forever; the wall-clock deadline wins."""
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'0' * 4096)
    monkeypatch.setattr(transcriber.time, 'sleep', lambda s: None)
    clock = {'t': 0.0}
    def fake_monotonic():
        clock['t'] += 1.0
        return clock['t']
    monkeypatch.setattr(transcriber.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(transcriber, '_api_timeout', lambda settings: 2.0)
    calls = []
    def fake_post(*a, **k):
        calls.append(1)
        r = MagicMock(); r.status_code = 429; r.headers = {'Retry-After': '0'}; r.text = 'busy'
        return r
    with patch('transcriber.safe_post', side_effect=fake_post):
        result = transcriber.Transcriber()._transcribe_via_api(str(audio), _whisper_settings(), preprocessed=True)
    assert result is None
    assert len(calls) < 10


def test_429_is_a_plain_failure_while_inactive(tmp_path, monkeypatch):
    p = WhisperPool(_settings(enabled=False))
    monkeypatch.setattr(transcriber, 'get_pool', lambda: p)
    audio = tmp_path / 'a.wav'; audio.write_bytes(b'0' * 4096)
    r = MagicMock(); r.status_code = 429; r.headers = {}; r.text = 'busy'
    with patch('transcriber.safe_post', return_value=r):
        assert transcriber.Transcriber()._transcribe_via_api(str(audio), _whisper_settings(), preprocessed=True) is None


def test_chunk_pool_size_comes_from_the_pool(pool, monkeypatch):
    sizes = []
    real = transcriber.ThreadPoolExecutor
    class Spy(real):
        def __init__(self, max_workers=None, **kw):
            sizes.append(max_workers); super().__init__(max_workers=max_workers, **kw)
    monkeypatch.setattr(transcriber, 'ThreadPoolExecutor', Spy)
    monkeypatch.setattr(transcriber, '_get_chunk_settings',
                        lambda: {'max_chunk_seconds': 10, 'concurrent_chunks': 8, 'chunk_overlap_seconds': 1})
    # Truthy path (not a real file) so chunks fail in _transcribe_via_api's
    # size check rather than tripping the unrelated extraction-failure abort.
    monkeypatch.setattr(transcriber, 'extract_audio_chunk', lambda *a, **k: 'chunk.flac')
    t = transcriber.Transcriber()
    with pool.transcribing():
        t._transcribe_chunked_parallel_api('x.wav', 25.0, _whisper_settings())
    assert sizes == [2]  # capacity 2, one transcribing episode, configured 8
