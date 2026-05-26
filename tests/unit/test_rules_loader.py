"""Unit tests for app/rules.py — YAML loader + Context-field whitelist."""

from pathlib import Path

import pytest

from app.rules import ALLOWED_CONTEXT_FIELDS, load_rules


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_thresholds_with_defaults(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
thresholds:
  allow_max: 0.55
  block_min: 0.75
rules: []
""")
    ruleset = load_rules(path)
    assert ruleset.thresholds.allow_max == 0.55
    assert ruleset.thresholds.block_min == 0.75
    assert ruleset.rules == ()


def test_threshold_defaults_when_absent(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, "rules: []\n")
    ruleset = load_rules(path)
    assert ruleset.thresholds.allow_max == 0.60
    assert ruleset.thresholds.block_min == 0.80


def test_loads_simple_rule(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
rules:
  - name: hi_value_vpn
    description: VPN + high value
    condition: "is_vpn AND shipment_value > 1000"
    weight: 0.3
""")
    ruleset = load_rules(path)
    assert len(ruleset.rules) == 1
    rule = ruleset.rules[0]
    assert rule.name == "hi_value_vpn"
    assert rule.weight == 0.3
    assert rule.action == ""
    assert rule.maturity_sensitive is False
    assert rule.evaluate({"is_vpn": True, "shipment_value": 1500}) is True
    assert rule.evaluate({"is_vpn": False, "shipment_value": 1500}) is False


def test_loads_block_rule(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
rules:
  - name: blacklisted_ip
    condition: "ip_in_level1"
    weight: 1.0
    action: BLOCK
""")
    ruleset = load_rules(path)
    assert ruleset.rules[0].action == "BLOCK"


def test_loads_maturity_sensitive_flag(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
rules:
  - name: ip_velocity_high
    condition: "velocity_ip_hourly > 50"
    weight: 0.3
    maturity_sensitive: true
""")
    assert load_rules(path).rules[0].maturity_sensitive is True


def test_unknown_context_field_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
rules:
  - name: bad_rule
    condition: "is_vpn AND nonexistent_field"
    weight: 0.3
""")
    with pytest.raises(ValueError, match="nonexistent_field"):
        load_rules(path)


@pytest.mark.parametrize("bad_weight", [-0.1, -1.0, 1.01, 2.0, 100.0])
def test_weight_out_of_range_raises(tmp_path: Path, bad_weight: float) -> None:
    path = _write_yaml(tmp_path, f"""
rules:
  - name: bad_weight
    condition: "is_vpn"
    weight: {bad_weight}
""")
    with pytest.raises(ValueError, match="must be in"):
        load_rules(path)


def test_weight_at_boundaries_accepted(tmp_path: Path) -> None:
    """0.0 and 1.0 are valid boundary values."""
    path = _write_yaml(tmp_path, """
rules:
  - name: zero_weight
    condition: "is_vpn"
    weight: 0.0
  - name: max_weight
    condition: "is_vpn"
    weight: 1.0
""")
    ruleset = load_rules(path)
    assert ruleset.rules[0].weight == 0.0
    assert ruleset.rules[1].weight == 1.0


def test_unsupported_action_raises(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """
rules:
  - name: bad_action
    condition: "is_vpn"
    weight: 0.5
    action: ALLOW
""")
    with pytest.raises(ValueError, match="unsupported action"):
        load_rules(path)


def test_allowed_context_fields_is_frozen() -> None:
    """ALLOWED_CONTEXT_FIELDS is the single source of truth for the rule
    DSL vocabulary. It must remain a frozenset (immutable)."""
    assert isinstance(ALLOWED_CONTEXT_FIELDS, frozenset)
    assert "is_vpn" in ALLOWED_CONTEXT_FIELDS
    assert "shipment_value" in ALLOWED_CONTEXT_FIELDS
    assert "customer_observations" in ALLOWED_CONTEXT_FIELDS
    assert "trust_score" in ALLOWED_CONTEXT_FIELDS
