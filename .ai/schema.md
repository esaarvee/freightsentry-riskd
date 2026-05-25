# schema.md — Redis Keys, Stream Payloads & AI Contract

> For PostgreSQL table definitions, see `services/gateway/app/migrations/versions/` (Alembic revisions).
> For MySQL platform tables, see `infra/mysql/mysql-init.sql`.

---

## Enum Quick Reference

```sql
CREATE TYPE decision_type    AS ENUM ('ALLOW', 'REVIEW', 'BLOCK');
CREATE TYPE risk_level_type  AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL');
CREATE TYPE feedback_outcome AS ENUM ('CONFIRMED_FRAUD', 'FALSE_POSITIVE', 'LEGITIMATE', 'INCONCLUSIVE');
CREATE TYPE ai_confidence    AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'VERY_HIGH');
-- Note: ai_confidence is NOT named ai_confidence_type
```

---

## New PostgreSQL Tables (vector promotion)

Added by migration `c1d2e3f4a5b6_vector_promotion_tables` (decision-aware vector promotion).

### `global_blocked_vectors`

Feedback-gated global block list. Written only on `CONFIRMED_FRAUD` outcome.

| Column | Type | Notes |
|---|---|---|
| `vector_type` | `blocked_vector_type` enum | `IP`, `DEVICE`, `EMAIL`, `PHONE` |
| `vector_value` | TEXT | inet text / raw fingerprint / hex(hmac) |
| `source_transaction_id` | VARCHAR(100) | audit trail |
| `source_customer_id` | VARCHAR(100) | audit trail; NOT a scope gate |
| `source_decision` | `decision_type` enum | `'ALLOW'`, `'BLOCK'`, or `'REVIEW'` at the time of evaluation; `ALLOW` is valid for late-reversal entries |
| `blocked_at` | TIMESTAMPTZ | default NOW() |

Primary key: `(vector_type, vector_value)` — insert uses `ON CONFLICT DO NOTHING` for idempotency.

### `pending_review_vectors`

Holds vectors for BLOCK and REVIEW decisions until analyst feedback resolves them.

| Column | Type | Notes |
|---|---|---|
| `transaction_id` | VARCHAR(100) PK | |
| `user_id` | VARCHAR(50) NOT NULL | |
| `customer_id` | VARCHAR(50) | nullable |
| `original_decision` | VARCHAR(10) CHECK IN ('BLOCK','REVIEW') | |
| `ip_address` | INET | |
| `device_fingerprint` | TEXT | |
| `email_hmacs` | BYTEA[] | per-leg, deduped origin+destination |
| `phone_hmacs` | BYTEA[] | per-leg, deduped origin+destination |
| `ua_hmac` | BYTEA | stored for completeness; never promoted to block list |
| `ship_from` | VARCHAR(200) | |
| `ship_to` | VARCHAR(200) | |
| `shipment_value` | DECIMAL(12,2) | |
| `is_cloud_ip` | BOOLEAN | gates IP from global block on CONFIRMED_FRAUD |
| `created_at` | TIMESTAMPTZ | default NOW() |
| `expires_at` | TIMESTAMPTZ | `created_at + INTERVAL '60 days'` |

Rows are deleted on feedback resolution or after 60 days by `feedback.RunExpiry` (sweeps every `PENDING_SWEEP_INTERVAL`, default `1h`).

### `audit_logs` additions

Three columns added by the same migration:

| Column | Type | Notes |
|---|---|---|
| `email_hmacs` | BYTEA[] | per-leg HMAC-SHA256 of origin + destination emails |
| `phone_hmacs` | BYTEA[] | per-leg HMAC-SHA256 of origin + destination phones |
| `is_cloud_ip` | BOOLEAN | copied from stream:audit `is_cloud_ip` field |

Used by feedback handler for late-ALLOW reversal (when `pending_review_vectors` row has already expired but `CONFIRMED_FRAUD` feedback still arrives).

---

