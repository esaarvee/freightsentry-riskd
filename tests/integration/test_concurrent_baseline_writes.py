"""Concurrent baseline-writes integration tests.

A booking POST and a feedback POST for the same customer issued in
parallel must both succeed without interleaved state. The booking
endpoint takes SELECT FOR UPDATE on customer_baselines (via
CustomerBaseline.load(for_update=True)); the feedback endpoint
acquires the same lock. The two requests must serialise — neither
deadlocks, neither loses its write, neither sees a partially-applied
baseline.

This file exercises the lock discipline at the integration boundary;
the unit tests of baseline.add_observation / .add_rejected_observation
pin the per-operation correctness.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
from httpx import AsyncClient

from app import db as app_db
from tests.conftest import seeded_ip_enrichment, set_test_tenant_id

_BOOKING_PATH = "/api/v1/shipments/booking/evaluate"
_FEEDBACK_PATH = "/api/v1/shipments/feedback"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _booking_payload(
    *,
    request_id: str,
    source_ip: str = "203.0.113.70",
    booking_ts: str = "2026-05-27T08:00:00Z",
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "customer": {"external_id": "conc-cust"},
        "user": {"external_id": "conc-user"},
        "source_ip": source_ip,
        "shipment": {
            "origin": {"address": "10 Origin Lane"},
            "destination": {"address": "20 Destination Ave"},
            "value": 1000.00,
            "channel": "api",
        },
        "booking_ts": booking_ts,
        "contact": {"origin_email": "conc@example.com"},
    }


def _feedback_payload(*, request_id: str, target: str, label: str = "rejected") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "target_request_id": target,
        "label": label,
        "feedback_ts": "2026-05-27T09:00:00Z",
    }


async def test_concurrent_booking_and_feedback_serialise(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Issue a second-booking POST and a feedback POST in parallel
    for the same customer. Both must succeed (no deadlock), the final
    baseline state must reflect BOTH writes (booking's n increment AND
    feedback's r_n increment), and customers.flagged_count must be 1
    (not 0 if feedback was lost; not 2 if double-applied)."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.70", asn_org="Comcast"):
        # Seed the first booking so feedback has a target
        seed = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="conc-book-seed"),
            headers=_headers(token),
        )
        assert seed.status_code == 200, seed.text

        async def post_second_booking() -> Any:
            return await unauth_client.post(
                _BOOKING_PATH,
                json=_booking_payload(
                    request_id="conc-book-parallel",
                    booking_ts="2026-05-27T08:15:00Z",
                ),
                headers=_headers(token),
            )

        async def post_feedback() -> Any:
            return await unauth_client.post(
                _FEEDBACK_PATH,
                json=_feedback_payload(
                    request_id="conc-fb-parallel",
                    target="conc-book-seed",
                    label="rejected",
                ),
                headers=_headers(token),
            )

        booking_resp, fb_resp = await asyncio.gather(post_second_booking(), post_feedback())
        assert booking_resp.status_code == 200, booking_resp.text
        assert fb_resp.status_code == 200, fb_resp.text
        assert fb_resp.json()["applied"] is True

        # Customers: flagged_count == 1 (rejected applied exactly once);
        # total_shipments == 2 (seed + parallel booking)
        customer = await db_conn.fetchrow(
            "SELECT total_shipments, flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "conc-cust",
        )
        assert customer["total_shipments"] == 2
        assert customer["flagged_count"] == 1

        # Baseline: ip_stats for 203.0.113.70 has n >= 2 (two booking
        # observations) AND r_n >= 1 (one rejection). The seed and the
        # parallel booking both call add_observation; feedback calls
        # add_rejected_observation. All three writes must persist
        # (lock serialises, doesn't lose).
        baseline_row = await db_conn.fetchrow(
            "SELECT ip_stats FROM customer_baselines WHERE tenant_id = $1",
            tenant_id,
        )
        ip_stats = json.loads(baseline_row["ip_stats"])
        entry = ip_stats["203.0.113.70"]
        assert entry["n"] >= 2.0
        assert entry["r_n"] >= 1.0


async def _wait_until_a_backend_blocks(probe: asyncpg.Connection, timeout: float = 5.0) -> None:
    """Poll pg_locks until some backend is waiting on a lock (granted=false).

    Used to deterministically order a forced lock-interleave: we proceed
    only once the feedback request has reached — and blocked on — its
    first row lock.

    The count is instance-global (any ungranted lock), which is sound only
    because integration tests run sequentially on a session-scoped event
    loop — no unrelated backend contends concurrently. If the suite ever
    runs in parallel against one DB (xdist), scope this to the feedback
    backend (join pg_stat_activity on pid) to avoid a false early proceed."""
    for _ in range(int(timeout / 0.05)):
        waiting = await probe.fetchval("SELECT count(*) FROM pg_locks WHERE NOT granted")
        if waiting and waiting > 0:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no backend blocked on a lock within timeout")


async def test_feedback_acquires_customers_lock_before_baselines_no_deadlock(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Deterministic regression for the booking-vs-feedback deadlock (T5).

    Forces the exact opposite-order interleave that deadlocked before the
    fix, using a holder connection that mimics the booking path's lock
    order (customers FIRST, then customer_baselines):

      1. holder: BEGIN; lock the `customers` row FOR UPDATE.
      2. fire a `rejected` feedback POST for the same customer; wait until
         its backend blocks on a row lock.
      3. holder: lock the `customer_baselines` row FOR UPDATE, then commit.

    With the fix (feedback locks customers BEFORE baselines), step 2 blocks
    feedback on the `customers` row held by the holder while it holds
    nothing; step 3's baselines lock is free, the holder commits and
    releases customers, and feedback completes → 200. With the OLD order
    (feedback locks baselines first, then customers), step 2 leaves
    feedback holding baselines and waiting on customers, so step 3
    deadlocks (holder wants baselines, feedback wants customers) and
    Postgres aborts one side with DeadlockDetectedError. The assertion
    below (feedback returns 200) fails RED on the old order.
    """
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.73", asn_org="Comcast"):
        seed = await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="lockorder-seed", source_ip="203.0.113.73"),
            headers=_headers(token),
        )
        assert seed.status_code == 200, seed.text
        cust_id = await db_conn.fetchval(
            "SELECT id FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "conc-cust",
        )

        holder = await app_db._pool.acquire()
        try:
            await set_test_tenant_id(holder, tenant_id)
            tx = holder.transaction()
            await tx.start()
            # Step 1 — holder locks customers first (booking-path order).
            await holder.execute(
                "SELECT 1 FROM customers WHERE id = $1 AND tenant_id = $2 FOR UPDATE",
                cust_id,
                tenant_id,
            )
            # Step 2 — fire feedback and wait until it blocks on a row lock.
            fb_task = asyncio.create_task(
                unauth_client.post(
                    _FEEDBACK_PATH,
                    json=_feedback_payload(
                        request_id="lockorder-fb", target="lockorder-seed", label="rejected"
                    ),
                    headers=_headers(token),
                )
            )
            try:
                await _wait_until_a_backend_blocks(db_conn)
                # Step 3 — holder now takes baselines. Deadlocks iff feedback
                # holds baselines while waiting on customers (the old order).
                await holder.execute(
                    "SELECT 1 FROM customer_baselines WHERE customer_id = $1 AND tenant_id = $2 "
                    "FOR UPDATE",
                    cust_id,
                    tenant_id,
                )
                await tx.commit()
            except BaseException:
                await tx.rollback()
                raise
            fb_resp = await fb_task
        finally:
            await app_db._pool.release(holder)

        assert fb_resp.status_code == 200, fb_resp.text
        assert fb_resp.json()["applied"] is True


