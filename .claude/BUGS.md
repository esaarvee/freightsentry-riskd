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

## 2026-06-09 — IP2Proxy LITE actual extracted BIN is 1.5 GiB, not the ~50 MB stated in the brief

Discovered by: implementer during Pattern B-lite Amendment 2 F3 verification
Location: REPORT_INFRA_CFN.md Part 2 upstreams table + docs/verification-pattern-b-lite.md V-9 row "IP2Proxy LITE PX11 (extracted BIN) 20 MB / ~50 MB"
Severity: low (caught pre-execution; plan recalibrated)
Observation: Live verification of `/Users/drshott/PX11.zip` reports the
inner `IP2PROXY-LITE-PX11.BIN` member at 1,610,484,245 bytes (1.5 GiB)
uncompressed; the ZIP itself is 82 MB. The brief sourced "~50 MB" from
either an older IP2Proxy LITE release or a stale reference. The
discrepancy is ~32×. Plan amendments: raw ZIP floor 20 MB → 30 MB;
new extracted-BIN floor 500 MB; `atomic_replace` signature split into
bytes-form + streaming-form (`atomic_replace_stream`) since loading
1.5 GiB into a Python `bytes` object would OOM the refresh task on
small Fargate sizings.
Suggested action: future verification-phase rows for binary-data
sources should pull live file size via a one-shot upstream probe
rather than echoing brief-sourced approximations. Especially relevant
for any source whose post-extract size exceeds 100 MB (the implicit
threshold above which in-memory handling is no longer safe).

## 2026-06-09 — IP2Location download endpoint enforces 5/24h per-token quota

Discovered by: implementer during Pattern B-lite F3 verification
Location: https://www.ip2location.com/download/?token=<TOKEN>&file=PX11LITEBIN
Severity: low
Observation: After three probe attempts within ~15 minutes on
2026-06-09, the endpoint returned a 56-byte ASCII body starting
`THIS FILE CAN ONLY BE DOWNLOADED 5 TIMES WITHIN…` (truncated; full
window is 24h). Quota is per-token, not per-IP. Implications for
Pattern B-lite: refresh cadence 1×/24h leaves 4 slots/day for
operator-side ad-hoc probes — generous margin. The rate-limit
response is plain ASCII (not HTML, not redirect to /log-in) and is
distinguishable by prefix-match. C1 `refresh_ip2proxy` adds an
explicit `rate_limited` failure_class for this case so ops
dashboards can split "throttle" from "broken upstream."
Suggested action: none for code; runbook section in C6 already
documents the cadence. Possible follow-up: alert on
`enrich.refresh.failure` with `failure_class="rate_limited"`
firing more than once per 24h (signals a token-share collision
or runaway operator probing).

## 2026-06-08 — scripts/fetch_enrichment.py output filenames don't match Enricher loader

Discovered by: implementer during Pattern B-lite verification phase
Location: scripts/fetch_enrichment.py:58 (writes `aws.json` / `gcp.json`)
vs app/enrich.py:163 (reads `{provider}.cidr`)
Severity: low
Observation: The sync refresh script writes the raw upstream JSON for
AWS/GCP (`aws.json`, `gcp.json`) but the `Enricher._load_cloud_cidrs`
loader expects `aws.cidr` / `gcp.cidr` (newline-separated CIDR list,
one per line). The script has either never been run end-to-end with
the loader OR was paired with an out-of-tree post-process step that's
been lost. Pattern B-lite's new async module writes `.cidr` (after
parsing IPv4 prefixes out of the JSON), so the launch-blocker is
resolved either way. The sync script remains stale code.
Suggested action: reconcile post-Pattern-B-lite — either delete
`scripts/fetch_enrichment.py` (Pattern B-lite supersedes it) or
extend it with the same JSON-to-CIDR parsing logic so it stays
viable as an out-of-process cron fallback.

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

## 2026-06-12 — defer entrypoint refactor + 3-secret DATABASE_URL composition

