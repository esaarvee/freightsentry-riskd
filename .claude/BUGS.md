# .claude/BUGS.md

Tangential issues discovered mid-task. Operator drains at phase boundaries.

## 2026-05-27 — PLAN_PHASE_2C.md 2C.6 rule-count arithmetic error

Discovered by: implementer during 2C.6 execution
Location: PLAN_PHASE_2C.md § 2C.6, line 380 ("this commit lands 13 rules, not 15")
Severity: low
Observation: The 2C.6 section table enumerates 6 (value-anomaly) + 5
(geographic) + 8 (threat-intel composites, of which 2 are triaged
out) = 17 rules after triage. The plan body says "13 rules" and the
header on the threat-intel section says "(~4)". Both numbers
disagree with the actual table contents. Implementation lands 17
rules to match the table; reviewer panel verified the arithmetic and
approved.
Suggested action: when 2C closes, refresh the rule-count summary in
PLAN_PHASE_2C.md to reflect actual 17 (and update the batch-summary
"~63" total downstream — actual is 62 at the end of 2C.6, will be
greater at end of 2C.7).
RESOLVED: pre-Phase-5 — Phase 4 wrap report (REPORT_PHASE_4.md) and
the verified `rules.yaml` rule count (79 rules per Phase 5 bootstrap
precondition) supersede the stale PLAN_PHASE_2C.md count. The plan-file
arithmetic is a frozen historical artifact; no live code or doc depends
on the "13"/"~63" numbers.

## 2026-05-27 — decisions.ux_decisions_tenant_request UNIQUE is flat across request_type

Discovered by: reviewer panel during 3A.6 execution (senior-engineer,
security-auditor, code-flow concurred)
Location: alembic/versions/0001_initial.py:141 +
app/api/booking.py + app/api/modification.py
Severity: medium
Observation: The `decisions` table's UNIQUE constraint
`(tenant_id, request_id)` predates the Phase 3A `request_type`
discriminator. Both the booking and modification endpoint idempotency
checks scope their SELECT by their own request_type (so a booking
replay won't return a modification's envelope and vice versa), but the
DB constraint does not include `request_type`. Consequence: a tenant
who submits a booking with `request_id='X'` and later a modification
with `request_id='X'` (or vice versa) hits an INSERT-time
UniqueViolation. 3A.6 added try/except handlers on both endpoints to
translate the violation into a clean 409 — but the underlying
mismatch between the public idempotency semantics
("(tenant_id, request_id, request_type)") and the DB enforcement
("(tenant_id, request_id)") remains.
Suggested action: Phase 5 hardening (or earlier opportunity) widens
the UNIQUE constraint to
`(tenant_id, request_type, request_id)` via an alembic migration. Once
in place, the try/except 409 catches in booking.py + modification.py
become defense-in-depth; the comments referencing this BUGS entry
should be updated to mark RESOLVED.
RESOLVED: 5A.7 (migration 0007 drops `ux_decisions_tenant_request`
and adds `CREATE UNIQUE INDEX ux_decisions_tenant_request_type ON
decisions (tenant_id, request_type, request_id)`). Inline comments in
booking.py / modification.py updated. Try/except → 409 retained as
defense-in-depth for intra-type duplicate POSTs.

## 2026-06-01 — ruff version drift between pre-commit pin and local install

