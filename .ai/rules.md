# rules.md — Scoring model + rule catalogue + DSL reference

Authoritative content for the scoring model, the rule catalogue (81 rules), and the DSL contract.
Rule definitions (conditions, weights, categories) live in `app/rules.yaml` — that file is the source
of truth for weights and condition strings. The Context-field whitelist lives in
`app/rules.py::ALLOWED_CONTEXT_FIELDS`.

For the architectural rationale behind these values (why `MaturityK=0.30`, why thresholds are 0.60/0.80,
why no trust-override, why the noisy-OR composition), see `.ai/decisions.md`. For the historical
derivation of each rule (phase-by-phase evolution, deleted rules, calibration history), see
`docs/history.md`.

---

## Scoring model — 3-layer noisy-OR

The scorer composes three layers via noisy-OR: Layer 1 short-circuits on hard-block matches; Layer 2
contributes an account prior from maturity, trust score, and historical flag count; Layer 3
contributes signal score from non-BLOCK fired rules with maturity downweighting on cold-start-sensitive
rules.

### Layer 1 — Hard-block short-circuit

Iterate every rule with `action: BLOCK`. First firing rule short-circuits the scorer:
`decision=BLOCK, score=1.0, classification=RED, risk_level=CRITICAL`. No subsequent layer runs.

Iteration order is YAML file order. BLOCK rules are listed at the top of `app/rules.yaml` to make
precedence explicit.

### Layer 2 — Account prior

```
maturity           = clamp(age_days / maturity_age_days, 0, 1)
                   * clamp(shipments / maturity_shipments, 0, 1)

base_prior         = MaxNewAccount * (1 - maturity)
trust_risk         = max(0, (0.5 - trust_score) / 0.5)
trust_contribution = trust_risk * TrustFactor
flag_prior         = flag_weights[flagged_count_tier]

account_prior      = noisyOR(base_prior, trust_contribution, flag_prior)
```

`flagged_count_tier`: 0 → 0, 1-2 → 1, 3-5 → 2, 6+ → 3.

Constants (defined in `app/scoring_constants.py`; per-tenant overridable via
`tenants.config` since Phase 4C):

| Constant | Value |
|---|---|
| `MAX_NEW_ACCOUNT` | 0.10 |
| `TRUST_FACTOR` | 0.25 |
| `FLAG_WEIGHTS` | `(0.00, 0.15, 0.25, 0.35)` |
| `MATURITY_AGE_DAYS` | 180 |
| `MATURITY_SHIPMENTS` | 50 |
| `MATURITY_K` | 0.30 |

### Cold-start grace multiplier

For freshly-onboarded tenants whose customer population has not yet accumulated baseline mass, Layer 2
applies an additional `0.5` multiplier to the maturity term while the tenant is within its
`cold_start_grace_days` window (per-tenant override; default 0 = disabled). Lower effective maturity
means stronger maturity downweighting on Layer 3 maturity-sensitive rules, so cold-start-tenant noise
is suppressed during the grace window. Implemented in `app/scoring.py::_apply_cold_start_grace`.

### Layer 3 — Signal noisy-OR with maturity downweighting

For each fired non-BLOCK rule:

```
if rule.maturity_sensitive:
    effective_weight = rule.weight * (1 - MaturityK * (1 - maturity))
else:
    effective_weight = rule.weight

signal_score = 1 - prod(1 - effective_weight_i  for fired rules)
```

At full maturity (`maturity=1`), the cold-start factor `(1 - 0.30 * 0) = 1.00` → full weight. At zero
maturity, `(1 - 0.30 * 1) = 0.70` → 70% weight.

### Final score

```
final_score = noisyOR(account_prior, signal_score)
```

### Thresholds and bands

| Score | Decision | Classification | Risk level |
|---|---|---|---|
| `score <= allow_max` (0.60) | ALLOW | GREEN | LOW (<0.30) or MEDIUM |
| `allow_max < score < block_min` (0.60-0.80) | REVIEW | YELLOW | MEDIUM (<0.60) or HIGH |
| `score >= block_min` (0.80) | BLOCK | RED | CRITICAL |

Risk-level boundaries: `<0.30` LOW, `<0.60` MEDIUM, `<0.80` HIGH, `>=0.80` CRITICAL.

Thresholds are loaded from `app/rules.yaml` at startup. Scoring constants live in
`app/scoring_constants.py` and are overridable per-tenant via `tenants.config`.

---

## YAML rule shape

