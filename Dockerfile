ARG BASE_APP_IMAGE=ubuntu:26.04

# Set --build-arg SECURITY_SRC=stub to skip rebuilding patched packages (faster
# local builds; stock Ubuntu packages are used). Example:
# $ docker build . --build-arg SECURITY_SRC=stub -t minuspod:local
ARG SECURITY_SRC=builder

# Stage 1: Build frontend
FROM node:26-alpine@sha256:e71ac5e964b9201072425d59d2e876359efa25dc96bb1768cb73295728d6e4ea AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files (.npmrc carries legacy-peer-deps for vite-plugin-pwa peer cap)
COPY frontend/package.json frontend/package-lock.json* frontend/.npmrc ./

# Install dependencies
RUN --mount=type=cache,id=npmcache,target=/root/.npm \
    npm ci

# Copy frontend source
COPY frontend/ ./

# Build frontend
RUN npm run build

# Copy Swagger UI assets into the built static dir so the /docs route
# can serve them locally (no third-party CDN).
RUN mkdir -p /app/static/ui/swagger \
    && cp node_modules/swagger-ui-dist/swagger-ui.css \
          node_modules/swagger-ui-dist/swagger-ui-bundle.js \
          node_modules/swagger-ui-dist/swagger-ui-standalone-preset.js \
          /app/static/ui/swagger/

# Stage 2: Build patched security packages
FROM ${BASE_APP_IMAGE} AS base-security-builder
ENV DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates devscripts quilt

# Build patched media packages with Ubuntu's full build recipes.
FROM base-security-builder AS media-security-builder
RUN apt-get build-dep -y --no-install-recommends ffmpeg libass srt libsndfile
COPY scripts/build_media_security_packages.py /build/scripts/
COPY scripts/media-security/ /build/scripts/media-security/
RUN python3 /build/scripts/build_media_security_packages.py

# Build core libraries independently from the media package cache.
FROM base-security-builder AS core-security-builder
RUN apt-get build-dep -y --no-install-recommends -P nobiarch glibc pcre2
COPY scripts/build_media_security_packages.py /build/scripts/
COPY scripts/core-security/ /build/scripts/core-security/
RUN python3 /build/scripts/build_media_security_packages.py --input-dir /build/scripts/core-security

# Build ancillary libraries as an unprivileged package builder.
FROM base-security-builder AS misc-security-builder
RUN apt-get build-dep -y --no-install-recommends cjson glib2.0
COPY scripts/build_media_security_packages.py /build/scripts/
COPY scripts/misc-security/ /build/scripts/misc-security/
RUN install -d -o ubuntu -g ubuntu /work/media-security /out
USER ubuntu
RUN python3 /build/scripts/build_media_security_packages.py --input-dir /build/scripts/misc-security

# Build the patched graphics and video libraries separately.
FROM base-security-builder AS graphics-security-builder
RUN apt-get build-dep -y --no-install-recommends cairo x264
COPY scripts/build_media_security_packages.py /build/scripts/
COPY scripts/graphics-security/ /build/scripts/graphics-security/
RUN python3 /build/scripts/build_media_security_packages.py --input-dir /build/scripts/graphics-security

# Build patched utilities with unprivileged package tests.
FROM base-security-builder AS aux-security-builder
RUN apt-get build-dep -y --no-install-recommends p11-kit bubblewrap tar libxrender \
    && apt-get install -y --no-install-recommends gtk-doc-tools dh-exec bison xutils-dev libselinux1-dev
COPY scripts/build_media_security_packages.py /build/scripts/
COPY scripts/aux-security/ /build/scripts/aux-security/
RUN install -d -o ubuntu -g ubuntu /work/media-security /out
USER ubuntu
RUN python3 /build/scripts/build_media_security_packages.py --input-dir /build/scripts/aux-security

# Define stubs for each security package family
FROM ${BASE_APP_IMAGE} AS base-security-stub
RUN mkdir -p /out/packages /out/provenance
FROM base-security-stub AS core-security-stub
FROM base-security-stub AS media-security-stub
FROM base-security-stub AS misc-security-stub
FROM base-security-stub AS graphics-security-stub
FROM base-security-stub AS aux-security-stub

