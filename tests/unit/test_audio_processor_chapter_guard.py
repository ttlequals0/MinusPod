import logging
from unittest.mock import MagicMock

import audio_processor
from audio_processor import AudioProcessor
from id3_chapters import ChapterTagError
from utils.audio import RenderInput


def test_chapter_tag_error_does_not_fail_the_cut(monkeypatch, caplog):
    p = AudioProcessor()
    monkeypatch.setattr(p, 'get_audio_duration', MagicMock(return_value=541.0))
    monkeypatch.setattr(p, 'get_beep_duration', MagicMock(return_value=1.0))
    monkeypatch.setattr(audio_processor, 'tracked_run',
                        MagicMock(return_value=MagicMock(returncode=0)))
    monkeypatch.setattr(audio_processor, 'probe_render_input',
                        MagicMock(return_value=RenderInput(600.0, None, [])))
    restore = MagicMock(side_effect=ChapterTagError('Invalid ID3 synchsafe integer'))
    monkeypatch.setattr(audio_processor, 'restore_chapter_tag', restore)
    with caplog.at_level(logging.WARNING, logger='audio_processor'):
        applied = p.remove_ads('/nonexistent-in.mp3', [{'start': 100.0, 'end': 160.0}],
                               '/nonexistent-out.mp3')
    assert applied is not None
    restore.assert_called_once()
    assert any('Chapter tag not restored: Invalid ID3 synchsafe integer' in r.message
               for r in caplog.records)
