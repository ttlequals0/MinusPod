# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files
COPY frontend/package.json frontend/package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY frontend/ ./

# Build frontend
RUN npm run build

# Stage 2: Python application
# Use CUDA runtime image - PyTorch bundles its own cuDNN/cuBLAS via pip
# Base image CUDA only needs host driver compatibility (forward compatible)
FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

# Install Python 3.11 from deadsnakes PPA and system dependencies
# Ubuntu 24.04 ships Python 3.12; we use deadsnakes to keep Python 3.11
RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common \
    && add-apt-repository ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-dev \
    python3.11-venv \
    ffmpeg \
    curl \
    libsndfile1 \
    libchromaprint-tools \
    && apt-get upgrade -y \
    && rm -rf /usr/lib/python3/dist-packages/cryptography* \
              /usr/lib/python3/dist-packages/PyJWT* \
              /usr/lib/python3/dist-packages/jwt* \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set python3.11 as default, create venv for all pip installs
# Venv avoids pip 26+ "uninstall-no-record-file" errors with system packages
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && python3.11 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip setuptools \
    && rm -rf /opt/venv/lib/python3.11/site-packages/setuptools/_vendor/jaraco* \
              /opt/venv/lib/python3.11/site-packages/setuptools/_vendor/wheel*

# Set working directory
WORKDIR /app

# Pre-install PyTorch 2.6.0 with CUDA 12.4 (includes bundled cuDNN 9)
RUN pip install --no-cache-dir \
    torch==2.6.0+cu124 \
    torchaudio==2.6.0+cu124 \
    --extra-index-url https://download.pytorch.org/whl/cu124

# Install cuDNN 8 runtime for CTranslate2 4.4.0 compatibility
# CTranslate2 requires libcudnn_ops_infer.so.8; torch 2.6.0 only ships cuDNN 9
# Download wheel without deps (avoids replacing torch's nvidia packages), extract .so files
RUN pip download --no-cache-dir --no-deps --dest /tmp nvidia-cudnn-cu12==8.9.7.29 \
    && mkdir -p /opt/cudnn8/lib \
    && python3 -c "import zipfile,glob,shutil,os;whl=glob.glob('/tmp/nvidia_cudnn_cu12-8.9.7.29*.whl')[0];z=zipfile.ZipFile(whl);[shutil.copy2(z.extract(n,'/tmp/cudnn8_x'),'/opt/cudnn8/lib/') for n in z.namelist() if 'libcudnn' in n and '.so.8' in n];z.close()" \
    && rm -rf /tmp/nvidia_cudnn_cu12* /tmp/cudnn8_x

# Copy requirements and install remaining Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache /tmp/* \
    && find /opt/venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Set cache directories to /app/data/.cache (works with volume mounts and non-root users)
# HOME must point to writable location (/app/data is the volume mount)
# ORT_LOG_LEVEL=3 suppresses onnxruntime warnings (GPU discovery fails for AMD, irrelevant for NVIDIA)
# LD_LIBRARY_PATH: cuDNN 8 compat (CTranslate2) + venv nvidia pip dirs (cuDNN 9, cuBLAS)
ENV HOME=/app/data \
    WHISPER_MODEL=small \
    HF_HOME=/app/data/.cache \
    HUGGINGFACE_HUB_CACHE=/app/data/.cache/hub \
    XDG_CACHE_HOME=/app/data/.cache \
    RETENTION_PERIOD=1440 \
    ORT_LOG_LEVEL=3 \
    LD_LIBRARY_PATH=/opt/cudnn8/lib:/opt/venv/lib/python3.11/site-packages/nvidia/cudnn/lib:/opt/venv/lib/python3.11/site-packages/nvidia/cublas/lib

# Copy application code
COPY src/ ./src/
COPY version.py ./
COPY assets/ ./assets/
COPY assets/ ./assets_builtin/
COPY openapi.yaml ./

# Copy built frontend from builder stage
COPY --from=frontend-builder /app/static/ui ./static/ui/

# Copy entrypoint script
COPY entrypoint.sh /app/

# Set permissions - use find to recursively set permissions on subdirectories
# IMPORTANT: glob pattern *.py does NOT match files in subdirectories!
RUN find ./src -type f -name '*.py' -exec chmod 644 {} \; && \
    find ./src -type d -exec chmod 755 {} \; && \
    find ./static/ui -type f -exec chmod 644 {} \; && \
    find ./static/ui -type d -exec chmod 755 {} \; && \
    chmod 755 /app/entrypoint.sh && \
    mkdir -p /app/data

# Expose port
EXPOSE 8000

# Health check - verify the app is responding
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s \
  CMD curl -f http://localhost:8000/api/v1/health || exit 1

# Run the application via entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