# Finally, define the final stage (builder or stub) for each security package family
FROM core-security-${SECURITY_SRC} AS core-security-out
FROM media-security-${SECURITY_SRC} AS media-security-out
FROM misc-security-${SECURITY_SRC} AS misc-security-out
FROM graphics-security-${SECURITY_SRC} AS graphics-security-out
FROM aux-security-${SECURITY_SRC} AS aux-security-out

# Python application
# Plain Ubuntu base - no nvidia/cuda image. ctranslate2 statically links the
# CUDA runtime, and cuDNN/cuBLAS come from the pip nvidia-* wheels (torch
# deps) via LD_LIBRARY_PATH below. GPU access is injected by the NVIDIA
# container runtime, driven by the NVIDIA_* env vars in the ENV block.
FROM ${BASE_APP_IMAGE}
ARG MINUSPOD_VERSION=dev

# Install Python 3.12 from deadsnakes PPA and system dependencies
# Ubuntu 26.04 ships Python 3.14; deadsnakes pins 3.12 (cp312 is the
# ceiling for numpy<2.0 wheels; see the numpy pin in requirements.in)
# setpriv (from util-linux, present in the base image) is used by
# entrypoint.sh to drop privileges after the root-only chown step that
# migrates the data volume on first boot.
RUN : "$MINUSPOD_VERSION" && apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-dev \
    python3.12-venv \
    ffmpeg \
    curl \
    libsndfile1 \
    libchromaprint-tools \
    tzdata \
    unzip \
    && apt-get upgrade -y \
    && rm -rf /usr/lib/python3/dist-packages/cryptography* \
              /usr/lib/python3/dist-packages/PyJWT* \
              /usr/lib/python3/dist-packages/jwt* \
    # pebble ships in the ubuntu:26.04 OCI rootfs (not dpkg-owned); unused
    # here and its embedded Go deps carry unfixed HIGH CVEs, so drop it
    && rm -rf /usr/bin/pebble /var/lib/pebble \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && setpriv --reuid=nobody --regid=nogroup --init-groups true

# yt-dlp floor: the pinned release is baked in; the app copies it to the data
# volume on boot and self-updates there, so a new image never downgrades it.
ARG TARGETARCH
ARG DENO_VERSION=v2.9.8
COPY ytdlp-version.txt /tmp/ytdlp-version.txt
RUN set -eu; \
    YTDLP_VERSION=$(tr -d '[:space:]' < /tmp/ytdlp-version.txt); \
    case "${TARGETARCH:-amd64}" in \
      amd64) YTDLP_ASSET=yt-dlp_linux; DENO_ASSET=deno-x86_64-unknown-linux-gnu.zip ;; \
      arm64) YTDLP_ASSET=yt-dlp_linux_aarch64; DENO_ASSET=deno-aarch64-unknown-linux-gnu.zip ;; \
      *) echo "unsupported TARGETARCH ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}"; \
    curl -fsSL -o /tmp/yt-dlp "${base}/${YTDLP_ASSET}"; \
    curl -fsSL -o /tmp/SHA2-256SUMS "${base}/SHA2-256SUMS"; \
    grep " ${YTDLP_ASSET}\$" /tmp/SHA2-256SUMS | sed "s| ${YTDLP_ASSET}| /tmp/yt-dlp|" | sha256sum -c -; \
    install -m 0755 /tmp/yt-dlp /usr/local/bin/yt-dlp; \
    curl -fsSL -o /tmp/deno.zip "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/${DENO_ASSET}"; \
    curl -fsSL -o /tmp/deno.zip.sha256sum "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/${DENO_ASSET}.sha256sum"; \
    (cd /tmp && sed 's|  .*| deno.zip|' deno.zip.sha256sum | sha256sum -c -); \
    unzip -q /tmp/deno.zip -d /usr/local/bin && chmod 0755 /usr/local/bin/deno; \
    rm -f /tmp/yt-dlp /tmp/SHA2-256SUMS /tmp/deno.zip /tmp/deno.zip.sha256sum /tmp/ytdlp-version.txt; \
    /usr/local/bin/yt-dlp --version && /usr/local/bin/deno --version

