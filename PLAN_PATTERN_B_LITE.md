# PLAN — Pattern B-lite enrichment refresh module

Resolution plan for the Pattern B-lite launch blocker identified in
`REPORT_INFRA_CFN.md` Part 2 and constrained by the prompt brief.

Date: 2026-06-08
Branch: `feat/refactor`
Pre-pass HEAD: `4377922`
Pre-pass test count: **1116** (regression baseline)
Verification doc: [`docs/verification-pattern-b-lite.md`](docs/verification-pattern-b-lite.md)

---

## Amendment 2 — F3 resolution + IP2Proxy calibration corrections (2026-06-09)

Operator-side live verification via `/Users/drshott/PX11.zip` (fresh
upstream fetch outside the quota-exhausted F3 probe path) resolved
F3 and surfaced two calibration corrections.

| Item | Resolution |
|---|---|
| F3 archive format | **ZIP** confirmed (`PK\x03\x04` magic; deflate compression). Inner members: `README_LITE.TXT` (1.2 KB), `LICENSE_LITE.TXT` (1.2 KB), `IP2PROXY-LITE-PX11.BIN` (1.61 GB). C1 `refresh_ip2proxy` opens via `zipfile.ZipFile.open("IP2PROXY-LITE-PX11.BIN")` and streams to tempfile. Defensive magic-byte detection still added per the F3-Option-2 fallback in case upstream format ever drifts. |
| Calibration: IP2Proxy size | Brief / V-9 stated `~50 MB extracted`. Actual is **1.61 GB**. `_SANITY_FLOORS["ip2proxy"]` raw-ZIP floor raised to **30 MB** (was 20 MB). New `_EXTRACTED_FLOORS["ip2proxy_extracted"] = 500_000_000` (500 MB) defends against partial extract / mid-zip truncation. |
| In-memory hazard | The original `atomic_replace(target, content: bytes)` would load 1.5 GiB into a Python `bytes` object → OOM risk on small Fargate sizings. New `atomic_replace_stream(target, src: IO[bytes])` streams from `zipfile.ZipFile.open()` member fileobj in 1 MiB chunks. The 8 non-IP2Proxy sources stay on bytes-form `atomic_replace`. |
| Disk-budget note | IP2Proxy extract path uses ~3.2 GiB peak in `ENRICHMENT_DATA_DIR` during atomic swap (1.6 GiB target file + 1.6 GiB tempfile). ECS Fargate ephemeral storage default 20 GiB is comfortable. C6 adds a note to `.ai/enrichment.md` and the runbook. |
| Rate-limit semantics | IP2Location enforces 5 downloads / 24h per token (observed in F3 probe). Refresh cadence 1×/24h leaves 4 slots/day margin. C1 adds `rate_limited` failure_class with prefix-match detection on `b"THIS FILE CAN ONLY BE DOWNLOADED"` (the upstream throttle-response body). |

Amendment 1 F3 row updated below from PENDING → RESOLVED (Amendment 2).

---

## Amendment 1 — Critical findings from plan review (2026-06-08)

Five critical findings surfaced by operator review of the initial
plan draft. Each finding's verification work and proposed resolutions
were captured in the amendment prompt. Resolutions below are
operator-approved via AskUserQuestion on 2026-06-08; F3 remains
pending operator-side `curl` verification before C0 begins.

| # | Finding | Resolution | Approval |
|---|---|---|---|
| F1 | Module-level `asyncio.Lock()` event-loop binding hazard. Verification: `_loaded_sources` is single-writer (refresh task) multi-reader (health probe); `set.add` and `set.__contains__` are GIL-atomic. | **Drop the lock entirely.** Supersedes the `_loaded_sources_lock` line in §Module structure. Worst-case "transient partial-marked" read by health probe is correct degraded behavior. | 2026-06-08 |
| F2 | `Enricher.reload()` segfault risk: concurrent `enricher.enrich()` calls (from `app/context.py:227` + `app/api/feedback.py:398`) while reload closes MaxMind/IP2Proxy C-extension handles. | **Copy-on-write swap of `app.state.enricher`.** Supersedes D-2 entirely. Refresh task builds a NEW `Enricher`, calls `new._load_sources()`, atomically swaps `app.state.enricher = new`. In-flight requests finish on old instance; refcount → 0 triggers handle close via `__del__`. `Enricher.reload()` removed from scope. | 2026-06-08 |
| F3 | IP2Proxy LITE archive format (ZIP vs direct BIN). | **RESOLVED (Amendment 2)** — ZIP format with inner `IP2PROXY-LITE-PX11.BIN` member. Streaming extraction via `zipfile.ZipFile.open()` + new `atomic_replace_stream` helper. Defensive magic-byte detection + `rate_limited` failure_class retained. | 2026-06-09 |
| F4 | AWS/GCP/Azure: raw-JSON sanity floor doesn't catch "parse yields empty extracted CIDR list" → silent `is_cloud=False` degradation. | **Two-stage floor for AWS/GCP/Azure**: raw-byte floor before parse + extracted-byte floor after parse. New `_EXTRACTED_FLOORS` table for these 3 sources. | 2026-06-08 |
| F5 | C2 lifespan integration test pattern: project has NO existing `TestClient`/`LifespanManager` usage; conftest deliberately skips lifespan via `ASGITransport`. | **Direct `async with lifespan(app):` invocation in the C2 test.** No new dev dep; no new project test convention; `lifespan` is an `@asynccontextmanager` so this is the canonical Python pattern. | 2026-06-08 |

Plan body below incorporates F1, F2, F4, F5. F3 placeholder marked
where format-dependent text lives. **Per the amendment workflow,
C0 does not commit until F3 is resolved.**

