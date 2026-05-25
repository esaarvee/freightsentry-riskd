# rules.md — Scoring Model & DSL Reference

> For individual rules (conditions, weights, categories), see `services/rules-engine/configs/rules.yaml`.
> Hot-reloaded via fsnotify. SHA-256 of file = `rule_version` in API responses.
> 62 rules total (8 BLOCK + 54 scoring).

---

## Scoring Model

Three-layer noisy-OR model:

1. **Hard BLOCK** — short-circuit rules (score = 1.0, no further evaluation)
2. **Account prior** — continuous maturity × base_prior + trust_risk × trust_factor + flag_prior; combined via noisy-OR
3. **Transaction signal** — remaining rules via noisy-OR; `maturity_sensitive` rules downweighted by `maturity_k`

Final score: `noisy-OR(P_account, P_signal)`. Max new-account prior = 0.10 (verified sales accounts).

### Account Prior Formula

```
maturity = min(age_days / maturity_age_days, log1p(shipments) / log1p(maturity_shipments))
base_prior = max_new_account × (1 - maturity)
trust_risk = ((0.5 - trust_score) / 0.5) × trust_factor           [activates only when trust_score < 0.5]
flag_prior = noisy-OR of flag tier weights for flagged_count > 2, > 5
account_prior = noisy-OR(base_prior, trust_risk, flag_prior)
```

Customer maturity: inherited from customer_entity aggregates, weighted by `customer_inheritance_factor=0.50`. New employee at 5-year customer inherits partial maturity.

### Maturity Downweighting

Rules marked `maturity_sensitive: true` in YAML are downweighted:
```
effective_weight = weight × (1 - maturity_k × maturity)
```
At full maturity: effective = weight × 0.30. At zero maturity: effective = weight × 1.0.

---

## Thresholds

```yaml
thresholds:
  allow_max:  0.50   # score <= 0.50 → ALLOW
  block_min:  0.70   # score >= 0.70 → BLOCK  — REVIEW band is (0.50, 0.70)

  account_prior:
    max_new_account: 0.10          # all accounts are sales-verified; day-0 prior capped low
    maturity_age_days: 180
    maturity_shipments: 50
    trust_factor: 0.25
    flag_weights: [0.15, 0.35]     # noisy-OR tier weights for flagged_count > 2, > 5
    customer_maturity_age_days: 730
    customer_maturity_shipments: 500
    customer_inheritance_factor: 0.50

  maturity_k: 0.70                 # downweight factor for cold-start-sensitive rules
```

### Threshold Rationale

- `allow_max=0.50`, `block_min=0.70`: REVIEW band narrowed from (0.30, 0.60) to (0.50, 0.70)
  to reduce AI token costs. Only 10% of REVIEW decisions are sampled to `stream:ai_analysis`
  (controlled by `AI_SAMPLE_RATE` env var; default 0.10). All decisions are still audited in `audit_logs`.
- `max_new_account=0.10` (not 0.25): all accounts are sales-verified by human reps.
  Day-0 users are not inherently suspicious — ATO is the primary threat model.

---

## Scoring DSL Context Fields

Available condition variables in `rules.yaml`:

**Transaction fields:**
- `shipment_value` (float) — from request
- `is_vpn` (bool) — computed in rules engine via IP2Proxy LITE PX11 when `IPPROXY_ENABLED=true`; falls back to request payload value when the store is disabled (see `[VPN-DETECTION]` in `.ai/decisions-security.md`)
- `is_new_route` (bool) — new to user AND new to customer
- `is_new_ip` (bool) — new to user AND new to customer
- `is_new_user` (bool) — `total_shipments == 0` (user-level)
- `is_new_device` (bool) — FingerprintJS `visitor_id` not in `known_devices`; False if no fingerprint provided
- `is_abnormally_dormant` (bool) — days since last_seen > max(max_inactive_days × 1.5, 30)
- `value_zscore` (float) — z-score vs user avg shipment value

**User/fraud profile:**
- `trust_score` (float, 0.0–1.0)
- `flagged_count` (int)
- `fraud_confirmed` (int)
- `is_blocked` / `user_is_blocked` (bool)
- `total_shipments` (int64)
- `account_age_days` (int32)

**Velocity (from Redis):**
- `velocity_user_hourly` (int)
- `velocity_user_daily` (int)
- `velocity_ip_hourly` (int)

