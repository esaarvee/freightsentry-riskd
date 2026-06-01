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

### Admin endpoint scope (Phase 4D — 2026-06-01)

Both Phase 4 admin endpoints are READ-ONLY and TENANT-BOUNDED:

- `GET /api/v1/admin/decisions/{request_id}` — full decision detail + linked shipment data (city + country only; full address NOT surfaced).
- `GET /api/v1/admin/customers/{external_id}/baseline` — customer record + truncated baseline (stat-dicts top-10 by `n` desc + `total_count` + `truncated` flag).

Authorization: `require_admin_role` (`app/auth.py`) checks `auth.role == "admin"`, returns 403 otherwise. `auth.role` is sourced from `api_tokens.role` (Phase 1 schema). `app_users.role` exists but is not wired to auth in Phase 4 (Phase 5+ may add multi-user admin model).

Cross-tenant lookups return 404 — hides existence per security-by-default convention.

Admin write endpoints (decision overrides, manual feedback, etc.) are out of scope for v1 per `## Out of scope`. v2+ may introduce a separate admin write surface with workflow approvals.

Stat-dict truncation: customer baseline endpoint truncates each stat-dict to top-10 by `n` desc. Full dicts deferred to Phase 5+ if a use case emerges.

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

#### Amendment 2026-05-26 (Phase 2A planning) — formula divergences from FreightSentry `scorer.go:300-415`

The Layer 2 formula above is **Design-Context-authoritative**. Reading FreightSentry's reference implementation at `services/rules-engine/internal/scoring/scorer.go:300-415` surfaces four substantive divergences. We follow the Design Context (above), not the reference. Each divergence is intentional; Phase 6 staging replay measures FPR/recall at the resulting operating point.

1. **Maturity is multiplicative, not `min` of fractions.** The Design Context says `maturity = clamp(age_frac, 0, 1) * clamp(ship_frac, 0, 1)`. FreightSentry's `accountMaturity()` returns `min(clamped_age_frac, clamped_ship_frac)`. The multiplicative product is more conservative when both factors are moderate: `(0.5, 0.5)` → 0.25 here vs 0.5 in FreightSentry. The conservative form means brand-new and moderate-tenure customers retain higher account_prior contribution; maturity-downweight kicks in slower.

2. **Shipments fraction is linear, not log-scaled.** Design Context: `total_shipments / maturity_shipments` clamped to [0, 1]. FreightSentry: `log1p(shipments) / log1p(maturity_shipments)`. Linear means a customer with 25 of 50 needed shipments reaches `ship_frac = 0.5`; log-scaled would reach `log1p(25)/log1p(50) ≈ 0.83`. The linear form penalizes new customers harder for the first ~50 shipments — appropriate for our scale (per-tenant ~45K shipments/day target; the first 50 are a small fraction).

3. **Flag prior is a 4-tier direct lookup, not 2-tier noisy-OR.** Design Context: `flag_prior = FLAG_WEIGHTS[flagged_count_tier]` over four tiers (0/1-2/3-5/6+) mapping to `(0.00, 0.15, 0.25, 0.35)`. FreightSentry's `flagContribution()` evaluates two thresholds (`flagged_count > 2` and `> 5`) and noisy-ORs them. The 4-tier table gives finer-grained behavior at the low-flag boundary (1-2 flags contribute 0.15 instead of nothing), and the direct-lookup table is simpler than a noisy-OR composition over independent tier activations.

4. **No customer-inheritance term.** FreightSentry's `accountMaturity()` optionally folds in an enterprise-level "customer maturity" via `CustomerMaturityAgeDays / CustomerMaturityShipments / CustomerInheritanceFactor` (default 730 / 500 / 0.50). The Design Context formula uses single-customer maturity only. We have no Phase 2 enterprise-aggregate to inherit from; the new project's `customers.enterprise_id` FK provides the grouping, but rolling maturity across an enterprise is post-launch tuning. Phase 2 ships single-customer maturity.

#### Constants live in `app/scoring_constants.py`, not `rules.yaml`

