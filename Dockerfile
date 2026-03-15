# Stage 1: Build frontend
FROM --platform=linux/amd64 node:20-alpine AS frontend-builder

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
FROM --platform=linux/amd64 nvidia/cuda:12.6.3-runtime-ubuntu24.04

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
    libsndfile1 \
    libchromaprint-tools \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Set python3.11 as default and bootstrap pip via ensurepip
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && python3.11 -m ensurepip --upgrade \
    && python3.11 -m pip install --upgrade pip

# Set working directory
WORKDIR /app

# Pre-install PyTorch 2.5.0 with CUDA 12.1 (includes bundled cuDNN)
RUN pip install --no-cache-dir \
    torch==2.5.0+cu121 \
    torchaudio==2.5.0+cu121 \
    --extra-index-url https://download.pytorch.org/whl/cu121

# Copy requirements and install remaining Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf /root/.cache /tmp/* \
    && find /usr/local -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Set cache directories to /app/data/.cache (works with volume mounts and non-root users)
# HOME must point to writable location (/app/data is the volume mount)
# ORT_LOG_LEVEL=3 suppresses onnxruntime warnings (GPU discovery fails for AMD, irrelevant for NVIDIA)
# LD_LIBRARY_PATH includes nvidia pip package dirs so CTranslate2 can dlopen cuDNN/cuBLAS
ENV HOME=/app/data \
    WHISPER_MODEL=small \
    HF_HOME=/app/data/.cache \
    HUGGINGFACE_HUB_CACHE=/app/data/.cache/hub \
    XDG_CACHE_HOME=/app/data/.cache \
    RETENTION_PERIOD=1440 \
    ORT_LOG_LEVEL=3 \
    LD_LIBRARY_PATH=/usr/local/lib/python3.11/dist-packages/nvidia/cudnn/lib:/usr/local/lib/python3.11/dist-packages/nvidia/cublas/lib

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

# Run the application via entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