```yaml
thresholds:
  allow_max: 0.60
  block_min: 0.80

rules:
  - name: blacklisted_ip
    description: "IP found in FireHOL Level 1 threat feed"
    condition: "ip_in_level1"
    weight: 1.0
    action: BLOCK

  - name: vpn_high_value
    description: "VPN + high-value shipment"
    condition: "is_vpn AND shipment_value > shipment_value_threshold_low"
    weight: 0.3

  - name: ip_velocity_high_api
    description: "API booking burst from one IP"
    condition: "is_api_booking AND velocity_ip_hourly > 100"
    weight: 0.3
    maturity_sensitive: true
```

Per-rule fields:

- `name` (str, unique) — also used in the `triggered_rules` audit trail
- `description` (str) — human-readable; surfaced in admin endpoint and audit
- `condition` (str) — DSL expression parsed at startup (see DSL grammar below)
- `weight` (float, [0.0, 1.0]) — Layer 3 contribution if `maturity_sensitive` false; downweighted by
  maturity otherwise
- `action` (`BLOCK` | absent) — when `BLOCK`, rule short-circuits Layer 1; else contributes to Layer 3
- `maturity_sensitive` (bool, default false) — when true, weight is downweighted by
  `MaturityK * (1 - maturity)` in Layer 3

Account-prior constants (`MAX_NEW_ACCOUNT`, `TRUST_FACTOR`, `FLAG_WEIGHTS`, `MATURITY_*`) live in
`app/scoring_constants.py`, not in YAML — per-tenant overrides go through `tenants.config`.

---

## DSL grammar

Pure Python `ast`-based parser (`app/dsl.py`, ~150 LOC). Each rule's `condition` string is parsed at
startup and compiled to a callable evaluated at runtime against a Context env dict.

### Whitelisted AST nodes

Any other AST node → `DSLError` at parse time (fail-fast).

- `BoolOp` (with `And` / `Or`)
- `UnaryOp` (with `Not`)
- `Compare` (with `Gt` / `Lt` / `GtE` / `LtE` / `Eq` / `NotEq`)
- `Name` — env-lookup only; **no attribute access** (`x.y`), **no subscript** (`x[0]`)
- `Constant` — `int`, `float`, `str`, `bool`, `None` only (no `bytes`, no `complex`)
- `Load` context

Evaluation: `eval(code, {"__builtins__": {}}, env)` with `env` a `MappingProxyType` over the Context
dict (read-only).

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

Any change to `app/dsl.py` is never-skip review per `CLAUDE.md`. Security-auditor verifies whitelist
completeness; security tests assert that `__class__`, `__bases__`, attribute access, subscript, and
function calls are all rejected.

---

## DSL Context fields

Rule conditions reference these names. The authoritative whitelist is the `ALLOWED_CONTEXT_FIELDS`
frozenset in `app/rules.py` (75 names). Categories below group by semantic role.

### Request payload (5)

| Field | Type | Semantic |
|---|---|---|
| `shipment_value` | float | Shipment declared value in the payload currency |
| `is_api_booking` | bool | `channel == "api"` |
| `is_platform_booking` | bool | not `is_api_booking` (web/UI channel) |
| `booking_hour_utc` | int (0-23) | UTC hour derived from `booking_ts` |
| `booking_weekday` | int (0-6) | Weekday index derived from `booking_ts` |

### Customer + maturity (6)

| Field | Type | Semantic |
|---|---|---|
| `customer_observations` | float | Decay-weighted activity proxy from `customer_baselines.value_n` post-decay |
| `account_age_days` | int | `(today - customer.first_seen).days` |
| `total_shipments` | int | Monotonic lifetime count from `customers.total_shipments` |
| `flagged_count` | int | `customers.flagged_count` |
| `fraud_confirmed_count` | int | `customers.fraud_confirmed_count` |
| `trust_score` | float (0.0-1.0) | Computed per request by `app/trust.py::compute_trust_score`; consumed by Layer 2 + trust-conditional rules |

### IP enrichment (16)

