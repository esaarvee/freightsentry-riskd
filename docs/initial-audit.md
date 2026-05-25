# FreightSentry / freight_risk — Comparative Audit & Path Forward

## Executive summary

**The bottom line:** freight_risk has empirically validated the core of what FreightSentry was trying to be, in ~15% of the code, with the same primary capability (baseline-deviation fraud detection) at 98% accuracy on the case-2 ATO. The remaining 85% of FreightSentry's code is overhead — service splits and language splits for scale you don't have, an LLM orchestration path that isn't load-bearing, async streaming infrastructure that exists for one service but adds no value at single-process scale, and audit/reviewer infrastructure that grew because the system grew.

**You don't need to choose between "rewrite from scratch" and "simplify in place."** The third option, which I'd recommend, is **promote freight_risk to the production system** by adding a thin real-time API layer over it and migrating the small set of FreightSentry capabilities that aren't yet there. This preserves the months of fraud-pattern knowledge embedded in your rules, recovers the value of your domain expertise, and stops carrying the architectural weight that's not earning its keep.

What you keep from FreightSentry: the rule definitions, the IP enrichment data sources, the schema design for customer baselines (which freight_risk already mostly inherited anyway), the reviewer panel and 6-step commit cycle, the AI documentation discipline.

What you discard from FreightSentry: the gateway/rules-engine service split, the Go re-implementations, the gRPC contract, the Redis Streams pipeline, the AI orchestration with Bedrock/Ollama and MCP tools, the trust-score subsystem, the device-fingerprint and user-agent rules (data not available for ≥1 year anyway), the global-blocked-vectors machinery, the velocity-counters-in-Redis pattern (use SQL counts), the migration squash infrastructure (start fresh schema).

Estimated effort: 4–6 weeks for a single engineer + Claude Code with the disciplined process you've built. Significantly less than the ~4-month production launch sprint as currently scoped.

---

## Part 1 — What FreightSentry does today

### System shape

Three services, two languages, ~52K lines of source + ~30K lines of tests.

| Service | Language | Lines | Purpose |
|---|---|---|---|
| Gateway | Python 3.14 / FastAPI | 4,599 | REST API, enrichment, idempotency, gRPC client |
| Rules Engine | Go 1.25 / gRPC | 17,908 | In-memory rule evaluation, velocity counters |
| Async Worker | Go 1.25 | 13,554 | Audit logging, AI analysis, feedback, blacklist sync |

Plus ~30K lines of tests, 916 lines of rules YAML, 434 lines of proto definition, 1,469 lines of architectural decision docs, 1,336 lines of reviewer agent definitions, multiple migration files, and a 642-line audit document.

### Endpoints (current)

| Endpoint | Used in your envisioned API |
|---|---|
| POST `/evaluate` | yes (renamed `/shipments/booking/evaluate`) |
| POST `/feedback` | yes (renamed `/shipments/feedback`) |
| GET `/decisions/{id}` | possibly later, not in your initial 4 |
| GET `/decisions/{id}/analysis` | no — depends on AI subsystem you're removing |
| GET `/metrics` | no — Prometheus/CloudWatch handles this |
| GET `/rules` | no — operator concern, not API |
| GET `/users/{id}/profile`, `/history` | no — operator concern |
| `/blacklist/ips`, `/blacklist/devices`, `/blacklist/sync` | no — operator concern, not API |

**~70% of the endpoint surface is operator tooling that doesn't belong in the production API.** Most of this should be cut or moved to an internal admin surface (or a future dashboard product).

### Rules

102 rules in YAML, evaluated via expr-lang DSL. Categorized into 16 buckets per the YAML comments. About 47 rules are "active" per the audit document.

Rule taxonomy:
- Hard BLOCK (short-circuit to score=1.0)
- Threat intelligence (FireHOL Level 1/2)
- IP2Proxy threat flags (BOTNET, SCANNER, SPAM)
- VPN / proxy / Tor signals
- Velocity (hourly + daily, UI + API split)
- Geographic (intercontinental jumps, country mismatches)
- Cloud IP misuse (API customers off cloud, web from cloud)
- IP rotation signals
- Value anomaly (absolute + relative)
- Contact identity (disposable email, dummy phone, unknown for customer)
- Address mismatch (origin/destination not registered)
- Account state (dormant + new IP, established + new IP, etc.)
- Trust-score-conditioned rules (a continuous trust value influences many rules)
- Device-based rules (currently disabled — data unavailable)

