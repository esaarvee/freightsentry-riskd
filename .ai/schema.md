# schema.md — Current schema (post-squash, 5 migrations)

Single Postgres 16 database. No Redis, no MySQL, no streams. JSONB-heavy customer baselines. RLS-enforced multi-tenancy.

This document describes the **current** schema as produced by running `alembic upgrade head` against an empty Postgres 16. It is a navigation aid, not a source of truth.

- **Source of truth**: the five migration files under `alembic/versions/` (`0001_foundation.py` through `0005_runtime_roles.py`). When this document conflicts with the migrations, the migrations win.
- **Anti-drift gate**: [`tests/integration/test_schema_golden.py`](../tests/integration/test_schema_golden.py) compares a live `pg_dump` against `tests/golden/schema.sql` under a canonical normalizer. The golden file is the byte-equivalence anchor established in Phase 8A.0; any DDL change without a regenerated golden fails CI.
- **Architectural rationale**: see [`.ai/decisions.md`](decisions.md) for the load-bearing choices (single-store, RLS as defence-in-depth, JSONB stat-dicts, lazy decay, HMAC at egress, the non-superuser runtime role).
- **Squash history**: the 11 → 5 migration squash, the auth-table RLS drop, and the pre-squash phase chain are documented in [`docs/history.md`](../docs/history.md).

The migration chain — `0001_foundation` → `0002_booking_flow` → `0003_baselines` → `0004_enrichment_global` → `0005_runtime_roles` — is linear; each migration includes both `upgrade()` and `downgrade()` and is round-trip tested by the schema-golden gate.

---

## `0001_foundation` — bootstrap tables

Creates the `riskd_app` NOLOGIN permissions role and the auth / tenant-root tables: `tenants`, `enterprises`, `customers`, `users`, `api_tokens`, `app_users`. Enables RLS on the three business-data tables (`enterprises`, `customers`, `users`). The auth tables (`api_tokens`, `app_users`) **intentionally skip RLS** — see the migration's module docstring and the RLS pattern section below for the auth chicken-and-egg rationale.

### Role: `riskd_app`

`CREATE ROLE riskd_app NOLOGIN` — permissions container. Subsequent migrations re-issue broad GRANTs (`SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public`; `USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public`) to cover newly-created objects. The LOGIN-capable companion `riskd_app_login` lands in `0005_runtime_roles`.

Idempotent guard: `DO $$ ... duplicate_object` block so re-runs against an already-populated cluster succeed (matters for the `docker compose up` local-dev path).

### Table: `tenants`

Per-customer-of-ours record. One row per SaaS tenant. The partitioning dimension — no RLS (tenants are not scoped to themselves).

- `id` serial PRIMARY KEY
- `name` text NOT NULL — display name
- `config` jsonb NOT NULL DEFAULT `'{}'::jsonb` — validated against the `TenantConfig` Pydantic model on read by `load_tenant_config`
- `first_seen` timestamptz NOT NULL DEFAULT `now()`
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- `updated_at` timestamptz NOT NULL DEFAULT `now()` — comment: "Last time the tenant row (including config JSONB) was modified. Populated by load_tenant_config (Phase 4A) and updated by scripts/tenant_onboard.py."

Indexes: none beyond the PK.
RLS: none (partitioning dimension).

### Table: `enterprises`

Optional corporate-account grouping within a tenant.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `external_id` text NOT NULL — tenant's stable enterprise ID
- `first_seen` timestamptz NOT NULL DEFAULT `now()`
- `created_at` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- `ux_enterprises_tenant_external` UNIQUE (`tenant_id`, `external_id`)

Indexes:
- `ix_enterprises_tenant_id` (`tenant_id`)

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `customers`

