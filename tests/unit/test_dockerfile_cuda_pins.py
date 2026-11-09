"""The GPU Dockerfile keeps cuDNN on the last release that supports pre-Turing GPUs."""
import re
from pathlib import Path

GPU_DOCKERFILE = Path(__file__).resolve().parents[2] / 'Dockerfile'


def test_gpu_cudnn_stays_on_last_pre_turing_release():
    code = '\n'.join(
        ln for ln in GPU_DOCKERFILE.read_text().splitlines() if not ln.strip().startswith('#'))
    match = re.search(r'nvidia-cudnn-cu12==(\d+)\.(\d+)\.', code)
    assert match and (int(match.group(1)), int(match.group(2))) == (9, 10), (
        'cuDNN 9.11+ drops Maxwell/Pascal/Volta (#988); bumping needs a decision')
