"""Pin the tuned thresholds from verification §2.2.

The rule additions applied the tuned values in-place. This module
pins them so a future refactor that changes a literal threshold
value (or copy-pastes a rule from a different rule set) will fail
loudly here.

Audited thresholds:
1. cadence_anomaly: is_abnormally_dormant = cadence_zscore > 6.0
   (derivation in app/context.py)
2. velocity_spike_daily_api: velocity_user_daily > 50
   (freight_risk's source was 5000)
3. residential_asn_high_velocity: velocity_ip_hourly > 15
   (freight_risk's source was 5)
4. ip_familiarity_tier "family_familiar": /24 match only, no
   cloud+ASN shortcut (app/baseline.py)
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.baseline import CustomerBaseline

_RULES_YAML = Path(__file__).resolve().parents[2] / "app" / "rules.yaml"
_CONTEXT_PY = Path(__file__).resolve().parents[2] / "app" / "context.py"


def _load_rule_by_name(name: str) -> dict[str, object]:
    data = yaml.safe_load(_RULES_YAML.read_text())
    for r in data["rules"]:
        if r["name"] == name:
            return r
    msg = f"rule {name!r} not in app/rules.yaml"
    raise AssertionError(msg)


def test_cadence_anomaly_z_threshold_is_6() -> None:
    """is_abnormally_dormant must remain at z > 6.0. A drift to 4.0 would
    fire dormancy rules far too often; a drift to 8.0 would miss case-1."""
    src = _CONTEXT_PY.read_text()
    assert "cadence_zscore > 6.0" in src, (
        "is_abnormally_dormant derivation no longer uses the tuned z > 6.0 threshold"
    )


def test_velocity_spike_daily_api_threshold_is_50() -> None:
    """freight_risk's source said > 5000; verification §2.2 tuned to 50."""
    rule = _load_rule_by_name("velocity_spike_daily_api")
    condition = str(rule["condition"])
    assert "velocity_user_daily > 50" in condition
    assert "5000" not in condition, (
        "velocity_spike_daily_api drifted back to the untuned 5000 threshold"
    )


def test_residential_asn_high_velocity_threshold_is_15() -> None:
    """freight_risk's source said > 5; verification §2.2 tuned to 15."""
    rule = _load_rule_by_name("residential_asn_high_velocity")
    condition = str(rule["condition"])
    assert "velocity_ip_hourly > 15" in condition
    # Symmetric drift-back guard (trailing space to avoid matching `> 15`)
    assert "velocity_ip_hourly > 5 " not in condition, (
        "residential_asn_high_velocity drifted back to the untuned 5 threshold"
    )


def test_ip_familiarity_tier_requires_netblock_match() -> None:
    """family_familiar requires /24 match — no cloud+ASN shortcut per
    verification §2.2. ASN-only match yields new_known_asn, not
    family_familiar."""
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    baseline.ip_asn_stats["GCP"] = {"n": 5.0, "r_n": 0, "last": "2026-05-01"}

    # ASN match without /24 match → new_known_asn (NOT family_familiar)
    tier = baseline.ip_familiarity_tier(ip="8.8.8.8", ip_netblock="8.8.8.0", ip_asn="GCP")
    assert tier == "new_known_asn", (
        f"family_familiar tier accepts ASN-only match — the cloud+ASN "
        f"shortcut should be removed per verification §2.2; got {tier!r}"
    )

    # Adding /24 match → family_familiar (different IP within the netblock)
    baseline.ip_netblock_stats["8.8.8.0"] = {"n": 5.0, "r_n": 0, "last": "2026-05-01"}
    tier = baseline.ip_familiarity_tier(ip="8.8.8.99", ip_netblock="8.8.8.0", ip_asn="GCP")
    assert tier == "family_familiar"
