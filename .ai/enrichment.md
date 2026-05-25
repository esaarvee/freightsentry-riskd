# enrichment.md — IP enrichment + Context building

How the per-request `Context` object is assembled from baseline + IP enrichment + per-request fields. Code lives in `app/context.py`, `app/enrich.py`, `app/baseline.py`, `app/trust.py`, `app/velocity.py`.

For the IP enrichment source URLs and license terms, see `.ai/decisions.md` § IP enrichment sources.

---

## Top-level entry point

```python
async def build_context(
    conn: asyncpg.Connection,
    tenant_id: int,
    payload: BookingRequest,
    hmac_secret: bytes,
) -> Context
```

Single per-request orchestration. Runs all reads in parallel via `asyncio.gather`, applies lazy decay to the baseline, computes derived flags, returns a frozen `Context` consumed by signal modules and the scorer.

---

## Parallel read fan-out

```
asyncio.gather(
    baseline.load(conn, tenant_id, customer_id),     # FOR UPDATE on write txn
    enrich.enrich(conn, payload.source_ip),          # ip_enrichment cache or lazy refresh
    customers.load_or_upsert(conn, tenant_id, payload.customer),
    enterprises.load_or_upsert(conn, tenant_id, payload.enterprise) if payload.enterprise else None,
    velocity.count_user_hourly(conn, tenant_id, customer_id),
    velocity.count_user_daily(conn, tenant_id, customer_id),
    velocity.count_user_30d(conn, tenant_id, customer_id),
    velocity.count_ip_hourly(conn, tenant_id, source_ip),
    velocity.count_ip_daily(conn, tenant_id, source_ip),
)
```

All reads use the same connection (so they share the RLS session context). Velocity counts hit `shipments` with composite indexes leading by `tenant_id`.

---

## Post-fan-out

After parallel reads return:

1. `baseline.decay_to(today)` — lazy decay applied to every stat-dict, Welford triple, and histogram. Per-IP-type half-life for `ip_stats` entries; uniform 90 days for others.
2. `compute_trust_score(customer, baseline)` — pure-Python arithmetic; <1ms.
3. Derive booleans and numerics from baseline + enrichment (see "Computed fields" below).
4. HMAC PII fields from payload (`origin_email`, `origin_phone`, `destination_email`, `destination_phone`) via `signal_helpers.hmac_hex(value, hmac_secret)`.
5. Construct `Context` dict; freeze via `MappingProxyType` before passing to DSL evaluator.

---

## IP enrichment pipeline (`app/enrich.py`)

```python
class Enricher:
    def __init__(self, data_dir: Path) -> None:
        # Lazy-load MaxMind MMDBs, FireHOL netsets, IP2Proxy BIN, cloud CIDR tries
        ...

    async def enrich(self, conn: asyncpg.Connection, ip: IPv4Address) -> EnrichmentRow:
        # Cache check: SELECT * FROM ip_enrichment WHERE ip = $1
        # If hit AND updated_at > 14 days ago → stale → refresh
        # If miss → full enrichment + INSERT
        # Else → return cached row
        ...
```

### Sources (each lazy-loaded at `Enricher` init from `data/enrichment/`)

| Source | File(s) | Loader | Returns |
|---|---|---|---|
| MaxMind GeoLite2 City | `GeoLite2-City.mmdb` | `_mm_lookup` | country, region, city, lat, lon |
| MaxMind GeoLite2 ASN | `GeoLite2-ASN.mmdb` | `_mm_lookup` | asn_org |
| FireHOL Level 1 + Level 2 | `firehol_level1.netset`, `firehol_level2.netset` | `_firehol_match` (pytricia trie) | fh_level1, fh_level2, fh_lists |
| IP2Proxy LITE PX11 | `IP2PROXY-LITE-PX11.BIN` | `_ip2proxy_lookup` | is_proxy, is_vpn, is_tor, proxy_type, threat |
| Cloud CIDRs | `aws.cidr`, `gcp.cidr`, `azure.cidr`, `cloudflare.cidr` | `_cloud_match` (pytricia tries) + `_asn_cloud_match` (substring fallback) | is_cloud, cloud_provider |

### Merging logic

`enrich()` runs all sources sequentially (per-IP, not batch) since per-source cost is microseconds:

