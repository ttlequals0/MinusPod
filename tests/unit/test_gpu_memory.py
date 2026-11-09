"""GPU memory and device name come from NVML, device-wide."""
import logging
import types

import pytest

from utils import gpu

GIB = 1024 ** 3


class FakeNVMLError(Exception):
    pass


class FakeNVML:
    NVMLError = FakeNVMLError

    def __init__(self, free=3 * GIB, total=15 * GIB, name='Fake GPU', init_error=None, count=1):
        self.free, self.total, self.name = free, total, name
        self.init_error = init_error
        self.count = count
        self.calls = []
        self.init_calls = 0

    def nvmlInit(self):
        self.init_calls += 1
        if self.init_error:
            raise self.init_error

    def nvmlDeviceGetCount(self):
        return self.count

    def nvmlDeviceGetHandleByIndex(self, i):
        if i < 0:
            raise FakeNVMLError('invalid index')
        self.calls.append(('index', i))
        return ('h', i)

    def nvmlDeviceGetHandleByUUID(self, u):
        self.calls.append(('uuid', u))
        return ('h', u)

    def nvmlDeviceGetMemoryInfo(self, handle):
        return types.SimpleNamespace(free=self.free, total=self.total, used=self.total - self.free)

    def nvmlDeviceGetName(self, handle):
        return self.name


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(gpu, '_nvml_ready', None)
    monkeypatch.setattr(gpu, '_index_warned', False)
    monkeypatch.delenv('CUDA_DEVICE_ORDER', raising=False)
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeNVML()
    monkeypatch.setattr(gpu, 'pynvml', fake)
    return fake


def test_available_gpu_memory_uses_device_wide_free_bytes(fake):
    assert gpu.get_available_gpu_memory_gb() == 3.0


@pytest.mark.parametrize('value,expected', [
    (None, ('index', 0)),
    ('1', ('index', 1)),
    ('GPU-abc', ('uuid', 'GPU-abc')),
    ('MIG-abc', ('uuid', 'MIG-abc')),
])
def test_device_selection(monkeypatch, fake, value, expected):
    if value is not None:
        monkeypatch.setenv('CUDA_VISIBLE_DEVICES', value)
    gpu.get_available_gpu_memory_gb()
    assert fake.calls == [expected]


@pytest.mark.parametrize('value', ['', '-1'])
def test_cuda_disabled(monkeypatch, fake, value):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', value)
    assert gpu.get_available_gpu_memory_gb() is None
    assert gpu.get_gpu_device_name() == 'cpu'


def test_nvml_init_error_reports_no_gpu(monkeypatch):
    monkeypatch.setattr(gpu, 'pynvml', FakeNVML(init_error=FakeNVMLError('no driver')))
    assert gpu.get_available_gpu_memory_gb() is None
    assert gpu.get_gpu_device_name() == 'cpu'
    assert gpu.get_gpu_memory_info() == {}


def test_pynvml_missing_reports_no_gpu(monkeypatch):
    monkeypatch.setattr(gpu, 'pynvml', None)
    assert gpu.get_available_gpu_memory_gb() is None
    assert gpu.get_gpu_device_name() == 'cpu'
    assert gpu.get_gpu_memory_info() == {}


def test_memory_info_keys(fake):
    assert gpu.get_gpu_memory_info() == {'used': 12 * GIB, 'total': 15 * GIB}


def test_device_name_decodes_bytes(monkeypatch):
    monkeypatch.setattr(gpu, 'pynvml', FakeNVML(name=b'GeForce GTX 1070'))
    assert gpu.get_gpu_device_name() == 'GeForce GTX 1070'


def test_nvml_init_failure_is_remembered(monkeypatch):
    fake = FakeNVML(init_error=FakeNVMLError('no driver'))
    monkeypatch.setattr(gpu, 'pynvml', fake)
    gpu.get_available_gpu_memory_gb()
    gpu.get_available_gpu_memory_gb()
    assert gpu._nvml_ready is False
    assert fake.init_calls == 1


def test_numeric_index_warns_on_multi_gpu(monkeypatch, caplog):
    fake = FakeNVML(count=2)
    monkeypatch.setattr(gpu, 'pynvml', fake)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '1')
    with caplog.at_level(logging.WARNING, logger=gpu.logger.name):
        gpu.get_available_gpu_memory_gb()
        gpu.get_available_gpu_memory_gb()
    assert fake.calls == [('index', 1), ('index', 1)]
    assert len([r for r in caplog.records if 'numeric index' in r.getMessage()]) == 1


def test_numeric_index_no_warning_with_pci_order(monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'pynvml', FakeNVML(count=2))
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '1')
    monkeypatch.setenv('CUDA_DEVICE_ORDER', 'PCI_BUS_ID')
    with caplog.at_level(logging.WARNING, logger=gpu.logger.name):
        gpu.get_available_gpu_memory_gb()
    assert not [r for r in caplog.records if 'numeric index' in r.getMessage()]


def test_unreadable_gpu_memory_warns_and_uses_system(monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'pynvml', None)
    monkeypatch.setattr(gpu, 'get_available_system_memory_gb', lambda: 8.0)
    with caplog.at_level(logging.WARNING, logger=gpu.logger.name):
        assert gpu.get_available_memory_gb('cuda') == (8.0, 'system')
    assert 'sizing chunks from system RAM' in caplog.text
