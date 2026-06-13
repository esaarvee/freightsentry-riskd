"""Named, role-specific test IP constants (RFC-5737 documentation ranges).

Disjoint by purpose: a clean-intent IP can never share a /32 with a
malicious-intent IP. Determinism comes from the per-test
`_isolate_enrichment_state` fixture (truncates ip_enrichment + resets the
shared enricher); these constants make each test's intent explicit and
stop a future test from reusing a malicious /32 as a clean one. All
addresses are RFC-5737 (never routable).

Scope: this is a TARGETED migration of the collision-relevant scoring
tests. Some files still use raw doc-IP literals (notably test_velocity.py,
which keeps clean-intent IPs inside 192.0.2.0/24 as load-bearing per-IP
SQL keys). That is safe — determinism is provided by per-test truncation,
not address-space discipline — but the clean/malicious /24 split is only
enforced within the migrated files. A future pass extending the migration
(e.g. velocity) must move those clean keys into 198.51.100.0/24 to honour
the split."""

# Clean residential IPs (no threat flags, non-cloud). Distinct values for
# tests that need several independent clean IPs (e.g. velocity per-IP counts).
CLEAN_IP = "198.51.100.10"
CLEAN_IP_2 = "198.51.100.11"
CLEAN_IP_3 = "198.51.100.12"
CLEAN_IP_4 = "198.51.100.13"

# Malicious IPs by threat class (kept in 192.0.2.0/24, the malicious range).
BLACKLISTED_IP = "192.0.2.81"  # FireHOL Level 1 -> Layer-1 BLOCK (blacklisted_ip)
FIREHOL_L2_IP = "192.0.2.71"  # FireHOL Level 2 -> stays on the Layer-3 path
# Reserved threat-class vocabulary for future tests — give the next author
# a named slot in the malicious range rather than a fresh literal.
TOR_IP = "192.0.2.91"
VPN_IP = "192.0.2.92"
PROXY_IP = "192.0.2.93"