## Redis Key Patterns

Redis 7. All keys created/managed by freightsentry services.

### Streams (async pipeline)

| Stream key         | Producer       | Consumer group  | Purpose |
|--------------------|----------------|-----------------|---------|
| `stream:audit`     | Gateway        | `async_workers` | All evaluations → audit_logs INSERT |
| `stream:ai_analysis` | Async Worker (audit handler) | `async_workers` | REVIEW txns → Bedrock/Ollama → ai_analysis |
| `stream:feedback`  | Gateway        | `async_workers` | Feedback → trust score update |

Consumer group created by Redis init:
```
XGROUP CREATE stream:audit async_workers 0 MKSTREAM
XGROUP CREATE stream:ai_analysis async_workers 0 MKSTREAM
XGROUP CREATE stream:feedback async_workers 0 MKSTREAM
```

AI enqueue design: audit worker publishes to `stream:ai_analysis` AFTER
audit_log INSERT (so it has a real audit_log_id). Gateway does NOT publish
to `stream:ai_analysis` directly.

### Velocity Counters

| Pattern                          | Type   | TTL    | Purpose |
|----------------------------------|--------|--------|---------|
| `vel:user:{user_id}:hourly`        | STRING     | 3600s  | User txn count this hour |
| `vel:user:{user_id}:daily`         | STRING     | 86400s | User txn count today |
| `vel:ip:{ip}:hourly`               | STRING     | 3600s  | IP txn count this hour |
| `vel:user:{user_id}:destinations`  | HYPERLOGLOG | 86400s | Unique destination count (PFADD ship_to) |

Pipeline: INCR + EXPIRE in a single Redis pipeline per evaluation.
Background context (2s) for Increment() — not request context.

### Idempotency (Two-Key Pattern)

| Pattern                                    | Type   | TTL   | Purpose |
|--------------------------------------------|--------|-------|---------|
| `idempotent:{txn_id}:{user_id}:lock`       | STRING | 300s  | SET NX lock (prevents double processing) |
| `idempotent:{txn_id}:{user_id}:result`     | STRING | 300s  | Cached EvaluationResponse JSON |

Keys are scoped to `(transaction_id, user_id)` to prevent cross-user replay attacks. Both fields are restricted to `[A-Za-z0-9_\-]+` at the model layer so the colon delimiter is unambiguous.

### Blacklist Sets

| Key                   | Type | Purpose |
|-----------------------|------|---------|
| `blacklist:ips`       | SET  | Active blacklisted IPs (synced from PG `blacklist_ips` every 5min) |
| `blacklist:devices`   | SET  | Active blacklisted device fingerprints (synced from PG `blacklist_devices`) |

Sync atomicity: SADD to tmp keys → RENAME (no DEL gap).

### Global Block Sets

Feedback-gated. Populated only when analyst feedback outcome is `CONFIRMED_FRAUD`. Synced from the `global_blocked_vectors` PG table by the same blacklist syncer that handles `blacklist:ips/devices`.

| Key                    | Type | Value format | Purpose |
|------------------------|------|--------------|---------|
| `global_block:ip`      | SET  | inet text (e.g. `"1.2.3.4"`) | Globally blocked IP addresses |
| `global_block:device`  | SET  | raw device fingerprint string | Globally blocked device fingerprints |
| `global_block:email`   | SET  | lowercase hex of HMAC-SHA256 | Globally blocked email addresses |
| `global_block:phone`   | SET  | lowercase hex of HMAC-SHA256 | Globally blocked phone numbers |

Known limitations:
- Cloud IPs (`is_cloud_ip=true`) are never written to `global_block:ip` (gate in feedback handler).
- User agents are excluded (too low-cardinality for global blocking).
- Entries have no automatic TTL; removal requires a manual unblock workflow (not yet implemented).

### Pub/Sub