Primary fraud-evaluation entity. Auto-created on first booking (implicit registration).

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `enterprise_id` int NULL REFERENCES `enterprises(id)`
- `external_id` text NOT NULL — tenant's stable customer ID
- `registered_address` text NULL — populated on first sight; updated on newer payloads
- `business_name` text NULL — informational; not consumed by any rule
- `is_api_partner` boolean NOT NULL DEFAULT `false` — operator-set suppression flag
- `first_seen` timestamptz NOT NULL DEFAULT `now()` — drives `account_age_days`
- `last_seen` timestamptz NOT NULL DEFAULT `now()` — updated on every booking
- `flagged_count` int NOT NULL DEFAULT `0` — drives flag-prior signal
- `fraud_confirmed_count` int NOT NULL DEFAULT `0` — drives trust score
- `total_shipments` int NOT NULL DEFAULT `0` — monotonic lifetime count
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- `registered_country` varchar(2) NULL — comment: "ISO 3166-1 alpha-2 country code supplied by platform integration on booking commits. Drives case-3b detection via the customer_destination_country_mismatch_outbound derivation (build_context) and the tenant_route_baselines population (6A.7 upsert). Pydantic enforces shape at ingress (CustomerData.registered_country, ^[A-Z]{2}$)."

No `shipment_volume_30d` column. 30-day counts compute on demand from `shipments`.

Constraints:
- `ux_customers_tenant_external` UNIQUE (`tenant_id`, `external_id`)

Indexes:
- `ix_customers_tenant_id` (`tenant_id`)

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `users`

Actor performing actions (within a customer). Auto-created on first booking.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `customer_id` int NOT NULL REFERENCES `customers(id)`
- `external_id` text NOT NULL — tenant's stable user ID
- `first_seen` timestamptz NOT NULL DEFAULT `now()`
- `last_seen` timestamptz NOT NULL DEFAULT `now()`
- `created_at` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- `ux_users_tenant_customer_external` UNIQUE (`tenant_id`, `customer_id`, `external_id`)

Indexes: none beyond the PK and the UNIQUE constraint (which itself covers tenant-leading lookups).
RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `api_tokens`

Tenant-scoped API token lookup. SHA-256 hash storage; plaintext never persisted. **NO RLS** — see the foundation migration's module docstring and the RLS pattern section below for the auth chicken-and-egg rationale.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `token_hash` text NOT NULL — `sha256(plaintext_token)`
- `role` text NOT NULL DEFAULT `'tenant'` — `'tenant'` or `'admin'`
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- `last_used_at` timestamptz NULL — updated by the auth dependency on each request

Constraints:
- `ux_api_tokens_token_hash` UNIQUE (`token_hash`)

Indexes:
- `ix_api_tokens_tenant` (`tenant_id`)
- `ix_api_tokens_tenant_last_used` (`tenant_id`, `last_used_at DESC NULLS LAST`) — comment: "Supports stale-token queries (least-recently-used / unused tokens per tenant). NULLS LAST so never-used tokens sort at the tail of DESC scans."

RLS: **none** (intentional). The auth dependency runs `SELECT FROM api_tokens WHERE token_hash = $1` BEFORE the endpoint handler issues `set_tenant_id` — there is no tenant to set yet because `tenant_id` IS the result of the auth lookup. Each token's secret is itself the credential (UNIQUE `token_hash`), so the table-level RLS is unnecessary at the data layer. The auth lookup runs as `riskd_app_login` (a non-superuser; does not bypass RLS) — so the absence of a policy is what permits the lookup, not a bypass.

### Table: `app_users`

Admin endpoint principals. **NO RLS** — same auth-lookup rationale as `api_tokens`.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `external_id` text NOT NULL
- `role` text NOT NULL — `'admin'` only in v1
- `created_at` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- `ux_app_users_tenant_external` UNIQUE (`tenant_id`, `external_id`)

Indexes:
- `ix_app_users_tenant` (`tenant_id`)

RLS: **none** (intentional; see `api_tokens`).

### Grants issued in `0001_foundation`

