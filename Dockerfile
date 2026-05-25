FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps via pyproject.toml. Cached unless pyproject changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Application code (lands fully from 1B.3 onwards; copies whatever exists).
COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
