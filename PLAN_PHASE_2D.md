# Phase 2 — Batch 2D Plan — Tuned thresholds, case-1 fixture, integration validation

> **Status (2026-05-26)**: Pending operator approval after 2C execution. Re-review against the final rule set (which 2C rules actually shipped vs deferred) before approving — the case-2 BLOCK expectation depends on which lock-in / dormancy / VPN-conditioned rules ship.

Batch 2D closes Phase 2 by applying the tuned thresholds from verification §2.2 to any Phase 1 rules that still carry default values, synthesizing the case-1 (dashboard ATO ~50 shipments) integration fixture, asserting the case-2 (API ATO) fixture now crosses BLOCK with Layer 2 + lock-in rules active, and adding the focused integration tests for account-prior math edges + maturity downweight + lock-in threshold + recipient tenant scoping.

Target: 5 commits.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Tuned thresholds | `cadence_anomaly z > 6` (already in `app/context.py:165` as `is_abnormally_dormant`); `velocity_spike_daily_api = 50` (applied in 2C.5); `residential_asn_high_velocity = 15` (applied in 2C.3). 2D audits these are correctly in place and applies any remaining Phase 1 defaults. | Verification §2.2 |
| `ip_familiarity_tier` family_familiar | /24-only (no cloud + ASN shortcut) — already correct in `app/baseline.py:417-427` post-Phase 1. 2D audits via test. | Verification §2.2 |
| Case-1 fixture | Synthesized from MASTER_PLAN.md + bootstrap-prompt case description. Customer with established cloud-IP + web-channel baseline (~30-40 historical bookings), then ~10-15 booking burst from VPN IPs in ~1 hour. Expected score curve: REVIEW by burst-shipment ~10, BLOCK by burst-shipment ~20-30. | Phase 2 bootstrap |
| Case-2 BLOCK assertion | Phase 1 case-2 fixture (`tests/fixtures/payloads/case_2_*.json` per `app/api/booking.py` flow) asserts signals fire. 2D extends to assert final score crosses BLOCK band (≥ 0.80). If it doesn't, surface — rule weights need tuning before Phase 6 (rule weight tuning is post-launch / Phase 6, per `.ai/decisions.md`; we don't tune in Phase 2D). | Phase 2 bootstrap |
| No Phase 1 schema change | Migrations are stable from 2B.1. | Phase 2 critical framing |
| Test additions land with the code they cover | Per `.ai/conventions.md`. Each new integration test lands in its own commit with the corresponding test data. | Phase 1 invariant |

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. **Reviewer-panel quota is available** — every code-path commit runs the full panel at commit time.
- Reviewer routing per CLAUDE.md triage gate:
  - 2D.1 (tuned-threshold audit): touches `app/rules.yaml` if any threshold change needed — standard path full panel.
  - 2D.2 (case-1 fixture creation): adds JSON test data + integration test — test-reviewer + senior-engineer (lightweight per triage gate "ONLY test file additions"). The fixture is generated test data; no production code change.
  - 2D.3 (account-prior + maturity unit-test matrix): test-only — test-reviewer + senior-engineer + code-flow.
  - 2D.4 (case-2 BLOCK assertion): integration test change — test-reviewer + senior-engineer + code-flow.
  - 2D.5 (Phase 2 wrap report): doc-only — doc-reviewer.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_2D.md, current commit: 2D.N (<title>), upcoming commits: 2D.{N+1} through 2D.5 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 2A**: Layer 2 + maturity downweight wiring; `account_prior` / `signal_score` / `maturity` fields on `ScoringResult`.
- **Consumes from 2B**: All Phase 2 Context fields populated; tenant-scoped recipient overlap.
- **Consumes from 2C**: All ~63 rules in `app/rules.yaml`; rule loader validates at startup.
- **Consumed by Phase 3**: feedback endpoint will reuse the case-1 fixture shape for marking decisions; modification endpoint reuses the case-2 fixture as a basis for modification-evaluation scenarios.

---

## 2D.1 — Tuned-threshold audit + applies any remaining defaults