- `GRANT USAGE ON SCHEMA public TO riskd_app`
- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app`
- `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app`

The `ON ALL TABLES IN SCHEMA public` form covers `alembic_version` (created by Alembic before the upgrade SQL runs) and every table created in this migration. Subsequent migrations re-issue the broad grants to cover newly-created tables (idempotent on already-granted objects).

---

## `0002_booking_flow` — request / decision / feedback tables

Creates `shipments`, `decisions`, `feedback`, all RLS-enabled. Folds in the Phase 2B `destination_hmac`, the Phase 3A `request_type` discriminator on decisions, the Phase 3B feedback shape (no `decision_id` FK; opaque `operator_id`), the Phase 3B PII HMAC columns on shipments, and the Phase 5A.7 widened-UNIQUE on decisions. Re-issues the broad grants to cover the new tables.

### Table: `shipments`

Inbound booking events. INSERT-only. Idempotency contract: `UNIQUE (tenant_id, request_id)`.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `customer_id` int NOT NULL REFERENCES `customers(id)`
- `user_id` int NOT NULL REFERENCES `users(id)`
- `request_id` text NOT NULL — idempotency token from request
- `source_ip` inet NOT NULL
- `origin` jsonb NOT NULL — `{address, city, country, postal_code?}`
- `destination` jsonb NOT NULL — same shape as `origin`
- `value` numeric(14, 2) NOT NULL
- `channel` text NOT NULL — `'api' | 'web' | 'portal' | ...`
- `booking_ts` timestamptz NOT NULL — from request payload
- `created_at` timestamptz NOT NULL DEFAULT `now()` — wall-clock arrival
- `destination_hmac` text NOT NULL — HMAC of the destination address; populated by the booking endpoint at write time
- `email_hmac` text NULL — comment: "HMAC of the email present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no email was supplied in the request."
- `phone_hmac` text NULL — comment: "HMAC of the phone present on this shipment, computed via signal_helpers.hmac_hex at booking-write time. NULL on rows written before Phase 3B or when no phone was supplied in the request."

Constraints:
- `ux_shipments_tenant_request` UNIQUE (`tenant_id`, `request_id`) — idempotency.

Indexes:
- `ix_shipments_tenant_customer_booking_ts` (`tenant_id`, `customer_id`, `booking_ts`) — customer velocity counts.
- `ix_shipments_tenant_ip_booking_ts` (`tenant_id`, `source_ip`, `booking_ts`) — IP velocity counts.
- `ix_shipments_tenant_dest_hmac_booking_ts` (`tenant_id`, `destination_hmac`, `booking_ts`) — destination-address velocity counts via HMAC (Phase 2B).

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `decisions`

Persisted output of each evaluation. The UNIQUE idempotency key is widened to `(tenant_id, request_type, request_id)` so a booking and a modification can legitimately share a `request_id` per the public idempotency contract (Phase 5A.7).

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `shipment_id` int NOT NULL REFERENCES `shipments(id)`
- `request_id` text NOT NULL — mirrors the request's idempotency token
- `score` numeric(5, 4) NOT NULL — final noisy-OR score
- `decision` text NOT NULL — comment: "One of ALLOW | REVIEW | BLOCK; final routing outcome from the scorer"
- `classification` text NOT NULL — comment: "One of GREEN | YELLOW | RED; presentation tier paired with decision"
- `risk_level` text NOT NULL — comment: "One of LOW | MEDIUM | HIGH | CRITICAL; score-band classification independent of decision"
- `triggered_rules` text[] NOT NULL DEFAULT `'{}'::text[]` — rule names that fired
- `risk_factors` jsonb NOT NULL DEFAULT `'[]'::jsonb` — `[{name, description, weight}]` per fired rule
- `created_at` timestamptz NOT NULL DEFAULT `now()`
- `request_type` text NOT NULL DEFAULT `'booking'` — comment: "One of booking | modification; discriminates which evaluate endpoint produced this decision. DEFAULT booking preserved as safety net — endpoints supply request_type explicitly in 3A.6."

Constraints:
- `ck_decisions_request_type` CHECK (`request_type IN ('booking', 'modification')`)

Indexes:
- `ix_decisions_tenant_shipment` (`tenant_id`, `shipment_id`)
- `ix_decisions_tenant_request_type_created` (`tenant_id`, `request_type`, `created_at`) — supports per-type recent-decision lookups (Phase 3A).
- `ux_decisions_tenant_request_type` UNIQUE (`tenant_id`, `request_type`, `request_id`) — comment: "UNIQUE idempotency key. Replaces 0001 flat (tenant_id, request_id) constraint so booking and modification with the same request_id are valid (Phase 5A.7)."

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `feedback`

Operator-supplied outcomes for prior decisions. Phase 3B bootstrap shape: **no `decision_id` FK** (target resolution goes through `decisions.request_id` lookup at the endpoint layer); **no FK to `app_users`** (operator_id is opaque tenant-supplied text from the start).

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `request_id` text NOT NULL — comment: "Per-POST idempotency token. UNIQUE (tenant_id, request_id) prevents replay double-apply."
- `target_request_id` text NOT NULL — comment: "request_id of the prior booking/modification this feedback targets. Indexed for monotonicity lookups."
- `label` text NOT NULL — constrained to `'approved' | 'rejected' | 'fraud_confirmed'`
- `feedback_ts` timestamptz NOT NULL — comment: "Event time (operator-supplied). server-side created_at is the persistence timestamp."
- `note` text NULL
- `operator_id` text NULL — comment: "Opaque tenant-supplied operator identifier (text). Not an FK; Phase 4 may layer validation via TenantConfig."
- `created_at` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- `ux_feedback_tenant_request` UNIQUE (`tenant_id`, `request_id`) — replay protection.
- `ck_feedback_label` CHECK (`label IN ('approved', 'rejected', 'fraud_confirmed')`)

Indexes:
- `ix_feedback_tenant_target` (`tenant_id`, `target_request_id`) — supports monotonicity lookups (the prior decision being feedback'd against).

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Grants re-issued in `0002_booking_flow`

- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app`
- `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app`

