# Phase 1 Verification

Purpose: confirm reference-codebase facts the new project's Design Context relies on, surface discoveries the Design Context did not anticipate, and articulate design risks worth knowing before execution. This document is **not** a restatement of the Design Context (in the bootstrap prompt) — only what changes, supplements, or challenges it.

Verification was performed against:

- `/Users/drshott/PycharmProjects/miscProj/freight_risk/` (commit at HEAD of `freight_risk` package as of 2026-05-25)
- `/Users/drshott/PycharmProjects/github_fc/freightcom-risk/` (commit at HEAD of `services/` as of 2026-05-25)

Two read-only research agents were deployed in parallel (one per codebase), each scoped to the read-list in the bootstrap prompt. Subsequent ad-hoc `grep` confirmations are noted inline.

---

## 1. Design Context fact corrections

These two items in the Design Context are objectively wrong and should be treated as superseded by this verification:

### 1.1 FreightSentry rule count is 102, not 117

The Design Context's "Known audit errors" table claims `^  - name:` count returns 117. Direct re-count:

```
$ grep -c '^  - name:' services/rules-engine/configs/rules.yaml
102
```

The audit doc (`docs/initial-audit.md` line 48) was correct; the Design Context's correction was wrong. The new-project rule-count projection ("~95-100 by end of Phase 2") is unaffected because it derives from freight_risk's 84-rule base + 10-15 FreightSentry-port rules, not from FreightSentry's full 102.

### 1.2 Recipient-overlap rules are in freight_risk, not FreightSentry

The Design Context lists `recipient_used_by_many_customers` / `recipient_used_by_very_many_customers` under "**Add from FreightSentry (~10-15 unique rules) → Cross-customer recipient overlap**." Direct check:

```
$ grep -nE 'recipient_used_by' freight_risk/rules.yaml
524:  - name: recipient_used_by_many_customers
529:  - name: recipient_used_by_very_many_customers
$ grep -nE 'recipient_used_by' services/rules-engine/configs/rules.yaml
(no matches)
```

Both rules are part of the freight_risk 84-rule base and are already in scope without a FreightSentry port. The FreightSentry-port count drops from ~15 to ~13.

Net rule-count target for end of Phase 2 is unchanged at ~95-100 because the recipients were already in the freight_risk base.

---

## 2. freight_risk implementation facts the Design Context did not capture

These are domain knowledge embedded in the reference code that must port into the new system. Failing to carry them forward will regress detection quality measurably.

### 2.1 Datacenter-keyword heuristics (signals.py:132-149)

`is_datacenter_asn(asn_org: str) -> bool` matches against two lower-cased substring sets:

- `_DATACENTER_KEYWORDS` — 15 phrases: `data center`, `colocation`, `web hosting`, `cloud`, `vps`, `dedicated server`, etc.
- `_DATACENTER_PROVIDERS` — ~20 brand names: `estruxture`, `equinix`, `ovh`, `hetzner`, `digitalocean`, `linode`, `vultr`, `rackspace`, `softlayer`, `leaseweb`, etc.

This drives the `is_datacenter_ip` Context flag, which is load-bearing for four `non_cloud_*` rules. Without it, eStruxture/Equinix/OVH IPs trip `api_non_cloud_ip` incorrectly. **Must port these constants verbatim.**

### 2.2 Tuned thresholds (not the original defaults)

freight_risk's production-tuned values diverge from earlier defaults captured in the YAML:

| Setting | Original | Tuned (current) | Source |
|---|---|---|---|
| `cadence_anomaly` z-threshold | z > 4 | **z > 6** | rules.yaml:464-472 + inline comment ("z>4 fired ~3.4K times on Mondays because weekday-only customers' weekend gaps are ~16 std-dev above their weekday norm") |
| `velocity_spike_daily_api` | 5000 | **50** | rules.yaml:131-138 + inline comment dated 2026-05 |
| `ip_familiarity_tier` "cloud + ASN" shortcut | enabled | **removed** | baseline.py:504-511 ("ASN granularity (e.g. all of GCP) is too coarse; /24 match is now the only signal that confers `family_familiar`") |
| `residential_asn_high_velocity` IP-velocity threshold | 5 | **15** | rules.yaml:540-543 |