The Layer 2 + maturity constants are scoring-formula machinery, not rule parameters. They land as Python module constants in `app/scoring_constants.py` (Phase 2A). `app/rules.yaml` continues to own only `allow_max` and `block_min`. Pydantic-settings does NOT carry them. Single source of truth; rebinding requires a code change reviewed under the never-skip rule (CLAUDE.md). Per-tenant overrides land in Phase 4 via `tenants.config`.

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

### TenantConfig design (Phase 4A — 2026-06-01)

Phase 4A operationalizes the per-tenant configuration layer described above. The following choices were made during 4A planning + execution and are operator-approved.

**Column reuse, not addition.** `tenants.config` (already in `alembic/versions/0001_initial.py:42` as `jsonb NOT NULL DEFAULT '{}'`) is the storage column. The Phase 4 prompt initially referenced `tenants.config_json` as a new column; that was a drafting inconsistency with the pre-existing schema. 4A reuses the existing column.

**Module path.** `app/tenant_config.py` (Phase 4 prompt path; supersedes the earlier `app/config_tenant.py` reference at the top of this section).

**Final field set** (as shipped in 4A.1):

```python
class TenantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: int                                       # gt=0; supplied by loader, not stored in JSONB
    config_version: int = 0                              # ge=0; bumped on every config change

    # Override fields — None means "use scoring_constants.py default"
    maturity_age_days: int | None = None                 # gt=0; default 180
    maturity_shipments: int | None = None                # gt=0; default 50
    maturity_k: float | None = None                      # 0.0-1.0; default 0.30
    value_caps: dict[str, dict[str, float]] | None = None  # {currency: {tier: threshold}}

    # Non-None defaults
    allowed_currencies: list[str] = ["USD"]              # ISO 4217 3-letter
    cold_start_grace_days: int = 0                       # ge=0

    # Metadata — supplied by the loader from row columns
    created_at: datetime
    updated_at: datetime
```

`app/scoring_constants.py` REMAINS the source of truth for project defaults; TenantConfig overrides on top. The `allow_max`/`block_min`/`country_blocklist`/`country_allowlist`/`is_api_partner_default` fields in the historical sketch above did NOT land in 4A — `allow_max`/`block_min` continue to live in `app/rules.yaml` per Phase 2A discipline, and country lists + api_partner_default are out of scope until a consumer exists.

**`value_caps` shape: 4-tier per-currency.** `dict[currency: str, dict[tier: str, threshold: float]]` where `tier ∈ {high, new_user, medium, low}` — matches the 4 distinct thresholds in the 7 currency-implicit rules 4B rewrites. Adding a 5th tier is a model change reviewed under the standard panel. `value_caps == None` means "use DEFAULT_VALUE_CAPS at the 4B consumer" (USD-implicit, Phase 2 thresholds).

**Loading semantics: per-request fresh load.** `load_tenant_config(conn, tenant_id)` runs inside every booking/modification/feedback endpoint's transaction, AFTER `set_tenant_id(conn, auth.tenant_id)` and BEFORE any other DB read. The `tenants` table is intentionally non-RLS (`0001_initial.py:36-37`); the loader uses explicit `WHERE id = $1` as defense-in-depth. No caching in Phase 4. Phase 5 wraps the loader with a 60s in-process TTL cache (carry-forward).

**Validation timing.** Read path: `parse_config_jsonb` validates JSONB → Pydantic every load; stored-data corruption surfaces as `pydantic.ValidationError` propagating through the endpoint (no try/except in 4A — Phase 4D/5 may translate to 500 with structured log). Write path: `scripts/tenant_onboard.py::_validate_initial_config` validates before INSERT/UPDATE.

**JSONB codec discipline.** asyncpg returns JSONB as `str` by default in this project (no `set_type_codec` registered). The loader's cast-at-boundary pattern handles BOTH codec paths (str via `json.loads`, dict via direct cast) so a future codec registration is non-breaking. Phase 3B lesson applied.

**Migration 0005.** Added `tenants.updated_at timestamptz NOT NULL DEFAULT now()` for staleness tracking. The onboarding script and admin write endpoints (post-v1) bump it on every config change.

