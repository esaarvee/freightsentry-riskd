# scripts/calibration/ — Phase 7 ephemera

> This directory is Phase 7 ephemera. Created in PLAN_PHASE_7A.md
> commit 7A.2; deleted in PLAN_PHASE_7E.md commit 7E.3. Do not rely
> on it after Phase 7 closes.

The directory is gitignored (`.gitignore` `scripts/calibration/` since
Phase 7A.0). Files here must be staged with `git add -f` during Phase 7.

## Contents

- `export_from_freight_risk.py` — reads the sibling freight_risk
  SQLite database read-only and writes three NDJSON corpora to
  `/tmp/riskd-replay/`. Used by 7A.2 to bootstrap the corpora; rerun
  before 7D if `/tmp/` has been cleaned.
- `run_variants.py` — variant orchestration runner. Stub in 7A.2;
  full implementation in 7B.1.

## Output policy

**Output is never inside the repo.** Both scripts write to `/tmp/`
paths by default. The repo is scrubbed of freight_risk-derived data
in 7A.0 via `git filter-repo`; no per-record content reaches the
working tree of this repo at any point during Phase 7.

## `export_from_freight_risk.py` usage

```bash
python3 scripts/calibration/export_from_freight_risk.py \
    --db /Users/drshott/PycharmProjects/miscProj/freight_risk/freight_risk.db \
    --out-dir /tmp/riskd-replay/ \
    --seed 42
```

Outputs (each line is one BookingRequest JSON):
- `/tmp/riskd-replay/approved_jan_mar.ndjson` — 10,000 records;
  approved transactions in Jan-Mar 2026 sampled with seed 42.
- `/tmp/riskd-replay/case2_sample.ndjson` — 500 records; the gobolt
  API ATO fraud cluster.
- `/tmp/riskd-replay/case3_census.ndjson` — 95 records; Roulottes
  Lupien full census.

### Customer-country derivation tiers

1. **Explicit country column** — not available in the current
   freight_risk schema (no clean structured field). Tier returns
   None for every record.
2. **Address last-token regex** `r'.*,\s*([A-Z]{2})\s*$'` —
   primary path. Matches addresses ending in 2-letter country code.
3. **Modal IP geo** — modal country is accepted only when at least
   5 successful lookups are available AND the top country represents
   ≥70% of those lookups (`_MODAL_IP_MIN_SAMPLES = 5`,
   `_MODAL_IP_MIN_CONCENTRATION = 0.70` in
   `export_from_freight_risk.py`). Requires
   `~/.maxmind/GeoLite2-Country.mmdb` and the `maxminddb` Python
   package. When either is absent, the tier returns None for every
   record and the record falls through to tier 4.
4. **Null fallback** — `customer.registered_country = None`. The
   record cannot trigger the case-3b outbound rule by accident (the
   derivation requires both inputs truthy).

### Per-corpus overrides

- **case-3 records** (Roulottes Lupien): `customer.registered_country`
  forced to `"CA"` regardless of derivation result; operator ground
  truth from the fraud investigation. `shipment.origin_via_carrier_dropoff`
  forced to `True`.
- **case-2 + approved records**: derivation result for
  `customer.registered_country`; `shipment.origin_via_carrier_dropoff`
  forced to `False` (case-2 was API ATO automation, not carrier
  dropoff; approved corpus likewise).

All corpora: `shipment.currency = "CAD"` (Phase 6B project default).

### Tier counts logged to stderr

After the export completes, the script logs per-tier record counts to
stderr so the operator can verify derivation quality, e.g.:

```
tier 1 (explicit column): 0 records
tier 2 (address regex):   8,234 records
tier 3 (modal IP geo):    0 records
tier 4 (null fallback):   1,766 records
case-3 hardcoded:         95 records (customer_registered_country = 'CA')
```

## `run_variants.py` usage

7B.1 lands the full implementation. The 7A.2 stub establishes the CLI
surface but exits non-zero pointing at the plan.

## Lifecycle

| Phase | Action |
|---|---|
| 7A.0 | `scripts/calibration/` added to .gitignore |
| 7A.2 | This README + export script + run_variants stub created (`git add -f`) |
| 7B.1 | `run_variants.py` full implementation lands |
| 7E.3 | Entire `scripts/calibration/` directory deleted; .gitignore entry retained for future-rerun defense |
