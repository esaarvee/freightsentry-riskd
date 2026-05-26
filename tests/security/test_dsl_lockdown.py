"""Security lockdown tests for app/dsl.py.

Every test here exercises an explicit escape attempt that has appeared
in CPython-sandbox-bypass writeups. The DSL must reject ALL of them via
the whitelist (any non-whitelisted AST node → DSLError BEFORE any
eval() call).
"""

import pytest

from app.dsl import DSLError, parse_condition


@pytest.mark.parametrize(
    "source",
    [
        # Attribute-walk attacks
        "x.__class__",
        "x.__class__.__bases__",
        "x.__class__.__mro__",
        "x.__class__.__subclasses__",
        "().__class__.__mro__[-1].__subclasses__()",
        "x.__init__",
        "x.__dict__",
        "x.__globals__",
        "x.__builtins__",
        # Function-call attacks (any call is rejected)
        "getattr(x, 'attr')",
        "setattr(x, 'attr', 1)",
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('print(1)')",
        "compile('1', '<s>', 'eval')",
        "__import__('os')",
        "type(x)",
        "globals()",
        "locals()",
        "vars()",
        # Subscript-walk attacks
        "x[0]",
        "x['key']",
        "x[1:2]",
        # Generator / comprehension attacks
        "[i for i in range(10)]",
        "(i for i in range(10))",
        "{i for i in range(10)}",
        "{i: i for i in range(10)}",
        # Walrus, starred, formatted strings
        "(x := 5)",
        "[*x]",
        "f'{x}'",
    ],
)
def test_escape_attempts_rejected_at_parse_time(source: str) -> None:
    with pytest.raises(DSLError):
        parse_condition(source)


def test_no_builtins_accessible_via_name_token() -> None:
    """Even a bare Name token referencing a builtin name resolves via
    the env dict, not the builtins. With `{"__builtins__": {}}` and the
    env not containing `len`, the evaluator raises DSLError (missing
    field), NOT NameError-from-builtins."""
    fn = parse_condition("len")
    with pytest.raises(DSLError, match="unknown field"):
        fn({})


def test_builtins_walled_off_behaviourally() -> None:
    """End-to-end proof that builtins are not accessible from the
    evaluator. If `__builtins__` were the default Python dict (which
    happens when you pass an empty dict as globals — Python silently
    inserts builtins) then `len` would resolve to the builtin and
    `parse_condition("len")(env)` would return the function object
    coerced via bool() to True. Production passes
    `{"__builtins__": {}}` (explicit empty dict), which defeats the
    silent-insertion behavior and forces a NameError → DSLError."""
    from app.dsl import DSLError, parse_condition
    fn = parse_condition("len")
    with pytest.raises(DSLError, match="unknown field"):
        fn({})  # env has no `len`; builtins are walled off, so this raises
