# decisions.md — Architectural decisions (current state)

This document records the **current** load-bearing architectural choices for freightsentry-riskd. Each section describes the system as it stands today; it does not narrate how the choice was reached.

- **Historical reasoning** (Phase-by-phase derivation, rejected alternatives, dated amendment trails): see [docs/history.md](../docs/history.md).
- **Live status / current phase / open work**: see [.ai/system-status.md](system-status.md).
- **Full rule catalogue and weights**: see [.ai/rules.md](rules.md) and `app/rules.yaml`.
- **Schema reference**: see [.ai/schema.md](schema.md) and the migration files under `alembic/versions/`.

When this document conflicts with the bootstrap prompt's "Design Context", this document wins. When in doubt during execution, consult this file before improvising.

---

## Project identity

- Real-time fraud detection SaaS for freight aggregation platforms.
- Single Python service. No second process, no second language, no second storage engine.
- PostgreSQL 16 only.
- Multi-tenant from day one. Every tenant-scoped table carries `tenant_id`; Postgres Row-Level Security is the defensive backstop.
- Stack: Python 3.13+ · FastAPI · asyncpg · Pydantic v2 · Alembic.
- Cost ceiling: CAD 1000/month operational, combined production + test/staging.
- Latency target: p95 < 200 ms across all evaluation endpoints.
- Scale ceiling: 100 TPS sustained; ~45K shipments/day average; five-year peak-of-peak under 100 TPS at 20% YoY growth.
- Operational currency: CAD (Canadian freight aggregator). Multi-currency support via per-tenant `allowed_currencies` and `value_caps`.

---

## Endpoints

The v1 API surface is four endpoints under `/api/v1/`:

1. `POST /api/v1/shipments/booking/evaluate` — booking risk evaluation
2. `POST /api/v1/shipments/modification/evaluate` — modification risk evaluation
3. `POST /api/v1/shipments/feedback` — operator feedback ingestion (approved / rejected / fraud_confirmed)
4. `GET /health/` — liveness/readiness

Plus two read-only admin endpoints under `/api/v1/admin/`:

- `GET /api/v1/admin/customers/{external_id}/baseline` — customer record + truncated baseline (stat-dicts top-10 by `n` desc + `total_count` + `truncated` flag). PII fields HMAC'd in response.
- `GET /api/v1/admin/decisions/{request_id}` — full decision detail + linked shipment data (city + country only; full address NOT surfaced).

### Admin endpoint constraints

- Authorization: `require_admin_role` (`app/auth.py`) checks `auth.role == "admin"` — 403 otherwise. `auth.role` is sourced from `api_tokens.role`.
- Tenant-bounded: cross-tenant lookups return 404 (hides existence per security-by-default convention).
- READ-ONLY. Admin write endpoints (decision overrides, manual feedback) are out of scope for v1.
- Stat-dict truncation: customer baseline endpoint truncates each stat-dict to top-10 by `n` desc.

### No tenant-registration endpoint

Tenant onboarding is an operator script (`scripts/tenant_onboard.py`). The script is idempotent UPSERT-by-name with `pg_advisory_xact_lock(hashtext(external_id))` to serialize concurrent runs. The `--rotate-token` flag REVOKES prior tokens (in-transaction DELETE) before issuing a new one.

### Implicit entity registration

Customer / enterprise / user records auto-upsert from the first booking payload that references them. Booking payload carries optional metadata (`registered_address`, `business_name`, `enterprise_id`, `registered_country`) which populates the records on first sight and can update on subsequent bookings via a COALESCE-on-update pattern that protects operator-supplied values from being overwritten by payload nulls.

### Platform-supplied shipment identity (system of record)