---

## Decisions absorbed

Resolves V-1 through V-10 of the verification phase + operator
AskUserQuestion answers from end of Phase 2.

| ID | Decision | Source |
|---|---|---|
| D-1 | `scripts/fetch_enrichment.py` is NOT lifted into the async module — clean rewrite. Sync script remains for cron / ECS-scheduled-task fallback (Pattern A future option). | V-1 |
| D-2 | **SUPERSEDED BY AMENDMENT 1 / F2.** Original: Enricher.reload() method. Replacement: copy-on-write swap. Refresh task builds new `Enricher(data_dir)`, calls `new._load_sources()`, then atomically swaps `app.state.enricher = new`. Concurrent `enrich()` callers hold a reference to the OLD Enricher and finish safely on it; refcount drops to 0 when last in-flight call returns and MaxMind/IP2Proxy `__del__` closes the handles. No locks; no segfault risk; no Enricher API change. | V-2 + Q3 + Amendment 1 F2 |
| D-3 | Health probe extension: 200 response payload gains an `enrichment: "ok" \| "degraded"` field. **Degraded does NOT return 503** — ALB target health unaffected; 503 remains pool-failure-only. | V-3 + Q2 (operator confirmed) |
| D-4 | Lifespan shutdown order: `refresh_task.cancel()` → `await refresh_task` (swallow `CancelledError`) → `await close_pool()`. Refresh task spawn is AFTER `init_runtime` (needs `app.state.enricher`). | V-4 |
| D-5 | 3 new METRIC_SPECS entries: `enrich.refresh.success` (dim: source_name; metrics: duration_ms, bytes_written, count), `enrich.refresh.failure` (dim: source_name, failure_class; metric: count), `enrich.refresh.skipped_sanity_floor` (dim: source_name; metrics: bytes_attempted, floor_bytes, count). | V-5 |
| D-6 | NO Dockerfile changes. Python stdlib `tarfile` (MaxMind tar.gz) + stdlib `zipfile` (IP2Proxy ZIP if present) cover archive extraction in pure Python. | V-6 |
| D-7 | Settings fields already in place (`maxmind_license_key`, `ip2proxy_download_token`, `enrichment_data_dir`); refresh module reads via `get_settings()`. | V-7 |
| D-8 | Test fixtures live under `tests/fixtures/enrichment_refresh/`. Sanitized format-valid samples; no real upstream data. | V-8 |
| D-9 | Sanity-floor values per V-9 table accepted as initial conservative thresholds (~30-50% of observed typical). Tunable post-launch via module constants. | V-9 + Q4 (operator confirmed) |
| D-10 | Refresh fault model: per-source independent via `asyncio.gather(*tasks, return_exceptions=True)`. One source failing does NOT block others. | brief §Constraints #2 |
| D-11 | Refresh cadence: startup + every 24h. Cancel-and-await on lifespan shutdown. | brief §Required deliverables #1 + #3 |
| D-12 | Retry policy: bounded retries max 3, jittered exponential backoff starting at 2s (2s, 4s, 8s with ±25% jitter). On exhaustion → log + metric + RefreshResult{status=failed}; the loop tick continues. | brief §Constraints #6 |
| D-13 | Atomic file replacement: tempfile in SAME directory as target (`{target.name}.tmp.{uuid4().hex[:8]}`), `fsync` tempfile, `os.rename(tempfile, target)`. Cross-filesystem rename is not atomic on POSIX — tempfile must share the target's filesystem. | brief §Constraints #3 |
| D-14 | License-key sanitization: URL construction confines the secret to a local variable; logger/metric emits use `source_name` only; exception messages from `httpx` strip query strings before logging. Test with sentinel key string + grep-the-test-output. | brief §Constraints #5 |
| D-15 | `.ai/enrichment.md` updated in place (refresh-script section) — not a new file. Existing doc structure absorbs the Pattern B-lite narrative cleanly. | brief §S-10 + verification |
| D-16 | Commit strategy: ATOMIC. 7 commits, one logical change per commit per the brief's suggested breakdown. | Q1 (operator confirmed) |
| D-17 | Launch-blocker banner removal happens ONLY in the FINAL commit (C6), AFTER C0-C5 are all reviewer-approved at cleanest verdict (SHIP IT / LOW RISK / CLEAN / ACTUALLY GOOD). | brief §Constraints #11 |
| D-18 | Reviewer panel per commit: senior + security + code-flow + test-reviewer on every code-path commit (tests change every commit). doc-reviewer on C6. db-reviewer NOT needed (no schema changes). | brief §Constraints #10 |

---

## Resource map

### Files created

