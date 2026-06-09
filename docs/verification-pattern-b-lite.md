# Verification — Pattern B-lite enrichment refresh

Verification facts for the `PLAN_PATTERN_B_LITE.md` planning phase.
Read-only discovery; no code, no decisions; raw observations only.

Date: 2026-06-08
Branch: `feat/refactor`
HEAD: `4377922` (`INFRA-CFN C8`)
Pre-pass test baseline: **1116 tests collected** (`pytest tests/ --collect-only -q`)

---

## V-1 — `scripts/fetch_enrichment.py` reuse

**Synchronous**, uses `urllib.request.urlretrieve` directly to dest path.

Per-source functions present: `_fetch_firehol`, `_fetch_cloud_cidrs`,
`_fetch_maxmind`, `_fetch_ip2proxy`. Each function constructs the URL
inline (including license-key in the MaxMind / IP2Proxy URLs).

Has: MaxMind tarball extraction via `tarfile.open(...).extract(member,
data_dir, filter="data")`.

Does NOT have: async support, atomic-replace (urlretrieve writes
directly to dest), retries, sanity floors, log scrubbing of license
keys, Azure handling, IP2Proxy archive extraction (saves response
body directly as `.BIN`), cloud-provider JSON → `.cidr` extraction
(it saves `aws.json` / `gcp.json` but `app/enrich.py` reads
`aws.cidr` / `gcp.cidr` — mismatch flagged in BUGS).

**Reuse verdict**: structural reference (URL patterns + tarball
extract pattern + license-key gating) but the new
`app/enrichment_refresh.py` is a clean async rewrite. The sync
script remains for ECS-scheduled-task / cron fallback (Pattern A
future option).

---

## V-2 — `app/enrich.py` re-init mechanism

`Enricher._loaded` is a single-shot sentinel set early in
`_load_sources()` to prevent re-attempts on partial failure.
Once `True`, `_load_sources()` is a no-op.

Per-source state attributes: `_mm_city`, `_mm_asn` (maxminddb Reader
instances — have `.close()`), `_ip2p` (IP2Proxy instance — has
`.close()`), `_firehol_l1`, `_firehol_l2`, `_cloud_tries`
(pytricia tries — no close needed, GC handles).

**Re-init options**:
- (a) Direct method: `Enricher.reload()` — closes existing handles,
  resets `_loaded=False`, calls `_load_sources()`. Refresh task
  holds the Enricher instance via `app.state.enricher`.
- (b) Sentinel invalidation: external setter `enricher._loaded =
  False`. Cheaper but leaks `_loaded` as public API and leaves
  old handles open until the next `_load_sources` call (which
  rebinds the attributes, dropping refs — closes happen via GC,
  not explicitly).
- (c) Module-level reload-flag: indirection without clear benefit.

**Recommendation**: (a). Explicit method with explicit handle
cleanup. Surfaces in Plan Decision D-Refresh-1.

---

## V-3 — `app/api/health.py` current shape

Returns `JSONResponse` directly (not Pydantic). Response shape:
- 200: `{"ok": True, "db": "ok", "pool": {...}}`
- 503: `{"ok": False, "db": "unreachable"}` (on TimeoutError /
  asyncpg / OSError)
- 500: programmer errors propagate (RuntimeError from `get_pool()`,
  etc.)

ALB target group probes `/health/` on HTTP:8000; the ALB only
removes a target from rotation on non-2xx. Adding `enrichment:
"degraded"` to the 200 payload does NOT affect ALB target health —
operator-observable via the response body / CloudWatch logs only.

To affect ALB rotation, degraded enrichment must surface as a 503.
Brief recommendation: 200 (degraded is observability; only pool
failure removes the target).

---

## V-4 — `app/main.py` lifespan

Current order (startup): `configure_logging` → `init_pool` →
`init_runtime(settings)` → bind `ruleset` + `enricher` to
`app.state` → yield.

Current order (shutdown): `await close_pool()` → log shutdown.

`app.state.enricher: Enricher` is the singleton refresh-task target.

For Pattern B-lite, refresh task spawns BEFORE yield (after the
runtime init), and must cancel BEFORE `close_pool()` so the
refresh logger has the pool available for its final-tick log if
it's mid-write. Concretely the shutdown order becomes:
`refresh_task.cancel()` → `await refresh_task` (swallow
`CancelledError`) → `await close_pool()`.

---

## V-5 — `app/observability.py METRIC_SPECS`

`METRIC_SPECS` is a `dict[str, MetricSpec]` at module scope.
`MetricSpec` carries `dimensions: tuple[str, ...]`, `metrics:
tuple[tuple[str, str], ...]`, `synthetic_count: bool`.

The processor emits a one-shot stderr warning for unknown
`metric=True` events not in `METRIC_SPECS`; adding the 3 new
entries (`enrich.refresh.success`, `enrich.refresh.failure`,
`enrich.refresh.skipped_sanity_floor`) avoids the warning.

No collision with the 21 existing keys (which include
`enrich.cache_hit` / `enrich.cache_miss` — namespace pattern
match).

---

## V-6 — Dockerfile system-tool audit

