# cloudip parser duplicated across both Go services

The CIDR/IP-list parser lives in **two** near-identical copies — each a
standalone `parseInto(reader, ranger)` called from its package's `Load`:

- [services/async-worker/internal/cloudip/cloudip.go:114-138](../../services/async-worker/internal/cloudip/cloudip.go#L114-L138)
- [services/rules-engine/internal/cloudip/loader.go:76-101](../../services/rules-engine/internal/cloudip/loader.go#L76-L101)

Both parse the same upstream format (line-per-entry, `#` comments, blanks
skipped, CIDR or bare IPv4/IPv6 → `/32`/`/128`). Left duplicated because the
two services are separate Go modules with no shared parent; seeding `libs/go/`
is an ADR-level decision deferred by the May 2026 audit (Item 6 of
`docs/audits/code-audit-2026-05-followups.md`).

## Rule: any upstream-format change MUST update both copies in one PR

If the upstream list format changes (new separator, metadata columns, IPv6
encoding tweak, comment syntax, anything), update **both** files in the same
commit. Reviewing only one will silently misclassify cloud IPs in the other
service. Before touching either copy run:
`grep -rE 'parseInto|net.ParseCIDR.*scanner' services/`.

## Re-open the libs/go/ gate when

- A 3rd cross-service shared-Go-code candidate appears, or
- The cloudip upstream format changes, or
- A future audit shows the two copies have drifted.

Then revisit Items 5 + 6 in the audit and pick between go.work, replace
directives, or a single-module restructure.