async def test_concurrent_feedback_replays_idempotent(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """Two identical feedback POSTs with the same request_id issued in
    parallel — one wins the UNIQUE(tenant_id, request_id) race, the
    other gets the idempotent-replay envelope. The customer counter
    must increment exactly once (not zero, not twice)."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.71", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="conc-rep-book", source_ip="203.0.113.71"),
            headers=_headers(token),
        )

        async def post_fb() -> Any:
            return await unauth_client.post(
                _FEEDBACK_PATH,
                json=_feedback_payload(
                    request_id="conc-rep-fb",
                    target="conc-rep-book",
                    label="rejected",
                ),
                headers=_headers(token),
            )

        resp_a, resp_b = await asyncio.gather(post_fb(), post_fb())
        # Acceptable outcomes per the race timing:
        # - 200 + 200: one application + one tier-1 idempotent replay
        #   (the loser saw the winner's committed audit row first)
        # - 200 + 409: one application + one UniqueViolation
        #   (the loser missed tier-1 because the winner hadn't committed,
        #   then hit the UNIQUE constraint at INSERT — txn rolled back,
        #   no leftover side effects)
        # Either way the final state is the SAME: counter=1, audit=1.
        statuses = sorted([resp_a.status_code, resp_b.status_code])
        assert statuses in ([200, 200], [200, 409]), statuses

        flagged = await db_conn.fetchval(
            "SELECT flagged_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "conc-cust",
        )
        assert flagged == 1, f"flagged_count should be 1, got {flagged}"

        # Only one feedback row exists (UNIQUE constraint enforced)
        fb_count = await db_conn.fetchval(
            "SELECT count(*) FROM feedback WHERE tenant_id = $1 AND request_id = $2",
            tenant_id,
            "conc-rep-fb",
        )
        assert fb_count == 1


async def test_concurrent_upgrade_feedbacks_apply_correctly(
    unauth_client: AsyncClient,
    db_conn: asyncpg.Connection,
    seeded_api_token: tuple[str, int],
) -> None:
    """A rejected feedback and a fraud_confirmed feedback for the same
    target issued in parallel. Both have distinct request_ids so the
    tier-1 UNIQUE doesn't dedup them. Whichever lands first applies;
    the second runs the monotonicity check against the first's label.

    Possible outcomes (timing-dependent):
    (a) rejected lands first, fraud_confirmed lands second → upgrade
        applies: flagged=1 (from rejected), fraud=1 (from upgrade)
    (b) fraud_confirmed lands first, rejected lands second → rejected
        is a downgrade, blocked: flagged=1, fraud=1 (from
        fraud_confirmed; rejected's audit row persisted but no counter
        delta)

    Either way the final state is flagged=1 AND fraud=1. The lock
    serialises so the second feedback sees the first's label as prior."""
    token, tenant_id = seeded_api_token
    async with seeded_ip_enrichment(db_conn, "203.0.113.72", asn_org="Comcast"):
        await unauth_client.post(
            _BOOKING_PATH,
            json=_booking_payload(request_id="conc-up-book", source_ip="203.0.113.72"),
            headers=_headers(token),
        )

        async def post_rejected() -> Any:
            return await unauth_client.post(
                _FEEDBACK_PATH,
                json=_feedback_payload(
                    request_id="conc-up-rej", target="conc-up-book", label="rejected"
                ),
                headers=_headers(token),
            )

        async def post_fraud() -> Any:
            return await unauth_client.post(
                _FEEDBACK_PATH,
                json=_feedback_payload(
                    request_id="conc-up-fraud",
                    target="conc-up-book",
                    label="fraud_confirmed",
                ),
                headers=_headers(token),
            )

        rej_resp, fraud_resp = await asyncio.gather(post_rejected(), post_fraud())
        assert rej_resp.status_code == 200, rej_resp.text
        assert fraud_resp.status_code == 200, fraud_resp.text

        # Final state pin: regardless of timing, counters converge
        counts = await db_conn.fetchrow(
            "SELECT flagged_count, fraud_confirmed_count FROM customers WHERE tenant_id = $1 AND external_id = $2",
            tenant_id,
            "conc-cust",
        )
        assert counts["flagged_count"] == 1
        assert counts["fraud_confirmed_count"] == 1

        # Both feedback rows persisted (audit-trail visibility regardless
        # of whether the second was applied or skipped by monotonicity)
        fb_count = await db_conn.fetchval(
            "SELECT count(*) FROM feedback WHERE tenant_id = $1 AND target_request_id = $2",
            tenant_id,
            "conc-up-book",
        )
        assert fb_count == 2
