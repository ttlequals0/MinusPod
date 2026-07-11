# Stage 1: Build frontend
FROM node:26-alpine@sha256:e71ac5e964b9201072425d59d2e876359efa25dc96bb1768cb73295728d6e4ea AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files (.npmrc carries legacy-peer-deps for vite-plugin-pwa peer cap)
COPY frontend/package.json frontend/package-lock.json* frontend/.npmrc ./

# Install dependencies
RUN npm ci

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

# Stage 2: Python application
# Plain Ubuntu base - no nvidia/cuda image. ctranslate2 statically links the
# CUDA runtime, and cuDNN/cuBLAS come from the pip nvidia-* wheels (torch
# deps) via LD_LIBRARY_PATH below. GPU access is injected by the NVIDIA
# container runtime, driven by the NVIDIA_* env vars in the ENV block.
# This also avoids the driver>=580 gate that CUDA 13.x base images enforce.
FROM ubuntu:26.04

# Install Python 3.12 from deadsnakes PPA and system dependencies
# Ubuntu 26.04 ships Python 3.14; deadsnakes pins 3.12 (numpy<2.0 caps
# wheels at cp312, and numpy 2.x needs x86-64-v2 the target server lacks)
# setpriv (from util-linux, present in the base image) is used by
# entrypoint.sh to drop privileges after the root-only chown step that
# migrates the data volume on first boot.
RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && apt-get upgrade -y \
    && rm -rf /usr/lib/python3/dist-packages/cryptography* \
              /usr/lib/python3/dist-packages/PyJWT* \
              /usr/lib/python3/dist-packages/jwt* \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && setpriv --reuid=nobody --regid=nogroup --init-groups true

# Set python3.12 as default, create venv for all pip installs
# Venv avoids pip 26+ "uninstall-no-record-file" errors with system packages
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && python3.12 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip==25.2 setuptools==80.9.0 \
    && rm -rf /opt/venv/lib/python3.12/site-packages/setuptools/_vendor/jaraco* \
              /opt/venv/lib/python3.12/site-packages/setuptools/_vendor/wheel*

# Set working directory
WORKDIR /app

# Pre-install PyTorch 2.13.0 with CUDA 12.6 (bundled cuDNN 9 / cuBLAS via
# pip nvidia-* deps). cu13x wheels need driver>=580; prod T4 runs 535, and
# CUDA 12.x wheels only need driver>=525.
RUN pip install --no-cache-dir \
    torch==2.13.0+cu126 \
    --extra-index-url https://download.pytorch.org/whl/cu126

# Copy requirements and install remaining Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Build headers only needed for pip C-extension builds above; linux-libc-dev
# carries a stream of unfixed kernel-header CVEs the runtime never touches.
RUN apt-get purge -y linux-libc-dev python3.12-dev libpython3.12-dev libc6-dev libexpat1-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/* \
    && find /opt/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Set cache directories to /app/data/.cache (works with volume mounts and non-root users)
# HOME must point to writable location (/app/data is the volume mount)
# ORT_LOG_LEVEL=3 suppresses onnxruntime warnings (GPU discovery fails for AMD, irrelevant for NVIDIA)
# LD_LIBRARY_PATH: venv nvidia pip dirs (cuDNN 9 bundled with torch, cuBLAS)
# NVIDIA_*: previously inherited from the nvidia/cuda base image; needed so
# the NVIDIA container runtime injects the driver under --gpus/legacy runtime
ENV HOME=/app/data \
    WHISPER_MODEL=small \
    HF_HOME=/app/data/.cache \
    HUGGINGFACE_HUB_CACHE=/app/data/.cache/hub \
    XDG_CACHE_HOME=/app/data/.cache \
    RETENTION_PERIOD=1440 \
    ORT_LOG_LEVEL=3 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib

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

# Expose port
EXPOSE 8000

# Health check - verify the app is responding
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD curl -f http://localhost:${MINUSPOD_PORT:-8000}/api/v1/health || exit 1

# Run the application via entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