Re-issued to cover `shipments`, `decisions`, `feedback` and their sequences.

---

## `0003_baselines` — baseline persistence

Creates the per-customer JSONB-heavy baseline table and the per-tenant route-baseline histogram. Folds in the Phase 6A.1 `country_route_stats` JSONB column on `customer_baselines` and the Phase 6A.6 `tenant_route_baselines` table. Includes a seed `INSERT ... SELECT` for `tenant_route_baselines` (no-op on a fresh DB; correctly populates on dev DBs with existing shipments). Re-issues the broad grants.

### Table: `customer_baselines`

One row per customer. Stat-dict entries follow the shape `{key: {n, r_n, last, type?}}`; `type` only on entries inside `ip_stats`. Per-IP-type decay is applied on read by `baseline.decay_to(as_of)` using the `type` field.

- `id` serial PRIMARY KEY
- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `customer_id` int NOT NULL REFERENCES `customers(id)`
- `origin_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — `{address: {n, r_n, last}}`
- `dest_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — same shape
- `lane_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — key = `f"{origin}||{destination}"`
- `ip_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — comment: "Stat-dict {ip: {n, r_n, last, type}} where type is cloud|dc|residential"
- `ip_netblock_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — /24 key
- `ip_asn_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — ASN-org key
- `country_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb`
- `origin_ip_country_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — key = `f"{origin}||{country}"`
- `email_hmacs` jsonb NOT NULL DEFAULT `'{}'::jsonb` — `{hmac_hex: {n, r_n, last}}`
- `phone_hmacs` jsonb NOT NULL DEFAULT `'{}'::jsonb`
- `rejected_email_hmacs` jsonb NOT NULL DEFAULT `'{}'::jsonb` — separate from approved per the rejected-prior design
- `rejected_phone_hmacs` jsonb NOT NULL DEFAULT `'{}'::jsonb`
- `email_domain_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb`
- `phone_prefix_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — first 3 digits HMAC
- `ip_type_hist` jsonb NOT NULL DEFAULT `'{}'::jsonb` — `{"cloud": float, "dc": float, "residential": float}`
- `hour_hist` jsonb NOT NULL DEFAULT `'{}'::jsonb` — keys 0-23 as string
- `weekday_hist` jsonb NOT NULL DEFAULT `'{}'::jsonb` — keys 0-6 as string
- `channel_hist` jsonb NOT NULL DEFAULT `'{}'::jsonb`
- `value_n` numeric NOT NULL DEFAULT `0` — comment: "Welford count; post-decay, exposed to rule conditions as customer_observations"
- `value_mean` numeric NOT NULL DEFAULT `0`
- `value_m2` numeric NOT NULL DEFAULT `0`
- `cadence_n` numeric NOT NULL DEFAULT `0` — hours-between-bookings Welford
- `cadence_mean_h` numeric NOT NULL DEFAULT `0`
- `cadence_m2_h` numeric NOT NULL DEFAULT `0`
- `last_booking_ts` timestamptz NULL
- `last_booking_lat` numeric(8, 5) NULL
- `last_booking_lon` numeric(8, 5) NULL
- `last_booking_country` text NULL — comment: "ISO country code from MaxMind GeoLite2 lookup at last booking"
- `decay_anchor_date` date NULL — comment: "Lazy-decay anchor date; advances on every successful baseline save"
- `first_seen` timestamptz NOT NULL DEFAULT `now()`
- `last_seen` timestamptz NOT NULL DEFAULT `now()`
- `updated_at` timestamptz NOT NULL DEFAULT `now()`
- `country_route_stats` jsonb NOT NULL DEFAULT `'{}'::jsonb` — comment: "Per-customer (origin_country, destination_country) route-pair histogram. Keys are \"{origin_country}||{destination_country}\" composite strings; values are observation counts. Populated by baseline updater on shipment commit. Consumed by build_context to derive shipment_route_unfamiliar_for_customer (case-3a signal)."

