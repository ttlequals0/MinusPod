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
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

# Install Python 3.11 and system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3.11-dev \
    python3-pip \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set python3.11 as default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set cache directories to /app/data/.cache (works with volume mounts and non-root users)
ENV WHISPER_MODEL=small
ENV HF_HOME=/app/data/.cache
ENV HUGGINGFACE_HUB_CACHE=/app/data/.cache/hub
ENV XDG_CACHE_HOME=/app/data/.cache

# Note: We don't pre-download the model here because /app/data is typically
# a volume mount. The model will download on first run to the mounted volume.

# Copy application code
COPY src/ ./src/
COPY version.py ./
COPY assets/ ./assets/
COPY assets/ ./assets_builtin/
COPY openapi.yaml ./

# Ensure source files are readable
RUN chmod -R 644 ./src/*.py && chmod 755 ./src

# Copy built frontend from builder stage and fix permissions
COPY --from=frontend-builder /app/static/ui ./static/ui/
RUN chmod -R 644 ./static/ui/* && chmod 755 ./static/ui ./static/ui/assets

# Copy entrypoint script (755 = rwxr-xr-x, readable and executable by all)
COPY entrypoint.sh /app/
RUN chmod 755 /app/entrypoint.sh

# Create data directory (will be overwritten by volume mount in most cases)
RUN mkdir -p /app/data

# Environment variables (RETENTION_PERIOD is in minutes, 1440 = 24 hours)
ENV RETENTION_PERIOD=1440

# Expose port
EXPOSE 8000

# Run the application via entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]