| Field | Type | Semantic |
|---|---|---|
| `is_cloud_ip` | bool | IP in AWS/GCP/Azure/Cloudflare CIDRs OR asn_org matches cloud-provider pattern |
| `is_datacenter_ip` | bool | `signal_helpers.is_datacenter_asn(asn_org)` true (eStruxture, Equinix, OVH, Hetzner, etc.) |
| `is_vpn` | bool | IP2Proxy `proxy_type == "VPN"` |
| `is_tor` | bool | IP2Proxy `proxy_type == "TOR"` |
| `is_proxy` | bool | IP2Proxy `is_proxy` AND non-sentinel `proxy_type` |
| `ip_in_level1` | bool | FireHOL Level 1 (high-confidence) match |
| `ip_in_level2` | bool | FireHOL Level 2 (medium-confidence) match |
| `ip_in_threat_list` | bool | `ip_in_level1 OR ip_in_level2` |
| `ip_threat_score` | float (0.0-1.0) | Composite of FireHOL + IP2Proxy threat tags |
| `ip_country` | str | MaxMind country ISO code |
| `ip_distance_km` | float | Haversine from `last_booking_lat/lon` if present, else 0 |
| `ip_country_changed` | bool | `ip_country != last_booking_country` |
| `ip2p_threat_botnet` | bool | IP2Proxy PX11 threat tag — known botnet member |
| `ip2p_threat_scanner` | bool | IP2Proxy PX11 threat tag — port scanner |
| `ip2p_threat_spam` | bool | IP2Proxy PX11 threat tag — spam infrastructure |
| `ip2p_threat_any` | bool | Any IP2Proxy threat tag present |
| `is_residential_asn` | bool | `signal_helpers.is_residential_asn(asn_org)` true (Comcast, Bell, Rogers, etc.) |

### Familiarity (baseline-derived, 9)

Per-customer baselines maintained in `customer_baselines` JSONB; familiarity = matching key in the
relevant sub-baseline.

| Field | Type | Semantic |
|---|---|---|
| `ip_familiarity_tier` | str | Tier label derived from per-customer IP baseline (familiar / known-asn / new) |
| `is_new_ip` | bool | IP not in `customer_baselines.ip_stats` |
| `ip_new_known_asn` | bool | IP and its /24 are new but ASN is known to this customer |
| `ip_fully_new` | bool | IP, /24, and ASN all unseen for this customer |
| `ip_family_familiar` | bool | /24 match in `ip_netblock_stats` |
| `is_new_route` | bool | `(origin, destination)` lane not in `lane_stats` |
| `origin_address_familiar` | bool | Origin in `origin_stats` |
| `destination_address_familiar` | bool | Destination in `dest_stats` |
| `origin_ip_country_familiar` | bool | `(origin, ip_country)` pair in `origin_ip_country_stats` |

### Identity classifiers (4)

| Field | Type | Semantic |
|---|---|---|
| `is_email_disposable` | bool | `signal_helpers.is_email_disposable(origin_email)` |
| `is_email_blocklisted` | bool | `signal_helpers.is_email_blocklisted(origin_email)` |
| `is_email_suspicious_pattern` | bool | `signal_helpers.is_email_suspicious_pattern(origin_email)` |
| `is_phone_dummy_pattern` | bool | `signal_helpers.is_phone_dummy_pattern(origin_phone)` |

### Velocity (SQL-backed, 7)

Computed in `app/velocity.py` from the `shipments` table; bounded by
`(tenant_id, customer_id, booking_ts > now() - interval)`. No Redis.

| Field | Type | Semantic |
|---|---|---|
| `velocity_user_hourly` | int | This customer's count in the last hour |
| `velocity_user_daily` | int | This customer's count in the last day |
| `velocity_user_30d` | int | This customer's count in the last 30 days |
| `velocity_ip_hourly` | int | This `source_ip` count in the last hour |
| `velocity_ip_daily` | int | This `source_ip` count in the last day |
| `customer_distinct_ips_30d` | int | Distinct IPs this customer has booked from in the last 30 days |
| `recipient_cross_customer_count` | int | Distinct customers (this tenant) that shipped to this destination HMAC in 30 days |

### Value and cadence (3)

| Field | Type | Semantic |
|---|---|---|
| `value_zscore` | float | z-score of `shipment_value` against `value_mean`, `value_m2` Welford triple; 0 if std-dev is 0 |
| `cadence_zscore_hours` | float | z-score of hours-since-`last_booking_ts` against cadence Welford |
| `is_abnormally_dormant` | bool | `cadence_zscore_hours > 6` |

### Customer lock-in + Layer 2 inputs (5)

| Field | Type | Semantic |
|---|---|---|
| `customer_locked_cloud_api` | bool | `cloud_share_n / total > 0.95 AND api_share_n / total > 0.95 AND effective_observations >= 20`. Case-2 detection input |
| `customer_locked_web_only` | bool | `web_share_n / total > 0.95 AND effective_observations >= 20` |
| `days_since_last_booking` | int | Whole-day count since `last_booking_ts`; 0 if no prior |
| `is_new_user` | bool | `customer_observations < 10` shorthand |
| `impossible_travel` | bool | Same-day booking from > 500km from last known location |

