"""Torch pins in the two Dockerfiles stay compatible and in step."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GPU_DOCKERFILE = ROOT / 'Dockerfile'
CPU_DOCKERFILE = ROOT / 'Dockerfile.cpu'

_GPU_PIN = re.compile(r'torch==(\d+\.\d+\.\d+)\+cu(\d+)')
_CPU_PIN = re.compile(r'torch==(\d+\.\d+\.\d+)\s*\\')


def test_gpu_torch_stays_on_a_cuda_12_wheel():
    # cu13x wheels raise the host driver floor from 525 to 580, which would
    # strand deployments still on a 5xx driver.
    match = _GPU_PIN.search(GPU_DOCKERFILE.read_text())
    assert match, 'no torch==X.Y.Z+cuNNN pin found in Dockerfile'
    assert match.group(2).startswith('12'), (
        f'torch pinned to cu{match.group(2)}; CUDA 12.x is required until the '
        f'documented driver floor moves')


def test_cpu_and_gpu_torch_versions_match():
    gpu = _GPU_PIN.search(GPU_DOCKERFILE.read_text())
    cpu = _CPU_PIN.search(CPU_DOCKERFILE.read_text())
    assert gpu and cpu, 'torch pin missing from one of the Dockerfiles'
    assert gpu.group(1) == cpu.group(1), (
        f'GPU pins torch {gpu.group(1)} but CPU pins {cpu.group(1)}; the CPU '
        f'image mirrors the version, only the CUDA build differs')


def test_cpu_dockerfile_does_not_pull_a_cuda_wheel():
    text = CPU_DOCKERFILE.read_text()
    install = [ln for ln in text.splitlines() if 'torch==' in ln and not ln.strip().startswith('#')]
    assert install, 'no torch install line in Dockerfile.cpu'
    assert all('+cu' not in ln for ln in install), 'CPU image must use the CPU wheel'
