"""Remap embedded (ID3v2 CHAP) chapters onto the post-cut timeline (issue #500)."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from embedded_chapters import probe_chapters, remap_chapters, render_ffmetadata


class TestRemapChapters:
    CUTS = [{'start': 60.0, 'end': 120.0}]  # 60s cut, 2s beep

    def test_chapter_before_cut_unchanged(self):
        chapters = [{'start': 0.0, 'end': 60.0, 'title': 'Intro'}]
        out = remap_chapters(chapters, self.CUTS, replacement_duration=2.0, new_duration=100.0)
        assert out == [{'start': 0.0, 'end': 100.0, 'title': 'Intro'}]

    def test_chapter_after_cut_shifts_by_cut_minus_beep(self):
        chapters = [
            {'start': 0.0, 'end': 130.0, 'title': 'Intro'},
            {'start': 130.0, 'end': 200.0, 'title': 'Main'},
        ]
        out = remap_chapters(chapters, self.CUTS, replacement_duration=2.0, new_duration=142.0)
        assert out[1]['start'] == pytest.approx(72.0)  # 130 - 58
        assert out[0]['end'] == pytest.approx(72.0)
        assert out[1]['end'] == pytest.approx(142.0)

    def test_chapter_swallowed_by_cut_is_dropped(self):
        chapters = [
            {'start': 0.0, 'end': 70.0, 'title': 'Intro'},
            {'start': 70.0, 'end': 110.0, 'title': 'Ad Break Chapter'},
            {'start': 130.0, 'end': 200.0, 'title': 'Main'},
        ]
        out = remap_chapters(chapters, self.CUTS, replacement_duration=2.0, new_duration=142.0)
        assert [c['title'] for c in out] == ['Intro', 'Main']

    def test_chapter_starting_inside_cut_snaps_to_beep_start(self):
        chapters = [
            {'start': 0.0, 'end': 90.0, 'title': 'Intro'},
            {'start': 90.0, 'end': 200.0, 'title': 'Main'},  # starts mid-cut, extends past it
        ]
        out = remap_chapters(chapters, self.CUTS, replacement_duration=2.0, new_duration=142.0)
        assert [c['title'] for c in out] == ['Intro', 'Main']
        assert out[1]['start'] == pytest.approx(60.0)

    def test_zero_length_result_dropped(self):
        # Two chapters collapse onto the same instant after the cut.
        chapters = [
            {'start': 0.0, 'end': 60.0, 'title': 'Intro'},
            {'start': 60.0, 'end': 60.5, 'title': 'Sliver'},
            {'start': 60.5, 'end': 200.0, 'title': 'Main'},
        ]
        cuts = [{'start': 60.0, 'end': 60.5}]
        out = remap_chapters(chapters, cuts, replacement_duration=0.0, new_duration=199.5)
        assert [c['title'] for c in out] == ['Intro', 'Main']

    def test_sliver_drop_keeps_chapters_contiguous(self):
        chapters = [
            {'start': 0.0, 'end': 100.0, 'title': 'Intro'},
            {'start': 100.0, 'end': 100.5, 'title': 'Sliver'},
            {'start': 100.5, 'end': 200.0, 'title': 'Main'},
        ]
        out = remap_chapters(chapters, [], replacement_duration=0.0, new_duration=200.0)
        assert out == [
            {'start': 0.0, 'end': 100.5, 'title': 'Intro'},
            {'start': 100.5, 'end': 200.0, 'title': 'Main'},
        ]

    def test_no_cuts_passthrough(self):
        chapters = [{'start': 0.0, 'end': 50.0, 'title': 'A'}, {'start': 50.0, 'end': 100.0, 'title': 'B'}]
        out = remap_chapters(chapters, [], replacement_duration=2.0, new_duration=100.0)
        assert [c['start'] for c in out] == [0.0, 50.0]


class TestRenderFfmetadata:
    def test_renders_chapter_sections_in_milliseconds(self):
        text = render_ffmetadata([{'start': 0.0, 'end': 12.5, 'title': 'Intro'}])
        assert text.startswith(";FFMETADATA1\n")
        assert "[CHAPTER]" in text
        assert "TIMEBASE=1/1000" in text
        assert "START=0" in text
        assert "END=12500" in text
        assert "title=Intro" in text

    def test_escapes_ffmetadata_special_characters(self):
        text = render_ffmetadata([{'start': 0.0, 'end': 1.0, 'title': 'A=B;C#D\\E'}])
        assert r"title=A\=B\;C\#D\\E" in text


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not available")
class TestProbeChaptersIntegration:
    def test_probe_reads_id3_chapters(self, tmp_path):
        meta = tmp_path / "chap.ffmeta"
        meta.write_text(
            ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=5000\ntitle=One\n"
            "[CHAPTER]\nTIMEBASE=1/1000\nSTART=5000\nEND=10000\ntitle=Two\n"
        )
        mp3 = tmp_path / "in.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
             "-i", str(meta), "-map_metadata", "1", "-map_chapters", "1",
             "-acodec", "libmp3lame", "-ab", "64k", str(mp3)],
            check=True, capture_output=True,
        )
        chapters = probe_chapters(str(mp3))
        assert [c['title'] for c in chapters] == ['One', 'Two']
        assert chapters[0]['start'] == pytest.approx(0.0)
        assert chapters[1]['start'] == pytest.approx(5.0)

    def test_probe_returns_empty_on_chapterless_file(self, tmp_path):
        mp3 = tmp_path / "plain.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-acodec", "libmp3lame", "-ab", "64k", str(mp3)],
            check=True, capture_output=True,
        )
        assert probe_chapters(str(mp3)) == []

    def test_probe_returns_none_on_missing_file(self):
        # Probe failure is distinct from "no chapters": callers keep ffmpeg's
        # default chapter passthrough instead of stripping.
        assert probe_chapters("/nonexistent/file.mp3") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg/ffprobe not available")
class TestRemoveAdsRemapsEmbeddedChapters:
    """End-to-end: remove_ads must rewrite embedded chapters onto the cut
    timeline instead of copying the stale originals (issue #500)."""

    def _make_chaptered_mp3(self, tmp_path, duration=60):
        meta = tmp_path / "in.ffmeta"
        meta.write_text(
            ";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=40000\ntitle=Intro\n"
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART=40000\nEND={duration * 1000}\ntitle=Main\n"
        )
        mp3 = tmp_path / "in.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
             "-i", str(meta), "-map_metadata", "1", "-map_chapters", "1",
             "-acodec", "libmp3lame", "-ab", "64k", str(mp3)],
            check=True, capture_output=True,
        )
        return mp3

    def _make_beep(self, tmp_path, duration=2):
        beep = tmp_path / "beep.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}",
             "-acodec", "libmp3lame", "-ab", "64k", str(beep)],
            check=True, capture_output=True,
        )
        return beep

    def test_output_chapters_are_remapped(self, tmp_path):
        from audio_processor import AudioProcessor

        mp3 = self._make_chaptered_mp3(tmp_path)
        beep = self._make_beep(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")
        applied = proc.remove_ads(str(mp3), [{'start': 10.0, 'end': 30.0}], str(out))

        assert applied and len(applied) == 1
        chapters = probe_chapters(str(out))
        assert [c['title'] for c in chapters] == ['Intro', 'Main']
        beep_len = proc.get_beep_duration()
        # Main was at 40s; 20s cut replaced by the beep.
        assert chapters[1]['start'] == pytest.approx(40.0 - 20.0 + beep_len, abs=0.5)

    def test_chapterless_input_stays_chapterless(self, tmp_path):
        from audio_processor import AudioProcessor

        mp3 = tmp_path / "plain.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
             "-acodec", "libmp3lame", "-ab", "64k", str(mp3)],
            check=True, capture_output=True,
        )
        beep = self._make_beep(tmp_path)
        out = tmp_path / "out.mp3"
        proc = AudioProcessor(replace_audio_path=str(beep), bitrate="64k")
        applied = proc.remove_ads(str(mp3), [{'start': 10.0, 'end': 30.0}], str(out))

        assert applied and len(applied) == 1
        assert probe_chapters(str(out)) == []


class TestProbeFailureSemantics:
    def test_nonzero_ffprobe_exit_returns_none(self, monkeypatch):
        import embedded_chapters as ec

        class _Result:
            returncode = 1
            stdout = b''

        monkeypatch.setattr(ec, 'tracked_run', lambda *a, **k: _Result())
        assert ec.probe_chapters('/some/file.mp3') is None