| Path | Purpose |
|---|---|
| `app/enrichment_refresh.py` | Async refresh module: per-source downloaders, atomic-replace, sanity floors, retry+backoff, refresh loop. |
| `tests/unit/test_enrichment_refresh.py` | Unit tests: mocked downloaders, atomic-replace, sanity floors, retry, license-key sanitization, refresh-loop orchestration. |
| `tests/integration/test_enrichment_refresh_e2e.py` | Integration: end-to-end refresh against recorded fixtures + lifespan startup integration. |
| `tests/fixtures/enrichment_refresh/__init__.py` | Marker. |
| `tests/fixtures/enrichment_refresh/firehol_level1.netset` | ~5KB sanitized FireHOL L1 sample (CIDR list with header comments). |
| `tests/fixtures/enrichment_refresh/firehol_level2.netset` | ~5KB sanitized FireHOL L2 sample. |
| `tests/fixtures/enrichment_refresh/GeoLite2-City.tar.gz` | ~10KB minimal valid MaxMind tar.gz containing a tiny City MMDB (uses `maxminddb` test-data shape; not real MaxMind data). |
| `tests/fixtures/enrichment_refresh/GeoLite2-ASN.tar.gz` | ~5KB minimal valid MaxMind tar.gz for ASN MMDB. |
| `tests/fixtures/enrichment_refresh/IP2PROXY-LITE-PX11.zip` | ~5KB synthetic ZIP (deflate) containing 3 members: tiny README + LICENSE stubs + a placeholder `IP2PROXY-LITE-PX11.BIN` with the IP2Proxy magic-header bytes followed by ~3 KB of padding. Format locked per Amendment 2 F3 verification. Enricher-side IP2Proxy.open() is NOT called against this fixture — refresh tests verify extract + atomic-replace; Enricher tests use existing mock pattern. |
| `tests/fixtures/enrichment_refresh/ip-ranges.json` | ~2KB AWS ip-ranges JSON sample. |
| `tests/fixtures/enrichment_refresh/cloud.json` | ~1KB GCP cloud.json sample. |
| `tests/fixtures/enrichment_refresh/azure-service-tags.json` | ~3KB Azure service-tags JSON sample. |
| `tests/fixtures/enrichment_refresh/ips-v4.txt` | ~500B Cloudflare CIDR list. |

### Files modified

| Path | Change |
|---|---|
| `app/enrich.py` | **NO CHANGES** (per Amendment 1 F2 — CoW swap of `app.state.enricher` removes need for `Enricher.reload()`). |
| `app/api/health.py` | Adds `enrichment` field to 200 response payload per D-3. |
| `app/main.py` | Spawns refresh task in lifespan; cancel-and-await on shutdown per D-4. The refresh task receives a reference to `app` so it can swap `app.state.enricher` after each successful per-source refresh. |
| `app/observability.py` | 3 new METRIC_SPECS entries per D-5. |
| `.ai/enrichment.md` | Pattern B-lite narrative replaces the §Refresh script paragraph per D-15. |
| `infra/cloudformation/README.md` | Launch-blocker banner promoted to historical note in C6. |
| `docs/aws-deploy-runbook.md` | Launch-blocker banner promoted to historical note in C6. |

### Files NOT touched

- `scripts/fetch_enrichment.py` — left as-is (cron fallback). BUGS.md
  entry from V-1 surfaces the stale `.json` vs `.cidr` filename
  mismatch for separate triage post-pass.
- `infra/cloudformation/freightsentry-riskd.yml` — no infra changes.
- `infra/ecs-task-definition.json` — already wires the two license
  secrets and `ENRICHMENT_DATA_DIR`.
- `alembic/versions/*` — no schema changes (uses existing
  `ip_enrichment` table read-side only).
- `pyproject.toml` — httpx already present; no new deps.
- `Dockerfile` — no changes per D-6.

---

## Sanity-floor table (D-9 + Amendment 1 F4)

Two-stage table per Amendment 1 F4. Raw floor applies to bytes
received from the upstream BEFORE any extraction; extracted floor
applies AFTER parse/extract and BEFORE `atomic_replace` writes the
on-disk artifact the Enricher loads.

Constants live in `app/enrichment_refresh.py` as module-level:
- `_SANITY_FLOORS: Final[dict[str, int]]` — raw download floors
- `_EXTRACTED_FLOORS: Final[dict[str, int]]` — post-extract floors
  (only the 3 sources where raw bytes and on-disk artifact diverge:
  AWS/GCP/Azure go JSON → CIDR list. MaxMind goes tar.gz → MMDB
  but the existing `maxmind_*` raw floors are already calibrated
  to the tar.gz size; extracted-MMDB floor is implicit — if tar.gz
  passes 30 MB, the MMDB inside is guaranteed >0 bytes and any
  partial-extract failure surfaces as a tarfile exception caught
  in the parse_error retry path. IP2Proxy similarly: ZIP/BIN
  extraction failure surfaces as a parse_error.)

### Raw download floors

| Source | Floor (bytes) | Floor (human) | Observed typical |
|---|---|---|---|
| `firehol_level1` | 50_000 | 50 KB | 1-2 MB |
| `firehol_level2` | 50_000 | 50 KB | 3-5 MB |
| `maxmind_city` | 30_000_000 | 30 MB | ~70 MB (tar.gz) |
| `maxmind_asn` | 5_000_000 | 5 MB | ~10 MB (tar.gz) |
| `ip2proxy` | 30_000_000 | 30 MB | ~82 MB ZIP, extracted BIN ~1.61 GB (observed 2026-06-09 per Amendment 2 F3) |
| `aws` | 30_000 | 30 KB | ~150 KB (raw JSON) |
| `gcp` | 5_000 | 5 KB | ~30 KB (raw JSON) |
| `azure` | 1_000_000 | 1 MB | ~5 MB (raw JSON) |
| `cloudflare` | 100 | 100 B | ~600 B |

### Extracted artifact floors (AWS/GCP/Azure + IP2Proxy)

Applied AFTER parse/extract and BEFORE the on-disk artifact is
`atomic_replace`d. For AWS/GCP/Azure defends against "raw JSON
looks legitimate but parse yields empty CIDR list" (silent
`is_cloud=False` degradation per V-3 Enricher behavior on empty
trie). For IP2Proxy defends against "ZIP downloaded but member
extract yielded a truncated BIN" (per Amendment 2 F3 — extracted
BIN is 1.6 GB so partial extract is a real risk).