### Modification (Phase 3A, 6)

Populated by `build_modification_context` on the modification endpoint. On booking-path requests,
`modification_type` is the `"none"` sentinel (matches no enum literal), keeping modification rules
structurally dormant. See `app/context.py::BOOKING_PATH_MODIFICATION_DEFAULTS`.

| Field | Type | Semantic |
|---|---|---|
| `modification_time_since_booking` | Literal | `within_30_min` / `within_1_hour` / `within_24_hours` / `1_to_7_days` / `over_7_days` |
| `modification_magnitude` | float | `[0.0, +inf)` — fraction for value-type, 0/1 for categorical |
| `modification_direction` | Literal | `familiar` / `unfamiliar` / `blocked` / `unknown` |
| `modification_velocity_1h` | int | This customer's modification count in last 1h |
| `modification_velocity_24h` | int | This customer's modification count in last 24h |
| `modification_type` | Literal | `destination` / `value` / `recipient` / `service_level` / `pickup_time` (or `none` sentinel on booking path) |

### Previously rejected (Phase 3B, 4)

Populated by `build_context` from baseline state.

| Field | Type | Semantic |
|---|---|---|
| `email_previously_rejected` | bool | `email_hmac` present in `baseline.rejected_email_hmacs` |
| `phone_previously_rejected` | bool | `phone_hmac` present in `baseline.rejected_phone_hmacs` |
| `origin_previously_rejected` | bool | `baseline.origin_stats[origin_key].r_n > 0` |
| `ip_previously_rejected` | bool | `baseline.ip_stats[ip].r_n > 0` |

### Currency-normalized thresholds (Phase 4B, 5)

Populated by `build_context` from `tenant_config.value_caps` via `resolve_value_caps`. The 7
currency-implicit rules consult these fields instead of hardcoded value literals.

| Field | Type | Semantic |
|---|---|---|
| `shipment_currency` | str | 3-letter ISO 4217 code from `payload.shipment.currency` |
| `shipment_value_threshold_high` | float | `caps[currency]["high"]` |
| `shipment_value_threshold_medium` | float | `caps[currency]["medium"]` |
| `shipment_value_threshold_low` | float | `caps[currency]["low"]` |
| `shipment_value_threshold_new_user` | float | `caps[currency]["new_user"]` |

### Case-3 signals (Phase 6A, 3)

| Field | Type | Semantic |
|---|---|---|
| `origin_via_carrier_dropoff` | bool | Structured-field passthrough from `payload.shipment` — origin handed off through carrier facility |
| `shipment_route_unfamiliar_for_customer` | bool | Derived in `app/context.py::_derive_route_unfamiliar` from `baseline.country_route_stats`; True iff the country pair is outside the customer's top-N covering >= 80% of historical routes |
| `customer_registered_country` | str | Structured-field passthrough from `payload.customer` (ISO 3166-1 alpha-2) |

### Case-3b asymmetric mismatch (Phase 7C.2, 1)

| Field | Type | Semantic |
|---|---|---|
| `customer_destination_country_mismatch_outbound` | bool | True iff `customer.registered_country` and `shipment.destination.country` are both truthy AND differ. Derived in `app/context.py::_outbound_destination_mismatch` |

### Case-3b tenant-population baseline (Phase 6A.8, 1)

| Field | Type | Semantic |
|---|---|---|
| `shipment_route_rare_for_tenant` | bool | Derived from `app/tenant_route_baselines.py::derive_route_rarity`; True iff the `(customer_country, origin_country, destination_country)` triple is <2% of tenant's population AND tenant has >=100 total observations across triples |

### Case-2 learning-based ASN deviation (Phase 7C.6, 1)

| Field | Type | Semantic |
|---|---|---|
| `unfamiliar_asn_for_customer` | bool | True iff enrichment-derived `asn_org` is non-None AND `customer_observations >= 10` AND the `asn_org` is novel to the customer's accumulated `ip_asn_stats` baseline. Powers case-2 API-key-compromise detection |

---

## Rule catalogue

81 rules in `app/rules.yaml`. Weights are authoritative in YAML — values cited below are the
current weights. Categories below mirror the YAML comment-block structure.

### Layer 1 — Hard-block (2)

Short-circuit the scorer; weight 1.0; `action: BLOCK`.

| Rule | Condition (summary) | Intent |
|---|---|---|
| `blacklisted_ip` | `ip_in_level1` | FireHOL Level 1 IP — confirmed attacker, immediate block |
| `ip2p_threat_botnet_block` | `ip2p_threat_botnet` | IP2Proxy PX11 flags this IP as a known botnet member |

