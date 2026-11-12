"""GPU (via NVML) and system memory helpers."""

import ctypes
import functools
import gc
import logging
import os
import threading

try:
    import pynvml
except ImportError:
    pynvml = None

logger = logging.getLogger(__name__)

_GIB = 1024 ** 3
_nvml_ready = None  # None unknown, True initialised, False unavailable
_gpu_logged = False
_gpu_log_lock = threading.Lock()


def get_available_system_memory_gb() -> float | None:
    """Available system RAM in GB from /proc/meminfo or psutil, else None."""
    # Try reading from /proc/meminfo (Linux)
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    # Value is in KB
                    kb = int(line.split()[1])
                    gb = kb / (1024 * 1024)
                    return gb
    except (OSError, FileNotFoundError, ValueError):
        pass

    # Fall back to psutil if available
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.available / _GIB
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to get system memory via psutil: {e}")

    return None


def _cuda_device0_bus_id():
    """PCI bus ID of CUDA device 0, the device CTranslate2 and ONNX Runtime use."""
    try:
        cuda = ctypes.CDLL('libcuda.so.1')
    except OSError:
        return None
    dev = ctypes.c_int()
    buf = ctypes.create_string_buffer(32)
    if (cuda.cuInit(0) or cuda.cuDeviceGet(ctypes.byref(dev), 0)
            or cuda.cuDeviceGetPCIBusId(buf, len(buf), dev)):
        return None
    return buf.value.decode()


@functools.cache
def _bus_id():
    """CUDA device 0's PCI bus ID, resolved once per process (None is cached too)."""
    return _cuda_device0_bus_id()


def _nvml_query(fn):
    """Run fn(handle) against CUDA device 0; None without a usable GPU."""
    global _nvml_ready
    if pynvml is None or _nvml_ready is False:
        return None
    # The CUDA driver applies CUDA_VISIBLE_DEVICES and CUDA_DEVICE_ORDER itself.
    first = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')[0].strip()
    mig = first.startswith('MIG-')  # a MIG slice shares its card's bus ID
    if not mig and _bus_id() is None:
        return None
    if _nvml_ready is None:
        try:
            pynvml.nvmlInit()
        except pynvml.NVMLError as e:
            _nvml_ready = False
            logger.warning(f"NVML unavailable, GPU memory and name will not be reported: {e}")
            return None
        _nvml_ready = True
    try:
        if mig:
            handle = pynvml.nvmlDeviceGetHandleByUUID(first)
        else:
            handle = pynvml.nvmlDeviceGetHandleByPciBusId(_bus_id())
        return fn(handle)
    except pynvml.NVMLError as e:
        logger.debug(f"NVML query failed: {e}")
        return None


def get_available_gpu_memory_gb() -> float | None:
    """Free GPU VRAM in GB, or None without a usable GPU."""
    # Device-wide free memory, so ctranslate2 and ONNX Runtime allocations count.
    return _nvml_query(lambda h: pynvml.nvmlDeviceGetMemoryInfo(h).free / _GIB)


def _gpu_summary(handle) -> str:
    try:
        index = f"NVML index {pynvml.nvmlDeviceGetIndex(handle)}, "
    except pynvml.NVMLError:
        index = ""  # a MIG slice has no index of its own
    name = pynvml.nvmlDeviceGetName(handle)
    bus = pynvml.nvmlDeviceGetPciInfo(handle).busId
    total = pynvml.nvmlDeviceGetMemoryInfo(handle).total
    name = name.decode() if isinstance(name, bytes) else name
    bus = bus.decode() if isinstance(bus, bytes) else bus
    return f"GPU: {name} ({index}bus {bus}, {total / _GIB:.1f} GiB)"


def log_selected_gpu() -> None:
    """Log the selected GPU once per process; a transient NVML failure is retried on the next call."""
    global _gpu_logged
    with _gpu_log_lock:
        if _gpu_logged:
            return
        summary = _nvml_query(_gpu_summary)
        if summary is not None:
            logger.info(summary)
        # Only a missing pynvml or a failed init is final.
        _gpu_logged = summary is not None or pynvml is None or _nvml_ready is False


def get_available_memory_gb(device: str = "cuda") -> tuple[float | None, str]:
    """Memory available for transcription as (GB, "gpu" | "system" | "unknown")."""
    if device == "cuda":
        gpu_mem = get_available_gpu_memory_gb()
        if gpu_mem is not None:
            sys_mem = get_available_system_memory_gb()
            if sys_mem is not None:
                logger.info(f"Memory available - GPU: {gpu_mem:.1f}GB, System: {sys_mem:.1f}GB")
            log_selected_gpu()
            # VRAM is the limit; system RAM is not the bottleneck for faster-whisper
            return gpu_mem, "gpu"
        logger.warning("GPU memory unreadable, sizing chunks from system RAM")

    sys_mem = get_available_system_memory_gb()
    if sys_mem is not None:
        return sys_mem, "system"

    return None, "unknown"


def clear_gpu_memory() -> None:
    """Collect garbage; ctranslate2 and ONNX Runtime free VRAM on unload."""
    gc.collect()


def get_gpu_memory_info() -> dict:
    """Device-wide GPU memory in bytes as used/total, or {} without a GPU."""
    info = _nvml_query(lambda h: pynvml.nvmlDeviceGetMemoryInfo(h))
    if info is None:
        return {}
    return {'used': info.used, 'total': info.total}


def get_gpu_device_name() -> str:
    """The active GPU's name, or 'cpu' when none is available."""
    name = _nvml_query(lambda h: pynvml.nvmlDeviceGetName(h))
    if name is None:
        return 'cpu'
    return name.decode() if isinstance(name, bytes) else name