**Theme**: Quick pass over `app/rules.yaml` to confirm every tuned-threshold value from verification §2.2 is correctly in place. Most are already correct (Phase 1's `is_abnormally_dormant` is z>6; Phase 2C applies 50 for velocity_spike_daily_api and 15 for residential). This commit's job is to verify, document with a test, and apply any threshold that's still on a Phase 1 default.

**Files**:
- `app/rules.yaml` (EDIT only if defaults found that need updating; expected: 0 changes since 2C applied them in-place)
- `tests/unit/test_rules_tuned_thresholds.py` (NEW)

**Specifics**:

Audit checklist (run as part of the unit test, not just manual grep):
1. `cadence_anomaly z > 6` — verified via Phase 1's `is_abnormally_dormant = cadence_zscore > 6.0` in `app/context.py:165`. Test confirms `is_abnormally_dormant` is exactly `cadence_zscore > 6.0` (not 4.0).
2. `velocity_spike_daily_api > 50` — verified via the 2C.5 rule. Test confirms the YAML condition contains `velocity_user_daily > 50` (not `> 5000`).
3. `residential_asn_high_velocity` velocity threshold = 15 — verified via 2C.3. Test confirms `velocity_ip_hourly > 15` in the rule.
4. `ip_familiarity_tier` "family_familiar" — /24-only — verified via Phase 1's `app/baseline.py:417-427`. Test exercises the function with (a) a /24 match without ASN match → `family_familiar`, (b) no /24 match but ASN match → `new_known_asn`, NOT `family_familiar`.

`tests/unit/test_rules_tuned_thresholds.py`:
```python
import yaml
from pathlib import Path

RULES_YAML = Path("app/rules.yaml")

def test_cadence_anomaly_z_threshold_is_6():
    # Read app/context.py source and confirm the literal 6.0 appears in
    # the is_abnormally_dormant derivation.
    src = Path("app/context.py").read_text()
    assert "cadence_zscore > 6.0" in src

def test_velocity_spike_daily_api_threshold_is_50():
    rules = yaml.safe_load(RULES_YAML.read_text())["rules"]
    rule = next(r for r in rules if r["name"] == "velocity_spike_daily_api")
    assert "velocity_user_daily > 50" in rule["condition"]
    assert "5000" not in rule["condition"]

def test_residential_asn_high_velocity_threshold_is_15():
    rules = yaml.safe_load(RULES_YAML.read_text())["rules"]
    rule = next(r for r in rules if r["name"] == "residential_asn_high_velocity")
    assert "velocity_ip_hourly > 15" in rule["condition"]

def test_ip_familiarity_tier_family_familiar_requires_netblock_match():
    """Verify ip_familiarity_tier returns family_familiar ONLY on /24 match,
    NOT on ASN match (the 'cloud + ASN shortcut' was removed per
    verification §2.2)."""
    from app.baseline import CustomerBaseline
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    baseline.ip_asn_stats["GCP"] = {"n": 5.0, "r_n": 0, "last": "2026-05-01"}
    # ASN match but no IP / netblock match → new_known_asn, NOT family_familiar
    tier = baseline.ip_familiarity_tier(ip="8.8.8.8", ip_netblock="8.8.8.0/24", ip_asn="GCP")
    assert tier == "new_known_asn"
    # Now add /24 match
    baseline.ip_netblock_stats["8.8.8.0/24"] = {"n": 5.0, "r_n": 0, "last": "2026-05-01"}
    tier = baseline.ip_familiarity_tier(ip="8.8.8.99", ip_netblock="8.8.8.0/24", ip_asn="GCP")
    assert tier == "family_familiar"
```

**Validation**:
- `pytest tests/unit/test_rules_tuned_thresholds.py -v` — all 4 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green

**Risk**: **Low** if no thresholds need updating; **Medium** if drift is found (would require updating the rule in-place which could break tests written against the prior threshold). Expected: 0 drift since 2C applied the tuned values in-place.

**Reversibility**: Easy — revert removes the audit tests; no production code changes.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 4 unit tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Standard path. If no YAML changes, lightweight (test-only).

---

## 2D.2 — Case-1 fixture: dashboard ATO synthesis

**Theme**: Create the case-1 integration fixture. Customer with established cloud-IP + web-channel baseline gets an ATO; attacker shifts to VPN IPs and bursts through ~10-15 bookings in ~1 hour. The fixture is a sequence of booking payloads + a seeded baseline state.

**Files**:
- `tests/integration/fixtures/case_1_dashboard_ato.json` (NEW)
- `tests/integration/fixtures/case_1_seed_baseline.json` (NEW)
- `tests/integration/test_case_1_detection.py` (NEW)
- Possibly extend `tests/conftest.py` if seeded-baseline helpers don't exist for the customer with pre-existing observations.

**Specifics**:

**Fixture shape** (`case_1_dashboard_ato.json` — top-level structure):
```json
{
  "tenant": {
    "id": "tenant-case-1",
    "config": {"cold_start_days": 30}
  },
  "customer": {
    "external_id": "cust-case-1-ato",
    "first_seen": "2025-09-01T00:00:00Z",
    "total_shipments": 38,
    "flagged_count": 0,
    "fraud_confirmed_count": 0
  },
  "seed_baseline": {
    "value_n": 38.0,
    "value_mean": 425.50,
    "value_m2": 12000.0,
    "cadence_n": 37.0,
    "cadence_mean_h": 49.5,
    "cadence_m2_h": 380.0,
    "ip_stats": {
      "104.19.142.10": {"n": 18.0, "r_n": 0, "last": "2026-05-15", "type": "cloud"},
      "172.67.45.20": {"n": 12.0, "r_n": 0, "last": "2026-05-10", "type": "cloud"},
      "104.19.142.50": {"n": 8.0, "r_n": 0, "last": "2026-04-20", "type": "cloud"}
    },
    "ip_netblock_stats": {
      "104.19.142.0/24": {"n": 26.0, "r_n": 0, "last": "2026-05-15"},
      "172.67.45.0/24": {"n": 12.0, "r_n": 0, "last": "2026-05-10"}
    },
    "ip_asn_stats": {
      "Cloudflare, Inc.": {"n": 38.0, "r_n": 0, "last": "2026-05-15"}
    },
    "ip_type_hist": {"cloud": 38.0},
    "channel_hist": {"web": 38.0},
    "last_booking_ts": "2026-05-15T16:00:00Z",
    "last_booking_lat": 43.65,
    "last_booking_lon": -79.38,
    "last_booking_country": "CA"
  },
  "burst_sequence": [
    {
      "request_id": "case-1-burst-001",
      "booking_ts": "2026-05-26T10:00:00Z",
      "source_ip": "185.220.101.5",
      "shipment": {"channel": "web", "value": 425.0, "origin": {"address": "...Toronto..."}, "destination": {"address": "...Vancouver..."}}
    },
    {
      "request_id": "case-1-burst-002",
      "booking_ts": "2026-05-26T10:03:30Z",
      "source_ip": "185.220.101.5",
      "shipment": {"channel": "web", "value": 475.0, "origin": {"address": "..."}, "destination": {"address": "..."}}
    }
    ...
  ]
}
```

**Burst-sequence sizing**: 30 shipments total in the burst, all from VPN IPs in the same `185.220.101.0/24` netblock (a NordVPN range), within 60 minutes. Booking values escalate slightly (425 → 1200) to bring `value_zscore` up by burst-mid. Times: ~120 seconds between bookings.

**Expected signal progression** (documented in fixture comment + tested):

| Burst shipment | Expected fired rules | Expected score band |
|---|---|---|
| 1-3 | `ip_fully_new_for_customer` (since 185.220.101.x not in cust ip_stats) + `unfamiliar_ip_country_for_origin` (NordVPN → likely different country) + `dormant_new_ip` (if dormancy gap from last_booking is > 6σ — needs Welford check) | ALLOW ~0.40-0.55 |
| 4-9 | + `ip_velocity_high_ui` (web channel, > 10/hour from one IP), `customer_daily_volume_spike` (> 20/day) | REVIEW (0.60 < s < 0.80) |
| 10+ | + `dormant_vpn` (is_vpn AND is_abnormally_dormant — needs enrichment to report is_vpn=True for 185.220.101.x) + `vpn_high_value` (when value > 1000) + `low_trust_vpn` (trust_score drops as `n` grows with the burst's value-rejected pattern, but trust_score won't move much on a single burst — flag this) | BLOCK (s ≥ 0.80) |

**ENRICHMENT NOTE**: the 185.220.101.x range needs to be in the IP enrichment cache as `is_vpn=True`. Phase 1 has `app/enrich.py` with offline cache; the integration test seeds an `ip_enrichment` row before running the fixture:
```python
INSERT INTO ip_enrichment (ip_address, is_vpn, asn_org, country, fh_level2, ...)
VALUES ('185.220.101.5', true, 'Tor Project', 'DE', true, ...)
```

`tests/integration/test_case_1_detection.py`:

```python
async def test_case_1_dashboard_ato_progression(client, seeded_tenant, seed_case_1):
    """Replay the case-1 fixture and assert the score progression.

    The exact shipment indices may shift if rule-weight composition changes;
    use band-level assertions with a 2-shipment tolerance window.
    """
    fixture = load_fixture("case_1_dashboard_ato.json")
    await seed_baseline_from_json(fixture["seed_baseline"], fixture["customer"])
    await seed_ip_enrichment(["185.220.101.5"])  # VPN, DE, Tor exit

    scores = []
    for i, payload in enumerate(fixture["burst_sequence"]):
        resp = await client.post("/api/v1/shipments/booking/evaluate", json=payload, headers=AUTH)
        result = resp.json()
        scores.append(result["score"])

        # Band assertions with tolerance
        if i < 4:
            assert result["decision"] == "ALLOW", f"shipment {i} expected ALLOW"
        elif 4 <= i < 10:
            # REVIEW window may start 1-2 shipments later — accept ALLOW or REVIEW
            assert result["decision"] in ("ALLOW", "REVIEW")
        elif i >= 10:
            # By shipment 10+, expect REVIEW or BLOCK
            assert result["decision"] in ("REVIEW", "BLOCK")

    # End-of-burst: at least one BLOCK should have occurred
    assert any(s >= 0.80 for s in scores), \
        f"case-1 burst never crossed BLOCK band; max score was {max(scores):.2f}"

    # At least one REVIEW must have appeared before BLOCK (progressive escalation)
    first_review_idx = next((i for i, s in enumerate(scores) if 0.60 < s < 0.80), None)
    first_block_idx = next((i for i, s in enumerate(scores) if s >= 0.80), None)
    if first_review_idx is not None and first_block_idx is not None:
        assert first_review_idx < first_block_idx, \
            f"BLOCK preceded REVIEW: review at {first_review_idx}, block at {first_block_idx}"
```

**Validation**:
- `pytest tests/integration/test_case_1_detection.py -v --asyncio-mode=auto` — passes
- `pytest tests/ -q --asyncio-mode=auto` — full suite green

**Risk**: **High**. Case-1 is the new high-stakes integration test. If the rule mix + weights don't compose to the expected band progression, the test fails — and we're explicitly told NOT to tune weights to make it pass (per `.ai/decisions.md` and bootstrap "Watch points"). If the test fails, the right response is to surface to operator via STATUS.md — Phase 2 weight tuning is out of scope.

**Reversibility**: Easy — revert removes the fixture + test.

**Pre-commit verification**: check-json on the fixture; ruff + mypy on the test.

**Observability**: N/A.

**Test changes**: 1 integration test + 2 JSON fixtures.

**Rollback plan**: `git revert`.

**Declared breaks**:
- Scope: case-1 test asserts BAND-level behavior with a 2-shipment tolerance. If the test fails on a specific assertion, scope-limit is to the case-1 fixture itself; do NOT widen tolerances to make it pass.
- Resolved in: N/A (test stays in this form until Phase 6 staging replay tunes weights).

**Reviewer routing**: test-reviewer + senior-engineer + code-flow. The fixture's synthetic baseline and the expected-progression table are reviewed for soundness; the test code's band-tolerance is reviewed for non-flakiness.

---

## 2D.3 — Account-prior + maturity downweight integration test matrix

**Theme**: Targeted integration tests against the Layer 2 + Layer 3 wiring under realistic Context. These extend 2A.3's unit-level boundary tests to full-pipeline scenarios where multiple rules interact.

**Files**:
- `tests/integration/test_layer2_integration.py` (NEW)

**Specifics**:

`tests/integration/test_layer2_integration.py`:
- `test_brand_new_customer_with_no_signals_elevates_via_account_prior`: post a booking from a brand-new customer (no prior shipments) with a clean IP, clean contact, clean route → `result["score"] >= 0.10` (account_prior alone) and decision = ALLOW.
- `test_established_customer_clean_baseline_returns_zero_score`: post a booking from an established customer (50+ shipments, trust_score ~0.9) with everything clean → `score < 0.05`, decision = ALLOW.
- `test_flagged_customer_with_3_5_flags_elevates_at_tier_2`: established customer with `flagged_count=4` → flag_prior = 0.25, account_prior should be ~0.25 alone.
- `test_low_trust_customer_amplifies_account_prior`: brand-new customer with `trust_score=0.1` → `trust_contribution = 0.8 * 0.25 = 0.20`; combined with base_prior=0.10, `account_prior ~ noisyOR(0.10, 0.20, 0) ~ 0.28`.
- `test_maturity_downweight_on_brand_new_vs_mature`: same booking payload (one VPN + high-value rule fires) sent against a brand-new customer vs a mature customer. Brand-new sees `vpn_high_value` rule at weight 0.30 × 0.70 = 0.21 (with maturity_sensitive=true on that rule... actually, `vpn_high_value` does NOT have `maturity_sensitive: true` in Phase 1's YAML — check). For a rule that IS maturity-sensitive (e.g. `customer_daily_volume_spike`), the brand-new gets weight × 0.7, mature gets weight × 1.0. Assert final score differs by the expected ratio.
- `test_customer_locked_cloud_api_flips_exactly_at_threshold`: seed customer with `cloud_share_n / value_n = 0.95` (exactly at threshold) → `customer_locked_cloud_api == False`. Bump cloud_share by one observation → ratio = 0.952 → True.
- `test_lock_in_rule_fires_only_when_locked`: with `customer_locked_cloud_api=True`, post a booking from a non-cloud IP via API channel → `cloud_api_customer_deviation_iptype` fires.
- `test_recipient_overlap_count_does_not_cross_tenants`: 2D-level integration confirming the 2B.6 SQL-level test holds end-to-end through the booking endpoint.

**Validation**:
- `pytest tests/integration/test_layer2_integration.py -v --asyncio-mode=auto` — 8 tests pass
- `pytest tests/ -q --asyncio-mode=auto` — full suite green

**Risk**: **Medium**. These tests exercise the full pipeline (build_context + scoring + persist + decision response). Risk is fixture-setup complexity producing flaky tests. Mitigation: each test uses focused seeded state via conftest fixtures.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 8 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-reviewer + senior-engineer + code-flow.

---

## 2D.4 — Case-2 BLOCK assertion + final integration sweep

**Theme**: Strengthen the existing Phase 1 case-2 integration test (`test_unfamiliar_ip_against_established_customer_triggers_signals` in `tests/integration/test_booking_endpoint.py`) to assert that final score crosses BLOCK band (≥ 0.80). With Layer 2 + Phase 2C lock-in rules + dormancy rules + Layer-3 maturity downweighting all active, the case-2 fixture should now meet the BLOCK threshold.

**Files**:
- `tests/integration/test_booking_endpoint.py` (EDIT — strengthen assertion)
- Possibly: `tests/integration/fixtures/case_2_*.json` (verify the fixture is rich enough to reach BLOCK — may need to add a 21st burst shipment if 20 doesn't tip)

**Specifics**:

**Pre-check before strengthening the assertion** — the lock-in rule (`cloud_api_customer_deviation_iptype`) requires three conditions on the seeded baseline:
- `cloud_share > 0.95` (i.e., `ip_type_hist["cloud"] / sum(ip_type_hist.values()) > 0.95`)
- `api_share > 0.95` (i.e., `channel_hist["api"] / sum(channel_hist.values()) > 0.95`)
- `value_n >= 20`

Phase 1's case-2 fixture (`tests/fixtures/payloads/case_2_*.json` or wherever it lives) may not seed a baseline that satisfies all three. Before changing the assertion, RUN the existing test and add a one-time debug emission of `ctx["customer_locked_cloud_api"]` to confirm it's `True` under the existing seed. If it's `False`:
1. **First option (preferred)**: extend the case-2 fixture's seeded baseline to add more cloud-API observations until the derivation flips. This is a fixture-data change, not a weight tune — fully in scope.
2. **Second option**: if growing the fixture seed makes case-2 unrealistic (i.e., the case-2 customer was supposed to NOT be locked-in), drop the `cloud_api_customer_deviation_iptype` assertion from this commit's expected-rule list. The BLOCK assertion still stands; the rule list just shrinks.

Do NOT touch weights to force a pass. If the BLOCK assertion itself fails after the fixture pre-check, surface to `.claude/STATUS.md` per the bootstrap "don't tune weights in Phase 2" rule.

Current Phase 1 case-2 assertion:
```python
async def test_unfamiliar_ip_against_established_customer_triggers_signals(...):
    ...
    result = resp.json()
    assert result["score"] > 0.0
    assert "ip_fully_new_for_customer" in result["triggered_rules"]
```

Strengthened 2D assertion:
```python
async def test_unfamiliar_ip_against_established_customer_blocks_under_layer2(...):
    ...
    result = resp.json()
    assert result["decision"] == "BLOCK", \
        f"case-2 expected BLOCK with Layer 2 active; got {result['decision']} at score {result['score']:.2f}"
    assert result["score"] >= 0.80
    # Existing assertions preserved
    assert "ip_fully_new_for_customer" in result["triggered_rules"]
    # New: with lock-in active, expect the deviation rule to fire too
    assert "cloud_api_customer_deviation_iptype" in result["triggered_rules"]
```

**If the case-2 BLOCK assertion fails**: the right response per `.ai/decisions.md` is to SURFACE TO OPERATOR via `.claude/STATUS.md` — do NOT tune weights to force a pass. The fixture may need extension (more burst shipments, higher-value payloads) within the principled bounds — or the operator may need to accept that case-2 reaches REVIEW (not BLOCK) and adjust the expectation. Either way, this is a Phase 6 calibration decision.

**Validation**:
- `pytest tests/integration/test_booking_endpoint.py::test_unfamiliar_ip_against_established_customer_blocks_under_layer2 -v --asyncio-mode=auto` — passes
- `pytest tests/ -q --asyncio-mode=auto` — full suite green
- Final count expected: **~410 tests** (post-2D)

**Risk**: **High**. The case-2 BLOCK is the canonical Phase 2 success criterion. If it fails, the response is operator escalation, not weight tuning.

**Reversibility**: Moderate — reverting weakens the assertion back to score > 0.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: Strengthens one existing assertion + adds 2 new assertions in the same test.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-reviewer + senior-engineer + code-flow + security-auditor.

---

## 2D.5 — Phase 2 wrap-up report

**Theme**: Produce `REPORT_PHASE_2.md` aggregating the four per-batch reports. Per the bootstrap, each batch produces its own `REPORT_PHASE_2X.md` upon execution; this commit closes the phase by writing the aggregate.

**Files**:
- `REPORT_PHASE_2A.md` (NEW — produced at end of 2A execution, before 2B start)
- `REPORT_PHASE_2B.md` (NEW — produced at end of 2B execution)
- `REPORT_PHASE_2C.md` (NEW — produced at end of 2C execution)
- `REPORT_PHASE_2D.md` (NEW — produced at end of 2D execution, with this commit)
- `REPORT_PHASE_2.md` (NEW — aggregate, written in this commit)

**Specifics**:

Per-batch reports follow the same shape as `REPORT_PHASE_1.md`:
- Aggregate stats (commits, source files touched, tests added, test runtime, validation tooling status)
- Per-batch disposition (commit table with theme + outcome)
- Plan deviations (recorded in `.claude/STATUS.md` `Unforeseen / checkpoints`)
- Reviewer-caught corrections (with file:line refs)
- Explicitly deferred from Phase 2 (rules deferred per "Rules deferred" section of 2C; Phase 3+ landing path)

Aggregate `REPORT_PHASE_2.md`:
- Cross-batch totals (commits, rules, tests, fields in DSL whitelist)
- 5-line summary per batch with link to detailed `REPORT_PHASE_2X.md`
- Open items for Phase 3 (modification endpoint, feedback endpoint dependencies for the deferred rules)
- Final case-2 + case-1 detection outcomes (BLOCK / REVIEW / where they landed)
- **Currency-handling note (carry-forward to Phase 3 planning)**: Phase 2C ships rules with absolute-value thresholds (`absolute_high_value: shipment_value > 10000`, `threat_intel_high_value: shipment_value > 2000`, `flags_with_value: shipment_value > 2000`, `vpn_high_value: shipment_value > 1000`, etc.). These thresholds are **implicitly USD**. Phase 4's `TenantConfig` defines `value_caps: dict[str, float]` per currency — Phase 3 planning should consider whether mid-Phase-3 introduces a `value_in_usd` normalization (using `payload.shipment.currency` + a static rates table) so that per-tenant `value_caps` and absolute-value rule thresholds compose correctly. Not a Phase 2 blocker; flagged here for visibility.

**Validation**:
- Manual read for completeness
- doc-reviewer agent

**Risk**: **Low**. Doc-only.

**Reversibility**: Easy.

**Pre-commit verification**: trailing-whitespace, end-of-file-fixer, check-yaml all pass.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Doc-only path — doc-reviewer.

---

## Batch 2D summary

5 commits:
- 2D.1 — Tuned-threshold audit (4 unit tests)
- 2D.2 — Case-1 dashboard ATO fixture + integration test (1 integration test + 2 JSON fixtures)
- 2D.3 — Account-prior + maturity integration test matrix (8 integration tests)
- 2D.4 — Case-2 BLOCK assertion + final sweep (assertion strengthening)
- 2D.5 — Phase 2 aggregate report (`REPORT_PHASE_2.md` + 4 per-batch reports)

**Total tests after 2D**: 386 (post-2C) + 4 (2D.1) + 1 (2D.2) + 8 (2D.3) + 0 (2D.4 — strengthens existing) = **399 tests**.

**End-of-Phase-2 system state**:
- `app/scoring.py` implements full 3-layer scoring (Layer 1 + Layer 2 + Layer 3 with maturity downweight)
- `app/context.py` derives all Phase 2 fields including `customer_locked_cloud_api`, `days_since_last_booking`, recipient cross-customer count (tenant-scoped)
- `app/rules.yaml` contains ~63 rules (14 Phase 1 + ~49 net new from 2C)
- DSL whitelist contains 56 fields
- Case-2 fixture asserts BLOCK end-to-end
- Case-1 fixture asserts band progression (ALLOW → REVIEW → BLOCK over a 30-shipment burst)
- All Phase 1 schemas remain stable; only `customers.shipment_volume_30d` was dropped (2B.1)

**Phase 3 inherits**:
- Modification endpoint scope (uses the same Layer-2-and-3 scoring infrastructure)
- Feedback endpoint scope (writes `r_n` increments on baseline; uses existing `add_rejected_observation` helper)
- ~12 deferred rules waiting on feedback + global-blocked-vectors
- The Phase 6 case-1 + case-2 production replay (still uses synthesized fixtures; production data lands Phase 6)
