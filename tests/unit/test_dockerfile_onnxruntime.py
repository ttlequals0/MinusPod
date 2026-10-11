"""onnxruntime pins: GPU image swaps in onnxruntime-gpu at the CPU pin's version."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GPU_DOCKERFILE = ROOT / 'Dockerfile'
CPU_DOCKERFILE = ROOT / 'Dockerfile.cpu'
REQUIREMENTS = ROOT / 'requirements.txt'

_GPU_PIN = re.compile(r'onnxruntime-gpu==(\d+\.\d+\.\d+)')
_CPU_PIN = re.compile(r'^onnxruntime==(\d+\.\d+\.\d+)', re.MULTILINE)


def test_gpu_onnxruntime_matches_requirements_pin():
    gpu = _GPU_PIN.search(GPU_DOCKERFILE.read_text())
    cpu = _CPU_PIN.search(REQUIREMENTS.read_text())
    assert gpu, 'no onnxruntime-gpu==X.Y.Z pin found in Dockerfile'
    assert cpu, 'no onnxruntime==X.Y.Z pin found in requirements.txt'
    assert gpu.group(1) == '1.24.4'
    assert gpu.group(1) == cpu.group(1), (
        f'GPU swaps in onnxruntime-gpu {gpu.group(1)} but requirements pins {cpu.group(1)}')


def test_gpu_swap_runs_before_pip_is_removed():
    text = GPU_DOCKERFILE.read_text()
    assert text.index('onnxruntime-gpu==') < text.index('pip uninstall -y pip')


def test_cpu_dockerfile_does_not_use_onnxruntime_gpu():
    assert 'onnxruntime-gpu==' not in CPU_DOCKERFILE.read_text()


def test_requirements_include_onnx_asr():
    assert 'onnx-asr==' in REQUIREMENTS.read_text()
