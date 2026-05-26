"""Unit tests for app/dsl.py — whitelist behavior on every AST node type.

Lockdown tests live in tests/security/test_dsl_lockdown.py — they
exercise the explicit escape-attempt surface.
"""

import pytest

from app.dsl import DSLError, collect_names, parse_condition

# ---------------------------------------------------------------------------
# Whitelisted constructs — should compile and evaluate
# ---------------------------------------------------------------------------


def test_bare_name_returns_env_lookup() -> None:
    fn = parse_condition("is_vpn")
    assert fn({"is_vpn": True}) is True
    assert fn({"is_vpn": False}) is False


def test_and_combines() -> None:
    fn = parse_condition("is_vpn AND shipment_value > 1000")
    assert fn({"is_vpn": True, "shipment_value": 1500}) is True
    assert fn({"is_vpn": True, "shipment_value": 500}) is False
    assert fn({"is_vpn": False, "shipment_value": 1500}) is False


def test_or_combines() -> None:
    fn = parse_condition("is_vpn OR is_tor")
    assert fn({"is_vpn": False, "is_tor": True}) is True
    assert fn({"is_vpn": False, "is_tor": False}) is False


def test_not_negates() -> None:
    fn = parse_condition("NOT is_vpn")
    assert fn({"is_vpn": False}) is True
    assert fn({"is_vpn": True}) is False


def test_compound_with_grouping() -> None:
    fn = parse_condition(
        "(is_vpn OR is_tor) AND shipment_value >= 500 AND NOT is_api_partner"
    )
    env = {
        "is_vpn": True, "is_tor": False,
        "shipment_value": 500, "is_api_partner": False,
    }
    assert fn(env) is True
    assert fn({**env, "shipment_value": 499}) is False
    assert fn({**env, "is_api_partner": True}) is False


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        (">", 10, 5, True),
        (">=", 10, 10, True),
        ("<", 5, 10, True),
        ("<=", 5, 5, True),
        ("==", 5, 5, True),
        ("!=", 5, 6, True),
        (">", 5, 10, False),
        ("==", 5, 6, False),
    ],
)
def test_all_six_comparison_operators(
    op: str, left: int, right: int, expected: bool
) -> None:
    fn = parse_condition(f"x {op} y")
    assert fn({"x": left, "y": right}) is expected


def test_constant_int_float_str_bool_none() -> None:
    """All four primitive constant types are allowed."""
    assert parse_condition("x == 5")({"x": 5}) is True
    assert parse_condition("x == 5.0")({"x": 5.0}) is True
    assert parse_condition("x == 'web'")({"x": "web"}) is True
    assert parse_condition("x == True")({"x": True}) is True
    assert parse_condition("x == None")({"x": None}) is True


# ---------------------------------------------------------------------------
# Non-whitelisted constructs — must raise DSLError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "ctx.is_vpn",                    # attribute access
        "ctx['is_vpn']",                 # subscript
        "is_vpn()",                      # function call
        "len(triggered_rules)",          # builtin call
        "is_vpn + 1",                    # arithmetic (BinOp)
        "is_vpn * 2",
        "is_vpn - 1",
        "is_vpn / 1",
        "is_vpn % 1",
        "is_vpn ** 2",                   # power
        "is_vpn // 1",                   # floor div
        "is_vpn & is_tor",               # bitwise
        "is_vpn | is_tor",
        "is_vpn ^ is_tor",
        "is_vpn << 1",                   # shift
        "[1, 2, 3]",                     # list literal
        "{'k': 'v'}",                    # dict literal
        "{1, 2}",                        # set literal
        "(1, 2)",                        # tuple literal
        "lambda x: x",                   # lambda
        "x if y else z",                 # ternary (IfExp)
        "x in (1, 2, 3)",                # `in` operator
        "x is None",                     # `is` operator (we only allow ==/!=)
        "yield x",                       # yield (would fail mode='eval' too)
    ],
)
def test_disallowed_constructs_raise_dsl_error(source: str) -> None:
    with pytest.raises(DSLError):
        parse_condition(source)


def test_disallowed_constant_type_raises() -> None:
    """Bytes, complex, etc. — non-primitive constants."""
    with pytest.raises(DSLError):
        parse_condition("x == b'bytes'")


def test_invalid_syntax_raises_dsl_error() -> None:
    with pytest.raises(DSLError, match="syntax error"):
        parse_condition("is_vpn AND")  # incomplete


# ---------------------------------------------------------------------------
# collect_names — startup-validation contract
# ---------------------------------------------------------------------------


def test_collect_names_returns_referenced_identifiers() -> None:
    names = collect_names("is_vpn AND shipment_value > 1000 AND NOT is_api_partner")
    assert names == {"is_vpn", "shipment_value", "is_api_partner"}


def test_collect_names_dedups() -> None:
    names = collect_names("is_vpn AND is_vpn AND is_vpn")
    assert names == {"is_vpn"}


def test_collect_names_on_invalid_raises() -> None:
    with pytest.raises(DSLError):
        collect_names("ctx.foo")


# ---------------------------------------------------------------------------
# Runtime semantics
# ---------------------------------------------------------------------------


def test_missing_env_field_raises_dsl_error() -> None:
    fn = parse_condition("is_vpn")
    with pytest.raises(DSLError, match="unknown field"):
        fn({})


def test_truthy_values_coerce_to_bool() -> None:
    """The evaluator returns bool(result) — `5` is truthy, `0` is not."""
    fn = parse_condition("x")
    assert fn({"x": 5}) is True
    assert fn({"x": 0}) is False
    assert fn({"x": ""}) is False
    assert fn({"x": "anything"}) is True


def test_evaluator_does_not_mutate_caller_env() -> None:
    """The evaluator copies env into a frozen view, so even if the
    rule's compiled code somehow attempted mutation, the caller's dict
    would be unaffected. We can't construct an assignment under the
    whitelist (Store is not in the allowed AST node set), so the test
    verifies the contract by structural invariant: env passed in is
    bit-identical to env after the call."""
    fn = parse_condition("x > 5")
    env: dict[str, int] = {"x": 10}
    snapshot = dict(env)
    fn(env)
    assert env == snapshot
