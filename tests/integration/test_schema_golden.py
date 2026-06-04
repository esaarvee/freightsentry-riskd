"""Schema anti-drift gate.

Verifies that the current alembic chain produces a schema byte-equivalent
(under the canonical normalizer) to ``tests/golden/schema.sql``. Catches
unintentional schema changes in migrations, model updates, or refactors
that touch DDL.

Canonical normalizer: drop blank lines, comment lines (``--``), and psql
metacommands (``\\``); sort the remaining lines. Sort-based comparison
preserves the set-of-DDL identity that schema-equivalence requires while
ignoring pg_dump's non-deterministic statement ordering and per-dump
random restrict tokens.

If this test fails after intentional schema changes, regenerate the
golden file:

    docker compose exec -T postgres \\
      pg_dump --schema-only --no-comments --no-owner -U riskd riskd \\
      | python3 -c "import sys; \\
          ls = sys.stdin.read().splitlines(); \\
          k = [l for l in ls if l.strip() \\
               and not l.lstrip().startswith('--') \\
               and not l.lstrip().startswith('\\\\')]; \\
          print('\\n'.join(sorted(k)))" \\
      > tests/golden/schema.sql

Established in Phase 8A.0 as the equivalence anchor for the 8A.1
migration squash (11 → 5).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "schema.sql"


def _normalize(dump: str) -> str:
    """Apply the canonical normalizer to pg_dump output."""
    lines = [
        line
        for line in dump.splitlines()
        if line.strip()
        and not line.lstrip().startswith("--")
        and not line.lstrip().startswith("\\")
    ]
    return "\n".join(sorted(lines))


def _capture_schema_dump() -> str:
    """Run pg_dump against the test database; return raw text.

    Prefers a local ``pg_dump`` binary if available (postgresql-client
    installed on the host or in CI); falls back to invoking pg_dump
    inside the docker-compose postgres container, which is the
    default local-dev path. Skips the test if neither route works.
    """
    pg_args = [
        "--schema-only",
        "--no-comments",
        "--no-owner",
        "-U",
        "riskd",
        "riskd",
    ]

    if shutil.which("pg_dump") is not None:
        result = subprocess.run(
            ["pg_dump", "-h", "localhost", "-p", "5432", *pg_args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout

    if shutil.which("docker") is not None:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_dump", *pg_args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout

    pytest.skip(
        "Neither host pg_dump nor docker compose exec pg_dump available; "
        "schema golden test requires one of these paths."
    )


def test_schema_matches_golden() -> None:
    """Current schema is byte-equivalent to the golden file under the normalizer."""
    raw_dump = _capture_schema_dump()
    actual = _normalize(raw_dump)

    expected = GOLDEN_PATH.read_text().rstrip("\n")
    actual = actual.rstrip("\n")

    if actual != expected:
        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile="tests/golden/schema.sql",
                tofile="current schema (normalized)",
                lineterm="",
                n=3,
            )
        )
        pytest.fail(
            "Schema diverges from tests/golden/schema.sql.\n"
            "If the divergence is intentional, regenerate the golden file "
            "per the docstring at the top of this test module.\n\n"
            f"Diff (truncated to first 200 lines):\n{diff[:8000]}"
        )
