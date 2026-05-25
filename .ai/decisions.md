# decisions.md — Architectural decisions

Permanent record of architectural choices for freightsentry-riskd. Distilled from the bootstrap-prompt Design Context, with operator amendments applied. Every decision below is **load-bearing** — when in doubt during execution, consult this file before improvising.

Amendments tracked inline with date footers. Changes that supersede decisions in this file land via new dated rows or section rewrites — older versions remain accessible via git history.

---

## Project identity

- Real-time fraud detection SaaS for freight aggregation platforms.
- Single Python service, no second process / language / storage engine.
- Postgres-only (PostgreSQL 16).
- Multi-tenant from day one. Every tenant-scoped table has `tenant_id`; Postgres Row-Level Security is the defensive backstop.
- Cost ceiling: CAD 1000/month operational, combined production + test/staging.
- Latency target: <200ms p95 on all evaluations.
- Scale ceiling: 100 TPS sustained, ~45K shipments/day average, peak-of-peak under 100 TPS five years out at 20% YoY growth.

---

## Endpoints (the four in v1)

1. `POST /api/v1/shipments/booking/evaluate`
2. `POST /api/v1/shipments/modification/evaluate`
3. `POST /api/v1/shipments/feedback`
4. `GET /health/`

Two read-only admin endpoints land in Phase 4:
- `GET /api/v1/admin/customers/{id}/baseline` (admin-role auth; PII fields HMAC'd in response)
- `GET /api/v1/admin/decisions/{request_id}` (admin-role auth; full decision details)

No tenant-registration endpoint. No write-admin endpoints. Operator scripts (`scripts/tenant_onboard.py`) handle onboarding.

Implicit entity registration: customer / enterprise / user records auto-upsert from the first booking payload that references them. Booking payload carries optional metadata (registered_address, business_name, enterprise_id, etc.) which populates the records on first sight and can update on subsequent bookings.

---

## Scoring architecture — 3-layer noisy-OR

### Layer 1: Hard-block short-circuit

Any rule with `action: BLOCK` that fires returns immediately with score 1.0, decision BLOCK, no further evaluation.

Two BLOCK rules ship in Phase 1:
- `blacklisted_ip` — IP in FireHOL Level 1 threat feed → BLOCK
- `ip2p_threat_botnet_block` — IP2Proxy PX11 flags IP as BOTNET → BLOCK

Other BLOCK rules (confirmed-fraud-IP-list, etc.) land as the feature mature; the layer's machinery accepts an unbounded count.

### Layer 2: Account prior — Phase 2

Continuous customer-state contribution. **Implemented in Phase 2**, alongside trust-score consumption. Phase 1 ships scoring without Layer 2 (declared break — final score = signal_score, not noisyOR(account_prior, signal_score)).

```
base_prior        = MaxNewAccount * (1 - maturity)
trust_risk        = max(0, (0.5 - trust_score) / 0.5)
trust_contribution = trust_risk * TrustFactor
flag_prior        = flag_weights[flagged_count_tier]
account_prior     = noisyOR(base_prior, trust_contribution, flag_prior)
```

Where:
- `maturity = clamp(age_days / maturity_age_days, 0, 1) * clamp(shipments / maturity_shipments, 0, 1)`
- `flagged_count_tier`: 0 → 0, 1-2 → 1, 3-5 → 2, 6+ → 3

Constants (tenant-overridable via `tenants.config` from Phase 4):

| Constant | Value | Notes |
|---|---|---|
| `MaxNewAccount` | 0.10 | |
| `TrustFactor` | 0.25 | |
| `flag_weights` | `[0.00, 0.15, 0.25, 0.35]` | 4 tiers per `flagged_count_tier` mapping above |
| `maturity_age_days` | 180 | |
| `maturity_shipments` | 50 | |
| `MaturityK` | 0.30 | new customers' maturity-sensitive rules fire at 70% weight |

Verification doc §3.3 notes FreightSentry production uses `MaturityK=0.70` and 2-tier `flag_weights`; this project intentionally diverges to the foundation values pending Phase 6 staging-replay measurement. The Phase 6 report calibrates these against measured FPR.

### Layer 3: Signal noisy-OR with maturity downweighting

For each fired non-BLOCK rule:

```
if rule.maturity_sensitive:
    effective_weight = rule.weight * (1 - MaturityK * (1 - maturity))
else:
    effective_weight = rule.weight
signal_score = noisyOR(effective_weights of fired rules)
```

### Final score and thresholds

`final = noisyOR(account_prior, signal_score)` once Layer 2 lands in Phase 2. In Phase 1, `final = signal_score`.

Thresholds (initial; per-tenant overridable via `tenants.config` from Phase 4):
- `allow_max = 0.60` → ALLOW (GREEN)
- `block_min = 0.80` → BLOCK (RED)
- Between → REVIEW (YELLOW)

Risk-level bands: <0.30 LOW, <0.60 MEDIUM, <0.80 HIGH, ≥0.80 CRITICAL.

Source-of-truth for thresholds is `app/rules.yaml`. No Pydantic dataclass defaults — avoid drift between the two sources (per verification §2.3).

---

## Trust score — computed on read, never persisted

Continuous customer-level value in [0, 1]. Computed in `app/trust.py::compute_trust_score(customer, baseline) -> float` per request, in `build_context` after `baseline.decay_to(today)`.

Inputs (already loaded by build_context):
- `account_age_days` from `customers.first_seen`
- `effective_observations` from `customer_baselines` (post-decay)
- `flagged_count` from `customers`
- `fraud_confirmed_count` from `customers`

Sub-millisecond per call (pure arithmetic, no I/O).

**Do NOT persist `trust_score` as a column.** Trust depends on `effective_observations` which decays with time — a persisted value goes stale on every read after the write date. Computing on read has zero staleness risk and zero meaningful cost.

In Phase 1, the computed value is attached to Context but no Phase 1 rule conditions read it. Phase 2 Layer 2 plus the 11 trust-conditioned FreightSentry-port rules (see Rule catalogue section) consume it.

---

## Customer baseline

Per-customer JSONB columns. Stat-dict entry shape: `{n, r_n, last}` plus `type` field for IP-keyed entries only.

- `n` — decay-weighted approved-observation count
- `r_n` — decay-weighted rejected-observation count (anti-signal from feedback)
- `last` — ISO date of most recent observation
- `type` (IP entries only) — `"cloud" | "dc" | "residential"` (omitted for unknown)

### Dimensions

Stat-dicts (frequency-recency maps):
- `origin_stats`, `dest_stats`, `lane_stats` — route geography (lane key = `f"{origin}||{destination}"`)
- `ip_stats` (with `type` per entry), `ip_netblock_stats` (/24 key), `ip_asn_stats`, `country_stats`, `origin_ip_country_stats`
- `email_hmacs`, `phone_hmacs`, `email_domain_stats`, `phone_prefix_stats`
- `rejected_email_hmacs`, `rejected_phone_hmacs` (separate columns; not collapsed into `r_n` of the approved sets — operator-confirmed choice)

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

`value_n` (post-decay) is exposed to rule conditions via the `customer_observations` Context field — the decay-weighted activity proxy in lieu of a persisted 30-day count (per operator amendment 2026-05-25).

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

---

## Persistence

**Synchronous within the same transaction as baseline update** (operator amendment 2026-05-25; supersedes the bootstrap prompt's "background, non-blocking" framing).

Single txn per booking/modification request:
1. `SELECT FOR UPDATE` on `customer_baselines` (locks the row for this transaction)
2. INSERT into `shipments`
3. INSERT into `decisions`
4. `baseline.add_observation(...)` + `baseline.save(conn)` (UPDATE on customer_baselines)
5. UPDATE `customers` (last_seen, total_shipments increment)
6. Commit

Persistence failure → 500 to caller. Retry-safe via idempotency on `(tenant_id, request_id)`.

The bootstrap-prompt phrase "Audit writes happen as background asyncio tasks in the same process" was meant to forbid a separate worker process (vs FreightSentry's async-worker), NOT to make decision writes fire-and-forget after the response.

No persisted `shipment_volume_30d` column (operator amendment 2026-05-25). 30-day window counts compute on demand via `COUNT(*) FROM shipments WHERE booking_ts > now() - interval '30 days'`. Rules wanting decay-weighted activity read `customer_baselines.value_n`.

---

## Per-tenant configuration

Single `tenants.config JSONB` column. Schema validated at write time by `app/config_tenant.py::TenantConfig` (Pydantic v2).

Initial schema:
```python
class TenantConfig(BaseModel):
    allow_max: float = 0.60
    block_min: float = 0.80
    country_blocklist: list[str] = []
    country_allowlist: list[str] = []
    value_caps: dict[str, float] = {}      # per currency
    cold_start_days: int = 30
    is_api_partner_default: bool = False
```

Phase 4 ships the schema validation + onboarding script. Phase 1 reads `tenants.config` directly without validation (Pydantic round-trip lands in Phase 4). In-process cache for hot tenants from Phase 5.

---

## Cold start

For the first `cold_start_days` (per-tenant config, default 30):
- Universal signals work day 1 (threat feeds, hard-blocked vectors, disposable-email patterns)
- Per-tenant onboarding rules active day 1 (country blocklists, value caps)
- Mid-band scores route to REVIEW more aggressively (compress the ALLOW band via the `cold_start_days` window in tenant config)
- Per-customer cold-start within a tenant handled naturally by Layer 2 — `base_prior = MaxNewAccount * (1 - maturity)` elevates new-customer scores; maturity-sensitive rules fire softer.

---

## IP enrichment sources

All four, lazy-cached in `ip_enrichment` Postgres table keyed by IP. Cache freshness: 14 days; on staleness, re-enrich. Sources accessed only by `scripts/fetch_enrichment.py` (offline refresh) — no live source calls on the request path.

| Source | URL | Auth | License | Refresh |
|---|---|---|---|---|
| MaxMind GeoLite2 City + ASN | `https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-{City,ASN}&suffix=tar.gz&license_key=…` | Free signup → account_id + license_key | GeoLite2 EULA (free use, attribution, no redistribution) | Tue/Fri weekly |
| FireHOL Level 1 + Level 2 | `https://github.com/firehol/blocklist-ipsets` (raw `firehol_level1.netset`, `firehol_level2.netset`) | None | CC-BY-SA / public-domain | Daily |
| IP2Proxy LITE PX11 | `https://lite.ip2location.com/database-download?database=PX11LITEBIN&token=…` | Free signup → token | CC-BY-SA 4.0 | Monthly |
| Cloud provider CIDRs | AWS: `https://ip-ranges.amazonaws.com/ip-ranges.json` · GCP: `https://www.gstatic.com/ipranges/cloud.json` · Azure: weekly download from `https://www.microsoft.com/en-us/download/details.aspx?id=56519` · Cloudflare: `https://www.cloudflare.com/ips-v4` | None | Public | Continuously (AWS), weekly (GCP/Azure), rare (Cloudflare) |

Secret storage: `FG_MAXMIND_LICENSE_KEY`, `FG_IP2PROXY_DOWNLOAD_TOKEN` (Pydantic Settings; AWS Secrets Manager in prod).

`is_proxy` from IP2Proxy is gated on non-empty `proxy_type` (sentinel values: `""`, `-`, `INVALID IP ADDRESS`, `NOT SUPPORTED`, `INVALID DATABASE FILE`, `DATABASE NOT FOUND`, plus non-printable-byte payloads). Naive port without the gate produces false-positives.

FireHOL extended list (8 additional files) intentionally NOT loaded — only Level 1 + Level 2.

---

## Rule catalogue target

Phase 1: 12-15 rules wiring 10 initial signals (excludes trust-conditional rules).
Phase 2: extended to ~95-100 rules (freight_risk 84-rule offline subset + ~13 FreightSentry-port rules).

FreightSentry-port rules (Phase 2):
- Trust-conditioned (11): `very_low_trust`, `low_trust_high_value`, `low_trust_new_route`, `low_trust_vpn`, `mid_trust_new_route_value`, `daily_volume_low_trust_ui`, `daily_volume_low_trust_api`, `ip_velocity_low_trust_ui`, `ip_velocity_low_trust_api`, `threat_score_moderate`, `flags_with_value`
- Dormancy (3): `dormant_vpn`, `dormant_new_ip`, `ip_distance_dormant`
- Customer-lock-in (2): `cloud_api_customer_deviation_iptype`, `locked_customer_unfamiliar_ip`
- Residential proxy farm (1): `residential_asn_high_velocity`

Recipient-overlap rules (`recipient_used_by_many_customers`, `recipient_used_by_very_many_customers`) are in freight_risk's 84-rule base, NOT a FreightSentry port (per verification §1.2).

Tuned thresholds carried from freight_risk:
- `cadence_anomaly`: z > 6 (not z > 4 — weekend false-positive avoidance)
- `velocity_spike_daily_api`: 50 (not 5000)
- `residential_asn_high_velocity`: 15 (not 5)
- `ip_familiarity_tier`: /24-only family-familiar (no "cloud + ASN" shortcut)

Modification-specific signals (Phase 3 — fresh design):
- Time-since-booking buckets (within-hour, same-day, multi-day)
- Magnitude of change
- Direction of change (residential→commercial benign; legitimate→freight-forwarder suspicious)
- Modification velocity per customer

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

## Out of scope (v1, all phases unless explicitly pulled in by operator)

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
- `email_matches_customer_name` function (constraint #14 — out of scope)
- Any read from external databases (no platform MySQL, no tenant-side DB calls)
- Trust-override mechanisms or signal-suppression patterns
- Negative-weight rules
- Persisted `trust_score` column
- Tenant registration endpoints (implicit registration via booking only)
- Write admin endpoints (read-only only in Phase 4)
- Persisted `shipment_volume_30d` column (operator amendment 2026-05-25)

If a related improvement surfaces during execution, capture it in `MASTER_PLAN_AMENDMENTS.md` as a deferred follow-up. Do not pull into the current phase.

---

## Latency budget

| Step | p95 |
|---|---|
| Validate + idempotency check | 5ms |
| Load context (baseline, enrichment, customer/enterprise, velocity counts via SQL, all via `asyncio.gather`) | 30-50ms |
| Compute trust score | <1ms |
| Run signal modules in parallel | 20-40ms |
| Score (3-layer noisy-OR + decide) | 5ms |
| Persist (single txn: INSERT shipments + INSERT decisions + baseline save + UPDATE customers) | 15-30ms |
| **Total** | **<100ms typical, <200ms p95** |

Per operator amendment 2026-05-25: persistence is on the hot path; the budget accommodates it. Original Design Context allocated 0ms in-line persistence; this row captures the corrected allocation.

Phase 5 load test enforces the budget end-to-end against staging Docker Compose.

---

## Multi-tenancy

Every tenant-scoped table has `tenant_id` (FK to `tenants`). Per-request auth dependency sets `app.tenant_id` Postgres session variable; RLS policies enforce isolation:

```sql
ALTER TABLE <name> ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON <name>
  USING (tenant_id = current_setting('app.tenant_id')::int);
```

App role connects to Postgres as `riskd_app` (no `BYPASSRLS`).

Global (non-tenant-scoped) tables:
- `ip_enrichment` — shared IP-level facts across tenants
- `global_blocked_vectors` — capability stub; cross-tenant intelligence sharing disabled in v1

---

## Decision provenance

This document supersedes the bootstrap-prompt "Design Context" section where they conflict. Operator amendments (dated rows above) supersede this document where they conflict.

Subsequent decisions accumulate here as new sections or supersede older sections with dated change markers. Never delete content; let git history hold the trail.

Last full review: 2026-05-25 (Phase 1, commit 1A.4).