`python:3.13-slim` (Debian slim) runtime stage:
- `tar` — built-in via base-files (Debian slim includes tar)
- `gzip` — built-in
- `unzip` — NOT installed

**Mitigation**: Python stdlib `zipfile` handles ZIP archives in
pure Python (no system dep). Python stdlib `tarfile` handles
tar.gz (already used by `scripts/fetch_enrichment.py`).

**Verdict**: NO Dockerfile changes needed. The new
`app/enrichment_refresh.py` uses `tarfile` (MaxMind tar.gz) and
`zipfile` (IP2Proxy if the upstream response is a ZIP — confirmed
likely per IP2Proxy LITE distribution docs).

---

## V-7 — Env-var population

`app/config.py` Settings fields:
- `maxmind_license_key: str = ""` ← env `MAXMIND_LICENSE_KEY`
- `ip2proxy_download_token: str = ""` ← env `IP2PROXY_DOWNLOAD_TOKEN`
- `enrichment_data_dir: Path = Path("/app/data/enrichment")` ←
  env `ENRICHMENT_DATA_DIR`

`pydantic-settings` with `case_sensitive=False` — env-var names
map to lowercase field names.

CFN populates the secret containers (`MAXMIND_LICENSE_KEY` /
`IP2PROXY_DOWNLOAD_TOKEN`) and the ECS task definition wires them
as env vars (confirmed in `infra/ecs-task-definition.json` per
the CFN report Part 2 §"Pieces already provided").

Empty license-key → MaxMind / IP2Proxy downloaders skip with
WARNING + metric emit (matches the existing sync-script semantic
at `scripts/fetch_enrichment.py:64`).

---

## V-8 — Test fixture directory

`tests/fixtures/` exists: `__init__.py` + `payloads/` subdir.
The Pattern B-lite pass adds `tests/fixtures/enrichment_refresh/`
populated with small sanitized format-valid samples of each
upstream's response shape.

---

## V-9 — Sanity-floor calibration

Brief proposes (subject to operator confirm):

| Source | Floor | Observed typical |
|---|---|---|
| FireHOL level1 | 50 KB | 1-2 MB |
| FireHOL level2 | 50 KB | 3-5 MB |
| MaxMind City MMDB | 30 MB | ~70 MB |
| MaxMind ASN MMDB | 5 MB | ~10 MB |
| IP2Proxy LITE PX11 (raw ZIP) | 30 MB | ~82 MB (corrected 2026-06-09 — live observation per Amendment 2 F3) |
| IP2Proxy LITE PX11 (extracted BIN) | 500 MB | ~1.61 GB (corrected 2026-06-09 — the brief's "~50 MB" was off by ~32×) |
| AWS IP ranges (raw JSON) | 30 KB | ~150 KB |
| GCP IP ranges (raw JSON) | 5 KB | ~30 KB |
| Azure IP ranges (raw JSON) | 1 MB | ~5 MB |
| Cloudflare IP ranges (raw text) | 100 B | ~600 B |

Floor applies to the downloaded artifact AFTER extraction (for
MaxMind / IP2Proxy). Floors are ~30-50% of observed typical size
— conservative; protects against catastrophic-empty without
false-positive on minor week-to-week shrinkage.

---

## V-10 — Operator decision items (surface via AskUserQuestion)

| ID | Item | Recommendation |
|---|---|---|
| Q1 | Commit strategy (atomic vs batched) | atomic — one logical change per commit |
| Q2 | Refresh fault model (per-source independent vs sequential-abort) | per-source independent via `asyncio.gather(return_exceptions=True)` |
| Q3 | Health-probe degraded → ALB rotation (200 vs 503) | 200 — degraded is observability; ALB rotation only on pool failure |
| Q4 | Sanity-floor table (accept V-9 values vs alternate) | accept V-9 values |
| Q5 | Enricher re-init mechanism (reload method vs sentinel reset) | reload method (V-2 option a) |
| Q6 | `scripts/fetch_enrichment.py` future (keep for cron fallback / migrate / delete) | keep as-is for cron fallback |
| Q7 | Doc destination (update existing `.ai/enrichment.md` vs new `.ai/enrichment-refresh.md`) | update existing in-place |

Surfaced to operator via AskUserQuestion (consolidated into 4
batched questions per tool limit).

---

## Tangential issues logged to BUGS.md

- `scripts/fetch_enrichment.py:58` saves cloud-provider JSON as
  `aws.json` / `gcp.json`, but `app/enrich.py:163` reads
  `{provider}.cidr`. The sync script's output filenames don't
  match the loader's expected filenames for AWS/GCP. Either the
  sync script is dead code or it has never been run end-to-end
  with the loader. **Not a Pattern B-lite blocker** (the new
  async module writes the correct `.cidr` extension after
  parsing IPv4 prefixes from JSON), but the sync script should
  be reconciled or marked deprecated post-pass.

(Will append to `.claude/BUGS.md` in Phase 3 C0 prep if not
already drained at phase boundary.)

---

## Acceptance-criterion baseline lock

- Pre-pass test count: **1116** (locked here for regression check)
- Post-pass count must be **1116 + N** where N = new tests added
  by C0-C5
- Pre-pass HEAD: `4377922`
- Branch: `feat/refactor`
