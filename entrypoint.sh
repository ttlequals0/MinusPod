#!/bin/bash
set -e

# Ensure cache and data directories exist and are writable
# These may be mounted volumes, so we create them if needed
mkdir -p /app/data/.cache
mkdir -p /app/data/podcasts

# Run the main application
exec python src/main.py