### Subsystems (size-weighted)

**Async worker breakdown:**
- `feedback/` — 2,525 lines (most complex single piece — rule weight learning + blocked vectors)
- `audit/` — 4,391 lines (audit stream consumer)
- `ai/` — 2,425 lines (Bedrock/Ollama orchestration with MCP tools)
- `statdict/` — 634 lines (the JSONB stat-dict format for customer baselines)
- `blacklist/` — 753 lines (PG→Redis sync)
- `emf/` — 581 lines (CloudWatch EMF metrics)
- `mcp/` — 457 lines (MCP client for AI orchestrator)
- `pii/` — 430 lines (HMAC PII hashing)

**Rules engine breakdown:**
- `scoring/` — 6,022 lines (rule evaluation, noisy-OR, account maturity, expr-lang)
- `server/` — 2,366 lines (gRPC server)
- `grpc_gen/` — 2,028 lines (generated proto stubs)
- `rules/` — 1,513 lines (rule loader, fsnotify hot-reload)
- `proxyintel/` — 936 lines (IP2Proxy PX11 + threat-feed merging)
- `lookup/` — 818 lines (general lookup infrastructure)
- `threatintel/`, `asnlookup/`, `geolookup/`, `ipproxy/`, `cloudip/` — ~3,200 lines combined

### Data model

18 tables. Most complex (column counts above 30):
- `customer_profiles` — 31 columns, including hour_histogram, dow_histogram, cloud_share_n, api_share_n, web_share_n, known_asns, known_ips, known_emails, known_phones, known_user_agents, known_email_domains, known_phone_prefixes, daily_volume Welford stats, cadence Welford stats, decay_anchor_date
- `user_profiles` — 22 columns, parallel structure for user-level baseline
- `feature_vectors` — 35 columns of derived signal flags
- `audit_logs` — partitioned monthly, stores full Context per decision

**Several tables are operator tooling, not core fraud:** `blacklist_ips`, `blacklist_devices`, `app_users`, `api_tokens`, `pending_review_vectors`, `global_blocked_vectors`, `customer_rule_weights`, `rule_versions`, `system_metrics`. Roughly half the table count is non-core.

### AI orchestration

The async worker invokes an LLM (Bedrock or Ollama) for REVIEW decisions, using a 4-tool MCP interface:

1. `get_account_history` — 90-day account summary
2. `get_ip_context` — IP activity over 30 days
3. *(two more enrichment tools — pattern is similar)*

The LLM produces a JSON analysis stored in `ai_analysis` table, not used to override the rule-based decision. **The LLM is advisory only.** It's running, consuming ~$X/month of Bedrock or holding an Ollama instance, and its output influences nothing in the live decision path. The 2,425 lines of orchestrator code do real work that does not contribute to decision quality.

### Process / governance scaffolding

- 9 reviewer agents (senior-engineer, security-auditor, code-flow, test-reviewer, db-reviewer, doc-reviewer, codex-implementer, codex-diff, aws-solutions-architect)
- 9 decision documents totaling 1,469 lines (decisions split topically: data, infra, mcp, observability, scoring, security, stack, system + main)
- 5 conventions documents
- 6-step commit cycle with diff-based reviewer routing
- Plan Context mechanism with declared-breaks
- Refactor plans + reports (REFACTOR_PLAN_*, REFACTOR_REPORT_*)
- 2 active audit documents (deep-review + deep-review-01)
- Pending B5–B8 remediation against audit-01

This scaffolding is genuinely valuable and shouldn't be discarded. It scales gracefully to a smaller codebase — most of the reviewer dimensions apply regardless of code volume. The decision docs become a smaller set when the system is smaller.

---

## Part 2 — What freight_risk does today

### System shape