1. Validate IPv4 (`ipaddress.IPv4Address(ip)`).
2. MaxMind → country/region/city/lat/lon/asn_org.
3. FireHOL → fh_level1/fh_level2/fh_lists.
4. Cloud CIDR trie → is_cloud + cloud_provider; if no trie match, ASN-org substring fallback.
5. `signal_helpers.is_datacenter_asn(asn_org)` → is_datacenter.
6. IP2Proxy → is_proxy/is_vpn/is_tor/proxy_type/threat. `is_proxy` gated on non-sentinel `proxy_type` (per `.ai/decisions.md`).
7. Compose into `EnrichmentRow`; INSERT into `ip_enrichment` (ON CONFLICT UPDATE).

### Cache freshness

`updated_at` checked at lookup: if older than 14 days, row is stale and re-enriched on the next request. Operator may force-refresh via `scripts/fetch_enrichment.py --rebuild-ip <ip>`.

### Refresh script

`scripts/fetch_enrichment.py` downloads fresh source files into `data/enrichment/`:

- MaxMind: requires `FG_MAXMIND_LICENSE_KEY`. URL pattern in `.ai/decisions.md`.
- FireHOL: public Git raw URLs; no auth.
- IP2Proxy: requires `FG_IP2PROXY_DOWNLOAD_TOKEN`.
- Cloud CIDRs: public URLs per provider; AWS auto-fetches latest; Azure scrapes the dated JSON filename from the Microsoft download page.

Runs out-of-process: as ECS scheduled task in production (Phase 6), or local cron in dev. App reads from `data/enrichment/` (loader path configurable via `FG_ENRICHMENT_DATA_DIR`).

---

## Baseline load + decay (`app/baseline.py`)

```python
class CustomerBaseline:
    @classmethod
    async def load(
        cls,
        conn: asyncpg.Connection,
        tenant_id: int,
        customer_id: int,
        for_update: bool = False,
    ) -> "CustomerBaseline | None":
        # SELECT (FOR UPDATE if for_update) from customer_baselines
        # Hydrate JSONB columns into dataclass fields
        ...

    def decay_to(self, as_of: date) -> None:
        # For each stat-dict, Welford triple, histogram:
        #   factor = exp(-ln2 * delta_days / half_life)
        #   half_life = per-IP-type for ip_stats entries; 90d otherwise
        # Advance decay_anchor_date
        ...

    def add_observation(self, payload: BookingRequest, enrichment: EnrichmentRow) -> None:
        # Update stat-dicts: +1 to n, set last = today
        # Update Welford triples (value, cadence)
        # Update histograms (hour, weekday, channel, ip_type)
        # Set last_booking_* pointers
        ...

    async def save(self, conn: asyncpg.Connection) -> None:
        # UPDATE customer_baselines SET ... WHERE id = $1
        ...
```

Per the operator amendment, writes happen within the request's single transaction: `for_update=True` on load, then `add_observation`, then `save`, all before the transaction commits.

`add_rejected_observation` (feedback path, Phase 3) increments `r_n` instead of `n`.

---

## Velocity counters (`app/velocity.py`)

SQL-backed. No Redis.

```python
async def count_user_hourly(conn, tenant_id, customer_id) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM shipments "
        "WHERE tenant_id = $1 AND customer_id = $2 "
        "AND booking_ts > now() - interval '1 hour'",
        tenant_id, customer_id,
    )
```

Similar shape for `daily`, `30d`, `ip_hourly`, `ip_daily`. Composite indexes on `(tenant_id, customer_id, booking_ts)` and `(tenant_id, source_ip, booking_ts)` cover these.

`recipient_used_by_customer_count` (Phase 2) is a cross-customer query bounded by a destination HMAC index — added with the recipient-overlap rule in Phase 2.

---

## Trust score (`app/trust.py`)

```python
def compute_trust_score(customer: Customer, baseline: CustomerBaseline) -> float:
    age_days = (date.today() - customer.first_seen.date()).days
    effective_obs = baseline.value_n     # post-decay
    flagged = customer.flagged_count
    fraud_confirmed = customer.fraud_confirmed_count

    trust = (
        0.5
        + 0.3 * sigmoid((effective_obs - 20) / 10)
        + 0.2 * sigmoid((age_days - 60) / 30)
        - 0.4 * (1.0 if flagged > 0 else 0.0)
        - 0.6 * (1.0 if fraud_confirmed > 0 else 0.0)
    )
    return max(0.0, min(1.0, trust))
```