### Phase 1 baseline — signal rules (12)

The original Phase 1 contribution rules: threat intel, dummy identifiers, basic IP velocity, and
familiarity probes against established customers (`customer_observations >= 10`).

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `threat_intel_level2` | 0.40 | no | FireHOL Level 2 IP — moderate confidence |
| `tor_exit` | 0.50 | no | Booking from a Tor exit node |
| `vpn_high_value` | 0.30 | no | VPN + shipment above tenant low tier |
| `customer_daily_volume_spike` | 0.40 | yes | `velocity_user_daily > 20` |
| `ip_velocity_high_ui` | 0.30 | yes | Web booking burst (>10/hour from one IP) |
| `ip_velocity_high_api` | 0.30 | yes | API booking burst (>100/hour from one IP) |
| `dummy_email_disposable_domain` | 0.30 | no | Disposable/throwaway email domain |
| `dummy_phone_pattern` | 0.20 | no | Dummy phone pattern (1111111111, etc.) |
| `unknown_origin_address` | 0.25 | yes | New origin for an established customer |
| `unknown_destination_address` | 0.10 | yes | New destination for an established customer (Phase 7C.8 weight reduction; see below) |
| `ip_fully_new_for_customer` | 0.35 | yes | IP, /24, and ASN all unfamiliar to established customer |
| `unfamiliar_ip_country_for_origin` | 0.15 | yes | Origin paired with unseen IP country (Phase 7C.8 weight reduction) |

### Phase 2C.1 — Trust-conditioned (7)

Layer 2 made `trust_score` meaningful at scoring time in Phase 2A.3; these rules compound low-trust
with corroborating evidence.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `very_low_trust` | 0.15 | yes | `trust_score < 0.2` — broad reputational baseline |
| `low_trust_high_value` | 0.20 | yes | `trust_score < 0.3` + shipment > low tier |
| `low_trust_vpn` | 0.20 | yes | `trust_score < 0.3` + VPN |
| `very_low_trust_velocity` | 0.35 | yes | `trust_score < 0.2` + `velocity_user_hourly > 3` |
| `threat_score_moderate` | 0.25 | no | `ip_threat_score > 0.5` composite |
| `flags_with_value` | 0.40 | no | `flagged_count > 3` + shipment > medium tier |
| `vpn_known_user` | 0.25 | no | VPN + established customer (not new) |

### Phase 2C.2 — Dormancy + customer lock-in (5)

Dormancy = silent customer suddenly active (case-1 ATO primary detector). Lock-in = customer's
strong cloud+API baseline being violated by the current booking's infrastructure shape (case-2
primary detector).

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `dormant_vpn` | 0.55 | no | Dormant customer suddenly active over VPN |
| `dormant_new_ip` | 0.35 | yes | Dormant customer suddenly active from fully-new IP |
| `ip_distance_dormant` | 0.40 | yes | Dormant customer reactivating from IP >1000km |
| `cloud_api_customer_deviation_iptype` | 0.55 | no | Cloud+API customer booking from non-cloud/datacenter IP |
| `locked_customer_unfamiliar_ip` | 0.45 | no | Cloud+API customer arriving from fully-new IP |

### Phase 2C.3 — Residential ASN + IP-class (5)

Residential-ASN abuse (proxy farms, distributed bookings) and IP-class mismatches between customer's
typical infrastructure and current booking. Includes `api_booking_from_unfamiliar_asn` (Phase 7C.7
case-2 learning-based replacement for deleted `api_non_cloud_ip` / `non_cloud_established_account` —
see history.md).

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `residential_asn_high_velocity` | 0.40 | yes | Residential ASN + `velocity_ip_hourly > 15` |
| `api_booking_from_unfamiliar_asn` | 0.65 | no | Case-2 learning-based — API + ASN novel to this customer's baseline |
| `new_user_api_non_cloud` | 0.40 | no | New user + API + non-cloud/datacenter IP |
| `web_booking_from_cloud_ip` | 0.45 | no | Web booking from cloud/datacenter IP |
| `web_only_customer_using_api` | 0.50 | no | Web-only customer suddenly using API channel |

### Phase 2C.4 — Recipient overlap (2)

Cross-customer destination-HMAC overlap detection. Tier-disjoint: the lower-weight `_many_customers`
rule fires in 4-10 range, the higher-weight `_very_many_customers` rule fires for >10. The upper
bound on the lower rule prevents both firing simultaneously.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `recipient_used_by_many_customers` | 0.40 | yes | Destination shipped to by 4-10 distinct customers (30d) |
| `recipient_used_by_very_many_customers` | 0.60 | no | Destination shipped to by >10 distinct customers — likely fraud-ring drop point |