Single Python package + Go re-implementation, ~13K lines total (8K Python + 5K Go).

| Component | Lines | Purpose |
|---|---|---|
| Python core | 7,006 | Ingest, enrich, score, fold, report |
| Go re-implementation | 5,375 | Performance optimization for fold/score |
| Rules YAML | 555 | 84 named rules |
| Parity test | 801 | Verify Python ↔ Go output match |

The Go re-implementation is performance optimization for the batch fold over historical data — *not* a service split. The Python CLI shells out to the Go binary for the hot path. This is duplication that exists for batch processing speed; for real-time evaluation against a Postgres-backed baseline, it goes away.

### Module breakdown (Python)

| Module | Lines | Purpose |
|---|---|---|
| `baseline.py` | 1,527 | Customer baseline fold + decay |
| `pdf_report.py` | 696 | CTO + CEO PDF generation |
| `cli.py` | 604 | Command-line interface |
| `score_runner.py` | 551 | Score loop driver |
| `scoring.py` | 480 | Rule evaluation engine |
| `signals.py` | 449 | Email/phone/address signal computation |
| `enterprise_seed.py` | 404 | Initial enterprise data load |
| `db.py` | 369 | SQLite schema + connection |
| `enrich.py` | 364 | IP enrichment (MaxMind + FireHOL + IP2Proxy) |
| `interactive.py` | 308 | Interactive CLI mode |
| `ingest.py` | 301 | CloudWatch + shipment CSV ingestion |
| `humanize.py` | 285 | Human-readable rule explanations |
| `report.py` | 270 | CSV + summary text reports |
| `engine_go.py` | 172 | Go subprocess wrapper |
| `feedback.py` | 143 | Review CSV write + read |
| `config.py` | 82 | Configuration loader |

### What it does end-to-end

1. **Ingest** CloudWatch logs + shipment CSV. Extract source IP per shipment via SNS triplet correlation.
2. **Enrich** each IP: geo (MaxMind), ASN (MaxMind), threat (FireHOL Level 1/2), VPN/proxy/Tor (IP2Proxy PX11), cloud provider (AWS/GCP/Azure/Cloudflare CIDRs). Cache per-IP enrichment.
3. **Score** each shipment against the customer's baseline (frequency-recency maps for IPs, ASNs, /24s, emails, phones, addresses, origins, destinations, countries; Welford stats for value + cadence; hour and day-of-week histograms).
4. **Decide** ALLOW (≤0.60) / REVIEW (0.60–0.80) / BLOCK (≥0.80) via noisy-OR over fired rule weights. Hard-block rules short-circuit to 1.0.
5. **Report** per-shipment CSV + summary text + JSONL of factors + CTO PDF + CEO PDF.
6. **Fold** approved + GREEN shipments back into the baseline with exponential decay weight by age.

### Baseline data structure (the part worth borrowing)

For each customer, JSONB blob with:
- Frequency-recency maps per dimension: `{key: {n: weight, r_n: rejected_weight, last: date}}` for origins, destinations, IPs, netblocks, ASNs, countries, emails (HMAC), phones (HMAC), addresses
- Welford statistics for shipment value (mean, M2)
- Welford statistics for cadence (mean inter-arrival days, M2)
- Hour-of-day and day-of-week histograms
- Cloud/residential IP type histogram
- Channel histogram (web/API split)

The `n` / `r_n` split (positive observation vs rejected observation) is the data structure that makes feedback a first-class signal. The exponential decay makes the fold idempotent (re-running with the same as-of date produces the same baseline) while aging old patterns out.

### Reports

Two PDF outputs per day:
- **CTO report** (landscape) — technical drill-down with IPs, ASNs, countries, per-shipment risk factors
- **CEO report** (portrait, ≤2 pages) — GREEN/YELLOW/RED counts, $ value-at-risk, customer exposure, 7-day trend

This is operator-facing tooling that has no analog in FreightSentry. It's surprisingly valuable for SaaS — your tenants will want this view.

### Feedback loop

Reviewer fills in `approve` / `reject` in a CSV; `risk feedback --file` loads back into SQLite. Approved YELLOW/RED shipments fold into baseline as positive observations. Rejected ones fold as anti-signals (`r_n` field). Unreviewed YELLOW/RED stay out of baseline entirely.

