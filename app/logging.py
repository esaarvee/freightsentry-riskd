"""structlog setup — JSON to stdout, with metric: true tag for the metrics sink.

Configured once at app lifespan. Application code obtains a logger via
`structlog.get_logger(__name__)`; the underlying handler emits JSON lines
to stdout with ISO-8601 timestamps and a `level` field. Counters and
histograms are emitted as structured logs with `metric: true` so the
CloudWatch wiring can route them without re-instrumenting call sites.
"""

import logging
import sys

import structlog

from app.observability import emf_processor


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog. Idempotent — safe to call multiple times."""
    level_int = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # EMF processor: events with metric=True gain a
            # `_aws.CloudWatchMetrics` block before JSONRenderer
            # serializes the line. Non-metric events pass through.
            emf_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_int),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
