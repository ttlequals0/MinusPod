"""GPU utility functions.

Provides shared GPU memory management functions.
"""

import gc
import logging

logger = logging.getLogger(__name__)


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