Discovered by: implementer during PBL D5 (plan dapper-percolating-glade.md)
Location: infra/ecs-task-definition.json, infra/ecs-task-definition-migrate.json, alembic/env.py
Severity: low
Observation: PBL D1–D5 wired the deploy pipeline to inject DB_MASTER
(JSON) as the migrate task's master DSN and read DATABASE_URL as the
single source of truth for the `riskd_app_login` password. A future
refactor could go further: the runtime app's container could compose
DATABASE_URL itself from three smaller secrets (master credentials,
endpoint, runtime password) at entrypoint time — removing the
manual A.5 step in the runbook where the operator constructs and
stores the full DSN. Skipped in PBL D-series to keep the scope
tight: PBL D-series is "automate migrations and rotate the
riskd_app_login password automatically"; entrypoint composition is
a separate concern with its own design surface (entrypoint script,
how the app handles transient secret-fetch failures, whether to
keep the existing single-secret path for backwards-compat).
Suggested action: schedule as a follow-up after PBL D-series ships
and the auto-migration path has been exercised against the test
region for a few deploys.

## 2026-06-12 — CFN role names env-suffixed but task-defs use unsuffixed names

Discovered by: senior-engineer-reviewer during PBL D5 cycle 1
Location: infra/cloudformation/freightsentry-riskd.yml (RoleName uses `-${Environment}` suffix) vs infra/ecs-task-definition.json + infra/ecs-task-definition-migrate.json + .github/workflows/deploy.yml (use bare `freightsentry-riskd-task-exec` / `freightsentry-riskd-task` / `freightsentry-riskd-migrate-exec`)
Severity: medium
Observation: The CFN template names IAM roles with a `-${Environment}`
suffix (e.g. `freightsentry-riskd-task-exec-${Environment}`,
`freightsentry-riskd-migrate-exec-${Environment}`). All three task
definitions and the deploy workflow string-build ARNs against the
unsuffixed role name. If the deployed CFN stack has any non-empty
Environment value, register-task-definition will fail with "role does
not exist" until the operator reconciles. This drift pre-existed PBL
D5 (the runtime task-def has the same issue with TaskExecutionRole +
TaskRole) — PBL D5 amplifies the surface by one (the new migrate
exec role). The fact that the deploy presumably worked before
suggests the stack was deployed with Environment="" or the operator
created unsuffixed aliases — either way, undocumented.
Suggested action: either (a) make the task-defs read role ARNs from
CFN outputs via the workflow (one extra describe-stacks per deploy)
or (b) drop the `-${Environment}` suffix from the CFN RoleName fields
so the IDs match the task-defs. Option (b) is the lower-friction fix.
Verify against the actually-deployed stack first to avoid surprising
a working setup.

## 2026-06-13 — phone_prefix_stats never populated; email_domain_stats booking-only

Discovered by: dead-capability-audit during REFACTOR_PLAN_dead-capability-audit.md Phase 1
Location: app/baseline.py (add_observation phone_prefix/email_domain params) vs app/api/booking.py:208 + app/api/feedback.py:420 (call sites)
Severity: low
Observation: CustomerBaseline.add_observation accepts phone_prefix and
email_domain params, but no call site passes phone_prefix (both the
booking endpoint and the feedback fold omit it), so phone_prefix_stats
is never written. email_domain_stats is populated on the booking path
(booking.py passes email_domain_val) but NOT on the feedback fold
(feedback.py omits email_domain). Neither stat-dict feeds any
ALLOWED_CONTEXT_FIELDS field, so this is a latent baseline-dimension
gap, not a dead rule-field — no current scoring impact. Surfaced while
mapping the two-layer consumption graph.
Suggested action: Phase 9 — either wire phone_prefix/email_domain
consistently across both call sites (and add consuming rules) or
document both stat-dicts as reserved/unused baseline dimensions.

## 2026-06-13 — Pre-existing full-suite test failures (integration isolation + scoring; not in the unit gate)