### Phase 2C.5 — Velocity expansion + identity novelty (11)

Velocity-spike rules complementing Phase 1's basic `customer_daily_volume_spike`, plus identity
novelty rules.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `velocity_spike_hourly_ui` | 0.45 | yes | Web `velocity_user_hourly > 60` |
| `velocity_spike_hourly_api` | 0.25 | yes | API `velocity_user_hourly > 500` |
| `velocity_spike_daily_ui` | 0.35 | no | Web `velocity_user_daily > 300` + new-ish account |
| `velocity_spike_daily_api` | 0.25 | no | API `velocity_user_daily > 50` + new-ish account |
| `ip_velocity_threat` | 0.35 | no | `velocity_ip_daily > 5` + threat-list IP |
| `user_velocity_vpn` | 0.30 | no | `velocity_user_daily > 3` + VPN |
| `user_velocity_new_user` | 0.35 | no | New user posting >3 bookings/day |
| `dummy_email_blocklisted` | 0.55 | no | Email matches known blocklist |
| `dummy_email_suspicious_pattern` | 0.30 | no | Email matches suspicious-pattern heuristic |
| `vpn_new_user` | 0.40 | no | New user arriving via VPN |
| `high_value_new_user` | 0.35 | no | New user + shipment > new-user tier |

### Phase 2C.6 — Value-anomaly + geographic + threat-intel composites (17)

Value-anomaly (6), geographic (5), threat-intel composites (6). Phase 7C.12 calibrated the
MaxMind-enabled geographic rules downward after measurement on the Jan-Mar 2026 operator-approved
corpus showed they drove the bulk of approved-corpus BLOCK band.

#### Value-anomaly (6)

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `extreme_value` | 0.40 | yes | `value_zscore > 3.0` for established customer |
| `above_normal_value` | 0.15 | yes | `value_zscore` in 2-3σ range |
| `above_normal_value_vpn` | 0.25 | no | 2σ above baseline + VPN |
| `absolute_high_value` | 0.20 | no | Shipment > tenant high tier (currency-normalized) |
| `threat_intel_high_value` | 0.30 | no | FireHOL IP + shipment > medium tier |
| `ip2p_threat_high_value` | 0.40 | no | IP2Proxy threat tag + shipment > medium tier |

#### Geographic (5)

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `ip_intercontinental_jump` | 0.20 | no | `ip_distance_km > 5000` from last booking (calibrated from 0.65 in 7C.12) |
| `ip_long_distance_new_ip` | 0.15 | no | `ip_distance_km > 2000` + new IP (calibrated from 0.35 in 7C.12) |
| `ip_country_change` | 0.15 | no | `ip_country_changed` + new IP (calibrated from 0.25 in 7C.12) |
| `api_country_change_unfamiliar` | 0.55 | no | API + new IP + country changed — high-stakes ATO |
| `impossible_travel_geo` | 0.30 | no | `impossible_travel` flag (calibrated from 0.65 in 7C.12 — biggest single approved-corpus BLOCK contributor) |

#### Threat-intel composites (6)

Note: `ip2p_threat_scanner_signal` and `ip2p_threat_spam_signal` carry the `_signal` suffix to avoid
collision with the homonymous Context boolean fields they reference.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `threat_level2_vpn` | 0.20 | no | FireHOL L2 + VPN |
| `ip2p_threat_scanner_signal` | 0.50 | no | Port-scanner threat tag |
| `ip2p_threat_spam_signal` | 0.35 | no | Spam-infrastructure threat tag |
| `ip2p_threat_new_user` | 0.40 | no | Any IP2Proxy threat tag + new user |
| `ip2p_threat_api` | 0.45 | no | Any IP2Proxy threat tag + API booking |
| `open_proxy` | 0.30 | no | Open proxy (not VPN, not Tor — distinct class) |

### Phase 2C.7 — IP-familiarity tier + compound closers (5)

The 3 IP-familiarity-tier rules ported from freight_risk, plus 2 compound rules.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `ip_family_familiar_cloud` | 0.05 | yes | Cloud IP from familiar /24 — low signal |
| `ip_family_familiar_residential` | 0.15 | yes | Residential IP from familiar /24 — higher signal |
| `ip_new_known_asn_rule` | 0.30 | yes | New IP/24 but known ASN for established customer |
| `value_novelty_compound` | 0.35 | yes | `value_zscore > 1.5` + fully-new IP for established customer |
| `locked_customer_new_ip_family` | 0.30 | no | Cloud+API customer + known-ASN but new IP |

