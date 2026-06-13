# REFACTOR_PLAN — Dead-Capability Audit

Pass: dead-capability-audit · branch `feat/refactor` · HEAD `6dbe642` (PBL D6)
Runs AFTER PBL D-series, BEFORE the doc-staleness audit and any `v*` tag.
Phase 1 findings: `/tmp/dead-capability-findings-01.md` (the seed for Part A).

## Decisions absorbed

| # | Decision | Resolution |
|---|---|---|
| 1 | Cost-bearing dead fields (`customer_distinct_ips_30d`, `velocity_user_30d`) framing | **Keep-and-wire, note cost.** Document both as post-launch keep-and-wire; flag the wasted DB round-trip as a Phase 9 cost note. No code change. |
| 2 | IP2Proxy enrichment suppressing default (`is_vpn/is_proxy/is_tor=False` on failure) | **Dormant — classify, no fix.** Documented-intended + already observable (WARNING logs + `/health degraded`). Audit doc records it as classified-not-fixed. |
| 3 | Echo keep-and-wire set into `docs/calibration-backlog.md` now? | **Audit doc only.** The audit doc is the declared Phase 9 seed; no backlog edit this pass. |
| 4 | Commit strategy | **Atomic** (one logical change per commit). |
| 5 | Structural fixes (Part B) to land this pass | **None.** Phase 1 step 5 found zero unambiguous breakage. Part B is empty. |
| 6 | Discard-candidates to remove | **None.** No field/rule clears constraint #2's bar. All deferred-as-latent or keep-and-wire. |

## Outcome shape

**Part A only.** Phase 1 found zero fix-eligible structural bugs, so no code changes. The pass produces one committed classification document plus one tangential ledger entry. No rule/field removed; no weight/threshold/maturity/band changed; PBL D-series surface untouched.

---

## Part A — Classification deliverable

### Commit A1 — `docs/audits/dead-capability-audit.md` (classification doc)

**Changes**
- New file `docs/audits/dead-capability-audit.md` containing, from `/tmp/dead-capability-findings-01.md`:
  1. **Field partition** — all 77 `ALLOWED_CONTEXT_FIELDS` are computed; 0 constants (with the proof: string RHS are `ast.Constant`, not `Name`).
  2. **Two-layer consumption graph** — 63 directly-consumed; the 14 dead-to-direct-rules table with transitive/scoring resolution; the 5 genuinely-dead set; the stale-example correction (symmetric triangle → asymmetric outbound chain).
  3. **Per-field classification table** — disposition (keep-and-wire / keep-as-latent) + rationale for all 14, with the 5 dead-capability fields called out.
  4. **Cost-bearing subset** — `customer_distinct_ips_30d`, `velocity_user_30d`; the DB round-trip each adds; keep-and-wire-with-cost-note framing (decision #1).
  5. **Inert-rule analysis** — empirical degradation statement (per-record JSON scrubbed; aggregate-only); structurally-latent-in-replay rules (8 modification, 4 previously-rejected, 2 cold-start, case_3_compound) as keep-as-latent; Layer-1 shadowing result (no fully decision-inert rule); UNDETERMINED markings where data is absent.
  6. **Structural-bug list** — the three sub-classes checked, all clear; the enrichment-suppression item recorded as bug-vs-dormant → **dormant, classified-not-fixed** (decision #2). Conclusion: zero fix-eligible bugs.
  7. **Hand-off** — to the doc-staleness audit (stale transitive example; field-redundant exposures) and to Phase 9 (keep-and-wire seed). Discard-candidates: none.

**Tests** — none (doc-only; no code path changes). Validation is reviewer correctness of the consumption-graph reasoning, not a test.

**Validation**
- `git diff --stat` shows only the new doc.
- Re-run the Phase 1 discovery snippet (`collect_names` over `rules.yaml` vs `ALLOWED_CONTEXT_FIELDS`) and confirm the doc's 14-field dead-to-rules list + 63-consumed count still match HEAD (guards against the doc drifting from code at write time).
- Markdown renders (no broken tables/anchors).

**Review routing** — Doc-only path → **doc-reviewer + senior-engineer**. Senior-engineer validates the consumption-graph reasoning and the dead/transitive/cost classifications (not just prose); doc-reviewer validates structure/accuracy/links. Merge gate per CLAUDE.md doc path (PUBLISH/MINOR TWEAKS proceed; NEEDS EDITS/REJECT fix first). Iterate ≤3 cycles.

*No Declared breaks subsection — this commit introduces no transitional state.*

### Commit A2 — `.claude/BUGS.md` tangential entry

**Changes**
- Append one structured BUGS.md entry (per CLAUDE.md tangential-handling format): `phone_prefix_stats` is never populated (no `add_observation` call site passes `phone_prefix`), and `email_domain_stats` is populated on the booking path but not the feedback path; neither feeds any `ALLOWED_CONTEXT_FIELDS` field. Severity: low. Suggested action: Phase 9 — wire consistently or document as reserved baseline dimensions.

**Tests** — none.

**Validation** — `git diff` shows only the BUGS.md append; entry matches the required heading/fields format.

**Review routing** — ledger append, no decision content, no code → **triage-gate-trivial** (pre-commit hooks still run; commit footer `Review: triage-gate-trivial`).

---

## Part B — Structural-bug fixes

**Empty.** Phase 1 step 5 found zero unambiguous breakage. The one suppressing-default candidate (IP2Proxy enrichment) is dormant-by-design and already observable; recorded in the audit doc as classified-not-fixed per decision #2. No deferred-ambiguous bug to send to BUGS.md (the enrichment item is decided, not ambiguous).

---

## Phase 3 execution notes (per CLAUDE.md 6-step cycle)

- Land A1 first (it is the deliverable even with zero fixes), then A2.
- After each commit, post a one-line summary referencing the finding section.
- DO NOT: remove any rule/field; change any weight/threshold/maturity/band; wire keep-and-wire fields; touch the PBL D-series migrate surface; disturb historical ledgers.
- After commits land, produce `REFACTOR_REPORT_dead-capability-audit.md`: counts (77 fields audited; 14 dead-to-direct-rules → 5 dead-capability of which 2 keep-and-wire-cost-bearing / 1 keep-and-wire / 2 keep-as-latent; 81 rules audited, inert/latent set enumerated, 0 fires-but-never-decides, 0 discard; 0 structural bugs found/fixed, 0 deferred), reviewer-caught corrections, and the hand-off list to the doc audit + Phase 9.

This plan file is a working artifact (not committed); only `docs/audits/dead-capability-audit.md` and `.claude/BUGS.md` are committed.