Discovered by: senior-engineer reviewer during 4A.4 cycle-1
Location: `.pre-commit-config.yaml` (ruff hook version pin) vs locally
installed ruff (`pip show ruff` reports 0.15.7; pre-commit pin appears
to be 0.6.0)
Severity: low (workflow / review-quality)
Observation: `ruff format app/ tests/` on the local install produces
formatting changes (frozenset member layout, parenthesized call
expansion, implicit-string-concat merges, assert tuple-form) that the
pre-commit hook (running an older ruff) does NOT make. Net effect: any
commit that runs `ruff format` over the whole tree reformats ~22 files
unrelated to the current task, inflating the review surface. Caught
in 4A.4 cycle-1 review (NEEDS MINOR FIXES) and reverted via
`git checkout HEAD -- <unrelated-files>`; in-scope 5-file diff
restored.
Suggested action: bump the ruff pin in `.pre-commit-config.yaml` to
match the current ecosystem version (likely 0.15.x), run `ruff format`
across the codebase once in a dedicated formatting-sync commit, and
land that BEFORE the next phase to avoid re-running into the same
scope-creep risk on every commit. Until then: only run `ruff format`
on the files actually touched by the commit, not the whole tree.
RESOLVED: 5A.1 (pre-commit pin bumped to v0.15.15; pyproject ruff
constraint pinned `>=0.15.0,<0.16.0`; `uv.lock` regenerated; one-shot
`ruff format` applied across `app/`, `tests/`, `scripts/`, `alembic/`
as part of the same commit).

## 2026-06-02 — docker-compose `app` service unusable without DATABASE_URL override

Discovered by: implementer during PLAN_PHASE_5A.md 5A.4 (Dockerfile non-root smoke test)
Location: `.env` line 8 vs `docker-compose.yml` line 28
Severity: medium
Observation: `.env` sets `DATABASE_URL=postgresql://riskd:riskd@localhost:5432/riskd`
(correct for host-side pytest/alembic runs against dockerized postgres).
`docker-compose.yml` substitutes `${DATABASE_URL:-postgresql://riskd:riskd@postgres:5432/riskd}`,
preferring the `.env` value — which points at `localhost` from inside the
container, where it resolves to itself, not postgres. Net effect: `docker compose up`
of the `app` service fails on startup with `ConnectionRefusedError`. The dev
workflow has been "pytest from host against dockerized postgres only," so the
app container has not been exercised. 5A.4 smoke test required a transient
shell-env override (`DATABASE_URL='postgresql://riskd:riskd@postgres:5432/riskd'
docker compose up -d`) to validate the non-root user change.
Suggested action: split the env into two — `DATABASE_URL` for the container
(stays `postgres:5432`), and a separate `DATABASE_URL_HOST` for host-side
commands at `localhost:5432`. Or add a docker-compose `.env.docker` and
document the two-file workflow. Address before Phase 6 production deploy
(production won't have this confusion since it uses Secrets Manager) and
ideally before 5D.2's role transition (which already touches `DATABASE_URL`
and adds `ALEMBIC_DATABASE_URL`).
DEFERRED to Phase 6: 5D.2 retro added ALEMBIC_DATABASE_URL to the
docker-compose.yml app.environment block, but the host `.env`
localhost form still forces an explicit `DATABASE_URL=...` override
when running `docker compose up -d`. Production uses Secrets Manager
and never reads `.env`, so this is dev-host-only. Carry-forward in
docs/security-audit-rls-phase-5.md Phase 6 item #7.
DEFERRED to Phase 7: dev-host-only UX issue; production uses ECS
+ Secrets Manager (never reads .env). Not pulled into Phase 6
scope (Phase 6 focused on production deploy artifacts, not
local-dev UX cleanup).

## 2026-06-02 — Dockerfile pip install failed (pytricia sdist + missing build deps)

Discovered by: implementer during PLAN_PHASE_5A.md 5A.4
Location: `Dockerfile` line 12 (former), `pyproject.toml` pytricia dep, `uv.lock` pytricia entry
Severity: medium (pre-existing)
Observation: `pytricia==1.3.0` ships sdist only — no aarch64 wheel. The
`python:3.13-slim` base image lacks `gcc` and `libc6-dev`, so
`pip install --no-cache-dir .` failed mid-build. The Dockerfile had not
been rebuilt since the slim base dropped gcc; tests run host-side against
dockerized postgres only, so the broken app build was invisible. Resolved
in 5A.4 by adding `apt-get install -y --no-install-recommends build-essential`
to the Dockerfile before pip install. Phase 6 multi-stage build will strip
build-tools from the runtime image.
Suggested action: pytricia build RESOLVED in 5A.4 (build-essential
included in runtime image). The build-tools-in-runtime hardening
regression (gcc/make/libc-dev now ship to production) is deferred to
Phase 6 multi-stage. Re-check at Phase 6 plan time as a hard prerequisite
for production deploy.
RESOLVED: 6D.1 (multi-stage Dockerfile — builder stage installs
build-essential + pip-installs deps into /install; runtime stage
copies only site-packages onto clean python:3.13-slim with no
build toolchain).