This is the right shape. FreightSentry has a similar pattern but routed through Redis Streams and a Go consumer — significantly more infrastructure for the same logical operation.

---

## Part 3 — Capability comparison

| Capability | FreightSentry | freight_risk | Notes |
|---|---|---|---|
| Real-time evaluation | yes | no (batch) | freight_risk needs API layer |
| Customer baseline (stat-dicts) | yes (PG JSONB) | yes (SQLite JSON) | Identical concept |
| IP enrichment | yes (4 sources merged) | yes (4 sources merged) | Same data sources |
| Hard-block rules | yes | yes | |
| Threat intelligence rules | yes | yes | |
| Velocity rules | yes (Redis-backed) | yes (SQL count) | Both work, SQL is simpler |
| Geographic rules | yes | yes + impossible-travel | freight_risk has more |
| Cloud-IP rules | yes | yes | |
| Contact identity rules | yes | yes | |
| Address mismatch | yes | yes + origin-not-registered | freight_risk has more |
| Trust score (continuous) | yes | no | Removable — see Part 5 |
| Device fingerprint | yes (mostly dead) | no | Data unavailable, dead |
| User agent rules | yes (mostly dead) | no | Data unavailable, dead |
| AI/LLM analysis | yes (advisory, ~2.4K LoC) | no | Removable — proven unnecessary |
| Feedback loop | yes (Redis stream) | yes (CSV) | freight_risk simpler |
| Approved/rejected baseline | yes (`r_n` field) | yes (`r_n` field) | Identical |
| Global blocked vectors | yes | yes (IP only) | freight_risk simpler |
| Recipient cross-customer | no | yes | Real fraud-ring signal |
| Out-of-pattern hour/weekday | no (rules reserved but empty) | yes | Worth adding |
| Cadence anomaly | yes (data tracked) | yes (rule fires) | freight_risk completes the loop |
| Modification evaluation | no | no | Planned, nowhere |
| Hot-reload of rules | yes (fsnotify) | no (restart) | Restart is fine at this scale |
| Reports (PDF) | no | yes | Worth keeping |
| Idempotency (Redis SETNX) | yes | n/a (batch) | Needed for real-time |
| Partition management | yes (audit_logs) | no | Worth keeping for audit_logs |
| Multi-tenant | partial (enterprise/customer) | single-tenant | Both need work for SaaS |

### Rule overlap

- **45 rules** common to both codebases
- **56 rules** only in FreightSentry — most depend on features not available offline (trust_score, device fingerprint, real-time velocity, dormancy state)
- **39 rules** only in freight_risk — mostly novelty signals (origin-not-registered, cross-customer recipient, impossible-travel-geo, out-of-pattern hour) and IP family classifications

Among the 56 FreightSentry-exclusive rules:
- ~12 are trust-score-conditioned (removable when trust score goes)
- ~8 are device-fingerprint conditioned (already disabled)
- ~6 are user-agent conditioned (already disabled, will stay disabled for ≥1 year)
- ~10 are global-blocked-vectors lookups (depends on that subsystem)
- ~20 are velocity-counter-conditioned (achievable with SQL count queries)

**Net unique value in FreightSentry rules:** roughly 10–15 rules carry domain knowledge not already in freight_risk. Most are velocity-related and re-implementable easily.

---

## Part 4 — Gaps in both codebases

Things missing from FreightSentry, freight_risk, or both that the redesign needs to address:

### Missing from both

1. **Modification evaluation.** No support for shipment modifications. The fraud pattern "destination changed to freight forwarder hours before pickup" is invisible to both.
2. **True multi-tenant scoping.** FreightSentry has customer/enterprise but no SaaS-tenant boundary. freight_risk is single-tenant by design.
3. **Cold-start handling for new tenants.** Both assume customer baseline exists or will fill in naturally. SaaS needs explicit cold-start behavior with universal-only signals + heightened REVIEW routing for the first 30–60 days.
4. **Per-customer threshold tuning surfaces.** Both have global thresholds. A SaaS tenant should be able to configure stricter or looser bands per their risk appetite.

