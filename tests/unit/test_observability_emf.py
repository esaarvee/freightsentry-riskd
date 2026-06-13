"""Unit tests for `app.observability.emf_processor`.

Covers:
- `metric=True` events with a known spec produce a `_aws.CloudWatchMetrics`
  block with the correct namespace, dimensions, and metric definitions.
- `metric=False` events pass through unchanged.
- Events with no `metric` key pass through unchanged.
- Unknown event names with `metric=True` pass through (no `_aws` key)
  with a one-shot stderr warning, NOT dropped.
- `request_id` is NEVER promoted to dimensions for risk.evaluation
  (high-cardinality guard).
- `triggered_rule_count` is derived from `triggered_rules` length, not
  read from the event dict.
"""

from __future__ import annotations

from typing import Any

import pytest

from app import observability
from app.observability import EMF_NAMESPACE, emf_processor


@pytest.fixture(autouse=True)
def _reset_warning_state() -> None:
    """Reset the one-shot unknown-event warning set between tests."""
    observability._warned_unknown.clear()


def _emf_block(event: dict[str, Any]) -> dict[str, Any]:
    """Returns the EMF CloudWatchMetrics[0] dict or raises AssertionError
    if the event has no `_aws` block."""
    aws = event.get("_aws")
    assert aws is not None, f"expected _aws block, got: {event!r}"
    return aws["CloudWatchMetrics"][0]


def test_non_metric_event_passes_through_unchanged() -> None:
    event: dict[str, Any] = {"event": "some.log", "level": "info"}
    result = emf_processor(None, "info", event)
    assert "_aws" not in result
    assert result == {"event": "some.log", "level": "info"}


def test_event_without_metric_key_passes_through_unchanged() -> None:
    event: dict[str, Any] = {"event": "risk.evaluation", "tenant_id": 1}
    result = emf_processor(None, "info", event)
    assert "_aws" not in result


def test_metric_false_passes_through_unchanged() -> None:
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": False,
        "tenant_id": 1,
        "decision": "ALLOW",
    }
    result = emf_processor(None, "info", event)
    assert "_aws" not in result


def test_risk_evaluation_produces_emf_block_with_correct_shape() -> None:
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": True,
        "tenant_id": 42,
        "request_id": "REQ-001",
        "decision": "REVIEW",
        "score": 0.55,
        "account_prior": 0.10,
        "signal_score": 0.40,
        "maturity": 0.0,
        "triggered_rules": ["api_booking_from_unfamiliar_asn", "new_user_api_non_cloud"],
        "trust_score": 0.55,
        "flagged_count": 0,
    }
    result = emf_processor(None, "info", event)

    cw = _emf_block(result)
    assert cw["Namespace"] == EMF_NAMESPACE
    assert cw["Dimensions"] == [["tenant_id", "decision"]]

    metric_names = [m["Name"] for m in cw["Metrics"]]
    assert "score" in metric_names
    assert "account_prior" in metric_names
    assert "signal_score" in metric_names
    assert "maturity" in metric_names
    assert "trust_score" in metric_names
    assert "flagged_count" in metric_names
    assert "triggered_rule_count" in metric_names
    assert "count" in metric_names

    assert result["triggered_rule_count"] == 2
    assert result["count"] == 1


def test_risk_evaluation_request_id_not_in_dimensions() -> None:
    """High-cardinality guard: request_id must never be promoted to a
    CloudWatch dimension (would blow up billing + lookups)."""
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": True,
        "tenant_id": 42,
        "request_id": "REQ-must-not-appear-in-dimensions",
        "decision": "ALLOW",
        "score": 0.1,
        "account_prior": 0.1,
        "signal_score": 0.0,
        "maturity": 0.0,
        "triggered_rules": [],
        "trust_score": 0.5,
        "flagged_count": 0,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert "request_id" not in cw["Dimensions"][0]