## 2026-06-02 — Redundant index ix_api_tokens_tenant after 0006 lands

Discovered by: db-reviewer during PLAN_PHASE_5A.md 5A.6
Location: `alembic/versions/0001_initial.py:256` (the redundant index) vs
`alembic/versions/0006_api_tokens_last_used_index.py:32` (the superset)
Severity: low
Observation: 0006 adds `ix_api_tokens_tenant_last_used (tenant_id,
last_used_at DESC NULLS LAST)`. Per the leading-column rule, this composite
covers all `WHERE tenant_id = $1`-equality queries that the legacy
`ix_api_tokens_tenant (tenant_id)` index served. Both indexes now exist;
Postgres may pick either at planning time. The legacy index pays write
amplification on every api_tokens insert/update for no read benefit.
Suggested action: DROP INDEX ix_api_tokens_tenant in a future cleanup
migration. Not done in 5A.6 because that commit's framing is "purely
additive index" and removing the legacy would muddy review. Safe to defer
indefinitely — write amplification on api_tokens is negligible (low row
churn) — but worth a cleanup commit in Phase 6 or a later hardening pass.
DEFERRED to Phase 7 cleanup: not pulled into Phase 6 scope; safe to
defer indefinitely (write amplification negligible).

## 2026-06-02 — UniqueViolation 409 catch in booking/modification is unreachable in serial tests

Discovered by: test-reviewer during PLAN_PHASE_5A.md 5A.7
Location: `app/api/booking.py` line ~266; `app/api/modification.py` line ~234
Severity: low (defense-in-depth code, not load-bearing for primary flow)
Observation: After 5A.7 widened the UNIQUE to `(tenant_id, request_type,
request_id)`, the only way to hit the `except asyncpg.UniqueViolationError`
catch is a concurrent-race scenario: two writers both SELECT-miss the
idempotency check, then race the INSERT. Serial test flow always returns
200 via SELECT-then-replay before the INSERT can fail. The catch path is
correct defense-in-depth but has zero test coverage.
Suggested action: add an `asyncio.gather` race test that POSTs two same-
request-id same-type payloads concurrently and asserts one returns 200
(winner) and the other returns 409 (UniqueViolation catch fires). Defer
to Phase 5B or a dedicated concurrency-test commit. Not urgent — the
catch is small, the path is straightforward, and the failure mode (500
without the catch) would be loud in production logs.
DEFERRED to Phase 7: defense-in-depth code with low failure-mode
risk; not pulled into Phase 6 scope.

## 2026-06-02 — _assert_decisions_equivalent duplicated across two test files

Discovered by: test-reviewer during PLAN_PHASE_5A.md 5A.7 cycle 2
Location: `tests/integration/test_modification_endpoint.py:33-42` +
`tests/integration/test_decisions_unique_widening.py:29-39`
Severity: low
Observation: Both files define an identical `_assert_decisions_equivalent`
helper for handling numeric(5,4) score precision in idempotency-replay
assertions. Cleanest fix: lift to a shared `tests/integration/_helpers.py`
or extend the `db` fixture so the comparison is invoked via a fixture
method. Two copies WILL drift over time.
Suggested action: lift in a Phase 5B or 5C cleanup commit. Not urgent —
both copies are byte-identical today and the helper is small.
DEFERRED to Phase 7: cleanup not pulled into Phase 6 scope.

