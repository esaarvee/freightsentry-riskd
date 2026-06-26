"""Alembic env using psycopg (sync).

Migrations are one-shot DDL — they run synchronously via psycopg. The
runtime app uses asyncpg directly; the two drivers coexist without
issue. Splitting like this keeps alembic compatible with multi-statement
DDL scripts (asyncpg's prepared-statement protocol rejects them).
"""

from __future__ import annotations

import json
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import URL

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url_from_db_master() -> str | None:
    # `DB_MASTER` is the full AWS Secrets Manager secret value for the RDS
    # master credentials — a JSON blob with username/password/host/port/dbname
    # (CFN GenerateSecretString + SecretTargetAttachment shape). The deploy's
    # migrate task injects it as an env var; we assemble a psycopg DSN from
    # the parts so passwords with URL-special chars are encoded by URL.create.
    blob = os.environ.get("DB_MASTER")
    if not blob:
        return None
    d = json.loads(blob)
    return URL.create(
        "postgresql+psycopg",
        username=d["username"],
        password=d["password"],
        host=d["host"],
        port=int(d["port"]),
        database=d.get("dbname", "riskd"),
    ).render_as_string(hide_password=False)


def _build_url() -> str:
    # Precedence:
    #   1. ALEMBIC_DATABASE_URL — explicit override; local dev + operator-run.
    #   2. DB_MASTER — JSON blob from AWS Secrets Manager (deploy migrate task).
    #   3. DATABASE_URL — legacy fallback (runtime app role; lacks DDL grants
    #      under the non-superuser runtime role, so this path is only useful
    #      with a superuser DSN or for tooling that reuses the runtime DSN).
    override = os.environ.get("ALEMBIC_DATABASE_URL")
    if override:
        return _to_psycopg(override)
    from_master = _url_from_db_master()
    if from_master:
        return from_master
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        msg = "ALEMBIC_DATABASE_URL (preferred), DB_MASTER, or DATABASE_URL must be set"
        raise RuntimeError(msg)
    return _to_psycopg(raw)


def _to_psycopg(raw: str) -> str:
    # alembic always uses sync psycopg; rewrite the common runtime forms
    # (bare postgresql:// and postgresql+asyncpg://) to the sync driver.
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix) :]
    if raw.startswith("postgresql+psycopg://"):
        return raw
    msg = f"DATABASE_URL must use postgresql:// or postgresql+(asyncpg|psycopg):// scheme, got: {raw.split(':', 1)[0]}://..."
    raise RuntimeError(msg)


# Resolve the URL once and pass it directly to the engine/configure calls.
# We deliberately do NOT route it through config.set_main_option(): the
# DB_MASTER path percent-encodes the password (URL.create.render_as_string),
# and set_main_option writes via configparser, which treats '%' as
# interpolation syntax and raises ValueError on encoded passwords.
DATABASE_URL = _build_url()

target_metadata = None


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = DATABASE_URL
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