**Onboarding script** (`scripts/tenant_onboard.py`): idempotent UPSERT-by-name with `pg_advisory_xact_lock(hashtext(external_id))` to serialize concurrent runs (since `tenants.name` has no UNIQUE constraint today). Sets `set_config('app.tenant_id', tenant_id, true)` before any `api_tokens` query so the script works under the production non-superuser `riskd_app` role. `--rotate-token` REVOKES prior tokens (in-transaction DELETE) before issuing a new one.

**Phase 4A non-consumers.** No rule in 4A reads tenant_config — the parameter is threaded through `build_context` and `build_modification_context` as a passthrough. 4B (currency normalization) adds 5 ctx fields from `value_caps`. 4C (cold-start enforcement) adds tenant-config consultation inside `score()`.

**Carry-forward items** (post-4A):
- `UNIQUE (name)` on `tenants` would replace the advisory-lock pattern with `INSERT ... ON CONFLICT (name) DO UPDATE` (BUGS.md candidate).
- Destructive `tenants.config` overwrite on re-run of the onboarding script is intentional per docstring; an `--overwrite-config` flag may be added later.
- Private `_hash_token` import in the script suggests promoting to a public `hash_api_token` helper.
- Phase 5 in-process TTL cache wrapping `load_tenant_config`.

---

## Currency normalization (RESOLVED in Phase 4B, 2026-06-01)

**Decision (2026-05-28)**: All absolute-value thresholds in `app/rules.yaml` carry an implicit-USD assumption. Per-currency normalization deferred to Phase 4. **Resolved in Phase 4B** — see "Phase 4B resolution" subsection below.

### Scope of the implicit-USD assumption

The following Phase 2 rules in `app/rules.yaml` compare `shipment_value` against absolute literal thresholds. All assume USD:

| Rule | Threshold | Currency assumption |
|---|---|---|
| `vpn_high_value` | `shipment_value > 1000` | USD |
| `low_trust_high_value` | `shipment_value > 1000` | USD |
| `flags_with_value` | `shipment_value > 2000` | USD |
| `threat_intel_high_value` | `shipment_value > 2000` | USD |
| `ip2p_threat_high_value` | `shipment_value > 2000` | USD |
| `high_value_new_user` | `shipment_value > 5000` | USD |
| `absolute_high_value` | `shipment_value > 10000` | USD |

The `shipment_value` Context field is set directly from `BookingRequest.shipment.value` (a `Decimal` per `app/models.py`) with no transformation. `BookingRequest.shipment` has no `currency` field today — USD is presumed at the application boundary.

Tenants whose business operates in CAD / EUR / GBP cannot use these rules accurately without per-tenant calibration.

### Modification-specific note (Phase 3A scope)

The Phase 3A modification rule `modification_within_30_min_value_increase` (3A.7) uses `modification_magnitude > 0.2` — a currency-independent fraction computed as `abs(new_value - old_value) / old_value`. This rule does **not** inherit the implicit-USD assumption.

The other 7 modification rules (3A.7) condition on categorical fields (type, time bucket, direction, velocity) and do not introduce currency complexity.

### Deferral to Phase 4

Phase 4 will:

1. Add `TenantConfig.value_caps: dict[str, float]` (e.g., `{"USD": 10000, "CAD": 12500, "EUR": 9000}`).
2. Add an optional `currency: Literal["USD", "CAD", "EUR", ...]` field to `BookingRequest.shipment` and `ModificationRequest.new_value` where the value semantics apply.
3. Rewrite the 7 absolute-value rule conditions to consult tenant config:
   `shipment_value > tenant.value_caps.get(currency, tenant.value_caps['USD'])`.
4. Provide a Phase 4 migration helper to populate the default `value_caps` for existing tenants (all `{"USD": <current threshold>}` — no behavior change for USD-implicit tenants).

This deferral is intentional: Phase 3's scope is endpoint additions, not configuration model expansion. Mixing the two would conflate two different change axes.

### What this means today

- USD-implicit tenants are calibrated correctly out of the box.
- Non-USD tenants will see rule thresholds that don't match their currency. Options:
  1. **Wait for Phase 4** (recommended for production launch).
  2. **Provide values pre-converted to USD** at the integration boundary (operator-side conversion). Adequate for staging; not a long-term solution.

### Auditing

