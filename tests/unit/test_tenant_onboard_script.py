"""Unit tests for scripts/tenant_onboard.py helpers.

Pure-helper tests:
- _load_initial_config(None) -> {}
- _load_initial_config(<valid-path>) -> dict
- _load_initial_config(<bad-path>) -> SystemExit(1)
- _load_initial_config(<non-dict-json>) -> SystemExit(1)
- _validate_initial_config({}) -> None
- _validate_initial_config({"unknown": 1}) -> SystemExit(1)

Token-delivery tests (_store_token_secret + --token-secret-id arg):
- put_secret_value success -> no create_secret call
- ResourceNotFoundException -> falls back to create_secret
- other ClientError -> SystemExit(2)
- --token-secret-id parses (and defaults to None)

Full-script E2E tests live in tests/integration/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

from scripts.tenant_onboard import (
    _load_initial_config,
    _parse_args,
    _store_token_secret,
    _validate_initial_config,
)


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


class _FakeSecretsClient:
    """Records put/create calls; optionally raises a ClientError on put."""

    def __init__(self, put_error: ClientError | None = None) -> None:
        self.put_error = put_error
        self.put_kwargs: dict[str, str] | None = None
        self.create_kwargs: dict[str, str] | None = None

    def put_secret_value(self, **kwargs: str) -> None:
        if self.put_error is not None:
            raise self.put_error
        self.put_kwargs = kwargs

    def create_secret(self, **kwargs: str) -> None:
        self.create_kwargs = kwargs


def test_store_token_secret_put_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeSecretsClient()
    monkeypatch.setattr(boto3, "client", lambda service: fake)

    _store_token_secret("freightsentry-riskd/tenants/alpha/token", "tok-123")

    assert fake.put_kwargs == {
        "SecretId": "freightsentry-riskd/tenants/alpha/token",
        "SecretString": "tok-123",
    }
    assert fake.create_kwargs is None  # existing secret -> no create


def test_store_token_secret_falls_back_to_create_on_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    not_found = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "PutSecretValue")
    fake = _FakeSecretsClient(put_error=not_found)
    monkeypatch.setattr(boto3, "client", lambda service: fake)

    _store_token_secret("freightsentry-riskd/tenants/new/token", "tok-456")

    assert fake.create_kwargs == {
        "Name": "freightsentry-riskd/tenants/new/token",
        "SecretString": "tok-456",
    }


def test_store_token_secret_other_clienterror_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = ClientError({"Error": {"Code": "AccessDeniedException"}}, "PutSecretValue")
    fake = _FakeSecretsClient(put_error=denied)
    monkeypatch.setattr(boto3, "client", lambda service: fake)

    with pytest.raises(SystemExit) as exc:
        _store_token_secret("freightsentry-riskd/tenants/x/token", "tok-789")
    assert exc.value.code == 2
    assert fake.create_kwargs is None  # non-NotFound errors must not create


def test_parse_args_token_secret_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tenant_onboard.py",
            "--external-id",
            "alpha",
            "--display-name",
            "Alpha Corp",
            "--token-secret-id",
            "freightsentry-riskd/tenants/alpha/token",
        ],
    )
    args = _parse_args()
    assert args.token_secret_id == "freightsentry-riskd/tenants/alpha/token"


def test_parse_args_token_secret_id_defaults_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["tenant_onboard.py", "--external-id", "alpha", "--display-name", "Alpha Corp"],
    )
    args = _parse_args()
    assert args.token_secret_id is None