| Source (extracted) | Floor (bytes) | Floor (human) | Rationale |
|---|---|---|---|
| `aws_extracted` | 5_000 | 5 KB | AWS publishes ~700 IPv4 prefixes; typical extracted is ~15-25 KB. 5 KB is ~250 prefixes minimum. |
| `gcp_extracted` | 1_000 | 1 KB | GCP publishes ~150 IPv4 prefixes; typical extracted is ~3-5 KB. 1 KB is ~50 prefixes minimum. |
| `azure_extracted` | 50_000 | 50 KB | Azure publishes ~2000-3000 IPv4 prefixes across all service tags; typical extracted is ~80-150 KB. 50 KB is ~1500 prefixes minimum. |
| `ip2proxy_extracted` | 500_000_000 | 500 MB | IP2Proxy LITE PX11 BIN observed 1.61 GB on 2026-06-09 (Amendment 2 F3). 500 MB defends against partial extract / mid-ZIP truncation; well below typical so legitimate week-to-week shrinkage tolerated. |

Skipping the extracted-floor check emits the same metric event
(`enrich.refresh.skipped_sanity_floor`) with `source_name` ending
in `_extracted` to distinguish it from the raw-stage skip.

---

## Module structure — `app/enrichment_refresh.py`

```python
# Module surface (logical layout; not literal stub)

@dataclass(frozen=True)
class RefreshResult:
    source_name: str
    status: Literal["success", "failed", "skipped_sanity_floor"]
    bytes_written: int | None
    error: str | None  # str-form to avoid pickling Exception refs in logs
    duration_ms: float

_SOURCE_URLS: Final[dict[str, str]] = {...}             # public URLs
_SANITY_FLOORS: Final[dict[str, int]] = {...}           # raw-download floors per D-9
_EXTRACTED_FLOORS: Final[dict[str, int]] = {...}        # post-parse floors (AWS/GCP/Azure) per Amendment 1 F4
_REFRESH_INTERVAL_SECONDS: Final[int] = 24 * 60 * 60
_RETRY_MAX_ATTEMPTS: Final[int] = 3
_RETRY_BASE_DELAY_SECONDS: Final[float] = 2.0

# Module-level state for health probe (per D-3). No lock per Amendment 1 F1:
# `_loaded_sources` is single-writer (refresh task's mark_source_loaded) and
# multi-reader (health probe); set.add/__contains__ are GIL-atomic.
_loaded_sources: set[str] = set()

def all_sources_loaded_at_least_once() -> bool: ...
def mark_source_loaded(source_name: str) -> None: ...
def seed_loaded_from_disk(data_dir: Path) -> None: ...  # hybrid Pattern A baked-image defense

async def atomic_replace(target: Path, content: bytes) -> int:
    """Bytes-form atomic replace. For sources whose extracted size is
    safely under 100 MB (the 8 non-IP2Proxy sources). Writes content to
    tempfile, fsync, rename."""

async def atomic_replace_stream(
    target: Path, src: IO[bytes], *, chunk_size: int = 1 << 20
) -> int:
    """Streaming atomic replace. For IP2Proxy (1.5 GiB extracted BIN
    per Amendment 2 F3). Copies from src to tempfile in chunk_size
    blocks (default 1 MiB), fsync, rename. Avoids loading the full
    file into memory as a Python bytes object."""

async def _http_get_with_retries(
    client: httpx.AsyncClient, url: str, *, source_name: str
) -> bytes: ...  # license-key URL constructed by caller; never logged

async def refresh_firehol_level1(...) -> RefreshResult: ...
async def refresh_firehol_level2(...) -> RefreshResult: ...
async def refresh_maxmind_city(...) -> RefreshResult: ...
async def refresh_maxmind_asn(...) -> RefreshResult: ...
async def refresh_ip2proxy(...) -> RefreshResult:
    """Per Amendment 2 F3:
      1. _http_get_with_retries → response bytes
      2. raw sanity floor check (_SANITY_FLOORS["ip2proxy"], 30 MB)
      3. rate-limit prefix check (b"THIS FILE CAN ONLY BE DOWNLOADED")
         → failure_class="rate_limited"
      4. magic-byte detection:
           PK\\x03\\x04 → ZIP path (expected)
           \\x1f\\x8b   → tar.gz fallback (defensive)
           <! / <? / <h → upstream_html (token-rejected / login redirect)
           else         → direct BIN (fallback; atomic_replace bytes)
      5. ZIP path: zipfile.ZipFile.open("IP2PROXY-LITE-PX11.BIN") as member;
         stream to tempfile via atomic_replace_stream;
         apply _EXTRACTED_FLOORS["ip2proxy_extracted"] (500 MB) on the
         resulting target file (stat after fsync, before rename); skip
         + cleanup tempfile if below floor."""
async def refresh_aws(...) -> RefreshResult: ...    # applies _EXTRACTED_FLOORS["aws_extracted"]
async def refresh_gcp(...) -> RefreshResult: ...    # applies _EXTRACTED_FLOORS["gcp_extracted"]
async def refresh_azure(...) -> RefreshResult: ...  # applies _EXTRACTED_FLOORS["azure_extracted"]
async def refresh_cloudflare(...) -> RefreshResult: ...

# Per Amendment 1 F2: refresh_loop receives `app` (FastAPI) and on each
# successful per-source refresh builds a NEW Enricher, calls
# new._load_sources(), then atomically swaps app.state.enricher = new.
# No Enricher.reload() method; no handle close (Python __del__ on GC
# closes MaxMind/IP2Proxy handles when last in-flight enrich() releases
# the old instance).
async def refresh_all_once(
    data_dir: Path, settings: Settings, app: FastAPI
) -> list[RefreshResult]: ...

async def refresh_loop(
    data_dir: Path, settings: Settings, app: FastAPI
) -> None: ...  # forever; cancelled by lifespan
```

