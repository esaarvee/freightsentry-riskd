#!/usr/bin/env python3
"""Phase 7B variant-orchestration runner (stub; full impl in 7B.1).

This stub establishes the CLI surface for the four-variant comparison
runner. Real implementation lands in PLAN_PHASE_7B.md commit 7B.1.
Until then this entry point exits non-zero with a pointer to the plan.

Planned variants (per PLAN_PHASE_7B.md decisions absorbed via
operator AskUserQuestion 2026-06-04):

  A — Tightened maturity gate, weights unchanged
      unfamiliar_ip_country_for_origin: condition adds
        customer_observations >= 30 (was >= 10); weight 0.3
      unknown_destination_address: condition adds
        customer_observations >= 30 (was >= 10); weight 0.2

  B — Halved weights, gates unchanged at >= 10
      unfamiliar_ip_country_for_origin: weight 0.3 -> 0.15
      unknown_destination_address: weight 0.2 -> 0.10

  C — Combined (A + B)
      Gates >= 30 AND weights halved.

  D — Compound with secondary signal, weights unchanged
      unfamiliar_ip_country_for_origin: append AND (is_vpn OR
        is_proxy OR ip2p_threat_any OR ip_in_threat_list OR
        is_datacenter_ip); weight 0.3
      unknown_destination_address: append AND shipment_value >
        shipment_value_threshold_medium; weight 0.2

Per variant, for each of {approved, case2, case3} corpus:
  1. git restore app/rules.yaml (clean starting state)
  2. cp /tmp/rules-variants/{variant}.yaml app/rules.yaml
  3. docker compose restart app + healthcheck poll
  4. python3 scripts/replay_validation.py against /tmp/riskd-replay/
  5. Capture aggregate result to /tmp/phase-7b-results/.

After all 12 runs:
  6. git restore app/rules.yaml (return to baseline).
  7. Aggregate into docs/replay-validation.md Phase 7B section.

THIS IS PHASE 7 EPHEMERA. Deleted in PLAN_PHASE_7E.md commit 7E.3.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-dir", type=Path, default=Path("/tmp/riskd-replay/"))
    ap.add_argument("--base-rules", type=Path, default=Path("app/rules.yaml"))
    ap.add_argument("--variants-dir", type=Path, default=Path("/tmp/rules-variants/"))
    ap.add_argument("--results-dir", type=Path, default=Path("/tmp/phase-7b-results/"))
    ap.add_argument("--tenant-token")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.parse_args(argv)
    print(
        "Not implemented; see PLAN_PHASE_7B.md commit 7B.1 for the variant"
        " orchestration mechanism and the per-variant rule definitions.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
