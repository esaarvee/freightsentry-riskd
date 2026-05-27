# REPORT_PHASE_2.md

Phase 2 aggregate report. End-of-phase operator checkpoint.

Per-batch detail: [REPORT_PHASE_2A.md](REPORT_PHASE_2A.md) · [REPORT_PHASE_2B.md](REPORT_PHASE_2B.md) · [REPORT_PHASE_2C.md](REPORT_PHASE_2C.md) · [REPORT_PHASE_2D.md](REPORT_PHASE_2D.md)

---

## Phase 2 totals

| Metric | Value |
|---|---|
| Commits in Phase 2 | 27 across 4 batches + 5 plan/precondition commits |
| Tests at end of Phase 1 | 274 |
| Tests at end of Phase 2 | **432** (+158 net additions) |
| Production source files changed | 6 (1 NEW: `app/scoring_constants.py`; 5 EDITED: `app/scoring.py`, `app/api/booking.py`, `app/context.py`, `app/rules.py`, `app/baseline.py`) |
| Migrations added | 1 (`0002_shipments_destination_hmac.py`) |
| Rules in `app/rules.yaml` | 14 (Phase 1) → **67** (+53 net Phase 2C additions) |
| DSL whitelist (ALLOWED_CONTEXT_FIELDS) | 45 (Phase 1) → **56** (+11 Phase 2B additions) |
| Validation tooling | `ruff check` clean · `mypy --strict` clean · `pytest --asyncio-mode=auto` 432/432 |
| New test files | 12 (8 rule-test modules + 2 integration suites + 2 unit modules) |

---

## Per-batch summary

### Batch 2A — Layer 2 scoring infrastructure (5 commits)

Wires Layer 2 (account_prior) + Layer 3 maturity downweight into `app/scoring.py`. Final score: `noisyOR(account_prior, signal_score)`. Layer 1 BLOCK short-circuit bypasses Layer 2 entirely (sentinel zeros on `ScoringResult.{account_prior, signal_score, maturity}`). 4 formula divergences from FreightSentry's `scorer.go` documented in `.ai/decisions.md` (multiplicative vs min-of-fractions maturity; linear vs log1p shipments; 4-tier direct-lookup vs 2-tier noisy-OR flags; no customer-inheritance term).

Key deliverables:
- `app/scoring_constants.py` — locked constants (MAX_NEW_ACCOUNT=0.10, TRUST_FACTOR=0.25, MATURITY_K=0.30, FLAG_WEIGHTS=(0.00, 0.15, 0.25, 0.35), MATURITY_AGE_DAYS=180, MATURITY_SHIPMENTS=50)
- `CustomerState` frozen dataclass (PII-free)
- `risk.evaluation` structured-log event with Layer 2 + Layer 3 components (`account_prior`, `signal_score`, `maturity`, `trust_score`, `flagged_count`)
- 28 new tests (constants module + Layer 2 unit suite + observability strengthening)

All 5 commits cleared the merge gate on first pass.

### Batch 2B — Context derivations + recipient overlap + destination_hmac (7 commits)

Extends `build_context` with the 11 Phase 2 fields Phase 2C rules consume, lands the cross-customer recipient-overlap SQL with tenant-scoped binding, adds `shipments.destination_hmac` column + endpoint write + cross-tenant integration tests.

Key deliverables:
- 11 new Context fields: `customer_locked_cloud_api`, `customer_locked_web_only`, `days_since_last_booking`, `is_new_user`, `ip_familiarity_tier`, `ip_new_known_asn`, `is_residential_asn`, `ip2p_threat_any`, `recipient_cross_customer_count`, `customer_distinct_ips_30d`, `impossible_travel`
- DSL whitelist: 45 → 56 fields
- Migration 0002: `shipments.destination_hmac text NOT NULL` + `ix_shipments_tenant_dest_hmac_booking_ts` covering index
- Tenant isolation pinned in 3 independent tests (SQL helper, helper-via-build_context, full integration through booking endpoint)
- 2C.3 test infra fix: `_seed_baseline` decay_anchor_date aligned to Python `date.today()` (was using PG `current_date` → cross-TZ drift)
- 2C.1 skipped: `shipment_volume_30d` column never existed — STATUS row only

Substantive in-batch correction: 2B.4 false-pass unit tests (re-implemented threshold checks inline rather than calling production) — deleted entirely, rewrote 21 integration tests against `build_context` output.

### Batch 2C — Rule additions (9 commits including conftest extraction)