### Phase 3A — Modification-specific (8)

For the `POST /api/v1/shipments/modification/evaluate` endpoint. Weights chosen by operator judgment
per `.ai/decisions.md` "Modification rule weight rationale"; calibration deferred to Phase 6 staging
replay.

Booking-path dormancy: `build_context` populates `modification_type` with the `"none"` sentinel
(see `app/context.py::BOOKING_PATH_MODIFICATION_DEFAULTS`); the DSL evaluator requires every
referenced field to be populated, so the booking-path defaults are structural — NOT optional.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `modification_within_30_min_value_increase` | 0.65 | no | Value mod within 30 min + >20% magnitude — value-jacking |
| `modification_destination_change_pre_pickup` | 0.55 | yes | Destination change to unfamiliar within 24h — re-routing |
| `modification_high_velocity_1h` | 0.70 | no | `modification_velocity_1h > 3` — campaign signal |
| `modification_high_velocity_24h` | 0.45 | yes | `modification_velocity_24h > 10` — sustained volume |
| `modification_low_trust_customer` | 0.55 | no | Low-trust customer changing destination |
| `modification_dormant_customer` | 0.60 | yes | Dormant customer + destination mod — ATO pattern |
| `modification_recipient_change_to_unfamiliar` | 0.40 | yes | Recipient change to address not previously shipped |
| `modification_destination_change_residential_asn` | 0.35 | yes | Destination change + residential ASN |

### Phase 3B — Previously-rejected (4)

Consume the `email/phone/origin/ip_previously_rejected` Context fields populated in 3B.4. Weights
ported from freight_risk's catalogue. All maturity-sensitive: a thin-baseline customer with one
prior rejection still contributes under Layer 2 with appropriate downweighting.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `email_previously_rejected_for_customer` | 0.60 | yes | Email matches previously-rejected contact |
| `phone_previously_rejected_for_customer` | 0.60 | yes | Phone matches previously-rejected contact |
| `origin_previously_rejected_for_customer` | 0.70 | yes | Origin address previously rejected by reviewer |
| `ip_previously_rejected_for_customer` | 0.70 | yes | Source IP previously rejected by reviewer |

### Phase 4 — Calibration + cold-start + per-tenant maturity overrides (0 new rules)

Phase 4 did not add new rules. Three structural changes shipped:

1. **Currency-normalized value thresholds** (Phase 4B). The 7 currency-implicit rules
   (`vpn_high_value`, `low_trust_high_value`, `absolute_high_value`, `threat_intel_high_value`,
   `ip2p_threat_high_value`, `flags_with_value`, `high_value_new_user`) were rewritten to consult
   `shipment_value_threshold_{low,medium,high,new_user}` instead of literal value floors. Cap table
   per currency lives in `tenant_config.value_caps`; resolution via `app/value_caps.py::resolve_value_caps`.

2. **Per-tenant maturity overrides** (Phase 4C). `tenants.config` accepts
   `maturity_age_days`, `maturity_shipments`, `maturity_k` — all three optional and validated by
   pydantic constraints; null falls back to the global constant from `scoring_constants.py`.
   See `.ai/decisions.md § Cold start and maturity` for rationale.

3. **Cold-start grace multiplier**. `tenant_config.cold_start_grace_days` (default 0) widens the
   maturity downweighting on Layer 3 maturity-sensitive rules during the early-tenant window. While
   `tenant.created_at + cold_start_grace_days > now()`, Layer 2 maturity is multiplied by 0.5.
   Implementation in `app/scoring.py::_apply_cold_start_grace`.

### Phase 5 — Backfill workers, drift, observability (0 rule changes)

Phase 5 added baseline backfill workers, drift detection, and observability instrumentation. No
changes to `app/rules.yaml`.

### Phase 6 — Case-3 carrier dropoff (3)

Case-3a = established-customer compromise; case-3b = brand-new-customer fraud. Both threats use the
carrier-dropoff origin handoff as a structural attack signal.

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `case_3_compound` | 0.70 | yes | Case-3a — carrier-dropoff origin + route outside customer's top-N baseline + fully-new IP + `customer_observations >= 10` |
| `cold_start_outbound_carrier_dropoff` | 0.65 | no | Case-3b asymmetric — `customer_destination_country_mismatch_outbound` + carrier-dropoff origin + `customer_observations < 10`. Targets the Roulottes Lupien attack shape (Phase 7C.2 replacement for symmetric triangle; see below) |
| `cold_start_population_baseline_rare_with_carrier_dropoff` | 0.70 | no | Case-3b sophisticated — `shipment_route_rare_for_tenant` (<2% of tenant population, with tenant >=100 obs) + carrier-dropoff + `customer_observations < 10`. Tenant-population-derived; co-fires with the simple compound when both apply |

