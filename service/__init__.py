"""Queue-based, horizontally scalable transcription service (Kubernetes path).

This package is the async counterpart to the single-process interactive app
(app.py). It splits the work into a thin API front door (service.api) and a
queue worker (service.worker) that share state through Redis (service.queue),
so the workers can be autoscaled (including to zero) by KEDA on queue depth.
"""
