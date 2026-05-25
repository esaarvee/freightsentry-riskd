# schema.md — Postgres schema reference

Single Postgres 16 database. No Redis, no MySQL, no streams. JSONB-heavy customer baselines. RLS-enforced multi-tenancy.

For migrations, see `alembic/versions/`. For the decisions that shaped this schema, see `.ai/decisions.md`.

---

## Phase 1 tables (12)

Created by the initial migration (1B.2). All tenant-scoped tables have an index on `tenant_id` (or a composite leading with it) and RLS policies enabled.

### `tenants`

Per-customer-of-ours record. One row per SaaS tenant.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `name` | text NOT NULL | display name |
| `config` | jsonb NOT NULL DEFAULT `'{}'` | validated against `TenantConfig` Pydantic model (Phase 4) |
| `first_seen` | timestamptz NOT NULL DEFAULT now() | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

No RLS (tenants themselves are the partitioning dimension).

### `enterprises`

Optional corporate-account grouping within a tenant.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `external_id` | text NOT NULL | tenant's stable enterprise ID |
| `first_seen` | timestamptz NOT NULL DEFAULT now() | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(tenant_id, external_id)`.
RLS: `tenant_isolation` policy on `tenant_id`.

### `customers`

Primary fraud-evaluation entity. Auto-created on first booking (implicit registration).

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `enterprise_id` | int NULL FK → enterprises(id) | |
| `external_id` | text NOT NULL | tenant's stable customer ID |
| `registered_address` | text NULL | populated on first sight; updated on newer payloads |
| `business_name` | text NULL | informational; not used by any v1 signal |
| `is_api_partner` | bool NOT NULL DEFAULT false | operator-set suppression flag |
| `first_seen` | timestamptz NOT NULL DEFAULT now() | drives account_age_days |
| `last_seen` | timestamptz NOT NULL DEFAULT now() | updated on every booking |
| `flagged_count` | int NOT NULL DEFAULT 0 | drives Layer 2 flag_prior |
| `fraud_confirmed_count` | int NOT NULL DEFAULT 0 | drives trust score |
| `total_shipments` | int NOT NULL DEFAULT 0 | monotonic lifetime count |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

**No `shipment_volume_30d` column** (per operator amendment 2026-05-25). 30-day counts compute on demand from `shipments`.

Constraints: `UNIQUE(tenant_id, external_id)`.
Indexes: `ix_customers_tenant_id` (for tenant-scoped queries).
RLS: `tenant_isolation`.

### `users`

Actor performing actions (within a customer). Auto-created on first booking.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `customer_id` | int NOT NULL FK → customers(id) | |
| `external_id` | text NOT NULL | tenant's stable user ID |
| `first_seen` | timestamptz NOT NULL DEFAULT now() | |
| `last_seen` | timestamptz NOT NULL DEFAULT now() | |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(tenant_id, customer_id, external_id)`.
RLS: `tenant_isolation`.

### `shipments`

Inbound booking events. INSERT-only.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `customer_id` | int NOT NULL FK → customers(id) | |
| `user_id` | int NOT NULL FK → users(id) | |
| `request_id` | text NOT NULL | idempotency token from request |
| `source_ip` | inet NOT NULL | |
| `origin` | jsonb NOT NULL | `{address, city, country, postal_code?}` |
| `destination` | jsonb NOT NULL | same shape |
| `value` | numeric(14,2) NOT NULL | |
| `channel` | text NOT NULL | `"api" | "web" | "portal" | ...` |
| `booking_ts` | timestamptz NOT NULL | from request payload |
| `created_at` | timestamptz NOT NULL DEFAULT now() | wall-clock arrival |

Constraints: `UNIQUE(tenant_id, request_id)` (idempotency).
Indexes:
- `ix_shipments_tenant_customer_booking_ts` `(tenant_id, customer_id, booking_ts)` — for customer velocity counts
- `ix_shipments_tenant_ip_booking_ts` `(tenant_id, source_ip, booking_ts)` — for IP velocity counts

RLS: `tenant_isolation`.

### `decisions`

