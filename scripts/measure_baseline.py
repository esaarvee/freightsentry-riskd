#!/usr/bin/env python3
"""Baseline latency measurement.

Sends synthetic booking + modification + feedback requests against the
local docker-compose stack and reports per-endpoint p50/p95/p99 +
mean. Captures the latency baseline with the cache and EMF processor
in place, so the measurement reflects the production runtime shape
that the load test compares against.

Usage:
    docker compose up -d
    python scripts/measure_baseline.py \\
        --tenant-id <tenant-id> \\
        --token <api-token> \\
        [--base-url http://localhost:8000] \\
        [--booking-count 3000] \\
        [--modification-count 3000] \\
        [--feedback-count 4000] \\
        [--concurrency 50] \\
        [--json-out docs/baseline-phase-5c.json]

The headroom gate:
- If any endpoint p95 > 200ms: STOP. The cache + EMF processor changes
  should not degrade latency this much; surface to STATUS.md.
- If any endpoint p95 >= 170ms (less than 30ms headroom): YELLOW FLAG.
  The downstream load test's role transition + RLS overhead may push
  it over the 200ms gate; surface to the operator before running the
  load test.
- If all endpoints p95 < 170ms: green.

Exit codes:
  0 — green: all endpoints within budget with comfortable headroom
  1 — invalid args
  2 — yellow flag: at least one endpoint within 30ms of the 200ms gate
  3 — red: at least one endpoint exceeds 200ms p95
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

P95_HARD_LIMIT_MS: float = 200.0
P95_YELLOW_HEADROOM_MS: float = 30.0


@dataclass
class EndpointStats:
    name: str
    samples: list[float] = field(default_factory=list)
    errors: int = 0

    def percentile(self, p: float) -> float:
        if not self.samples:
            return float("nan")
        sorted_samples = sorted(self.samples)
        k = int(p * (len(sorted_samples) - 1))
        return sorted_samples[k]

    @property
    def p50(self) -> float:
        return self.percentile(0.50)

    @property
    def p95(self) -> float:
        return self.percentile(0.95)

    @property
    def p99(self) -> float:
        return self.percentile(0.99)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples) if self.samples else float("nan")

    def headroom_to_p95_limit(self) -> float:
        return P95_HARD_LIMIT_MS - self.p95


async def _time_call(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    json_body: dict[str, Any] | None,
    headers: dict[str, str],
    stats: EndpointStats,
) -> None:
    start = time.monotonic()
    try:
        resp = await client.request(method, url, json=json_body, headers=headers)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if 200 <= resp.status_code < 300:
            stats.samples.append(elapsed_ms)
        else:
            stats.errors += 1
    except httpx.HTTPError:
        stats.errors += 1


def _booking_body(request_id: str, customer_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "customer": {"external_id": customer_id},
        "user": {"external_id": "baseline-user"},
        "source_ip": "192.0.2.50",
        "shipment": {
            "origin": {"address": "1 Baseline Origin"},
            "destination": {"address": "2 Baseline Dest"},
            "value": 1500.00,
            "channel": "api",
        },
        "booking_ts": "2026-06-02T08:00:00Z",
    }


def _modification_body(request_id: str, original: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "original_request_id": original,
        "modification_ts": "2026-06-02T08:30:00Z",
        "modification_type": "value",
        "new_value": {"value": 1700},
    }


def _feedback_body(request_id: str, target: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target,
        "label": "approved",
        "feedback_ts": "2026-06-02T08:45:00Z",
    }


async def _run_endpoint_burst(
    count: int,
    concurrency: int,
    make_call: Callable[[int], Awaitable[None]],
) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def _wrapped(idx: int) -> None:
        async with sem:
            await make_call(idx)

    await asyncio.gather(*(_wrapped(i) for i in range(count)))


async def _measure(
    base_url: str,
    token: str,
    booking_count: int,
    modification_count: int,
    feedback_count: int,
    concurrency: int,
) -> dict[str, EndpointStats]:
    headers = {"Authorization": f"Bearer {token}"}
    booking_stats = EndpointStats("booking")
    modification_stats = EndpointStats("modification")
    feedback_stats = EndpointStats("feedback")

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:

        async def book_call(i: int) -> None:
            await _time_call(
                client,
                "POST",
                f"{base_url}/api/v1/shipments/booking/evaluate",
                _booking_body(f"baseline-book-{i}", f"baseline-cust-{i % 50}"),
                headers,
                booking_stats,
            )

        await _run_endpoint_burst(booking_count, concurrency, book_call)

        async def mod_call(i: int) -> None:
            await _time_call(
                client,
                "POST",
                f"{base_url}/api/v1/shipments/modification/evaluate",
                _modification_body(f"baseline-mod-{i}", f"baseline-book-{i % booking_count}"),
                headers,
                modification_stats,
            )

        await _run_endpoint_burst(modification_count, concurrency, mod_call)

        async def fb_call(i: int) -> None:
            await _time_call(
                client,
                "POST",
                f"{base_url}/api/v1/shipments/feedback",
                _feedback_body(f"baseline-fb-{i}", f"baseline-book-{i % booking_count}"),
                headers,
                feedback_stats,
            )

        await _run_endpoint_burst(feedback_count, concurrency, fb_call)

    return {
        "booking": booking_stats,
        "modification": modification_stats,
        "feedback": feedback_stats,
    }


def _print_summary(results: dict[str, EndpointStats]) -> tuple[int, str]:
    """Returns (exit_code, status_label) per the headroom gate."""
    print(
        f"{'endpoint':<14} {'p50':>8} {'p95':>8} {'p99':>8} "
        f"{'mean':>8} {'count':>8} {'errors':>8} {'headroom':>10}"
    )
    print("-" * 88)
    worst_headroom: float = P95_HARD_LIMIT_MS
    any_red = False
    for stats in results.values():
        print(
            f"{stats.name:<14} {stats.p50:>8.1f} {stats.p95:>8.1f} {stats.p99:>8.1f} "
            f"{stats.mean:>8.1f} {len(stats.samples):>8d} {stats.errors:>8d} "
            f"{stats.headroom_to_p95_limit():>10.1f}"
        )
        if stats.p95 > P95_HARD_LIMIT_MS:
            any_red = True
        worst_headroom = min(worst_headroom, stats.headroom_to_p95_limit())

    print()
    if any_red:
        print(
            f"RED: at least one endpoint exceeds the {P95_HARD_LIMIT_MS:.0f}ms p95 "
            f"gate. STOP — surface to .claude/STATUS.md before proceeding to 5D."
        )
        return 3, "red"
    if worst_headroom < P95_YELLOW_HEADROOM_MS:
        print(
            f"YELLOW: tight headroom (worst = {worst_headroom:.1f}ms of "
            f"{P95_YELLOW_HEADROOM_MS:.0f}ms cushion to the 200ms gate). "
            f"5D.3's role transition + RLS overhead may push the load test "
            f"over budget. Notify operator before 5D.3."
        )
        return 2, "yellow"
    print(
        f"GREEN: all endpoints under {P95_HARD_LIMIT_MS:.0f}ms p95 with "
        f">={P95_YELLOW_HEADROOM_MS:.0f}ms headroom. Safe to proceed to 5D."
    )
    return 0, "green"


def _to_json(results: dict[str, EndpointStats], status_label: str) -> dict[str, Any]:
    return {
        "p95_hard_limit_ms": P95_HARD_LIMIT_MS,
        "p95_yellow_headroom_ms": P95_YELLOW_HEADROOM_MS,
        "status": status_label,
        "endpoints": {
            name: {
                "p50_ms": s.p50,
                "p95_ms": s.p95,
                "p99_ms": s.p99,
                "mean_ms": s.mean,
                "count": len(s.samples),
                "errors": s.errors,
                "headroom_to_p95_limit_ms": s.headroom_to_p95_limit(),
            }
            for name, s in results.items()
        },
    }


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Phase 5C.4 latency baseline.")
    p.add_argument("--token", required=True)
    p.add_argument("--tenant-id", type=int, required=False, default=None)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--booking-count", type=int, default=3000)
    p.add_argument("--modification-count", type=int, default=3000)
    p.add_argument("--feedback-count", type=int, default=4000)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--json-out", type=Path, default=None)
    return p.parse_args()


async def _main_async(args: argparse.Namespace) -> int:
    results = await _measure(
        args.base_url,
        args.token,
        args.booking_count,
        args.modification_count,
        args.feedback_count,
        args.concurrency,
    )
    exit_code, status_label = _print_summary(results)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(_to_json(results, status_label), indent=2))
        print(f"\nbaseline JSON written to {args.json_out}")
    return exit_code


def main() -> int:
    args = _parse_args()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
