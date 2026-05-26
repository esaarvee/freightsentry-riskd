"""Layer 2 account-prior constants and the maturity helper.

Values are Design-Context-fixed for Phase 2; Phase 4 introduces
per-tenant override via tenants.config. These constants are not in
app/rules.yaml because they're scoring-formula machinery, not rule
parameters; rules.yaml continues to own allow_max / block_min only.

See .ai/decisions.md § Scoring architecture for the formula and the
documented divergences from FreightSentry's scorer.go (multiplicative
maturity vs. min, linear vs. log1p shipments, 4-tier direct-lookup
flag weights vs. 2-tier noisy-OR, no customer-inheritance term).
"""

from __future__ import annotations

MAX_NEW_ACCOUNT: float = 0.10
TRUST_FACTOR: float = 0.25
MATURITY_AGE_DAYS: int = 180
MATURITY_SHIPMENTS: int = 50
MATURITY_K: float = 0.30

# 4-tier direct-lookup table indexed by flagged_count_tier:
#   0 flagged shipments        → tier 0 → 0.00
#   1-2 flagged shipments      → tier 1 → 0.15
#   3-5 flagged shipments      → tier 2 → 0.25
#   6+ flagged shipments       → tier 3 → 0.35
FLAG_WEIGHTS: tuple[float, ...] = (0.00, 0.15, 0.25, 0.35)


def flagged_count_tier(flagged_count: int) -> int:
    if flagged_count <= 0:
        return 0
    if flagged_count <= 2:
        return 1
    if flagged_count <= 5:
        return 2
    return 3


def maturity(age_days: int, total_shipments: int) -> float:
    age_frac = min(max(age_days, 0) / MATURITY_AGE_DAYS, 1.0)
    ship_frac = min(max(total_shipments, 0) / MATURITY_SHIPMENTS, 1.0)
    return age_frac * ship_frac