Total 9 source downloaders (FireHOL L1, L2, MaxMind City, MaxMind
ASN, IP2Proxy, AWS, GCP, Azure, Cloudflare). The brief mentioned
"8 sources" — actually 9 once enumerated; locked here.

---

## Commit sequence

### C0 — Test fixtures (sanitized samples)

Adds `tests/fixtures/enrichment_refresh/` with the 9 sanitized
samples enumerated in §Resource map. No production code; no
consuming tests yet.

**Changes**: 9 fixture files + `__init__.py`. Total <50 KB across
all fixtures. License-key-bearing URLs do NOT appear in fixtures.

**Tests**: None added (fixtures pre-populate for C5).

**Validation**:
- `pytest tests/` collects 1116 tests (unchanged).
- Manual: `find tests/fixtures/enrichment_refresh -type f -exec
  stat -f '%z %N' {} \;` confirms each fixture < 50 KB.
- Manual: `python -c "import tarfile;
  tarfile.open('tests/fixtures/enrichment_refresh/GeoLite2-City.tar.gz').list()"`
  exits 0.
- Manual (format locked ZIP per Amendment 2 F3):
  `python -c "import zipfile; z=zipfile.ZipFile('tests/fixtures/enrichment_refresh/IP2PROXY-LITE-PX11.zip'); names=z.namelist(); assert 'IP2PROXY-LITE-PX11.BIN' in names, names"`
  exits 0.

**Reviewer panel**: senior + security (license-data sanitization
review) + code-flow + test-reviewer.

**Declared breaks**: none. (Fixtures are inert until C5 consumes.)

---

### C1 — Module skeleton + per-source downloaders + atomic-replace + sanity floors + retry

Single commit per brief's "tightly coupled" framing in §S-1, S-2,
S-3, S-4.

**Changes**:
- New `app/enrichment_refresh.py` with: `RefreshResult` dataclass,
  9 downloader functions, `atomic_replace` (bytes form) + new
  `atomic_replace_stream` (streaming form for IP2Proxy per
  Amendment 2 F3), two-stage sanity-floor tables (`_SANITY_FLOORS`
  + `_EXTRACTED_FLOORS` per Amendment 1 F4 + Amendment 2 ip2proxy
  recalibration), `_http_get_with_retries` with jittered
  exponential backoff, per-source URL constants.
  AWS/GCP/Azure downloaders apply both raw and extracted floors.
  IP2Proxy downloader implements the full magic-byte detection
  branch ladder + `rate_limited` / `upstream_html` failure_class
  paths per Amendment 2 F3. The refresh-loop function defers to
  C2.
- `app/enrich.py` is **NOT modified** in C1 (per Amendment 1 F2 —
  CoW swap of `app.state.enricher` removes need for
  `Enricher.reload()`). Enricher remains as-is.
- Module-level `_loaded_sources: set[str] = set()` is unlocked
  per Amendment 1 F1 (GIL-atomic single-writer pattern).

**Tests** (`tests/unit/test_enrichment_refresh.py`):
- Per-downloader (9 functions × 6 scenarios each = ~50 tests):
  success-200, sanity-floor-too-small, network-failure
  (httpx.ConnectError), 4xx response, 5xx response, parse-error
  (corrupt archive). Mocked via `httpx.MockTransport` per the
  test-reviewer's preferred pattern.
- AWS/GCP/Azure (3 × 1 extra each = 3 tests): raw download passes
  but post-parse extracted bytes < `_EXTRACTED_FLOORS[..._extracted]`
  → RefreshResult{status=skipped_sanity_floor, source_name ends
  in "_extracted"}; existing `.cidr` file is NOT overwritten.
- IP2Proxy branch ladder (Amendment 2 F3, ~6 extra tests):
  (a) ZIP happy path: fixture ZIP → extract `IP2PROXY-LITE-PX11.BIN`
      member → `atomic_replace_stream` writes target → RefreshResult
      success (use fixture-sized floor override for the test since
      the synthetic ZIP is ~5 KB, not 1.6 GB).
  (b) Magic-byte = `\x1f\x8b` → tar.gz fallback path exercised.
  (c) Magic-byte = `<!` → failure_class="upstream_html".
  (d) Body prefix `b"THIS FILE CAN ONLY BE DOWNLOADED"` → failure_class="rate_limited".
  (e) Raw response < 30 MB floor → skipped_sanity_floor("ip2proxy").
  (f) Extracted BIN < 500 MB extracted floor (override for test)
      → skipped_sanity_floor("ip2proxy_extracted").
- `atomic_replace_stream` (3 tests): chunk-size streaming write,
  fsync, rename; tempfile cleanup on simulated write error;
  large-fileobj path doesn't load full content into memory
  (sentinel via `tracemalloc` or memory cap fixture).
- `atomic_replace`: success path leaves no tempfile in target dir,
  rename succeeds; simulated write-fsync-rename failure preserves
  original target file and cleans tempfile.
- Retry-with-backoff: 2 failures then success returns successful
  RefreshResult; 3 failures returns failed RefreshResult; jitter
  bounds verified within ±25%.
- License-key sanitization: sentinel key `"SENTINEL-KEY-DO-NOT-LOG"`
  in MAXMIND_LICENSE_KEY / IP2PROXY_DOWNLOAD_TOKEN env, all log
  records captured via `caplog`, assert sentinel string never
  appears in any log message, exception message, or metric event
  dimension/value.
- (Amendment 1 F2: no `Enricher.reload()` test. Swap-pattern test
  lives in C2 where the refresh loop performs the actual swap.)

**Validation**:
- `pytest tests/unit/test_enrichment_refresh.py -v` — all new
  tests pass.
