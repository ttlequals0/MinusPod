"""Free GPU memory is read device-wide, not from torch's own allocator."""
import sys
import types

from utils import gpu


def _fake_torch(free_bytes, total_bytes):
    cuda = types.SimpleNamespace(
        is_available=lambda: True,
        current_device=lambda: 0,
        mem_get_info=lambda device: (free_bytes, total_bytes),
        memory_reserved=lambda device: 0,
        get_device_properties=lambda device: types.SimpleNamespace(total_memory=total_bytes),
    )
    return types.SimpleNamespace(cuda=cuda)


def test_available_gpu_memory_uses_device_wide_free_bytes(monkeypatch):
    gib = 1024 ** 3
    monkeypatch.setitem(sys.modules, 'torch', _fake_torch(3 * gib, 15 * gib))
    assert gpu.get_available_gpu_memory_gb() == 3.0
