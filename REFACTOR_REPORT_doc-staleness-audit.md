# REFACTOR_REPORT — Documentation & Comment Staleness Audit

Pass: doc-staleness-audit · branch `feat/refactor` · base HEAD `7885cc1` (after the dead-capability audit)
Date: 2026-06-13 · Outcome: **6 doc-staleness commits landed + a 3-commit test-fix detour that unblocked the gate.**
Phase-1 findings: `/tmp/doc-staleness-findings-01.md`. Plan: `REFACTOR_PLAN_doc-staleness-audit.md`.

---

## 1. Commits landed

### Doc-staleness pass (the deliverable)

| # | SHA | Files | Finding(s) | Review |
|---|---|---|---|---|
| 1 | `a170a1f` | `README.md` | F3 (greenfield→pre-launch), F4/F5 (orphaned `MASTER_PLAN.md`/`PLAN_PHASE_1.md` links) | doc + senior |
| 2 | `961bc41` | `production-launch-checklist.md`, `aws-deploy-runbook.md` | F1/F2 (deleted symmetric-triangle rule refs → asymmetric) | doc + senior |
| 3 | `399b5a8` | `production-launch-checklist.md` | F6 (Phase B migrate-execution model: auto-on-every-deploy, bootstrap = the only manual run) | doc + senior + code-flow |
| 4 | `bf9c881` | `.ai/system-status.md` | F10 (snapshot refresh: PBL D-series + enrichment-refresh) | doc + senior |
| 5 | `a62c350` | `app/context.py` | F7a (`7C.3`→`7C.2`), F7b (deleted triangle-derivation comment → asymmetric) | code-flow + senior + doc |
| 6 | `50e3f3f` | `alembic/versions/0001_foundation.py`, `.ai/schema.md` | F8/F9 (column-comment derivation name, in-place) | db + doc + senior + code-flow |

### Test-fix detour (operator-authorized "fix pre-existing tests first"; unblocked the gate)

| SHA | Files | Purpose | Review |
|---|---|---|---|
| `fdea14b` | `.claude/BUGS.md` | Log the 9 pre-existing full-suite failures | triage-trivial |
| `a8fdb09` | `tests/unit/test_alembic_env.py` | Fix the 8 alembic-env stub failures (declared-break `--no-verify`) | test + senior + code-flow |
| `25f9932` | `app/enrich.py`, `tests/unit/test_enrichment_refresh.py` | Guard binary-DB loads (fixes 3 enrichment + 4 e2e); restores the gate | senior + security + code-flow + test |

All reviewer panels returned cleanest verdicts on cycle 1 (merge gate satisfied without a second cycle). Two non-blocking reviewer notes on `25f9932` were addressed before commit (CoW-test stub removed so it exercises the real guard; `importorskip` added to the resilience test).

---

## 2. Counts

- **Files swept (in-scope forward-facing):** 18 docs/READMEs + in-code comments across `app/**`, `alembic/`, infra.
- **Replacements by category:**
  - stale-replace: **5** — F1, F2, F3, F4, F5.
  - stale-but-partially-true / scope-down: **1** — F6 (bootstrap-remainder; manual run kept, scoped to first deploy).
  - in-code comment: **3** — F7a, F7b (context.py), F8 (migration comment).
  - dependent doc sync: **1** — F9 (schema.md quote synced to F8).
  - snapshot refresh (operator-approved expansion): **1** — F10.
- **Scoped-down remainders preserved:** 1 (F6 — the manual `alembic upgrade head` + `ALEMBIC_DATABASE_URL` kept as the first-deploy bootstrap).
- **Unverifiable flags left for operator:** **0** — all three Phase-1 flags (F8/F9/F10) were adjudicated by the operator and actioned, not deferred.
- **Ledger errors flagged-not-fixed:** **0** in the carved-out ledgers (the one migration-comment staleness, F8, was operator-approved for in-place fix rather than flag-only).
- **CLAUDE.md:** swept, **no stale phrasing found** → no edits (the pre-authorized scope expansion yielded nothing).

---

## 3. Operator adjudications (AskUserQuestion)

| Item | Decision | Effect |
|---|---|---|
| Commit strategy | atomic (6 commits) | — |
| F8/F9 migration comment | **edit `0001` in place** (append-only exception) | No new migration, no 5→6 count cascade. Verified round-trip + live comment. |
| F10 system-status | **refresh** the snapshot | Commit 4. |
| F6 checklist Phase B | scope-down, **foreground every-deploy automation** | Commit 3 (corrected my initial "one-off" framing per operator). |
| Mid-run: gate red | **fix pre-existing tests first** | The test-fix detour (3 commits). |
| Enrichment fix approach | mock orchestration tests **+ guard prod** | `25f9932`. |

