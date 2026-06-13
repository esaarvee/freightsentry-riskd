"""Trust score — computed on read, never persisted.

Per `.ai/decisions.md` § Trust score, this is a sigmoid-based composite
in `[0, 1]` derived from:
  - `account_age_days` from customers.first_seen
  - `effective_observations` from baseline.value_n (post-decay)
  - `flagged_count` + `fraud_confirmed_count` from customers

`build_context` attaches the computed value to Context as
`trust_score`. Layer 2 and the trust-conditioned rules read it.

Sub-millisecond (pure arithmetic, no I/O). Deliberately not cached —
inputs change every booking, so caching gives nothing and complicates
secret rotation / per-tenant config-override.
"""

import math


def _sigmoid(x: float) -> float:
    """Numerically stable logistic sigmoid (no overflow on large |x|)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def compute_trust_score(
    *,
    account_age_days: int,
    effective_observations: float,
    flagged_count: int,
    fraud_confirmed_count: int,
) -> float:
    """Composite trust score in [0, 1].

    Centred at 0.5 with monotonic contributions from:
      +0.3 * sigmoid((effective_observations - 20) / 10) — activity proxy
      +0.2 * sigmoid((account_age_days - 60) / 30)        — tenure proxy
      -0.4 if any prior flag                              — explicit negative signal
      -0.6 if any confirmed fraud                         — strongest negative signal

    Clamped to `[0, 1]` after summation.
    """
    raw = (
        0.5
        + 0.3 * _sigmoid((effective_observations - 20.0) / 10.0)
        + 0.2 * _sigmoid((float(account_age_days) - 60.0) / 30.0)
        - (0.4 if flagged_count > 0 else 0.0)
        - (0.6 if fraud_confirmed_count > 0 else 0.0)
    )
    return max(0.0, min(1.0, raw))
