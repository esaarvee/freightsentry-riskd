# rules.md — Scoring Model & DSL Reference

Authoritative content for the scoring model and DSL contract. Rule definitions (conditions, weights, categories) live in `app/rules.yaml`. This file describes the **how**: scoring math, DSL grammar, and the Context fields rule conditions may reference.

For the architectural decisions behind these values (e.g. why `MaturityK=0.30`, why thresholds are 0.60/0.80, why no trust-override), see `.ai/decisions.md`.

---

## Scoring model — 3-layer noisy-OR

Phase 1 ships Layer 1 + Layer 3 only. Layer 2 lands in Phase 2 alongside trust-score consumption.

### Layer 1 — Hard-block short-circuit

Iterate every rule with `action: BLOCK`. First firing rule short-circuits: `decision=BLOCK, score=1.0, classification=RED, risk_level=CRITICAL`. No subsequent layer runs.

Iteration order is YAML file order. To make precedence explicit, list BLOCK rules near the top of `app/rules.yaml`.

### Layer 2 — Account prior (Phase 2)

```
maturity = clamp(age_days / maturity_age_days, 0, 1)
         * clamp(shipments / maturity_shipments, 0, 1)

base_prior         = MaxNewAccount * (1 - maturity)
trust_risk         = max(0, (0.5 - trust_score) / 0.5)
trust_contribution = trust_risk * TrustFactor
flag_prior         = flag_weights[flagged_count_tier]

account_prior = noisyOR(base_prior, trust_contribution, flag_prior)
```

`flagged_count_tier`: 0→0, 1-2→1, 3-5→2, 6+→3.

Constants (initial; per-tenant overridable via `tenants.config` from Phase 4):

| Constant | Value |
|---|---|
| `MaxNewAccount` | 0.10 |
| `TrustFactor` | 0.25 |
| `flag_weights` | `[0.00, 0.15, 0.25, 0.35]` |
| `maturity_age_days` | 180 |
| `maturity_shipments` | 50 |
| `MaturityK` | 0.30 |

### Layer 3 — Signal noisy-OR with maturity downweighting

For each fired non-BLOCK rule:

```
if rule.maturity_sensitive:
    effective_weight = rule.weight * (1 - MaturityK * (1 - maturity))
else:
    effective_weight = rule.weight

signal_score = 1 - prod(1 - effective_weight_i  for fired rules)
```

At full maturity (`maturity=1`), `(1 - 0.30 * 0) = 1.00` → full weight. At zero maturity, `(1 - 0.30 * 1) = 0.70` → 70% weight.

### Final score

Phase 1: `final_score = signal_score` (no Layer 2 yet).
Phase 2+: `final_score = noisyOR(account_prior, signal_score)`.

### Thresholds and bands

| Score | Decision | Classification | Risk level |
|---|---|---|---|
| `score <= allow_max` (0.60) | ALLOW | GREEN | LOW (<0.30) or MEDIUM |
| `allow_max < score < block_min` (0.60-0.80) | REVIEW | YELLOW | MEDIUM (<0.60) or HIGH |
| `score >= block_min` (0.80) | BLOCK | RED | CRITICAL |

Risk-level boundaries: `<0.30` LOW, `<0.60` MEDIUM, `<0.80` HIGH, `>=0.80` CRITICAL.

Thresholds and constants are loaded from `app/rules.yaml` at startup. No Pydantic dataclass defaults — single source of truth (per verification §2.3).

---

## YAML rule shape

```yaml
thresholds:
  allow_max: 0.60
  block_min: 0.80

account_prior:                # Phase 2
  max_new_account: 0.10
  trust_factor: 0.25
  flag_weights: [0.00, 0.15, 0.25, 0.35]
  maturity_age_days: 180
  maturity_shipments: 50

maturity_k: 0.30              # Layer 3 downweight for cold-start-sensitive rules

rules:
  - name: blacklisted_ip
    description: "IP found in FireHOL Level 1 threat feed"
    condition: "ip_in_level1"
    weight: 1.0
    action: BLOCK

  - name: vpn_high_value
    description: "VPN + high-value shipment"
    condition: "is_vpn AND shipment_value > 1000"
    weight: 0.3
    maturity_sensitive: false

  - name: ip_velocity_high_api
    description: "API booking burst from one IP"
    condition: "is_api_booking AND velocity_ip_hourly > 100"
    weight: 0.3
    maturity_sensitive: true
```

