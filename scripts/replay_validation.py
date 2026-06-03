#!/usr/bin/env python3
"""Phase 6C replay-validation orchestrator.

Reads a corpus NDJSON file from scripts/replay/data/, POSTs each
payload to the local freightsentry-riskd booking endpoint, captures
the decision + score + triggered_rules + latency, and writes
aggregated results to a JSON file. Throttled at 50 concurrent
in-flight requests (5x pool max 10; matches Phase 5 load-test
cadence).

The corpus files are NDJSON (one BookingRequest payload per line) —
chosen over JSON-array in 6C.1 to stay under the pre-commit large-
files cap (approved corpus is 5.8 MB compact). Read line by line
via `for line in f`, NOT json.load.

Idempotency: request_id is deterministic per-corpus per-index
(replay-{corpus}-{idx}), so a second run against the same tenant
returns the cached decision via the Phase 5 idempotency-replay
path. Re-runs are non-destructive.

Usage:
    docker compose up -d
    python scripts/replay_validation.py \\
        --corpus {approved|case2|case3} \\
        --base-url http://localhost:8000 \\
        --tenant-token $REPLAY_TENANT_TOKEN \\
        --out docs/replay-results-{corpus}.json \\
        [--concurrency 50] \\
        [--limit N]

Output JSON shape (consumed by 6C.4 docs/replay-validation.md):
    {
        "corpus": str,
        "started_at": iso8601 str,
        "finished_at": iso8601 str,
        "throttle_concurrency": int,
        "totals": {
            "requested": int,
            "responses_200": int,
            "errors": int,
        },
        "decision_distribution": {"ALLOW": int, "REVIEW": int, "BLOCK": int},
        "per_rule_fire_counts": {rule_name: int, ...},
        "latency_ms": {"p50": float, "p95": float, "p99": float, "mean": float},
        "per_transaction": [
            {
                "request_id": str,
                "decision": str,
                "score": float,
                "classification": str,
                "triggered_rules": [str, ...],
                "latency_ms": float,
            },
            ...
        ]
    }

The per_transaction array is retained for the approved corpus
specifically (FPR breakdown enumerates contributing rules per
record per the Phase 6 prompt's strict-reading methodology). For
case-2 / case-3 the array is also retained — orchestrator does NOT
prune; downstream 6C.4 markdown can summarize.
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

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORPUS_DIR = _REPO_ROOT / "scripts" / "replay" / "data"
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
    requested: int = 0
    responses_200: int = 0
    errors: int = 0
    transactions: list[TransactionResult] = field(default_factory=list)
    error_details: list[dict[str, Any]] = field(default_factory=list)

    def decision_distribution(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for t in self.transactions:
            counter[t.decision] += 1
        # Ensure all three bands are present even when zero.
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "corpus": self.corpus,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "throttle_concurrency": self.throttle_concurrency,
            "totals": {
                "requested": self.requested,
                "responses_200": self.responses_200,
                "errors": self.errors,
            },
            "decision_distribution": self.decision_distribution(),
            "per_rule_fire_counts": self.per_rule_fire_counts(),
            "latency_ms": self.latency_summary(),
            "per_transaction": [
                {
                    "request_id": t.request_id,
                    "decision": t.decision,
                    "score": t.score,
                    "classification": t.classification,
                    "triggered_rules": t.triggered_rules,
                    "latency_ms": t.latency_ms,
                }
                for t in self.transactions
            ],
            "error_details": self.error_details,
        }


def _percentile(sorted_samples: list[float], p: float) -> float:
    if not sorted_samples:
        return float("nan")
    k = int(p * (len(sorted_samples) - 1))
    return sorted_samples[k]


def load_corpus(corpus: str, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """Yield BookingRequest payloads line-by-line from the NDJSON corpus
    file. Does NOT use json.load (which would parse the whole stream as
    a single JSON document)."""
    filename = _CORPUS_FILES.get(corpus)
    if filename is None:
        msg = f"unknown corpus {corpus!r}; expected one of {sorted(_CORPUS_FILES)}"
        raise ValueError(msg)
    path = _CORPUS_DIR / filename
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


async def _post_one(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    base_url: str,
    token: str,
    payload: dict[str, Any],
    results: ReplayResults,
) -> None:
    async with sem:
        start = time.monotonic()
        try:
            response = await client.post(
                f"{base_url}/api/v1/shipments/booking/evaluate",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
        except (httpx.HTTPError, OSError) as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            results.errors += 1
            results.error_details.append(
                {
                    "request_id": payload.get("request_id", "unknown"),
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": round(elapsed_ms, 3),
                }
            )
            return
    elapsed_ms = (time.monotonic() - start) * 1000
    if response.status_code != 200:
        results.errors += 1
        results.error_details.append(
            {
                "request_id": payload.get("request_id", "unknown"),
                "status_code": response.status_code,
                "body": response.text[:500],
                "latency_ms": round(elapsed_ms, 3),
            }
        )
        return
    body = response.json()
    results.responses_200 += 1
    results.transactions.append(
        TransactionResult(
            request_id=body["request_id"],
            decision=body["decision"],
            score=float(body["score"]),
            classification=body["classification"],
            triggered_rules=list(body.get("triggered_rules", [])),
            latency_ms=round(elapsed_ms, 3),
        )
    )


async def run_replay(
    *,
    corpus: str,
    base_url: str,
    token: str,
    concurrency: int,
    limit: int | None,
) -> ReplayResults:
    results = ReplayResults(
        corpus=corpus,
        started_at=datetime.now(UTC).isoformat(),
        throttle_concurrency=concurrency,
    )
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        tasks: list[asyncio.Task[None]] = []
        for payload in load_corpus(corpus, limit=limit):
            results.requested += 1
            tasks.append(
                asyncio.create_task(_post_one(client, sem, base_url, token, payload, results))
            )
        await asyncio.gather(*tasks)
    results.finished_at = datetime.now(UTC).isoformat()
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True, choices=sorted(_CORPUS_FILES))
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--tenant-token", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="If set, stop after N records (useful for smoke runs).",
    )
    args = ap.parse_args(argv)

    if args.concurrency <= 0:
        print("--concurrency must be > 0", file=sys.stderr)
        return 1

    try:
        results = asyncio.run(
            run_replay(
                corpus=args.corpus,
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
