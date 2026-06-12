"""Unit tests for alembic/env.py DSN composition helpers.

env.py imports from `alembic.context` at module top, which is only
available when alembic is driving the run. We test the helpers in
isolation by loading the module file via importlib and stubbing the
alembic import to avoid the top-level context access.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_ENV_PATH = Path(__file__).resolve().parents[2] / "alembic" / "env.py"


def _load_env_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Stub the alembic package so `from alembic import context` succeeds
    # and the module-top context.config access does not blow up. We then
    # only exercise the pure helpers, not the migration runner.
    stub = types.ModuleType("alembic")
    fake_context = types.SimpleNamespace(
        config=types.SimpleNamespace(
            config_file_name=None,
            set_main_option=lambda *a, **k: None,
            get_section=lambda *a, **k: {},
            config_ini_section="alembic",
            get_main_option=lambda *a, **k: "",
        ),
        is_offline_mode=lambda: True,
        configure=lambda **k: None,
        begin_transaction=lambda: None,
        run_migrations=lambda: None,
    )
    stub.context = fake_context  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", stub)

    # Provide a dummy DATABASE_URL so module import doesn't raise during
    # the top-level _build_url() call.
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.delenv("DB_MASTER", raising=False)

    spec = importlib.util.spec_from_file_location("alembic_env_under_test", _ENV_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_url_from_db_master_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.setenv(
        "DB_MASTER",
        json.dumps(
            {
                "username": "master",
                "password": "secret123",
                "host": "db.example.com",
                "port": 5432,
                "dbname": "riskd",
            }
        ),
    )
    url = mod._url_from_db_master()
    # Structural checks (robust to SQLAlchemy URL.create rendering tweaks):
    # scheme, credentials, host:port, and database path all present.
    assert url is not None
    assert url.startswith("postgresql+psycopg://master:secret123@")
    assert "@db.example.com:5432/riskd" in url


def test_url_from_db_master_password_special_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    # Password with characters that have meaning in URLs (`@`, `:`, `/`, `#`).
    # URL.create must percent-encode them so the DSN is parseable.
    mod = _load_env_module(monkeypatch)
    monkeypatch.setenv(
        "DB_MASTER",
        json.dumps(
            {
                "username": "master",
                "password": "p@ss:w/o#rd",
                "host": "db.example.com",
                "port": "5432",  # str, as AWS sometimes serialises it
                "dbname": "riskd",
            }
        ),
    )
    url = mod._url_from_db_master()
    assert url is not None
    # The raw password must NOT appear verbatim — every special char must be
    # percent-encoded.
    assert "p@ss:w/o#rd" not in url
    assert "p%40ss%3Aw%2Fo%23rd" in url
    assert url.startswith("postgresql+psycopg://master:")
    assert "@db.example.com:5432/riskd" in url


def test_url_from_db_master_default_dbname(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.setenv(
        "DB_MASTER",
        json.dumps(
            {
                "username": "master",
                "password": "x",
                "host": "h",
                "port": 5432,
                # dbname intentionally omitted
            }
        ),
    )
    url = mod._url_from_db_master()
    assert url is not None
    assert url.endswith("/riskd")


def test_url_from_db_master_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.delenv("DB_MASTER", raising=False)
    assert mod._url_from_db_master() is None


def test_build_url_precedence_alembic_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", "postgresql://override:pw@h/db")
    monkeypatch.setenv(
        "DB_MASTER",
        json.dumps(
            {"username": "m", "password": "x", "host": "h2", "port": 5432, "dbname": "riskd"}
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@h3/db")
    assert mod._build_url() == "postgresql+psycopg://override:pw@h/db"


def test_build_url_db_master_beats_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DB_MASTER",
        json.dumps(
            {
                "username": "m",
                "password": "x",
                "host": "master-host",
                "port": 5432,
                "dbname": "riskd",
            }
        ),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:pw@runtime-host/db")
    assert mod._build_url() == "postgresql+psycopg://m:x@master-host:5432/riskd"


def test_build_url_falls_back_to_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_MASTER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://app:pw@h/db")
    # async driver gets rewritten to sync psycopg
    assert mod._build_url() == "postgresql+psycopg://app:pw@h/db"


def test_build_url_raises_when_all_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_env_module(monkeypatch)
    monkeypatch.delenv("ALEMBIC_DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_MASTER", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="ALEMBIC_DATABASE_URL"):
        mod._build_url()