Fields per rule:
- `name` (str, unique) — also used in `triggered_rules` audit trail
- `description` (str) — human-readable; surfaced in admin endpoint
- `condition` (str) — DSL expression parsed at startup (see DSL grammar below)
- `weight` (float, [0.0, 1.0]) — Layer 3 contribution if `maturity_sensitive` false; downweighted by maturity otherwise
- `action` (`BLOCK` | absent) — when `BLOCK`, rule short-circuits Layer 1; else contributes to Layer 3
- `maturity_sensitive` (bool, default false) — when true, weight is downweighted by `MaturityK * (1 - maturity)` in Layer 3

---

## DSL grammar

Pure Python `ast`-based parser (`app/dsl.py`, ~150 LOC). Each rule's `condition` string is parsed at startup and compiled to a callable evaluated at runtime against a Context env dict.

### Whitelisted AST nodes

Any other AST node → `DSLError` at parse time (fail-fast).

- `BoolOp` (with `And` / `Or`)
- `UnaryOp` (with `Not`)
- `Compare` (with `Gt` / `Lt` / `GtE` / `LtE` / `Eq` / `NotEq`)
- `Name` — env-lookup only; **no attribute access** (`x.y`), **no subscript** (`x[0]`)
- `Constant` — `int`, `float`, `str`, `bool`, `None` only (no `bytes`, no `complex`)
- `Load` context

Evaluation: `eval(code, {"__builtins__": {}}, env)` with `env` as a `MappingProxyType` over the Context dict (read-only).

### Operator precedence

Standard Python: `or` > `and` > `not` > comparison.

### Validation at startup

`rules.load_rules()` invokes the DSL loader, which:
1. Parses each rule's `condition` to an AST
2. Walks the AST collecting every `Name` token
3. Asserts every collected name resolves to a known Context field (see field list below)
4. Raises `DSLError` on first unknown name (with rule name + condition for traceability)

This catches typo'd field names at startup, not at request time.

### Security boundary

Any change to `app/dsl.py` is never-skip review per CLAUDE.md. Security-auditor verifies whitelist completeness; security tests assert that `__class__`, `__bases__`, attribute access, subscript, function calls are all rejected.

---

## DSL Context fields

Rule conditions reference these names. Categories:

### Request payload

- `shipment_value` (float)
- `is_api_booking` (bool) — `channel == "api"`
- `is_platform_booking` (bool) — not `is_api_booking`
- `booking_hour_utc` (int) — 0-23 from `booking_ts`
- `booking_weekday` (int) — 0-6 from `booking_ts`

### Customer + maturity

- `customer_observations` (float) — decay-weighted activity proxy from `customer_baselines.value_n` post-decay (per operator amendment; replaces a persisted 30-day count)
- `account_age_days` (int) — `(today - customer.first_seen).days`
- `total_shipments` (int) — monotonic lifetime count from `customers.total_shipments`
- `flagged_count` (int) — `customers.flagged_count`
- `fraud_confirmed_count` (int) — `customers.fraud_confirmed_count`
- `trust_score` (float, 0.0-1.0) — computed per request by `app/trust.py::compute_trust_score`. Phase 1 attaches but no rules read; Phase 2 trust-conditional rules consume.

### IP enrichment

- `is_cloud_ip` (bool) — IP in AWS/GCP/Azure/Cloudflare CIDRs OR asn_org matches cloud-provider pattern
- `is_datacenter_ip` (bool) — `signal_helpers.is_datacenter_asn(asn_org)` true (eStruxture, Equinix, OVH, Hetzner, …)
- `is_vpn` (bool) — from IP2Proxy `proxy_type == "VPN"`
- `is_tor` (bool) — from IP2Proxy `proxy_type == "TOR"`
- `is_proxy` (bool) — IP2Proxy `is_proxy` AND non-sentinel `proxy_type`
- `ip_proxy_threat_botnet` (bool) — IP2Proxy threat tag
- `ip_proxy_threat_scanner` (bool) — IP2Proxy threat tag
- `ip_proxy_threat_spam` (bool) — IP2Proxy threat tag
- `ip_in_level1` (bool) — FireHOL Level 1 match
- `ip_in_level2` (bool) — FireHOL Level 2 match
- `ip_in_threat_list` (bool) — Level 1 OR Level 2
- `ip_threat_score` (float, 0.0-1.0) — composite of FireHOL + IP2Proxy threat tags
- `ip_country` (str) — MaxMind country ISO code
- `ip_distance_km` (float) — haversine from `last_booking_lat/lon` if present, else 0
- `ip_country_changed` (bool) — `ip_country != last_booking_country`

### Familiarity (baseline-derived)

