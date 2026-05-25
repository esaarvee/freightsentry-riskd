# enrichment.md — Gateway Data Enrichment Spec

> Load when working on enrichment logic, gateway tests, or behavioral flags.
> Source: `services/gateway/app/enrichment.py`

---

## Entry Point

```python
async def enrich(
    fraud_db: FraudDB,
    platform_db: PlatformDB,
    user_id: str,
    ip_address: str,
    device_fingerprint: str | None,
    ship_from: str | None,
    ship_to: str | None,
    shipment_value: float,
    customer_id: str | None = None,
) -> EnrichmentContext
```

Runs three parallel queries via `asyncio.gather`, then computes behavioral flags.

---

## Three Parallel Queries

### 1. `_fetch_fraud_profile(fraud_db, user_id) -> dict | None`

```sql
SELECT trust_score, flagged_count, fraud_confirmed, is_blocked,
       avg_shipment_value, stddev_shipment_value,
       known_ips, known_devices, common_routes,
       last_seen, max_inactive_days, last_ip
FROM user_profiles WHERE user_id = $1
```

Returns `None` if user not found (new user).

### 2. `_fetch_customer_profile(fraud_db, customer_id) -> dict | None`

```sql
SELECT known_ips, common_routes FROM customer_profiles WHERE customer_id = $1
```

Skipped entirely (returns `None`) if `customer_id` is empty/None.

### 3. `_fetch_platform_data(platform_db, user_id) -> dict | None`

```sql
SELECT u.total_shipments, u.account_age_days,
       c.total_shipments AS customer_total_shipments,
       c.customer_age_days
FROM user_entity u
JOIN customer_entity c ON c.customer_id = u.customer_id
WHERE u.user_id = %s
```

MySQL (asyncmy), not PostgreSQL. Returns `None` if user not found.

---

## Behavioral Flag Computation

After all three queries return, `_compute_behavioral_flags()` computes:

### `is_new_user`
```python
ctx.is_new_user = (ctx.total_shipments == 0)
```
True when platform has zero shipments for this user.

### `is_new_ip`
```python
ip_known_to_user = ip_address in ctx.known_ips
ip_known_to_customer = ip_address in ctx.customer_known_ips
ctx.is_new_ip = not (ip_known_to_user or ip_known_to_customer)
```
New only if unknown to **both** the individual user AND the customer.

### `is_new_route`
```python
if not ship_from or not ship_to:
    ctx.is_new_route = False
else:
    route_key = f"{ship_from}-{ship_to}"
    in_user = route_key in ctx.common_routes
    in_customer = route_key in ctx.customer_common_routes
    ctx.is_new_route = not (in_user or in_customer)
```
False if ship_from/ship_to missing. New only if unknown to both user and customer.

### `is_new_device`
```python
if device_fingerprint:
    ctx.is_new_device = device_fingerprint not in ctx.known_devices
else:
    ctx.is_new_device = False  # signal absent, not a false positive
```
Always False when no fingerprint provided.

### `is_abnormally_dormant`
```python
if ctx.last_seen and ctx.max_inactive_days > 0:
    days_since = (datetime.now(tz=UTC) - ctx.last_seen).days
    threshold = max(ctx.max_inactive_days * 1.5, 30)
    ctx.is_abnormally_dormant = days_since > threshold
else:
    ctx.is_abnormally_dormant = False  # no history = not dormant
```
Requires both `last_seen` and a positive `max_inactive_days`.

### `value_zscore`
```python
if ctx.stddev_value and ctx.stddev_value > 0:
    ctx.value_zscore = (shipment_value - ctx.avg_value) / ctx.stddev_value
else:
    ctx.value_zscore = 0.0  # no variance = no anomaly signal
```

---

## Default Behaviors