The 7-rule corpus is enumerated above. If Phase 4+ adds another absolute-value rule, this section must be updated.

### Phase 4B resolution (2026-06-01)

Implemented per the deferral plan:

1. `BookingRequest.shipment.currency` and `ModificationRequest.currency` added as optional `str` fields with `"USD"` default. Validation: 3-letter uppercase ISO 4217 shape at the Pydantic layer; allowed-list check at request time against `tenant_config.allowed_currencies` (400 if not in list).
2. `TenantConfig.value_caps: dict[str, dict[str, float]] | None` carries per-currency-per-tier thresholds. 4-tier scheme: `high / new_user / medium / low` matches the 4 distinct thresholds in the 7 rewritten rules.
3. `DEFAULT_VALUE_CAPS = {"USD": {"high": 10000, "new_user": 5000, "medium": 2000, "low": 1000}}` (`app/tenant_config.py`) matches Phase 2 hardcoded thresholds. USD-default tenants see zero behavioral change.
4. `resolve_value_caps(tenant_config, currency)` resolves per-request, falling back to USD defaults with a `tenant_config.value_caps.fallback` structured warning (metric=True for Phase 5 EMF) if the tenant has an allowed currency without a matching value_caps entry.
5. The 7 rules in `app/rules.yaml` were rewritten to consult `shipment_value_threshold_<tier>` Context fields populated in `build_context`. Weights and maturity-sensitive flags unchanged. Modification rule 1 (`modification_within_30_min_value_increase`) was NOT rewritten — its `modification_magnitude > 0.2` is a fraction, currency-independent.
6. **Case-1 (dashboard ATO) and case-2 (API ATO) regression assertions pass unchanged with USD-default tenants** (the surgical invariance check for the rewrite).

### Currency conversion via rates table — explicitly rejected

Considered and rejected during Phase 4 planning. Reasons:
- Requires maintained rates data with refresh cadence.
- Float arithmetic against decay-weighted Welford accumulators introduces compounding precision drift.
- Per-currency thresholds are operator-tunable per tenant via `value_caps` and require no daily upkeep.

Currency conversion can be revisited if v2 demands cross-currency risk aggregation; it is out of scope for v1.

---

## Cold start

For the first `cold_start_days` (per-tenant config, default 30):
- Universal signals work day 1 (threat feeds, hard-blocked vectors, disposable-email patterns)
- Per-tenant onboarding rules active day 1 (country blocklists, value caps)
- Mid-band scores route to REVIEW more aggressively (compress the ALLOW band via the `cold_start_days` window in tenant config)
- Per-customer cold-start within a tenant handled naturally by Layer 2 — `base_prior = MaxNewAccount * (1 - maturity)` elevates new-customer scores; maturity-sensitive rules fire softer.

### Per-tenant maturity overrides (Phase 4C — 2026-06-01)

`app/scoring.py::score` consults `tenant_config` for the three Layer 2 + Layer 3 maturity constants:

| Constant | Override field | Project default |
|---|---|---|
| `maturity_age_days` | `tenant_config.maturity_age_days` | 180 (`MATURITY_AGE_DAYS`) |
| `maturity_shipments` | `tenant_config.maturity_shipments` | 50 (`MATURITY_SHIPMENTS`) |
| `maturity_k` | `tenant_config.maturity_k` | 0.30 (`MATURITY_K`) |

`None` on a TenantConfig override means "use project default from `app/scoring_constants.py`". The constants module REMAINS source of truth; TenantConfig is overrides on top.

The Phase 2A scoring formula is unchanged (multiplicative maturity, linear shipments fraction, 4-tier flag prior, no customer-inheritance). Only the thresholds consulted change.

### Cold-start grace period (Phase 4C — 2026-06-01)

`tenant_config.cold_start_grace_days` (default 0; disabled) — during the grace window after tenant onboarding (measured from `tenants.created_at`), the maturity formula multiplies its computed value by 0.5. After the window, no multiplier.

Rationale: a newly-onboarded tenant has no accumulated baselines, so maturity-sensitive rules may fire too aggressively on legitimate first customers. The 0.5 multiplier softens scoring during the grace window, biasing toward REVIEW rather than BLOCK while the tenant builds baselines.