### Missing from FreightSentry but present in freight_risk

1. **Recipient cross-customer overlap detection.** A drop address used by many unrelated customers is a strong fraud-ring signal. freight_risk has `recipient_used_by_many_customers` / `recipient_used_by_very_many_customers`; FreightSentry doesn't.
2. **Origin-not-matching-registered.** Compare ship_from to customer's registered address. Weak but real signal.
3. **Impossible-travel-geo.** User-level geo-velocity check. FreightSentry tracks geo but has no impossible-travel rule.
4. **Out-of-pattern hour / weekday rules.** FreightSentry tracks hour_histogram and dow_histogram in customer_profiles, but the rules to fire on them are reserved-but-empty in the YAML. freight_risk completes the loop.
5. **PDF reporting.** Daily summary outputs. Operationally useful, valuable as SaaS deliverable.
6. **Email-matches-customer-name signal.** Sophisticated string matching of email local-part against the customer's business name (the `email_matches_customer_name` function in signals.py).

### Missing from freight_risk but present in FreightSentry

1. **Idempotency.** Real-time evaluation needs deduplication on retried requests. FreightSentry uses Redis SETNX; in Postgres-only this becomes a unique constraint on request_id.
2. **Partition management for audit_logs.** FreightSentry partitions audit_logs monthly with automated rollover. At ~50K events/day this matters within a year.
3. **Velocity beyond same-day.** freight_risk's velocity rules are within-day SQL counts; FreightSentry's velocity counters span longer windows via Redis HyperLogLog. The longer-window signal is genuinely useful.
4. **Customer maturity model.** FreightSentry has an account-prior layer that downweights signals for new customers (avoiding excessive REVIEW load during cold-start). freight_risk has new-user rules but no graduated weighting curve.
5. **AI orchestration with MCP tools.** Whether this is *useful* is the question — see Part 5. The infrastructure exists if you decide to keep an LLM in the loop.

---

## Part 5 — What to discard, cannibalize, or migrate

Going piece by piece through FreightSentry, here's where each subsystem lands.

### Discard outright

| Component | Lines | Reason |
|---|---|---|
| `rules-engine` service entirely | 17,908 | Service split unjustified at <100 TPS. Rules evaluate fine in Python. |
| `grpc_gen/` (both services) | 4,000+ | No internal RPC if there's one service. |
| `cmd/freight-risk-go/` + `internal/` (freight_risk Go) | 5,375 | Performance optimization for batch — irrelevant in real-time. |
| `async-worker/internal/ai/` | 2,425 | LLM advisory output influences nothing. Proven unnecessary by freight_risk 98% accuracy without it. |
| `async-worker/internal/mcp/` | 457 | MCP client for the AI subsystem above. |
| `services/gateway/app/mcp_server.py` | 349 | MCP server for operator queries — separate concern, not core fraud. |
| Hot-reload via fsnotify | (in rules engine) | Restart is 5 seconds. Live-config-update is not worth the complexity. |
| `expr-lang` DSL engine | (in scoring) | Simpler DSL (named flags + AND/OR/NOT) like freight_risk's is sufficient. |
| Customer trust score subsystem | (scattered) | Continuous trust value with multiple conditioned rules. Replace with simple maturity flags (new/established/locked). |
| Device fingerprint rules | (in YAML) | Data unavailable for ≥1 year. Delete now; re-add when data arrives. |
| User agent rules (the 3 unknown_user_agent and bot_user_agent variants) | (in YAML) | Same — data unavailable. |
| `app_users`, `api_tokens` tables | (schema) | Auth lives elsewhere or as a thin separate concern, not in the fraud DB. |
| `system_metrics` table | (schema) | CloudWatch / Prometheus handles this. |
| `customer_rule_weights` table | (schema) | Per-customer weight learning is post-launch calibration work, defer. |
| `pending_review_vectors`, `global_blocked_vectors` tables | (schema) | Combine into single `blocked_vectors` table with type discriminator; freight_risk has simpler version. |
| Operator endpoints (`/metrics`, `/rules`, `/users/*`, `/blacklist/*`, `/decisions/*`) | ~1,000 | Move to a future internal admin surface or drop. Not in the production API. |
| Reviewer agents specific to multi-service work (codex-implementer, codex-diff, senior-devops, aws-solutions-architect) | | Smaller system needs fewer specialized reviewers. Keep senior-engineer, security-auditor, code-flow, test, db, doc. |

