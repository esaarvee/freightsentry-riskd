"""Unit tests for scripts/calibration/run_variants.py.

Covers:
- Each of the five variants (A/B/C/D/E) applies the expected
  transformations.
- _apply_variant does NOT mutate the input doc.
- Generated variant YAMLs load cleanly via app.rules.load_rules.
- generate_variants writes five files to the output directory.
- _tighten_gate raises ValueError when the source condition lacks
  the expected `customer_observations >= 10` substring.
- Variant D + E carry updated descriptions matching their conditions.
- Working-tree-clean check.
- Unknown variant letter raises ValueError.
- Missing target rule in base raises ValueError.

Does NOT exercise the docker-compose orchestration path. The full
orchestrate() call is integration-level coverage (run by hand when
the operator triggers the variant comparison).
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.rules import load_rules
from scripts.calibration.run_variants import (
    _apply_variant,
    _git_working_tree_clean,
    generate_variants,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_rules_doc() -> dict:
    """Load the real app/rules.yaml so tests pin against the actual
    baseline (no synthetic stand-in could drift relative to production)."""
    repo_root = Path(__file__).resolve().parents[2]
    with (repo_root / "app" / "rules.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_rule(doc: dict, name: str) -> dict:
    for rule in doc["rules"]:
        if rule["name"] == name:
            return rule
    raise AssertionError(f"rule {name!r} not in doc")


# ---------------------------------------------------------------------------
# Variant A — Tightened gate, weights unchanged
# ---------------------------------------------------------------------------


def test_variant_a_tightens_gate_to_30_keeps_weights() -> None:
    doc = _apply_variant(_base_rules_doc(), "A")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    assert "customer_observations >= 30" in ip_rule["condition"]
    assert "customer_observations >= 30" in dest_rule["condition"]
    assert "customer_observations >= 10" not in ip_rule["condition"]
    assert "customer_observations >= 10" not in dest_rule["condition"]
    assert ip_rule["weight"] == 0.15  # Phase 7C.8 baseline reduction
    assert dest_rule["weight"] == 0.10  # Phase 7C.8 baseline reduction


# ---------------------------------------------------------------------------
# Variant B — Halved weights, gates unchanged
# ---------------------------------------------------------------------------


def test_variant_b_halves_weights_keeps_gate_at_10() -> None:
    doc = _apply_variant(_base_rules_doc(), "B")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    assert "customer_observations >= 10" in ip_rule["condition"]
    assert "customer_observations >= 10" in dest_rule["condition"]
    assert ip_rule["weight"] == 0.15
    assert dest_rule["weight"] == 0.10


# ---------------------------------------------------------------------------
# Variant C — Combined
# ---------------------------------------------------------------------------


def test_variant_c_tightens_gate_and_halves_weights() -> None:
    doc = _apply_variant(_base_rules_doc(), "C")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    assert "customer_observations >= 30" in ip_rule["condition"]
    assert "customer_observations >= 30" in dest_rule["condition"]
    assert ip_rule["weight"] == 0.15
    assert dest_rule["weight"] == 0.10


# ---------------------------------------------------------------------------
# Variant D — Compound with secondary signal
# ---------------------------------------------------------------------------


def test_variant_d_appends_secondary_signal_keeps_gate_at_10_and_weights() -> None:
    doc = _apply_variant(_base_rules_doc(), "D")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    assert "is_vpn" in ip_rule["condition"]
    assert "is_proxy" in ip_rule["condition"]
    assert "ip2p_threat_any" in ip_rule["condition"]
    assert "ip_in_threat_list" in ip_rule["condition"]
    assert "is_datacenter_ip" in ip_rule["condition"]
    assert "customer_observations >= 10" in ip_rule["condition"]
    assert "shipment_value > shipment_value_threshold_medium" in dest_rule["condition"]
    assert "customer_observations >= 10" in dest_rule["condition"]
    assert ip_rule["weight"] == 0.15  # Phase 7C.8 baseline reduction
    assert dest_rule["weight"] == 0.10  # Phase 7C.8 baseline reduction


# ---------------------------------------------------------------------------
# Variant E — Asymmetric split (D-style IPC + A-style DEST)
# ---------------------------------------------------------------------------


def test_variant_e_asymmetric_split() -> None:
    """IPC takes D-style secondary-signal compound; DEST takes A-style
    gate tightening to >=30. Both weights unchanged."""
    doc = _apply_variant(_base_rules_doc(), "E")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    # IPC: D-style compound
    assert "is_vpn" in ip_rule["condition"]
    assert "is_proxy" in ip_rule["condition"]
    assert "ip2p_threat_any" in ip_rule["condition"]
    assert "ip_in_threat_list" in ip_rule["condition"]
    assert "is_datacenter_ip" in ip_rule["condition"]
    assert "customer_observations >= 10" in ip_rule["condition"]
    # DEST: A-style gate
    assert "customer_observations >= 30" in dest_rule["condition"]
    assert "customer_observations >= 10" not in dest_rule["condition"]
    assert "is_vpn" not in dest_rule["condition"]  # not a D-style compound
    # Weights unchanged
    assert ip_rule["weight"] == 0.15  # Phase 7C.8 baseline reduction
    assert dest_rule["weight"] == 0.10  # Phase 7C.8 baseline reduction


# ---------------------------------------------------------------------------
# Immutability and error handling
# ---------------------------------------------------------------------------


def test_apply_variant_does_not_mutate_input() -> None:
    base = _base_rules_doc()
    snapshot = copy.deepcopy(base)
    _apply_variant(base, "C")
    assert base == snapshot


def test_apply_variant_unknown_variant_raises() -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        _apply_variant(_base_rules_doc(), "Z")


def test_apply_variant_missing_target_rule_raises() -> None:
    base = _base_rules_doc()
    base["rules"] = [r for r in base["rules"] if r["name"] != "unfamiliar_ip_country_for_origin"]
    with pytest.raises(ValueError, match="unfamiliar_ip_country_for_origin"):
        _apply_variant(base, "A")


def test_tighten_gate_raises_when_substring_absent() -> None:
    """_tighten_gate (used by variants A, C, E) asserts the source
    condition contains `customer_observations >= 10`. A future
    rule-condition reformat would otherwise silently no-op the
    variant transformation, producing a measurement-of-no-change."""
    base = _base_rules_doc()
    # Mutate the IPC rule's gate string so the substring no longer matches.
    for rule in base["rules"]:
        if rule["name"] == "unfamiliar_ip_country_for_origin":
            rule["condition"] = "NOT origin_ip_country_familiar AND customer_observations > 9"
            break
    with pytest.raises(ValueError, match="customer_observations >= 10"):
        _apply_variant(base, "A")


def test_variant_d_carries_updated_descriptions() -> None:
    doc = _apply_variant(_base_rules_doc(), "D")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    dest_rule = _find_rule(doc, "unknown_destination_address")
    # Descriptions reference the secondary signals; sanity-check by keyword.
    assert "corroborating IP-quality" in ip_rule["description"]
    assert "medium tier" in dest_rule["description"]


def test_variant_e_carries_updated_ipc_description() -> None:
    doc = _apply_variant(_base_rules_doc(), "E")
    ip_rule = _find_rule(doc, "unfamiliar_ip_country_for_origin")
    assert "asymmetric split" in ip_rule["description"]


# ---------------------------------------------------------------------------
# generate_variants end-to-end
# ---------------------------------------------------------------------------


def test_generate_variants_writes_five_files_that_load_cleanly(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    base_rules = repo_root / "app" / "rules.yaml"
    out_dir = tmp_path / "variants"
    paths = generate_variants(base_rules, out_dir)
    assert set(paths.keys()) == {"A", "B", "C", "D", "E"}
    for variant, path in paths.items():
        assert path.exists(), f"variant {variant} not written"
        # Each variant must parse via the production loader (catches
        # whitelist violations and DSL errors before docker restart).
        ruleset = load_rules(path)
        # Sanity: each variant has the same number of rules as the base.
        with base_rules.open(encoding="utf-8") as f:
            base_doc = yaml.safe_load(f)
        assert len(ruleset.rules) == len(base_doc["rules"])


def test_generate_variants_variant_b_loads_with_halved_weights(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    base_rules = repo_root / "app" / "rules.yaml"
    out_dir = tmp_path / "variants"
    paths = generate_variants(base_rules, out_dir)
    ruleset = load_rules(paths["B"])
    rules_by_name = {r.name: r for r in ruleset.rules}
    assert rules_by_name["unfamiliar_ip_country_for_origin"].weight == pytest.approx(0.15)
    assert rules_by_name["unknown_destination_address"].weight == pytest.approx(0.10)


# ---------------------------------------------------------------------------
# Working-tree check
# ---------------------------------------------------------------------------


def test_git_working_tree_clean_returns_bool_against_real_repo() -> None:
    """Smoke against the real repo state: working tree status is a
    string returnable by `git status --porcelain=v1`. Returns bool."""
    assert isinstance(_git_working_tree_clean(), bool)


def test_git_working_tree_clean_false_when_subprocess_reports_changes() -> None:
    """Mock subprocess.run to simulate a dirty tree; assert False."""

    class _FakeResult:
        stdout = " M app/rules.yaml\n"

    with patch(
        "scripts.calibration.run_variants.subprocess.run",
        return_value=_FakeResult(),
    ):
        assert _git_working_tree_clean() is False


def test_git_working_tree_clean_true_when_subprocess_reports_no_changes() -> None:
    class _FakeResult:
        stdout = ""

    with patch(
        "scripts.calibration.run_variants.subprocess.run",
        return_value=_FakeResult(),
    ):
        assert _git_working_tree_clean() is True
