"""Autouse guard: a test that reaches a real model load or Hugging Face fetch fails in teardown."""
import importlib
import sys

import pytest

# (module, attribute) pairs; a class entry is blocked at __init__, anything else is replaced.
_BOUNDARIES = (
    ('faster_whisper', 'WhisperModel'),
    ('faster_whisper.utils', 'download_model'),
    ('huggingface_hub', 'snapshot_download'),
    ('huggingface_hub', 'hf_hub_download'),
    ('huggingface_hub', 'HfApi.model_info'),
    ('onnx_asr', 'load_model'),
)


def _blocker(name, hits):
    def blocked(*args, **kwargs):
        # Recorded, not only raised: product except blocks can swallow the error.
        hits.append(name)
        raise AssertionError(f"reached {name}; stub the model boundary")
    return blocked


def _resolve(module_name, dotted):
    try:
        owner = importlib.import_module(module_name)
    except ImportError:
        return None
    *path, attr = dotted.split('.')
    for part in path:
        owner = getattr(owner, part, None)
        if owner is None:
            return None
    target = getattr(owner, attr, None)
    return (owner, attr, target) if target is not None else None


@pytest.fixture(autouse=True)
def _no_real_model_loads(monkeypatch, request):
    """Fail the test that reaches a Whisper or Parakeet model load or a Hugging Face Hub fetch."""
    hits = []
    for module_name, dotted in _BOUNDARIES:
        resolved = _resolve(module_name, dotted)
        if resolved is None:
            continue
        owner, attr, target = resolved
        name = f'{module_name}.{dotted}'
        if isinstance(target, type):
            monkeypatch.setattr(target, '__init__', _blocker(name, hits))
            continue
        blocked = _blocker(name, hits)
        monkeypatch.setattr(owner, attr, blocked)
        # Names bound by `from x import y` in project or library modules keep the original otherwise.
        for module in list(sys.modules.values()):
            namespace = getattr(module, '__dict__', None)
            if namespace is not None and namespace.get(attr) is target:
                monkeypatch.setattr(module, attr, blocked)
    yield
    if hits:
        pytest.fail(f"{request.node.nodeid} reached {', '.join(sorted(set(hits)))}; stub the model boundary",
                    pytrace=False)