**Discardable total: ~32,000 lines plus the Go re-implementation in freight_risk.**

### Cannibalize (extract the value, drop the implementation)

| Component | What to keep |
|---|---|
| FreightSentry rule definitions | The 10–15 rules with unique domain knowledge. Port to freight_risk's signal+rule format. |
| FreightSentry `customer_profiles` schema | The column set is well-thought-out. freight_risk's baseline already has most; copy the rest. |
| FreightSentry IP enrichment aggregation | The pattern of combining MaxMind + FireHOL + IP2Proxy + cloud CIDRs into a unified IP record. freight_risk has the same; FreightSentry's is more polished in places. |
| FreightSentry feedback handler | The decay/weight learning math. Port the algorithm without the Redis-streams plumbing. |
| FreightSentry `statdict` package | The stat-dict shape with `n` / `r_n` / `last` is identical to freight_risk's. Already aligned. |
| FreightSentry's audit log partition rollover script | Useful for the audit_logs table in the new system. ~150 lines, port it. |
| FreightSentry's PII HMAC pattern | Hashing emails/phones at egress. freight_risk does this; FreightSentry's pattern is more thorough. |

### Migrate as-is (worth porting wholesale)

| Component | Destination |
|---|---|
| `.claude/agents/` (the 6 reviewers you actually use) | Same place in new repo |
| `.ai/decisions-scoring.md` | Becomes the new decisions doc, trimmed |
| `.ai/conventions.md` | Same place, slightly trimmed |
| Audit findings catalog (the IDs system C-*, S-*, D-*, P-*, O-*) | Same convention for future audits |
| The 6-step commit cycle from CLAUDE.md | Same in new repo |
| Plan Context mechanism with declared-breaks | Same |
| freight_risk's signal.py functions | Move directly into new repo |
| freight_risk's baseline.py fold logic | Port to Postgres |
| freight_risk's rules.yaml | Augment with the 10–15 rules from FreightSentry |
| freight_risk's PDF report generation | Move into new repo, hook off the same decision data |
| freight_risk's IP enrichment (enrich.py) | Move directly |

---

## Part 6 — Simplification of FreightSentry in place (not recommended, here for honesty)

If you decided to keep FreightSentry and just simplify in place, the path would look like:

1. **Collapse rules-engine into gateway as a Python module.** Re-implement scoring in Python. Delete the Go service. ~18K lines removed, weeks of porting work, and you still have the async worker + Redis Streams pipeline.
2. **Collapse async worker into background tasks in gateway.** Delete the Go service. ~13K lines removed, weeks of porting. Eliminates Redis Streams. Audit writes become asyncio background tasks.
3. **Delete the AI orchestration.** ~2.5K lines + MCP client. Straightforward.
4. **Cut operator endpoints.** ~1K lines, easy.
5. **Strip dead/disabled rules.** ~25 rules, easy.

You'd end up with a single Python service at ~10–15K lines, still carrying the audit history scars (15+ reserved fields in proto definition, 11 migrations on top of a squash baseline, 31-column customer_profiles, the trust-score subsystem scattered through enrichment, 9 decision docs covering an architecture you've half-discarded).

**This works, but it's twice the work of the alternative below, and you finish with a codebase that still reads like archaeology.** Every variable name, every schema column, every comment carries the assumptions of a multi-service system. The reviewer panel's "Plan Context" mechanism exists because of complexity you'd have just dismantled. The 6-step commit cycle was tuned for a system this complex — applied to a simpler one, it's overhead.

The estimate for simplifying in place: 8–12 weeks of careful work, with constant tension between "remove this" and "but this commit's audit findings depended on it." Risk of breaking pending B5–B8 remediation work that's mid-flight.