The 0.5 multiplier is hardcoded — not tenant-configurable in Phase 4. Phase 6 staging replay measures FPR impact and may revise.

Per-customer cold-start (a customer is new to a mature tenant) is NOT affected by this mechanism — that's handled by Layer 2 base_prior already. `cold_start_grace_days` is tenant-wide.

### Composition

Grace × maturity composition with a maturity-sensitive rule (weight 0.6):

| Maturity state | m | K=0.30 effective weight |
|---|---|---|
| Mature (post-grace, ≥180 days, ≥50 shipments) | 1.0 | 0.60 |
| Grace-active, mature customer (m_raw=1.0) | 0.5 | 0.51 |
| Brand-new at default tenant (m_raw=0.0) | 0.0 | 0.42 |

Grace creates an intermediate behavior path "softer than mature, harder than brand-new" — intentional. Phase 4C integration tests pin the formula behavior.

### Layer 1 invariance

Both per-tenant maturity overrides AND cold-start grace are bypassed when a Layer 1 BLOCK rule fires. Pinned by `test_layer_1_short_circuit_does_not_consult_tenant_config` (unit) and `test_overrides_do_not_affect_layer_1_block` + `test_grace_does_not_affect_layer_1_block` (integration). The fast-path BLOCK semantics are unchanged from Phase 2.

---

## IP enrichment sources

All four, lazy-cached in `ip_enrichment` Postgres table keyed by IP. Cache freshness: 14 days; on staleness, re-enrich. Sources accessed only by `scripts/fetch_enrichment.py` (offline refresh) — no live source calls on the request path.

| Source | URL | Auth | License | Refresh |
|---|---|---|---|---|
| MaxMind GeoLite2 City + ASN | `https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-{City,ASN}&suffix=tar.gz&license_key=…` | Free signup → account_id + license_key | GeoLite2 EULA (free use, attribution, no redistribution) | Tue/Fri weekly |
| FireHOL Level 1 + Level 2 | `https://github.com/firehol/blocklist-ipsets` (raw `firehol_level1.netset`, `firehol_level2.netset`) | None | CC-BY-SA / public-domain | Daily |
| IP2Proxy LITE PX11 | `https://lite.ip2location.com/database-download?database=PX11LITEBIN&token=…` | Free signup → token | CC-BY-SA 4.0 | Monthly |
| Cloud provider CIDRs | AWS: `https://ip-ranges.amazonaws.com/ip-ranges.json` · GCP: `https://www.gstatic.com/ipranges/cloud.json` · Azure: weekly download from `https://www.microsoft.com/en-us/download/details.aspx?id=56519` · Cloudflare: `https://www.cloudflare.com/ips-v4` | None | Public | Continuously (AWS), weekly (GCP/Azure), rare (Cloudflare) |

Secret storage: `MAXMIND_LICENSE_KEY`, `IP2PROXY_DOWNLOAD_TOKEN` (Pydantic Settings; AWS Secrets Manager in prod). No env prefix per operator amendment 2026-05-25.

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

### Modification rule weight rationale (Phase 3A — 2026-05-27)

Phase 3A.7 added 8 modification rules to `app/rules.yaml` (rule count: 67 → 75). No reference codebase contains modification-specific rules (verification §7, §8 of Phase 3A planning confirmed both `freight_risk` and `freightcom-risk` lack the surface). Weights are operator-judgment-based, anchored to Phase 2 weight bands for similar-severity rules. **No tuning of these weights in Phase 3** — calibration deferred to Phase 6 staging replay (per `feedback_no_weight_tuning_phase2` memory entry).

| Rule | Weight | Maturity-sensitive | Rationale band |
|---|---|---|---|
| `modification_within_30_min_value_increase` | 0.65 | no | hard signal — value-jacking immediately after booking; band: `vpn_high_value` (0.55-0.65) |
| `modification_destination_change_pre_pickup` | 0.55 | yes | re-routing pre-pickup is classic re-shipping fraud; maturity-sensitive because dormant-but-legit customers may correct addresses |
| `modification_high_velocity_1h` | 0.70 | no | sustained-rate signal regardless of customer age |
| `modification_high_velocity_24h` | 0.45 | yes | softer band; maturity-sensitive — some operators batch-edit |
| `modification_low_trust_customer` | 0.55 | no | compound: low trust × destination change |
| `modification_dormant_customer` | 0.60 | yes | case-1 ATO pattern applied to modification |
| `modification_recipient_change_to_unfamiliar` | 0.40 | yes | soft signal; recipient changes are normal at low rate |
| `modification_destination_change_residential_asn` | 0.35 | yes | compound destination + ASN signal |