As of migration `0006`, the upstream platform's shipment identifier is the system of record. `shipments.id` is the platform-supplied `shipment_id` (`text`, not a riskd-minted serial); the PK is composite `(tenant_id, id)` and `decisions.shipment_id` is `text` with a composite FK. Motivation: a future admin dashboard (separate repo) where a riskd-minted ID diverging from the platform's would create confusion. Booking and modification payloads carry `shipment_id` + `transaction_number` (both required `text`, `1..128`). See [`schema.md`](schema.md#0006_platform_shipment_id--platform-supplied-shipment-identity) for the full contract (intentional 409 on duplicate `shipment_id`; modification 422 cross-checks; response echoes).

**Boundary notes (intentional absences — do not "fix" in a dead-capability audit):**

- `shipments.transaction_number` is **stored unindexed by design.** It is an operator-facing reference, not a riskd query key. There is **no riskd read endpoint** for it.
- The external **admin dashboard is a separate repo** (later); it reads by **date range**, so **no `transaction_number` index and no timestamp/date-range index** belong in riskd. This absence is deliberate.
- `transaction_number` is the same logical value as `freight_risk.shipments.transaction_number` (the calibration ETL source) — the same identifier adopted from upstream, not a separate concept.
- The platform team owns the breaking-payload-change coordination (versioned endpoint / cutover); that is out of repo scope.

---

## Scoring architecture — 3-layer noisy-OR

Implemented in `app/scoring.py::score`. The pipeline is:

```
Layer 1 (hard-block short-circuit)
    ↓ no BLOCK rule fired
Layer 2 (account prior: maturity + trust + flag tiers)
    ↓
Layer 3 (signal noisy-OR with maturity downweighting)
    ↓
final = noisyOR(account_prior, signal_score)
    ↓
band: ALLOW | REVIEW | BLOCK
```

### Layer 1 — Hard-block short-circuit

Any rule with `action: BLOCK` that fires returns immediately with `score=1.0`, `decision=BLOCK`, no further evaluation. The machinery accepts an unbounded count of BLOCK rules.

### Layer 2 — Account prior

```
base_prior         = MaxNewAccount * (1 - maturity)
trust_risk         = max(0, (0.5 - trust_score) / 0.5)
trust_contribution = trust_risk * TrustFactor
flag_prior         = flag_weights[flagged_count_tier]
account_prior      = noisyOR(base_prior, trust_contribution, flag_prior)
```

Where:
- `maturity = clamp(age_days / maturity_age_days, 0, 1) * clamp(shipments / maturity_shipments, 0, 1)` (multiplicative, not min-of-fractions)
- `flagged_count_tier`: 0 → 0, 1-2 → 1, 3-5 → 2, 6+ → 3

Constants (in `app/scoring_constants.py`; per-tenant overrides via `tenants.config`):

| Constant | Default | Override field |
|---|---|---|
| `MaxNewAccount` | 0.10 | (not overridable) |
| `TrustFactor` | 0.25 | (not overridable) |
| `flag_weights` | `[0.00, 0.15, 0.25, 0.35]` | (not overridable) |
| `MATURITY_AGE_DAYS` | 180 | `tenant_config.maturity_age_days` |
| `MATURITY_SHIPMENTS` | 50 | `tenant_config.maturity_shipments` |
| `MATURITY_K` | 0.30 | `tenant_config.maturity_k` |

### Layer 3 — Signal noisy-OR with maturity downweighting

For each fired non-BLOCK rule:

```
if rule.maturity_sensitive:
    effective_weight = rule.weight * (1 - MaturityK * (1 - maturity))
else:
    effective_weight = rule.weight
signal_score = noisyOR(effective_weights of fired rules)
```

### Cold-start grace multiplier

`tenant_config.cold_start_grace_days` (default 0; disabled). During the grace window after tenant onboarding (measured from `tenants.created_at`), the maturity formula multiplies its computed value by 0.5. After the window, no multiplier. The 0.5 is hardcoded — not tenant-configurable.

Per-customer cold-start (a new customer at a mature tenant) is handled by Layer 2 `base_prior` already; `cold_start_grace_days` is tenant-wide.

### Final score and thresholds

`final = noisyOR(account_prior, signal_score)`. Thresholds (initial; per-tenant overridable):

- `allow_max = 0.60` → ALLOW (GREEN)
- `block_min = 0.80` → BLOCK (RED)
- Between → REVIEW (YELLOW)

Risk-level bands: `<0.30` LOW, `<0.60` MEDIUM, `<0.80` HIGH, `≥0.80` CRITICAL.

Source-of-truth for `allow_max` / `block_min` is `app/rules.yaml` (NOT `app/scoring_constants.py` and NOT Pydantic-settings — avoid drift).

### Layer 1 invariance

Per-tenant maturity overrides AND cold-start grace are bypassed when a Layer 1 BLOCK rule fires. Pinned by `test_layer_1_short_circuit_does_not_consult_tenant_config` (unit) and `test_overrides_do_not_affect_layer_1_block` + `test_grace_does_not_affect_layer_1_block` (integration).

---

## Trust score — computed on read, never persisted

Continuous customer-level value in `[0, 1]`. Computed by `app/trust.py::compute_trust_score(customer, baseline) -> float` per request, in `build_context` after `baseline.decay_to(today)`.

Inputs (already loaded by `build_context`):
- `account_age_days` from `customers.first_seen`
- `effective_observations` from `customer_baselines` (post-decay)
- `flagged_count` from `customers`
- `fraud_confirmed_count` from `customers`

Sub-millisecond per call (pure arithmetic, no I/O).

**Do NOT persist `trust_score` as a column.** Trust depends on `effective_observations` which decays with time — a persisted value goes stale on every read after the write date. Computing on read has zero staleness risk and zero meaningful cost.

The computed value is attached to the Context dict; Layer 2 and the trust-conditioned rules consume it (see `.ai/rules.md`).

---

## Customer baseline

Per-customer JSONB columns on `customer_baselines`. Schema and lifecycle are owned by `app/baseline.py`.

### Stat-dict entry shape

`{n, r_n, last}` plus `type` field for IP-keyed entries only:

- `n` — decay-weighted approved-observation count
- `r_n` — decay-weighted rejected-observation count (anti-signal from feedback)
- `last` — ISO date of most recent observation
- `type` (IP entries only) — `"cloud" | "dc" | "residential"` (omitted for unknown)

### Dimensions

Stat-dicts (frequency-recency maps):
- `origin_stats`, `dest_stats`, `lane_stats` — route geography (lane key = `f"{origin}||{destination}"`)
- `ip_stats` (with `type` per entry), `ip_netblock_stats` (/24 key), `ip_asn_stats`, `country_stats`, `origin_ip_country_stats`
- `email_hmacs`, `phone_hmacs`, `email_domain_stats`, `phone_prefix_stats`
- `rejected_email_hmacs`, `rejected_phone_hmacs` (separate columns; not collapsed into `r_n` of the approved sets)
- `country_route_stats` — keyed `f"{origin_country}||{destination_country}"`; powers `shipment_route_unfamiliar_for_customer`

Flat histograms (`{key: float}`):
- `ip_type_hist` — keys: `"cloud"`, `"dc"`, `"residential"`
- `hour_hist`, `weekday_hist`, `channel_hist`

Welford triples:
- Value: `value_n`, `value_mean`, `value_m2`
- Cadence (inter-arrival hours): `cadence_n`, `cadence_mean_h`, `cadence_m2_h`

Last-booking pointers:
- `last_booking_ts`, `last_booking_lat`, `last_booking_lon`, `last_booking_country`

Lifecycle:
- `decay_anchor_date` (lazy decay coordination)
- `first_seen`, `last_seen`, `updated_at`

`value_n` (post-decay) is exposed to rule conditions via the `customer_observations` Context field — the decay-weighted activity proxy in lieu of a persisted 30-day count.

### Decay strategy

Lazy decay: applied on read via `decay_to(as_of)`. `decay_anchor_date` advances on every successful write.

Per-IP-type half-lives for `ip_stats` entries:
- `cloud`: 365 days
- `dc`: 365 days
- `residential`: 60 days
- unknown (no `type`): 180 days

Other dimensions: uniform 90-day half-life.

Decay function applied per entry's `n`, `r_n`, and Welford accumulators:

```
factor = exp(-ln2 * delta_days / half_life)
```

### ALLOW-gated accumulation (current architecture)

**The customer baseline is a record of operator-confirmed legitimate behavior, NOT a record of all evaluated bookings.**

- **Booking endpoint** (`app/api/booking.py`): `baseline.add_observation()` + `baseline.save()` run only when `result.decision == "ALLOW"`. REVIEW/BLOCK bookings are HELD in pending state — baseline state unchanged (stat-dicts, Welford accumulators, last_booking_*, histograms all untouched).
- **Feedback endpoint** (`app/api/feedback.py`): when operator submits `approved` feedback AND the prior decision was REVIEW or BLOCK, the deferred observation is folded into the baseline NOW using the same `add_observation` shape; `source_ip` is re-enriched via the cached `ip_enrichment` row.
- The monotonicity guard prevents double-folding: an ALLOW + approved feedback does NOT re-fold (`decision_band != "ALLOW"` guard). A REVIEW + rejected + later approved sequence is monotonicity-skipped.
- A REVIEW + rejected feedback runs `add_rejected_observation` — creates fresh entries with `r_n=1`, `n=0` even when keys were missing pre-feedback.

This means attack bookings stay OUT of the baseline forever (they are never operator-approved), and future records from the same ASN/IP remain "unfamiliar" to the case-2 rule. The trade-off is a longer cold-start ramp: maturity-gated rules come online later because REVIEW bookings do not contribute to `customer_observations`.

Test coverage: `tests/integration/test_baseline_gating.py` pins the five-case matrix (REVIEW-no-fold, approve-then-fold, ALLOW-no-double-add, monotonicity-skip, rejected-on-non-folded).

### Velocity counts are unaffected

`velocity_user_hourly`, `velocity_user_daily`, `velocity_ip_*` are SQL queries on the `shipments` table, not baseline state. They count all bookings regardless of decision band. The customer's baseline state diverges from their booking history; this is by design (the baseline is a quality signal, not a frequency signal).

---

## Persistence

Single transaction per booking/modification request, owned by the endpoint handler in `app/api/booking.py` / `app/api/modification.py`:

1. `SELECT FOR UPDATE` on `customer_baselines` (locks the row for the transaction)
2. INSERT into `shipments`
3. INSERT into `decisions`
4. If `decision == "ALLOW"`: `baseline.add_observation(...)` + `baseline.save(conn)` (UPDATE on customer_baselines)
5. UPDATE `customers` (last_seen, total_shipments increment)
6. UPSERT into `tenant_route_baselines`
7. Commit

Persistence failure → 500 to caller. Retry-safe via idempotency on `(tenant_id, request_id)`.

`build_context` loads sequentially on the transaction connection rather than via `asyncio.gather` — asyncpg does not multiplex operations on a single connection. The baseline FOR UPDATE lock must hold across the read-modify-write window, so the lock-holding connection cannot be split.

No persisted `shipment_volume_30d` column. 30-day window counts compute on demand via `COUNT(*) FROM shipments WHERE booking_ts > now() - interval '30 days'`. Rules wanting decay-weighted activity read `customer_baselines.value_n`.

Postgres + Alembic for migrations. Schema definition lives in `alembic/versions/`; the post-squash final state is composed of five migrations (foundation, booking_flow, baselines, enrichment_global, runtime_roles). The schema golden test (`tests/integration/test_schema_golden.py`) is the anti-drift gate.

---

## Per-tenant configuration

Single `tenants.config JSONB` column. Schema validated at write time and on every load via `app/tenant_config.py::TenantConfig` (Pydantic v2).

### Field set (current)

```python
class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: int                                       # supplied by loader, not stored in JSONB
    config_version: int = 0                              # bumped on every config change

    # Override fields — None means "use scoring_constants.py default"
    maturity_age_days: int | None = None                 # default 180
    maturity_shipments: int | None = None                # default 50
    maturity_k: float | None = None                      # default 0.30
    value_caps: dict[str, dict[str, float]] | None = None  # {currency: {tier: threshold}}

    # Non-None defaults
    allowed_currencies: list[str] = ["CAD"]              # ISO 4217 3-letter
    cold_start_grace_days: int = 0

    # Metadata — supplied by the loader from row columns
    created_at: datetime
    updated_at: datetime
```

`app/scoring_constants.py` REMAINS the source of truth for project defaults; TenantConfig overrides on top. `allow_max` / `block_min` live in `app/rules.yaml`, NOT in TenantConfig.

### `value_caps` shape

`dict[currency: str, dict[tier: str, threshold: float]]` where `tier ∈ {high, new_user, medium, low}` — matches the 4 distinct thresholds in the 7 currency-aware rules. `value_caps == None` means "use `DEFAULT_VALUE_CAPS` at the resolver" (CAD-keyed defaults).

### Loading semantics

- Fresh-load via `load_tenant_config(conn, tenant_id)` at endpoint entry, AFTER `set_tenant_id(conn, auth.tenant_id)` and BEFORE any other DB read.
- `tenants` table is intentionally non-RLS; the loader uses explicit `WHERE id = $1` as defense-in-depth.
- Wrapped by an in-process 60-second TTL cache (see "Tenant-config caching" below).

### Validation

- Read path: `parse_config_jsonb` validates JSONB → Pydantic every load. Stored-data corruption surfaces as `pydantic.ValidationError`.
- Write path: `scripts/tenant_onboard.py::_validate_initial_config` validates before INSERT/UPDATE.

### JSONB codec discipline

asyncpg returns JSONB as `str` by default in this project (no `set_type_codec` registered). The loader's cast-at-boundary pattern handles BOTH codec paths (str via `json.loads`, dict via direct cast) so a future codec registration is non-breaking.

---

## Currency normalization

### Current behavior

- `BookingRequest.shipment.currency` and `ModificationRequest.currency` are optional `str` fields. Validation: 3-letter uppercase ISO 4217 shape at the Pydantic layer; allowed-list check at request time against `tenant_config.allowed_currencies` (400 if not in list).
- Per-currency, per-tier thresholds live in `tenant_config.value_caps`. 4-tier scheme: `high / new_user / medium / low`.
- `DEFAULT_VALUE_CAPS = {"CAD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}}` (`app/tenant_config.py`).
- `DEFAULT_ALLOWED_CURRENCIES = ["CAD"]`.
- `resolve_value_caps(tenant_config, currency)` resolves per-request, falling back to `DEFAULT_VALUE_CAPS["CAD"]` with a `tenant_config.value_caps.fallback` structured warning (`metric=True`) if the tenant has an allowed currency without a matching `value_caps` entry.
- 7 currency-aware rules in `app/rules.yaml` consult `shipment_value_threshold_<tier>` Context fields populated in `build_context`. Modification rule `modification_within_30_min_value_increase` is fraction-based and currency-independent.

### Backward compatibility

`ShipmentData.currency` / `ModificationRequest.currency` Pydantic field defaults remain `"USD"` to preserve payload-shape backward-compat with pre-CAD-switch requests. The tenant-config layer (`allowed_currencies`, `value_caps`, fallback) is what drives the CAD-default operational behavior.

### No conversion

Currency conversion via a rates table is explicitly rejected. Per-currency thresholds are operator-tunable per tenant via `value_caps` and require no daily upkeep. Cross-currency risk aggregation is out of scope for v1.

---

## Cold start and maturity

### Per-tenant maturity overrides

`app/scoring.py::score` consults `tenant_config` for the three maturity constants. `None` means "use project default from `app/scoring_constants.py`":

| Constant | Override field | Project default |
|---|---|---|
| `maturity_age_days` | `tenant_config.maturity_age_days` | 180 |
| `maturity_shipments` | `tenant_config.maturity_shipments` | 50 |
| `maturity_k` | `tenant_config.maturity_k` | 0.30 |

### Cold-start grace window

`tenant_config.cold_start_grace_days` (default 0; disabled). During the grace window after tenant onboarding (measured from `tenants.created_at`), the maturity formula multiplies its computed value by 0.5. After the window, no multiplier. The 0.5 is hardcoded.

Composition with a maturity-sensitive rule (weight 0.6, MaturityK=0.30):

| Maturity state | m | Effective weight |
|---|---|---|
| Mature (post-grace, ≥180 days, ≥50 shipments) | 1.0 | 0.60 |
| Grace-active, mature customer (m_raw=1.0) | 0.5 | 0.51 |
| Brand-new at default tenant (m_raw=0.0) | 0.0 | 0.42 |

### Per-customer cold start

Handled by Layer 2 `base_prior = MaxNewAccount * (1 - maturity)` — elevates new-customer scores; maturity-sensitive rules fire softer via the Layer 3 downweight. The tenant-wide `cold_start_grace_days` window does NOT affect per-customer cold-start.

### Per-customer maturity gates inside rule derivations

Several derivations apply their own `customer_observations >= N` gate before contributing (e.g., `_asn_unfamiliar_for_customer` and `_outbound_destination_mismatch` use a `>= 10` gate inside `app/context.py`). These gates are independent of the Layer 3 maturity downweight and prevent cold-start customers from tripping baseline-aware signals.

### Population baseline cold start

The `tenant_route_baselines` subsystem applies a strict-less-than cold-start gate (`total_count < 100` → False; the 100th observation passes). The 100-observation seed is a per-tenant ramp, distinct from per-customer cold-start.

---

## IP enrichment

All four sources, lazy-cached in the `ip_enrichment` Postgres table keyed by IP. Cache freshness: 14 days; on staleness, re-enrich. Sources are accessed only by `scripts/fetch_enrichment.py` (offline refresh) — no live source calls on the request path.

| Source | Auth | License | Refresh |
|---|---|---|---|
| MaxMind GeoLite2 City + ASN | Free signup → account_id + license_key | GeoLite2 EULA (free use, attribution, no redistribution) | Tue/Fri weekly |
| FireHOL Level 1 + Level 2 | None | CC-BY-SA / public-domain | Daily |
| IP2Proxy LITE PX11 | Free signup → token | CC-BY-SA 4.0 | Monthly |
| Cloud provider CIDRs (AWS, GCP, Azure, Cloudflare) | None | Public | Continuously (AWS), weekly (GCP/Azure), rare (Cloudflare) |

Secret storage: `MAXMIND_LICENSE_KEY`, `IP2PROXY_DOWNLOAD_TOKEN` in Pydantic Settings (AWS Secrets Manager in prod). No env prefix — env var names match Pydantic field names verbatim.

`is_proxy` from IP2Proxy is gated on non-empty `proxy_type` (sentinel values: `""`, `-`, `INVALID IP ADDRESS`, `NOT SUPPORTED`, `INVALID DATABASE FILE`, `DATABASE NOT FOUND`, plus non-printable-byte payloads). The naive port without the gate produces false-positives.

FireHOL extended list (8 additional files) intentionally NOT loaded — only Level 1 + Level 2.

See [.ai/enrichment.md](enrichment.md) for the full enrichment pipeline detail.

---

## Rule catalogue overview

Full catalogue with conditions, weights, and rationale lives in [.ai/rules.md](rules.md) and `app/rules.yaml` (the authoritative source for weights). Rule categories at a glance:

- **Hard-block** (`action: BLOCK`): threat-feed hits and IP2Proxy BOTNET classifications.
- **Threat-intel signal**: FireHOL Level 1/Level 2 matches at non-BLOCK severities; IP2Proxy proxy/VPN flags.
- **Device / IP type**: cloud, datacenter, residential classification; ASN-based signals.
- **Geo**: IP-country novelty, intercontinental jumps, impossible-travel-geo, long-distance new-IP. Weights calibrated against Jan-Mar 2026 measured FPR.
- **Identity**: email / phone / domain familiarity; HMAC-keyed presence rules; previously-rejected rules per customer (3B.5).
- **Value-anomaly**: 7 currency-aware rules consulting `shipment_value_threshold_<tier>` Context fields against `tenant_config.value_caps`.
- **Velocity**: SQL-counted per-customer / per-IP velocity over 1h / 24h windows.
- **Modification-specific** (Phase 3A.7): 8 rules for modification-path-only signals (time-since-booking buckets, magnitude, direction, modification velocity).
- **Baseline-aware** (per-customer): familiarity rules consuming `customer_baselines` stat-dicts. Includes `ip_fully_new_for_customer`, `ip_seen_count`, pair-novelty rules (`unfamiliar_ip_country_for_origin`, `unknown_destination_address`).
- **Case-3 (carrier-dropoff) compounds**:
  - `case_3_compound` (case-3a, established-customer compromise, weight 0.70, maturity_sensitive)
  - `cold_start_outbound_carrier_dropoff` (case-3b asymmetric, weight 0.65)
  - `cold_start_population_baseline_rare_with_carrier_dropoff` (case-3b sophisticated, weight 0.70)
- **Case-2 (API-key compromise) learning-based**: `api_booking_from_unfamiliar_asn` (weight 0.65, condition `is_api_booking AND unfamiliar_asn_for_customer`). Cold-start gate `customer_observations >= 10` is inside the `_asn_unfamiliar_for_customer` derivation. Replaces the deprecated `api_non_cloud_ip` + `non_cloud_established_account` pair.

Tuned weight cluster (current values; see `app/rules.yaml` for authoritative):
- `unfamiliar_ip_country_for_origin`: 0.15 (pair-novelty, intentionally low after case-2 detection moved to ASN rule)
- `unknown_destination_address`: 0.10
- `impossible_travel_geo`: 0.30
- `ip_intercontinental_jump`: 0.20
- `ip_country_change`: 0.15
- `ip_long_distance_new_ip`: 0.15

---

## DSL evaluator

Pure-Python `ast`-based parser, ~150 LOC, `app/dsl.py`. Compiles each `rules.yaml` rule's `condition` string at startup.

Whitelist (any other AST node → `DSLError`):
- `BoolOp` (with `And` / `Or`)
- `UnaryOp` (with `Not`)
- `Compare` (with `Gt` / `Lt` / `GtE` / `LtE` / `Eq` / `NotEq`)
- `Name` (env-lookup only; no attribute access; no subscript)
- `Constant` (int / float / str / bool / None only)
- `Load` context

Evaluation: `eval(code, {"__builtins__": {}}, env)` with `env` as a `MappingProxyType` over the Context dict.

Loader validates at startup that every `Name` referenced in `rules.yaml` resolves to a known Context field (fail-fast on unknown names).

Any change to `app/dsl.py` is never-skip review per CLAUDE.md.

---

## Multi-tenancy and RLS

Every tenant-scoped table carries `tenant_id` (FK to `tenants`). The per-request auth dependency sets the `app.tenant_id` Postgres session variable; RLS policies enforce isolation:

```sql
ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <name>
  USING (tenant_id = current_setting('app.tenant_id')::int);
```

### Tables with RLS

- `enterprises`, `customers`, `users` (migration 0001 `foundation`)
- `shipments`, `decisions`, `feedback` (migration 0002 `booking_flow`)
- `customer_baselines`, `tenant_route_baselines` (migration 0003 `baselines`)

### Tables WITHOUT RLS (intentional)

- `api_tokens` — the auth-time `SELECT ... FROM api_tokens WHERE token_hash = $1` runs BEFORE `app.tenant_id` is set, so RLS on this table would filter all rows out and break authentication.
- `app_users` — same auth-time access pattern.
- `tenants` — non-RLS as the canonical tenant directory; loaders defend in depth with explicit `WHERE id = $1`.
- `ip_enrichment` — global cache of IP-level facts shared across tenants.
- `global_blocked_vectors` — capability stub; cross-tenant intelligence sharing disabled in v1.

The runtime app connects as `riskd_app_login` (LOGIN role, no `BYPASSRLS`).

---

## Authentication and tokens

Bearer token authentication at the `/v1/*` edge.

- `app/auth.py` looks up the incoming token via `SELECT ... FROM api_tokens WHERE token_hash = $1` (HMAC-hashed at egress; the plaintext token is never persisted).
- The lookup runs BEFORE `set_tenant_id(conn, ...)`. This is why `api_tokens` and `app_users` do NOT have RLS — RLS would filter all rows out before the auth boundary establishes `app.tenant_id`.
- On hit, the loader sets `app.tenant_id` to the row's `tenant_id`. All subsequent queries in the request transaction are RLS-isolated.
- `auth.role` is sourced from `api_tokens.role`. `require_admin_role` enforces `auth.role == "admin"` for the admin endpoints.
- `api_tokens.last_used_at` is updated opportunistically; supported by index `ix_api_tokens_tenant_last_used`.
- `--rotate-token` in `scripts/tenant_onboard.py` REVOKES prior tokens (in-transaction DELETE) before issuing a new one.

### Runtime DB role

The application connects as `riskd_app_login` (LOGIN role). Both `riskd_app_login` and the prior `riskd_app` NOLOGIN role exist in the final schema for backward-compat; new GRANTs target the LOGIN role and the NOLOGIN role is the historical group owner.

Token plaintext lives only in transport (Authorization header) and in operator-managed secret stores. `app/signal_helpers.py::hmac_hex` is the egress canonical HMAC.

---

## Modification evaluation

`POST /api/v1/shipments/modification/evaluate` evaluates a `ModificationRequest` payload describing a change to an existing shipment. Architecture lives in `app/api/modification.py` + `app/context.py::build_modification_context`.

- 6 modification-specific Context fields: `modification_type`, `modification_magnitude`, `modification_time_bucket`, `modification_direction`, `modification_velocity_1h`, `modification_velocity_24h`.
- 8 modification-specific rules (3A.7 catalogue). See `.ai/rules.md` for conditions, weights, and maturity flags.
- Booking-path safety: `build_context` populates the 6 modification fields with neutral defaults (`modification_type='none'`, magnitudes/velocities zero, time bucket widest, direction unknown). The `'none'` literal matches no enum value the modification rules condition on, so they are structurally dormant on the booking path. Pinned by `test_modification_rules_dormant_under_booking_path_defaults`.
- The same `Decision`/`shipments` write path applies; modification persistence runs in the same single-txn pattern as booking.

---

## Feedback ingestion

`POST /api/v1/shipments/feedback` accepts operator-confirmed labels for prior bookings: `approved`, `rejected`, `fraud_confirmed`.

- Handler: `app/api/feedback.py`.
- On `approved` AND prior `decision_band != "ALLOW"`: fold the deferred observation into `customer_baselines` (the ALLOW-gated accumulation pattern). Re-enriches `source_ip` via the cached `ip_enrichment` row.
- On `rejected` / `fraud_confirmed`: write `add_rejected_observation` to `rejected_email_hmacs`, `rejected_phone_hmacs`, `origin_stats.r_n`, `ip_stats.r_n`. Updates `customers.flagged_count` and `customers.fraud_confirmed_count`.
- Monotonicity guard: feedback cannot revert an earlier `approved` to a worse state; ALLOW + approved does NOT re-fold.
- The four "previously-rejected" rules (`email_previously_rejected_for_customer`, `phone_previously_rejected_for_customer`, `origin_previously_rejected_for_customer`, `ip_previously_rejected_for_customer`) consume the `rejected_*` baseline columns at the next booking by the same customer. Structurally dormant for clean baselines.

---

## Production observability

### Backend

CloudWatch Embedded Metric Format (EMF). The same JSON line on stdout serves as both a structured log entry AND a metric point — the CloudWatch Logs agent ingests EMF-formatted lines directly without a separate metric pipeline. No Prometheus, no StatsD, no second container.

### Discriminator

`metric=True` keyword on the structured-log call site. The `emf_processor` (`app/observability.py`) short-circuits when the keyword is absent.

### Namespace

`FreightSentry/RiskD`. Single namespace for all v1 metrics.

### MetricSpec taxonomy

`METRIC_SPECS` in `app/observability.py` is the single source of truth. Each event family declares:

- **Dimensions**: low-cardinality grouping keys (e.g., `tenant_id`, `decision`, `role`). CloudWatch hashes the tuple per metric point — high-cardinality fields like `request_id` are structurally excluded.
- **Metrics**: numeric measurements with a CloudWatch unit (`Count`, `Milliseconds`, or unitless for normalized scores like `score`).
- **synthetic_count**: flag to emit a constant `count=1` for events that fire as "this happened once" without an inherent numeric payload (auth.success, cache.hit, idempotent_replay).

`triggered_rule_count` is DERIVED in the processor from `len(triggered_rules)`. The `triggered_rules` list stays in the log line as a regular field but is NOT promoted to a metric.

### High-cardinality guard

`request_id` is structurally incapable of being promoted to a dimension. The processor reads dimensions exclusively from `MetricSpec.dimensions`; it never iterates `event_dict` to discover keys.

### Unknown event handling

A `metric=True` event whose name is not in `METRIC_SPECS` passes through with a one-shot stderr warning, NOT silently dropped. Forward-compat for new metric=True call sites that predate their `MetricSpec` entry.

### Wire-up

The CloudWatch Logs agent on the production ECS task ingests stdout JSON; lines with an `_aws` block become metric points under `FreightSentry/RiskD`.

See [docs/observability.md](../docs/observability.md) for the full event family catalogue.

---

## Tenant-config caching

`app/tenant_config_cache.py` wraps `load_tenant_config`.

### Shape

- In-process dict keyed by `tenant_id`, value `(TenantConfig, loaded_at_monotonic)`.
- TTL: 60 seconds.
- Per-process scope. Multi-worker uvicorn deployments each carry their own cache; TTL bounds divergence at 60s.

### Concurrency

- Reads are lock-free dict lookups followed by a TTL check (`time.monotonic()` source via a `_now()` seam — module-level so tests can mock without poisoning asyncio's internal `time.monotonic` reads).
- Misses serialize per-tenant via `asyncio.Lock` — N concurrent requests for the same `tenant_id` produce exactly 1 DB load. Misses for *different* `tenant_id`s do NOT serialize against each other.
- Per-tenant lock creation uses `dict.setdefault(tenant_id, asyncio.Lock())` — atomic under CPython GIL. No meta-lock needed.

### Invalidation

**TTL-only.** A config write via `scripts/tenant_onboard.py` takes up to 60 seconds to propagate to all workers. The staleness window is acceptable because tenant config changes are operator-initiated narrowing/widening of an authenticated tenant's own settings — never a cross-tenant security boundary. Explicit per-tenant invalidate-on-write is out of scope for v1.

### Errors are not cached

`LookupError` (tenant missing) propagates and is NOT cached. A subsequent legitimate request retries the DB.

### Observability

Cache hit/miss events emit structured-log entries with `metric=True`; the EMF formatter consumes them.

### Test seam

`tenant_config_cache._reset_for_tests()` invalidates the cache between/inside tests where mid-test config changes are exercised.

---

## Latency budget

| Step | p95 |
|---|---|
| Validate + idempotency check | 5 ms |
| Load context (baseline FOR UPDATE, enrichment, velocity counts; sequential awaits on the txn connection) | 30-50 ms |
| Compute trust score | <1 ms |
| Run signal modules + rule evaluation | 20-40 ms |
| Score (3-layer noisy-OR + decide) | 5 ms |
| Persist (single txn: INSERT shipments + INSERT decisions + baseline save (if ALLOW) + UPDATE customers + UPSERT tenant_route_baselines) | 15-30 ms |
| **Total** | **<100 ms typical, <200 ms p95** |

Phase 5 load test enforces the budget end-to-end against staging Docker Compose.

---

## Out of scope (v1)

- LLM integration in any form (no Bedrock, Ollama, OpenAI, Anthropic)
- PDF report generation, CTO/CEO views, daily summaries, scheduled report jobs
- Per-rule per-customer rule-weight learning (Mechanism C) — post-launch
- Service split, multi-language, gRPC, proto files
- Redis or Redis Streams
- AI orchestrator, MCP integration, MCP server
- Operator dashboard, admin UI
- Device fingerprint rules
- User agent rules
- Federated auth (OAuth/SSO/SAML)
- Cross-tenant intelligence auto-sharing (capability stubbed in `global_blocked_vectors`, sharing disabled in v1)
- Go re-implementation of any kind
- Hot-reload of rules via fsnotify
- Customer-supplied custom signals or custom rules
- External DSL libraries
- `email_matches_customer_name` function
- Any read from external databases (no platform MySQL, no tenant-side DB calls)
- Trust-override mechanisms or signal-suppression patterns
- Negative-weight rules
- Persisted `trust_score` column
- Tenant registration endpoints (implicit registration via booking only)
- Write admin endpoints (read-only only in v1)
- Persisted `shipment_volume_30d` column
- Currency conversion via rates table
- Auto-rollback on deploy (manual operator step via ECS console)
- IaC (Terraform / CloudFormation / CDK) — AWS infra provisioned via GUI runbook in [docs/aws-deploy-runbook.md](../docs/aws-deploy-runbook.md)

Improvements surfaced during execution are captured in `.claude/BUGS.md` and triaged at phase boundaries; see [docs/history.md](../docs/history.md) for the historical trail of which decisions were rejected and why.

---

## Decision provenance

This document supersedes the bootstrap-prompt "Design Context" section where they conflict. Older versions and the phase-by-phase derivation narrative are preserved in [docs/history.md](../docs/history.md). Git history is the primary record of every change to this file — never delete content; let `git log .ai/decisions.md` hold the trail.
