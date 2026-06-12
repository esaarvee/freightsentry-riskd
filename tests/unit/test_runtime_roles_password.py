"""Unit tests for migration 0005's _app_login_password helper.

Migration imports the helper at upgrade time; we import the helper
directly here. The migration module imports `from alembic import op`
at module top, so we stub alembic before import (same technique as
test_alembic_env.py).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_MIG_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0005_runtime_roles.py"


def _load_mig(monkeypatch: pytest.MonkeyPatch) -> Any:
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(execute=lambda *a, **k: None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "alembic", stub)
    spec = importlib.util.spec_from_file_location("mig_0005_under_test", _MIG_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_password_from_database_url_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://riskd_app_login:mypw123@h:5432/riskd")
    mod = _load_mig(monkeypatch)
    assert mod._app_login_password() == "mypw123"


def test_password_percent_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    # URL-special characters in the password come through percent-encoded;
    # we must decode them so the literal that lands in CREATE ROLE matches
    # the password the runtime app actually presents to Postgres.
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://riskd_app_login:p%40ss%3Aw%2Fo%23rd@h:5432/riskd",
    )
    mod = _load_mig(monkeypatch)
    assert mod._app_login_password() == "p@ss:w/o#rd"


def test_password_falls_back_to_dev_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    mod = _load_mig(monkeypatch)
    assert mod._app_login_password() == "riskd_app_login_dev"


def test_password_falls_back_to_dev_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    # urlsplit on a DSN with no userinfo returns password=None → "".
    monkeypatch.setenv("DATABASE_URL", "postgresql://h:5432/riskd")
    mod = _load_mig(monkeypatch)
    assert mod._app_login_password() == "riskd_app_login_dev"


def test_sql_quote_escapes_single_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_mig(monkeypatch)
    assert mod._sql_quote("") == ""
    assert mod._sql_quote("ab'cd") == "ab''cd"
    assert mod._sql_quote("a'b'c") == "a''b''c"
    assert mod._sql_quote("no-quotes") == "no-quotes"


def test_percent_encoded_single_quote_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # End-to-end check on the decode-then-quote seam: a DSN-encoded `%27`
    # must decode to a literal `'` and then be SQL-quote-doubled to `''`
    # before it lands in the CREATE/ALTER ROLE statement. This is the
    # highest-risk composition between the two helpers.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p%27w@h/db")
    mod = _load_mig(monkeypatch)
    assert mod._app_login_password() == "p'w"
    assert mod._sql_quote(mod._app_login_password()) == "p''w"
