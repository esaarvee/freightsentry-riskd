# Phase 4 — Batch 4D Plan — Admin endpoints + audit refresh + Phase 4 wrap

> **Status (2026-06-01)**: Pending operator approval. Approval may be deferred until after 4C execution reports.

Batch 4D adds two read-only admin endpoints (`GET /api/v1/admin/decisions/{request_id}` and `GET /api/v1/admin/customers/{external_id}/baseline`), introduces role-based auth enforcement against the existing `auth.role` field (sourced from `api_tokens.role`), publishes a Phase 4 audit doc (`docs/security-audit-rls-phase-4.md`), and closes Phase 4 with per-batch + aggregate reports.

**Auth path resolution.** The Phase 4 prompt references "`app_users.role`" for admin enforcement, but the actual auth flow reads `role` from `api_tokens` (per `app/auth.py:76-91`), and that role is already exposed as `AuthContext.role`. The `app_users` table (Phase 1 schema) is currently unused. 4D enforces admin via `auth.role == "admin"` from `api_tokens` (the operational reality), matching the existing `seeded_admin_token` test fixture pattern. **Documented deviation from prompt text below.**

Target: **6 commits**.

---

## Decisions absorbed (batch-relevant subset)

| Decision | Value | Source |
|---|---|---|
| Admin auth source column | **`api_tokens.role`** (operational reality, AuthContext.role already populated). **DEVIATES from Phase 4 prompt's "app_users.role"** — the prompt's framing conflicts with the existing auth.py flow. `app_users` table exists but is not wired to auth. Operator confirmation requested at end-of-plan checkpoint. | Phase 4 verification (this plan); `app/auth.py:76-91` |
| Admin role check | `auth.role == "admin"` returns from the auth dependency. If not admin → 403. | Phase 4 prompt + existing AuthContext shape |
| Admin endpoint scope | Tenant-bounded — admin can only see decisions/customers within their own tenant. Cross-tenant attempts return 404 (NOT 403 — 404 hides the existence of the other tenant's resource). | Phase 4 prompt |
| Admin endpoint surface | 2 read-only GET endpoints. NO write endpoints in Phase 4 (per decisions.md § Endpoints). | Phase 4 prompt + decisions.md |
| Decision endpoint | `GET /api/v1/admin/decisions/{request_id}` — returns decision row + linked shipment row + triggered_rules + risk_factors. | Phase 4 prompt |
| Customer baseline endpoint | `GET /api/v1/admin/customers/{external_id}/baseline` — returns customer row + baseline state with stat-dicts TRUNCATED for response size. Full dicts available via separate endpoint if needed → deferred to Phase 5+. | Phase 4 prompt |
| Stat-dict truncation strategy | Return top-10 entries per stat-dict by `n` value, descending. Total entries count + truncated flag returned alongside. | Phase 4 prompt judgment |
| PII handling on admin responses | Customer baseline response: `email_hmacs`, `phone_hmacs`, `rejected_email_hmacs`, `rejected_phone_hmacs` returned as HMAC hex strings (no further obfuscation — they're already HMAC'd). `customers.business_name` and `registered_address` returned as-is (admin within tenant is authorized for tenant data). Decision response: triggered_rules + risk_factors only — no PII. | decisions.md § Multi-tenancy + Phase 4 prompt |
| HTTP status codes | 200 (found), 401 (no auth — existing pattern), 403 (auth but non-admin), 404 (admin but resource not in tenant), 500 (server error). | Phase 4 prompt |
| Both endpoints idempotent reads | No state changes. No transactions needed beyond the implicit one for `set_tenant_id`. | Standard practice |
| RLS coverage | Both admin endpoints query tables already covered by Phase 1 RLS policies (decisions, shipments, customers, customer_baselines). Defense-in-depth `tenant_id = $1` on every WHERE/JOIN. | decisions.md § Multi-tenancy + Phase 3 audit |
| Audit doc strategy | NEW file `docs/security-audit-rls-phase-4.md` as a delta over Phase 3 doc. Preserves Phase 3 as a snapshot; Phase 4 audit is the delta. Inventory grows by ~6 query rows (3 from each endpoint). | Phase 4 prompt watch-point |
| Admin write endpoints | OUT of scope for Phase 4. Decision overrides, manual feedback, etc. deferred to v2+. | Phase 4 prompt |
| Reuse of `seeded_admin_token` fixture | YES — already exists in `tests/conftest.py:133-147`. No new fixture required. | conftest.py |
| Phase 4 wrap report | `REPORT_PHASE_4D.md` (per-batch) + aggregate `REPORT_PHASE_4.md`. Same shape as Phase 3 reports. | Phase 4 prompt |

### Documented deviation (auth source)

The Phase 4 prompt says:

> "Auth enforcement: existing `app_users.role` field exercised for the first time."
> "Admin endpoints exercise the `app_users.role` field for the first time."

But the existing auth flow reads role from `api_tokens` (`app/auth.py:76` — `SELECT tenant_id, role FROM api_tokens WHERE token_hash = $1`) and exposes it as `AuthContext.role`. The `app_users` table exists in Phase 1 schema but is NOT wired to the auth dependency.

Two paths:

1. **Use `auth.role == "admin"` from api_tokens** (this plan's choice). Reuses existing infrastructure; `seeded_admin_token` fixture already covers the test path. No new auth wiring. Zero risk of double-source-of-truth.

2. Wire `app_users.role` into auth (would require fundamentally restructuring `app/auth.py` to look up app_users in addition to api_tokens, and reconciling when both have role values).

Option 1 is the minimum-blast-radius interpretation. Per the autonomous-execution rule, this is a substantive decision that genuinely contradicts the prompt; surfacing at end-of-plan operator checkpoint.

Phase 5+ could add `app_users` wiring if the multi-user-per-tenant admin model is needed.

---

## Workflow context

- 6-step commit cycle per CLAUDE.md. Pre-commit hooks active.
- Reviewer routing per CLAUDE.md triage gate:
  - 4D.1 (admin auth helper / `require_admin_role` dependency): Never-Skip (auth/authorization) → standard panel + security-auditor.
  - 4D.2 (decisions admin endpoint): Never-Skip (new `.py` file under `app/`) → standard panel + db-reviewer + security-auditor + test-reviewer.
  - 4D.3 (customer baseline admin endpoint): Never-Skip → standard panel + db-reviewer + security-auditor + test-reviewer.
  - 4D.4 (role enforcement + cross-tenant tests + 3C.3 canary re-run): test-only → test-reviewer + senior + code-flow + security-auditor.
  - 4D.5 (audit doc): doc-only → doc-reviewer only.
  - 4D.6 (Phase 4 reports + aggregate REPORT_PHASE_4.md): doc-only → doc-reviewer only.

- Reviewer-invocation slice template:
  > `Plan file: PLAN_PHASE_4D.md, current commit: 4D.N (<title>), upcoming commits: 4D.{N+1} through 4D.6 sections. Read only those sections.`

---

## Cross-batch dependencies

- **Consumes from 4A**: `load_tenant_config` (admin endpoints load it for consistency with tenant data scoping; no rule consumption on admin path).
- **Consumes from Phase 1**: `tenants`, `customers`, `shipments`, `decisions`, `customer_baselines`, `api_tokens` tables; existing RLS policies; `app/auth.py::require_api_token` returns `AuthContext`.
- **Consumes from Phase 3**: `decisions.request_type` discriminator; `docs/security-audit-rls-phase-3.md` as the precursor audit doc.
- **Does NOT depend on 4B or 4C functionality**: admin endpoints are read-only; they don't invoke scoring or currency validation.
- **Consumed by Phase 5**: Phase 5's RLS role transition exercises the admin endpoints via the same `riskd_app_login` role; canary test extends to admin endpoints.

---

## 4D.1 — `require_admin_role` dependency in `app/auth.py`

**Theme**: Add a new FastAPI dependency `require_admin_role` that calls `require_api_token` and then checks `auth.role == "admin"`. Returns 403 otherwise. The carve-out `AUTH_ENABLED=false` path still works (synthetic AuthContext with role="tenant" is rejected; tests use the dependency override to inject role="admin").

**Files**:
- `app/auth.py` (EDIT — append `require_admin_role`)
- `tests/unit/test_auth_admin.py` (NEW)
- `tests/integration/test_admin_auth_dependency.py` (NEW)

**Specifics**:

```python
# Appended to app/auth.py

async def require_admin_role(
    auth: Annotated[AuthContext, Depends(require_api_token)],
) -> AuthContext:
    """Authorization layer: principal must carry role='admin' from api_tokens.

    Composes with require_api_token. Returns the same AuthContext (the
    tenant_id is preserved for downstream tenant-scoped queries).

    403 is returned for authenticated-but-not-admin (auth.role != 'admin');
    401 is returned upstream by require_api_token for unauthenticated calls.

    The AUTH_ENABLED=false local-dev carve-out returns AuthContext with
    role='tenant', which fails this check — admin endpoints under local
    dev require AUTH_ENABLED=true. Local admin testing pattern: set
    AUTH_ENABLED=true and seed an admin api_token via the onboarding
    script (Phase 4A.5) with --rotate-token and manually update
    api_tokens.role to 'admin' (Phase 4 onboarding script does not yet
    issue admin tokens — out of scope per 4A.5 decisions).
    """
    if auth.role != "admin":
        _log.info(
            "auth.admin_required_denied",
            tenant_id=auth.tenant_id,
            role=auth.role,
            metric=True,
        )
        raise HTTPException(
            status_code=403,
            detail="admin role required",
        )
    return auth
```

### Unit tests

`tests/unit/test_auth_admin.py` — 5 tests:

1. `require_admin_role(AuthContext(tenant_id=1, role="admin"))` → returns the same AuthContext.
2. `require_admin_role(AuthContext(tenant_id=1, role="tenant"))` → HTTPException(403).
3. `require_admin_role(AuthContext(tenant_id=1, role="reviewer"))` → HTTPException(403) — only "admin" passes.
4. `require_admin_role(AuthContext(tenant_id=1, role=""))` → HTTPException(403).
5. Composition: calling `require_admin_role` invokes `require_api_token` first (verified via mock).

### Integration tests

`tests/integration/test_admin_auth_dependency.py` — 4 tests (use a stub endpoint added inline to test routing):

1. **No auth header** → 401.
2. **Tenant token** → 403 (token validates but role != admin).
3. **Admin token** (`seeded_admin_token`) → endpoint reachable.
4. **Invalid token** → 401.

**Validation**:
- `pytest tests/unit/test_auth_admin.py tests/integration/test_admin_auth_dependency.py -v --asyncio-mode=auto` → 9 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.
- `mypy app/` strict clean.

**Risk**: **High**. Auth/authorization change. Reviewer panel + security-auditor verify the composition with `require_api_token` doesn't bypass token validation.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: 403 logged at INFO; standard auth observability.

**Test changes**: 9 tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (auth/authorization) → standard panel + security-auditor.

---

## 4D.2 — `GET /api/v1/admin/decisions/{request_id}` endpoint

**Theme**: Read-only endpoint returning decision detail. Composes with `require_admin_role` dependency. Tenant-scoped via auth.tenant_id + explicit WHERE filter.

**Files**:
- `app/api/admin.py` (NEW — both admin endpoints in one module to keep the router compact)
- `app/main.py` (EDIT — register the admin router under `/api/v1/admin`)
- `tests/integration/test_admin_decisions_endpoint.py` (NEW)

**Specifics**:

```python
"""Phase 4D admin endpoints — read-only.

Both endpoints require admin role (api_tokens.role == 'admin' via
require_admin_role) and are tenant-bounded: an admin sees only their
own tenant's data; cross-tenant lookups return 404 (hides existence).

NO write endpoints in v1. Decision overrides, manual feedback, etc.
deferred to v2+ per .ai/decisions.md § Endpoints.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, require_admin_role
from app.db import get_conn, set_tenant_id
from app.tenant_config import load_tenant_config

_log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/decisions/{request_id}")
async def get_admin_decision(
    request_id: str,
    auth: Annotated[AuthContext, Depends(require_admin_role)],
) -> dict[str, Any]:
    """Return full decision detail for the given request_id.

    Tenant-bounded: only this admin's tenant_id is searched. Cross-
    tenant lookups return 404 (not 403 — hides existence of other
    tenants' resources).

    Response shape:
        {
          "request_id": "...",
          "request_type": "booking" | "modification",
          "decision": "ALLOW" | "REVIEW" | "BLOCK",
          "score": float,
          "classification": "GREEN" | "YELLOW" | "RED",
          "risk_level": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
          "triggered_rules": [str, ...],
          "risk_factors": [{"name": str, "description": str, "weight": float}, ...],
          "shipment": {
            "id": int,
            "source_ip": "x.x.x.x",
            "origin_city": str | None,
            "origin_country": str | None,
            "destination_city": str | None,
            "destination_country": str | None,
            "value": float,
            "channel": str,
            "booking_ts": "...",
          },
          "created_at": "...",
        }

    PII handling: origin/destination addresses are returned as
    city + country (not the full street address). Email/phone HMACs
    are not surfaced on this endpoint — they're on the baseline
    endpoint, scoped to that admin's tenant_id.
    """
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)
        # Load tenant config for shape consistency with other endpoints
        # (4A wiring); admin endpoint doesn't consult config fields.
        _tc = await load_tenant_config(conn, auth.tenant_id)
        _ = _tc

        row = await conn.fetchrow(
            """
            SELECT
                d.request_id,
                d.request_type,
                d.score,
                d.decision,
                d.classification,
                d.risk_level,
                d.triggered_rules,
                d.risk_factors,
                d.created_at AS decision_created_at,
                s.id            AS shipment_id,
                s.source_ip,
                s.origin,
                s.destination,
                s.value,
                s.channel,
                s.booking_ts
              FROM decisions d
              JOIN shipments s ON s.id = d.shipment_id AND s.tenant_id = d.tenant_id
             WHERE d.tenant_id = $1 AND d.request_id = $2
            """,
            auth.tenant_id,
            request_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="decision not found")

    origin = row["origin"]
    if isinstance(origin, str):
        origin = json.loads(origin)
    destination = row["destination"]
    if isinstance(destination, str):
        destination = json.loads(destination)
    risk_factors_raw = row["risk_factors"]
    if isinstance(risk_factors_raw, str):
        risk_factors_raw = json.loads(risk_factors_raw)

    _log.info(
        "admin.decision_lookup",
        tenant_id=auth.tenant_id,
        request_id=request_id,
        request_type=row["request_type"],
        metric=True,
    )
    return {
        "request_id": row["request_id"],
        "request_type": row["request_type"],
        "decision": row["decision"],
        "score": float(row["score"]),
        "classification": row["classification"],
        "risk_level": row["risk_level"],
        "triggered_rules": list(row["triggered_rules"]),
        "risk_factors": risk_factors_raw,
        "shipment": {
            "id": row["shipment_id"],
            "source_ip": str(row["source_ip"]),
            "origin_city": origin.get("city"),
            "origin_country": origin.get("country"),
            "destination_city": destination.get("city"),
            "destination_country": destination.get("country"),
            "value": float(row["value"]),
            "channel": row["channel"],
            "booking_ts": row["booking_ts"].isoformat(),
        },
        "created_at": row["decision_created_at"].isoformat(),
    }
```

### Main router wiring

In `app/main.py`, register:

```python
from app.api.admin import router as admin_router
app.include_router(admin_router, prefix="/api/v1")
```

### Integration tests

`tests/integration/test_admin_decisions_endpoint.py` — 8 tests:

1. **Admin token + existing decision in tenant** → 200, full response shape.
2. **Tenant token + existing decision** → 403 (require_admin_role).
3. **Admin token + non-existent request_id** → 404.
4. **Cross-tenant admin lookup** — tenant_a admin attempts to look up tenant_b's decision (same request_id, different tenant) → 404 (hides existence).
5. **No auth** → 401.
6. **Modification decision** — admin lookup on a modification's request_id → 200; `request_type == "modification"`.
7. **Risk factor shape** — assert `risk_factors[0]` keys: `name`, `description`, `weight`.
8. **Shipment city/country** — assert `shipment.origin_city` and `destination_city` populated when payload had them; null when missing.

**Validation**:
- `pytest tests/integration/test_admin_decisions_endpoint.py -v --asyncio-mode=auto` → 8 tests pass.
- `pytest tests/integration/test_rls_enforcement_under_riskd_app.py -v --asyncio-mode=auto` — 3C.3 canary still passes (RLS doesn't block admin reads; tenant_id filter is the defense).
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.
- `ruff check app/ tests/` clean. `mypy app/` strict clean.

**Risk**: **High**. New `.py` file under `app/`; new endpoint surface. Cross-tenant scoping is critical (test 4); a missing `tenant_id` filter would leak.

**Reversibility**: Medium — `git revert` removes endpoint + router registration cleanly.

**Pre-commit verification**: All gates green.

**Observability**: `admin.decision_lookup` structured log per call.

**Test changes**: 8 integration tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip (new `.py` file under `app/`) → standard panel + db-reviewer + security-auditor + test-reviewer.

---

## 4D.3 — `GET /api/v1/admin/customers/{external_id}/baseline` endpoint

**Theme**: Read-only customer baseline detail endpoint. Truncated stat-dicts to keep response size bounded. Composes with `require_admin_role`.

**Files**:
- `app/api/admin.py` (EDIT — append second route)
- `tests/integration/test_admin_customers_endpoint.py` (NEW)

**Specifics**:

```python
# Appended to app/api/admin.py

_STAT_DICT_TRUNCATION_LIMIT = 10


def _truncate_stat_dict(raw: Any, *, limit: int = _STAT_DICT_TRUNCATION_LIMIT) -> dict[str, Any]:
    """Return top-N entries by `n` descending, plus a count of total entries.

    Stat-dict shape: `{key: {n, r_n, last, type?}}`. Sorted by `n`
    desc to surface the highest-frequency entries (most operationally
    interesting). If the stat-dict is small (< limit entries), returns
    everything plus `truncated=False`.

    Returns:
        {"entries": [{...}, ...], "total_count": int, "truncated": bool}
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return {"entries": [], "total_count": 0, "truncated": False}
    items = list(raw.items())
    items.sort(key=lambda kv: float(kv[1].get("n", 0)), reverse=True)
    total = len(items)
    entries = [{"key": k, **v} for k, v in items[:limit]]
    return {"entries": entries, "total_count": total, "truncated": total > limit}


def _truncate_hmac_set(raw: Any, *, limit: int = _STAT_DICT_TRUNCATION_LIMIT) -> dict[str, Any]:
    """Truncate a flat HMAC set/dict (e.g., rejected_email_hmacs).

    Returns up to `limit` HMAC hex strings + a total count. HMACs are
    pre-obfuscated (no PII visible) so direct surfacing is acceptable
    within the tenant-bounded admin scope.
    """
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        keys = list(raw.keys())
    elif isinstance(raw, list):
        keys = list(raw)
    else:
        return {"entries": [], "total_count": 0, "truncated": False}
    total = len(keys)
    return {"entries": keys[:limit], "total_count": total, "truncated": total > limit}


@router.get("/customers/{external_id}/baseline")
async def get_admin_customer_baseline(
    external_id: str,
    auth: Annotated[AuthContext, Depends(require_admin_role)],
) -> dict[str, Any]:
    """Return customer record + baseline state (truncated for response size).

    Tenant-bounded. Cross-tenant lookups return 404.

    Stat-dicts truncated to top-10 by `n` desc (configurable via
    _STAT_DICT_TRUNCATION_LIMIT). Total count + truncated flag included
    per dict. Full dicts available via Phase 5+ separate endpoint if
    needed.

    PII surfaced:
      - business_name, registered_address (admin is authorized for
        their tenant's customer data)
      - HMAC hex strings for email/phone dimensions (already obfuscated)
    """
    async with get_conn() as conn, conn.transaction():
        await set_tenant_id(conn, auth.tenant_id)
        _tc = await load_tenant_config(conn, auth.tenant_id)
        _ = _tc

        customer_row = await conn.fetchrow(
            """
            SELECT id, external_id, registered_address, business_name,
                   is_api_partner, first_seen, last_seen, flagged_count,
                   fraud_confirmed_count, total_shipments, created_at
              FROM customers
             WHERE tenant_id = $1 AND external_id = $2
            """,
            auth.tenant_id,
            external_id,
        )
        if customer_row is None:
            raise HTTPException(status_code=404, detail="customer not found")

        baseline_row = await conn.fetchrow(
            """
            SELECT
                origin_stats, dest_stats, lane_stats, ip_stats,
                ip_netblock_stats, ip_asn_stats, country_stats,
                origin_ip_country_stats,
                email_hmacs, phone_hmacs,
                rejected_email_hmacs, rejected_phone_hmacs,
                email_domain_stats, phone_prefix_stats,
                ip_type_hist, hour_hist, weekday_hist, channel_hist,
                value_n, value_mean, value_m2,
                cadence_n, cadence_mean_h, cadence_m2_h,
                last_booking_ts, last_booking_country,
                decay_anchor_date, first_seen AS baseline_first_seen,
                last_seen AS baseline_last_seen, updated_at
              FROM customer_baselines
             WHERE tenant_id = $1 AND customer_id = $2
            """,
            auth.tenant_id,
            customer_row["id"],
        )

    _log.info(
        "admin.customer_baseline_lookup",
        tenant_id=auth.tenant_id,
        customer_external_id=external_id,
        baseline_present=baseline_row is not None,
        metric=True,
    )

    response: dict[str, Any] = {
        "customer": {
            "external_id": customer_row["external_id"],
            "registered_address": customer_row["registered_address"],
            "business_name": customer_row["business_name"],
            "is_api_partner": customer_row["is_api_partner"],
            "first_seen": customer_row["first_seen"].isoformat(),
            "last_seen": customer_row["last_seen"].isoformat(),
            "flagged_count": customer_row["flagged_count"],
            "fraud_confirmed_count": customer_row["fraud_confirmed_count"],
            "total_shipments": customer_row["total_shipments"],
            "created_at": customer_row["created_at"].isoformat(),
        },
        "baseline": None,
    }

    if baseline_row is not None:
        response["baseline"] = {
            "origin_stats": _truncate_stat_dict(baseline_row["origin_stats"]),
            "dest_stats": _truncate_stat_dict(baseline_row["dest_stats"]),
            "lane_stats": _truncate_stat_dict(baseline_row["lane_stats"]),
            "ip_stats": _truncate_stat_dict(baseline_row["ip_stats"]),
            "ip_netblock_stats": _truncate_stat_dict(baseline_row["ip_netblock_stats"]),
            "ip_asn_stats": _truncate_stat_dict(baseline_row["ip_asn_stats"]),
            "country_stats": _truncate_stat_dict(baseline_row["country_stats"]),
            "origin_ip_country_stats": _truncate_stat_dict(baseline_row["origin_ip_country_stats"]),
            "email_hmacs": _truncate_hmac_set(baseline_row["email_hmacs"]),
            "phone_hmacs": _truncate_hmac_set(baseline_row["phone_hmacs"]),
            "rejected_email_hmacs": _truncate_hmac_set(baseline_row["rejected_email_hmacs"]),
            "rejected_phone_hmacs": _truncate_hmac_set(baseline_row["rejected_phone_hmacs"]),
            "email_domain_stats": _truncate_stat_dict(baseline_row["email_domain_stats"]),
            "phone_prefix_stats": _truncate_stat_dict(baseline_row["phone_prefix_stats"]),
            "ip_type_hist": dict(baseline_row["ip_type_hist"]) if not isinstance(baseline_row["ip_type_hist"], str) else json.loads(baseline_row["ip_type_hist"]),
            "hour_hist": dict(baseline_row["hour_hist"]) if not isinstance(baseline_row["hour_hist"], str) else json.loads(baseline_row["hour_hist"]),
            "weekday_hist": dict(baseline_row["weekday_hist"]) if not isinstance(baseline_row["weekday_hist"], str) else json.loads(baseline_row["weekday_hist"]),
            "channel_hist": dict(baseline_row["channel_hist"]) if not isinstance(baseline_row["channel_hist"], str) else json.loads(baseline_row["channel_hist"]),
            "value_n": float(baseline_row["value_n"]),
            "value_mean": float(baseline_row["value_mean"]),
            "value_m2": float(baseline_row["value_m2"]),
            "cadence_n": float(baseline_row["cadence_n"]),
            "cadence_mean_h": float(baseline_row["cadence_mean_h"]),
            "cadence_m2_h": float(baseline_row["cadence_m2_h"]),
            "last_booking_ts": baseline_row["last_booking_ts"].isoformat() if baseline_row["last_booking_ts"] else None,
            "last_booking_country": baseline_row["last_booking_country"],
            "decay_anchor_date": baseline_row["decay_anchor_date"].isoformat() if baseline_row["decay_anchor_date"] else None,
            "updated_at": baseline_row["updated_at"].isoformat(),
        }

    return response
```

### Unit tests for truncation helpers

In `tests/unit/test_admin_truncation_helpers.py` (NEW) — 6 tests:

1. Empty stat-dict → `entries=[], total_count=0, truncated=False`.
2. 5 entries → all returned, `truncated=False`.
3. 15 entries → top 10 by n desc returned, `truncated=True`.
4. JSONB-as-string input parses correctly.
5. Truncation by `n` descending (mix of low + high entries).
6. HMAC set helper truncates list-form and dict-form input.

### Integration tests

`tests/integration/test_admin_customers_endpoint.py` — 8 tests:

1. **Admin + existing customer with baseline** → 200, full shape.
2. **Admin + existing customer without baseline** → 200, `baseline: null`.
3. **Cross-tenant** → 404.
4. **Non-existent external_id** → 404.
5. **Tenant token** → 403.
6. **No auth** → 401.
7. **Truncation**: customer with 20 origin_stats entries → response shows top 10 + `total_count=20, truncated=true`.
8. **HMAC fields surfaced**: customer with rejected_email_hmacs entries → returned as HMAC hex strings in `entries[]`.

**Validation**:
- `pytest tests/unit/test_admin_truncation_helpers.py tests/integration/test_admin_customers_endpoint.py -v --asyncio-mode=auto` → 14 tests pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.

**Risk**: **High**. New endpoint surface with PII surfacing in response (HMAC entries, business_name). Reviewer panel + security-auditor verify tenant-scoping invariants.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: `admin.customer_baseline_lookup` log per call.

**Test changes**: 6 + 8 = 14 tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: Never-Skip → standard panel + db-reviewer + security-auditor + test-reviewer.

---

## 4D.4 — Cross-cutting tests: role enforcement matrix + cross-tenant + 3C.3 canary

**Theme**: Cross-cutting integration tests that exercise the full role × endpoint × tenant matrix described in the Phase 4 prompt watch-point. Includes the 3C.3 RLS canary re-run to confirm admin endpoints don't break tenant isolation under the non-superuser RLS role.

**Files**:
- `tests/integration/test_admin_role_matrix.py` (NEW)
- `tests/integration/test_admin_cross_tenant_isolation.py` (NEW)
- `tests/integration/test_rls_enforcement_under_riskd_app.py` (EDIT — extend with 2 admin endpoint scenarios; OR a separate file `test_rls_admin_extension.py` if the existing file is preserved as a snapshot)

**Specifics**:

### `tests/integration/test_admin_role_matrix.py` — 10 tests

The 5-cell matrix from the watch-point, fanned across both admin endpoints:

1. Admin token + admin decisions endpoint → 200.
2. Admin token + admin customers endpoint → 200.
3. Tenant token + admin decisions endpoint → 403.
4. Tenant token + admin customers endpoint → 403.
5. No auth + admin decisions endpoint → 401.
6. No auth + admin customers endpoint → 401.
7. Admin token + booking endpoint → 200 (admin role doesn't restrict normal access).
8. Admin token + modification endpoint → 200.
9. Admin token + feedback endpoint → 200.
10. Invalid token + admin endpoint → 401.

### `tests/integration/test_admin_cross_tenant_isolation.py` — 5 tests

1. tenant_a admin attempts decisions lookup of tenant_b decision (same request_id; tenant_b's tenant_id) → 404.
2. tenant_a admin attempts customer baseline lookup of tenant_b customer (same external_id) → 404.
3. Two tenants each have a customer with external_id="acme"; tenant_a admin → sees only tenant_a's baseline.
4. Same as 3 for decisions: tenant_a and tenant_b each have request_id="REQ-1"; tenant_a admin sees only tenant_a's decision.
5. Concurrent admin lookups from tenant_a and tenant_b under separate AsyncClient instances → no cross-leak (each lookup sets its own `app.tenant_id` session var).

### 3C.3 canary extension

The existing `tests/integration/test_rls_enforcement_under_riskd_app.py` runs each tenant-scoped operation as the non-superuser `riskd_app_login` role (which respects RLS) — proving Phase 5 role transition will work. Extend this test (or add a sibling file) to cover both admin endpoints under the same role.

For 4 admin scenarios:
1. Admin decisions lookup under riskd_app_login + correct tenant → 200.
2. Admin decisions lookup under riskd_app_login + wrong tenant → 404 (RLS hides; the `WHERE tenant_id = $1` would match nothing).
3. Admin customer baseline lookup under riskd_app_login + correct tenant → 200.
4. Admin customer baseline lookup under riskd_app_login + wrong tenant → 404.

Pin the invariant: Phase 5 RLS activation does not break admin endpoints.

**Validation**:
- `pytest tests/integration/test_admin_role_matrix.py tests/integration/test_admin_cross_tenant_isolation.py -v --asyncio-mode=auto` → 15 tests pass.
- `pytest tests/integration/test_rls_enforcement_under_riskd_app.py -v --asyncio-mode=auto` → existing tests + 4 new admin scenarios pass.
- `pytest tests/ --asyncio-mode=auto -q` → full suite green.

**Risk**: **Medium**.

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: 15 + 4 = 19 tests.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: test-only with auth/authz coverage → test-reviewer + senior + code-flow + security-auditor.

---

## 4D.5 — Phase 4 audit doc: `docs/security-audit-rls-phase-4.md`

**Theme**: New audit doc as a delta over Phase 3's `docs/security-audit-rls-phase-3.md`. Documents the 2 new admin endpoints, their queries, the `require_admin_role` dependency, and the role-based authorization model. Cross-references Phase 3 for the still-applicable findings.

**Files**:
- `docs/security-audit-rls-phase-4.md` (NEW)

**Specifics**:

Audit doc structure:

```markdown
# Phase 4 Multi-tenant Scoping Audit — RLS + Authorization Layer

> Date: 2026-06-01
> Phase: 4D wrap
> Predecessor: `docs/security-audit-rls-phase-3.md` (preserved as snapshot)
> Scope: Phase 4 additions only — admin endpoints + `require_admin_role` dependency

## Summary

Phase 4 adds 2 read-only admin endpoints under `/api/v1/admin/`:

| Endpoint | Auth required | Tenant-scope | RLS-eligible |
|---|---|---|---|
| `GET /admin/decisions/{request_id}` | admin role | YES (auth.tenant_id) | YES (decisions + shipments) |
| `GET /admin/customers/{external_id}/baseline` | admin role | YES (auth.tenant_id) | YES (customers + customer_baselines) |

Both compose with `require_admin_role` (4D.1) which composes with
`require_api_token`. Cross-tenant lookups return 404 (hides existence
per security-by-default convention).

Phase 3 audit findings remain in effect — Phase 4 does NOT remove RLS
policies, does NOT alter tenant-scoping discipline.

## Authorization model

| Role | Source | Phase introduced |
|---|---|---|
| `tenant` | `api_tokens.role` (default) | Phase 1 |
| `admin` | `api_tokens.role` | Phase 4D (first enforcement) |

`app_users.role` exists in Phase 1 schema but is NOT wired to the auth
dependency in Phase 4. Phase 5+ may add app_users wiring if a
multi-user admin model is needed (separate token vs user identity).

The auth dependency `require_admin_role` (`app/auth.py`):
1. Calls `require_api_token` → validates Bearer token, returns AuthContext.
2. Checks `auth.role == "admin"`. Returns 403 otherwise.
3. Returns the AuthContext unchanged (tenant_id preserved).

The `AUTH_ENABLED=false` local-dev carve-out yields `role="tenant"`,
which FAILS the admin check. Admin endpoints under local dev require
`AUTH_ENABLED=true`.

## Query inventory delta (Phase 3 → Phase 4)

Phase 3 inventory: 36 queries across 4 endpoints (booking, modification,
feedback, health).

Phase 4 additions:

| Query # | File:Line | Query | Tenant-scope mechanism | Notes |
|---|---|---|---|---|
| 37 | `app/api/admin.py:get_admin_decision` | SELECT FROM tenants WHERE id = $1 (via load_tenant_config) | Explicit WHERE | tenants table not RLS-enabled (intentional) |
| 38 | `app/api/admin.py:get_admin_decision` | SELECT FROM decisions JOIN shipments WHERE d.tenant_id = $1 AND d.request_id = $2 | Explicit WHERE on outer + JOIN ON s.tenant_id = d.tenant_id | Dual filter; defense-in-depth above RLS |
| 39 | `app/api/admin.py:get_admin_customer_baseline` | SELECT FROM tenants WHERE id = $1 (via load_tenant_config) | Explicit WHERE | Same as #37 |
| 40 | `app/api/admin.py:get_admin_customer_baseline` | SELECT FROM customers WHERE tenant_id = $1 AND external_id = $2 | Explicit WHERE | RLS-eligible |
| 41 | `app/api/admin.py:get_admin_customer_baseline` | SELECT FROM customer_baselines WHERE tenant_id = $1 AND customer_id = $2 | Explicit WHERE | RLS-eligible |

Total: 41 queries across 6 endpoints.

## Verification

Phase 3C.3 canary (`tests/integration/test_rls_enforcement_under_riskd_app.py`)
is the structural readiness gate for Phase 5's `riskd_app_login` role
transition. Phase 4D.4 extends this canary with 4 admin endpoint
scenarios — admin endpoints work under the non-superuser RLS role and
continue to scope by tenant_id.

Cross-tenant integration tests in `tests/integration/test_admin_cross_tenant_isolation.py`
prove that admin endpoints do not leak data between tenants.

## Phase 5 carry-forward

- `ux_decisions_tenant_request` UNIQUE widening (still BUGS.md entry from Phase 3).
- `riskd_app_login` role transition becomes the production RLS activation.
- `app_users` table wiring (if Phase 5+ adds multi-user admin).
- Admin write endpoints (decision overrides, etc.) — v2+ per decisions.md.

## Conclusion

Phase 4 admin endpoints are tenant-scoped at the application layer
(explicit WHERE/JOIN) AND eligible for Phase 5 RLS enforcement
(no queries depend on RLS bypass). The `require_admin_role` dependency
adds authorization on top of authentication; the 5-cell test matrix
(4D.4) confirms enforcement.

Zero queries with potentially missing scope identified in Phase 4.
```

**Validation**:
- Doc-reviewer reads and confirms accuracy of query line refs.
- `grep -c "tenant_id" docs/security-audit-rls-phase-4.md` non-zero.

**Risk**: **Low** (doc-only).

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only → doc-reviewer only.

---

## 4D.6 — Phase 4 wrap: per-batch report + aggregate REPORT_PHASE_4 + `.ai/decisions.md` close-out

**Theme**: End-of-phase reports. Per-batch `REPORT_PHASE_4D.md` plus aggregate `REPORT_PHASE_4.md`. Updates to `.ai/decisions.md` for any Phase 4 decisions not already covered by 4A.7 / 4B.7 / 4C.5.

**Files**:
- `REPORT_PHASE_4D.md` (NEW)
- `REPORT_PHASE_4.md` (NEW)
- `.ai/decisions.md` (EDIT — admin endpoint scope subsection if not already covered)

**Specifics**:

### `REPORT_PHASE_4D.md` shape (mirrors REPORT_PHASE_3D.md):

```markdown
# Phase 4 — Batch 4D Report

**Batch**: 4D — Admin endpoints + audit refresh + Phase 4 wrap
**Commits**: 6 (4D.1 through 4D.6)
**Tests added**: ~50 (4D.1: 9, 4D.2: 8, 4D.3: 14, 4D.4: 19, 4D.5: 0, 4D.6: 0)
**Status**: COMPLETE

## Per-commit disposition
... (each 4D.* commit: theme, deviations, reviewer-caught corrections, observations) ...

## Cumulative Phase 4 metrics (after 4D)
- Endpoints: 6 (booking, modification, feedback, health, admin decisions, admin customers)
- Test count: ~865 (from ~815 post-4C)
- Migrations: 5 (no new migrations in 4D)
- Rule count: 79 (no change)
- ALLOWED_CONTEXT_FIELDS: 71 (no change in 4D)
- Audit docs: 2 (`security-audit-rls-phase-3.md` + `security-audit-rls-phase-4.md`)
- New modules under app/: tenant_config.py (4A), admin.py (4D)
- New scripts: tenant_onboard.py (4A)

## Reviewer-caught corrections
(file:line refs for each)

## Carry-forward to Phase 5
- ux_decisions_tenant_request UNIQUE widening (still pending from Phase 3)
- riskd_app_login role transition (now validated by extended 3C.3 canary covering admin endpoints)
- In-process tenant-config cache (60s TTL)
- Observability backend (CloudWatch EMF)
- uv.lock, non-root container user, last_used_at writer
```

### `REPORT_PHASE_4.md` (aggregate; mirrors REPORT_PHASE_3.md shape):

```markdown
# Phase 4 — Aggregate Report

**Phase**: 4 of 6 (Week 4)
**Batches**: 4A (TenantConfig foundation), 4B (currency normalization), 4C (cold-start enforcement), 4D (admin endpoints + audit + wrap)
**Commits**: ~25-28 implementation commits + 4 per-batch reports + 4 plan commits + 1 aggregate report
**Date range**: 2026-06-01 to ~2026-06-08
**Status**: COMPLETE

## Phase 4 invariants achieved

- Per-tenant `TenantConfig` model + per-request loader + onboarding script
- USD-implicit assumption resolved via per-currency `value_caps`
- Cold-start window enforcement via grace mechanism
- 2 read-only admin endpoints with role-based auth
- Phase 4 audit doc published; zero queries with potentially missing scope
- USD-default tenants see ZERO behavioral change from Phase 3 (case-1 + case-2 still BLOCK)

## Aggregate stats

| Metric | Pre-Phase-4 (end of Phase 3) | Post-Phase-4 |
|---|---|---|
| Rule count | 79 | 79 (7 rewritten, no count change) |
| Test count | 675 | ~865 (+190) |
| ALLOWED_CONTEXT_FIELDS | 66 | 71 (+5) |
| Migrations | 4 | 5 (+1: tenants.updated_at) |
| Endpoints | 4 | 6 (+admin/decisions, +admin/customers) |
| `.ai/decisions.md` new sections | — | 4 (TenantConfig design, currency resolution, cold-start mechanism, admin scope) |
| Audit docs | 1 | 2 (added `security-audit-rls-phase-4.md`) |
| New modules under app/ | — | 2 (`tenant_config.py`, `admin.py`) |
| New scripts | — | 1 (`tenant_onboard.py`) |

## Per-batch summary

... (4A / 4B / 4C / 4D one-paragraph each, mirroring REPORT_PHASE_3.md) ...

## Plan deviations
... (aggregated table from per-batch reports) ...

## Reviewer-caught corrections
... (24 corrections across 27 implementation commits — same shape as Phase 3) ...

## Tangential issues logged to BUGS.md
... (carry-forward from Phase 4 if any) ...

## Phase 5 readiness assessment
- TenantConfig load is the per-request hot-path target for caching
- Admin endpoints exist and exercise role-based auth via api_tokens.role
- Currency normalization is operational; non-USD tenants can be onboarded via the script
- Cold-start grace mechanism is in place for newly-onboarded tenants
- 3C.3 canary extended to admin endpoints; Phase 5 role transition can proceed
- BUGS.md carry-forward for Phase 5: `ux_decisions_tenant_request` UNIQUE widening (from Phase 3)
- Audit doc trail: Phase 3 (snapshot) + Phase 4 (delta); Phase 5 audit refresh extends Phase 4.

## Recommended Phase 5 pre-flight
... (drain BUGS.md, confirm REPORT_PHASE_4.md matches operator's understanding, approve Phase 5 scope) ...

## Tests status
| Component | Pre-Phase-4 | Post-Phase-4 | Delta |
|---|---|---|---|
| Unit | ~430 | ~600 | +170 |
| Integration | ~245 | ~265 | +20 |
| Total | 675 | ~865 | +190 |

All ~865 tests pass. ruff clean, mypy strict clean.
```

### `.ai/decisions.md` admin scope subsection (append if not covered elsewhere)

Append to `## Endpoints` section:

```markdown
### Admin endpoint scope (Phase 4D — 2026-06-01)

Both Phase 4 admin endpoints are READ-ONLY and TENANT-BOUNDED:

- `GET /api/v1/admin/decisions/{request_id}` — full decision detail + linked shipment data.
- `GET /api/v1/admin/customers/{external_id}/baseline` — customer record + truncated baseline.

Authorization: `require_admin_role` dependency checks `auth.role == "admin"`,
returns 403 otherwise. `auth.role` is sourced from `api_tokens.role`
(Phase 1 schema); `app_users.role` exists but is not wired to auth in
Phase 4 (Phase 5+ may add multi-user admin model).

Cross-tenant lookups return 404, not 403 — hides existence per
security-by-default convention.

Admin write endpoints (decision overrides, manual feedback, etc.) are
out of scope for v1 per `## Out of scope`. v2+ may introduce a separate
admin write surface with workflow approvals.

Stat-dict truncation: customer baseline endpoint truncates each
stat-dict to top-10 by `n` desc. Full dicts available via separate
endpoint if needed — deferred to Phase 5+ as the data-volume calibration
needs production observation first.
```

**Validation**:
- All 3 docs pass doc-reviewer.
- Aggregate report numbers match per-batch reports' contributions.

**Risk**: **Low** (doc-only).

**Reversibility**: Easy.

**Pre-commit verification**: All gates green.

**Observability**: N/A.

**Test changes**: None.

**Rollback plan**: `git revert`.

**Declared breaks**: None.

**Reviewer routing**: doc-only → doc-reviewer only.

---

## Batch 4D summary table

| Commit | Theme | Files | Tests added | Risk | Reviewer panel |
|---|---|---|---|---|---|
| 4D.1 | `require_admin_role` dependency | `app/auth.py` (EDIT), 2 new tests | 9 | High | Never-Skip + security-auditor |
| 4D.2 | Decisions admin endpoint | `app/api/admin.py` (NEW), `app/main.py` (EDIT), 1 new test | 8 | High | Never-Skip + db-reviewer + security-auditor + test-reviewer |
| 4D.3 | Customer baseline admin endpoint | `app/api/admin.py` (EDIT), 2 new tests | 14 | High | Never-Skip + db-reviewer + security-auditor + test-reviewer |
| 4D.4 | Role matrix + cross-tenant + 3C.3 canary extension | 2 new tests + 1 edit | 19 | Medium | test-reviewer + senior + code-flow + security-auditor |
| 4D.5 | Phase 4 audit doc | `docs/security-audit-rls-phase-4.md` (NEW) | 0 | Low | doc-reviewer only |
| 4D.6 | Phase 4 wrap reports | `REPORT_PHASE_4D.md`, `REPORT_PHASE_4.md`, `.ai/decisions.md` (EDIT) | 0 | Low | doc-reviewer only |
| **Total** | | | **~50 new tests** | | |

Expected test count at end of Batch 4D: **~815 (post-4C) + 50 = ~865 tests**.

Endpoints at end of Batch 4D: **6** (health, booking, modification, feedback, admin/decisions, admin/customers).

Audit docs at end of Batch 4D: **2** (Phase 3 snapshot + Phase 4 delta).

Migrations at end of Batch 4D: **5** (unchanged from 4A).

Rules at end of Batch 4D: **79** (unchanged).

ALLOWED_CONTEXT_FIELDS at end of Batch 4D: **71** (unchanged from 4B).
