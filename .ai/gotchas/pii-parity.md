# PII HMAC cross-language parity guard

The async-worker `internal/pii` and `internal/statdict` packages expose five
symbols (four HMAC-producing helpers plus one stat-dict membership lookup)
that have **no production caller today**:

- `pii.HmacEmail`, `pii.HmacPhone`, `pii.HmacEmailDomain`, `pii.HmacPhonePrefix`
- `statdict.ContainsHMAC` (consumes a raw `[]byte` HMAC — production code only ever holds the hex string)

The gateway pre-hashes PII in `services/gateway/app/enrichment.py` and publishes
hex strings on `stream:audit`; the worker consumes hex via
`audit/handler.go` and never hashes a raw email or phone. The only HMAC helpers
actually invoked on the Go side are `HmacUserAgentFamily` and `HmacASNOrg`,
which hash values the gateway does not pre-hash.

**Why we keep them:** the package's test suite (`hash_test.go`) compares
output bit-exactly against golden vectors pinned in
`proto/test_fixtures/pii_hash_vectors.json` so any drift in Python's
normalization in `services/gateway/app/pii_hash.py` fails the Go-side
regression test. Deleting the helpers would silently lose that guard and
would force a careful re-implementation if a future code path needs Go-side
hashing (incident recovery, backfill, dual-write migration, CDC-driven worker
emission).

**Expected lint output:** all five symbols carry `//lint:ignore U1000` so
`staticcheck` is silent. `deadcode ./cmd/...` (which ignores `//lint:ignore`)
**will still flag them** — this is intentional. Do not delete or "fix" the
flag; future audits should treat the five symbols as documented exceptions.
