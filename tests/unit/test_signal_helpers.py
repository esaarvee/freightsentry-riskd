"""Unit tests for app/signal_helpers.py — pure-function coverage.

Tests are parametrised where the helper is a classifier (one row per
positive/negative case). Constants (THROWAWAY_DOMAINS, EMAIL_BLOCKLIST,
KEYBOARD_MASH, _DATACENTER_*) are spot-checked rather than enumerated —
the tuning lives in `signal_helpers.py`.
"""

import math

import pytest

from app.signal_helpers import (
    EMAIL_BLOCKLIST,
    KEYBOARD_MASH,
    THROWAWAY_DOMAINS,
    address_match,
    email_domain,
    haversine_km,
    hmac_hex,
    is_datacenter_asn,
    is_email_blocklisted,
    is_email_disposable,
    is_email_suspicious_pattern,
    is_phone_dummy_pattern,
    netblock_16,
    netblock_24,
    normalize_address,
    normalize_email,
    normalize_phone,
)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Foo@Example.COM", "foo@example.com"),
        ("  trim@me.com  ", "trim@me.com"),
        ("", ""),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (555) 123-4567", "5551234567"),
        ("555.123.4567", "5551234567"),
        ("123", "123"),  # <10 digits passes through (caller's job to validate)
        ("", ""),
        ("+44 20 7946 0958", "2079460958"),  # international → last 10
    ],
)
def test_normalize_phone(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123 Main St.", "123 main st"),
        ("  Multi   Spaces  ", "multi spaces"),
        ("123-A Bay Street", "123-a bay street"),
        ("123 Main St, Toronto, ON M5V 3K9", "123 main st, toronto, on m5v3k9"),
        ("", ""),
    ],
)
def test_normalize_address(raw: str, expected: str) -> None:
    assert normalize_address(raw) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("123 Main St", "123 main st", True),
        ("12-123 Main St", "123 Main St", True),  # unit prefix stripped
        ("123 Main St", "456 Oak Rd", False),
        ("123 Main St", "", False),
    ],
)
def test_address_match(a: str, b: str, expected: bool) -> None:
    assert address_match(a, b) is expected


# ---------------------------------------------------------------------------
# HMAC
# ---------------------------------------------------------------------------


def test_hmac_hex_deterministic() -> None:
    """Same input + secret → same output (no salt, no randomness)."""
    secret = b"test-secret-32-bytes-or-more....."
    assert hmac_hex("foo@bar.com", secret) == hmac_hex("foo@bar.com", secret)


def test_hmac_hex_secret_dependent() -> None:
    """Different secrets → different outputs for the same value."""
    a = hmac_hex("x", b"secret-a")
    b = hmac_hex("x", b"secret-b")
    assert a != b


def test_hmac_hex_value_dependent() -> None:
    secret = b"k" * 32
    assert hmac_hex("a", secret) != hmac_hex("b", secret)


def test_hmac_hex_no_lru_cache() -> None:
    """hmac_hex must NOT be lru-cached (secret-rotation hazard)."""
    assert not hasattr(hmac_hex, "cache_info"), (
        "hmac_hex is lru-cached — secret-rotation hazard per "
        ".ai/gotchas/python.md"
    )


def test_hmac_hex_output_shape() -> None:
    """SHA-256 hex digest = 64 lowercase hex chars."""
    h = hmac_hex("x", b"k")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Email classifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("alice@example.com", "example.com"),
        ("BOB@Example.COM", "example.com"),
        ("noatsign", ""),
        ("multi@dots@host.com", "host.com"),  # rfind picks the rightmost
        ("", ""),
    ],
)
def test_email_domain(email: str, expected: str) -> None:
    assert email_domain(email) == expected


@pytest.mark.parametrize(
    "email",
    ["alice@mailinator.com", "BOB@YOPMAIL.COM", "x@10minutemail.com"],
)
def test_is_email_disposable_positive(email: str) -> None:
    assert is_email_disposable(email) is True


@pytest.mark.parametrize("email", ["alice@example.com", "ops@acme.example", ""])
def test_is_email_disposable_negative(email: str) -> None:
    assert is_email_disposable(email) is False