# Set python3.12 as default, create venv for all pip installs
# Venv avoids pip 26+ "uninstall-no-record-file" errors with system packages
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && python3.12 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN --mount=type=cache,id=pipcache,target=/root/.cache/pip \
    pip install --upgrade pip==26.2.1 setuptools==84.0.0 \
    && rm -rf /opt/venv/lib/python3.12/site-packages/setuptools/_vendor/jaraco* \
              /opt/venv/lib/python3.12/site-packages/setuptools/_vendor/wheel*

# Pre-install PyTorch 2.13.0 with CUDA 12.9 (bundled cuDNN 9 / cuBLAS via
# pip nvidia-* deps). Stay on CUDA 12.x wheels: they run on driver >= 525,
# while cu13x wheels raise the host driver floor to >= 580.
# cu129 over cu126 for Blackwell (sm_120, RTX 50-series). It trades away the
# sm_50/60/70 kernels cu126 carried, which only costs Maxwell/Pascal/Volta
# their VRAM readings here: torch reports memory, ctranslate2 transcribes.
RUN --mount=type=cache,id=pipcache,target=/root/.cache/pip \
    pip install torch==2.13.0+cu129 \
    --extra-index-url https://download.pytorch.org/whl/cu129

# Set working directory
WORKDIR /app

# Copy requirements and install remaining Python dependencies
COPY requirements.txt .
RUN --mount=type=cache,id=pipcache,target=/root/.cache/pip \
    pip install -r requirements.txt
# Parakeet CUDA provider. Same version as the CPU pin; links cudart 12, cudnn 9,
# cublas 12, cufft 11, curand 10 from the torch nvidia wheels.
RUN --mount=type=cache,id=pipcache,target=/root/.cache/pip \
    pip uninstall -y onnxruntime && pip install onnxruntime-gpu==1.24.4