Grows `app/rules.yaml` 14 → 67 rules. All conditions reference only post-2B whitelist fields. No production-Python changes — rules-only batch.

Per-batch rule additions:
- 2C.1 — trust-conditioned (7 rules)
- 2C.2 — dormancy + customer lock-in (5 rules; case-1/2 ATO primary detectors)
- 2C.3 — residential ASN + IP-class diversity (6 rules; first 2C rule with parenthesized OR)
- 2C.4 — recipient overlap (2 rules, tier-disjoint via upper bound; security-priority)
- 2C.5 — velocity + identity-novelty (11 rules)
- 2C.6 — value-anomaly + geographic + threat composites (17 rules after triage; plan summary said 13 — arithmetic error logged to `.claude/BUGS.md`)
- 2C.7 — IP-familiarity tier + closing pieces (5 rules; canonical total-count audit at 67)

Substantive in-batch corrections:
- 2C.3 false-pass `decision in ("ALLOW", "REVIEW")` integration assertion dropped per test-reviewer feedback (verified persistence-row checks, not decision outcome)
- 2C.3 cross-TZ time-bomb fix in `_seed_baseline`
- Proactive D3 prevention (conftest extraction of shared rule-test helpers) before second copy would land

Triaged from Phase 2C scope (each documented + audit-tested):
- `threat_intel_level1` — Phase 1 BLOCK already covers
- `outside_allowed_country` — Phase 4 tenant-config dependency
- `unknown_email/phone_for_customer` — Phase 3 (feedback endpoint)
- `user_ip_rotation_*` — semantic mismatch between candidate Context fields and source

### Batch 2D — Threshold audit + case fixtures + canonical BLOCK assertion (5 commits)

Locks the tuned-threshold values, synthesizes case-1 dashboard ATO, builds the Layer 2 integration matrix, lifts the case-2 BLOCK assertion to canonical Phase 2 success criterion. Test-only batch — no production changes.

Key deliverables:
- 4 tuned-threshold pins (cadence z>6, velocity_spike_daily_api>50, residential_asn_high_velocity>15, ip_familiarity_tier /24-only)
- Case-1 synthetic 30-shipment burst fixture + integration test: at least one BLOCK occurs, ip_fully_new fires from shipment 0, ip_velocity_high_ui fires by shipment 11
- 8 Layer 2 integration tests covering brand-new + base-prior, established + collapse, flagged tier-2, brand-new + 1-flag, lock-in positive + negative, maturity collapse, lock-in observation-threshold boundary
- Case-2 BLOCK end-to-end with 6-rule compound assertion (the canonical Phase 2 success criterion)
- Shared conftest helpers: `seed_customer_with_baseline` + `seeded_ip_enrichment` async context-manager

Substantive in-batch corrections:
- 2D.2 false-pass assertion (d) (vacuous when first_block_idx=0) replaced with compound-evidence check
- 2D.2 4-IP burst that made ip_velocity_high_ui impossible to fire → switched to single-IP (more realistic ATO anyway)
- 2D.2 ip_enrichment cleanup leak (global table not in `_TENANT_SCOPED_TABLES`) → try/finally with DELETE
- 2D.3 D3 enrichment-helper duplication → extracted `seeded_ip_enrichment` to conftest
- 2D.3 lock-in negative test had 2 non-firing reasons → bumped obs=25 so only the lock flag is load-bearing
- 2D.3 maturity downweight test name overpromised → renamed to `..._collapses_account_prior_for_mature_customer`
- 2D.3 tautological smoke replaced with substantive obs-threshold lock-in boundary test
- 2D.4 6-rule compound assertion (was 2-rule only) — catches "BLOCK reached for the right reasons" not just "BLOCK reached"

---

## Cross-batch in-batch corrections — the recurring patterns

Two patterns surfaced across multiple batches:

**False-pass shapes detected by reviewers (5 occurrences across 2B.4, 2C.3, 2D.2, 2D.3, 2D.4)**: tests that pass when production code is broken. Each caught by test-reviewer cycle 1 and resolved in cycle 2:
- 2B.4: unit tests re-implementing boolean expressions inline → deleted, rewrote as integration tests
- 2C.3: `decision in ("ALLOW", "REVIEW")` relaxation → dropped decision assertion (verified persistence-row scope)
- 2D.2: assertion (d) vacuous at first_block_idx=0 → compound-evidence check
- 2D.3: lock-in negative test had two non-firing reasons → bumped obs to isolate gate
- 2D.4: docstring promised 6 rules, asserted only 2 → set-membership over the full 6

