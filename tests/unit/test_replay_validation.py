"""Unit tests for the Phase 7A.1-rewritten replay orchestrator
(scripts/replay_validation.py).

Pure-Python exercises of the deterministic surfaces:
- corpus loader (NDJSON line-by-line, limit, missing-file,
  unknown-corpus, blank-line skipping) — now parameterized on
  corpus_dir (no module-level hardcoded path)
- aggregator (decision distribution, per-rule fire counts, latency
  percentiles)
- request_id format pattern (deterministic per-corpus per-index)
- CLI argument surface (--corpus-dir required; --compare mode;
  validation errors on missing arguments)
- aggregate-only output policy (no per_transaction in to_dict)
- _verify_corpus_dir fail-fast on missing files
- compare_results delta report
- rules_file_recorded round-trip

Does NOT exercise the network POST loop — integration-only
(implicit coverage during Phase 7B/7D replay execution).
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
    _verify_corpus_dir,
    compare_results,
    load_corpus,
    main,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures (the production NDJSON corpora are not committed; the
# operator runs the Phase 7 export script to /tmp/riskd-replay/ before any
# real run. Unit tests use minimal synthetic fixtures.)
# ---------------------------------------------------------------------------


def _write_synthetic_corpora(corpus_dir: Path) -> None:
    """Populate corpus_dir with minimal synthetic NDJSON files matching
    the three corpus names. Each line is a syntactically-valid
    BookingRequest payload (shape-level; not Pydantic-validated)."""
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for slug, filename in _CORPUS_FILES.items():
        lines = [
            json.dumps(
                {
                    "request_id": f"replay-{slug}-{i}",
                    "customer": {"external_id": f"cust-{i}"},
                    "user": {"external_id": f"user-{i}"},
                    "source_ip": "203.0.113.5",
                    "shipment": {},
                    "booking_ts": "2026-01-01T00:00:00+00:00",
                }
            )
            for i in range(3)
        ]
        (corpus_dir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Corpus loader
# ---------------------------------------------------------------------------


def test_corpus_loader_reads_each_of_the_three_corpora(tmp_path: Path) -> None:
    """Loader resolves a path under corpus_dir for each known corpus
    slug and yields one dict per NDJSON line."""
    _write_synthetic_corpora(tmp_path)
    for slug in _CORPUS_FILES:
        got = sum(1 for _ in load_corpus(slug, tmp_path))
        assert got == 3


def test_corpus_loader_respects_limit(tmp_path: Path) -> None:
    _write_synthetic_corpora(tmp_path)
    payloads = list(load_corpus("approved", tmp_path, limit=2))
    assert len(payloads) == 2


def test_corpus_loader_yields_dict_shape(tmp_path: Path) -> None:
    _write_synthetic_corpora(tmp_path)
    for payload in load_corpus("case3", tmp_path, limit=3):
        assert isinstance(payload, dict)
        assert "request_id" in payload


def test_corpus_loader_unknown_corpus_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown corpus"):
        list(load_corpus("bogus", tmp_path))


def test_corpus_loader_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    """If the specific corpus file does not exist under corpus_dir, the
    loader raises FileNotFoundError so the caller can surface a useful
    error rather than yielding zero records silently."""
    with pytest.raises(FileNotFoundError, match="corpus file missing"):
        list(load_corpus("approved", tmp_path))


def test_corpus_loader_skips_blank_lines(tmp_path: Path) -> None:
    body = '{"request_id":"r1"}\n\n{"request_id":"r2"}\n'
    (tmp_path / _CORPUS_FILES["approved"]).write_text(body, encoding="utf-8")
    payloads = list(load_corpus("approved", tmp_path))
    assert [p["request_id"] for p in payloads] == ["r1", "r2"]


def test_request_id_pattern_is_deterministic_per_corpus(tmp_path: Path) -> None:
    """Synthetic fixtures bake request_id = replay-{corpus}-{idx}. The
    deterministic format is the idempotency contract — second replay
    against the same tenant returns cached decisions."""
    _write_synthetic_corpora(tmp_path)
    for slug in _CORPUS_FILES:
        for idx, payload in enumerate(load_corpus(slug, tmp_path, limit=3)):
            assert payload["request_id"] == f"replay-{slug}-{idx}"


# ---------------------------------------------------------------------------
# _verify_corpus_dir
# ---------------------------------------------------------------------------


def test_verify_corpus_dir_missing_directory_raises(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-such-dir"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _verify_corpus_dir(nonexistent)


def test_verify_corpus_dir_missing_file_lists_in_error(tmp_path: Path) -> None:
    (tmp_path / _CORPUS_FILES["approved"]).write_text("", encoding="utf-8")
    # case2 + case3 still missing
    with pytest.raises(FileNotFoundError) as excinfo:
        _verify_corpus_dir(tmp_path)
    assert "case2_sample.ndjson" in str(excinfo.value)
    assert "case3_census.ndjson" in str(excinfo.value)


def test_verify_corpus_dir_all_present_does_not_raise(tmp_path: Path) -> None:
    _write_synthetic_corpora(tmp_path)
    _verify_corpus_dir(tmp_path)  # no raise


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
    assert next(iter(counts.keys())) == "high_fire"
    assert counts["high_fire"] == 4


def test_per_rule_fire_counts_empty_transactions_is_empty_dict() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    assert r.per_rule_fire_counts() == {}


# ---------------------------------------------------------------------------
# Aggregator: latency percentiles
# ---------------------------------------------------------------------------


def test_latency_summary_percentiles_match_nearest_rank() -> None:
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend(
        _txn("ALLOW", latency_ms=float(i)) for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    )
    summary = r.latency_summary()
    assert summary["p50"] == 5.0
    assert summary["p95"] == 9.0
    assert summary["p99"] == 9.0
    assert summary["mean"] == pytest.approx(5.5)


def test_latency_summary_empty_returns_nan() -> None:
    import math

    r = ReplayResults(corpus="approved", started_at="")
    summary = r.latency_summary()
    for key in ("p50", "p95", "p99", "mean"):
        assert math.isnan(summary[key])


def test_percentile_helper_handles_single_sample() -> None:
    assert _percentile([42.0], 0.50) == 42.0
    assert _percentile([42.0], 0.95) == 42.0
    assert _percentile([42.0], 0.99) == 42.0


# ---------------------------------------------------------------------------
# to_dict: aggregate-only output policy
# ---------------------------------------------------------------------------


def test_to_dict_round_trip_json_safe() -> None:
    r = ReplayResults(
        corpus="case3",
        started_at="2026-06-04T20:00:00+00:00",
        finished_at="2026-06-04T20:00:30+00:00",
        throttle_concurrency=50,
        rules_file_recorded="app/rules.yaml",
        requested=2,
        responses_200=2,
        errors=0,
    )
    r.transactions.extend(
        [_txn("BLOCK", rules=["case_3_compound"], latency_ms=10.0), _txn("ALLOW", latency_ms=12.0)]
    )
    payload = r.to_dict()
    serialized = json.dumps(payload)
    parsed = json.loads(serialized)
    assert parsed["corpus"] == "case3"
    assert parsed["totals"] == {"requested": 2, "responses_200": 2, "errors": 0}
    assert parsed["decision_distribution"] == {"ALLOW": 1, "REVIEW": 0, "BLOCK": 1}
    assert parsed["per_rule_fire_counts"] == {"case_3_compound": 1}
    assert parsed["rules_file_recorded"] == "app/rules.yaml"


def test_to_dict_does_not_emit_per_transaction_array() -> None:
    """Aggregate-only policy: the output JSON MUST NOT contain a
    per_transaction array. Per-record content stays in memory; only
    aggregates are serialized."""
    r = ReplayResults(corpus="approved", started_at="")
    r.transactions.extend([_txn("ALLOW"), _txn("BLOCK", rules=["x"])])
    payload = r.to_dict()
    assert "per_transaction" not in payload


def test_to_dict_emits_error_details_with_index_not_request_id() -> None:
    """error_details entries identify failures by integer index, not by
    request_id, so per-record content does not leak via the error path."""
    r = ReplayResults(corpus="approved", started_at="")
    r.error_details.append(
        {
            "index": 42,
            "status_code": 500,
            "body_snippet": "internal server error",
            "latency_ms": 12.3,
            "error": None,
        }
    )
    payload = r.to_dict()
    assert payload["error_details"][0]["index"] == 42
    assert "request_id" not in payload["error_details"][0]


# ---------------------------------------------------------------------------
# CLI argument validation
# ---------------------------------------------------------------------------


def test_cli_missing_corpus_dir_fails() -> None:
    rc = main(["--corpus", "approved", "--tenant-token", "T", "--out", "/tmp/x.json"])
    assert rc == 2


def test_cli_missing_corpus_fails(tmp_path: Path) -> None:
    rc = main(
        [
            "--corpus-dir",
            str(tmp_path),
            "--tenant-token",
            "T",
            "--out",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 2


def test_cli_missing_token_fails(tmp_path: Path) -> None:
    rc = main(
        [
            "--corpus",
            "approved",
            "--corpus-dir",
            str(tmp_path),
            "--out",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 2


def test_cli_missing_out_fails(tmp_path: Path) -> None:
    rc = main(["--corpus", "approved", "--corpus-dir", str(tmp_path), "--tenant-token", "T"])
    assert rc == 2


def test_cli_nonexistent_corpus_dir_fails(tmp_path: Path) -> None:
    rc = main(
        [
            "--corpus",
            "approved",
            "--corpus-dir",
            str(tmp_path / "no-such-dir"),
            "--tenant-token",
            "T",
            "--out",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 2


def test_cli_corpus_dir_with_missing_files_fails(tmp_path: Path) -> None:
    (tmp_path / _CORPUS_FILES["approved"]).write_text("", encoding="utf-8")
    rc = main(
        [
            "--corpus",
            "approved",
            "--corpus-dir",
            str(tmp_path),
            "--tenant-token",
            "T",
            "--out",
            str(tmp_path / "x.json"),
        ]
    )
    assert rc == 2


def test_cli_zero_concurrency_fails(tmp_path: Path) -> None:
    _write_synthetic_corpora(tmp_path)
    rc = main(
        [
            "--corpus",
            "approved",
            "--corpus-dir",
            str(tmp_path),
            "--tenant-token",
            "T",
            "--out",
            str(tmp_path / "x.json"),
            "--concurrency",
            "0",
        ]
    )
    assert rc == 2


# ---------------------------------------------------------------------------
# --compare mode
# ---------------------------------------------------------------------------


def _write_result_file(
    path: Path, *, requested: int, dist: dict[str, int], rules: dict[str, int]
) -> None:
    path.write_text(
        json.dumps(
            {
                "corpus": "approved",
                "started_at": "",
                "finished_at": "",
                "throttle_concurrency": 50,
                "rules_file_recorded": "app/rules.yaml",
                "totals": {"requested": requested, "responses_200": requested, "errors": 0},
                "decision_distribution": dist,
                "per_rule_fire_counts": rules,
                "latency_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0, "mean": 1.5},
                "error_details": [],
            }
        ),
        encoding="utf-8",
    )


def test_compare_results_emits_per_rule_delta_sorted_by_magnitude(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_result_file(
        a,
        requested=100,
        dist={"ALLOW": 50, "REVIEW": 40, "BLOCK": 10},
        rules={"rule_x": 80, "rule_y": 50},
    )
    _write_result_file(
        b,
        requested=100,
        dist={"ALLOW": 80, "REVIEW": 15, "BLOCK": 5},
        rules={"rule_x": 20, "rule_y": 40},
    )
    report = compare_results(a, b)
    assert report["a"]["requested"] == 100
    assert report["b"]["requested"] == 100
    # rule_x had the largest delta (80% → 20% = -60pp), rule_y smaller (50% → 40% = -10pp)
    assert report["per_rule_delta"][0]["rule"] == "rule_x"
    assert report["per_rule_delta"][0]["delta_pp"] == -60.0


def test_compare_results_handles_rules_in_only_one_file(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_result_file(
        a,
        requested=100,
        dist={"ALLOW": 100, "REVIEW": 0, "BLOCK": 0},
        rules={"only_in_a": 30},
    )
    _write_result_file(
        b,
        requested=100,
        dist={"ALLOW": 100, "REVIEW": 0, "BLOCK": 0},
        rules={"only_in_b": 50},
    )
    report = compare_results(a, b)
    rule_names = {r["rule"] for r in report["per_rule_delta"]}
    assert rule_names == {"only_in_a", "only_in_b"}


def test_cli_compare_mode_with_missing_file_fails(tmp_path: Path) -> None:
    rc = main(["--compare", str(tmp_path / "missing.json"), str(tmp_path / "also-missing.json")])
    assert rc == 2


def test_cli_compare_mode_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    _write_result_file(
        a,
        requested=10,
        dist={"ALLOW": 5, "REVIEW": 3, "BLOCK": 2},
        rules={"x": 4},
    )
    _write_result_file(
        b,
        requested=10,
        dist={"ALLOW": 8, "REVIEW": 2, "BLOCK": 0},
        rules={"x": 1},
    )
    out_path = tmp_path / "delta.json"
    rc = main(["--compare", str(a), str(b), "--out", str(out_path)])
    assert rc == 0
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed["a"]["requested"] == 10
    assert parsed["b"]["requested"] == 10
    assert parsed["per_rule_delta"][0]["rule"] == "x"
