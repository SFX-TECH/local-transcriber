# Local Transcriber -- CPU image.
# Runs the exact same app as on Windows, on any machine with Docker. No GPU
# needed. Models are NOT baked in; they download once into a mounted cache
# volume (see docker-compose.yml). For GPU, use Dockerfile.gpu instead.
#
# One image, three entrypoints (the default is the interactive app):
#   interactive UI : python -m uvicorn app:app --host 0.0.0.0 --port 8765   (default)
#   queue API      : python -m uvicorn service.api:app --host 0.0.0.0 --port 8000
#   queue worker   : python -m service.worker
# The Kubernetes chart (deploy/helm) overrides the command to pick api or worker.

FROM python:3.11-slim

# ffmpeg: audio extraction (transcribe_core finds it on PATH).
# libgomp1: OpenMP runtime that ctranslate2 / onnxruntime link against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer caches across code changes.
COPY requirements-cpu.txt .
RUN pip install --no-cache-dir -r requirements-cpu.txt

# Application code (kept minimal; see .dockerignore).
COPY app.py transcribe_core.py test_smoke.py ./
COPY static ./static
# Queue-based service (api + worker) for the Kubernetes path.
COPY service ./service

# Send model downloads to a path we mount a named volume on, so they survive
# container restarts and are never re-downloaded.
ENV HF_HOME=/models
# Unbuffered stdout/stderr -> live logs via `docker logs`.
ENV PYTHONUNBUFFERED=1

EXPOSE 8765
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