| Channel                  | Publisher | Subscriber | Purpose |
|--------------------------|-----------|------------|---------|
| `fraud:blacklist:sync`   | Gateway · Async Worker feedback handler | Async Worker blacklist syncer | Trigger immediate blacklist re-sync after `global_blocked_vectors` write |

### HyperLogLog

| Key                   | Purpose |
|-----------------------|---------|
| `hll:user:{user_id}`  | Approximate unique IP count for user (fraud signal) |

### ATO Behavioral Signals

Written by async-worker after each audit event. All keys expire after 48h. Consumed by gateway before each evaluation to populate ATO rule inputs.

| Pattern | Type | TTL | Purpose |
|---|---|---|---|
| `user:ip_types:{user_id}:{YYYYMMDD}` | SET | 48h | IP type members: `"cloud"` and/or `"residential"` seen for this user today |
| `customer:ip_types:{customer_id}:{YYYYMMDD}` | SET | 48h | IP type members for this account today |
| `user:non_cloud_ips:{user_id}:{YYYYMMDD}` | HLL | 48h | Approximate count of distinct non-cloud IPs for this user today |
| `non_cloud_ip:accounts:{ip}:{YYYYMMDD}` | HLL | 48h | Approximate count of distinct account IDs that used this non-cloud IP today |
| `non_cloud_ip:devices:{ip}:{YYYYMMDD}` | HLL | 48h | Approximate count of distinct device fingerprints for this non-cloud IP today — drives `consumer_asn_session_churn` |
| `user:netblocks:{user_id}:{YYYYMMDD}` | HLL | 48h | Approximate count of distinct /16 netblocks (IPv4 first two octets) for this user's non-cloud IPs today |

Concurrent presence of both `"cloud"` and `"residential"` in a user/customer ip_types SET → dual-channel ATO signal.
IPv6 addresses are excluded from `user:netblocks` (no /16 concept for IPv6).

### Memory Budget

~87MB total Redis usage. Redis configured: `maxmemory 256mb`, `maxmemory-policy allkeys-lru`, AOF persistence.

---

## Stream Payload Schemas

All stream values are **strings** in Redis (XADD serializes everything as string).

### `stream:audit` — Gateway → Audit Handler

Published by: `services/gateway/app/events.py` → `publish_audit()`
Consumed by: `services/async-worker/internal/audit/handler.go` → `processAuditMessage()`

**Blacklist path note**: fields marked `""` are absent on the fast-exit blacklist BLOCK path (IP/device blocked before enrichment runs). All other fields are always present.

**Core fields** (always present)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `transaction_id` | string | request |
| `user_id` | string | request |
| `customer_id` | string | request (may be empty) |
| `ip_address` | string | request |
| `is_vpn` | `"0"` / `"1"` | request bool → string |
| `device_fingerprint` | string | request (may be empty) |
| `ship_from` | string | request (may be empty) |
| `ship_to` | string | request (may be empty) |
| `shipment_value` | string | request Decimal → string |
| `user_agent` | string | request (may be empty) |
| `decision` | `"ALLOW"` / `"REVIEW"` / `"BLOCK"` | rules engine result |
| `risk_score` | string | float → string (e.g. `"0.4500"`) |
| `risk_level` | `"LOW"` / `"MEDIUM"` / `"HIGH"` / `"CRITICAL"` | rules engine result |
| `rules_triggered` | JSON array string | `'["rule1","rule2"]'` |
| `rules_triggered_count` | string | int → string; derived from `rules_triggered` length |
| `risk_factors` | JSON array string | `'[{"name":"...","description":"...","weight":0.3}]'` |
| `rule_version` | string | SHA-256 hash |
| `processing_time_ms` | string | int → string |
| `log_month` | string | `"YYYY-MM-01"` date |

