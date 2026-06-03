"""Unit tests for the Phase 6C replay orchestrator
(scripts/replay_validation.py).

Pure-Python exercises of the deterministic surfaces:
- corpus loader (NDJSON line-by-line, limit, missing-file,
  unknown-corpus, blank-line skipping)
- aggregator (decision distribution, per-rule fire counts, latency
  percentiles)
- request_id format pattern (deterministic across corpora)

Does NOT exercise the network POST loop — that's integration-only
(implicit coverage during 6C.4 replay execution).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_validation import (
    _CORPUS_FILES,
    ReplayResults,
    TransactionResult,
    _percentile,
    load_corpus,
)

# ---------------------------------------------------------------------------
# Corpus loader
# ---------------------------------------------------------------------------


def test_corpus_loader_reads_each_of_the_three_corpora() -> None:
    """The three production corpus files exist + load successfully.

    Validates that the loader's path-resolution + NDJSON streaming work
    end-to-end against the actual files committed in 6C.1. Each yields
    the documented record count (10000 / 500 / 95).
    """
    expected = {"approved": 10_000, "case2": 500, "case3": 95}
    for corpus, want in expected.items():
        got = sum(1 for _ in load_corpus(corpus))
        assert got == want, f"corpus {corpus}: expected {want}, got {got}"


def test_corpus_loader_respects_limit() -> None:
    """`limit=N` stops streaming after N payloads regardless of file
    size. Smoke runs and reviewer-facing checks use small limits."""
    payloads = list(load_corpus("approved", limit=5))
    assert len(payloads) == 5


def test_corpus_loader_yields_valid_booking_request_shape() -> None:
    """Each yielded payload is a dict with the required BookingRequest
    top-level fields. Guards against an NDJSON decode regression
    silently producing strings or partial objects."""
    for payload in load_corpus("case3", limit=10):
        assert isinstance(payload, dict)
        assert "request_id" in payload
        assert "customer" in payload
        assert "user" in payload
        assert "source_ip" in payload
        assert "shipment" in payload
        assert "booking_ts" in payload


def test_corpus_payloads_validate_against_booking_request_pydantic_model() -> None:
    """Full Pydantic round-trip on the first records of each corpus.
    Catches schema drift between the sibling-repo export contract and
    the consuming `BookingRequest` model — type drift, required-field
    omissions, ISO-3166 validation regressions on the case-3 country
    fields, ISO-4217 currency regressions, etc. The shape-only test
    above would let invalid-typed payloads slip past.

    Limit=5 per corpus is sufficient to catch schema drift; full
    10K-payload validation would slow the unit suite without
    additional coverage value (corpus is uniform within each file)."""
    from app.models import BookingRequest

    for corpus in _CORPUS_FILES:
        for payload in load_corpus(corpus, limit=5):
            BookingRequest.model_validate(payload)


def test_corpus_loader_unknown_corpus_raises() -> None:
    with pytest.raises(ValueError, match="unknown corpus"):
        list(load_corpus("bogus"))


def test_corpus_loader_missing_file_raises_filenotfound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the corpus file does not exist (e.g. operator didn't run the
    sibling-repo export), the loader raises FileNotFoundError so the
    CLI handler can surface a useful error rather than yielding zero
    records silently."""
    monkeypatch.setattr("scripts.replay_validation._CORPUS_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="corpus file missing"):
        list(load_corpus("approved"))


def test_corpus_loader_skips_blank_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive against editor-added trailing blank lines in NDJSON.
    The loader skips blank lines rather than raising json.JSONDecodeError."""
    monkeypatch.setattr("scripts.replay_validation._CORPUS_DIR", tmp_path)
    monkeypatch.setitem(_CORPUS_FILES, "fake", "fake.ndjson")
    body = (
        '{"request_id":"r1","customer":{},"user":{},"source_ip":"","shipment":{},"booking_ts":""}\n'
        "\n"  # blank line
        '{"request_id":"r2","customer":{},"user":{},"source_ip":"","shipment":{},"booking_ts":""}\n'
    )
    (tmp_path / "fake.ndjson").write_text(body, encoding="utf-8")
    payloads = list(load_corpus("fake"))
    assert [p["request_id"] for p in payloads] == ["r1", "r2"]


# ---------------------------------------------------------------------------
# Request_id format pattern
# ---------------------------------------------------------------------------


def test_request_id_pattern_is_deterministic_per_corpus() -> None:
    """The 6C.1 export bakes request_id = "replay-{corpus}-{idx}" into
    every payload. This deterministic format is the idempotency contract
    — second replay against the same tenant returns cached decisions."""
    for corpus in _CORPUS_FILES:
        for idx, payload in enumerate(load_corpus(corpus, limit=10)):
            assert payload["request_id"] == f"replay-{corpus}-{idx}"


# ---------------------------------------------------------------------------
# Aggregator: decision distribution
# ---------------------------------------------------------------------------


def _txn(
    decision: str, *, rules: list[str] | None = None, latency_ms: float = 1.0
) -> TransactionResult:
    return TransactionResult(
        request_id=f"t-{decision}-{latency_ms}",
        decision=decision,
        score=0.5,
        classification="GREEN",
        triggered_rules=list(rules or []),
        latency_ms=latency_ms,
    )


def test_decision_distribution_counts_each_band() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend(
        [_txn("ALLOW"), _txn("ALLOW"), _txn("REVIEW"), _txn("BLOCK"), _txn("BLOCK")]
    )
    assert r.decision_distribution() == {"ALLOW": 2, "REVIEW": 1, "BLOCK": 2}


def test_decision_distribution_zero_band_appears_as_zero_not_missing() -> None:
    """Operator-facing reading: a corpus that fired zero BLOCKs should
    still emit `"BLOCK": 0` in the JSON output so the reading is
    explicit-zero, not absent-key. Guards against a downstream summary
    that conflates 'no BLOCKs' with 'this band wasn't measured'."""
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend([_txn("ALLOW"), _txn("ALLOW"), _txn("ALLOW")])
    dist = r.decision_distribution()
    assert dist == {"ALLOW": 3, "REVIEW": 0, "BLOCK": 0}


def test_decision_distribution_empty_transactions_returns_all_zeros() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    assert r.decision_distribution() == {"ALLOW": 0, "REVIEW": 0, "BLOCK": 0}


# ---------------------------------------------------------------------------
# Aggregator: per-rule fire counts
# ---------------------------------------------------------------------------


def test_per_rule_fire_counts_aggregates_across_transactions() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend(
        [
            _txn("ALLOW", rules=["r_a"]),
            _txn("REVIEW", rules=["r_a", "r_b"]),
            _txn("BLOCK", rules=["r_a", "r_b", "r_c"]),
        ]
    )
    assert r.per_rule_fire_counts() == {"r_a": 3, "r_b": 2, "r_c": 1}


def test_per_rule_fire_counts_sorted_descending() -> None:
    """Operator scanning the JSON should see the loudest rules first."""
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend(
        [
            _txn("ALLOW", rules=["low_fire"]),
            _txn("REVIEW", rules=["high_fire"]),
            _txn("BLOCK", rules=["high_fire", "high_fire"]),
            _txn("REVIEW", rules=["mid_fire", "high_fire"]),
        ]
    )
    counts = r.per_rule_fire_counts()
    assert list(counts.keys()) == ["high_fire", "low_fire", "mid_fire"]
    assert counts["high_fire"] == 4
    assert counts["low_fire"] == 1
    assert counts["mid_fire"] == 1


def test_per_rule_fire_counts_empty_transactions_is_empty_dict() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    assert r.per_rule_fire_counts() == {}


# ---------------------------------------------------------------------------
# Aggregator: latency percentiles
# ---------------------------------------------------------------------------


def test_latency_summary_percentiles_match_nearest_rank() -> None:
    """`_percentile` uses k = int(p * (len - 1)) — nearest-rank style,
    not interpolated. Pin the contract so a future refactor doesn't
    silently switch to interpolation."""
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend(
        _txn("ALLOW", latency_ms=float(i)) for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    )
    summary = r.latency_summary()
    # _percentile on sorted [1..10] at p=0.50 → idx 4 (= 5.0)
    # p=0.95 → idx 8 (= 9.0); p=0.99 → idx 8 (= 9.0).
    assert summary["p50"] == 5.0
    assert summary["p95"] == 9.0
    assert summary["p99"] == 9.0
    # mean = 5.5 regardless of percentile style.
    assert summary["mean"] == pytest.approx(5.5)


def test_latency_summary_empty_returns_nan() -> None:
    """Empty corpus run (zero successful 200s) should return NaN, NOT
    raise. The CLI's stderr summary printf would emit `nan` rather
    than crashing — operator can investigate the error_details."""
    import math

    r = ReplayResults(corpus="approved", started_at="")
    summary = r.latency_summary()
    for key in ("p50", "p95", "p99", "mean"):
        assert math.isnan(summary[key])


def test_percentile_helper_handles_single_sample() -> None:
    """Degenerate case the aggregator may hit if only one record
    succeeds — `_percentile([x], 0.95)` should not raise."""
    assert _percentile([42.0], 0.50) == 42.0
    assert _percentile([42.0], 0.95) == 42.0
    assert _percentile([42.0], 0.99) == 42.0


# ---------------------------------------------------------------------------
# to_dict round-trip
# ---------------------------------------------------------------------------


def test_to_dict_round_trip_json_safe() -> None:
    """The output dict must be json-serializable end to end — guards
    against a non-JSON type slipping into the aggregator (e.g., a
    Decimal or datetime that requires custom encoding)."""
    r = ReplayResults(
        corpus="case3",
        started_at="2026-06-03T20:00:00+00:00",
        finished_at="2026-06-03T20:00:30+00:00",
        throttle_concurrency=50,
        requested=2,
        responses_200=2,
        errors=0,
    )
    r.transactions.extend(
        [_txn("BLOCK", rules=["case_3_compound"], latency_ms=10.0), _txn("ALLOW", latency_ms=12.0)]
    )
    payload = r.to_dict()
    # Json round-trip must not raise.
    serialized = json.dumps(payload)
    parsed = json.loads(serialized)
    assert parsed["corpus"] == "case3"
    assert parsed["totals"] == {"requested": 2, "responses_200": 2, "errors": 0}
    assert parsed["decision_distribution"] == {"ALLOW": 1, "REVIEW": 0, "BLOCK": 1}
    assert parsed["per_rule_fire_counts"] == {"case_3_compound": 1}
    assert len(parsed["per_transaction"]) == 2
