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
