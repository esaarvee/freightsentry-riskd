"""Pure-Python signal helpers — stateless, no I/O, no DB.

Constants and functions used by signal modules (`app/signals/*.py`) and
the baseline / enrichment layers. The module is import-side-effect free
and synchronous; tests are unit-only.

Constants `THROWAWAY_DOMAINS`, `EMAIL_BLOCKLIST`, `KEYBOARD_MASH`,
`_DATACENTER_KEYWORDS`, `_DATACENTER_PROVIDERS` come from freight_risk's
empirically-tuned lists. Tuning revisions land here, not at signal
call-sites.

`hmac_hex` is intentionally NOT decorated with `@lru_cache` (the freight_risk
original was — it's a secret-rotation hazard per `.ai/gotchas/python.md`).
"""

import hashlib
import hmac
import ipaddress
import math
import re

# ---------------------------------------------------------------------------
# Email constants (lowercased domains / addresses)
# ---------------------------------------------------------------------------

THROWAWAY_DOMAINS: frozenset[str] = frozenset({
    "10minutemail.com", "20minutemail.com", "30minutemail.com",
    "anonbox.net", "deadaddress.com", "dispostable.com",
    "fakeinbox.com", "getairmail.com", "guerrillamail.com",
    "guerrillamail.org", "guerrillamailblock.com", "harakirimail.com",
    "incognitomail.com", "incognitomail.net", "jetable.org",
    "mailcatch.com", "mailinator.com", "mailmoat.com",
    "mailnator.com", "mailnesia.com", "mailtemp.info",
    "maildrop.cc", "mintemail.com", "mohmal.com",
    "moot.email", "mvrht.com", "nada.email",
    "spamgourmet.com", "tempmail.de", "tempmailo.com",
    "tempr.email", "throwawaymail.com", "trashmail.com",
    "trashmail.de", "trashmail.io", "yopmail.com",
    "yopmail.net",
})

EMAIL_BLOCKLIST: frozenset[str] = frozenset({
    "abuse@example.com", "noreply@example.com", "no-reply@example.com",
    "test@test.com", "test@example.com", "fake@fake.com",
    "anonymous@anonymous.com", "user@example.com",
    "admin@example.com", "root@localhost", "fraud@fraud.com",
})

KEYBOARD_MASH: tuple[str, ...] = (
    "asdf", "qwer", "zxcv", "qwerty", "asdfgh", "wasd", "1234", "abcd",
)

# ---------------------------------------------------------------------------
# ASN heuristics — datacenter vs cloud vs residential signals
# ---------------------------------------------------------------------------

_DATACENTER_KEYWORDS: tuple[str, ...] = (
    "data center", "data centre", "datacenter", "datacentre",
    "colocation", "co-location", "web hosting", "hosting services",
    "dedicated server", "dedicated servers", "vps", "virtual private server",
    "cloud", "cloud services", "cloud computing",
)

_DATACENTER_PROVIDERS: tuple[str, ...] = (
    "estruxture", "equinix", "ovh", "ovh sas", "hetzner",
    "digitalocean", "linode", "vultr", "rackspace", "softlayer",
    "leaseweb", "scaleway", "contabo", "interserver", "iweb",
    "akamai", "fastly", "limelight",
)

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_email(email: str) -> str:
    """Lowercase + strip whitespace. Returns empty string for falsy input."""
    if not email:
        return ""
    return email.strip().lower()


_DIGITS_RE = re.compile(r"\D")


def normalize_phone(phone: str) -> str:
    """Strip non-digits; keep last 10 (NANP-style). Returns empty for falsy."""
    if not phone:
        return ""
    digits = _DIGITS_RE.sub("", phone)
    return digits[-10:] if len(digits) >= 10 else digits


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[,.;]+")
_CA_POSTAL_RE = re.compile(r"([a-z]\d[a-z])\s+(\d[a-z]\d)")
_UNIT_PREFIX_RE = re.compile(r"^\d+-")


def normalize_address(address: str) -> str:
    """Lowercase, collapse whitespace, normalise Canadian postal (remove
    inner space), collapse punctuation. Best-effort fuzzy-equality input."""
    if not address:
        return ""
    s = _WS_RE.sub(" ", address.lower()).strip()
    s = _CA_POSTAL_RE.sub(r"\1\2", s)
    s = _PUNCT_RE.sub(",", s)
    return s.strip(", ")


