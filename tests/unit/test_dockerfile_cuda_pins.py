"""The GPU Dockerfile keeps cuDNN on the last release that supports pre-Turing GPUs."""
import re
from pathlib import Path

GPU_DOCKERFILE = Path(__file__).resolve().parents[2] / 'Dockerfile'


def _code():
    return '\n'.join(
        ln for ln in GPU_DOCKERFILE.read_text().splitlines() if not ln.strip().startswith('#'))


def test_gpu_cudnn_stays_on_last_pre_turing_release():
    match = re.search(r'nvidia-cudnn-cu12==(\d+)\.(\d+)\.', _code())
    assert match and (int(match.group(1)), int(match.group(2))) == (9, 10), (
        'cuDNN 9.11+ drops Maxwell/Pascal/Volta (#988); bumping needs a decision')


def test_gpu_image_ships_nvrtc_on_library_path():
    """cuDNN dlopens NVRTC, so the wheel and its lib dir must both be present."""
    code = _code()
    assert 'nvidia-cuda-nvrtc-cu12==12.' in code
    assert re.search(r'LD_LIBRARY_PATH=\S*nvidia/cuda_nvrtc/lib', code)