**Threat intelligence (from FireHOL store):**
- `ip_in_level1` (bool) — FireHOL Level 1 match
- `ip_in_level2` (bool) — FireHOL Level 2 match
- `ip_in_threat_list` (bool) — Level 1 OR Level 2
- `ip_threat_score` (float) — 0.0–1.0 score

**IP2Proxy threat class (from the same PX11 BIN that drives `is_vpn` — see `[VPN-DETECTION]`):**
Mutually exclusive on the IP2Proxy record — at most one is true per IP. Empty / cold-start does not imply verified-clean.
- `ip_proxy_threat_spam` (bool) — IP flagged as a known spam source
- `ip_proxy_threat_scanner` (bool) — IP flagged as a known port/credential scanner
- `ip_proxy_threat_botnet` (bool) — IP flagged as a known botnet member

**Geolocation proximity (from MaxMind GeoLite2-City store):**
- `ip_distance_km` (float) — haversine distance between current IP and last known IP
- `ip_country_changed` (bool) — true if IP country changed since last evaluation

**Cloud IP classification (from CIDR trie — GCP/AWS/Azure/Cloudflare ranges):**
- `is_cloud_ip` (bool) — IP belongs to a known cloud provider range; also returned in `EvaluationResponse` so the async-worker can write behavioral Redis signals

**ATO behavioral signals (pre-computed by async-worker from prior evaluations; gateway reads from Redis before each call):**
- `user_concurrent_ip_types` (bool) — this user used both cloud and non-cloud IPs today (UTC)
- `customer_concurrent_ip_types` (bool) — this account used both cloud and non-cloud IPs today
- `user_unique_non_cloud_ips_daily` (int32) — HyperLogLog count of distinct non-cloud IPs this user appeared from today; cloud IPs excluded to avoid inflation from rotating GCP pool
- `ip_account_count_non_cloud_daily` (int32) — HyperLogLog count of distinct account IDs that used this non-cloud IP today
- `user_unique_netblocks_daily` (int32) — HyperLogLog count of distinct /16 netblocks this user appeared from via non-cloud IPs today; proxy farms cycle through unrelated /16 ranges; legitimate users stay within their ISP's block

**Blacklist:**
- `ip_blacklisted` (bool) — from Redis `blacklist:ips` set
- `device_blacklisted` (bool) — from Redis `blacklist:devices` set; dormant until platform sends `device_fingerprint`

**Global block list (feedback-gated; populated by CONFIRMED_FRAUD analyst feedback only):**
- `is_ip_globally_blocked` (bool) — from Redis `global_block:ip` set; inet text value
- `is_device_globally_blocked` (bool) — from Redis `global_block:device` set; raw device fingerprint
- `is_email_globally_blocked` (bool) — from Redis `global_block:email` set; lowercase hex of HMAC-SHA256
- `is_phone_globally_blocked` (bool) — from Redis `global_block:phone` set; lowercase hex of HMAC-SHA256
- Cloud IPs are never written to `global_block:ip` (gated by `is_cloud_ip` flag in async-worker feedback handler)
- All four trigger hard-BLOCK rules at weight=1.0 (score=1.0, no further evaluation)

**Contact identity signals (from customer_profiles JSONB stat-dicts; gateway-computed against HMAC blind indexes):**
- `is_new_email` (bool) — HMAC-SHA256 of the exact email address not in `known_emails`; suppressed when dict is empty (no history)
- `is_new_email_domain` (bool) — HMAC-SHA256 of the email domain (everything after `@`, lowercased) not in `known_email_domains`; suppressed when dict is empty; None-returning hash (malformed input) treated as unknown = True (conservative). Cross-language HMAC normalization locked by golden vectors in `test_pii_hash.py`.
- `is_new_phone` (bool) — HMAC-SHA256 of the E.164 phone number not in `known_phones`; suppressed when dict is empty
- `is_new_phone_prefix` (bool) — HMAC-SHA256 of the first 3 digits of the E.164 phone (after stripping `+`) not in `known_phone_prefixes`; suppressed when dict is empty; pre-image space is 1,000 values (intentional grouping signal, not PII anonymization). Cross-language HMAC normalization locked by golden vectors in `test_pii_hash.py`.
- `is_new_user_agent` (bool) — UA family HMAC not in `known_user_agents`; suppressed when dict is empty; version-stable (Chrome/120 = Chrome/121)
- Both legs (origin + destination) are OR'd: signal fires if EITHER leg is novel
- Async-worker upserts stat-dicts after each ALLOW evaluation; CONFIRMED_FRAUD feedback removes vectors
