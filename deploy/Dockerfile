# One image, many roles. ROLE env var (set per container in docker-compose) decides
# whether this becomes CAx, Metrics, MC1, MC3, or the producer.
# Build context is the repo ROOT (see docker-compose.yml), so paths below are repo-relative.
FROM python:3.11-slim

WORKDIR /app

# deps first (better layer caching)
COPY deploy/requirements-runtime.txt /app/requirements-runtime.txt
RUN pip install --no-cache-dir -r /app/requirements-runtime.txt

# the canonical core + the container runtime (only what the image needs)
COPY yprov4agenticai/ /app/yprov4agenticai/
COPY runtime/ /app/runtime/

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    OUT_DIR=/out \
    MPLCONFIGDIR=/tmp/matplotlib

# rootless-friendly (so the same image converts cleanly to Apptainer on HPC later)
RUN useradd -m appuser \
 && mkdir -p /out /tmp/matplotlib \
 && chown -R appuser:appuser /out /tmp/matplotlib /app
USER appuser

ENTRYPOINT ["python", "-m", "runtime.agent_runner"]