**Enrichment context fields** (`""` on blacklist path)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `trust_score` | string or `""` | float → string; user trust score from PG |
| `flagged_count` | string or `""` | int → string; prior flagged shipments |
| `fraud_confirmed` | string or `""` | int → string; confirmed fraud count |
| `is_blocked` | `"0"` / `"1"` or `""` | bool; user blocked in PG |
| `avg_value` | string or `""` | float → string; average shipment value |
| `stddev_value` | string or `""` | float → string; stddev of shipment values |
| `is_new_user` | `"0"` / `"1"` or `""` | bool; no prior shipments |
| `is_new_route` | `"0"` / `"1"` or `""` | bool; origin/dest pair not seen before |
| `is_new_device` | `"0"` / `"1"` or `""` | bool; FingerprintJS visitor_id not seen before; `"0"` when fingerprint absent |
| `is_new_ip` | `"0"` / `"1"` or `""` | bool; IP not seen before for this user |
| `is_abnormally_dormant` | `"0"` / `"1"` or `""` | bool; unusual gap since last shipment |
| `value_zscore` | string or `""` | float → string; z-score vs user's history |
| `total_shipments` | string or `""` | int → string; lifetime shipment count |
| `account_age_days` | string or `""` | int → string; days since first shipment |
| `customer_total_shipments` | string or `""` | int → string; customer lifetime count |
| `customer_age_days` | string or `""` | int → string; days since customer first shipment |
| `total_shipments_value` | string or `""` | float → string; lifetime shipment value |
| `customer_total_shipments_value` | string or `""` | float → string; customer lifetime value |

**Velocity fields** (`""` on blacklist path)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `velocity_user_hourly` | string or `""` | int → string; user txn count this hour |
| `velocity_user_daily` | string or `""` | int → string; user txn count today |
| `velocity_ip_hourly` | string or `""` | int → string; IP txn count this hour |

**Threat intel fields** (`""` on blacklist path)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `ip_threat_score` | string or `""` | float → string; FireHOL composite threat score |
| `ip_in_threat_list` | `"0"` / `"1"` or `""` | bool; IP in any FireHOL list |
| `ip_in_level1` | `"0"` / `"1"` or `""` | bool; IP in FireHOL level 1 (high confidence) |
| `ip_in_level2` | `"0"` / `"1"` or `""` | bool; IP in FireHOL level 2 |

**Geolocation fields** (`""` on blacklist path)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `ip_distance_km` | string or `""` | float → string; distance from last known IP |
| `ip_country_changed` | `"0"` / `"1"` or `""` | bool; country differs from last known IP |

**Cloud IP classification field** (always present; no empty-string state)

| Field | Type (string-encoded) | Source |
|---|---|---|
| `is_cloud_ip` | `"0"` / `"1"` | bool; IP belongs to GCP/AWS/Azure/Cloudflare CIDR ranges; `"0"` on blacklist path (classification not attempted) |

**Contact identity fields** (comma-separated HMAC-SHA256 hex; `""` when no contacts present)

PII is never written to the stream in plaintext. The gateway pre-computes HMAC-SHA256 digests using `FG_PII_HMAC_KEY` and writes comma-separated hex strings. The async-worker decodes and deduplicates via `parseHexHashes`. Both legs (origin + destination) are combined in the same field.

| Field | Type (string-encoded) | Source |
|---|---|---|
| `email_hmacs` | comma-joined hex strings or `""` | HMAC-SHA256 of origin + destination email addresses (lowercased, trimmed) |
| `phone_hmacs` | comma-joined hex strings or `""` | HMAC-SHA256 of origin + destination E.164 phone numbers |
| `email_domain_hmacs` | comma-joined hex strings or `""` | HMAC-SHA256 of the domain portion (`@`-suffix) of each email; `None`-guarded (absent field → excluded from join) |
| `phone_prefix_hmacs` | comma-joined hex strings or `""` | HMAC-SHA256 of the first 3 digits of each E.164 phone; `None`-guarded |

Values are used by the async-worker to upsert `customer_profiles.known_emails`, `known_phones`, `known_email_domains`, `known_phone_prefixes` JSONB stat-dicts. Duplicates within a field (e.g. same origin and destination domain) are silently deduplicated on decode.

