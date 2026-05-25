# Shared review mechanics

> Loaded by every reviewer agent (senior-engineer, security-auditor,
> code-flow, test-reviewer, db-reviewer, doc-reviewer) before they begin.
> Covers git command conventions, diff-reading rules, Plan Context check
> order, and output format expectations.
>
> Reviewer-specific dimensions, verdict scales, FreightSentry patterns
> (C1/C2/C3, D1/D2/D3, P1/P2), recurrence citation rules, and safety
> carve-outs remain in each agent's own file.

## Git commands

Run `git diff HEAD`, `git diff --cached`, and `git status` directly — do
not use `cd && git`. If you need to scope to a subdirectory use
`git -C <path>` instead.

For new untracked files, `git diff` produces no output. Run `git status`
to find them, then use the Read tool to read the full content. Once a
new file is staged with `git add`, `git diff --cached` will display it.

Always read every changed file fully — do not skim. The diff alone may
miss context (imports, surrounding control flow, comments above the
hunk) that is load-bearing for the finding.

## Plan Context check order

Apply this check order whenever the invocation prompt includes a `Plan file:` reference:

1. **Declared breaks (preferred)**: if the current commit section in the plan includes a `Declared breaks` subsection, consult it. Findings whose scope matches a declared-break entry go into the "Plan-suppressed" subsection of your output, citing the declared scope and the resolving commit number.

2. **Plan-file inference (fallback)**: if no `Declared breaks` section is present AND the diff suggests a transitional state (orphan functions, unused fields, dangling references, single-implementation interfaces, missing wiring), fall back to reading the current-commit + upcoming-commits sections of the plan for resolving changes. Suppress findings only when an upcoming commit clearly resolves the state. State the inference explicitly in the "Plan-suppressed" subsection (e.g., "would have flagged X as dead code, but inferred plan commit N+2 wires it up").

3. **No suppression**: if neither (1) nor (2) applies, flag findings normally.

The fallback (step 2) ensures unforeseen transitional states still get caught by inference. Declared breaks (step 1) is the optimized path for known-at-plan-time transitional states.

If `Plan file: none`, this dimension does not apply — review as a standalone change.

### Safety carve-outs (each reviewer's own file lists its specific carve-out)

The Plan Context check order above does NOT excuse any of the following — each reviewer's own file states which class applies to it:

- **test-reviewer** never plan-suppresses false-pass tests (test that passes when the production code is broken)
- **security-auditor** never plan-suppresses currently-exploitable vulnerabilities (a working endpoint without auth ships *exploitable* at commit N, even if commit M "fixes" it)
- **db-reviewer** never plan-suppresses dangerous locks or data-loss risk in a migration that runs in production (a migration that runs is a permanent action)
- **doc-reviewer** never plan-suppresses stale-on-arrival docs (a doc that claims X exists when X does not exist at the end of this commit and no follow-up commit creates X within a short window)

## Output format conventions

These rules apply to every reviewer's output. They replace the
previously-duplicated bullets in each reviewer's "Rules" section.

- **Omit empty sections**: skip any heading or subsection that has zero
  findings. Do not write "No issues found" or "None" — absence is the
  signal. The verdict line and Summary convey overall assessment;
  intermediate empty sections add noise without information. The
  verdict line itself is always present.

- **Materiality threshold for Suggestion-tier findings**: flag a
  suggestion only if the operator would plausibly act on it. Routine
  style preferences, theoretical improvements without concrete benefit,
  and "you could also do X" alternatives that don't add value should
  not be flagged. Critical and Important tier findings have no
  materiality threshold — always flag. The threshold applies only to
  the lowest-severity tier.
