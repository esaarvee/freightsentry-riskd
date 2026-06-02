"""Alembic env using psycopg (sync).

Migrations are one-shot DDL — they run synchronously via psycopg. The
runtime app uses asyncpg directly; the two drivers coexist without
issue. Splitting like this keeps alembic compatible with multi-statement
DDL scripts (asyncpg's prepared-statement protocol rejects them).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _build_url() -> str:
    # Phase 5D.2: prefer ALEMBIC_DATABASE_URL if set (typically the
    # superuser DSN so alembic can DROP/CREATE roles, ALTER policies,
    # etc.). Fall back to DATABASE_URL — the runtime connection role,
    # which under post-5D is `riskd_app_login` and lacks the privileges
    # to run schema-changing migrations. Local dev MUST set
    # ALEMBIC_DATABASE_URL once 5D.2 ships; CI alembic invocations and
    # operator-run `alembic upgrade head` commands likewise.
    raw = os.environ.get("ALEMBIC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not raw:
        msg = "ALEMBIC_DATABASE_URL (preferred) or DATABASE_URL must be set"
        raise RuntimeError(msg)
    # alembic always uses sync psycopg; rewrite the common runtime forms
    # (bare postgresql:// and postgresql+asyncpg://) to the sync driver.
    for prefix in ("postgresql+asyncpg://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix) :]
    if raw.startswith("postgresql+psycopg://"):
        return raw
    msg = f"DATABASE_URL must use postgresql:// or postgresql+(asyncpg|psycopg):// scheme, got: {raw.split(':', 1)[0]}://..."
    raise RuntimeError(msg)


config.set_main_option("sqlalchemy.url", _build_url())

target_metadata = None


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
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
        url=config.get_main_option("sqlalchemy.url"),
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
