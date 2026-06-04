#!/usr/bin/env python3
"""Replay-validation orchestrator (Phase 7A.1 rewrite).

Reads a corpus NDJSON file from an operator-supplied directory, POSTs
each payload to the local freightsentry-riskd booking endpoint, captures
the decision + score + triggered_rules + latency, and writes AGGREGATE
results (decision counts, per-rule fire counts, latency percentiles) to
a JSON file. Throttled at 50 concurrent in-flight requests by default.

Per Phase 7's "aggregate stats only" policy, this orchestrator does NOT
emit per-record content in the output JSON. Per-record content (request
id, triggered_rules list per booking) stays in memory during the run
and is summarized into aggregates before serialization.

The corpus files are NDJSON (one BookingRequest payload per line). The
corpus directory is supplied via --corpus-dir (no hardcoded path).
Operator typically populates the directory via the Phase 7 export
script (scripts/calibration/export_from_freight_risk.py) writing to
/tmp/riskd-replay/.

Idempotency: request_id is deterministic per-corpus per-index
(replay-{corpus}-{idx}), so a second run against the same tenant
returns the cached decision via the existing idempotency-replay path.
Re-runs are non-destructive.

Usage (single replay):
    python scripts/replay_validation.py \\
        --corpus {approved|case2|case3} \\
        --corpus-dir /tmp/riskd-replay/ \\
        --tenant-token $REPLAY_TENANT_TOKEN \\
        --out /tmp/result.json \\
        [--rules app/rules.yaml] \\
        [--base-url http://localhost:8000] \\
        [--concurrency 50] \\
        [--limit N]

Usage (compare two pre-computed result files):
    python scripts/replay_validation.py --compare RESULT_A.json RESULT_B.json

--rules records WHICH rule file the operator believes the server has
loaded; it is NOT a runtime swap (the FastAPI app loads rules at
lifespan startup). Variant orchestration (Phase 7B run_variants.py)
restarts the app between variants and passes the variant path via
--rules so the output JSON carries the variant identity for audit.

Output JSON shape (aggregate-only):
    {
        "corpus": str,
        "started_at": iso8601 str,
        "finished_at": iso8601 str,
        "throttle_concurrency": int,
        "rules_file_recorded": str,
        "totals": {
            "requested": int,
            "responses_200": int,
            "errors": int,
        },
        "decision_distribution": {"ALLOW": int, "REVIEW": int, "BLOCK": int},
        "per_rule_fire_counts": {rule_name: int, ...},
        "latency_ms": {"p50": float, "p95": float, "p99": float, "mean": float},
        "error_details": [
            {"index": int, "status_code": int | null, "body_snippet": str,
             "latency_ms": float, "error": str | null},
            ...
        ]
    }
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

_CORPUS_FILES: dict[str, str] = {
    "approved": "approved_jan_mar.ndjson",
    "case2": "case2_sample.ndjson",
    "case3": "case3_census.ndjson",
}


@dataclass
class TransactionResult:
    request_id: str
    decision: str
    score: float
    classification: str
    triggered_rules: list[str]
    latency_ms: float


@dataclass
class ReplayResults:
    corpus: str
    started_at: str
    finished_at: str = ""
    throttle_concurrency: int = 50
    rules_file_recorded: str = ""
    requested: int = 0
    responses_200: int = 0
    errors: int = 0
    transactions: list[TransactionResult] = field(default_factory=list)
    error_details: list[dict[str, Any]] = field(default_factory=list)
    # Phase 7C.9 warmup-vs-measurement split. Warmup decisions are
    # recorded but EXCLUDED from decision_distribution / per_rule
    # _fire_counts / latency_summary — those aggregates reflect
    # measurement-only outcomes. Warmup's side-effect (baseline
    # population) is the load-bearing operation for warmup; its
    # decision data is captured here for diagnostic completeness.
    warmup_requested: int = 0
    warmup_responses_200: int = 0
    warmup_errors: int = 0
    warmup_transactions: list[TransactionResult] = field(default_factory=list)

    def decision_distribution(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for t in self.transactions:
            counter[t.decision] += 1
        return {band: counter.get(band, 0) for band in ("ALLOW", "REVIEW", "BLOCK")}

    def per_rule_fire_counts(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for t in self.transactions:
            counter.update(t.triggered_rules)
        return dict(sorted(counter.items(), key=lambda kv: kv[1], reverse=True))

    def latency_summary(self) -> dict[str, float]:
        if not self.transactions:
            return {
                "p50": float("nan"),
                "p95": float("nan"),
                "p99": float("nan"),
                "mean": float("nan"),
            }
        latencies = sorted(t.latency_ms for t in self.transactions)
        return {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "mean": statistics.fmean(latencies),
        }

    def warmup_decision_distribution(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for t in self.warmup_transactions:
            counter[t.decision] += 1
        return {band: counter.get(band, 0) for band in ("ALLOW", "REVIEW", "BLOCK")}

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "throttle_concurrency": self.throttle_concurrency,
            "rules_file_recorded": self.rules_file_recorded,
            "totals": {
                "requested": self.requested,
                "responses_200": self.responses_200,
                "errors": self.errors,
            },
            "decision_distribution": self.decision_distribution(),
            "per_rule_fire_counts": self.per_rule_fire_counts(),
            "latency_ms": self.latency_summary(),
            # Phase 7C.9 warmup summary. Aggregate decision counts on
            # warmup records (excluded from the primary aggregates).
            "warmup_summary": {
                "requested": self.warmup_requested,
                "responses_200": self.warmup_responses_200,
                "errors": self.warmup_errors,
                "decision_distribution": self.warmup_decision_distribution(),
            },
            "error_details": self.error_details,
        }


def _percentile(sorted_samples: list[float], p: float) -> float:
    if not sorted_samples:
        return float("nan")
    k = int(p * (len(sorted_samples) - 1))
    return sorted_samples[k]


def load_corpus(
    corpus: str, corpus_dir: Path, limit: int | None = None
) -> Iterator[dict[str, Any]]:
    """Yield BookingRequest payloads line-by-line from the NDJSON corpus
    file located at `corpus_dir / _CORPUS_FILES[corpus]`. Streams via
    `for line in f`; does NOT use json.load on the whole file."""
    filename = _CORPUS_FILES.get(corpus)
    if filename is None:
        msg = f"unknown corpus {corpus!r}; expected one of {sorted(_CORPUS_FILES)}"
        raise ValueError(msg)
    path = corpus_dir / filename
    if not path.exists():
        msg = f"corpus file missing: {path} — run the freight_risk export script first"
        raise FileNotFoundError(msg)
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
            n += 1
            if limit is not None and n >= limit:
                return


def _verify_corpus_dir(corpus_dir: Path) -> None:
    """Fail-fast if `corpus_dir` does not exist or any of the three
    expected NDJSON files is missing. Raises FileNotFoundError with a
    per-file message so the operator can fix the export step."""
    if not corpus_dir.is_dir():
        msg = f"--corpus-dir does not exist or is not a directory: {corpus_dir}"
        raise FileNotFoundError(msg)
    missing = [
        filename for filename in _CORPUS_FILES.values() if not (corpus_dir / filename).exists()
    ]
    if missing:
        msg = (
            f"--corpus-dir {corpus_dir} missing files: {missing}. "
            "Run scripts/calibration/export_from_freight_risk.py to populate."
        )
        raise FileNotFoundError(msg)


async def _post_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    token: str,
    index: int,
    payload: dict[str, Any],
    results: ReplayResults,
    *,
    is_warmup: bool = False,
) -> None:
    # Phase 7C.9: strip orchestrator-internal metadata fields
    # (prefixed with `_`) before POSTing. BookingRequest is
    # extra="forbid"; sending `_replay_role` would 422 the request.
    booking_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
    async with sem:
        start = time.monotonic()
        try:
            response = await client.post(
                f"{base_url}/api/v1/shipments/booking/evaluate",
                json=booking_payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        except (httpx.HTTPError, OSError) as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            if is_warmup:
                results.warmup_errors += 1
            else:
                results.errors += 1
            results.error_details.append(
                {
                    "index": index,
                    "role": "warmup" if is_warmup else "measurement",
                    "status_code": None,
                    "body_snippet": "",
                    "latency_ms": round(elapsed_ms, 3),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return
    elapsed_ms = (time.monotonic() - start) * 1000
    if response.status_code != 200:
        if is_warmup:
            results.warmup_errors += 1
        else:
            results.errors += 1
        results.error_details.append(
            {
                "index": index,
                "role": "warmup" if is_warmup else "measurement",
                "status_code": response.status_code,
                "body_snippet": response.text[:500],
                "latency_ms": round(elapsed_ms, 3),
                "error": None,
            }
        )
        return
    body = response.json()
    txn = TransactionResult(
        request_id=body["request_id"],
        decision=body["decision"],
        score=float(body["score"]),
        classification=body["classification"],
        triggered_rules=list(body.get("triggered_rules", [])),
        latency_ms=round(elapsed_ms, 3),
    )
    if is_warmup:
        results.warmup_responses_200 += 1
        results.warmup_transactions.append(txn)
    else:
        results.responses_200 += 1
        results.transactions.append(txn)


async def run_replay(
    *,
    corpus: str,
    corpus_dir: Path,
    rules_path_recorded: str,
    base_url: str,
    token: str,
    concurrency: int,
    limit: int | None,
) -> ReplayResults:
    results = ReplayResults(
        corpus=corpus,
        started_at=datetime.now(UTC).isoformat(),
        throttle_concurrency=concurrency,
        rules_file_recorded=rules_path_recorded,
    )
    sem = asyncio.Semaphore(concurrency)
    # Phase 7C.9: split warmup and measurement records, process warmup
    # FIRST and wait for completion before any measurement records hit
    # the booking endpoint. Warmup's purpose is to populate the
    # customer baseline before measurement evaluates against it; the
    # phase barrier ensures correctness.
    warmup_payloads: list[dict[str, Any]] = []
    measurement_payloads: list[dict[str, Any]] = []
    for payload in load_corpus(corpus, corpus_dir, limit=limit):
        role = payload.get("_replay_role", "measurement")
        if role == "warmup":
            warmup_payloads.append(payload)
        else:
            measurement_payloads.append(payload)

    async with httpx.AsyncClient() as client:
        # Warmup phase — complete fully before measurement starts.
        if warmup_payloads:
            warmup_tasks: list[asyncio.Task[None]] = []
            for idx, payload in enumerate(warmup_payloads):
                results.warmup_requested += 1
                warmup_tasks.append(
                    asyncio.create_task(
                        _post_one(
                            client, sem, base_url, token, idx, payload, results, is_warmup=True
                        )
                    )
                )
            await asyncio.gather(*warmup_tasks)
        # Measurement phase.
        tasks: list[asyncio.Task[None]] = []
        for idx, payload in enumerate(measurement_payloads):
            results.requested += 1
            tasks.append(
                asyncio.create_task(_post_one(client, sem, base_url, token, idx, payload, results))
            )
        await asyncio.gather(*tasks)
    results.finished_at = datetime.now(UTC).isoformat()
    return results


def _format_pct(num: float, denom: int) -> str:
    if denom <= 0:
        return "n/a"
    return f"{(num / denom) * 100:.2f}%"


def compare_results(path_a: Path, path_b: Path) -> dict[str, Any]:
    """Compute a delta report between two pre-computed result files.

    Emits per-band share deltas, per-rule fire-rate deltas, and a one-
    line headline summary. Does NOT run any new replays.
    """
    a = json.loads(path_a.read_text(encoding="utf-8"))
    b = json.loads(path_b.read_text(encoding="utf-8"))
    requested_a = a["totals"]["requested"]
    requested_b = b["totals"]["requested"]
    dist_a = a["decision_distribution"]
    dist_b = b["decision_distribution"]
    rules_a = a["per_rule_fire_counts"]
    rules_b = b["per_rule_fire_counts"]
    all_rules = sorted(set(rules_a) | set(rules_b))
    per_rule_delta = []
    for rule in all_rules:
        a_count = rules_a.get(rule, 0)
        b_count = rules_b.get(rule, 0)
        a_share = (a_count / requested_a) if requested_a > 0 else 0.0
        b_share = (b_count / requested_b) if requested_b > 0 else 0.0
        per_rule_delta.append(
            {
                "rule": rule,
                "a_count": a_count,
                "b_count": b_count,
                "a_share_pct": round(a_share * 100, 2),
                "b_share_pct": round(b_share * 100, 2),
                "delta_pp": round((b_share - a_share) * 100, 2),
            }
        )
    per_rule_delta.sort(key=lambda r: abs(r["delta_pp"]), reverse=True)
    return {
        "a": {
            "path": str(path_a),
            "corpus": a.get("corpus"),
            "rules_file_recorded": a.get("rules_file_recorded", ""),
            "requested": requested_a,
            "decision_distribution_share": {
                k: _format_pct(v, requested_a) for k, v in dist_a.items()
            },
        },
        "b": {
            "path": str(path_b),
            "corpus": b.get("corpus"),
            "rules_file_recorded": b.get("rules_file_recorded", ""),
            "requested": requested_b,
            "decision_distribution_share": {
                k: _format_pct(v, requested_b) for k, v in dist_b.items()
            },
        },
        "per_rule_delta": per_rule_delta,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("RESULT_A", "RESULT_B"),
        help=(
            "Compare two pre-computed result JSON files; print a delta report "
            "to --out (default stdout). When set, no replay runs."
        ),
    )
    ap.add_argument("--corpus", choices=sorted(_CORPUS_FILES))
    ap.add_argument("--corpus-dir", type=Path)
    ap.add_argument("--rules", type=Path, default=Path("app/rules.yaml"))
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--tenant-token")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="If set, stop after N records (useful for smoke runs).",
    )
    args = ap.parse_args(argv)

    if args.compare is not None:
        path_a, path_b = args.compare
        if not path_a.exists():
            print(f"--compare: file not found: {path_a}", file=sys.stderr)
            return 2
        if not path_b.exists():
            print(f"--compare: file not found: {path_b}", file=sys.stderr)
            return 2
        report = compare_results(path_a, path_b)
        serialized = json.dumps(report, indent=2)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(serialized)
        else:
            print(serialized)
        return 0

    if args.corpus is None:
        print("--corpus is required when not in --compare mode", file=sys.stderr)
        return 2
    if args.corpus_dir is None:
        print("--corpus-dir is required when not in --compare mode", file=sys.stderr)
        return 2
    if args.tenant_token is None:
        print("--tenant-token is required when not in --compare mode", file=sys.stderr)
        return 2
    if args.out is None:
        print("--out is required when not in --compare mode", file=sys.stderr)
        return 2
    if args.concurrency <= 0:
        print("--concurrency must be > 0", file=sys.stderr)
        return 2

    try:
        _verify_corpus_dir(args.corpus_dir)
    except FileNotFoundError as exc:
        print(f"corpus error: {exc}", file=sys.stderr)
        return 2

    try:
        results = asyncio.run(
            run_replay(
                corpus=args.corpus,
                corpus_dir=args.corpus_dir,
                rules_path_recorded=str(args.rules),
                base_url=args.base_url.rstrip("/"),
                token=args.tenant_token,
                concurrency=args.concurrency,
                limit=args.limit,
            )
        )
    except FileNotFoundError as exc:
        print(f"corpus error: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results.to_dict(), indent=2))

    summary = results.decision_distribution()
    latency = results.latency_summary()
    print(
        f"corpus={args.corpus} requested={results.requested} "
        f"responses_200={results.responses_200} errors={results.errors}",
        file=sys.stderr,
    )
    print(
        f"  decisions: BLOCK={summary['BLOCK']} "
        f"REVIEW={summary['REVIEW']} ALLOW={summary['ALLOW']}",
        file=sys.stderr,
    )
    print(
        f"  latency_ms p50={latency['p50']:.1f} p95={latency['p95']:.1f} p99={latency['p99']:.1f}",
        file=sys.stderr,
    )
    print(f"  → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