Discovered by: doc-staleness-audit (during the operator-authorized "fix pre-existing tests first" detour that unblocked the unit gate)
Location:
  - tests/integration/test_per_tenant_maturity_overrides.py (5): test_default_thresholds_score_is_baseline, test_maturity_age_days_override_makes_younger_customer_mature, test_maturity_shipments_override_reduces_threshold, test_combined_overrides_score_matches_expected, test_empty_config_tenant_uses_defaults
  - tests/integration/test_feedback_chain_e2e.py (2): test_chain_origin_previously_rejected_fires_on_next_booking, test_chain_email_previously_rejected_fires_on_next_booking
  - tests/integration/test_concurrent_baseline_writes.py (1): test_concurrent_booking_and_feedback_serialise
  - tests/unit/test_enrichment_refresh.py::TestCowConcurrencyInvariant::test_log_tick_summary_counts (1) — UNIT test, but fails ONLY when integration tests run first in a full `pytest tests/` run; passes in `pytest tests/unit/` (the pre-commit gate).
Severity: medium
Observation: All of these FAIL at HEAD baseline (commit bf9c881, before the
alembic-stub + enrichment-guard test fixes) and are UNRELATED to those fixes
— verified by a stash/baseline diff: full suite went 25 -> 9 failures, every
one fixed was a target of this work, zero regressions introduced. The 9
leftovers are NOT in the pre-commit gate (`pytest tests/unit/` = 937 passed);
they only surface in a full `pytest tests/` run. The maturity failures assert
a clean baseline score (e.g. `assert resp["score"] < 0.05`) but observe a
saturated 1.0, with `enrich.cache_hit` for IPs seeded by other tests — i.e.
cross-test state leaking through the shared Postgres DB. This looks like a
test-isolation gap (no per-test truncation / fresh-DB in the ad-hoc
docker-compose DB used here), not a production-logic bug — but it is NOT
root-caused yet. `test_log_tick_summary_counts` is purely an ordering-pollution
artifact (global state mutated by an earlier integration test).
Suggested action: post-phase — investigate integration-test isolation
(per-test DB truncation or transactional rollback fixtures), then re-run the
full suite against the canonical harness to confirm these are environment/
isolation artifacts vs. real regressions. Do NOT block the doc-staleness phase
on them (out of scope; not in the unit gate).

## 2026-06-13 — Dependency lock drift: uv.lock pins structlog 25.5.0, pip resolves 26.1.0

Discovered by: test-soundness pass (T3 structlog review) during REFACTOR_PLAN_test-soundness.md commit 3
Location: uv.lock vs pyproject.toml (and .github/workflows/test.yml which installs via `pip install -e ".[dev,test]"`)
Severity: low
Observation: uv.lock pins structlog==25.5.0, but a fresh `pip install -e
".[dev,test]"` (the path the Dockerfile and CI use — uv.lock-based
reproducible install is deferred per 6D.1) resolves structlog==26.1.0.
The two versions' internals relevant to the T3 fix
(BoundLoggerLazyProxy at structlog._config, cache_logger_on_first_use
bind override) are identical, so the harness fix is stable across both.
But the broader lock-vs-installed drift means CI and local envs do not
match uv.lock — the same class of risk .ai/conventions.md calls out for
the ruff pre-commit rev. Likely affects more than structlog.
Suggested action: Phase 9 — adopt uv.lock-based install in CI/Dockerfile
(or re-resolve uv.lock to current and pin the pre-commit/test envs to it).

## 2026-06-13 — CI: snyk action pinned to floating @master ref

Discovered by: test-soundness pass (CI security review) during REFACTOR_PLAN_test-soundness.md CI commit
Location: .github/workflows/test.yml (snyk job, `uses: snyk/actions/python@master`)
Severity: low
Observation: The Snyk job pins a third-party action to a moving `@master`
ref, which runs in CI with access to SNYK_TOKEN — a compromised upstream
master would execute with that secret. Pre-existing (not introduced by
the two-job CI split; that diff only removed a comment from the job).
First-party actions (checkout@v4, setup-python@v5) are pinned to major
tags. Suggested action: pin snyk/actions/python to a release tag or
commit SHA. Out of scope for the test-soundness pass.

