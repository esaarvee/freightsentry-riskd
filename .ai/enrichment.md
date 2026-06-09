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

### Refresh module

`app/enrichment_refresh.py` (Pattern B-lite, landed 2026-06-09 across PBL C0–C6) runs an in-process async refresh task spawned by the FastAPI lifespan. Each tick:

1. Concurrently fetches all 9 sources via `httpx.AsyncClient` with bounded retries + jittered exponential backoff:
   - FireHOL Level 1 + Level 2 (`raw.githubusercontent.com`, no auth)
   - MaxMind GeoLite2 City + ASN (`download.maxmind.com`, keyed on `MAXMIND_LICENSE_KEY`)
   - IP2Proxy LITE PX11 (`www.ip2location.com`, keyed on `IP2PROXY_DOWNLOAD_TOKEN`; 5/24h per-token quota)
   - AWS / GCP / Azure / Cloudflare cloud-CIDR feeds (public URLs; Azure scrapes the dated JSON filename from `microsoft.com/en-us/download/details.aspx?id=56519`)

2. Each downloader applies **two-stage sanity floors**: a raw-bytes floor on the upstream response (catches HTTP 503 / rate-limit / login-redirect bodies — 30 MB minimum for IP2Proxy ZIP, ~50 KB to ~30 MB depending on source), then a post-parse floor on the extracted artifact (catches "JSON parses but the IPv4 prefix list is empty"; 5 KB to 500 MB depending on source). A skipped floor surfaces as `RefreshResult{status="skipped_sanity_floor"}` and preserves the existing on-disk artifact.

3. Each downloader writes via `atomic_replace` (bytes-form, for sources < 100 MB) or `atomic_replace_stream` (1 MiB chunk streaming, for IP2Proxy's 1.6 GB extracted BIN — never loads the full BIN into a Python `bytes` object). The tempfile lives in the same directory as the target so `os.rename` is atomic on POSIX.

4. IP2Proxy uses **magic-byte detection** before extraction: `PK\x03\x04` → ZIP path (extract via `zipfile.ZipFile.open("IP2PROXY-LITE-PX11.BIN")`), `\x1f\x8b` → tar.gz fallback, `<!`/`<?`/`<h` → `failure_class="upstream_html"` (token rejected, redirected to login), `THIS FILE CAN ONLY BE DOWNLOADED` prefix → `failure_class="rate_limited"`.

5. On ≥1 successful download per tick, the loop builds a new `Enricher(data_dir)` instance, eagerly loads its sources via `_load_sources()` on a worker thread (MaxMind / IP2Proxy C-extensions open file handles), and atomically swaps `app.state.enricher = new_enricher`. In-flight `enrich()` callers stay on the OLD instance until they finish; refcount → 0 then closes the prior MaxMind / IP2Proxy handles via their C-extension finalizers. No locks; no segfault risk; no enrich-path latency cost.

6. `/health/` exposes an `enrichment: "ok" | "degraded"` field. `"ok"` iff every source has either successfully refreshed at least once OR was present on disk at startup (hybrid Pattern A defense). Degraded does NOT change the HTTP status code — the ALB target stays in rotation while sources warm up.

7. Observability: every per-source outcome emits a CloudWatch EMF metric (`enrich.refresh.success` with `duration_ms` + `bytes_written`; `enrich.refresh.failure` with `failure_class` dimension across `network` / `parse_error` / `rate_limited` / `upstream_html` / `other`; `enrich.refresh.skipped_sanity_floor` with `bytes_attempted` + `floor_bytes`). License keys never appear in log fields, metric dimensions, exception messages, or RefreshResult fields — sentinel-string tests pin the invariant.

**Refresh cadence**: 24h between ticks. IP2Location's 5/24h per-token quota gives 4 slots/day of headroom for operator-side ad-hoc probes.

**Disk budget**: `ENRICHMENT_DATA_DIR` needs ≥3.5 GiB free (IP2Proxy LITE BIN is ~1.6 GiB; atomic-replace tempfile peaks at 2× that during the swap). ECS Fargate ephemeral storage default 20 GiB is comfortable.

**Cancellation**: lifespan shutdown cancels the refresh task; the task swallows `CancelledError`, cleans any `*.tmp.*` orphans in `ENRICHMENT_DATA_DIR`, and re-raises. The pool stays open until after the refresh task is fully drained so the task's final log calls have a working context.

**Out-of-process fallback**: `scripts/fetch_enrichment.py` is retained as a manual / cron-driven option (synchronous `urllib`-based; predates Pattern B-lite). Known issues: it saves AWS/GCP JSON without parsing to the `.cidr` form the Enricher reads, and saves IP2Proxy as `.BIN` without ZIP-extracting. See `.claude/BUGS.md` for the reconciliation note. The in-process refresh module is the recommended path.

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

**ALLOW-only baseline accumulation gate**: `baseline.add_observation` is invoked only when `result.decision == "ALLOW"` (`app/api/booking.py:207`). REVIEW/BLOCK bookings are HELD in pending state — no per-customer baseline mutation (ip/netblock/asn stats, value/cadence Welford accumulators, `last_booking_*`, channel histograms, country/origin/dest/lane stats, email/phone HMACs are all untouched). When operator feedback later marks a held booking as `approved`, the feedback endpoint folds the deferred observation then (see `app/api/feedback.py`). This gating makes the baseline a record of operator-confirmed legitimate behavior, not a record of all evaluated bookings — rationale in [`.ai/decisions.md` § Customer baseline](./decisions.md#customer-baseline).

Side effect: velocity counts (SQL-based on the `shipments` table) are UNAFFECTED — those still count REVIEW/BLOCK bookings. Only per-customer baseline state is gated.

`add_rejected_observation` (feedback path) increments `r_n` instead of `n`. Used by the feedback endpoint to fold operator-confirmed rejections into the `rejected_*` stat-dicts that power the previously-rejected rule family.

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
  → if result.decision == "ALLOW":
        baseline.add_observation + baseline.save
  → UPDATE customers (last_seen, total_shipments + 1)
  → COMMIT
  → return BookingResponse
```

Persistence failure returns 500; retry-safe via `(tenant_id, request_id)` idempotency.