- `pytest tests/` total = 1116 + N (N ~50-60).
- `ruff check app/ tests/` clean.
- `mypy app/` strict mode clean.
- Coverage of `app/enrichment_refresh.py` ≥ 85% via
  `pytest --cov=app/enrichment_refresh.py`.

**Reviewer panel**: senior + security MANDATORY (URL construction
+ license-key handling + EMF dimension safety per D-14) +
code-flow + test-reviewer.

**Declared breaks**:
- **Scope**: `refresh_loop()` and lifespan integration not present
  in this commit. The module is importable but no background task
  spawns; downloaders are callable but no scheduled invocation.
- **Resolved in**: C2 adds `refresh_loop()` and wires it into
  `app/main.py` lifespan.

(Amendment 1 F2 retires the second declared-break entry that was
about `Enricher.reload()` — no Enricher API change is shipped.)

---

### C2 — Refresh loop + lifespan integration

**Changes**:
- `app/enrichment_refresh.py` adds `refresh_loop(data_dir,
  settings, app)` which (per Amendment 1 F2):
  - On first tick: calls `seed_loaded_from_disk(data_dir)` to mark
    sources whose files are already present at startup (hybrid
    Pattern A defense per D-3).
  - Tick body: `asyncio.gather(*[refresh_<source>(...) for source
    in sources], return_exceptions=True)`. After the gather
    returns, if ≥1 source succeeded:
      1. Build a NEW `Enricher(data_dir)`.
      2. Call `new._load_sources()` (loads fresh handles from the
         freshly-atomic-renamed files).
      3. Atomically swap: `app.state.enricher = new`.
      4. For each successful source: `mark_source_loaded(source_name)`.
    Aggregate-tick log line summarizing successes / failures /
    sanity-skips.
  - In-flight `enrich()` callers hold a reference to the OLD
    Enricher and finish on it; refcount → 0 closes handles via
    `__del__`.
  - Between ticks: `await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)`.
  - Cancellation: catches `asyncio.CancelledError`, logs
    `enrichment_refresh.cancelled`, cleans any orphan tempfiles
    in `data_dir` (best-effort: `data_dir.glob("*.tmp.*")`),
    re-raises (so the awaiter's gather correctly observes
    cancellation).
- `app/main.py` lifespan changes per D-4: spawn refresh task
  AFTER `init_runtime`; the task receives `app` so it can swap
  `app.state.enricher`. On shutdown cancel → await (swallowing
  `CancelledError`) → `close_pool`.

**Tests**:
- `refresh_loop` one-tick happy path: mock httpx; all 9 sources
  succeed; assert `app.state.enricher` is a NEW instance after
  the tick (`id(post) != id(pre)`); assert `mark_source_loaded`
  populated for all 9; assert the new Enricher's `_loaded=True`
  (sources loaded).
- One-tick mixed-outcome: mock 4 sources to succeed, 2 to fail,
  2 to sanity-floor-skip, 1 to throw; loop continues; aggregate
  log line emits with correct counts; `mark_source_loaded`
  populated for the 4 successful sources only; the swap still
  happens because ≥1 source succeeded (new Enricher loads
  whatever combination of fresh + on-disk-stale files are
  present).
- All-failed tick: mock all 9 to fail; assert
  `app.state.enricher` IDENTITY unchanged (no swap on zero
  successes — the old instance keeps serving).
- In-flight concurrency: hold a reference to
  `pre = app.state.enricher`; trigger one tick; verify `pre` is
  STILL functional after swap (call `pre.enrich(...)` against
  the old handles; succeeds without segfault). Validates the
  CoW concurrency model from Amendment 1 F2.
- Cancellation: spawn loop task in a test, sleep 50ms, cancel;
  await with `pytest.raises(CancelledError)` (re-raise per the
  loop body); confirm no orphan `*.tmp.*` files in tmp_path.
- **Lifespan integration** (per Amendment 1 F5): direct
  invocation of the `lifespan` async context manager:
  ```python
  from app.main import lifespan, app
  async with lifespan(app):
      # refresh task is alive; verifiable via task introspection
      # or asserting app.state.enricher is the swap target
      ...
  # context exit: refresh task cancelled cleanly
  ```
  Mocks the refresh module's `httpx.AsyncClient` via
  `httpx.MockTransport` so the lifespan-spawned task uses fixture
  responses, not live network. NO `TestClient` / `LifespanManager`
  — matches the project convention of ASGI-transport-only with
  manual lifespan handling, but here we DO want the lifespan, so
  we invoke it directly.

**Validation**:
- `pytest tests/` total = previous + new (~10-15).
- `ruff check app/ tests/` + `mypy app/` clean.
- Manual: `docker compose up -d` then `docker compose logs app
  | grep enrichment_refresh.cancelled` after `docker compose down`
  shows clean cancellation.

**Reviewer panel**: senior + security + code-flow + test-reviewer.

**Declared breaks**:
- **Scope**: Health probe still reports the old shape (no
  `enrichment` field). Refresh task runs and populates
  `_loaded_sources` but `app/api/health.py` doesn't consume it
  yet.
- **Resolved in**: C3 extends `app/api/health.py` to read
  `all_sources_loaded_at_least_once()`.

---

### C3 — Health probe extension

**Changes**:
- `app/api/health.py`: 200 response gains `"enrichment": "ok" |
  "degraded"` field. `enrichment="ok"` iff
  `enrichment_refresh.all_sources_loaded_at_least_once()` returns
  True (every enabled source has either successfully refreshed at
  least once OR was present-on-disk at startup per
  `seed_loaded_from_disk`). 503 path unchanged (pool-failure
  only); degraded does NOT return 503 per D-3.
