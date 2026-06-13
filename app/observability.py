"""CloudWatch Embedded Metric Format (EMF) processor for structlog.

Detects `metric=True` events and reshapes the event dict to
carry a CloudWatch EMF block (`_aws.CloudWatchMetrics`) alongside the
existing fields. Non-metric events flow through unchanged.

The EMF spec is JSON-based and read directly by the CloudWatch Logs
agent in production: lines that carry a `_aws` block with a
`CloudWatchMetrics` array are ingested as metric points; lines without
that block continue to be plain log lines. This module emits both
shapes through the same stdout sink — no separate metric pipeline.

Dimensions vs metric values: dimensions are LOW-cardinality
identifiers (`tenant_id`, `decision`, `modification_type`) that
CloudWatch can group by. Metric values are NUMERIC measurements
(`score`, `account_prior`, `triggered_rule_count`). High-cardinality
fields like `request_id` are KEPT in the log line as regular fields
but are NEVER promoted to dimensions (would blow up CloudWatch
billing + lookups).

The MetricSpec table below is the single source of truth for which
fields each event family publishes as dimensions vs metrics. Events
with `metric=True` but no MetricSpec entry pass through with a
warning so forward-compatibility doesn't drop metrics for new
classifications.
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any

EMF_NAMESPACE: str = "FreightSentry/RiskD"


@dataclass(frozen=True)
class MetricSpec:
    """Per-event-family metric definition.

    `dimensions` — list of event-dict keys whose values become EMF
    Dimensions. Order matters (CloudWatch hashes the tuple).

    `metrics` — list of (field_name, unit) pairs whose values become
    EMF MetricValues. `unit` is the CloudWatch unit string (e.g.
    "Count", "Milliseconds", "None"); use "None" for unitless metrics
    like normalized scores.

    `synthetic_count` — if True, emit a constant `count=1` metric
    value alongside any others. Useful for events that fire as
    "this happened once" but carry no inherent numeric measurement
    (auth.success, auth.invalid_token, cache.hit, idempotent_replay).
    """

    dimensions: tuple[str, ...] = ()
    metrics: tuple[tuple[str, str], ...] = ()
    synthetic_count: bool = False


# Single source of truth for which event families publish what.
# Keep in sync with the 20 unique `metric=True` event families across
# app/auth.py, app/api/{booking,modification,feedback,admin}.py,
# app/tenant_config.py, app/tenant_config_cache.py, app/enrich.py.
# Adding a new metric=True call site requires a corresponding entry
# here — otherwise the processor emits a one-shot stderr warning and
# the event falls through without an EMF block.
METRIC_SPECS: dict[str, MetricSpec] = {
    "risk.evaluation": MetricSpec(
        dimensions=("tenant_id", "decision"),
        metrics=(
            ("score", "None"),
            ("account_prior", "None"),
            ("signal_score", "None"),
            ("maturity", "None"),
            ("trust_score", "None"),
            ("flagged_count", "Count"),
        ),
        synthetic_count=True,
    ),
    "modification.evaluation": MetricSpec(
        dimensions=("tenant_id", "decision", "modification_type"),
        metrics=(
            ("score", "None"),
            ("account_prior", "None"),
            ("signal_score", "None"),
            ("maturity", "None"),
            ("modification_velocity_1h", "Count"),
            ("modification_velocity_24h", "Count"),
        ),
        synthetic_count=True,
    ),
    "auth.success": MetricSpec(
        dimensions=("tenant_id", "role"),
        synthetic_count=True,
    ),
    "auth.invalid_token": MetricSpec(synthetic_count=True),
    "auth.admin_required_denied": MetricSpec(
        dimensions=("tenant_id", "role"),
        synthetic_count=True,
    ),
    "auth.carveout_active": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "tenant_config.cache.hit": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "tenant_config.cache.miss": MetricSpec(
        dimensions=("tenant_id",),
        metrics=(("cache_size", "Count"),),
        synthetic_count=True,
    ),
    "tenant_config.loaded": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "tenant_config.value_caps.fallback": MetricSpec(
        dimensions=("tenant_id", "currency"),
        synthetic_count=True,
    ),
    "feedback.applied": MetricSpec(
        dimensions=("tenant_id", "label"),
        metrics=(
            ("flag_delta", "Count"),
            ("fraud_delta", "Count"),
            ("dimensions_written", "Count"),
        ),
        synthetic_count=True,
    ),
    "feedback.monotonicity_skip": MetricSpec(
        dimensions=("tenant_id", "new_label", "prior_label"),
        synthetic_count=True,
    ),
    "feedback.monotonicity_skip_post_lock": MetricSpec(
        dimensions=("tenant_id", "new_label", "prior_label"),
        synthetic_count=True,
    ),
    "feedback.idempotent_replay": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "booking.idempotent_replay": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "modification.idempotent_replay": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
    "enrich.cache_hit": MetricSpec(synthetic_count=True),
    "enrich.cache_miss": MetricSpec(synthetic_count=True),
    # A loaded source's binary DB was present but failed to parse/open
    # (corrupt / version-incompatible). `source` dimension breaks the
    # count out per source (maxmind_city / maxmind_asn / ip2proxy). This
    # alarm guards the fail-open source-load path: it surfaces a loaded
    # source whose binary DB was present but failed to parse/open,
    # closing an otherwise-silent suppression of that failure.
    "enrich.source_load_failed": MetricSpec(
        dimensions=("source",),
        synthetic_count=True,
    ),
    "enrich.refresh.success": MetricSpec(
        dimensions=("source_name",),
        metrics=(
            ("duration_ms", "Milliseconds"),
            ("bytes_written", "Bytes"),
        ),
        synthetic_count=True,
    ),
    "enrich.refresh.failure": MetricSpec(
        dimensions=("source_name", "failure_class"),
        synthetic_count=True,
    ),
    "enrich.refresh.skipped_sanity_floor": MetricSpec(
        dimensions=("source_name",),
        metrics=(
            ("bytes_attempted", "Bytes"),
            ("floor_bytes", "Bytes"),
        ),
        synthetic_count=True,
    ),
    "admin.decision_lookup": MetricSpec(
        dimensions=("tenant_id", "request_type"),
        synthetic_count=True,
    ),
    "admin.customer_baseline_lookup": MetricSpec(
        dimensions=("tenant_id",),
        synthetic_count=True,
    ),
}


_warned_unknown: set[str] = set()

# Event families that derive a `triggered_rule_count` metric from the
# `triggered_rules` list field in their event dict. Table-driven so
# adding a new evaluation event family is a one-line change.
_DERIVED_TRIGGERED_RULE_COUNT: frozenset[str] = frozenset(
    {"risk.evaluation", "modification.evaluation"}
)


def _len_or_zero(value: Any) -> int:
    """Safe len() for sequence-like values (triggered_rules list).
    Returns 0 for None or non-sized values."""
    try:
        return len(value)
    except TypeError:
        return 0


def emf_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor: if `metric=True`, add an EMF `_aws` block
    using the MetricSpec for the event name. Non-metric events return
    unchanged.

    Unknown metric event names pass through with a one-shot warning
    (logged via stderr so the warning itself doesn't recurse through
    this processor)."""
    if event_dict.get("metric") is not True:
        return event_dict

    event_name = event_dict.get("event")
    if not isinstance(event_name, str):
        return event_dict

    spec = METRIC_SPECS.get(event_name)
    if spec is None:
        if event_name not in _warned_unknown:
            _warned_unknown.add(event_name)
            import sys

            print(
                f"emf_processor: unknown metric event {event_name!r}; "
                "add to METRIC_SPECS in app/observability.py to enable EMF",
                file=sys.stderr,
            )
        return event_dict

    metric_definitions: list[dict[str, str]] = []
    for field, unit in spec.metrics:
        if field not in event_dict:
            continue
        if unit == "None":
            metric_definitions.append({"Name": field})
        else:
            metric_definitions.append({"Name": field, "Unit": unit})

    if spec.synthetic_count:
        # setdefault rather than unconditional write so a future caller
        # that legitimately emits its own `count=N` (e.g. batched event)
        # isn't silently overwritten.
        event_dict.setdefault("count", 1)
        metric_definitions.append({"Name": "count", "Unit": "Count"})

    if event_name in _DERIVED_TRIGGERED_RULE_COUNT and "triggered_rules" in event_dict:
        event_dict["triggered_rule_count"] = _len_or_zero(event_dict["triggered_rules"])
        metric_definitions.append({"Name": "triggered_rule_count", "Unit": "Count"})

    timestamp_ms = int(time.time() * 1000)

    event_dict["_aws"] = {
        "Timestamp": timestamp_ms,
        "CloudWatchMetrics": [
            {
                "Namespace": EMF_NAMESPACE,
                "Dimensions": [list(spec.dimensions)] if spec.dimensions else [[]],
                "Metrics": metric_definitions,
            }
        ],
    }

    return event_dict
