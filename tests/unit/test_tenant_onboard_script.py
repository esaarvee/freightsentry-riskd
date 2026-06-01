"""Unit tests for scripts/tenant_onboard.py helpers (4A.5).

6 tests on the pure helpers:
- _load_initial_config(None) -> {}
- _load_initial_config(<valid-path>) -> dict
- _load_initial_config(<bad-path>) -> SystemExit(1)
- _load_initial_config(<non-dict-json>) -> SystemExit(1)
- _validate_initial_config({}) -> None
- _validate_initial_config({"unknown": 1}) -> SystemExit(1)

Full-script E2E tests live in tests/integration/ (4A.6).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.tenant_onboard import _load_initial_config, _validate_initial_config


def test_load_initial_config_none_returns_empty_dict() -> None:
    assert _load_initial_config(None) == {}


def test_load_initial_config_valid_path_returns_dict(tmp_path: Path) -> None:
    cfg = {"maturity_age_days": 90, "cold_start_grace_days": 7}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    assert _load_initial_config(p) == cfg


def test_load_initial_config_missing_path_exits(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(SystemExit) as exc:
        _load_initial_config(missing)
    assert exc.value.code == 1


def test_load_initial_config_non_dict_top_level_exits(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _load_initial_config(p)
    assert exc.value.code == 1


def test_load_initial_config_malformed_json_exits(tmp_path: Path) -> None:
    # JSONDecodeError branch — catches the third exit path in the helper.
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        _load_initial_config(p)
    assert exc.value.code == 1


def test_validate_initial_config_empty_passes() -> None:
    _validate_initial_config({})  # no exception


def test_validate_initial_config_extra_field_exits() -> None:
    with pytest.raises(SystemExit) as exc:
        _validate_initial_config({"unknown_field": 1})
    assert exc.value.code == 1