Constraints:
- `ux_customer_baselines_tenant_customer` UNIQUE (`tenant_id`, `customer_id`)

Indexes: covered by the UNIQUE constraint.

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Table: `tenant_route_baselines`

Per-tenant population frequency of `(customer_country, origin_country, destination_country)` triples. Synchronously upserted on each booking commit (Phase 6A.7) and consumed by `derive_route_rarity` (Phase 6A.8) to produce the `shipment_route_rare_for_tenant` signal.

- `tenant_id` int NOT NULL REFERENCES `tenants(id)`
- `customer_country` varchar(2) NOT NULL
- `origin_country` varchar(2) NOT NULL
- `destination_country` varchar(2) NOT NULL
- `observation_count` bigint NOT NULL DEFAULT `0`
- `last_updated` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- PRIMARY KEY (`tenant_id`, `customer_country`, `origin_country`, `destination_country`) — composite PK doubles as the natural row-lookup index.

Indexes: none beyond the composite PK. The PK's leading-column (`tenant_id`) BTREE serves the tenant-wide total-observations sum the rarity derivation needs (Phase 6A.8); no separate single-column index needed.

Table comment: "Per-tenant population frequency of (customer_country, origin_country, destination_country) triples. Populated synchronously on each booking commit (6A.7 UPSERT) and consumed by Phase 6A.8 derive_route_rarity to produce the shipment_route_rare_for_tenant signal used by the cold_start_population_baseline_rare_with_carrier_dropoff rule (6A.9)."

RLS: `tenant_isolation` USING `(tenant_id = current_setting('app.tenant_id')::int)`.

### Seed: backfill `tenant_route_baselines`

The migration runs a separate `op.execute(SEED_SQL)` after the schema additions. The seed:

```sql
INSERT INTO tenant_route_baselines (
    tenant_id, customer_country, origin_country, destination_country, observation_count
)
SELECT
    s.tenant_id,
    c.registered_country  AS customer_country,
    s.origin->>'country'  AS origin_country,
    s.destination->>'country' AS destination_country,
    COUNT(*) AS observation_count
FROM shipments s
JOIN customers c
    ON c.id = s.customer_id
    AND c.tenant_id = s.tenant_id
WHERE c.registered_country IS NOT NULL
  AND s.origin->>'country' IS NOT NULL
  AND s.destination->>'country' IS NOT NULL
GROUP BY 1, 2, 3, 4;
```

Pre-launch this yields 0 rows (no customers with `registered_country` set; no shipments). On dev DBs populated mid-Phase-6 it back-fills the histogram from the existing join. Separating the seed `op.execute` ensures any error fails the migration loudly rather than producing partial state.

### Grants re-issued in `0003_baselines`

- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app`
- `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app`

Covers `customer_baselines` + `customer_baselines_id_seq` and `tenant_route_baselines` (no sequence; composite PK).

---

## `0004_enrichment_global` — intentionally-global tables

Creates `ip_enrichment` and `global_blocked_vectors`. Both **intentionally skip RLS** — they have no `tenant_id` column and the shared-data semantics are part of the design, not an oversight. Re-issues the broad grants.

### Table: `ip_enrichment`

Global IP-level facts. NOT tenant-scoped. Lazy-cached by `app/enrich.py` with 14-day freshness. Primary key is the IP itself (no surrogate id; UPSERT on conflict).

- `ip` inet PRIMARY KEY — IPv4 only in v1
- `country` text NULL — MaxMind ISO code
- `region` text NULL — MaxMind subdivision
- `city` text NULL — MaxMind city
- `lat` numeric(8, 5) NULL — MaxMind latitude
- `lon` numeric(8, 5) NULL — MaxMind longitude
- `asn_org` text NULL — MaxMind ASN org name
- `fh_level1` boolean NOT NULL DEFAULT `false` — FireHOL Level 1 match
- `fh_level2` boolean NOT NULL DEFAULT `false` — FireHOL Level 2 match
- `fh_lists` text NULL — diagnostic; which lists matched
- `is_cloud` boolean NOT NULL DEFAULT `false` — AWS/GCP/Azure/Cloudflare CIDR match
- `cloud_provider` text NULL — matched provider name
- `is_datacenter` boolean NOT NULL DEFAULT `false` — `signal_helpers.is_datacenter_asn(asn_org)`
- `is_proxy` boolean NOT NULL DEFAULT `false` — IP2Proxy `is_proxy` gated on non-sentinel `proxy_type`
- `is_vpn` boolean NOT NULL DEFAULT `false` — `proxy_type == "VPN"`
- `is_tor` boolean NOT NULL DEFAULT `false` — `proxy_type == "TOR"`
- `proxy_type` text NULL — IP2Proxy raw value
- `threat` text NULL — IP2Proxy threat tag (`BOTNET` / `SCANNER` / `SPAM`)
- `updated_at` timestamptz NOT NULL DEFAULT `now()` — freshness check (14-day TTL)

Indexes: none beyond the PK.

Table comment: "Intentionally global (no RLS): IP enrichment is shared across tenants".

RLS: **none** (intentional; global cache).

### Table: `global_blocked_vectors`

Capability stub for future cross-tenant intelligence sharing. **`share_enabled = false` in v1.** The architectural design retains cross-tenant scope so a future toggle can opt enterprises into sharing without a schema migration.

- `id` serial PRIMARY KEY
- `vector_type` text NOT NULL — `'IP' | 'EMAIL' | 'PHONE' | 'RECIPIENT'`
- `vector_hash` text NOT NULL — for IP: inet text; for others: HMAC hex
- `created_by_tenant_id` int NOT NULL REFERENCES `tenants(id)` — provenance
- `share_enabled` boolean NOT NULL DEFAULT `false` — when true, lookup is cross-tenant; always false in v1
- `created_at` timestamptz NOT NULL DEFAULT `now()`

Constraints:
- `ux_global_blocked_vectors_type_hash` UNIQUE (`vector_type`, `vector_hash`)

Indexes: none beyond the PK and UNIQUE constraint.

Table comment: "Intentionally global (no RLS): capability stub for cross-tenant sharing; share_enabled=false in v1".

RLS: **none** (intentional; sharing disabled by `share_enabled=false` until a future phase enables it tenant-by-tenant).

### Grants re-issued in `0004_enrichment_global`

- `GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO riskd_app`
- `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO riskd_app`

Covers `ip_enrichment` (no sequence; inet PK) and `global_blocked_vectors` + `global_blocked_vectors_id_seq`.

---

## `0005_runtime_roles` — non-superuser runtime role

Creates the LOGIN-capable companion `riskd_app_login` for the runtime DB connection. Final migration in the squashed chain. No tables, no RLS DDL.

### Role: `riskd_app_login`

```sql
CREATE ROLE riskd_app_login WITH LOGIN INHERIT PASSWORD 'riskd_app_login_dev';
GRANT riskd_app TO riskd_app_login;
```

Role comment: "Runtime DB connection role (Phase 5D). LOGIN INHERIT; receives all grants of riskd_app via the GRANT below. Local-dev password; production rotates from Secrets Manager."

`LOGIN INHERIT` so grants on `riskd_app` propagate transparently. The runtime `DATABASE_URL` connects as `riskd_app_login` (not as `postgres`); this is the load-bearing switch that makes RLS active at runtime — `postgres` is a superuser and bypasses RLS by definition, so the pre-5D dev path silently dropped tenant isolation.

**LOCAL-DEV PASSWORD ONLY.** Production deployment MUST rotate this password from AWS Secrets Manager before exposing the service. The deploy step either recreates the role with a SecretsManager-sourced password or runs `ALTER ROLE ... PASSWORD '...'` from the deploy script. Tracked in [`docs/history.md`](../docs/history.md) under the Phase 5D security audit.

Idempotent guard: `DO $$ ... duplicate_object` block so re-runs against an already-populated cluster succeed.

### What's NOT in this migration

The original chain included `0009_drop_rls_on_auth_tables.py` (drop RLS on `api_tokens` and `app_users`). The squash skips this entirely — `0001_foundation` never creates RLS on those tables in the first place. Final-state schema is byte-equivalent to the pre-squash chain. The full reasoning is in the `0001_foundation` module docstring and cross-referenced from [`docs/history.md`](../docs/history.md).

---

## Stat-dict entry shape

JSONB shape for entries inside the stat-dict columns of `customer_baselines` (`origin_stats`, `dest_stats`, `lane_stats`, `ip_stats`, `ip_netblock_stats`, `ip_asn_stats`, `country_stats`, `origin_ip_country_stats`, `email_hmacs`, `phone_hmacs`, `rejected_email_hmacs`, `rejected_phone_hmacs`, `email_domain_stats`, `phone_prefix_stats`):

```jsonc
{
  "<key>": {
    "n": <float>,           // decay-weighted approved-observation count
    "r_n": <float>,         // decay-weighted rejected-observation count (feedback-driven)
    "last": "<YYYY-MM-DD>", // most recent observation date
    "type": "<cloud|dc|residential>"   // ONLY on entries inside ip_stats; omitted otherwise
  }
}
```

Per-IP-type half-lives are applied on read by `baseline.decay_to(as_of)` using the `type` field. Non-IP entries use the uniform 90-day half-life.

The histogram-style stat-dicts (`ip_type_hist`, `hour_hist`, `weekday_hist`, `channel_hist`) and the route-pair histogram (`country_route_stats`) use a flat `{key: <float>}` shape — no `{n, r_n, last}` envelope, no decay envelope. Welford-tracked numerics (`value_n` / `value_mean` / `value_m2`; `cadence_n` / `cadence_mean_h` / `cadence_m2_h`) live as plain numeric columns, not inside any JSONB.

---

## RLS pattern

Every tenant-scoped table receives:

```sql
ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <name>
    USING (tenant_id = current_setting('app.tenant_id')::int);