- Docstring updated to reflect new field; OpenAPI docs refresh
  automatically via FastAPI router introspection.

**Tests**:
- Cold-start (no fixtures pre-seeded, refresh not yet run):
  response is 200 with `enrichment="degraded"`.
- After-first-refresh (`mark_source_loaded` called for all 9
  sources): response is 200 with `enrichment="ok"`.
- Partial-loaded (5 of 9 sources marked): response is 200 with
  `enrichment="degraded"`.
- Existing pool-failure path still returns 503 (regression).

**Validation**:
- `pytest tests/integration/test_health_endpoint.py` (existing)
  + new tests pass.
- `ruff check app/ tests/` + `mypy app/` clean.
- Manual: GET `/health/` against local stack returns
  `enrichment="degraded"` initially; after the first refresh tick
  completes (mock it with `monkeypatch`) returns `enrichment="ok"`.

**Reviewer panel**: senior + security + code-flow + test-reviewer.

**Declared breaks**: none.

---

### C4 — Observability metrics

**Changes**:
- `app/observability.py METRIC_SPECS` gains 3 entries per D-5.
- `app/enrichment_refresh.py` emits `metric=True` log calls at
  three call sites:
  - On success: `_log.info("enrich.refresh.success",
    source_name=..., duration_ms=..., bytes_written=...,
    metric=True)`
  - On failure exhaustion: `_log.warning("enrich.refresh.failure",
    source_name=..., failure_class=...,
    metric=True)` — failure_class ∈ {"network",
    "sanity_floor", "parse_error", "other"}.
  - On sanity-floor skip: `_log.warning(
    "enrich.refresh.skipped_sanity_floor", source_name=...,
    bytes_attempted=..., floor_bytes=..., metric=True)`.

**Tests**:
- For each of the 3 event types: invoke the relevant code path
  with mocked httpx response; capture log records via `caplog`;
  assert event_dict has the correct `_aws.CloudWatchMetrics` EMF
  block after passing through `emf_processor`.
- License-key sanitization (per D-14 and brief §S-9): repeat the
  C1 sentinel-key test now for EMF dimension values + metric
  field values; assert sentinel never appears in EMF block fields.
- `failure_class` taxonomy: parametrize through (network →
  httpx.ConnectError), (sanity_floor → response too small),
  (parse_error → corrupt tarball), (other → unexpected exception)
  and verify the correct `failure_class` lands in the metric.

**Validation**:
- `pytest tests/` total = previous + new (~12-15).
- `ruff check app/ tests/` + `mypy app/` clean.
- Manual: run a refresh tick locally (mocked); observe
  CloudWatch-EMF-shape JSON lines in stdout.

**Reviewer panel**: senior + security MANDATORY (EMF dimension
safety — license keys MUST NOT leak to dimensions, which are
indexed by CloudWatch and would surface in dashboards) +
code-flow + test-reviewer.

**Declared breaks**: none.

---

### C5 — Integration tests with recorded fixtures

**Changes**:
- New `tests/integration/test_enrichment_refresh_e2e.py`:
  - Mock httpx (via `httpx.MockTransport`) to return the C0
    fixture content for each upstream URL pattern.
  - Use `tmp_path` for `ENRICHMENT_DATA_DIR`.
  - Spin up FastAPI via `TestClient`; lifespan runs the refresh
    task once (or trigger one tick via `refresh_all_once` direct
    call); assert files appear in `tmp_path` with correct names
    (`firehol_level1.netset`, `GeoLite2-City.mmdb` extracted from
    tar.gz, `IP2PROXY-LITE-PX11.BIN` extracted from zip,
    `aws.cidr` / `gcp.cidr` / `azure.cidr` parsed from JSON,
    `cloudflare.cidr`).
  - Construct an `Enricher(tmp_path)` after refresh; call
    `enrich(conn, IPv4Address("8.8.8.8"))` (or a fixture-shaped
    test IP); assert non-trivial fields are populated (proves
    Enricher consumes the refresh output end-to-end).
  - Verify `/health/` reports `enrichment="ok"` after the tick.

**Tests**: ~5-8 integration tests in the new file.

**Validation**:
- `pytest tests/integration/test_enrichment_refresh_e2e.py -v` —
  passes.
- `pytest tests/` total = 1116 + total-new (locked at end of C5).
- Pre-commit hook subset (unit only) passes; integration subset
  passes when invoked separately.
- No live network calls: verify with `pytest
  --disable-network` if available, OR by mocked `httpx.AsyncClient`
  in tests.

**Reviewer panel**: senior + code-flow + test-reviewer (per brief
§S-9 / §C5 — security panel slot retired here; no new code paths
introduced).

**Declared breaks**: none.

---

### C6 — Documentation updates + launch-blocker banner removal

**FINAL commit.** Lands ONLY after C0-C5 are reviewer-approved at
cleanest verdict (per D-17).

**Changes**:
- `infra/cloudformation/README.md`:
  - Top banner (lines 7-13): promote from "⚠ LAUNCH BLOCKER" to
    a historical note: "Pattern B-lite enrichment refresh landed
    in commit `<C6-sha>` on branch `feat/refactor`; see
    `.ai/enrichment.md` § Refresh module for the current
    architecture. The banner below preserves the pre-Pattern-B-lite
    context for audit."
  - Line 161 (post-deploy step 4 banner): update to "Pattern
    B-lite ships the in-process refresh task; values become
    consumed at next startup after the operator populates the
    secrets."
  - Carry-forward section line 305: change "LAUNCH BLOCKER:
    Pattern B-lite…" to "RESOLVED (post-CFN-pass): Pattern B-lite
    enrichment refresh landed; see `app/enrichment_refresh.py`
    and `.ai/enrichment.md` § Refresh module."