- `is_new_ip` (bool) — IP not in `customer_baselines.ip_stats`
- `ip_fully_new` (bool) — IP, its /24, and its ASN all unseen for this customer
- `ip_family_familiar` (bool) — /24 match in `ip_netblock_stats` (per verification §2.2: ASN-only no longer confers family-familiar)
- `is_new_route` (bool) — `(origin, destination)` lane not in `lane_stats`
- `origin_address_familiar` (bool) — origin in `origin_stats`
- `destination_address_familiar` (bool) — destination in `dest_stats`
- `origin_ip_country_familiar` (bool) — `(origin, ip_country)` pair in `origin_ip_country_stats`

### Identity (HMAC-keyed)

- `origin_email_hmac_known` (bool) — HMAC of origin email in `email_hmacs`
- `origin_email_domain_known` (bool) — domain HMAC in `email_domain_stats`
- `origin_phone_hmac_known` (bool) — phone HMAC in `phone_hmacs`
- `origin_phone_prefix_known` (bool) — first 3 digits HMAC in `phone_prefix_stats`
- `is_email_disposable` (bool) — `signal_helpers.is_email_disposable(origin_email)`
- `is_email_blocklisted` (bool) — `signal_helpers.is_email_blocklisted(origin_email)`
- `is_email_suspicious_pattern` (bool) — `signal_helpers.is_email_suspicious_pattern(origin_email)`
- `is_phone_dummy_pattern` (bool) — `signal_helpers.is_phone_dummy_pattern(origin_phone)`

### Velocity (SQL-backed)

Computed in `app/velocity.py` from `shipments` table; bounded by `(tenant_id, customer_id, booking_ts > now() - interval)`. No Redis.

- `velocity_user_hourly` (int) — count for this customer in the last hour
- `velocity_user_daily` (int) — count in the last day
- `velocity_user_30d` (int) — count in the last 30 days
- `velocity_ip_hourly` (int) — count for this source_ip in the last hour
- `velocity_ip_daily` (int) — count for this source_ip in the last day

### Value and cadence

- `value_zscore` (float) — z-score of `shipment_value` against `value_mean`, `value_m2` Welford triple; 0 if std-dev is 0 (no variance)
- `cadence_zscore_hours` (float) — z-score of hours-since-`last_booking_ts` against cadence Welford
- `is_abnormally_dormant` (bool) — `cadence_zscore_hours > 6` (per verification §2.2: tuned from z>4)

### Customer lock-in (Phase 2)

- `customer_locked_cloud_api` (bool) — derived in `build_context`: `cloud_share_n / total > 0.95 AND api_share_n / total > 0.95 AND effective_observations >= 20`. Drives case-2 detection rules.

### Recipient overlap (Phase 2)

- `recipient_used_by_customer_count` (int) — cross-customer SQL count: distinct customers that have shipped to this destination HMAC in the last 30 days
- `recipient_globally_rejected` (bool) — destination HMAC in `global_blocked_vectors` with `vector_type='RECIPIENT'` AND `share_enabled` (Phase 4+ when sharing is enabled; always False in v1)

### Modification-specific (Phase 3)

- `modification_within_hour_of_booking` (bool)
- `modification_same_day_as_booking` (bool)
- `recipient_changed_to_freight_forwarder` (bool)
- `velocity_modifications_per_customer_daily` (int)

---

## Rule catalogue organisation

- Categories captured as YAML comments grouping rules (`# --- Threat intel ---`, `# --- Dormancy ---`, etc.). Categories are documentation; the loader treats them as flat.
- Hard-BLOCK rules first in the file (explicit precedence).
- Maturity-sensitive flag explicit per rule (default `false`).
- `recipient_overlap` rules carried from freight_risk's 84-rule base (per verification §1.2 — NOT a FreightSentry-only port).
- Freight_risk tuned thresholds applied verbatim: `cadence_anomaly` z>6, `velocity_spike_daily_api` 50, `residential_asn_high_velocity` 15.

---

## Loading rules

`app/rules.py::load_rules(yaml_path, dsl_module) -> RuleSet`

At app lifespan:
1. Load YAML
2. Validate top-level `thresholds`, `account_prior`, `maturity_k` are present (or have hardcoded fallback values matching `.ai/decisions.md`)
3. For each rule, parse `condition` via DSL, compile to callable, store with `weight`, `action`, `maturity_sensitive`, `name`, `description`
4. Walk all conditions collecting `Name` references; assert each resolves to a known Context field (fail-fast)
5. Return immutable `RuleSet` consumed by the scorer

No hot-reload via fsnotify. Restart is the way to deploy a rule-set change. (5-second restart on small instances; well below operational sensitivity.)
