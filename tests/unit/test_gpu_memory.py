"""GPU memory and device name come from NVML, device-wide."""
import logging
import types

import pytest

from utils import gpu

_real_bus_id = gpu._cuda_device0_bus_id

GIB = 1024 ** 3


class FakeNVMLError(Exception):
    pass


class FakeNVML:
    NVMLError = FakeNVMLError

    def __init__(self, free=3 * GIB, total=15 * GIB, name='Fake GPU', init_error=None):
        self.free, self.total, self.name = free, total, name
        self.init_error = init_error
        self.calls = []
        self.init_calls = 0

    def nvmlInit(self):
        self.init_calls += 1
        if self.init_error:
            raise self.init_error

    def nvmlDeviceGetHandleByPciBusId(self, bus_id):
        self.calls.append(('bus', bus_id))
        return ('h', bus_id)

    def nvmlDeviceGetHandleByUUID(self, u):
        self.calls.append(('uuid', u))
        return ('h', u)

    def nvmlDeviceGetMemoryInfo(self, handle):
        return types.SimpleNamespace(free=self.free, total=self.total, used=self.total - self.free)

    def nvmlDeviceGetName(self, handle):
        return self.name

    def nvmlDeviceGetIndex(self, handle):
        return 0

    def nvmlDeviceGetPciInfo(self, handle):
        return types.SimpleNamespace(busId=b'00000000:01:00.0')


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(gpu, '_nvml_ready', None)
    monkeypatch.setattr(gpu, '_gpu_logged', False)
    gpu._bus_id.cache_clear()
    monkeypatch.setattr(gpu, '_cuda_device0_bus_id', lambda: '0000:01:00.0')
    monkeypatch.delenv('CUDA_VISIBLE_DEVICES', raising=False)
    yield
    gpu._bus_id.cache_clear()


@pytest.fixture
def fake(monkeypatch):
    fake = FakeNVML()
    monkeypatch.setattr(gpu, 'pynvml', fake)
    return fake


def test_available_gpu_memory_uses_device_wide_free_bytes(fake):
    assert gpu.get_available_gpu_memory_gb() == 3.0


def test_default_device_uses_cuda_bus_id(fake):
    gpu.get_available_gpu_memory_gb()
    assert fake.calls == [('bus', '0000:01:00.0')]


def test_mig_uuid_skips_bus_id(monkeypatch, fake):
    def boom():
        raise AssertionError('bus id must not be read for MIG')
    monkeypatch.setattr(gpu, '_cuda_device0_bus_id', boom)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', 'MIG-abc')
    gpu.get_available_gpu_memory_gb()
    assert fake.calls == [('uuid', 'MIG-abc')]


@pytest.mark.parametrize('bus', ['0000:01:00.0', None])
def test_bus_id_resolved_once(monkeypatch, fake, bus):
    n = []
    monkeypatch.setattr(gpu, '_cuda_device0_bus_id', lambda: n.append(1) or bus)
    gpu.get_available_gpu_memory_gb()
    gpu.get_available_gpu_memory_gb()
    assert len(n) == 1


@pytest.mark.parametrize('value', ['', '-1'])
def test_cuda_disabled(monkeypatch, fake, value):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', value)
    monkeypatch.setattr(gpu, '_cuda_device0_bus_id', lambda: None)
    assert gpu.get_available_gpu_memory_gb() is None
    assert gpu.get_gpu_device_name() == 'cpu'
    assert fake.init_calls == 0


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


def test_unreadable_gpu_memory_warns_and_uses_system(monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'pynvml', None)
    monkeypatch.setattr(gpu, 'get_available_system_memory_gb', lambda: 8.0)
    with caplog.at_level(logging.WARNING, logger=gpu.logger.name):
        assert gpu.get_available_memory_gb('cuda') == (8.0, 'system')
    assert 'sizing chunks from system RAM' in caplog.text


class FakeCuda:
    def __init__(self, init_rc=0, get_rc=0, bus_rc=0):
        self.init_rc, self.get_rc, self.bus_rc = init_rc, get_rc, bus_rc
        self.init_args, self.get_args = None, None

    def cuInit(self, flags):
        self.init_args = flags
        return self.init_rc

    def cuDeviceGet(self, ref, ordinal):
        self.get_args = ordinal
        return self.get_rc

    def cuDeviceGetPCIBusId(self, buf, length, dev):
        buf.value = b'0000:65:00.0'
        return self.bus_rc