## 2026-06-03 — Missing ALTER DEFAULT PRIVILEGES means each new tenant-scoped table needs an explicit GRANT to riskd_app

Discovered by: db-reviewer during PLAN_PHASE_6A.md 6A.6 cycle 1
Location: alembic/versions/0001_initial.py:324-326 (one-shot grant);
absence anywhere else in the chain
Severity: medium
Observation: Migration 0001 ran `GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public TO riskd_app` once. There is no
`ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ... TO riskd_app`
anywhere in the chain, so tables created by later migrations are NOT
automatically granted. 6A.6 cycle 1 missed adding an explicit grant on
the new `tenant_route_baselines` table; under riskd_app_login the
runtime role hit `permission denied for table tenant_route_baselines`
(verified empirically). Cycle 2 fixed the immediate symptom in 0011
with `GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_route_baselines
TO riskd_app;` + matching `REVOKE` in downgrade. The structural fix
— landing `ALTER DEFAULT PRIVILEGES` so every future table gets the
grant automatically — was deferred so the migration scope stayed
narrow.
Suggested action: future plan time for any commit that adds a new
tenant-scoped table MUST include an explicit GRANT to riskd_app in
the UPGRADE_SQL. As a hardening pass, a follow-up migration could
land `ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT,
UPDATE, DELETE ON TABLES TO riskd_app` (one statement; permanent for
the schema; eliminates this whole failure class). Reasonable Phase
6B/6C cleanup commit or a dedicated 6A.10 polish item.
DEFERRED to Phase 7: explicit-GRANT discipline enforced by
reviewer panel for the remainder of Phase 6 (worked correctly for
0011 and would catch any future new-table migration). The durable
ALTER DEFAULT PRIVILEGES fix is a one-statement schema-level
hardening migration deferred to Phase 7.

## 2026-06-04 — .pre-commit-config.yaml stale 8000KB cap + comment

Discovered by: senior-engineer reviewer during PLAN_PHASE_7A.md 7A.0 panel
Location: .pre-commit-config.yaml ~lines 56-62
Severity: low
Observation: The --maxkb=8000 cap on check-added-large-files was raised
in 6C.1 to accommodate scripts/replay/data/ NDJSON corpora. Those files
are now scrubbed from history and gitignored; the cap rationale is gone
and the inline comment references scripts/replay/data/ which no longer
exists.
Suggested action: at Phase 7E or Phase 8 closeout, either (a) tighten
cap back toward 500KB now that the large-file justification is gone, or
(b) update the comment to reflect scripts/calibration/ ephemera context.

## 2026-06-04 — /tmp/riskd-replay/ gitignore entry semantics observation

Discovered by: doc-reviewer during PLAN_PHASE_7A.md 7A.0 panel
Location: .gitignore line 59
Severity: low
Observation: The /tmp/riskd-replay/ entry has a leading slash so it is
anchored to repo root; it ignores <repo>/tmp/riskd-replay/, NOT the
absolute filesystem path /tmp/riskd-replay/ where 7A.2's export script
writes corpora. The script writes outside the repo so gitignore can
never see those files. The entry is harmless and defensive (against
an accidental <repo>/tmp/ directory ever appearing) but the rationale
differs from the other three entries.
Suggested action: optional — Phase 8 doc consolidation could note this
distinction explicitly. No production-correctness concern.

## 2026-06-04 — replay orchestrator narrow exception handling on success path

Discovered by: senior-engineer + code-flow reviewers during PLAN_PHASE_7A.md 7A.1 panel
Location: scripts/replay_validation.py _post_one ~lines 245-269
Severity: low (operational, not correctness)
Observation: _post_one catches (httpx.HTTPError, OSError) for the
network request, but the subsequent body.json() + body["request_id"]
field access path is NOT caught. A malformed 200 response (JSON decode
error) or unexpected response shape (KeyError on missing field) would
propagate out of the task and through asyncio.gather, aborting the
whole replay. Over a 10K-record corpus this is operationally brittle.
Suggested action: widen the except clause (or add a second one) to
include json.JSONDecodeError, KeyError, ValueError and route the
failure into error_details just like the HTTP-error branch. Pure
operational hardening; no scope creep for 7A.1 contract.

