"""GPU (via NVML) and system memory helpers."""

import gc
import logging
import os

try:
    import pynvml
except ImportError:
    pynvml = None

logger = logging.getLogger(__name__)

_GIB = 1024 ** 3
_nvml_ready = None  # None unknown, True initialised, False unavailable
_index_warned = False


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


def _nvml_query(fn):
    """Run fn(handle) against the first visible GPU; None without a usable GPU."""
    global _nvml_ready, _index_warned
    if pynvml is None or _nvml_ready is False:
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
        # NVML ignores CUDA_VISIBLE_DEVICES, so apply it here. Numeric indexes are PCI
        # order in NVML but fastest-first in CUDA unless CUDA_DEVICE_ORDER=PCI_BUS_ID.
        visible = os.environ.get('CUDA_VISIBLE_DEVICES')
        if visible is None:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        else:
            first = visible.split(',')[0].strip()
            if first.startswith(('GPU-', 'MIG-')):
                handle = pynvml.nvmlDeviceGetHandleByUUID(first)
            else:
                handle = pynvml.nvmlDeviceGetHandleByIndex(int(first))
                if (not _index_warned and pynvml.nvmlDeviceGetCount() > 1
                        and os.environ.get('CUDA_DEVICE_ORDER') != 'PCI_BUS_ID'):
                    _index_warned = True
                    logger.warning(
                        "CUDA_VISIBLE_DEVICES=%s is a numeric index; NVML may read a different GPU "
                        "on this multi-GPU host, use the GPU-<uuid> form", visible)
        return fn(handle)
    except (pynvml.NVMLError, ValueError) as e:
        logger.debug(f"NVML query failed: {e}")
        return None


def get_available_gpu_memory_gb() -> float | None:
    """Free GPU VRAM in GB, or None without a usable GPU."""
    # Device-wide free memory, so ctranslate2 and ONNX Runtime allocations count.
    return _nvml_query(lambda h: pynvml.nvmlDeviceGetMemoryInfo(h).free / _GIB)


def get_available_memory_gb(device: str = "cuda") -> tuple[float | None, str]:
    """Memory available for transcription as (GB, "gpu" | "system" | "unknown")."""
    if device == "cuda":
        gpu_mem = get_available_gpu_memory_gb()
        if gpu_mem is not None:
            sys_mem = get_available_system_memory_gb()
            if sys_mem is not None:
                logger.info(f"Memory available - GPU: {gpu_mem:.1f}GB, System: {sys_mem:.1f}GB")
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
