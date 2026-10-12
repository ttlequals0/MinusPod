"""The transcriber package keeps its public surface and an acyclic submodule import graph."""
import pathlib

import transcriber
from tests.import_graph import assert_package_acyclic

PKG = pathlib.Path(__file__).resolve().parents[2] / 'src/transcriber'


def test_transcriber_import_graph_is_acyclic_and_init_imports_every_module():
    assert_package_acyclic(PKG, 'transcriber')


def test_package_exposes_the_transcriber_class_and_constants():
    assert hasattr(transcriber, 'Transcriber')
    assert isinstance(transcriber.CDN_REFUSED_PREFIX, str)


def test_whisper_model_singleton_lives_in_local():
    from transcriber.local import WhisperModelSingleton
    assert WhisperModelSingleton.__module__ == 'transcriber.local'
