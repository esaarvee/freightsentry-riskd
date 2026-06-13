"""Unit tests for the 4 previously-rejected Context fields.

build_context populates email_previously_rejected, phone_previously_rejected,
origin_previously_rejected, ip_previously_rejected from the loaded
baseline. Pure dict lookups — no SQL added. Tests pin the derivation
shape directly against baseline state without exercising the full
async build_context (that's integration territory).
"""

from __future__ import annotations

import pytest

from app.baseline import CustomerBaseline


def _baseline_with_rejections(
    *,
    rejected_emails: list[str] | None = None,
    rejected_phones: list[str] | None = None,
    origins_with_rn: dict[str, float] | None = None,
    ips_with_rn: dict[str, float] | None = None,
) -> CustomerBaseline:
    """Build a CustomerBaseline with selectively-rejected dimensions.

    `rejected_emails` / `rejected_phones`: HMAC hex strings to add to
    the dedicated rejected dicts (the feedback endpoint writes here for
    rejected/fraud_confirmed labels — see app/api/feedback.py).
    `origins_with_rn` / `ips_with_rn`: maps from key to r_n value for
    the entry (n=0 in the test data to isolate the rejection signal).
    """
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    for h in rejected_emails or []:
        baseline.rejected_email_hmacs[h] = {"n": 0.0, "r_n": 1.0, "last": "2026-05-27"}
    for h in rejected_phones or []:
        baseline.rejected_phone_hmacs[h] = {"n": 0.0, "r_n": 1.0, "last": "2026-05-27"}
    for k, rn in (origins_with_rn or {}).items():
        baseline.origin_stats[k] = {"n": 0.0, "r_n": rn, "last": "2026-05-27"}
    for k, rn in (ips_with_rn or {}).items():
        baseline.ip_stats[k] = {"n": 0.0, "r_n": rn, "last": "2026-05-27"}
    return baseline


# These tests directly exercise the dict-lookup shape used by
# build_context — copied here as the canonical derivation. If
# build_context's derivation drifts from this shape, the integration
# tests catch it; these unit tests pin the semantics.


def _email_previously_rejected(baseline: CustomerBaseline, email_hmac: str | None) -> bool:
    return email_hmac is not None and email_hmac in baseline.rejected_email_hmacs


def _phone_previously_rejected(baseline: CustomerBaseline, phone_hmac: str | None) -> bool:
    return phone_hmac is not None and phone_hmac in baseline.rejected_phone_hmacs


def _origin_previously_rejected(baseline: CustomerBaseline, origin: str) -> bool:
    return float(baseline.origin_stats.get(origin, {}).get("r_n", 0.0)) > 0.0


def _ip_previously_rejected(baseline: CustomerBaseline, ip: str) -> bool:
    return float(baseline.ip_stats.get(ip, {}).get("r_n", 0.0)) > 0.0


# ---------------------------------------------------------------------------
# email_previously_rejected
# ---------------------------------------------------------------------------


def test_email_previously_rejected_true_when_hmac_in_dict() -> None:
    baseline = _baseline_with_rejections(rejected_emails=["abc123"])
    assert _email_previously_rejected(baseline, "abc123") is True


def test_email_previously_rejected_false_when_hmac_not_in_dict() -> None:
    baseline = _baseline_with_rejections(rejected_emails=["abc123"])
    assert _email_previously_rejected(baseline, "def456") is False


def test_email_previously_rejected_false_when_email_hmac_is_none() -> None:
    """Current request supplied no email — cannot have been rejected."""
    baseline = _baseline_with_rejections(rejected_emails=["abc123"])
    assert _email_previously_rejected(baseline, None) is False


def test_email_previously_rejected_false_on_empty_baseline() -> None:
    baseline = _baseline_with_rejections()
    assert _email_previously_rejected(baseline, "any-hmac") is False


# ---------------------------------------------------------------------------
# phone_previously_rejected (mirror)
# ---------------------------------------------------------------------------


def test_phone_previously_rejected_true_when_hmac_in_dict() -> None:
    baseline = _baseline_with_rejections(rejected_phones=["phone-hmac-1"])
    assert _phone_previously_rejected(baseline, "phone-hmac-1") is True


def test_phone_previously_rejected_false_when_phone_hmac_is_none() -> None:
    baseline = _baseline_with_rejections(rejected_phones=["phone-hmac-1"])
    assert _phone_previously_rejected(baseline, None) is False


# ---------------------------------------------------------------------------
# origin_previously_rejected — checks r_n > 0 on origin_stats entry
# ---------------------------------------------------------------------------


def test_origin_previously_rejected_true_when_rn_positive() -> None:
    baseline = _baseline_with_rejections(origins_with_rn={"123 Main St": 2.0})
    assert _origin_previously_rejected(baseline, "123 Main St") is True


def test_origin_previously_rejected_false_when_rn_zero() -> None:
    """Origin known (n > 0) but never rejected (r_n == 0) → no signal."""
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    baseline.origin_stats["123 Main St"] = {"n": 5.0, "r_n": 0.0, "last": "2026-05-27"}
    assert _origin_previously_rejected(baseline, "123 Main St") is False


def test_origin_previously_rejected_false_when_origin_absent() -> None:
    baseline = _baseline_with_rejections(origins_with_rn={"123 Main St": 1.0})
    assert _origin_previously_rejected(baseline, "999 Other Ave") is False


@pytest.mark.parametrize("rn", [0.0, 0.5, 1.0, 10.0])
def test_origin_previously_rejected_boundary(rn: float) -> None:
    """Strict >0 — any positive r_n fires; 0.0 does not."""
    baseline = _baseline_with_rejections(origins_with_rn={"k": rn})
    assert _origin_previously_rejected(baseline, "k") is (rn > 0.0)


# ---------------------------------------------------------------------------
# ip_previously_rejected (mirror of origin)
# ---------------------------------------------------------------------------


def test_ip_previously_rejected_true_when_rn_positive() -> None:
    baseline = _baseline_with_rejections(ips_with_rn={"1.2.3.4": 1.0})
    assert _ip_previously_rejected(baseline, "1.2.3.4") is True


def test_ip_previously_rejected_false_when_rn_zero() -> None:
    baseline = CustomerBaseline.empty(tenant_id=1, customer_id=1)
    baseline.ip_stats["1.2.3.4"] = {"n": 10.0, "r_n": 0.0, "last": "2026-05-27"}
    assert _ip_previously_rejected(baseline, "1.2.3.4") is False


def test_ip_previously_rejected_false_when_ip_absent() -> None:
    baseline = _baseline_with_rejections(ips_with_rn={"1.2.3.4": 1.0})
    assert _ip_previously_rejected(baseline, "5.6.7.8") is False