## 2026-06-13 — auth.py docstring asserts superuser connecting-role; runtime is riskd_app_login

Discovered by: security-auditor during comment-cleanliness pass (Commit 4)
Location: app/auth.py:15-18
Severity: low
Observation: The module docstring (de-phased this pass, claim preserved from the
pre-existing "Phase 1 superuser" text) states the connecting role is the postgres
bootstrap superuser that bypasses RLS, so the api_tokens lookup needs no RLS-exempt
path. But docker-compose.yml, .env.example, and .ai/decisions.md document the runtime
role as `riskd_app_login` (LOGIN, no BYPASSRLS). The docstring is stale relative to the
now-documented runtime role; the comment-cleanliness pass only removed the phase IDs and
did not change the (pre-existing) claim, since correcting it requires confirming the
actual auth-path RLS behavior under the non-superuser role.
Suggested action: confirm api_tokens read behavior under riskd_app_login (api_tokens has
no RLS per the 0001 squash, so the lookup likely works without an exempt path) and rewrite
the docstring to the verified current state.

## 2026-06-13 — models.py shipment currency default is USD while project default is CAD

Discovered by: comment-cleanliness pass (Commit 2)
Location: app/models.py (ShipmentData.currency default) + app/tenant_config.py DEFAULT_VALUE_CAPS
Severity: low
Observation: ShipmentData.currency Pydantic default is "USD", but the project operates
CAD-default (DEFAULT_VALUE_CAPS keyed "CAD"; allowed_currencies defaults ["CAD"]). The
comment pass left the code value untouched (comment-only scope) and only stripped the
"defaults to USD so Phase 1-3 payloads…" history. Possible latent inconsistency between the
wire-model default and the operational default currency.
Suggested action: investigation needed — confirm whether the USD wire-default is intentional
(backward-compat) or should be CAD.

## 2026-06-13 — test_schema_golden fails on host pg_dump 18 (golden generated with pg16)

Discovered by: comment-cleanliness pass (Commit 6 validation)
Location: tests/integration/test_schema_golden.py
Severity: low
Observation: The golden test prefers a host pg_dump when present. Host pg_dump is 18.3
while tests/golden/schema.sql was generated with the container's pg16; pg18 emits
slightly different DDL formatting, so the test fails on the host path. Confirmed the live
schema MATCHES golden when dumped via the container pg16 (docker compose exec postgres
pg_dump), so the schema is correct — this is purely a pg_dump-version skew in the host
test path, not a schema defect. Independent of the comment-cleanliness edits (the test
dumps the live DB, which the edits do not touch).
Suggested action: pin the test to the container pg_dump (or a pinned client version), or
regenerate the golden with the supported client version, so the host path is deterministic.

RESOLVED (golden-and-archival pass, 2026-06-15): `tests/integration/test_schema_golden.py`
`_capture_schema_dump` now prefers the docker-compose `postgres` container (canonical pg16),
using the host `pg_dump` only as a fallback, so the host major version is irrelevant on the
primary path. Belt-and-suspenders: the normalizer also drops top-level session `SET <guc> = …;`
preamble lines (the class pg18 varies via `SET transaction_timeout = 0;`), and the golden was
regenerated to match (delta: 11 session-`SET` lines removed, zero DDL change). On a host-only
fallback with a non-canonical major, the failure message now flags the possible version artifact
and points to the container command. Verified green on both the container pg16 and host pg18 paths.
See also the duplicate 2026-06-15 entry below.

## 2026-06-13 — stale test identifier: test_allowed_context_fields_count_is_76_after_6a8 asserts ==77