def address_match(a: str, b: str) -> bool:
    """True if two addresses are the same after normalisation, OR after
    additionally stripping a leading unit prefix like `12-` from one side."""
    na, nb = normalize_address(a), normalize_address(b)
    if na == nb:
        return True
    return _UNIT_PREFIX_RE.sub("", na) == _UNIT_PREFIX_RE.sub("", nb)


# ---------------------------------------------------------------------------
# HMAC
# ---------------------------------------------------------------------------


def hmac_hex(value: str, secret: bytes) -> str:
    """HMAC-SHA256 hex digest. Never `@lru_cache`-decorated (secret-rotation
    hazard). Per-request cost is negligible at 100 TPS."""
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Email classification
# ---------------------------------------------------------------------------


def email_domain(email: str) -> str:
    """Lowercased domain portion (everything after `@`). Empty if no `@`."""
    norm = normalize_email(email)
    at = norm.rfind("@")
    return norm[at + 1 :] if at >= 0 else ""


def is_email_disposable(email: str) -> bool:
    return email_domain(email) in THROWAWAY_DOMAINS


def is_email_blocklisted(email: str) -> bool:
    return normalize_email(email) in EMAIL_BLOCKLIST


def is_email_suspicious_pattern(email: str) -> bool:
    """Heuristics on the local part: too short, all-digit, or matches a
    common keyboard-mash pattern."""
    norm = normalize_email(email)
    at = norm.find("@")
    local = norm[:at] if at >= 0 else norm
    if not local or len(local) <= 2 or local.isdigit():
        return True
    return any(pat in local for pat in KEYBOARD_MASH)


# ---------------------------------------------------------------------------
# Phone classification
# ---------------------------------------------------------------------------


def is_phone_dummy_pattern(phone: str) -> bool:
    """All-one-digit, ascending sequence, descending sequence, or
    out-of-range length (after digit-only normalisation)."""
    if not phone:
        return True
    digits = _DIGITS_RE.sub("", phone)
    if len(digits) < 10 or len(digits) > 15:
        return True
    if len(set(digits)) == 1:
        return True
    # Ascending: 1234567890 — each char is (prev + 1) mod 10
    if all(int(digits[i]) == (int(digits[0]) + i) % 10 for i in range(len(digits))):
        return True
    # Descending: 9876543210 — each char is (prev - 1) mod 10
    return all(int(digits[i]) == (int(digits[0]) - i) % 10 for i in range(len(digits)))


# ---------------------------------------------------------------------------
# ASN classification
# ---------------------------------------------------------------------------


def is_datacenter_asn(asn_org: str | None) -> bool:
    """True if the ASN org text contains a datacenter-keyword phrase or
    matches a known datacenter-provider name (eStruxture, Equinix, OVH,
    Hetzner, etc.)."""
    if not asn_org:
        return False
    s = asn_org.lower()
    return any(kw in s for kw in _DATACENTER_KEYWORDS) or any(
        p in s for p in _DATACENTER_PROVIDERS
    )


# ---------------------------------------------------------------------------
# IP netblock helpers
# ---------------------------------------------------------------------------


def netblock_16(ip: str) -> str:
    """Return the /16 network address as a string. Raises ValueError on
    invalid input (let the caller decide whether to swallow)."""
    return str(ipaddress.ip_network(f"{ip}/16", strict=False).network_address)


def netblock_24(ip: str) -> str:
    """Return the /24 network address as a string."""
    return str(ipaddress.ip_network(f"{ip}/24", strict=False).network_address)


# ---------------------------------------------------------------------------
# Geo distance
# ---------------------------------------------------------------------------

_EARTH_RADIUS_KM = 6371.0


def composite_threat_score(
    *,
    fh_level1: bool,
    fh_level2: bool,
    ip2p_threat: str | None,
) -> float:
    """Compose [0, 1] threat score from FireHOL hits + IP2Proxy tags.

    Used by build_context to populate the `ip_threat_score` Context
    field. Pure function — caller passes the already-extracted
    enrichment fields.
    """
    score = 0.0
    if fh_level1:
        score += 0.8
    elif fh_level2:
        score += 0.5
    if ip2p_threat:
        if "BOTNET" in ip2p_threat:
            score += 0.4
        if "SCANNER" in ip2p_threat:
            score += 0.3
        if "SPAM" in ip2p_threat:
            score += 0.2
    return min(1.0, score)


def haversine_km(
    lat1: float | None,
    lon1: float | None,
    lat2: float | None,
    lon2: float | None,
) -> float:
    """Great-circle distance in km between two lat/lon pairs. Returns 0.0
    if any coordinate is None (signal inert, not an error)."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))
