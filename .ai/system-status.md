# FreightSentry deployment status (as of 2026-05-21)

Stage: prototype. Not deployed to production.

Planned production launch: ~2026-07 (target; subject to platform team's schedule).

Implications for design and remediation work:
- There is no production traffic. All "compare mode" validation runs against
  synthetic load (Newman fixtures, property tests, profile generators).
- There are no production logs, no production telemetry, no production
  performance data. All performance numbers come from local benchmarks
  or staging if/when staging exists.
- The 5-month "ignore suggestions" window mentioned in feedback-loop
  design begins at production launch, not at any earlier date.
- Operator runbooks describe procedures for when those procedures
  become applicable; they do not describe currently-occurring operations.

Audit doc references to "production" in docs/archive/refactor/REFACTOR_PLAN_B34.md gates,
five-gate Commit 23 list, S-3 deployment ordering, etc. should be read as
"at production launch and after" unless context makes clear they refer to
the prototype environment.