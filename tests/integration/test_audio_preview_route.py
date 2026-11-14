"""GET /patterns/{id}/audio/preview streams the source span as mp3."""
import os
import subprocess
import tempfile

import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match


def _url(pid, query=''):
    return f'/api/v1/patterns/{pid}/audio/preview{query}'


@pytest.fixture
def isolated_tmp(tmp_path, monkeypatch):
    """Temp files made by the route land in a directory the test can list."""
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(scratch))
    return scratch


def test_preview_streams_mp3(app_client, fp_audio_pattern, fp_audio_original, isolated_tmp):
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 200 and r.mimetype == 'audio/mpeg'
    assert r.headers['Accept-Ranges'] == 'bytes'
    assert r.data[:3] in (b'ID3', b'\xff\xfb', b'\xff\xf3') or r.data[0] == 0xFF
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', '-'],
        input=r.data, capture_output=True, check=True)
    assert 5.5 < float(probe.stdout) < 6.6
    assert os.listdir(isolated_tmp) == []


def test_preview_honors_range_requests(app_client, fp_audio_pattern, fp_audio_original):
    headers = {**csrf(app_client), 'Range': 'bytes=0-99'}
    r = app_client.get(_url(fp_audio_pattern), headers=headers)
    assert r.status_code == 206 and len(r.data) == 100
    assert r.headers['Content-Range'].startswith('bytes 0-99/')


def test_preview_of_a_given_match(app_client, fp_feed, fp_audio_pattern, fp_audio_original):
    match = record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 1.0, 4.0, 0.9, 0.9, True, False)
    r = app_client.get(_url(fp_audio_pattern, f'?matchId={match}'), headers=csrf(app_client))
    assert r.status_code == 200
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', '-'],
        input=r.data, capture_output=True, check=True)
    assert 2.5 < float(probe.stdout) < 3.6


def test_match_of_another_pattern_is_404(app_client, fp_feed, fp_audio_pattern, fp_audio_original):
    from tests.integration.fingerprint_helpers import TEMPLATE
    other = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE + ' other')
    foreign = record_fingerprint_match(fp_feed, other, EPISODE, SLUG, 1.0, 4.0, 0.9, 0.9, True, False)
    r = app_client.get(_url(fp_audio_pattern, f'?matchId={foreign}'), headers=csrf(app_client))
    assert r.status_code == 404
    assert app_client.get(_url(fp_audio_pattern, '?matchId=99999'), headers=csrf(app_client)).status_code == 404


@pytest.mark.parametrize('bad', ['abc', '1.5', '-1', '', '1e3', '%EF%BC%91'])
def test_match_id_must_be_an_integer(app_client, fp_audio_pattern, fp_audio_original, bad):
    r = app_client.get(_url(fp_audio_pattern, f'?matchId={bad}'), headers=csrf(app_client))
    assert r.status_code == 400


def test_pattern_id_must_be_an_integer(app_client):
    assert app_client.get('/api/v1/patterns/abc/audio/preview', headers=csrf(app_client)).status_code == 404


def test_unknown_pattern_is_404(app_client, fp_feed):
    r = app_client.get(_url(987654), headers=csrf(app_client))
    assert r.status_code == 404


def test_pattern_without_audio_is_404_no_audio(app_client, fp_pattern):
    r = app_client.get(_url(fp_pattern), headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json()['code'] == 'no_audio'


def test_preview_404_when_original_not_retained(app_client, fp_audio_pattern):
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json()['code'] == 'original_not_retained'


def test_slice_is_capped_at_600_seconds(app_client, fp_feed, fp_audio_pattern, fp_audio_original, monkeypatch):
    import api.patterns_audio as module
    fp_feed.create_audio_fingerprint(fp_audio_pattern, b'AQAA', 2000.0, source_podcast_slug=SLUG,
                                     source_episode_id=EPISODE, source_start=1.0, source_end=2001.0)
    seen = []
    real = module.tracked_run

    def spy(cmd, **kw):
        seen.append(cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(module, 'tracked_run', spy)
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 200
    cmd = seen[0]
    assert cmd[cmd.index('-t') + 1] == '600.000'
    assert cmd[1:4] == ['-nostdin', '-protocol_whitelist', 'file,pipe']


def test_ffmpeg_failure_is_a_500_without_stderr_and_leaves_no_temp(
        app_client, fp_audio_pattern, fp_audio_original, isolated_tmp, monkeypatch):
    import api.patterns_audio as module

    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b'secret /srv/private/path.mp3: broken')
    monkeypatch.setattr(module, 'tracked_run', boom)
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    body = r.get_json()
    assert r.status_code == 500 and body['status'] == 500
    assert 'secret' not in r.get_data(as_text=True) and '/srv/private' not in r.get_data(as_text=True)
    assert os.listdir(isolated_tmp) == []


def test_ffmpeg_timeout_is_a_500(app_client, fp_audio_pattern, fp_audio_original, isolated_tmp, monkeypatch):
    import api.patterns_audio as module

    def slow(cmd, **kw):
        assert kw['timeout'] > 0
        raise subprocess.TimeoutExpired(cmd, kw['timeout'])
    monkeypatch.setattr(module, 'tracked_run', slow)
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 500 and os.listdir(isolated_tmp) == []


def test_empty_ffmpeg_output_is_a_500(app_client, fp_audio_pattern, fp_audio_original, isolated_tmp, monkeypatch):
    import api.patterns_audio as module
    monkeypatch.setattr(module, 'tracked_run', lambda cmd, **kw: None)
    r = app_client.get(_url(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 500 and os.listdir(isolated_tmp) == []
