"""GPU and memory utility functions.

Provides shared GPU memory management and system memory querying functions.
"""

import gc
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_available_system_memory_gb() -> Optional[float]:
    """Get available system RAM in gigabytes.

    Uses /proc/meminfo on Linux, falls back to psutil if available.

    Returns:
        Available RAM in GB, or None if unable to determine
    """
    # Try reading from /proc/meminfo (Linux)
    try:
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                if line.startswith('MemAvailable:'):
                    # Value is in KB
                    kb = int(line.split()[1])
                    gb = kb / (1024 * 1024)
                    return gb
    except (FileNotFoundError, IOError, ValueError):
        pass

    # Fall back to psutil if available
    try:
        import psutil
        mem = psutil.virtual_memory()
        return mem.available / (1024 ** 3)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to get system memory via psutil: {e}")

    return None


def get_available_gpu_memory_gb() -> Optional[float]:
    """Get available GPU VRAM in gigabytes.

    Returns:
        Available VRAM in GB, or None if CUDA not available
    """
    try:
        import torch
        if torch.cuda.is_available():
            # Get total and allocated memory
            device = torch.cuda.current_device()
            total = torch.cuda.get_device_properties(device).total_memory
            allocated = torch.cuda.memory_allocated(device)
            cached = torch.cuda.memory_reserved(device)

            # Available = total - max(allocated, cached)
            # Use cached as it represents actually reserved memory
            available = total - cached
            return available / (1024 ** 3)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Failed to get GPU memory: {e}")

    return None


def get_available_memory_gb(device: str = "cuda") -> Tuple[Optional[float], str]:
    """Get available memory for transcription in gigabytes.

    For CUDA devices, returns GPU VRAM as the primary limit since the model
    runs on the GPU. System RAM is logged for visibility but not used as
    the constraint (faster-whisper loads audio in small chunks, not all at once).

    Args:
        device: "cuda" or "cpu"

    Returns:
        Tuple of (available_memory_gb, memory_type)
        memory_type is "gpu" or "system"
    """
    if device == "cuda":
        gpu_mem = get_available_gpu_memory_gb()
        if gpu_mem is not None:
            # Log both for visibility
            sys_mem = get_available_system_memory_gb()
            if sys_mem is not None:
                logger.info(f"Memory available - GPU: {gpu_mem:.1f}GB, System: {sys_mem:.1f}GB")
            # Use GPU VRAM as the limit since model runs on GPU
            # System RAM is not the bottleneck for faster-whisper
            return gpu_mem, "gpu"

    # CPU mode or CUDA not available - use system RAM
    sys_mem = get_available_system_memory_gb()
    if sys_mem is not None:
        return sys_mem, "system"

    return None, "unknown"


def clear_gpu_memory() -> None:
    """Clear GPU memory cache.

    Runs garbage collection and clears CUDA cache if available.
    Safe to call even if CUDA is not available.
    """
    gc.collect()

    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            logger.debug("Cleared GPU memory cache")
    except ImportError:
        # torch not installed, nothing to clear
        pass
    except Exception as e:
        logger.warning(f"Failed to clear GPU memory: {e}")


def get_gpu_memory_info() -> dict:
    """Get current GPU memory usage information.

    Returns:
        Dict with 'available', 'allocated', 'cached' in bytes,
        or empty dict if CUDA not available
    """
    try:
        import torch
        if torch.cuda.is_available():
            return {
                'allocated': torch.cuda.memory_allocated(),
                'cached': torch.cuda.memory_reserved(),
                'max_allocated': torch.cuda.max_memory_allocated(),
            }
    except ImportError:
        pass
    except Exception:
        pass

    return {}