### `stream:ai_analysis` — Audit Handler → AI Handler

Published by: `services/async-worker/internal/audit/handler.go` (after INSERT, REVIEW only, sampled at `AI_SAMPLE_RATE` — default 10%)
Consumed by: `services/async-worker/internal/ai/handler.go` → `processAIMessage()`

| Field | Type (string-encoded) | Source |
|---|---|---|
| `audit_log_id` | string (UUID) | from INSERT RETURNING id |
| `transaction_id` | string | from audit message |
| `risk_score` | string | from audit message |
| `rules_triggered` | JSON array string | from audit message |
| `risk_factors` | JSON array string | from audit message |
| `shipment_value` | string | from audit message |
| `rule_version` | string | from audit message |
| `user_id` | string | from audit message (MCP enrichment) |
| `ip_address` | string | from audit message (MCP enrichment) |
| `customer_id` | string | from audit message (MCP enrichment) |
| `ship_from` | string | from audit message (MCP enrichment) |
| `ship_to` | string | from audit message (MCP enrichment) |

Last 5 fields added in Module 3 (MCP). Missing fields default to empty string (backward-compatible).

### `stream:feedback` — Gateway → Feedback Handler

Published by: `services/gateway/app/events.py` → `publish_feedback()`
Consumed by: `services/async-worker/internal/feedback/handler.go` → `processFeedbackMessage()`

| Field | Type (string-encoded) | Source |
|---|---|---|
| `transaction_id` | string | request |
| `user_id` | string | looked up from audit_logs |
| `outcome` | string | `"CONFIRMED_FRAUD"` / `"FALSE_POSITIVE"` / `"LEGITIMATE"` / `"INCONCLUSIVE"` |
| `confirming_signals` | JSON array string | rule names attributed to this outcome; default `"[]"` (max 50 items, each ≤ 100 chars) |

---

## AI Analysis Contract

Source: `services/async-worker/internal/ai/` (provider.go, prompt.go, bedrock.go)

### Normalization Defaults (`normalizeResult`)

| Field | Default when empty/missing |
|---|---|
| `Decision` | `"REVIEW"` |
| `Confidence` | `"LOW"` |
| `KeyConcerns` | `[]` (empty slice) |

### Validation Rules (`validateResult`)

- `Decision` must be one of: `ALLOW`, `REVIEW`, `BLOCK`
- `Confidence` must be one of: `LOW`, `MEDIUM`, `HIGH`, `VERY_HIGH`
- `RiskScore` must be in range `[0.0, 1.0]`
- Returns error if any check fails (result discarded, not persisted)

### Provider Differences

| Property | Ollama | Bedrock |
|---|---|---|
| API | `POST /api/chat` | Converse API (`bedrockruntime.Converse`) |
| Timeout | 5 minutes | 30 seconds |
| Retries | None | 2 retries |
| Tool calling | `/api/chat` with `tools` array | `ConverseInput.ToolConfig` |
| Model config | `OLLAMA_MODEL` env var | `BEDROCK_MODEL_ID` env var (default: `anthropic.claude-haiku-4-5`) |
| Selection | `AI_PROVIDER=ollama` | `AI_PROVIDER=bedrock` |

### `tool_trace` Column (ai_analysis)

Added by migration `b3c4d5e6f7a8`. JSONB, nullable — NULL when `MCP_ENABLED=false`.

```json
{
  "tools_called": [
    {
      "step": 1,
      "tool_name": "get_account_history",
      "arguments": {"user_id": "usr_abc", "lookback_days": 90},
      "result_summary": "47 shipments, 2 flagged, avg value $1,200",
      "result_tokens": 10,
      "latency_ms": 12
    }
  ],
  "tool_rounds": 2,
  "tool_cap_hit": false,
  "input_tokens_total": 720,
  "output_tokens_total": 150
}
```

Go struct: `mcp.AnalysisTrace` in `services/async-worker/internal/mcp/types.go`.
