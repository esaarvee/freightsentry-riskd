# =============================================================================
# Builder stage — installs build-essential + builds wheels for native
# dependencies (e.g. pytricia which has no aarch64 wheel published) into a
# self-contained /install tree. Project source (app/) is intentionally NOT
# copied into this stage; we install dependencies only, then COPY the app
# source into the runtime stage's WORKDIR. This keeps the dep-install step
# Docker-cacheable (only re-runs when pyproject.toml changes) and avoids
# baking the project package into site-packages, which would create a
# stale-second-copy hazard alongside /app/app/ at runtime.
# =============================================================================
FROM python:3.13-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Extract main runtime dependencies from pyproject.toml via stdlib tomllib
# and install ONLY those (no project source, no [test] or [dev] extras).
# Robust against hatchling lifecycle changes — pip operates only on the
# explicit dependency list, never builds the project wheel.
COPY pyproject.toml ./
RUN python -c "import tomllib, sys; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; sys.stdout.write('\n'.join(deps) + '\n')" > /tmp/requirements.txt \
    && pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt


# =============================================================================
# Runtime stage — drops build-essential. Only python:3.13-slim base + the
# pre-built site-packages from the builder stage + application source +
# alembic migrations + a non-root user (UID/GID 1000) with /sbin/nologin
# shell.
# =============================================================================
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Non-root runtime user. /sbin/nologin disables interactive shell — the
# app process runs as `app` with no need for a login shell.
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /app --shell /sbin/nologin app

# Copy the pre-built site-packages + python entry-point scripts from the
# builder stage. The /install/lib/python3.13/site-packages tree contains
# pytricia + every other declared dependency; /install/bin contains uvicorn,
# alembic, etc.
COPY --from=builder /install/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /install/bin /usr/local/bin

WORKDIR /app

# Project source lives ONLY in /app/app/ (not duplicated in site-packages).
# uvicorn picks it up via cwd-on-sys.path from WORKDIR /app.
COPY --chown=app:app app/ ./app/
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app alembic/ ./alembic/

USER app

EXPOSE 8000

# ALB target group + ECS task health-check probe both reach the /health/
# endpoint. Uses stdlib urllib.request so the probe has zero non-stdlib
# dependencies — independent of any transitive that might be reorganized
# upstream (e.g. fastapi[standard] grouping changes). urlopen raises on
# non-2xx, which propagates as non-zero exit; CMD's `python -c` returns 1
# on uncaught exception → Docker daemon marks the container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