def test_auth_success_produces_count_only_emf() -> None:
    event: dict[str, Any] = {
        "event": "auth.success",
        "metric": True,
        "tenant_id": 7,
        "role": "tenant",
        "token_hash_prefix": "abcd1234",
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [["tenant_id", "role"]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]
    assert result["count"] == 1


def test_auth_invalid_token_has_no_dimensions() -> None:
    """auth.invalid_token fires before tenant binding, so no tenant_id
    or role dimension exists. EMF Dimensions array must be present but
    empty-as-inner-list to satisfy the spec."""
    event: dict[str, Any] = {
        "event": "auth.invalid_token",
        "metric": True,
        "token_hash_prefix": "deadbeef",
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [[]]
    assert [m["Name"] for m in cw["Metrics"]] == ["count"]


def test_unknown_metric_event_passes_through_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    event: dict[str, Any] = {
        "event": "some.new.unclassified",
        "metric": True,
        "tenant_id": 1,
    }
    result = emf_processor(None, "info", event)
    assert "_aws" not in result
    captured = capsys.readouterr()
    assert "some.new.unclassified" in captured.err
    assert "METRIC_SPECS" in captured.err


def test_unknown_event_warning_fires_only_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    event: dict[str, Any] = {"event": "another.unknown", "metric": True}
    emf_processor(None, "info", event)
    emf_processor(None, "info", event)
    emf_processor(None, "info", event)
    captured = capsys.readouterr()
    occurrences = captured.err.count("another.unknown")
    assert occurrences == 1, f"expected 1 warning, saw {occurrences}"


def test_tenant_config_cache_miss_carries_cache_size_metric() -> None:
    event: dict[str, Any] = {
        "event": "tenant_config.cache.miss",
        "metric": True,
        "tenant_id": 99,
        "cache_size": 5,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    metric_names = [m["Name"] for m in cw["Metrics"]]
    assert "cache_size" in metric_names
    assert "count" in metric_names
    cache_size_metric = next(m for m in cw["Metrics"] if m["Name"] == "cache_size")
    assert cache_size_metric["Unit"] == "Count"


def test_modification_evaluation_dimensions_and_metrics() -> None:
    event: dict[str, Any] = {
        "event": "modification.evaluation",
        "metric": True,
        "tenant_id": 1,
        "decision": "BLOCK",
        "modification_type": "value",
        "score": 0.9,
        "account_prior": 0.5,
        "signal_score": 0.8,
        "maturity": 0.7,
        "triggered_rules": ["r1", "r2", "r3"],
        "modification_velocity_1h": 2,
        "modification_velocity_24h": 5,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [["tenant_id", "decision", "modification_type"]]
    assert {m["Name"] for m in cw["Metrics"]} == {
        "score",
        "account_prior",
        "signal_score",
        "maturity",
        "modification_velocity_1h",
        "modification_velocity_24h",
        "triggered_rule_count",
        "count",
    }
    assert result["triggered_rule_count"] == 3


def test_score_metric_has_no_unit_key() -> None:
    """unit='None' in MetricSpec must render as an EMF metric WITHOUT a
    `Unit` key (not a literal "None" string — CloudWatch would reject)."""
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": True,
        "tenant_id": 1,
        "decision": "ALLOW",
        "score": 0.5,
        "account_prior": 0.1,
        "signal_score": 0.0,
        "maturity": 0.0,
        "triggered_rules": [],
        "trust_score": 0.5,
        "flagged_count": 0,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    score_entry = next(m for m in cw["Metrics"] if m["Name"] == "score")
    assert "Unit" not in score_entry
    flagged_entry = next(m for m in cw["Metrics"] if m["Name"] == "flagged_count")
    assert flagged_entry["Unit"] == "Count"


def test_missing_metric_field_is_skipped_not_synthesized() -> None:
    """If an event omits a metric field declared in MetricSpec, the
    processor must skip it (not synthesize zero, not raise)."""
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": True,
        "tenant_id": 1,
        "decision": "ALLOW",
        "score": 0.5,
        "signal_score": 0.0,
        "maturity": 0.0,
        "triggered_rules": [],
        "trust_score": 0.5,
        "flagged_count": 0,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert "score" in metric_names
    assert "account_prior" not in metric_names


def test_triggered_rules_none_yields_zero_count() -> None:
    """`_len_or_zero` fallback: triggered_rules=None must not crash;
    triggered_rule_count derives to 0."""
    event: dict[str, Any] = {
        "event": "risk.evaluation",
        "metric": True,
        "tenant_id": 1,
        "decision": "ALLOW",
        "score": 0.1,
        "account_prior": 0.1,
        "signal_score": 0.0,
        "maturity": 0.0,
        "triggered_rules": None,
        "trust_score": 0.5,
        "flagged_count": 0,
    }
    result = emf_processor(None, "info", event)
    assert result["triggered_rule_count"] == 0


def test_non_string_event_value_passes_through_unchanged() -> None:
    """Defensive: metric=True with event=None (or non-string) must NOT
    crash. Returns dict unchanged with no _aws block."""
    event: dict[str, Any] = {"event": None, "metric": True, "tenant_id": 1}
    result = emf_processor(None, "info", event)
    assert "_aws" not in result


def test_synthetic_count_does_not_clobber_existing_count() -> None:
    """setdefault contract: an event that legitimately carries its own
    count value (e.g. a future batched event) must not be silently
    overwritten by the synthetic count=1."""
    event: dict[str, Any] = {
        "event": "auth.success",
        "metric": True,
        "tenant_id": 1,
        "role": "tenant",
        "count": 7,
    }
    result = emf_processor(None, "info", event)
    assert result["count"] == 7


@pytest.mark.parametrize("event_name", list(observability.METRIC_SPECS.keys()))
def test_every_metric_spec_produces_emf_block(event_name: str) -> None:
    """Forward-compat: every event_name in METRIC_SPECS must emit an
    EMF block. Synthesizes a minimal event_dict with values for every
    declared dimension key. Adding a new MetricSpec entry automatically
    extends this test to cover it."""
    spec = observability.METRIC_SPECS[event_name]
    event: dict[str, Any] = {"event": event_name, "metric": True}
    for dim in spec.dimensions:
        event[dim] = "synthetic"

    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Namespace"] == EMF_NAMESPACE
    expected_dims = list(spec.dimensions) if spec.dimensions else []
    assert cw["Dimensions"] == [expected_dims]
    if spec.synthetic_count:
        names = {m["Name"] for m in cw["Metrics"]}
        assert "count" in names


# ---------------------------------------------------------------------------
# enrich.refresh.{success,failure,skipped_sanity_floor}
# ---------------------------------------------------------------------------


def test_enrich_refresh_success_emits_emf_with_dims_and_metrics() -> None:
    """enrich.refresh.success: source_name dimension; duration_ms +
    bytes_written + count metrics."""
    event: dict[str, Any] = {
        "event": "enrich.refresh.success",
        "metric": True,
        "source_name": "firehol_level1",
        "duration_ms": 1234.5,
        "bytes_written": 524288,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [["source_name"]]
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert metric_names == {"duration_ms", "bytes_written", "count"}
    metric_units = {m["Name"]: m.get("Unit") for m in cw["Metrics"]}
    assert metric_units["duration_ms"] == "Milliseconds"
    assert metric_units["bytes_written"] == "Bytes"
    assert metric_units["count"] == "Count"
    # source_name stays in the event dict (CloudWatch uses it for the
    # dimension lookup at ingest time)
    assert result["source_name"] == "firehol_level1"


@pytest.mark.parametrize(
    "failure_class",
    ["network", "parse_error", "rate_limited", "upstream_html", "other"],
)
def test_enrich_refresh_failure_dim_taxonomy(failure_class: str) -> None:
    """enrich.refresh.failure dimensions: source_name + failure_class.
    Every value in the failure_class taxonomy lands as a discrete EMF
    dimension value, enabling per-class alerting."""
    event: dict[str, Any] = {
        "event": "enrich.refresh.failure",
        "metric": True,
        "source_name": "ip2proxy",
        "failure_class": failure_class,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [["source_name", "failure_class"]]
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert metric_names == {"count"}
    assert result["failure_class"] == failure_class


def test_enrich_refresh_skipped_sanity_floor_emits_byte_metrics() -> None:
    """enrich.refresh.skipped_sanity_floor: source_name dimension;
    bytes_attempted + floor_bytes + count metrics. The byte values let
    dashboards trend "how far below floor is upstream serving" over
    time."""
    event: dict[str, Any] = {
        "event": "enrich.refresh.skipped_sanity_floor",
        "metric": True,
        "source_name": "ip2proxy_extracted",
        "bytes_attempted": 1024,
        "floor_bytes": 500_000_000,
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    assert cw["Dimensions"] == [["source_name"]]
    metric_units = {m["Name"]: m.get("Unit") for m in cw["Metrics"]}
    assert metric_units == {
        "bytes_attempted": "Bytes",
        "floor_bytes": "Bytes",
        "count": "Count",
    }


def test_enrich_refresh_emf_does_not_leak_license_keys() -> None:
    """Defense-in-depth: even if a future change accidentally passed
    license-key bytes into a log event, the EMF processor only promotes
    fields listed in the MetricSpec to dimensions / metric values. The
    sentinel key in an unauthorized field should not surface in the
    EMF block."""
    sentinel = "SENTINEL-LICENSE-KEY-DO-NOT-LOG-1234567890"
    event: dict[str, Any] = {
        "event": "enrich.refresh.success",
        "metric": True,
        "source_name": "ip2proxy",
        "duration_ms": 1000.0,
        "bytes_written": 1_000_000,
        # Hostile injection attempt:
        "license_key": sentinel,
        "url": f"https://download.example.com/?token={sentinel}",
    }
    result = emf_processor(None, "info", event)
    cw = _emf_block(result)
    # Dimensions: only source_name (license_key is NOT a declared dim)
    assert cw["Dimensions"] == [["source_name"]]
    # Metrics: declared 2 + synthetic count (license_key not promoted)
    metric_names = {m["Name"] for m in cw["Metrics"]}
    assert metric_names == {"duration_ms", "bytes_written", "count"}
    # The injected fields pass through to the log record (so an emergency
    # `caplog` capture would see them — that's the structlog convention)
    # but the EMF metric pipeline doesn't promote them to dimensions or
    # metric values, which is the disclosure path that matters at scale.
    import json as _json

    emf_json = _json.dumps(result["_aws"])
    assert sentinel not in emf_json, (
        "EMF block must not contain license-key strings — only the "
        "explicitly-declared MetricSpec fields should reach EMF"
    )
