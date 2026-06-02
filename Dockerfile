FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential (gcc + libc headers) needed to build pytricia from sdist
# (no aarch64 wheel published). Phase 6 multi-stage strips build-tools from
# the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. Pip installs happen as root (before USER switch)
# so site-packages ownership is system-wide; the app process runs as `app`.
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home-dir /app --shell /bin/bash app

WORKDIR /app

# Install deps via pyproject.toml. Cached unless pyproject changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Application code (lands fully from 1B.3 onwards; copies whatever exists).
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Hand /app to the non-root user before switching identity.
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