Persisted output of each evaluation.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `shipment_id` | int NOT NULL FK → shipments(id) | |
| `request_id` | text NOT NULL | mirrors `shipments.request_id` for query convenience |
| `score` | numeric(5,4) NOT NULL | final noisy-OR score |
| `decision` | text NOT NULL | `"ALLOW" | "REVIEW" | "BLOCK"` |
| `classification` | text NOT NULL | `"GREEN" | "YELLOW" | "RED"` |
| `risk_level` | text NOT NULL | `"LOW" | "MEDIUM" | "HIGH" | "CRITICAL"` |
| `triggered_rules` | text[] NOT NULL DEFAULT `'{}'` | rule names that fired |
| `risk_factors` | jsonb NOT NULL DEFAULT `'[]'` | `[{name, description, weight}]` per fired rule |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(tenant_id, request_id)` (idempotency).
Indexes: `ix_decisions_tenant_shipment` `(tenant_id, shipment_id)`.
RLS: `tenant_isolation`.

### `feedback`

Operator-supplied outcomes for prior decisions.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `decision_id` | int NOT NULL FK → decisions(id) | |
| `label` | text NOT NULL | `"CONFIRMED_FRAUD" | "FALSE_POSITIVE" | "LEGITIMATE" | "INCONCLUSIVE"` |
| `reviewer_user_id` | text NULL | informational; not validated |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Indexes: `ix_feedback_tenant_decision` `(tenant_id, decision_id)`.
RLS: `tenant_isolation`.

### `customer_baselines`

Per-customer JSONB baseline. One row per customer.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `customer_id` | int NOT NULL FK → customers(id) | |
| `origin_stats` | jsonb NOT NULL DEFAULT `'{}'` | `{address: {n, r_n, last}}` |
| `dest_stats` | jsonb NOT NULL DEFAULT `'{}'` | same shape |
| `lane_stats` | jsonb NOT NULL DEFAULT `'{}'` | key = `f"{origin}||{destination}"` |
| `ip_stats` | jsonb NOT NULL DEFAULT `'{}'` | `{ip: {n, r_n, last, type}}` — `type` only on IP entries |
| `ip_netblock_stats` | jsonb NOT NULL DEFAULT `'{}'` | /24 key |
| `ip_asn_stats` | jsonb NOT NULL DEFAULT `'{}'` | ASN-org key |
| `country_stats` | jsonb NOT NULL DEFAULT `'{}'` | |
| `origin_ip_country_stats` | jsonb NOT NULL DEFAULT `'{}'` | key = `f"{origin}||{country}"` |
| `email_hmacs` | jsonb NOT NULL DEFAULT `'{}'` | `{hmac_hex: {n, r_n, last}}` |
| `phone_hmacs` | jsonb NOT NULL DEFAULT `'{}'` | |
| `rejected_email_hmacs` | jsonb NOT NULL DEFAULT `'{}'` | separate from approved (per `.ai/decisions.md`) |
| `rejected_phone_hmacs` | jsonb NOT NULL DEFAULT `'{}'` | |
| `email_domain_stats` | jsonb NOT NULL DEFAULT `'{}'` | |
| `phone_prefix_stats` | jsonb NOT NULL DEFAULT `'{}'` | first 3 digits HMAC |
| `ip_type_hist` | jsonb NOT NULL DEFAULT `'{}'` | `{"cloud": float, "dc": float, "residential": float}` |
| `hour_hist` | jsonb NOT NULL DEFAULT `'{}'` | keys 0-23 as str |
| `weekday_hist` | jsonb NOT NULL DEFAULT `'{}'` | keys 0-6 as str |
| `channel_hist` | jsonb NOT NULL DEFAULT `'{}'` | |
| `value_n` | numeric NOT NULL DEFAULT 0 | Welford count — exposed as `customer_observations` Context field |
| `value_mean` | numeric NOT NULL DEFAULT 0 | |
| `value_m2` | numeric NOT NULL DEFAULT 0 | |
| `cadence_n` | numeric NOT NULL DEFAULT 0 | hours-between-bookings Welford |
| `cadence_mean_h` | numeric NOT NULL DEFAULT 0 | |
| `cadence_m2_h` | numeric NOT NULL DEFAULT 0 | |
| `last_booking_ts` | timestamptz NULL | |
| `last_booking_lat` | numeric(8,5) NULL | |
| `last_booking_lon` | numeric(8,5) NULL | |
| `last_booking_country` | text NULL | |
| `decay_anchor_date` | date NULL | lazy decay anchor; advances on every successful write |
| `first_seen` | timestamptz NOT NULL DEFAULT now() | |
| `last_seen` | timestamptz NOT NULL DEFAULT now() | |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(tenant_id, customer_id)`.
Indexes: covered by the UNIQUE constraint.
RLS: `tenant_isolation`.

### `ip_enrichment`

Global IP-level facts. NOT tenant-scoped. Lazy-cached by `app/enrich.py`.

