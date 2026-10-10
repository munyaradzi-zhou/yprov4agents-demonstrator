# One image, many roles: the ROLE environment variable selects which agent the container
# becomes (see runtime/agent_runner.py). Build context is the repository root.
#
# Dependencies are installed from uv.lock, not from a loose requirements file, so the
# image provably holds the same versions as a local run. A paper artifact whose container
# and host disagree about library versions cannot support a reproducibility claim.

# Base images are pinned by digest as well as tag. A tag is a pointer its publisher can
# move — python:3.11-slim is rebuilt for every security patch — so the tag alone names a
# different image from one month to the next. The tag stays for the reader; the digest is
# what is pulled. Refresh both together, deliberately.
FROM ghcr.io/astral-sh/uv:0.7.2@sha256:3b898ca84fbe7628c5adcd836c1de78a0f1ded68344d019af8478d4358417399 AS uv
FROM python:3.11-slim@sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce AS runtime

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OUT_DIR=/out \
    MPLCONFIGDIR=/tmp/matplotlib \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NATIVE_TLS=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

# Resolve dependencies before copying source, so editing an agent does not invalidate the
# dependency layer. --frozen fails the build if uv.lock is stale rather than silently
# resolving something different from what was locked.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# The instrumentation core, the agent runtime, and the evaluation harness.
COPY yprov4agenticai/ ./yprov4agenticai/
COPY runtime/ ./runtime/
COPY eval/ ./eval/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Rootless, so the same image converts cleanly to Apptainer for HPC deployment later.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /out /tmp/matplotlib \
 && chown -R appuser:appuser /out /tmp/matplotlib /app /opt/venv
USER appuser

# Fails the build if the image cannot import its own packages, which catches a broken
# layer copy at build time instead of at `compose up`.
RUN python -c "import yprov4agenticai, runtime, eval; print(yprov4agenticai.__version__)"

ENTRYPOINT ["python", "-m", "runtime.agent_runner"]