- `docs/aws-deploy-runbook.md`:
  - Lines 12-22 banner: same historical-note treatment.
- `.ai/enrichment.md`:
  - Replace the §Refresh script paragraph (lines 98-107) with a
    new §Refresh module section describing:
    - The in-process async refresh task (`app/enrichment_refresh.py`)
    - Per-source independence + sanity floors + atomic-replace
      (bytes form for 8 sources; streaming form for IP2Proxy per
      Amendment 2 F3)
    - Retry policy + cadence (1×/24h refresh; IP2Location's 5/24h
      token quota gives 4 spare slots/day for operator probes)
    - Health-probe `enrichment` field contract
    - **Disk-budget note**: `ENRICHMENT_DATA_DIR` should have
      ≥3.5 GiB free (IP2Proxy BIN is 1.6 GiB; atomic-replace
      tempfile peaks at 2× that). ECS Fargate ephemeral storage
      default 20 GiB is comfortable; reduce only with caution.
    - Sync `scripts/fetch_enrichment.py` retained as cron
      fallback option (caveats: existing `.json` vs `.cidr`
      mismatch + saves IP2Proxy as `.BIN` directly without ZIP
      extract — both per `.claude/BUGS.md`).
- `.claude/STATUS.md`: append a row "Pattern B-lite landed;
  launch-blocker resolved; CFN README + runbook banners promoted
  to historical notes."

**Tests**: none.

**Validation**:
- `cfn-lint infra/cloudformation/freightsentry-riskd.yml` (no
  template changes; sanity).
- `grep -rn "LAUNCH BLOCKER\|launch.blocker" infra/cloudformation/
  docs/` returns ONLY historical-note text (the `launch-blocking`
  reference at `docs/aws-deploy-runbook.md:388` belongs to an
  UNRELATED Phase 6A platform-integration dependency — not the
  Pattern B-lite blocker — and is preserved).
- `pytest tests/` regression check passes at the locked end-of-C5
  count.

**Reviewer panel**: doc-reviewer (primary) + senior-engineer (for
status-row accuracy + the cross-reference in
`infra/cloudformation/README.md` to `.ai/enrichment.md`).

**Declared breaks**: none.

---

## Acceptance criteria (re-stated for execution-time check)

Restates brief §Acceptance with concrete validation commands:

1. `/health/` reports `enrichment="degraded"` on cold start
   (no enrichment data present in ENRICHMENT_DATA_DIR);
   transitions to `enrichment="ok"` after first successful refresh
   tick of all 9 enabled sources.
   Verify: integration test in C5.

2. Refresh loop survives transient upstream failures.
   Verify: C1 retry tests + C2 mixed-outcome tick test.

3. Sanity floors prevent catastrophic upstream emptying.
   Verify: C1 sanity-floor tests per source.

4. License keys never surface in logs / EMF / errors.
   Verify: C1 + C4 sentinel-key tests.

5. Unit tests pass: all categories enumerated in §S-9.
   Verify: `pytest tests/unit/test_enrichment_refresh.py -v` after
   C4 lands.

6. Integration test exercises end-to-end refresh.
   Verify: C5 commit.

7. Pre-commit gates pass on every commit.
   Verify: `pre-commit run --all-files` after each commit (the
   commit itself triggers hooks).

8. EMF metrics emit for refresh success / failure / sanity-floor.
   Verify: C4 commit.

9. CFN README + runbook banners promoted to historical note in C6.
   Verify: grep check in C6 validation.

10. No regression: pre-pass test count **1116** preserved; post-pass
    count is `1116 + N` where N is the count of new tests in
    C1+C2+C3+C4+C5.
    Verify: `pytest tests/ --collect-only -q | tail -1` after C5
    locks the post-pass count.

11. Reviewer panel approval on every commit at cleanest verdict
    on first or second cycle (per CLAUDE.md cycle cap).

12. Refresh-task cancellation is clean on FastAPI shutdown.
    Verify: C2 cancellation test + the `grep -rn "*.tmp.*"
    $ENRICHMENT_DATA_DIR` returns empty after `docker compose down`.

---

## Carry-forward (out of scope this pass)

Per brief §"Carry-forward AFTER Pattern B-lite":
- Scheduled refresh as separate ECS task (in-process simplifies
  v1).
- Caching downloaded artifacts in S3 (post-launch if rate-limit
  pressure observed).
- Cross-tenant enrichment cache sharing (multi-tenant scale only).
- Provider-managed feeds (Microsoft Azure service-tag official
  API has matured — post-launch refactor).
- Per-source granular health endpoints (`/health/enrich/maxmind`
  etc. — single `enrichment` field is enough at launch).
- `scripts/fetch_enrichment.py` reconciliation (the V-1 BUGS entry
  is preserved for post-launch triage; the script remains as cron
  fallback but its `.json` vs `.cidr` filename mismatch needs a
  decision: extend with parsing logic OR delete as superseded).

---

## Reviewer checkpoint (end of Phase 2)

Operator confirms (already answered via AskUserQuestion at end of
verification phase):

| Question | Answer | Plan reference |
|---|---|---|
| Commit strategy | Atomic, one logical change per commit | D-16 |
| Health degraded → HTTP | 200 (degraded informational) | D-3 |
| Enricher re-init | `Enricher.reload()` method | D-2 |
| Sanity-floor values | Accept V-9 table | D-9 |

Pending operator final approval of THIS plan document before
Phase 3 (execution) begins. Per CLAUDE.md Autonomous Execution
rule #1: no further AskUserQuestion calls during Phase 3 unless a
substantive drift surfaces.

---

## End of plan