**Cross-TZ time bombs (2 occurrences in 2B.6 and 2C.3)**: hardcoded dates or PG `current_date` calls that silently drift from Python `date.today()` on cross-TZ runs:
- 2B.6: `booking_ts` hardcoded to "2026-05-26T10:00:00Z" → switched to `datetime.now(UTC)`
- 2C.3: `_seed_baseline` `decay_anchor_date = current_date` (PG) → switched to `date.today()` (Python)

Both lessons applied proactively in 2D.

---

## Notable architectural decisions absorbed during Phase 2

1. **Single connection for sequential reads** (Phase 1 deviation reinforced): `build_context` does 7 sequential `await`s on the txn connection. asyncpg does not multiplex on a single connection; parallelism via separate pool connections would lose the `FOR UPDATE` lock. Phase 5 load test revisits if context-load latency tightens.

2. **No weight tuning in Phase 2**: every per-rule weight came from freight_risk / FreightSentry source catalogs. Phase 6 staging replay calibrates per-tenant FPR and tunes. The bootstrap rule prevented several almost-tunes during in-batch corrections.

3. **Test fixtures extend rather than tune**: when 2D.4 found the case-2 customer wasn't satisfying the lock-in gate, the fix was to extend the fixture's seeded baseline (add `channel_hist`) — not to tune the lock-in rule's threshold.

4. **HMAC at egress invariant preserved**: `destination_hmac` computed via the canonical `signal_helpers.hmac_hex` at the same point as `email_hmac`/`phone_hmac` in `app/api/booking.py`. Plaintext doesn't propagate to new sinks.

5. **DSL evaluator security boundary unchanged**: Phase 2's 53 rule additions reference only Phase-1+Phase-2B whitelist fields. The AST whitelist (`BoolOp`/`UnaryOp(Not)`/`Compare`/`Name`/`Constant`/`Load`/`And`/`Or`/`Not`), `{"__builtins__": {}}` lockdown, and `MappingProxyType` env wrapping are all unchanged.

---

## Final case-1 + case-2 outcomes

| Case | Fixture | Outcome |
|---|---|---|
| Case-1 (dashboard ATO) | 30-shipment burst, single VPN IP, established cloud customer | BLOCK from shipment 0 (signal_score ~0.93 — VPN + threat_level2 + intercontinental + ip_fully_new compound). Compound-evidence guard ensures every BLOCK has >= 2 fired rules. |
| Case-2 (API ATO) | API booking from unfamiliar residential IP against cloud-API-locked customer | BLOCK end-to-end with 6 compound rules firing. **Canonical Phase 2 success criterion met.** |

---

## Phase 3 inheritance

Phase 3 inherits from Phase 2:

1. **Modification endpoint scope** — reuses the same Layer 2 + Layer 3 + maturity-downweight scoring infrastructure. No new scoring code needed; modification endpoint produces a different request type but shares the rule-evaluation path.
2. **Feedback endpoint scope** — writes `r_n` increments on baseline; the `add_rejected_observation` helper exists in `app/baseline.py` already.
3. **~12 deferred rules** waiting on feedback (4 `_previously_rejected` rules) + globally-blocked-vectors (4-8 `_globally_blocked` rules) + rarity p-values (5-7 `*_rarity_p` rules) + cadence/burst rules (2-3) + device fingerprinting (out-of-scope).
4. **Currency normalization** — Phase 2C's absolute-value thresholds (`>10000`, `>5000`, `>2000`, `>1000`) are implicitly USD. Phase 3 planning should decide whether to introduce a `value_in_usd` normalization or document the USD-implicit assumption for operators.
5. **Phase 6 staging replay** — case-1 + case-2 still use synthesized fixtures; production data lands Phase 6 for FPR/recall calibration.

---

## Open items at the Phase 2 / Phase 3 boundary

1. **Operator approval to open Phase 3 scope** per `MASTER_PLAN.md`.
2. **Drain `.claude/BUGS.md`** — single entry (PLAN_PHASE_2C arithmetic error, 17 rules vs plan's 13).
3. **Phase 5 carry-forwards**: non-superuser RLS-enforcing role, lockfile (`uv.lock`), non-root container user, `last_used_at` on api_tokens, in-process tenant-config cache. None blocking Phase 3.

---

End of Phase 2. `feat/refactor` branch ready for Phase 3 operator approval.