Booking-path safety: `build_context` populates the 6 modification fields with neutral defaults (`modification_type='none'`, magnitudes/velocities zero, time bucket widest, direction unknown) so the DSL evaluator can resolve every Name reference at evaluation time. The `'none'` literal matches no enum value the modification rules condition on, so the rules are structurally dormant on the booking path. `test_modification_rules_dormant_under_booking_path_defaults` pins this invariant.

Calibration commitments: in Phase 6 staging replay, every weight in this table is candidate for adjustment based on observed precision/recall against labelled fraud cases. The weights here are starting points, not final values.

### Previously-rejected rule weight rationale (Phase 3B — 2026-05-27)

Phase 3B.5 added 4 previously-rejected rules to `app/rules.yaml` (rule count: 75 → 79). Weights ported from `freight_risk`'s catalogue per Phase 3B verification §6 — these rules existed in the reference codebase and the operator decision is to mirror their proven values rather than re-derive.

| Rule | Weight | Maturity-sensitive | freight_risk source |
|---|---|---|---|
| `email_previously_rejected_for_customer` | 0.60 | yes | freight_risk catalogue |
| `phone_previously_rejected_for_customer` | 0.60 | yes | freight_risk catalogue |
| `origin_previously_rejected_for_customer` | 0.70 | yes | freight_risk catalogue |
| `ip_previously_rejected_for_customer` | 0.70 | yes | freight_risk catalogue |

Origin and IP carry higher weight than email/phone — physical-address and source-IP re-use after a prior rejection is a stronger fraud signal than contact-info reuse (the latter can be a legitimate operator typo or a new use of the same person's email).

All 4 are maturity-sensitive: a brand-new customer's single rejection should not dominate the score; Layer 2 downweights appropriately. A mature customer with one rejection contributes the full weight.

Booking-path dormancy: build_context populates the 4 fields as `False` for any customer whose baseline has no prior rejections — pure dict lookups via 3B.4 (no SQL). The rules are structurally dormant for clean baselines. `test_previously_rejected_rules_dormant_under_clean_baseline` pins this invariant.

Feedback path: the feedback endpoint (3B.3) writes `add_rejected_observation` to `rejected_email_hmacs`, `rejected_phone_hmacs`, `origin_stats.r_n`, and `ip_stats.r_n` for `rejected`/`fraud_confirmed` labels. The next booking by the same customer with matching dimensions trips the corresponding rule via the 3B.4 derivation. Integration verified in 3B.6 (booking → feedback → next-booking-triggers-rule chain).

Calibration commitments: same as modification rules — Phase 6 staging replay candidate for adjustment based on observed precision/recall.

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
| Load context (baseline FOR UPDATE, enrichment, 5 velocity counts — sequential awaits on the txn connection; see Phase 1 amendment below) | 30-50ms |
| Compute trust score | <1ms |
| Run signal modules in parallel | 20-40ms |
| Score (3-layer noisy-OR + decide) | 5ms |
| Persist (single txn: INSERT shipments + INSERT decisions + baseline save + UPDATE customers) | 15-30ms |
| **Total** | **<100ms typical, <200ms p95** |

Per operator amendment 2026-05-25: persistence is on the hot path; the budget accommodates it. Original Design Context allocated 0ms in-line persistence; this row captures the corrected allocation.

Phase 1 amendment 2026-05-26: build_context loads sequentially on the
transaction connection rather than via `asyncio.gather`. asyncpg does
not multiplex operations on a single connection; gather over the txn
connection raises `InterfaceError`. The baseline FOR UPDATE lock must
hold across the read-modify-write window, so the lock-holding
connection cannot be split. Phase 5 load test revisits if parallel
reads on separate pool connections are needed.

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