---

## 4. Not-actually-stale register (verified current; recorded so they aren't re-flagged)

- **N1** `.ai/rules.md:586-623` — case-3b catalogue + deletion record: accurate.
- **N2** `.ai/rules.md:226/242/327` — `ip_country`/`ip_familiarity_tier`/`shipment_currency` are field-definition tables, **not** rule-consumption claims (dead-cap hand-off #2 not triggered).
- **N3** `.ai/enrichment.md` (L116, L235-246) — already frames IP2Proxy/enrichment as graceful degradation (hand-off #3 satisfied).
- **N4** `docs/calibration-backlog.md` Item 6 (incl. L82) — accurate historical context in an active item.
- **N5** `docs/calibration-backlog.md:414-445` "Resolved/superseded" + L440 — ledger carve-out.
- **N6** runbook §B.1 (L340-415) + CFN README §1 (L115-141) — already correctly auto-migration/bootstrap (PBL D6).
- **N7** `CLAUDE.md:88-89`, `README.md:33` — local docker-compose alembic; accurate.
- **N8** seeded-but-absent: **no** "two DSN sources" comment (env.py has three: `ALEMBIC_DATABASE_URL`→`DB_MASTER`→`DATABASE_URL`); **no** three-service architecture inheritance anywhere.
- **N9** `.ai/conventions.md:31,244` — sync-psycopg env.py + append-only migrations: accurate.
- **N10** `docs/observability.md:22,142` — `FreightSentry/RiskD` is the live EMF namespace string, not stale branding.
- **N11** `CLAUDE.md` overall — no stale migrate/greenfield/MASTER_PLAN/phase phrasing.
- **N12** `app/rules.py:129`, `app/context.py:364-366`, `app/models.py:47-49` — accurate 7C.2 symmetric→asymmetric replacement notes.
- **N13** `app/rules.yaml:216-217,651-686,722` — accurate deleted-rule/replacement comments (also outside the `*.py`/alembic/infra comment-sweep scope).

---

## 5. Cross-reference fixes (side effects)

- `README.md` Quick-links: dead `MASTER_PLAN.md` / `PLAN_PHASE_1.md` pointers replaced with a live `docs/history.md` pointer (resolves two orphaned links).
- `production-launch-checklist.md` Phase B now cross-refs runbook §B.1 "Auto-migration on deploy" (verified to resolve).

---

## 6. Detailed test-failure report (operator-requested)

The doc-staleness pass is prose/comment-only, but commits 5 & 6 touch `.py` files, so they hit the
`pytest tests/unit/ -x` pre-commit gate. That gate was **already red on HEAD** (independent of this
pass) with **11 unit-test failures**, which blocked the commits. The operator authorized fixing them
first. Full root-cause analysis below.

### 6.1 Environment provisioning (required to even run the gate)

The session had no `.env` and no database. Provisioned: `docker compose up -d postgres`, `alembic
upgrade head` (creates `riskd_app_login` + RLS + schema), and host pytest with inline
`DATABASE_URL`/`ALEMBIC_DATABASE_URL`/`HMAC_SECRET` (localhost). The unit suite's session-scoped
`autouse` `_pool` fixture opens a real Postgres connection for *every* test, so a live DB is mandatory.

### 6.2 Cluster 1 — `test_alembic_env.py` (8 failures) → FIXED (`a8fdb09`)

- **Root cause:** `alembic/env.py:107-108` runs `run_migrations_offline()` at *module import* when
  `is_offline_mode()` is True; the test loads env.py via importlib with a stubbed `alembic.context`
  whose `begin_transaction` returned `None`, so the import hit `with None:` → `TypeError`. Both env.py
  and the test landed in the same commit (`989dbbc`, PBL D1) — the stub was incomplete from the start.
- **Fix:** stub `begin_transaction` returns `contextlib.nullcontext()` (faithful to alembic's real
  context-manager contract). Pure test fix; all 8 now exercise the DSN-composition helpers.

### 6.3 Cluster 2 — `test_enrichment_refresh.py` (3 failures) → FIXED (`25f9932`)

- **Root cause:** `Enricher._load_sources` opened the MaxMind (`maxminddb.open_database`) and IP2Proxy
  (`IP2Proxy.open`) binaries **without exception-guarding**. The committed test fixtures were built for
  older library versions; the bumped libs (**maxminddb 3.1.1** — `Metadata` became a strict `kw_only`
  dataclass requiring 9 fields; **IP2Proxy 3.6.1** — header validation) reject them, so the unguarded
  opens raised and crashed `_load_sources`. (`test_unexpected_exception` crashed on a *different* path:
  the refresh's direct-BIN fallback wrote a 44-byte text body as the `.BIN`, which `_load_ip2proxy`
  then rejected.)
- **Why not "regenerate fixtures":** ruled out by evidence — `mmdb_writer` can synthesize a valid mmdb
  (round-trip verified), but the **IP2Proxy PX11 BIN has no public writer**, is ~1.6 GB real (not
  committable), and `_load_ip2proxy` runs right after `_load_maxmind`, so a fixed mmdb just moves the
  crash one line down. Real downloads wouldn't help (tests mock the network; fixtures must be tiny).
- **Fix (production hardening):** guard the City/ASN/IP2Proxy opens → `WARNING` (error type only,
  leak-safe) + leave the reader `None`. A corrupt/incompatible DB now degrades **exactly like a
  missing one** (geo/proxy signals `None`/`False`, no spurious positives) instead of 500-ing a booking
  request or blocking the refresh swap — extending the documented graceful-degradation design; the
  lookup path was already `None`-safe. Test side: a `_stub_binary_loads` fixture isolates the
  swap/result-handling orchestration tests; the CoW test + a new `TestLoadSourcesResilience` exercise
  the real guard.
- **Bonus:** the same guard also fixed **4 enrichment-e2e integration tests**
  (`test_enrichment_refresh_e2e.py`).

### 6.4 Net gate result

`pytest tests/unit/` went from **11 failing → 937 passing, 0 failing**. Verified by stash/baseline
diff (full suite 25 → 9 failures): every one of the 16 fixed was a target of this work; **zero
regressions introduced**.

### 6.5 Remaining 9 full-suite failures — PRE-EXISTING, OUT OF SCOPE (logged: BUGS.md `2026-06-13`)

These fail at HEAD baseline, are unrelated to this work, and are **not in the unit gate** (only surface
in a full `pytest tests/` run):

| Test | Count | Apparent cause (not root-caused) |
|---|---|---|
| `test_per_tenant_maturity_overrides.py` | 5 | Scoring assertion (`assert resp["score"] < 0.05` observes `1.0`) with `enrich.cache_hit` leaking from other tests → shared-DB cross-test state |
| `test_feedback_chain_e2e.py` | 2 | Previously-rejected feedback chain — likely same shared-DB-state issue |
| `test_concurrent_baseline_writes.py` | 1 | Concurrency serialization |
| `test_enrichment_refresh.py::...test_log_tick_summary_counts` | 1 | **Unit** test, but fails only when integration tests run first (global-state ordering pollution); passes in `pytest tests/unit/` |

These look like integration-test isolation gaps (no per-test DB truncation/rollback in the ad-hoc
docker-compose DB), not production-logic bugs — **but they are not root-caused.** Deferred to a
post-phase pass per operator direction.

---

## 7. Operator follow-ups (flagged, not actioned here)

1. **Durable staging RDS (us-east-2)** that already ran `0001`: its `registered_country` column comment
   stays the old text until a one-off `COMMENT ON COLUMN` or a rebuild (cosmetic; from the in-place-fix
   decision). Pre-launch production has no DB, so prod is unaffected.
2. **`tests/**` triangle comments** (`test_customer_registered_country.py:6` loosely worded; others
   accurate) — outside this pass's declared `*.py`/alembic/infra comment-sweep scope. Candidate for a
   future tests-doc pass.
3. **Enrichment observability** — a `metric=True` tag / EMF metric on the new `enrich.*_load_failed`
   WARNINGs (and optionally extending `/health` to reflect parse failure, which currently keys off
   download success) so a corrupt-but-downloaded DB can alarm rather than only logging. (Security +
   senior reviewer note on `25f9932`.)
4. **9 pre-existing full-suite failures** (§6.5) — investigate integration-test isolation, then re-run
   against the canonical harness. Logged to `.claude/BUGS.md`.
5. **Not-actually-stale register** (§4, N1-N13) — feeds Phase 9's doc lens so these phrasings aren't
   re-investigated.

---

## 8. Constraints honored

Replace-not-append throughout (git history is the supersession record; no "supersedes/previously"
framing) · no carved-out ledger rewritten (`decisions.md`, `BUGS.md` content, `STATUS.md`,
`REPORT_*`, `REFACTOR_REPORT_*`, `history.md`, backlog "Resolved/superseded") — the BUGS.md entry is an
operator-directed *append*, not a rewrite · the only behavior change (the enrichment guard) was an
explicit operator-authorized scope addition to unblock the gate, fully reviewed · migration edit was
operator-approved in-place, verified, comment-only · next reasonable action: the `v*` tag push once the
operator is satisfied (and after the post-phase integration-test pass).