Deletion record: `cold_start_country_triangle_with_carrier_dropoff` (symmetric triangle compound)
was DELETED in 7C.2 after the Phase 6C empirical measurement (0/95 detection on the Roulottes Lupien
census) revealed the attack shape is asymmetric. The deletion is documented in `docs/history.md`.

### Phase 7 — Case-2 learning + calibrations (1 new rule, 5 calibrations)

#### New rule

| Rule | Weight | Maturity-sens. | Intent |
|---|---|---|---|
| `api_booking_from_unfamiliar_asn` | 0.65 | no | Case-2 learning-based (Phase 7C.7) — `is_api_booking AND unfamiliar_asn_for_customer`. Replaces the deleted `api_non_cloud_ip` + `non_cloud_established_account` (tenant-agnostic heuristics that fired universally on any tenant's API-from-non-cloud traffic). The learning-based version uses per-customer accumulated `ip_asn_stats`: deviation from the customer's own ASN baseline is the signal |

#### Weight calibrations (Phase 7C.8 and 7C.12)

| Rule | Old → New weight | Phase | Rationale |
|---|---|---|---|
| `unknown_destination_address` | 0.20 → 0.10 | 7C.8 | Case-2 detection migrated to `api_booking_from_unfamiliar_asn`; this rule shifts from primary FPR contributor to secondary corroborating signal |
| `unfamiliar_ip_country_for_origin` | 0.30 → 0.15 | 7C.8 | Same rationale; 72% baseline fire rate on approved corpus is intentionally preserved but contribution drops below REVIEW-pushing |
| `ip_intercontinental_jump` | 0.65 → 0.20 | 7C.12 | MaxMind-enabled geo measurement revealed fire on legitimate multi-region behavior; signal preserved on noisy-OR ladder at REVIEW-pushing weight |
| `ip_long_distance_new_ip` | 0.35 → 0.15 | 7C.12 | Same MaxMind-calibration cohort |
| `ip_country_change` | 0.25 → 0.15 | 7C.12 | Same MaxMind-calibration cohort |
| `impossible_travel_geo` | 0.65 → 0.30 | 7C.12 | Biggest single approved-corpus BLOCK contributor (71.7% fire share, n=346); signal preserved at REVIEW |

#### Deletions in Phase 7

- `api_non_cloud_ip` (deleted 7C.6) — tenant-agnostic novelty heuristic, replaced by
  `api_booking_from_unfamiliar_asn`.
- `non_cloud_established_account` (deleted 7C.6) — same signal at lower weight; same replacement.
- `cold_start_country_triangle_with_carrier_dropoff` (deleted 7C.2) — symmetric triangle compound;
  replaced by `cold_start_outbound_carrier_dropoff`.

All three deletions are documented in `docs/history.md`.

---

## Rule catalogue organisation

- Categories captured as YAML comments grouping rules (`# --- Threat intel ---`, `# --- Dormancy ---`,
  etc.). Categories are documentation; the loader treats them as flat.
- Hard-BLOCK rules first in the file (explicit precedence).
- `maturity_sensitive` flag explicit per rule (default `false`).
- Carrier-dropoff and tenant-population baseline rules (Phase 6+) gate cold-start customer state via
  inline `customer_observations < 10` condition rather than `maturity_sensitive: true`, so the
  signal-to-noise ratio for newly-onboarded customers stays intact.
- The 7 currency-implicit rules consult `shipment_value_threshold_*` fields rather than literal value
  floors so per-tenant currency caps work without rule-set forks.

---

## Loading rules

`app/rules.py::load_rules(yaml_path) -> RuleSet`

At app lifespan:

1. Load YAML
2. Validate top-level `thresholds` are present (or fall back to hardcoded defaults matching
   `.ai/decisions.md`)
3. For each rule, parse `condition` via DSL, compile to callable, store with `weight`, `action`,
   `maturity_sensitive`, `name`, `description`
4. Walk all conditions collecting `Name` references; assert each resolves to a known Context field
   (fail-fast)
5. Return immutable `RuleSet` consumed by the scorer

No hot-reload via fsnotify. Restart is the way to deploy a rule-set change. (5-second restart on
small instances; well below operational sensitivity.)