def test_bus_id_none_without_libcuda(monkeypatch):
    def no_lib(name):
        raise OSError(name)
    monkeypatch.setattr(gpu.ctypes, 'CDLL', no_lib)
    assert _real_bus_id() is None


@pytest.mark.parametrize('rc', ['init_rc', 'get_rc', 'bus_rc'])
def test_bus_id_none_when_driver_call_fails(monkeypatch, rc):
    lib = FakeCuda(**{rc: 100})
    monkeypatch.setattr(gpu.ctypes, 'CDLL', lambda name: lib)
    assert _real_bus_id() is None


def test_bus_id_read_from_driver(monkeypatch):
    lib = FakeCuda()
    monkeypatch.setattr(gpu.ctypes, 'CDLL', lambda name: lib)
    assert _real_bus_id() == '0000:65:00.0'
    assert lib.init_args == 0
    assert lib.get_args == 0


def _gpu_lines(caplog):
    return [r for r in caplog.records if r.getMessage().startswith('GPU:')]


def test_selected_gpu_is_logged_once_with_runtime_values(fake, caplog):
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.log_selected_gpu()
        gpu.log_selected_gpu()
    lines = _gpu_lines(caplog)
    assert [r.getMessage() for r in lines] == ['GPU: Fake GPU (NVML index 0, bus 00000000:01:00.0, 15.0 GiB)']
    assert lines[0].levelno == logging.INFO


def test_selected_gpu_decodes_bytes_name(monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'pynvml', FakeNVML(name=b'Fake GPU B'))
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.log_selected_gpu()
    assert 'Fake GPU B' in _gpu_lines(caplog)[0].getMessage()


def test_selected_gpu_silent_and_final_without_nvml(monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'pynvml', None)
    with caplog.at_level(logging.DEBUG, logger=gpu.logger.name):
        gpu.log_selected_gpu()
    assert _gpu_lines(caplog) == []
    assert gpu._gpu_logged is True


def test_selected_gpu_silent_and_final_when_nvml_init_fails(monkeypatch, caplog):
    nvml = FakeNVML(init_error=FakeNVMLError('no driver'))
    monkeypatch.setattr(gpu, 'pynvml', nvml)
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.log_selected_gpu()
        gpu.log_selected_gpu()
    assert _gpu_lines(caplog) == []
    assert nvml.init_calls == 1
    assert gpu._gpu_logged is True


def test_selected_gpu_retries_after_a_transient_nvml_failure(monkeypatch, caplog):
    class FlakyNVML(FakeNVML):
        failures = 1

        def nvmlDeviceGetName(self, handle):
            if self.failures:
                self.failures -= 1
                raise FakeNVMLError('transient')
            return self.name
    monkeypatch.setattr(gpu, 'pynvml', FlakyNVML())
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.log_selected_gpu()
        assert _gpu_lines(caplog) == [] and gpu._gpu_logged is False
        gpu.log_selected_gpu()
        gpu.log_selected_gpu()
    assert len(_gpu_lines(caplog)) == 1


def test_selected_gpu_survives_mig_index_failure(monkeypatch, caplog):
    class MigNVML(FakeNVML):
        def nvmlDeviceGetIndex(self, handle):
            raise FakeNVMLError('not supported')
    monkeypatch.setattr(gpu, 'pynvml', MigNVML())
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.log_selected_gpu()
    assert _gpu_lines(caplog)[0].getMessage() == 'GPU: Fake GPU (bus 00000000:01:00.0, 15.0 GiB)'


def test_available_memory_logs_the_gpu_once(fake, monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'get_available_system_memory_gb', lambda: 8.0)
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.get_available_memory_gb('cuda')
        gpu.get_available_memory_gb('cuda')
    assert len(_gpu_lines(caplog)) == 1


def test_cpu_device_never_logs_a_gpu(fake, monkeypatch, caplog):
    monkeypatch.setattr(gpu, 'get_available_system_memory_gb', lambda: 8.0)
    with caplog.at_level(logging.INFO, logger=gpu.logger.name):
        gpu.get_available_memory_gb('cpu')
    assert _gpu_lines(caplog) == []
