"""Interactive API docs (/docs, /redoc, /openapi.json) must be exposed
only in local/dev. In production they would publish the full route +
schema surface, unauthenticated, behind the internet-facing ALB. The
gate fails closed: anything that isn't an explicit dev marker disables
all three."""

import pytest

from app.main import _docs_kwargs

_DISABLED = {"docs_url": None, "redoc_url": None, "openapi_url": None}
_ENABLED = {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


@pytest.mark.parametrize("env", ["dev", "development", "local", "DEV", " Development "])
def test_docs_enabled_in_dev(env: str) -> None:
    assert _docs_kwargs(env) == _ENABLED


@pytest.mark.parametrize(
    "env",
    ["production", "prod", "staging", "test", "", "developmentt", "produciton"],
)
def test_docs_disabled_outside_dev(env: str) -> None:
    """Fail closed — production, unset, and typo'd values all disable docs."""
    assert _docs_kwargs(env) == _DISABLED
