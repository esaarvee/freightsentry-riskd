"""structlog setup — JSON to stdout, with metric: true tag for Phase 5 sink.

Configured once at app lifespan. Application code obtains a logger via
`structlog.get_logger(__name__)`; the underlying handler emits JSON lines
to stdout with ISO-8601 timestamps and a `level` field. Counters and
histograms are emitted as structured logs with `metric: true` so Phase 5
CloudWatch wiring can route them without re-instrumenting call sites.
"""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog. Idempotent — safe to call multiple times."""
    level_int = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