| Scenario | Effect |
|---|---|
| **New user** (no fraud profile) | trust_score=0.5, flagged_count=0, fraud_confirmed=0, is_blocked=False, empty known_ips/devices/routes, is_new_user=True (from platform total_shipments=0) |
| **Missing customer** (no customer_id or not found) | customer_known_ips=[], customer_common_routes={} — is_new_ip/is_new_route check user-only |
| **Missing platform data** (user not in MySQL) | total_shipments=0, account_age_days=0, customer_total_shipments=0, customer_age_days=0 |
| **No device fingerprint** | is_new_device=False always |
| **No ship_from/ship_to** | is_new_route=False always |
| **No last_seen / max_inactive_days=0** | is_abnormally_dormant=False always |
| **No value variance (stddev=0)** | value_zscore=0.0 always |
| **No last_ip (new user or null in DB)** | last_ip="", ComputeProximity returns zero, all geo rules inert |

---

## Data Flow After Enrichment

```
POST /evaluate
  → asyncio.gather(                         ← all run concurrently
        enrich() ← fraud PG + platform MySQL,
        sismember global_block:ip,
        sismember global_block:device,
        asyncio.gather(*sismember global_block:email per address),
        asyncio.gather(*sismember global_block:phone per number),
        smembers user/customer ip_types,
        pfcount non-cloud IPs / IP-accounts / netblocks,
    )
  → gRPC Evaluate() → rules engine scores
  → publish_audit() → stream:audit
      → audit handler: INSERT audit_logs (with email_hmacs, phone_hmacs, is_cloud_ip)
      → branch on decision:
          ALLOW  → upsertUserKnownVectors + upsertCustomerKnownVectors
          BLOCK  → insertPendingReviewVectors (original_decision='BLOCK')
          REVIEW → insertPendingReviewVectors (original_decision='REVIEW')
               + if REVIEW: XADD stream:ai_analysis (has audit_log_id)
                     → AI handler: Analyze() → INSERT ai_analysis
```

Metrics upserts (`upsertUserMetrics`, `upsertCustomerMetrics`) always run regardless of decision.

The enrichment context feeds into the gRPC `EvaluationRequest` proto fields.
See `proto/fraud_evaluation.proto` for proto field mapping.

---

## Vector Promotion Semantics

Known-vector writes (`known_ips`, `known_devices`, `known_emails`, `known_phones`, `common_routes`) are gated by decision to prevent BLOCK/REVIEW vectors silently suppressing `is_new_*` signals on repeat attempts.

| Decision | Known-vector write | Pending hold |
|---|---|---|
| `ALLOW` | Immediate | — |
| `BLOCK` | None — held in `pending_review_vectors` | 60 days |
| `REVIEW` | None — held in `pending_review_vectors` | 60 days |

### Feedback resolution

**Pending row exists (BLOCK or REVIEW, not yet expired):**

| Outcome | Action |
|---|---|
| `CONFIRMED_FRAUD` | Insert into `global_blocked_vectors` (skip UA; skip IP if `is_cloud_ip`); delete pending row; publish `fraud:blacklist:sync` |
| `FALSE_POSITIVE` / `LEGITIMATE` | Promote held vectors into `known_*` arrays; delete pending row |
| `INCONCLUSIVE` | No-op — 60 d expiry sweeper cleans up |

**No pending row (ALLOW original, or pending expired):**

| Outcome | Action |
|---|---|
| `CONFIRMED_FRAUD` | Late reversal: insert into `global_blocked_vectors`; `array_remove` IP + device from `user_profiles.known_*`; `array_remove` IP + email/phone hmacs from `customer_profiles.known_*`; publish `fraud:blacklist:sync` |
| All others | No-op |

### Known limitations

- **Cross-user known_* cleanup**: after a global block, the blocked vector may still sit in other users' `known_*` arrays. The `is_*_globally_blocked` flags override at evaluation time, so the functional impact is zero, but the data is cosmetically stale. A nightly hygiene job is a follow-up.
- **Manual unblock / appeal**: no analyst endpoint to remove from `global_blocked_vectors` yet. Required before operators encounter false-positive complaints.
- **Post-expiry CONFIRMED_FRAUD**: if the pending row has expired before feedback arrives, the late-reversal branch fires using `audit_logs.email_hmacs`, `phone_hmacs`, `is_cloud_ip` columns added by this migration.