```

Per-request, the auth dependency sets the session variable after the auth lookup resolves a tenant:

```sql
SET LOCAL app.tenant_id = '<id>';
```

`SET LOCAL` scopes the setting to the current transaction; FastAPI's per-request transaction lifecycle ensures it does not leak across requests sharing a pooled connection.

### Tables with RLS

Phase 0001 / 0002 / 0003 business-data tables:
- `enterprises`, `customers`, `users` (foundation)
- `shipments`, `decisions`, `feedback` (booking flow)
- `customer_baselines`, `tenant_route_baselines` (baselines)

### Tables intentionally without RLS

| Table | Reason |
|---|---|
| `tenants` | Partitioning dimension; not scoped to itself. |
| `api_tokens` | Auth-time lookup runs **before** `set_tenant_id`. `tenant_id` IS the result of the lookup. The UNIQUE `token_hash` is the credential; table-level RLS is vestigial under the post-5D non-superuser runtime model. See `0001_foundation` module docstring for the full chicken-and-egg trace. |
| `app_users` | Same auth-lookup rationale as `api_tokens`. |
| `ip_enrichment` | Intentionally global — IP enrichment is shared across tenants by design (no `tenant_id` column). |
| `global_blocked_vectors` | Capability stub for cross-tenant sharing; `share_enabled=false` in v1 but the architectural scope is cross-tenant. |

### Runtime role and RLS

The runtime DB connection runs as `riskd_app_login` (LOGIN INHERIT from `riskd_app`; NOT a superuser; does not bypass RLS). This is the load-bearing change introduced by `0005_runtime_roles`. Migrations themselves run as `postgres` (which bypasses RLS during DDL); only the application runtime connects as the non-superuser role. Tests that exercise RLS policies must connect as `riskd_app_login` (or a non-superuser equivalent) — connecting as `postgres` masks RLS bugs.

---

## Index strategy

Indexes documented per-table above. Project-wide rules:

- Every tenant-scoped query leads with `tenant_id` in the WHERE clause AND in the index leading column. Composite indexes preferred over single-column when the typical query joins multiple conditions.
- Customer-velocity composite: `(tenant_id, customer_id, booking_ts)` on `shipments` covers the per-customer velocity counts; a separate `(tenant_id, customer_id)` would be redundant given the leading-column rule.
- IP-velocity composite: `(tenant_id, source_ip, booking_ts)` on `shipments` covers per-IP velocity counts.
- Destination-address velocity (Phase 2B): `(tenant_id, destination_hmac, booking_ts)` on `shipments` covers the HMAC'd destination address-fan signal.
- Per-request-type decision lookups (Phase 3A): `(tenant_id, request_type, created_at)` on `decisions` supports `booking`-vs-`modification` filtered recent-decision queries.
- Idempotency keys are UNIQUE indexes with a `ux_` prefix (`ux_shipments_tenant_request`, `ux_decisions_tenant_request_type`, `ux_feedback_tenant_request`, `ux_customer_baselines_tenant_customer`). Non-unique indexes use the `ix_` prefix.
- Stale-token discovery (Phase 5A): `(tenant_id, last_used_at DESC NULLS LAST)` on `api_tokens` supports least-recently-used queries; NULLS LAST puts never-used tokens at the tail of DESC scans.
- Feedback monotonicity lookup (Phase 3B): `(tenant_id, target_request_id)` on `feedback` supports the prior-decision lookup at feedback-write time.
- JSONB containment queries (`@>`, `?`, `?|`, `?&`) require GIN indexes — none added in v1. Rule conditions read JSONB via Python after `baseline.load`; there are no SQL-side JSONB containment queries in the hot path.
- Composite PK on `tenant_route_baselines` doubles as both the natural row-lookup index and the leading-column BTREE for tenant-wide aggregation.

All indexes are named explicitly: `ix_<table>_<columns>` for non-unique, `ux_<table>_<columns>` for UNIQUE. The naming is load-bearing because the schema-golden gate normalizes pg_dump output by sorting lines — unnamed indexes get Postgres-assigned names which would not be stable across regenerations.

---

## Enum types (none)

Decisions, classifications, risk levels, feedback labels, and request-type discriminators are stored as `text` with values constrained by CHECK constraints (`ck_decisions_request_type`, `ck_feedback_label`) and at the Pydantic model layer. No SQL `CREATE TYPE` enums in v1 — keeps migrations forward-compatible (adding a new enum value via CHECK constraint relaxation is simpler than `ALTER TYPE`).

---

## Migration discipline

All migrations are Alembic-managed. Every migration defines `upgrade()` AND `downgrade()`; round-trip is verified by the schema-golden gate against a fresh Postgres 16. The chain is linear (no branches; `down_revision` always points to the immediately-prior numeric revision).

### Anti-drift gate

[`tests/integration/test_schema_golden.py`](../tests/integration/test_schema_golden.py) is the byte-equivalence anchor. It:

1. Runs `alembic upgrade head` against a fresh database.
2. Captures `pg_dump --schema-only --no-comments --no-owner`.
3. Applies a canonical normalizer (drop blank lines, drop comment lines, drop psql metacommands, sort the remaining lines).
4. Compares against `tests/golden/schema.sql` (also normalized).

If the test fails after an intentional schema change, regenerate the golden file with the procedure documented in the test's module docstring. The gate was established in Phase 8A.0 specifically to anchor the 11 → 5 migration squash; it remains in place for all future schema work.

### Comments are load-bearing

Per CLAUDE.md, migration `COMMENT ON COLUMN` / `COMMENT ON TABLE` / `COMMENT ON INDEX` statements are part of the audit trail. The squash preserves every comment from the pre-squash chain verbatim. New schema work MUST include comments explaining non-obvious column semantics (especially: any column populated by a derivation in `build_context`; any column with a CHECK constraint; any index whose name does not make its purpose obvious).

### Review routing

Per CLAUDE.md "Triage gate and reviewer routing":
- Any change to `alembic/versions/`, `*.sql`, or ORM/Pydantic model files invokes **db-reviewer** in addition to the standard panel.
- RLS DDL (`CREATE POLICY`, `ENABLE ROW LEVEL SECURITY`) is **never-skip** — full panel runs regardless of triage routing.
- Comment-only edits to existing migrations are still **never-skip** under the migration carve-out — migration comments are load-bearing for audit.

### Squash history

The 11 → 5 squash landed in Phase 8A.1. Each squashed migration's module docstring lists which pre-squash migrations folded in and why each fold preserves byte-equivalence under the canonical normalizer. The full pre-squash chain and the rationale for the squash boundaries are documented in [`docs/history.md`](../docs/history.md).
