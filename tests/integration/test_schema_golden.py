"""Schema anti-drift gate.

Verifies that the current alembic chain produces a schema byte-equivalent
(under the canonical normalizer) to ``tests/golden/schema.sql``. Catches
unintentional schema changes in migrations, model updates, or refactors
that touch DDL.

Canonical normalizer: drop blank lines, comment lines (``--``), psql
metacommands (``\\``), and top-level session ``SET <guc> = …;`` preamble
lines; sort the remaining lines. Sort-based comparison preserves the
set-of-DDL identity that schema-equivalence requires while ignoring
pg_dump's non-deterministic statement ordering and per-dump random
restrict tokens. Dropping the session ``SET`` preamble (connection GUCs
such as ``statement_timeout``, ``client_encoding``) makes the golden
robust to pg_dump-version differences that vary only that preamble — e.g.
pg18 emits ``SET transaction_timeout = 0;`` where pg16 does not — without
touching any DDL. ``ALTER … SET DEFAULT`` (begins ``ALTER``) and the
version-invariant ``SELECT pg_catalog.set_config('search_path', …)`` line
are retained.

The canonical schema lineage is the docker-compose ``postgres`` container
(pg16). Both the runtime capture below and the regeneration command prefer
that container, so a host with a different ``pg_dump`` major version cannot
produce a version-skewed result on the primary path.

If this test fails after intentional schema changes, regenerate the
golden file via the container-pinned command:

    docker compose exec -T postgres \\
      pg_dump --schema-only --no-comments --no-owner -U riskd riskd \\
      | python3 -c "import re, sys; \\
          ls = sys.stdin.read().splitlines(); \\
          k = [l for l in ls if l.strip() \\
               and not l.lstrip().startswith('--') \\
               and not l.lstrip().startswith('\\\\') \\
               and not re.match(r'SET \\w+ = ', l)]; \\
          print('\\n'.join(sorted(k)))" \\
      > tests/golden/schema.sql

Serves as the equivalence anchor for the migration squash (11 → 5).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

GOLDEN_PATH = Path(__file__).parent.parent / "golden" / "schema.sql"

# Golden lineage: the docker-compose ``postgres`` container (pg16). A host
# fallback dump with a different major is flagged as a possible version artifact.
CANONICAL_PG_MAJOR = 16

# Top-level session GUC preamble lines (``SET statement_timeout = 0;`` etc.).
# Anchored at column 0, so ``ALTER … SET DEFAULT`` (begins ``ALTER``) never matches.
_SESSION_SET = re.compile(r"SET \w+ = ")


def _normalize(dump: str) -> str:
    """Apply the canonical normalizer to pg_dump output."""
    lines = [
        line
        for line in dump.splitlines()
        if line.strip()
        and not line.lstrip().startswith("--")
        and not line.lstrip().startswith("\\")
        and not _SESSION_SET.match(line)
    ]
    return "\n".join(sorted(lines))


def _pg_dump_major(version_argv: list[str]) -> int | None:
    """Best-effort major version for a pg_dump invocation; None if undetermined."""
    try:
        result = subprocess.run(
            [*version_argv, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(\d+)\.", result.stdout)
    return int(match.group(1)) if match else None


def _capture_schema_dump() -> tuple[str, str, int | None]:
    """Run pg_dump against the test database; return ``(raw, source, major)``.

    Prefers the docker-compose ``postgres`` container (the canonical pg16
    lineage the golden is generated from), so the host ``pg_dump`` major
    version is irrelevant on the primary path. Falls back to a local
    ``pg_dump`` binary only when docker is unavailable, returning a
    ``"host"`` source label and its major version so the caller can flag a
    version-skew artifact. Skips the test if neither route works.
    """
    pg_args = [
        "--schema-only",
        "--no-comments",
        "--no-owner",
        "-U",
        "riskd",
        "riskd",
    ]

    if shutil.which("docker") is not None:
        container_argv = ["docker", "compose", "exec", "-T", "postgres", "pg_dump"]
        result = subprocess.run(
            [*container_argv, *pg_args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            # Container is the canonical pg16 lineage by definition; its major
            # is uninteresting (the diagnostic only fires on the host fallback),
            # so skip the extra `--version` round-trip and report None.
            return result.stdout, "container", None

    if shutil.which("pg_dump") is not None:
        result = subprocess.run(
            ["pg_dump", "-h", "localhost", "-p", "5432", *pg_args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout, "host", _pg_dump_major(["pg_dump"])

    pytest.skip(
        "Neither docker compose exec pg_dump nor host pg_dump available; "
        "schema golden test requires one of these paths."
    )


def test_schema_matches_golden() -> None:
    """Current schema is byte-equivalent to the golden file under the normalizer."""
    raw_dump, source, major = _capture_schema_dump()
    actual = _normalize(raw_dump).rstrip("\n")

    expected = GOLDEN_PATH.read_text().rstrip("\n")

    if actual != expected:
        import difflib

        version_note = ""
        if source == "host" and major is not None and major != CANONICAL_PG_MAJOR:
            version_note = (
                f"\nNOTE: this dump came from a HOST pg_dump major {major}, but the golden "
                f"lineage is pg{CANONICAL_PG_MAJOR} (the docker-compose postgres container). Some or "
                f"all of this diff may be a pg_dump version artifact rather than a real schema "
                f"change. Regenerate / validate via the container-pinned command in this module's "
                f"docstring (docker compose exec -T postgres pg_dump …) before treating the diff "
                f"as schema drift.\n"
            )

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
            "per the docstring at the top of this test module.\n"
            f"{version_note}\n"
            f"Diff (truncated to first 8000 chars):\n{diff[:8000]}"
        )