Discovered by: test-reviewer + code-flow during comment-cleanliness pass (Commit 7)
Location: tests/unit/test_context_tenant_config_passthrough.py (function name) and the
similarly-named test_rule_count_after_6a9
Severity: low
Observation: The test function name embeds a stale count ("_is_76_") and a phase token
("_after_6a8") while the assertion checks == 77. Pre-existing (confirmed identical at HEAD);
the comment-cleanliness pass intentionally did NOT rename it (renaming an identifier is a
code change, out of scope for a comment/docstring-text pass). The _PHASE_2B_ADDITIONS
constant name is a related out-of-scope phase-token-in-identifier case.
Suggested action: rename to a count-agnostic, phase-agnostic identifier (e.g.
test_allowed_context_fields_present / _expected_count) in a separate code commit.

## 2026-06-13 — stale migration numbers (0007/0008/0009/0011) in integration test comments

Discovered by: senior + doc reviewers during comment-cleanliness pass (Commit 8)
Location: tests/integration/test_rls_enforcement_under_riskd_app.py, test_modification_endpoint.py,
test_booking_e2e.py (comments referencing migrations 0007/0008/0009/0011)
Severity: low
Observation: Several integration-test comments cite migration numbers 0007/0008/0009/0011, but
the chain was squashed 11->5, so only 0001-0005 exist (riskd_app_login is created in 0005, not
0008; RLS-on-auth was never created so there is no 0009-drop). These numbers are present on both
sides of the diff (pre-existing, not introduced by the comment-cleanliness pass) and correcting
them is a factual-accuracy fix of a different class than ID/history removal — out of scope here.
Suggested action: update the comments to reference the squashed migration numbers (0001-0005), or
describe by content, in a follow-up.

## 2026-06-15 — Pre-existing case-2 lock-in rule failure (date-sensitive)

Discovered by: implementer during REFACTOR_PLAN_platform-shipment-id.md Commit 1
Location: tests/integration/test_case_2.py::test_unfamiliar_ip_against_established_customer_blocks_under_layer2
Severity: medium
Observation: This test fails with `missing: {'cloud_api_customer_deviation_iptype',
'locked_customer_unfamiliar_ip'}` — the two `customer_locked_cloud_api`-gated rules
do not fire against a directly-seeded locked baseline (cloud_share=1.0, api_share=1.0,
value_n=20). PROVEN PRE-EXISTING and independent of the platform-shipment-id change:
reproduced identically on pure HEAD code (commit 04f0c15, no uncommitted changes) +
HEAD schema (migrations 0001-0005) on a fresh scratch DB. The platform-shipment-id
diff touches only models/booking-persist/modification-cross-check/_booking_from_prior_shipment;
`build_context`, `score`, `rules.yaml`, `dsl` are byte-identical to HEAD, so `triggered_rules`
is computed identically. Almost certainly date-sensitive (surfaced as today rolled to
2026-06-15; the seed uses hardcoded stat `last="2026-05-20"` + `now() - interval '6 days'`
and decay against `date.today()`, interacting with the `customer_observations >= 20`
clause / lock-gate derivation). The test itself documents that failure = operator
escalation, NOT a weight tune.
Suggested action: investigation needed — determine whether the lock-gate / customer_observations
derivation has a date/decay edge at the seed boundary; do NOT tune weights. Out of scope
for the platform-shipment-id pass (not caused by it).