| Column | Type | Notes |
|---|---|---|
| `ip` | inet PK | IPv4 only in v1 |
| `country` | text NULL | MaxMind ISO code |
| `region` | text NULL | MaxMind subdivision |
| `city` | text NULL | MaxMind city |
| `lat` | numeric(8,5) NULL | MaxMind latitude |
| `lon` | numeric(8,5) NULL | MaxMind longitude |
| `asn_org` | text NULL | MaxMind ASN org name |
| `fh_level1` | bool NOT NULL DEFAULT false | FireHOL Level 1 match |
| `fh_level2` | bool NOT NULL DEFAULT false | FireHOL Level 2 match |
| `fh_lists` | text NULL | diagnostic; which lists matched |
| `is_cloud` | bool NOT NULL DEFAULT false | AWS/GCP/Azure/Cloudflare CIDR match |
| `cloud_provider` | text NULL | matched provider name |
| `is_datacenter` | bool NOT NULL DEFAULT false | `signal_helpers.is_datacenter_asn(asn_org)` |
| `is_proxy` | bool NOT NULL DEFAULT false | IP2Proxy `is_proxy` gated on non-sentinel `proxy_type` |
| `is_vpn` | bool NOT NULL DEFAULT false | `proxy_type == "VPN"` |
| `is_tor` | bool NOT NULL DEFAULT false | `proxy_type == "TOR"` |
| `proxy_type` | text NULL | IP2Proxy raw value |
| `threat` | text NULL | IP2Proxy threat tag (`BOTNET` / `SCANNER` / `SPAM`) |
| `updated_at` | timestamptz NOT NULL DEFAULT now() | freshness check (14-day TTL) |

No RLS — IP enrichment is intentionally global.

### `api_tokens`

Tenant-scoped API token lookup. SHA-256 hash storage; plaintext never persisted.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `token_hash` | text NOT NULL | `sha256(plaintext_token)` |
| `role` | text NOT NULL DEFAULT `'tenant'` | `"tenant" | "admin"` |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |
| `last_used_at` | timestamptz NULL | updated by auth dependency on each request |

Constraints: `UNIQUE(token_hash)`.
RLS: `tenant_isolation` (defense in depth — looking up a token by hash returns 0 rows if RLS-scoped, but token lookup happens BEFORE tenant context is set, so the auth dependency runs at app-role with RLS disabled for this query path; documented in `app/auth.py`).

### `app_users`

Phase 4 admin endpoint principals. Defined now to lock the RLS pattern.

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `tenant_id` | int NOT NULL FK → tenants(id) | |
| `external_id` | text NOT NULL | |
| `role` | text NOT NULL | `"admin"` only in v1 |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(tenant_id, external_id)`.
RLS: `tenant_isolation`.

### `global_blocked_vectors`

Capability stub for future cross-tenant intelligence sharing. **Sharing disabled in v1.**

| Column | Type | Notes |
|---|---|---|
| `id` | serial PK | |
| `vector_type` | text NOT NULL | `"IP" | "EMAIL" | "PHONE" | "RECIPIENT"` |
| `vector_hash` | text NOT NULL | for IP: inet text; for others: HMAC hex |
| `created_by_tenant_id` | int NOT NULL FK → tenants(id) | provenance |
| `share_enabled` | bool NOT NULL DEFAULT false | when true, lookup is cross-tenant; always false in v1 |
| `created_at` | timestamptz NOT NULL DEFAULT now() | |

Constraints: `UNIQUE(vector_type, vector_hash)`.
No RLS — intentionally global (sharing disabled by `share_enabled=false` until Phase 4+ enables it tenant-by-tenant).

---

## Stat-dict entry shape

JSONB shape for entries inside the stat-dict columns of `customer_baselines`:

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

---

## RLS pattern

```sql
ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <name>
  USING (tenant_id = current_setting('app.tenant_id')::int);
```

Per-request, the auth dependency sets the session variable:

```sql
SET LOCAL app.tenant_id = '<id>';
```

The app role (`riskd_app`) is created without `BYPASSRLS`. Migrations apply policies to every tenant-scoped table; `ip_enrichment` and `global_blocked_vectors` are intentionally global and have no policy.

---

## Index strategy

Phase 1 indexes documented per table above. Project-wide rules:

- Every tenant-scoped query leads with `tenant_id` in the WHERE clause AND in the index leading column.
- Composite indexes preferred over single-column when the typical query joins multiple conditions: `(tenant_id, customer_id, booking_ts)` covers velocity counts; a separate `(tenant_id, customer_id)` would be redundant.
- JSONB containment queries (`@>`, `?`, `?|`, `?&`) require GIN indexes — none added in Phase 1 (no queries against JSONB keys yet; rule conditions read JSONB via Python after `baseline.load`).
- All indexes named explicitly: `ix_<table>_<columns>` non-unique, `ux_<table>_<columns>` unique.

---

## Enum types (none in Phase 1)

Decisions, classifications, risk levels stored as `text` with values constrained at the Pydantic model layer. No SQL `CREATE TYPE` enums in Phase 1 — keeps migrations forward-compatible (adding a new decision value is a code-only change).

If a Phase 5+ migration introduces operator-relevant enum values (e.g. feedback labels), Postgres `CREATE TYPE` may make sense; revisit then.

---

## Migration discipline

All migrations Alembic-managed. Every migration defines `upgrade()` AND `downgrade()`; round-trip tested via `alembic downgrade base && alembic upgrade head` against a fresh Postgres.

Migration commits trigger the db-reviewer agent automatically per CLAUDE.md routing.

`schema_migrations` is Alembic's `alembic_version` table — no custom tracking layer in this project.
