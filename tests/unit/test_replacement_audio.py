"""Uploading a replacement beep has to persist, take effect without a restart,
and never install a file that would wreck every cut."""
import os
import subprocess
import sys
from pathlib import Path
import tempfile

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='replacement_audio_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['MINUSPOD_DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import replacement_audio as ra  # noqa: E402
from audio_processor import (  # noqa: E402
    get_replace_audio_path,
    get_replacement_duration,
    get_uploaded_replace_audio_path,
)


def _tone(path, seconds=1.0, channels=2, freq=440, bitrate=None):
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
        '-i', f'sine=frequency={freq}:duration={seconds}:sample_rate=44100',
        '-ac', str(channels),
    ]
    if bitrate:
        cmd += ['-b:a', bitrate]
    subprocess.run(cmd + [path], check=True)
    return open(path, 'rb').read()


@pytest.fixture(autouse=True)
def _clean_upload():
    yield
    target = get_uploaded_replace_audio_path()
    if target.exists():
        target.unlink()


@pytest.fixture
def tmp_tone(tmp_path):
    def make(**kw):
        return _tone(str(tmp_path / 'tone.wav'), **kw)
    return make


class TestResolutionOrder:
    def test_default_is_reported_when_nothing_uploaded(self):
        info = ra.describe()

        assert info['source'] == ra.SOURCE_DEFAULT
        assert info['canRevert'] is False

    def test_upload_wins_over_the_default(self, tmp_tone):
        ra.save_upload(tmp_tone(seconds=2.0))

        assert get_replace_audio_path() == str(get_uploaded_replace_audio_path())
        assert ra.describe()['source'] == ra.SOURCE_UPLOADED

    def test_upload_lands_on_the_data_volume(self, tmp_tone):
        """assets/ is baked into the image, so an upload there dies on redeploy."""
        ra.save_upload(tmp_tone())

        assert get_uploaded_replace_audio_path().parent == Path(_test_data_dir)

    def test_revert_restores_the_default(self, tmp_tone):
        ra.save_upload(tmp_tone())

        assert ra.revert() is True
        assert ra.describe()['source'] == ra.SOURCE_DEFAULT

    def test_revert_is_a_no_op_when_nothing_was_uploaded(self):
        assert ra.revert() is False


class TestTakesEffectWithoutRestart:
    def test_pipeline_duration_follows_the_upload(self, tmp_tone):
        """get_replacement_duration used to freeze the path at import."""
        before = get_replacement_duration()
        ra.save_upload(tmp_tone(seconds=3.0))
        after = get_replacement_duration()

        assert after == pytest.approx(3.0, abs=0.1)
        assert after != pytest.approx(before, abs=0.1)

    def test_a_default_constructed_processor_picks_up_the_upload(self, tmp_tone):
        from audio_processor import AudioProcessor
        ra.save_upload(tmp_tone(seconds=2.0))

        assert AudioProcessor().replace_audio_path == str(get_uploaded_replace_audio_path())

    def test_render_path_and_timestamp_math_agree(self, tmp_tone):
        """A mismatch here shifts every chapter and cue in the episode."""
        from audio_processor import AudioProcessor
        ra.save_upload(tmp_tone(seconds=2.5))

        assert AudioProcessor().get_beep_duration() == pytest.approx(get_replacement_duration())


class TestRejections:
    def test_a_non_audio_file_is_rejected(self):
        with pytest.raises(ra.ReplacementAudioError, match='could not be read as audio'):
            ra.save_upload(b'PK\x03\x04 definitely not audio')

    def test_an_empty_file_is_rejected(self):
        with pytest.raises(ra.ReplacementAudioError, match='empty'):
            ra.save_upload(b'')

    def test_an_oversized_file_is_rejected(self):
        with pytest.raises(ra.ReplacementAudioError, match='The limit is 5 MB'):
            ra.save_upload(b'\x00' * (ra.MAX_UPLOAD_BYTES + 1))

    def test_a_long_file_is_rejected_because_every_cut_becomes_this_long(self, tmp_tone):
        raw = tmp_tone(seconds=45.0, channels=1, bitrate='32k')

        with pytest.raises(ra.ReplacementAudioError, match='every cut becomes this long'):
            ra.save_upload(raw)

    def test_a_rejected_upload_leaves_the_previous_file_in_place(self, tmp_tone):
        ra.save_upload(tmp_tone(seconds=2.0))
        with pytest.raises(ra.ReplacementAudioError):
            ra.save_upload(b'not audio')

        assert ra.describe()['durationSeconds'] == pytest.approx(2.0, abs=0.1)

    def test_no_temp_files_are_left_behind_after_a_rejection(self):
        before = set(os.listdir(_test_data_dir))
        with pytest.raises(ra.ReplacementAudioError):
            ra.save_upload(b'not audio')

        assert set(os.listdir(_test_data_dir)) == before


class TestMetadata:
    def test_channels_and_rate_are_reported(self, tmp_tone):
        info = ra.save_upload(tmp_tone(seconds=1.0, channels=1))

        assert info['channels'] == 1
        assert info['sampleRateHz'] == 44100
        assert info['sizeBytes'] > 0

    def test_a_wav_upload_is_stored_as_mp3(self, tmp_tone):
        """The render path is handed a file whose name claims MP3."""
        ra.save_upload(tmp_tone())
        head = get_uploaded_replace_audio_path().read_bytes()[:3]

        assert head in (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2')

    def test_stereo_is_preserved_through_the_transcode(self, tmp_tone):
        info = ra.save_upload(tmp_tone(channels=2))

        assert info['channels'] == 2