RESOLVED (test-harness artifact, not a detection regression): root-caused to a cross-timezone
date-source mismatch in the test seed, NOT a Layer-2 logic bug. `_seed_established_customer`
anchored `decay_anchor_date = current_date` (Postgres, UTC) with `value_n = 20` sitting exactly
on the `customer_locked_cloud_api` gate's `value_n >= 20.0` boundary, while scoring decays to
`as_of = date.today()` (Python, local tz). In the local-time window where the UTC date still
lags local by a day (here ~00:00-05:30 IST), `decay_to` applied ~1 day of default-half-life
(90d) decay: `value_n = 20 * exp(-ln2/90) ≈ 19.85 < 20.0`, dropping BOTH the lock gate and the
`customer_observations >= 20` clause, so the two lock-in rules silently stopped firing. Proven
empirically: the test FAILED at 00:01 IST (UTC=prev day) and PASSED at 09:32 IST (UTC=same day),
with no code change. Fixed by anchoring the seed's `decay_anchor_date` to Python `date.today()`
(the same source build_context uses), matching the existing convention documented in
tests/conftest.py (`_seed_customer_with_baseline`) and test_context.py. Same one-line fix applied
to the two sibling boundary seeds (test_baseline_gating.py, test_admin_endpoints.py) that used
`current_date`. Detection logic was correct throughout. Swept the whole suite + `alembic/`:
these three were the ONLY `decay_anchor_date = current_date` seed sites; no others remain (the
residual `current_date` occurrences are explanatory comments). No further fix outstanding.

## 2026-06-15 — Golden-schema test pg_dump version skew (host 18 vs container 16)

Discovered by: implementer during REFACTOR_PLAN_platform-shipment-id.md Commit 1
Location: tests/integration/test_schema_golden.py (_capture_schema_dump prefers host pg_dump)
Severity: low
Observation: The host pg_dump is 18.3; the docker postgres container is 16.13. The committed
`tests/golden/schema.sql` is generated via the container's pg_dump 16 (the docstring's canonical
command uses `docker compose exec postgres pg_dump`). pg_dump 18 emits an extra
`SET transaction_timeout = 0;` line absent in 16, so regenerating/validating the golden via the
host binary on this machine introduces a spurious one-line diff. The golden for this pass was
(correctly) regenerated via the container pg_dump 16 to match the canonical lineage; the golden
gate must be exercised via the container path (or a pg_dump-16 host binary), which is what CI does.
Suggested action: pin the golden test to the container pg_dump (or a 16.x host binary), or make
the normalizer strip version-variable `SET ...` lines, so host-18 dev machines don't see phantom
golden diffs.