def test_email_blocklist_membership() -> None:
    for entry in EMAIL_BLOCKLIST:
        assert is_email_blocklisted(entry) is True
    assert is_email_blocklisted("real@user.com") is False


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        ("a", True),  # too short
        ("ab", True),  # too short
        ("12345", True),  # all-digit
        ("asdf", True),  # keyboard mash
        ("qwerty123", True),
        ("alice", False),
        ("john.doe", False),
    ],
)
def test_is_email_suspicious_pattern(local: str, expected: bool) -> None:
    assert is_email_suspicious_pattern(f"{local}@example.com") is expected


def test_keyboard_mash_constants_cover_expected_patterns() -> None:
    for pattern in ("asdf", "qwer", "zxcv", "qwerty", "1234"):
        assert pattern in KEYBOARD_MASH


def test_throwaway_domains_spot_check() -> None:
    for domain in ("mailinator.com", "yopmail.com", "10minutemail.com"):
        assert domain in THROWAWAY_DOMAINS


# ---------------------------------------------------------------------------
# Phone classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("5551234567", False),  # real-looking 10-digit
        ("+1 (555) 123-4567", False),
        ("1111111111", True),  # all same digit
        ("1234567890", True),  # ascending
        ("0987654321", True),  # descending
        ("123", True),  # too short
        ("", True),
        ("123456789012345678", True),  # too long
    ],
)
def test_is_phone_dummy_pattern(phone: str, expected: bool) -> None:
    assert is_phone_dummy_pattern(phone) is expected


# ---------------------------------------------------------------------------
# ASN classifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "asn_org",
    [
        "Hetzner Online GmbH",
        "OVH SAS",
        "DigitalOcean, LLC",
        "Equinix Inc.",
        "eStruxture Data Centers",
        "Vultr Holdings, LLC",
        "Some Cloud Hosting Co",
        "Co-location Services",
        "Generic VPS Provider",
    ],
)
def test_is_datacenter_asn_positive(asn_org: str) -> None:
    assert is_datacenter_asn(asn_org) is True


@pytest.mark.parametrize(
    "asn_org",
    [
        "Bell Canada",
        "Rogers Communications",
        "Comcast Cable",
        "Verizon Wireless",
        "",
        None,
    ],
)
def test_is_datacenter_asn_negative(asn_org: str | None) -> None:
    assert is_datacenter_asn(asn_org) is False


# ---------------------------------------------------------------------------
# Netblock helpers
# ---------------------------------------------------------------------------


def test_netblock_16() -> None:
    assert netblock_16("192.168.42.100") == "192.168.0.0"
    assert netblock_16("10.0.5.7") == "10.0.0.0"


def test_netblock_24() -> None:
    assert netblock_24("192.168.42.100") == "192.168.42.0"
    assert netblock_24("203.0.113.5") == "203.0.113.0"


def test_netblock_invalid_raises() -> None:
    with pytest.raises(ValueError):
        netblock_16("not-an-ip")


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------


def test_haversine_zero_for_identical_points() -> None:
    assert haversine_km(43.6532, -79.3832, 43.6532, -79.3832) == pytest.approx(0.0)


def test_haversine_toronto_to_montreal() -> None:
    """Toronto (43.6532, -79.3832) → Montreal (45.5017, -73.5673) ≈ 504 km."""
    distance = haversine_km(43.6532, -79.3832, 45.5017, -73.5673)
    assert distance == pytest.approx(504, abs=10)


def test_haversine_antipodes() -> None:
    """Antipodal points: ~half Earth's circumference (~20015 km)."""
    distance = haversine_km(0.0, 0.0, 0.0, 180.0)
    assert distance == pytest.approx(math.pi * 6371.0, rel=0.001)


@pytest.mark.parametrize(
    ("lat1", "lon1", "lat2", "lon2"),
    [
        (None, 0.0, 0.0, 0.0),
        (0.0, None, 0.0, 0.0),
        (0.0, 0.0, None, 0.0),
        (0.0, 0.0, 0.0, None),
        (None, None, None, None),
    ],
)
def test_haversine_none_inputs_return_zero(
    lat1: float | None, lon1: float | None, lat2: float | None, lon2: float | None
) -> None:
    assert haversine_km(lat1, lon1, lat2, lon2) == 0.0