Per-request, sub-millisecond. Not persisted (computed on read). Phase 1 attaches to Context; Phase 2 trust-conditional rules consume.

---

## Computed Context fields

Derived in `build_context` after parallel reads complete. Full list in `.ai/rules.md` § DSL Context fields. Key derivations:

- `is_cloud_ip` = the strict cloud (CIDR / cloud-provider ASN) flag. `is_datacenter_ip` = the datacenter-ASN flag (eStruxture / Equinix / OVH / Hetzner / …) computed via `signal_helpers.is_datacenter_asn(asn_org)`. Rules can reference either independently.
- `is_new_ip` = source_ip not in `baseline.ip_stats`
- `ip_fully_new` = IP, /24, and ASN all absent from their respective baseline stat-dicts
- `ip_family_familiar` = /24 in `baseline.ip_netblock_stats` (per `.ai/decisions.md` § Tuned thresholds)
- `is_new_route` = `(payload.origin, payload.destination)` not in `baseline.lane_stats`
- `is_abnormally_dormant` = `cadence_zscore_hours > 6`
- `value_zscore` = `(payload.value - baseline.value_mean) / sqrt(baseline.value_m2 / baseline.value_n)` if `value_n > 0` and variance > 0, else 0
- `cadence_zscore_hours` = `(hours_since(baseline.last_booking_ts) - baseline.cadence_mean_h) / sqrt(baseline.cadence_m2_h / baseline.cadence_n)` if `cadence_n > 0`, else 0
- `customer_observations` = `baseline.value_n` (post-decay; the activity proxy)
- `customer_locked_cloud_api` (Phase 2) = computed from `cloud_share_n / total > 0.95` etc.

---

## Default behaviours

| Scenario | Effect |
|---|---|
| **New customer** (no baseline row) | `baseline = CustomerBaseline.empty(tenant_id, customer_id)` — all stat-dicts empty, Welford zeros, `effective_observations=0`. `is_new_ip / is_new_route / etc.` all True. |
| **Enterprise absent in payload** | `enterprise=None`; no enterprise-level lookups. |
| **Source IP not in `ip_enrichment` cache** | Lazy refresh; if upstream sources unavailable, serve `EnrichmentRow.empty(ip)` and proceed. Rules conditioned on enrichment booleans get `False`. |
| **IPv6 source_ip** | Out of scope in v1. Request is rejected at Pydantic validation. |
| **MaxMind ASN lookup returns no asn_org** | `asn_org=None`; `is_datacenter_ip=False`; cloud-CIDR check still runs. |
| **No `last_booking_ts`** | `cadence_zscore_hours=0`; `is_abnormally_dormant=False`. |
| **`value_n == 0`** | `value_zscore=0`; rules conditioned on value-novelty inert until the customer has observations. |
| **Missing `origin_email`** | `origin_email_hmac_known=False`; `is_email_disposable / blocklisted / suspicious_pattern` all `False`. |

---

## Wiring summary

```
POST /api/v1/shipments/booking/evaluate
  → require_api_token (sets RLS app.tenant_id)
  → BEGIN TRANSACTION
  → SELECT FOR UPDATE on customer_baselines (per-customer lock for this txn)
  → asyncio.gather(
        baseline.load,
        enrich.enrich,
        customers.load_or_upsert,
        enterprises.load_or_upsert (if present),
        velocity.count_user_hourly / daily / 30d,
        velocity.count_ip_hourly / daily,
    )
  → baseline.decay_to(today)
  → trust.compute_trust_score()
  → derive computed Context fields
  → HMAC PII from payload
  → freeze Context
  → run signal modules in parallel (each populates its Context fields)
  → score(context, rules)
  → INSERT shipments
  → INSERT decisions
  → baseline.add_observation + baseline.save
  → UPDATE customers (last_seen, total_shipments + 1)
  → COMMIT
  → return BookingResponse
```

Persistence failure returns 500; retry-safe via `(tenant_id, request_id)` idempotency.
