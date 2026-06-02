"""Pure-Python `ast`-based DSL evaluator for rule conditions.

Rule conditions in `app/rules.yaml` are short boolean expressions like:
    `is_vpn AND shipment_value > 1000`
    `velocity_user_hourly > 50 OR (is_new_route AND trust_score < 0.5)`
    `NOT is_api_partner AND is_new_ip`

Each condition is parsed to a Python AST at startup, walked to verify it
contains ONLY whitelisted node types, then compiled and evaluated against
a per-request env dict (the Context). Evaluation uses
`eval(code, {"__builtins__": {}}, env)` — frozen no-builtins globals
prevent any escape to `__class__`, `__subclasses__`, `getattr`, etc.

Whitelist (ANY other node type raises `DSLError`):
- BoolOp (with `And`, `Or`)
- UnaryOp (with `Not`)
- Compare (with `Gt`, `Lt`, `GtE`, `LtE`, `Eq`, `NotEq`)
- Name (env lookup only — no attribute access, no subscript, no calls)
- Constant of `int | float | str | bool | None` (no bytes, no complex)
- Load context

Convention: rule YAML uses `AND` / `OR` / `NOT` (uppercase) for
readability; the parser lowercases the source before parsing so both
work. Identifiers retain case.

Any change to this file is never-skip security review per CLAUDE.md.
"""

import ast
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

# Allowed comparison operators (Python ast types)
_ALLOWED_CMP_OPS: frozenset[type[ast.cmpop]] = frozenset(
    {
        ast.Gt,
        ast.Lt,
        ast.GtE,
        ast.LtE,
        ast.Eq,
        ast.NotEq,
    }
)

# Allowed primitive constant types
_ALLOWED_CONST_TYPES: tuple[type, ...] = (int, float, str, bool, type(None))


class DSLError(ValueError):
    """Raised on any rule-condition parse failure: unsupported AST node,
    disallowed operator, non-primitive constant, etc."""


def _validate(node: ast.AST, source: str) -> None:
    """Walk the AST and raise DSLError on any non-whitelisted construct."""
    if isinstance(node, ast.Expression):
        _validate(node.body, source)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise DSLError(f"unsupported BoolOp `{type(node.op).__name__}` in: {source}")
        for value in node.values:
            _validate(value, source)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            raise DSLError(f"unsupported UnaryOp `{type(node.op).__name__}` in: {source}")
        _validate(node.operand, source)
        return
    if isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _ALLOWED_CMP_OPS:
                raise DSLError(f"unsupported comparison `{type(op).__name__}` in: {source}")
        _validate(node.left, source)
        for comp in node.comparators:
            _validate(comp, source)
        return
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            raise DSLError(f"unsupported Name context `{type(node.ctx).__name__}` in: {source}")
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, _ALLOWED_CONST_TYPES):
            raise DSLError(f"unsupported constant type `{type(node.value).__name__}` in: {source}")
        return
    raise DSLError(f"unsupported AST node `{type(node).__name__}` in: {source}")


def parse_condition(source: str) -> Callable[[Mapping[str, Any]], bool]:
    """Parse + compile a rule condition. Returns a callable that takes a
    Context env and returns True/False.

    Whitespace and YAML-friendly `AND` / `OR` / `NOT` (uppercase) are
    normalised at parse time. Anything else raises DSLError.
    """
    normalised = _normalise(source)
    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        msg = f"syntax error in rule condition: {source!r} ({exc})"
        raise DSLError(msg) from exc
    _validate(tree, source)
    code = compile(tree, "<rule>", mode="eval")

    def evaluator(env: Mapping[str, Any]) -> bool:
        # Frozen builtins prevent any escape via __class__ etc. The Name
        # nodes can only reference env keys (no attribute / subscript /
        # call in the whitelist), and env is read-only via MappingProxyType.
        frozen_env = MappingProxyType(dict(env))
        try:
            result = eval(code, {"__builtins__": {}}, frozen_env)
        except NameError as exc:
            # Missing Context field — fail loud at request time. (Rule
            # loader in 1D.7 validates names at startup, so this only
            # fires on a programming error.)
            msg = f"rule condition referenced unknown field: {exc}"
            raise DSLError(msg) from exc
        return bool(result)

    return evaluator


def collect_names(source: str) -> frozenset[str]:
    """Return the set of Name tokens referenced by a rule condition. The
    rule loader uses this to validate every name resolves to a known
    Context field at startup (fail-fast)."""
    normalised = _normalise(source)
    try:
        tree = ast.parse(normalised, mode="eval")
    except SyntaxError as exc:
        msg = f"syntax error in rule condition: {source!r} ({exc})"
        raise DSLError(msg) from exc
    _validate(tree, source)
    return frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))


def _normalise(source: str) -> str:
    """Map YAML-friendly `AND`/`OR`/`NOT` (uppercase, surrounded by
    whitespace) to Python `and`/`or`/`not`. Identifiers retain case."""
    out = source
    # Token-level replacements — only when surrounded by word boundaries.
    # We do this with simple string ops because the inputs are short and
    # the alternatives (regex \b) are equivalent here.
    for upper, lower in (
        (" AND ", " and "),
        (" OR ", " or "),
        ("(NOT ", "(not "),
        (" NOT ", " not "),
    ):
        out = out.replace(upper, lower)
    if out.startswith("NOT "):
        out = "not " + out[4:]
    return out