---

## Part 7 — Hybrid path: promote freight_risk (recommended)

The recommended path takes freight_risk as the starting point and wraps it for real-time + adds the missing pieces.

### Phase 1 — Real-time API layer (1.5 weeks)

- New repo. Copy freight_risk into it as the core, drop the Go re-implementation, drop the interactive CLI.
- Replace SQLite with Postgres. Same schemas, same JSONB blobs. ~3 days.
- Add FastAPI with the four endpoints: `/api/v1/shipments/booking/evaluate`, `/api/v1/shipments/modification/evaluate`, `/api/v1/shipments/feedback`, `/health/`. ~3 days.
- Add `tenant_id` column to every table. Query scoping. ~2 days.
- Idempotency via unique constraint on `request_id`. ~1 day.

Deliverable: API that accepts shipment events, runs the freight_risk rule engine in-process against a Postgres-backed baseline, returns ALLOW/REVIEW/BLOCK, persists decision.

### Phase 2 — Knowledge migration from FreightSentry (1 week)

- Port the 10–15 unique FreightSentry rules into freight_risk's rules.yaml + corresponding signal flags. ~3 days.
- Port the customer-maturity model (account_prior weight downscaling for new customers). ~2 days.
- Port the audit_logs partition rollover. ~1 day.
- Port any missing IP enrichment sources (FreightSentry's set is the same; ensure parity). ~1 day.

Deliverable: feature parity with FreightSentry's *useful* rule set, in the new codebase.

### Phase 3 — Modification evaluation + missing signals (1 week)

- Modification endpoint with diff-based evaluation. ~3 days.
- Recipient cross-customer signal (if not already there). ~1 day.
- Impossible-travel at user level. ~1 day.
- Out-of-pattern hour/weekday rules. ~1 day.

Deliverable: production-shape v1.

### Phase 4 — SaaS scaffolding (1 week)

- Per-tenant configuration table (thresholds, country allow/block lists). ~2 days.
- Tenant onboarding flow. ~2 days.
- Cold-start handling (configurable conservative behavior for first N days per tenant). ~1 day.

Deliverable: multi-tenant capable, configurable per tenant.

### Phase 5 — Reports + ops surface (0.5–1 week)

- Port freight_risk's PDF generation (CTO + CEO views), driven from Postgres decision data.
- Daily cron generates per-tenant PDFs.
- Minimal internal admin endpoints for query/lookup if needed.

Deliverable: operator-facing reporting in place, ready for SaaS pitch demos.

### Total estimate: 5–6 weeks for one engineer + Claude Code.

**Risk-adjusted:** add 2 weeks slack. So 6–8 weeks to a system that's at functional parity with FreightSentry's *useful* output and exceeds it in modification evaluation, recipient cross-customer, and reporting. Code volume estimate: 5–7K lines total, single language, single service, single database.

Pending B5–B8 audit remediation work on FreightSentry becomes moot — the findings reference code paths that no longer exist. Net schedule savings vs. continuing FreightSentry: probably 2–3 months.

---

## Part 8 — Things to keep regardless of path

These pieces of FreightSentry are not architecture, they're discipline and they're load-bearing:

1. **The reviewer panel.** Senior-engineer, security-auditor, code-flow, test-reviewer, db-reviewer, doc-reviewer. Move them to the new repo; trim the ones that don't apply (drop codex-implementer, codex-diff, senior-devops, aws-solutions-architect — they're for the multi-service world).
2. **The 6-step commit cycle in CLAUDE.md.** This is the workflow that produced the freight_risk tool you trust. Move it.
3. **The audit ID taxonomy (C-/S-/D-/P-/O-).** Use the same convention for future audits in the new repo.
4. **The Plan Context mechanism with declared-breaks.** It's what makes the reviewer panel scalable.
5. **The conventions docs** (Python, testing). Slightly trimmed.
6. **The system-status.md "prototype-not-production" framing.** Carry it forward.

These are 1–2 thousand lines of documentation that punch far above their weight. They are the most valuable artifact in the whole repository.

---

## Part 9 — Honest uncertainty

Things I'm not sure about and that affect the recommendation:

