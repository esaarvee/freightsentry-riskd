"""Whitelist + DSL parse tests for the 6 modification-Context fields (3A.3).

Mirrors `test_rules_whitelist.py` shape — additive size pin + per-field
membership. Also confirms the rule loader still loads the production
rules.yaml cleanly under the extended whitelist, and that the loader's
whitelist filter (`app/rules.py::load_rules`) rejects rules that reference
fields outside the whitelist.

Field-semantics testing (what value each field takes given a Context
state) lands in tests/unit/test_context_modification.py at 3A.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.dsl import collect_names, parse_condition
from app.rules import ALLOWED_CONTEXT_FIELDS, load_rules

_RULES_YAML = Path(__file__).resolve().parents[2] / "app" / "rules.yaml"


_PHASE_3A_MODIFICATION_FIELDS = frozenset(
    {
        "modification_time_since_booking",
        "modification_magnitude",
        "modification_direction",
        "modification_velocity_1h",
        "modification_velocity_24h",
        "modification_type",
    }
)


def test_phase_3a_additions_count_is_six() -> None:
    """Sanity: the pinned addition set is the documented 6 fields."""
    assert len(_PHASE_3A_MODIFICATION_FIELDS) == 6


def test_whitelist_contains_every_phase_3a_modification_field() -> None:
    """Each 3A modification field is present in the whitelist."""
    missing = _PHASE_3A_MODIFICATION_FIELDS - ALLOWED_CONTEXT_FIELDS
    assert not missing, f"Phase 3A modification fields not in whitelist: {missing}"


def test_production_rules_yaml_loads_under_extended_whitelist() -> None:
    """Plan-specified smoke: app/rules.yaml loads without unknown-field
    errors after the whitelist grows. Catches the case where a typo in
    a new whitelist entry would silently let a YAML rule sneak through —
    or the case where adding fields breaks existing rule parsing."""
    ruleset = load_rules(_RULES_YAML)
    assert len(ruleset.rules) > 0


@pytest.mark.parametrize("field_name", sorted(_PHASE_3A_MODIFICATION_FIELDS))
def test_dsl_parses_condition_referencing_new_field(field_name: str) -> None:
    """parse_condition accepts a synthetic rule referencing each new field
    AND the resulting evaluator returns a sensible boolean against a
    minimal ctx — proves the new field name flows through both parse
    and evaluate. AST whitelist is unchanged; only field-name additions
    are required."""
    condition = f"{field_name} == 0"
    evaluator = parse_condition(condition)
    # Evaluator should be callable and produce a boolean against any
    # ctx that supplies the field. Use 0 to match the == 0 condition;
    # the assertion is on truth, not on the specific value.
    assert evaluator({field_name: 0}) is True
    assert evaluator({field_name: 1}) is False
    # collect_names recognises the field name in the condition.
    assert field_name in collect_names(condition)


def test_load_rules_rejects_yaml_referencing_unknown_field(tmp_path: Path) -> None:
    """The rule loader's whitelist filter (app/rules.py::load_rules)
    raises ValueError when a YAML rule's condition references a field
    that is not in ALLOWED_CONTEXT_FIELDS. This is the actual security
    boundary; the DSL parser itself does not enforce field-name
    whitelisting."""
    bogus_yaml = tmp_path / "bogus_rules.yaml"
    bogus_yaml.write_text(
        yaml.safe_dump(
            {
                "thresholds": {"allow_max": 0.6, "block_min": 0.8},
                "rules": [
                    {
                        "name": "bogus_modification_test_rule",
                        "description": "references a field outside the whitelist",
                        "condition": "modification_nonsense_field == 0",
                        "weight": 0.5,
                    }
                ],
            }
        )
    )
    with pytest.raises(ValueError, match="modification_nonsense_field"):
        load_rules(bogus_yaml)


def test_unknown_modification_field_absent_from_whitelist() -> None:
    """Negative control complement: confirm the unknown field is in fact
    absent from ALLOWED_CONTEXT_FIELDS, so the loader rejection above
    is the genuine boundary check and not a side effect of some other
    failure mode."""
    assert "modification_nonsense_field" not in ALLOWED_CONTEXT_FIELDS
