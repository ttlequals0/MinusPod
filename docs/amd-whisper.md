# AMD GPU Transcription (ROCm whisper.cpp)

[< Docs index](README.md) | [Project README](../README.md)

---

## Contents

- [Status](#status)
- [How it fits together](#how-it-fits-together)
- [1. Build the image](#1-build-the-image)
- [2. Run the server](#2-run-the-server)
- [3. Point MinusPod at it](#3-point-minuspod-at-it)
- [CPU benchmark](#cpu-benchmark)
- [Notes](#notes)

## Status

This is a community-contributed setup, written and tested by @no84by on one AMD Radeon RX 7900 XTX (gfx1100). The project's CI does not build or test it, and the Dockerfile below is specific to that GPU family. Treat it as a starting point and expect to adjust `AMDGPU_TARGETS` and the device paths for your card. Credit for the setup and the measurements below goes to the contributor, who ported it from [no84by/MinusPod](https://github.com/no84by/MinusPod/tree/amd-whisper-rocmm).

The contributor found CPU transcription usually enough. ROCm is worth adding only when throughput or concurrency (many files at once) becomes a problem. See [CPU benchmark](#cpu-benchmark) for the numbers behind that.

## How it fits together

- whisper.cpp's official images are CUDA-only, so this builds whisper.cpp against AMD HIP/ROCm (`-DGGML_HIP=1 -DAMDGPU_TARGETS=gfx1100`).
- The resulting `whisper-server` runs in its own container with `/dev/kfd` and the render node passed through.
- MinusPod talks to it as a remote OpenAI-compatible Whisper backend, the same way it does for the [NVIDIA whisper.cpp setup](transcription.md#whispercpp-with-docker-nvidia-gpu). The GPU work happens inside the whisper container, so MinusPod runs with `WHISPER_DEVICE=cpu`.
- whisper.cpp's server is unauthenticated. Keep it on a private container network and do not publish its port to the host.

## 1. Build the image

Save as `Dockerfile.amd`. The base image, whisper.cpp tag and GPU target are the contributor's; change `AMDGPU_TARGETS` to match your card.

```dockerfile
FROM rocm/rocm:7.1.1 AS build

ARG WHISPER_VERSION=v1.8.1
ARG AMDGPU_TARGETS=gfx1100

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake build-essential ca-certificates ccache && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${WHISPER_VERSION} \
        https://github.com/ggml-org/whisper.cpp.git .

RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
        -DGGML_HIP=ON \
        -DAMDGPU_TARGETS="${AMDGPU_TARGETS}" \
        -DGGML_CCACHE=ON \
        -DWHISPER_BUILD_SERVER=ON \
        -DWHISPER_BUILD_EXAMPLES=OFF \
        -DWHISPER_BUILD_TESTS=OFF \
        -DCMAKE_C_COMPILER=/opt/rocm/bin/amdclang \
        -DCMAKE_CXX_COMPILER=/opt/rocm/bin/amdclang++ \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DCMAKE_INSTALL_PREFIX=/app && \
    cmake --build build --config Release -j"$(nproc)" && \
    cmake --install build

# Runtime image: needs the ROCm libs plus the host /dev/kfd and /dev/dri at runtime.
FROM rocm/rocm:7.1.1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && groupadd -r whisper && useradd -r -g whisper whisper

WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/bin:${PATH}"

RUN mkdir -p /models && chown -R whisper:whisper /app /models
USER whisper

EXPOSE 8765
# --dtw gives word-level timestamps for precise ad-boundary cutting.
ENTRYPOINT ["whisper-server"]
CMD ["--host","0.0.0.0","--port","8765","--model","/models/ggml-large-v3-turbo-q5_0.bin", \
     "--inference-path","/v1/audio/transcriptions", \
     "--threads","8","--processors","1", \
     "--dtw","large.v3.turbo","--no-flash-attn"]
```

Download the model the contributor used, large-v3-turbo q5_0 (574 MB, no login needed), into `./models`:

```bash
mkdir -p models && cd models && curl -LO \
  https://huggingface.co/crash-sv/scribe-whisper-turbo-ggml/resolve/main/ggml-large-v3-turbo-q5_0.bin && cd ..
```

The file's sha256 is `394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2`. As with the NVIDIA setup, `--no-flash-attn` is required alongside `--dtw`; see the note under [whisper.cpp with Docker](transcription.md#whispercpp-with-docker-nvidia-gpu).

## 2. Run the server

Save as `docker-compose.whisper.amd.yml` next to the Dockerfile and the `models` directory. The contributor ran it with podman; Docker works the same way.

```yaml
services:
  whisper-server:
    image: minuspod/whisper-amd:latest
    container_name: whisper-server
    build:
      context: .
      dockerfile: Dockerfile.amd
      args:
        AMDGPU_TARGETS: gfx1100
    restart: unless-stopped
    # ROCm talks to the card through /dev/kfd; the render node is needed too.
    # Check ls /dev/dri for the node names on your host.
    devices:
      - /dev/kfd:/dev/kfd
      - /dev/dri/renderD128:/dev/dri/renderD128
      - /dev/dri/card1:/dev/dri/card1
    environment:
      # Set only if ROCm does not detect the card; usually unnecessary on ROCm 7.
      # HSA_OVERRIDE_GFX_VERSION: "11.0.0"
    volumes:
      - ./models:/models:rw
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:8765/health || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
    networks:
      - podcast-net

networks:
  podcast-net:
    driver: bridge
    name: minuspod-podcast-net
```

```bash
docker compose -f docker-compose.whisper.amd.yml build   # about 15-25 min, the ROCm base image is about 3 GB
docker compose -f docker-compose.whisper.amd.yml up -d
```

## 3. Point MinusPod at it

```bash
WHISPER_BACKEND=openai-api
WHISPER_API_BASE_URL=http://whisper-server:8765/v1
WHISPER_DEVICE=cpu          # the GPU work happens inside the whisper container
```

MinusPod and `whisper-server` must be able to resolve each other. Add `networks: [podcast-net]` to the MinusPod service, and declare the same `podcast-net` network (named `minuspod-podcast-net` above) in its compose file as an external network. MinusPod's own compose files have no shared network block, so this join is a manual step. If the two run on separate hosts, use the host address and a published port instead, and restrict who can reach it.

## CPU benchmark

Measured by the contributor on 2026-11-01 on an AMD Ryzen 9 7950X3D (16 cores, 32 threads; `libggml-cpu` built with native AVX512, VNNI and BF16, `GGML_NATIVE=ON`). whisper.cpp was rebuilt with `-DWHISPER_BUILD_CLI=ON`: the shipped `main` binary is only a deprecation shim (it prints a warning, exits 1 and does no work), and the real CLI is `whisper-cli`. Model: large-v3-turbo q5_0 (574 MB) from the download above, checksum verified. Test input: a 900 s (15 min) 16 kHz mono WAV. `-ng` disables the GPU (the `ngl 0` equivalent), and the `-t` thread count is decisive.

| threads | 45 s clip wall | RTF |
|---|---|---|
| 1 | 87 s | 0.5x |
| 4 | 25 s | 1.8x |
| 8 | 13 s | 3.4x |
| **16** | **8 s** | **5.6x** (peak) |
| 32 | 120 s | 0.3x (oversubscription collapse) |

The full 900 s clip at `-t 16` took 144 s, which is 6.25x real time. The transcript was clean and accurate, and the language was auto-detected as `en`.

On that host CPU transcription is adequate. A 90 min podcast transcribes in about 16 min wall time at `-t 16`, comfortably inside an overnight or scheduled-worker window, so the CPU path was not the bottleneck on that host. Use `-t 16` on that CPU and never `-t 32`, which thrashes. Results on other hardware will differ.

## Notes

- ROCm 7 supports gfx1100 natively. Set `HSA_OVERRIDE_GFX_VERSION=11.0.0` only if the server does not see the card.
- The GPU can be faster than CPU, but a persistent whisper daemon on a GPU shared with other workloads can wedge HIP and force a GPU reset that affects those workloads. The contributor chose CPU for that reason on a shared host.
- If you only need CPU transcription, the bundled backend (`WHISPER_BACKEND=local` on the CPU image) needs no separate server.