## 2026-06-04 — compare_results emits per-rule delta_pp but not decision-band delta_pp

Discovered by: senior-engineer reviewer during PLAN_PHASE_7A.md 7A.1 panel
Location: scripts/replay_validation.py compare_results ~lines 307-359
Severity: low (operator can subtract shares by eye)
Observation: The per_rule_delta entries carry delta_pp (b_share - a_share)
in percentage-points, but decision_distribution_share for a/b emit
percentage strings only — no delta computed. Plan said "FPR change,
recall change, per-rule fire rate change". Operator can compute the
3 decision-band deltas by inspection.
Suggested action: when 7B variants finalize, optionally add a
decision_distribution_delta_pp block to compare_results output.

## 2026-06-04 — replay orchestrator warmup-phase test gaps

Discovered by: test-reviewer during PLAN_PHASE_7A.md / 7C.9 amendment
Location: scripts/replay_validation.py (Phase 7C.9 warmup/measurement
methodology)
Severity: low (calibration tooling; not production code path)
Observation: Three orchestrator-side correctness invariants from 7C.9
are exercised end-to-end (via the 7D measurement run) but lack
explicit unit tests:
1. Phase barrier: warmup tasks must FULLY complete (gather()) before
   any measurement task starts. A test with httpx.MockTransport
   recording POST timestamps + asserting max(warmup_ts) <
   min(measurement_ts) would lock the barrier.
2. _replay_role strip at POST egress: _post_one strips _-prefixed
   metadata before POST. A mock-transport test asserting the POST
   body does NOT contain "_replay_role" would catch accidental
   future regressions (BookingRequest is extra="forbid"; a leak
   422s every request).
3. Per-customer chronological warmup ordering: the SQL ORDER BY
   customer_id, target_date ASC contract is documented but not
   tested. A test with non-chronological insert order asserting
   chronological output would catch SQL ORDER BY regressions.

Suggested action: defer to Phase 8 test-suite audit or open as a
post-launch follow-up. scripts/calibration/ is deleted in 7E.3;
these tests would land in scripts/replay_validation.py's existing
test_replay_validation.py if added.

## 2026-06-05 — test_case_2.py::test_unfamiliar_ip_against_established_customer_blocks_under_layer2 missing 2 rules from the case-2 5-rule compound

Discovered by: implementer during PLAN_PHASE_8A.md 8A.1 (validation step)
Location: tests/integration/test_case_2.py:207
Severity: medium
Observation: The test asserts that 5 specific case-2 rules fire as a
compound — `ip_fully_new_for_customer`, `unfamiliar_ip_country_for_origin`,
`api_booking_from_unfamiliar_asn`, `locked_customer_unfamiliar_ip`, and
`cloud_api_customer_deviation_iptype`. Against the actual current HEAD
of feat/refactor (both pre- and post-Phase-8A squash — verified by
git-stash-and-rerun), only 5 different rules fire and the two
locked-customer-baseline rules (`locked_customer_unfamiliar_ip` and
`cloud_api_customer_deviation_iptype`) are missing. The decision
outcome remains BLOCK (the test still asserts BLOCK and that
assertion presumably passes; the missing-rule assertion is what
fails). The 2 missing rules are baseline-dependent; possible causes:
(a) a derivation regression somewhere between 7C.7 (where the test
landed) and 7C.13 (most recent commit before Phase 8) that broke
the locked-customer derivation paths, (b) a test-setup drift where
the baseline state no longer triggers the lock condition the
rules predicate on, or (c) the test's compound assertion was
over-specified at 7C.7 time and a subsequent semantic refinement
intentionally narrowed which rules fire.
Suggested action: investigate during 8B test audit OR defer to
post-launch. NOT caused by 8A squash (schema-equivalent verified).