RESOLVED (golden-and-archival pass, 2026-06-15): both suggested actions applied. `_capture_schema_dump`
is now container-first (canonical pg16) with a host fallback, and `_normalize` drops top-level
session `SET <guc> = …;` lines (including pg18's `SET transaction_timeout = 0;`). The golden was
regenerated under the new normalizer — the only delta is removal of the 11 pg16 session-`SET` lines,
no DDL change. A host-fallback dump with a non-canonical pg_dump major now emits a version-skew
diagnostic instead of a raw diff. Verified normalized output identical on the container pg16 and
host pg18 paths. (Same root cause as the 2026-06-13 entry above; both resolved by this fix.)

## 2026-06-26 — Onboard task injects functionally-unused HMAC_SECRET

Discovered by: security-auditor during onboard-task plan Commit 3 (ecs-task-definition-onboard.json)
Location: infra/ecs-task-definition-onboard.json (secrets), app/config.py (Settings.hmac_secret)
Severity: low
Observation: The one-off onboarding task def injects HMAC_SECRET solely because
app.config.Settings() hard-requires `hmac_secret: str` (no default) for construction via
get_settings(); scripts/tenant_onboard.py never exercises HMAC. Injecting the egress-PII HMAC key
into a task that doesn't use it slightly widens that key's blast radius (a compromise of the
onboard container's env/logs would expose it). Acceptable for now: same key the long-running app
container already holds, and the onboard task is non-internet-facing and short-lived.
Suggested action: make hmac_secret optional/lazy in Settings (or add a script-scoped settings
accessor that only requires database_url) so the onboard path need not carry HMAC_SECRET. Weigh
against the app's current startup guarantee that HMAC is configured before serving.

## 2026-06-26 — Role-name env-suffix mismatch: CFN (suffixed) vs task-def JSON / deploy workflow (env-less)

Discovered by: implementer during onboard-task CloudFormation wiring
Location: infra/cloudformation/freightsentry-riskd.yml (RoleName !Sub '...-${Environment}') vs
  infra/ecs-task-definition*.json + .github/workflows/deploy.yml:136
Severity: medium
Observation: CFN names every IAM role with a `-${Environment}` suffix (e.g.
freightsentry-riskd-migrate-exec-production), but the task-definition JSONs and the deploy
workflow reference env-LESS names — the app/migrate/onboard task defs use
`arn:aws:iam::${ACCOUNT_ID}:role/freightsentry-riskd-task[-exec]` and deploy.yml:136 hardcodes
`freightsentry-riskd-migrate-exec` (no suffix). If the deployment uses the CFN-created (suffixed)
roles, the task-def registration / iam:PassRole would reference non-existent role names. The system
may currently run on the README's "legacy hand-applied" env-less roles, masking the gap. The new
OnboardTaskRole follows the CFN file's own (suffixed) convention for internal consistency, and the
onboard task def JSON follows the sibling app/migrate JSON (env-less) convention — i.e. it matches
the existing pattern on both sides rather than introducing a new inconsistency.
Suggested action: reconcile the two paths — either drop the `-${Environment}` suffix from CFN
RoleName (single-env-per-account) or substitute the real CFN-output role ARNs into the task defs at
register time (as migrate already does for its exec role via ${MIGRATION_TASK_EXEC_ROLE_ARN}, though
that placeholder is itself populated with an env-less literal). Investigation needed to confirm which
role set production actually uses before changing either side.

RESOLVED (commits 4777a8a + this docs commit, 2026-06-26): chose the substitute-CFN-suffixed-ARNs
path (operator confirmed one-account/two-regions, so the CFN suffix must stay — role names are
account-global). All task-def JSONs (app/migrate/onboard) now use `${...ROLE_ARN}` placeholders;
deploy.yml builds them env-suffixed (`...-${ENVIRONMENT}`) for the app+migrate registers, and runbook
B.2 documents the operator-side envsubst for the onboard register. deploy.yml:136's env-less migrate
ARN is fixed. The "legacy hand-applied env-less roles" question is moot under the chosen topology.

## 2026-06-26 — readonlyRootFilesystem would break enrichment data dir

Discovered by: security-auditor + senior-engineer during enrichment-refresh fix (Commit 2, Dockerfile data-dir)
Location: infra/ecs-task-definition.json + Dockerfile:70
Severity: low
Observation: The enrichment refresh writes to /app/data/enrichment, now pre-created + chowned in the image and served off the container's writable rootfs (task def sets no volume, no readonlyRootFilesystem). If readonlyRootFilesystem: true is ever adopted for container hardening, atomic_replace's writes will fail again — the dir would need a paired writable mount (tmpfs/EFS/ephemeral) owned by uid:gid 1000.
Suggested action: If/when adopting readonlyRootFilesystem, add a writable volume+mountPoint at /app/data with uid:gid 1000 ownership; note in the deploy runbook.

## 2026-06-30 — build.yml expects a role not granted by the CFN template

Discovered by: senior-engineer during OIDC trust-policy tightening (DeployRole environment-scoped sub + ref gate)
Location: .github/workflows/build.yml:12-15 + infra/cloudformation/freightsentry-riskd.yml (DeployRole)
Severity: low
Observation: build.yml documents/uses a sub-claim shaped like `repo:<org>/<repo>:ref:refs/heads/main` (push-to-main, no `environment:`). The CFN template defines only one OIDC-assumable role, `DeployRole` (`freightsentry-riskd-deploy-<env>`), whose trust policy matches neither the old nor the new build.yml token shape. This is pre-existing — the tightening did NOT regress build.yml; build.yml's token was already unsatisfiable against DeployRole. Needs reconciliation between build.yml's role expectation and the CFN-managed role set.
Suggested action: investigation needed — either build.yml shouldn't assume DeployRole, or a separate build-scoped role is missing from the template.
