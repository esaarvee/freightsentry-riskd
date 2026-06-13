"""Unit tests for _truncate_stat_dict and _truncate_hmac_set helpers.

6 tests covering:
- Empty stat-dict
- 5 entries (no truncation)
- 15 entries (top-10 by n desc returned, truncated=True)
- JSONB-as-string input parses correctly
- Truncation orders by `n` descending
- HMAC set helper handles list-form and dict-form input
"""

from __future__ import annotations

import json

from app.api.admin import _truncate_hmac_set, _truncate_stat_dict


def test_empty_stat_dict() -> None:
    out = _truncate_stat_dict({})
    assert out == {"entries": [], "total_count": 0, "truncated": False}


def test_five_entries_not_truncated() -> None:
    data = {f"k{i}": {"n": float(i), "r_n": 0, "last": "2026-01-01"} for i in range(5)}
    out = _truncate_stat_dict(data)
    assert out["total_count"] == 5
    assert out["truncated"] is False
    assert len(out["entries"]) == 5


def test_fifteen_entries_truncated_to_ten_by_n_desc() -> None:
    data = {f"k{i}": {"n": float(i), "r_n": 0, "last": "2026-01-01"} for i in range(15)}
    out = _truncate_stat_dict(data)
    assert out["total_count"] == 15
    assert out["truncated"] is True
    assert len(out["entries"]) == 10
    # Sorted desc by n — first entry should be k14 (highest n=14).
    assert out["entries"][0]["key"] == "k14"
    assert out["entries"][0]["n"] == 14.0
    # Last of the top-10 should be k5 (n=5).
    assert out["entries"][-1]["key"] == "k5"


def test_jsonb_string_input_parses() -> None:
    data = {"k1": {"n": 1.0, "r_n": 0, "last": "2026-01-01"}}
    out = _truncate_stat_dict(json.dumps(data))
    assert out["total_count"] == 1
    assert out["entries"][0]["key"] == "k1"


def test_hmac_set_helper_dict_form_preserves_payload_and_sorts_by_n_desc() -> None:
    # Dict-form HMAC sets (the production shape per .ai/schema.md) must
    # preserve the {n, r_n, last} payload AND order by n desc — same
    # contract as _truncate_stat_dict. Pre-retro behavior dropped payload
    # AND returned insertion order; this test guards the regression.
    data = {f"hmac{i}": {"n": float(15 - i), "r_n": 0.0, "last": "2026-01-01"} for i in range(15)}
    out = _truncate_hmac_set(data)
    assert out["total_count"] == 15
    assert out["truncated"] is True
    assert len(out["entries"]) == 10
    # Top entry has highest n (hmac0 has n=15.0).
    assert out["entries"][0]["key"] == "hmac0"
    assert out["entries"][0]["n"] == 15.0
    # Payload preserved.
    assert "r_n" in out["entries"][0]
    assert "last" in out["entries"][0]


def test_hmac_set_helper_handles_list_form() -> None:
    # List-form is the defensive fallback for legacy sets without payload.
    # No n to sort by → insertion order is acceptable.
    data = [f"hmac{i}" for i in range(7)]
    out = _truncate_hmac_set(data)
    assert out["total_count"] == 7
    assert out["truncated"] is False
    assert out["entries"] == data


def test_truncation_helpers_handle_non_dict_non_list_input() -> None:
    # Defensive: malformed JSONB (None, scalar values like int, or a
    # JSON-encoded scalar string like '42') must not crash the admin
    # endpoint. Both helpers return the empty-shape response.
    for bad_input in (None, 42, "42"):
        out_stat = _truncate_stat_dict(bad_input)  # type: ignore[arg-type]
        assert out_stat == {"entries": [], "total_count": 0, "truncated": False}
        out_hmac = _truncate_hmac_set(bad_input)  # type: ignore[arg-type]
        assert out_hmac == {"entries": [], "total_count": 0, "truncated": False}
