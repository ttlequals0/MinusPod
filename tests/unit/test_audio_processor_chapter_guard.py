import logging
from unittest.mock import MagicMock

import audio_processor
import embedded_chapters
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
    monkeypatch.setattr(embedded_chapters, 'remap_tag',
                        MagicMock(side_effect=ChapterTagError('Invalid ID3 synchsafe integer')))
    monkeypatch.setattr(audio_processor, 'source_chapter_tag', MagicMock(return_value=MagicMock()))
    with caplog.at_level(logging.WARNING, logger='embedded_chapters'):
        applied = p.remove_ads('/nonexistent-in.mp3', [{'start': 100.0, 'end': 160.0}],
                               '/nonexistent-out.mp3')
    assert applied is not None
    assert any('Chapter tag not restored: Invalid ID3 synchsafe integer' in r.message
               for r in caplog.records)


def test_read_tag_error_is_tolerated_by_restore(monkeypatch, caplog):
    monkeypatch.setattr(embedded_chapters, 'remap_tag', MagicMock())
    monkeypatch.setattr(embedded_chapters, 'read_tag',
                        MagicMock(side_effect=ChapterTagError('Truncated ID3 tag')))
    with caplog.at_level(logging.WARNING, logger='embedded_chapters'):
        embedded_chapters.restore_chapter_tag('/nonexistent-out.mp3', MagicMock(), [], 0, 10.0)
    assert any('Chapter tag not restored: Truncated ID3 tag' in r.message for r in caplog.records)