**Action**: carry the tuned values directly into the new system. Do not regenerate defaults from the comment-headers.

### 2.3 freight_risk scoring threshold source-of-truth

The `Thresholds` dataclass defaults at `scoring.py:32-35` are `0.50 / 0.70`, but `load_ruleset()` overrides them from `rules.yaml:9-11` to `0.60 / 0.80`. The Design Context picks 0.60/0.80 (correct), but the new-project's `app/scoring.py` port must either (a) not have dataclass defaults that diverge from production values, or (b) assert on load that YAML thresholds are present. Otherwise a malformed YAML load could silently fall back to 0.50/0.70.

### 2.4 IP2Proxy `is_proxy` is gated on non-empty `proxy_type` (enrich.py:230)

The `is_proxy` boolean is only set True when `proxy_type` is non-sentinel (sentinels: `""`, `-`, `INVALID IP ADDRESS`, `NOT SUPPORTED`, `INVALID DATABASE FILE`, `DATABASE NOT FOUND`, plus any payload containing non-printable bytes — guard against corrupt BIN). This must port: a naive port that takes `is_proxy` straight from the SDK without the gate will get false-positives from sentinel rows.

### 2.5 `email_matches_customer_name` removal — what the new system loses

Dropped per design (constraint #14). The function's downstream effect at `scoring.py:461-471` is a **post-noisy-OR trust override** that forces any non-hard-BLOCK result to ALLOW/GREEN with score=0.0 when the booking email's local-part matches the customer's business-name slug (three-tier match: forward token / reverse slug / fuzzy LCS). Removing this:

- Hard-BLOCK rules still win (no change there).
- Legitimate corporate-domain bookings will not bounce through REVIEW indefinitely — Layer-2 account-prior decay and Layer-3 maturity downweighting handle this for established customers. Cold-start customers shipping from corporate-domain emails *may* see one or two REVIEWs before maturity kicks in.
- The synthetic `trust_email_domain_match` factor disappears from audit trails.

This is acceptable per the Design Context's rationale. Flag for Phase 6 staging-replay measurement: confirm FPR on legitimate corporate-email cold-start bookings stays under operator tolerance.

### 2.6 `@lru_cache(200_000)` on `hmac_hex` is a secret-rotation hazard (signals.py:112-116)

`hmac_hex(value, secret)` is cached with the secret as part of the key. On secret rotation the cache must be cleared, otherwise stale hashes survive. **In the new project: drop the LRU decorator.** Per-request cost at 100 TPS is negligible.

### 2.7 FireHOL extended list (8 additional files) is intentionally not loaded (enrich.py:30-38)

The comment cites ~8s startup penalty as the reason. For an always-warm FastAPI service this concern goes away, but the rules consume only Level 1 / Level 2 / IP2Proxy threat — extended lists would only enrich the `fh_lists` diagnostic string. **Keep Level 1 + Level 2 only.**

---

## 3. FreightSentry implementation facts the Design Context partially captured

### 3.1 IP stat-dict `type` field is a FreightSentry extension, not in freight_risk

`statdict.go:63-68` defines:

```go
type Entry struct {
    N    float64 `json:"n"`
    RN   float64 `json:"r_n"`
    Last string  `json:"last"`
    Type string  `json:"type,omitempty"`
}
```

freight_risk's `_empty_entry()` (baseline.py:61) does NOT have `type`; IP-type info there lives in a separate flat `ip_type_hist`. The Design Context correctly anticipates the per-entry `type` field as a new-system extension required for per-IP-type decay. Worth recording explicitly: when porting freight_risk's `baseline.py`, the IP-stats handling diverges from the original — add `type` per entry, drop the standalone `ip_type_hist`.

### 3.2 Per-IP-type half-lives confirmed verbatim (statdict.go:40-43)

```
HalfLifeDaysIPCloud       = 365.0
HalfLifeDaysIPDC          = 365.0
HalfLifeDaysIPResidential = 60.0
HalfLifeDaysIPUnknown     = 180.0
```

Plus `HalfLifeDays = 90.0` for non-IP stat-dicts (statdict.go:30). Matches Design Context exactly.

### 3.3 FreightSentry constants that differ from Design Context (Design Context wins; recording divergence for measurement)

`services/rules-engine/configs/rules.yaml:30-43` + `services/rules-engine/internal/scoring/scorer.go:300-415`:

| Constant | Design Context (new project) | FreightSentry production | Divergence rationale |
|---|---|---|---|
| `MaturityK` | **0.30** | 0.70 | Design Context picks the foundation-default value. FreightSentry's 0.70 was a tuned response to its rule mix. New project's mix is closer to freight_risk's; 0.30 is the right starting point. Phase 6 staging replay measures whether to raise it. |
| `flag_weights` | **4-tier `[0.00, 0.15, 0.25, 0.35]`** at tiers (0, 1-2, 3-5, 6+) | 2-tier `[0.15, 0.35]` at thresholds (>2, >5), noisy-OR over independent activations | Design Context picks the finer-grained scheme. Behavioural difference is small for low flag counts; matters only when flagged_count is in the gap regions. |
| `allow_max` / `block_min` | **0.60 / 0.80** | 0.50 / 0.70 (post-widening) | Design Context picks the freight_risk-validated thresholds (where 98% case-2 recall was measured). FreightSentry widened to 0.50/0.70 for its own rule mix. |

**No operator action required**: the Design Context is authoritative. Recording here so Phase 6 replay measurements have a baseline to compare against.

### 3.4 `customer_locked_cloud_api` is derived, not stored

The rule conditions on it (rules.yaml:773-785, two highest-value case-2 detectors) but no schema column carries the boolean. FreightSentry derives it gateway-side from the triple `(cloud_share_n, api_share_n, effective_observations)`, all of which we do carry per Design Context. The new project must replicate this derivation in `build_context()`. Threshold per FreightSentry production: `cloud_share / total > 0.95 AND api_share / total > 0.95 AND effective_observations >= 20`.

### 3.5 customer_profiles columns the new project must add fresh

FreightSentry's `customer_profiles` (schema.sql:416-447) has **no** columns for:

- Value Welford triples (`value_n`, `value_mean`, `value_m2`) — Design Context expects them; FreightSentry stores them at user_profiles level.
- `last_booking_lat` / `last_booking_lon` / `last_booking_country` — Design Context expects them; FreightSentry computes from audit log on demand.
- `origin_stats`, `dest_stats`, `country_stats`, `origin_ip_country_stats` — FreightSentry collapses origin+dest into a single `common_routes` jsonb; Design Context wants them split.
- `rejected_email_hmacs` / `rejected_phone_hmacs` — FreightSentry and freight_risk both reuse the same `known_emails` / `known_phones` stat-dict with the `r_n` field. Design Context calls for separate columns. **This is a design choice worth confirming**: separate columns are clearer in queries but write-amplify (every contact observation touches two columns). Either works; defaulting to the Design Context (separate) is fine.

### 3.6 FreightSentry columns worth carrying that the Design Context doesn't list

- `shipment_volume_30d` (int) — FreightSentry caches this as a persisted column. **Per operator amendment 2026-05-25, the new project does NOT persist it.** Rules and admin endpoints needing a 30-day window count compute `COUNT(*) FROM shipments WHERE booking_ts > now() - interval '30 days'` on demand. Rules wanting a decay-weighted activity proxy read `customer_baselines.value_n` (post-decay) via the `customer_observations` Context field.
- `is_api_partner` (bool) — operator-set suppression flag; already in Design Context tenant config as `is_api_partner_default`. Per-customer override would be a Phase 4+ extension.
- `known_cloud_providers` (text[]) — supplementary classification for `customer_locked_cloud_api`. Optional.

Recommendation: defer `known_cloud_providers` unless a Phase 2 rule needs it.

---

## 4. IP enrichment source status

All four sources confirmed accessible and unchanged from the freight_risk integration shape:

| Source | URL | Auth | License | Refresh | freight_risk wrapper |
|---|---|---|---|---|---|
| MaxMind GeoLite2 City + ASN | `https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-{City,ASN}&suffix=tar.gz&license_key=…` | Free signup → account_id + license_key | GeoLite2 EULA (free use, attribution required, no redistribution; per-deployment license) | Tue/Fri weekly | `enrich.py::_load_maxmind`, `_mm_lookup` |
| FireHOL netsets | `https://github.com/firehol/blocklist-ipsets` (git clone or raw file fetch for `firehol_level1.netset`, `firehol_level2.netset`) | None | CC-BY-SA / public-domain (per upstream README) | Continuously updated; daily pull recommended | `enrich.py::_load_firehol`, `_firehol_match` |
| IP2Proxy LITE PX11 | `https://lite.ip2location.com/database-download?database=PX11LITEBIN&token=…` | Free signup → download token | CC-BY-SA 4.0 | Monthly | `enrich.py::_load_ip2proxy`, `_ip2proxy_lookup` |
| Cloud provider CIDRs | AWS: `https://ip-ranges.amazonaws.com/ip-ranges.json`<br>GCP: `https://www.gstatic.com/ipranges/cloud.json`<br>Azure: weekly download via `https://www.microsoft.com/en-us/download/details.aspx?id=56519` (URL changes per release; the page is the stable entry point)<br>Cloudflare: `https://www.cloudflare.com/ips-v4` | None | Public | AWS: continuously; GCP: weekly; Azure: weekly; Cloudflare: rare | `enrich.py::_load_cloud`, `_cloud_match` |

**Interface changes since freight_risk integration**: none detected. URLs above match the live endpoints as of 2026-05-25. Azure's CIDR download page wraps a dated JSON file (`ServiceTags_Public_YYYYMMDD.json`); the new project's `scripts/fetch_enrichment.py` must scrape the latest filename from the page rather than hardcoding it.

**Two enrichment-source decisions for Phase 1 plan**:

1. **License-key storage** — MaxMind and IP2Proxy each have a single secret. Store under `MAXMIND_LICENSE_KEY` / `IP2PROXY_DOWNLOAD_TOKEN` (no env prefix per operator amendment 2026-05-25). Loaded by pydantic-settings.
2. **Refresh script execution model** — `scripts/fetch_enrichment.py` runs out-of-process. In v1 it runs as an ECS scheduled task (Phase 6) or local cron (dev). The app reads `ip_enrichment` row freshness via the `updated_at` column; serves stale-but-cached if upstream is unavailable. Both these are already in Design Context — flag-only.

**Verification gap not closed**: I have not actually attempted a download from any source. The URLs are documented but not pinged. Live verification belongs in Batch 1D when the refresh script lands. If any source's auth has changed since the freight_risk integration, Phase 1 will surface it.

---

## 5. Items that have no port path (must be designed fresh)

These three pieces of the Design Context lack any direct reference implementation:

### 5.1 Modification-evaluation signals (Phase 3)

Neither freight_risk nor FreightSentry has shipment-modification evaluation. The Design Context's bullet list (time-since-booking buckets / magnitude / direction / modification velocity) is specification, not port. Phase 3 will design from scratch. Risk: the bullet list is a starting hypothesis; if real-world modification fraud patterns diverge from it, Phase 3 will iterate.

### 5.2 Multi-tenant scoping enforcement

Both reference codebases are effectively single-tenant. The Design Context's tenant_id-everywhere + Postgres RLS plan is sound but fresh. Phase 1's schema migration must add `tenant_id` and the RLS policies in the same commit. RLS coverage gaps are an easy security finding for the security-auditor reviewer to catch — first migration is the right place to lock it down.

### 5.3 Per-tenant config JSONB validation

FreightSentry has per-customer rule weight overrides (out of scope for v1 per Design Context). It does not have per-tenant config. The Pydantic `TenantConfig` schema in Design Context is fresh design. Phase 4 work; nothing to verify against.

---

## 6. Design risks

The Design Context section "What could be wrong" lists four items. Adding to that, post-verification:

### 6.1 Trust score divergence between Phase 1 plan and Phase 2 plan

The Design Context says Phase 1 implements Layers 1 and 3 of scoring; Layer 2 (account-prior + trust) arrives in Phase 2. But several FreightSentry-port rules (`very_low_trust`, `low_trust_high_value`, etc.) condition on `trust_score`. These rules cannot fire in Phase 1; they wait for Phase 2's trust-score computation to land. Phase 1's initial 12-15 rules (per Batch 1D scope) must therefore exclude all trust-conditional rules. Plan must call this out explicitly so Phase 1 reviewers don't flag missing rules as a gap.

### 6.2 Rule-conditional fields not yet enumerated

The Design Context lists rule families but no single inventory enumerates every Context field that rule conditions read. Without this enumeration, the DSL evaluator's whitelist (per Batch 1D scope: `Name` AST nodes resolve from env) cannot be exhaustively tested. Mitigation: in Batch 1D, generate the field whitelist by parsing `rules.yaml` once at startup and asserting every referenced field exists in the Context model. This catches typo'd field names at startup, not at request time. (freight_risk's evaluator does this implicitly via the `_NUMERIC_FIELDS` / `_BOOL_FIELDS` frozensets at scoring.py:337-376 — reuse the pattern.)

### 6.3 case-1 detection is inferred from signal-stack reasoning, not measured

Design Context acknowledges this. The expected signals (`ip_class_deviation` + `velocity_burst` firing → REVIEW or BLOCK) are present in both reference catalogs, but the 50-shipment dashboard ATO has never been replayed end-to-end. Phase 6 must produce a fixture for case 1 alongside the existing case-2 fixture, and the staging replay must hit case 1 specifically — not assume case-2 coverage transfers.

### 6.4 FPR at the 98% recall operating point is unmeasured

freight_risk reports 98% recall on case 2; the false-positive rate at the 0.60/0.80 thresholds is not published in either repo's documentation. If FPR is meaningfully above 1%, REVIEW queue volume could overwhelm operators at >45K shipments/day (450+ REVIEWs/day at 1% FPR). Phase 6 staging-replay must report FPR alongside recall and surface to operator for threshold-tuning before production cutover.

### 6.5 Pickup-time precision constraint

Design Context platform-constraint #2 ("Pickup time stored at date precision only — modification proximity bucketed coarsely") is acknowledged but its impact on modification rules is unmeasured. If most legitimate modifications happen within hours of booking (same-day bucket), the rule's signal-to-noise will be poor. Phase 3 must validate against historical modification data before locking in bucket boundaries.

### 6.6 Single-process at 100 TPS depends on Postgres tuning

Design Context's latency budget allocates 30-50ms p95 to context loading (baseline + enrichment + velocity counts via `asyncio.gather`). Velocity counts via SQL — even with indexes — are not free. At 100 TPS with 10 customer-IP combos per request, the per-second count-query load is ~1000 indexed-count-queries/sec on the shipments table. RDS small instance can handle this with appropriate `(tenant_id, customer_id, created_at)` and `(tenant_id, source_ip, created_at)` btree indexes, but it is the dominant load and Phase 5 load-test must measure under realistic baseline-store and enrichment-cache hit ratios, not synthetic 100% cache-hit conditions.

### 6.7 Phase 1 has no observability beyond logs

The bootstrap prompt requires "structured log + counter where applicable" on every commit that adds runtime behavior. In Phase 1 there is no metrics backend (CloudWatch arrives in Phase 5). Counters land as structured-log emissions tagged `metric: true` so Phase 5 can sink them. Document this in Batch 1B so reviewers don't flag missing CloudWatch wiring as a gap.

### 6.8 Reference codebases' email_matches_customer_name removal effect is not quantifiable here

We cannot quantify how many legitimate cold-start bookings the removal pushes through REVIEW until Phase 6 replay. If the number is operationally painful, the Design Context's mitigation path (operator-managed allowlist) is available but unscoped. Worth Phase 6 surfacing.

---

## 7. References (file:line)

freight_risk:

- signals.py:9-24 (THROWAWAY_DOMAINS), 26-30 (EMAIL_BLOCKLIST), 32 (KEYBOARD_MASH), 84-88 (LRU rationale), 112-116 (hmac_hex LRU concern), 132-149 (datacenter constants), 401-435 (email_matches_customer_name impl), 218-223 (FUZZY_GENERIC_OVERLAPS)
- enrich.py:30-38 (FireHOL extended skip), 40-45 (CLOUD_FILES), 48-60 (ASN cloud fallback), 76, 99, 110, 133, 140, 149, 160, 184, 218 (loader/lookup signatures), 200-201 (IP2P sentinels), 230-237 (proxy gating), 240, 278, 322-333 (enrich flow), 338, 349, 358 (helpers)
- baseline.py:61 (`_empty_entry`), 131-176 (`__init__`), 367-409 (`decay_to`), 504-511 (ip_familiarity_tier change rationale), 535 (add signature), 681-700 (JSON helpers)
- scoring.py:32-35 (Thresholds defaults), 49-52 (load_ruleset), 75-110 (DSL precedence), 237 (Context email-match field), 337-376 (field whitelists), 385-392 (virtual booleans), 396-417 (BLOCK loop), 422-431 (noisy-OR), 433-448 (decision/thresholding), 450-471 (trust override)
- rules.yaml:1-7 (offline-subset header), 9-11 (thresholds), 16-26 (BLOCK rules), 131-138 (velocity_daily_api tuning), 464-472 (cadence_anomaly tuning), 524, 529 (recipient rules location)
- score_runner.py:25, 310-322 (trusted_emails_cache)

FreightSentry:

- services/rules-engine/configs/rules.yaml:30-31 (thresholds), 33-43 (account-prior constants), 49-104 (BLOCK rules), 220-250 (trust-conditioned velocity rules), 293-337 (trust rules), 418-441 (dormancy rules), 540-550 (residential_asn velocity), 672-688 (threat/flags rules), 773-785 (lock-in rules)
- services/rules-engine/internal/scoring/scorer.go:300-415 (3-layer scoring), 441-458 (accountMaturity), 476-488 (flag tiers)
- services/rules-engine/internal/rules/loader.go:32 (Action default)
- services/async-worker/internal/statdict/statdict.go:30-43 (half-lives), 51-55 (type constants), 63-68 (Entry struct), 213-219 (DecayIP)
- services/async-worker/internal/feedback/ruleweights.go:14-22 (constants), 56-71 (feedback path), 98-105 (decay-fold)
- services/gateway/tests/golden/schema.sql:416-447 (customer_profiles), 482 (rejected-in-stat-dict comment), 517 (IP type-in-entry comment), 955-956 (PK)

---

## 8. Verification close

Sufficient confidence to proceed to Phase 2 plan. The two Design Context fact corrections in §1 are recorded; one decision (separate vs combined rejected-contact storage, §3.5) is noted as Design-Context-authoritative (separate columns) without operator escalation. All remaining items are observations to thread into the plans (§§2-7).

No AskUserQuestion escalation triggered. Proceeding to MASTER_PLAN.md + PLAN_PHASE_1.md.