COPY --from=core-security-out /out/packages/ /opt/core-security-packages/
COPY --from=core-security-out /out/provenance/ /usr/share/minuspod/core-security/
RUN apt-get update \
    && if ls /opt/core-security-packages/*.deb >/dev/null 2>&1; then \
        apt-get purge -y \
            linux-libc-dev python3.12-dev libpython3.12-dev libc6-dev libexpat1-dev \
        && apt-get install -y --no-install-recommends /opt/core-security-packages/*.deb; \
    fi \
    && rm -rf /opt/core-security-packages /var/lib/apt/lists/*
# Keep the matching shared-library family and corresponding patched source.
COPY --from=media-security-out /out/packages/ /opt/media-security-packages/
COPY --from=media-security-out /out/provenance/ /usr/share/minuspod/media-security/
RUN apt-get update \
    && if ls /opt/media-security-packages/*.deb >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends /opt/media-security-packages/*.deb; \
    fi \
    && rm -rf /opt/media-security-packages /var/lib/apt/lists/*

COPY --from=misc-security-out /out/packages/ /opt/misc-security-packages/
COPY --from=misc-security-out /out/provenance/ /usr/share/minuspod/misc-security/
RUN apt-get update \
    && if ls /opt/misc-security-packages/*.deb >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends /opt/misc-security-packages/*.deb; \
    fi \
    && rm -rf /opt/misc-security-packages /var/lib/apt/lists/*

COPY --from=graphics-security-out /out/packages/ /opt/graphics-security-packages/
COPY --from=graphics-security-out /out/provenance/ /usr/share/minuspod/graphics-security/
RUN apt-get update \
    && if ls /opt/graphics-security-packages/*.deb >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends /opt/graphics-security-packages/*.deb; \
    fi \
    && rm -rf /opt/graphics-security-packages /var/lib/apt/lists/*

COPY --from=aux-security-out /out/packages/ /opt/aux-security-packages/
COPY --from=aux-security-out /out/provenance/ /usr/share/minuspod/aux-security/
RUN apt-get update \
    && if ls /opt/aux-security-packages/*.deb >/dev/null 2>&1; then \
        apt-get install -y --no-install-recommends /opt/aux-security-packages/*.deb; \
    fi \
    && rm -rf /opt/aux-security-packages /var/lib/apt/lists/*

# Use Ubuntu's supported GNU provider instead of the protected Rust provider.
RUN apt-get update \
    && apt-get install -y --no-install-recommends --allow-remove-essential \
        coreutils-from-gnu coreutils-from-uutils- rust-coreutils- \
    && apt-get purge -y --allow-remove-essential \
        software-properties-common python3-jwt systemd systemd-sysv libpam-systemd \
    && apt-get autoremove -y \
    && for family in core media misc graphics aux; do \
        script=/usr/share/minuspod/$family-security/build_media_security_packages.py; \
        if [ -f "$script" ]; then \
            python3.12 "$script" --verify-installed || exit 1; \
        fi; \
    done \
    && python -m pip uninstall -y pip \
    && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && find /opt/venv -type d -name '__pycache__' -exec rm -rf {} +

# Set cache directories to /app/data/.cache (works with volume mounts and non-root users)
# HOME must point to writable location (/app/data is the volume mount)
# ORT_LOG_LEVEL=3 suppresses onnxruntime warnings (GPU discovery fails for AMD, irrelevant for NVIDIA)
# LD_LIBRARY_PATH: venv nvidia pip dirs for ctranslate2, which dlopens cuDNN 9
# and cuBLAS and statically links the rest. torch pins these via its
# cuda-toolkit metapackage dep; re-check the paths on the next torch bump.
# onnxruntime-gpu (Parakeet) also needs cufft and curand from the same dirs.
# cuda_runtime is unused today (ct2 links cudart statically) but kept on the
# path so a future ctranslate2 that links it dynamically still resolves.
# NVIDIA_*: make the NVIDIA container runtime inject the driver under
# --gpus/legacy runtime (compose device reservations inject regardless).
# NVIDIA_REQUIRE_CUDA restores the fail-fast start gate the nvidia/cuda base
# provided, at the true cu126-wheel floor: CUDA 12.x minor-version compat
# means any driver >= 525 (CUDA 12.0) works. Do NOT raise it to cuda>=12.6 --
# that would refuse drivers that merely predate 12.6 but run cu126 fine.
ENV HOME=/app/data \
    DENO_DIR=/app/data/.cache/deno \
    WHISPER_MODEL=small \
    HF_HOME=/app/data/.cache \
    HUGGINGFACE_HUB_CACHE=/app/data/.cache/hub \
    XDG_CACHE_HOME=/app/data/.cache \
    RETENTION_PERIOD=1440 \
    ORT_LOG_LEVEL=3 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    NVIDIA_REQUIRE_CUDA="cuda>=12.0" \
    LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cuda_runtime/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cufft/lib:/opt/venv/lib/python3.12/site-packages/nvidia/curand/lib

# Copy application code
COPY src/ ./src/
COPY version.py ./
COPY assets/ ./assets/
COPY assets/ ./assets_builtin/
COPY openapi.yaml ./
COPY gunicorn.conf.py ./

# Copy built frontend from builder stage
COPY --from=frontend-builder /app/static/ui ./static/ui/

# Copy entrypoint script
COPY entrypoint.sh /app/

# Set permissions - use find to recursively set permissions on subdirectories
# IMPORTANT: glob pattern *.py does NOT match files in subdirectories!
# Create a non-root minuspod user (UID/GID 1000) that entrypoint.sh drops
# privileges to via setpriv. The container still starts as root so the
# entrypoint can chown the data volume on first boot; no app code runs
# as root. UID/GID are overridable at runtime with APP_UID/APP_GID.
RUN find ./src -type f -name '*.py' -exec chmod 644 {} \; && \
    find ./src -type d -exec chmod 755 {} \; && \
    find ./static/ui -type f -exec chmod 644 {} \; && \
    find ./static/ui -type d -exec chmod 755 {} \; && \
    chmod 755 /app/entrypoint.sh && \
    mkdir -p /app/data && \
    (getent passwd ubuntu && userdel -r ubuntu 2>/dev/null || true) && \
    (getent group ubuntu && groupdel ubuntu 2>/dev/null || true) && \
    groupadd --system --gid 1000 minuspod && \
    useradd --system --uid 1000 --gid minuspod --home-dir /app/data \
            --shell /sbin/nologin minuspod && \
    chown -R minuspod:minuspod /app

LABEL org.opencontainers.image.version="${MINUSPOD_VERSION}"

# Expose port
EXPOSE 8000

# Health check - verify the app is responding
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD curl -f http://localhost:${MINUSPOD_PORT:-8000}/api/v1/health || exit 1

# Run the application via entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