1. **The 98% accuracy on case 2 — what's the false positive rate?** If FPR is also low (under 1%), the rules are well-tuned. If FPR is high (5%+), your operators drown in REVIEWs and the system loses commercial viability. The current freight_risk reports have this data; worth pulling before locking in the score thresholds.

2. **What does case 1 look like through freight_risk?** Case 2 was the API fraud freight_risk caught at 98%. Did freight_risk also catch case 1 (the 50-shipment dashboard ATO)? The signal stack is different (residential→residential VPN-IP shift on dashboard, vs cloud→residential on API). Worth re-running case 1 data through freight_risk to confirm both fraud shapes are covered.

3. **The trust-score subsystem in FreightSentry.** I'm calling it disposable because rule-conditioning on trust score adds complexity for marginal value. But there's a real argument for a *simple* trust value (e.g., maturity tiers: new / established / locked) that downweights signals for new customers. The question is whether the *continuous* trust score adds value over a 3-tier discrete maturity flag. I'd default to discrete; happy to revisit if you have a reason.

4. **Velocity windows beyond same-day.** FreightSentry's HyperLogLog counters in Redis give multi-day velocity. freight_risk uses within-day SQL counts. For your case 2 (15K shipments over a month), multi-day rollup catches it. For shorter bursts, within-day works. Worth knowing how much the longer windows matter for the fraud shapes you care about.

5. **The pending B5–B8 audit findings.** If any of them point at a real correctness bug in code paths that would survive into the new architecture (e.g., a stat-dict decay math error, a fold idempotency bug), those need to be addressed regardless. Worth a quick scan before committing to the new path.

---

## Appendix — Rule mapping (FreightSentry → freight_risk equivalents)

For the 45 common rules, names mostly match. The 56 FreightSentry-exclusive rules categorized:

**Removable when removing trust score (12):** very_low_trust, very_low_trust_velocity, low_trust_high_value, low_trust_new_route, low_trust_vpn, mid_trust_new_route_value, daily_volume_low_trust_ui, daily_volume_low_trust_api, ip_velocity_low_trust_ui, ip_velocity_low_trust_api, threat_score_moderate, flags_with_value (the last fires on aggregate flag count plus value — replaceable with simpler combination).

**Removable when removing device fingerprint (~8):** blacklisted_device, new_device_dormant, new_device_new_ip, new_device_vpn, threat_ip_new_device, globally_blocked_device, established_user_new_ip (mostly device-conditioned).

**Removable when removing user-agent rules (~3):** bot_user_agent, unknown_user_agent_for_customer, web_booking_from_automation.

**Removable when removing global_blocked_vectors (~5):** globally_blocked_ip, globally_blocked_email, globally_blocked_phone, ip_in_enterprise_pending_history, enterprise_pending_high_value (the enterprise pending history is the same idea).

**Velocity rules (~12) re-implementable with SQL counts:** velocity_spike_hourly_*, velocity_spike_daily_*, ip_velocity_elevated_*, ip_velocity_high_* — all of these freight_risk implements within-day; longer windows need SQL queries.

**Worth porting (the rest, ~16):** aggregated_abuse_ip, confirmed_fraud_block, consumer_asn_session_churn, dormant_new_ip, dormant_vpn, ip_country_change_new_ip, ip_country_vs_shipto_mismatch, ip_distance_dormant, ip_intercontinental_jump, ip_long_distance_new_ip, new_city_pair, new_country_pair, new_region_pair, new_route_high_value, new_route_new_ip, new_user_threat_ip, non_cloud_established_account, proxy_high_value, proxy_list_match, residential_asn_high_velocity, threat_intel_high_value, vpn_country_change. Most of these have analogs in freight_risk; double-check naming.

The 39 freight_risk-exclusive rules cover signal categories FreightSentry doesn't have rules for despite having the data (`out_of_pattern_hour`, `out_of_pattern_weekday`, `cadence_anomaly`), or cover identity/novelty patterns FreightSentry doesn't track at all (recipient cross-customer, origin-not-matching-registered, customer-novelty-compound).

---

*End of audit.*
