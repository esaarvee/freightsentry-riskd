#!/usr/bin/env python3
"""Variant comparison runner.

Generates five rule-file variants (A/B/C/D/E), runs the replay
orchestrator against three corpora for each variant, and writes
aggregate result JSON files to a results directory.

Ephemeral calibration script; not part of the production service.
Variant rule files and result aggregates live in /tmp; only the final
docs/replay-validation.md section is committed (by hand).

Variants:

  A — Tightened maturity gate, weights unchanged
      unfamiliar_ip_country_for_origin: gate >= 30; weight 0.3
      unknown_destination_address: gate >= 30; weight 0.2

  B — Halved weights, gates at >= 10
      unfamiliar_ip_country_for_origin: weight 0.15
      unknown_destination_address: weight 0.10

  C — Combined: gates >= 30 AND weights halved

  D — Compound with secondary signal, gates at >= 10, weights unchanged
      unfamiliar_ip_country_for_origin: append
        AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list
        OR is_datacenter_ip)
      unknown_destination_address: append
        AND shipment_value > shipment_value_threshold_medium

  E — Asymmetric split:
      IPC takes D-style secondary signal compound (most FPR-reducing)
      DEST takes A-style gate tightening to >= 30 (gentler)
      Both weights unchanged. Hypothesis: IPC drives more of the
      FPR; harsh treatment on IPC + gentle treatment on DEST should
      preserve case-2 recall while reducing FPR more than A/B/C.

Orchestration per (variant, corpus):
  1. docker compose cp <variant.yaml> app:/app/app/rules.yaml
  2. docker compose restart app + healthcheck poll
  3. python3 scripts/replay_validation.py --rules <variant.yaml> ...
  4. Capture result JSON to <results-dir>/<variant>-<corpus>.json

After all 12 runs: docker compose cp <base app/rules.yaml> app:..., then
restart (returns container to baseline image-bundled state). Aggregate
report compiled offline.

The host's app/rules.yaml is NEVER mutated. The variant is pushed
INTO the container's filesystem; the host tree stays clean throughout
the orchestrator's lifetime, so it never produces dirty git state.

Safety: clean working tree is a precondition (checked before any
mutation); try/finally guarantees container baseline restoration on
exit. Operator-supplied working tree MUST be clean before invocation.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.rules import load_rules  # noqa: E402

_VARIANTS = ("A", "B", "C", "D", "E")
_CORPORA = ("approved", "case2", "case3")

_HEALTHCHECK_TIMEOUT_S = 60
_HEALTHCHECK_INTERVAL_S = 1.0

_RULE_FPR_IP_COUNTRY = "unfamiliar_ip_country_for_origin"
_RULE_FPR_DEST_ADDR = "unknown_destination_address"


def _tighten_gate(rule: dict[str, Any]) -> None:
    """Replace `customer_observations >= 10` with `>= 30` in the rule's
    condition. Fails LOUDLY if the substring is absent — without this
    assertion a future rule-condition reformat would silently produce
    a no-op variant and the orchestrator would happily measure it."""
    original = rule["condition"]
    updated = original.replace("customer_observations >= 10", "customer_observations >= 30")
    if updated == original:
        msg = (
            f"_tighten_gate: rule {rule['name']!r} condition does not contain "
            f"the expected substring 'customer_observations >= 10'. "
            f"Condition was: {original!r}"
        )
        raise ValueError(msg)
    rule["condition"] = updated


def _apply_variant(rules_doc: dict[str, Any], variant: str) -> dict[str, Any]:
    """Return a NEW rules document with the named variant's
    transformations applied. Does not mutate the input."""
    doc = copy.deepcopy(rules_doc)
    rules_by_name = {r["name"]: r for r in doc["rules"]}
    if _RULE_FPR_IP_COUNTRY not in rules_by_name:
        msg = f"base rules.yaml missing rule {_RULE_FPR_IP_COUNTRY!r}"
        raise ValueError(msg)
    if _RULE_FPR_DEST_ADDR not in rules_by_name:
        msg = f"base rules.yaml missing rule {_RULE_FPR_DEST_ADDR!r}"
        raise ValueError(msg)
    ip_rule = rules_by_name[_RULE_FPR_IP_COUNTRY]
    dest_rule = rules_by_name[_RULE_FPR_DEST_ADDR]

    if variant == "A":
        _tighten_gate(ip_rule)
        _tighten_gate(dest_rule)
    elif variant == "B":
        ip_rule["weight"] = 0.15
        dest_rule["weight"] = 0.10
    elif variant == "C":
        _tighten_gate(ip_rule)
        _tighten_gate(dest_rule)
        ip_rule["weight"] = 0.15
        dest_rule["weight"] = 0.10
    elif variant == "D":
        ip_rule["condition"] = (
            "NOT origin_ip_country_familiar AND customer_observations >= 10 "
            "AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list "
            "OR is_datacenter_ip)"
        )
        ip_rule["description"] = (
            "Origin paired with an unseen IP country for this established "
            "customer AND a corroborating IP-quality signal (VPN, proxy, "
            "threat-list, datacenter)."
        )
        dest_rule["condition"] = (
            "NOT destination_address_familiar AND customer_observations >= 10 "
            "AND shipment_value > shipment_value_threshold_medium"
        )
        dest_rule["description"] = (
            "Destination address not seen before for this established "
            "customer AND value above tenant medium tier."
        )
    elif variant == "E":
        # IPC: D-style secondary-signal compound, weight unchanged.
        ip_rule["condition"] = (
            "NOT origin_ip_country_familiar AND customer_observations >= 10 "
            "AND (is_vpn OR is_proxy OR ip2p_threat_any OR ip_in_threat_list "
            "OR is_datacenter_ip)"
        )
        ip_rule["description"] = (
            "Origin paired with an unseen IP country for this established "
            "customer AND a corroborating IP-quality signal (VPN, proxy, "
            "threat-list, datacenter). Variant E asymmetric split."
        )
        # DEST: A-style gate tightening, weight unchanged.
        _tighten_gate(dest_rule)
    else:
        msg = f"unknown variant {variant!r}; expected one of {list(_VARIANTS)}"
        raise ValueError(msg)
    return doc


def generate_variants(base_rules_path: Path, out_dir: Path) -> dict[str, Path]:
    """Write all variant YAML files (A/B/C/D/E) to out_dir. Each is
    validated by app.rules.load_rules before being written out.
    Returns a dict mapping variant letter to its YAML path."""
    with base_rules_path.open(encoding="utf-8") as f:
        base_doc = yaml.safe_load(f)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for variant in _VARIANTS:
        variant_doc = _apply_variant(base_doc, variant)
        out_path = out_dir / f"{variant.lower()}.yaml"
        with out_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(variant_doc, f, sort_keys=False)
        # Fail-fast if the generated variant doesn't parse cleanly via
        # the production loader. Catches whitelist violations and DSL
        # errors before docker restart wastes time.
        load_rules(out_path)
        paths[variant] = out_path
    return paths


def _git_working_tree_clean() -> bool:
    """Returns True iff `app/rules.yaml` has no uncommitted changes.
    The orchestrator reads this file as the variant-generation
    baseline, so a dirty rules.yaml would contaminate every variant.
    Other tracked changes elsewhere in the working tree are
    tolerated; untracked files are ignored."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "app/rules.yaml"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == ""


def _docker_compose_restart_app() -> None:
    subprocess.run(
        ["docker", "compose", "restart", "app"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )


def _push_rules_to_container(rules_path: Path) -> None:
    """Copy a rules YAML into the running app container, replacing
    /app/app/rules.yaml. The container must be running; the change
    takes effect on the next app restart (which reloads rules at
    FastAPI lifespan startup)."""
    subprocess.run(
        ["docker", "compose", "cp", str(rules_path), "app:/app/app/rules.yaml"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )


def _reset_tenant_state(tenant_id: int) -> None:
    """Wipe the replay tenant's accumulated per-booking state so each
    variant starts from cold-customer baselines and an empty
    idempotency cache. Without this, deterministic request_ids
    (replay-{corpus}-{idx}) return cached decisions from prior
    variant runs instead of re-evaluating under the variant's rules.

    Tables truncated (in FK-safe order — users + shipments +
    customer_baselines reference customers):
      feedback, decisions, customer_baselines, shipments, users,
      customers, tenant_route_baselines, enterprises.

    The tenant row itself + its api_tokens + app_users are RETAINED
    so the bearer token continues to authenticate. Token + tenant
    config are not per-booking state.
    """
    sql = (
        "DELETE FROM feedback WHERE tenant_id=$1; "
        "DELETE FROM decisions WHERE tenant_id=$1; "
        "DELETE FROM customer_baselines WHERE tenant_id=$1; "
        "DELETE FROM shipments WHERE tenant_id=$1; "
        "DELETE FROM users WHERE tenant_id=$1; "
        "DELETE FROM customers WHERE tenant_id=$1; "
        "DELETE FROM tenant_route_baselines WHERE tenant_id=$1; "
        "DELETE FROM enterprises WHERE tenant_id=$1;"
    )
    # Execute via psql in the postgres container with the admin DSN
    # (riskd:riskd). The runtime app role (riskd_app_login) has RLS
    # restrictions that would prevent cross-tenant DELETE.
    subprocess.run(
        [
            "docker",
            "exec",
            "freightsentry-riskd-postgres-1",
            "psql",
            "-U",
            "riskd",
            "-d",
            "riskd",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql.replace("$1", str(int(tenant_id))),
        ],
        check=True,
        capture_output=True,
    )


def _wait_for_healthy(base_url: str, timeout_s: float = _HEALTHCHECK_TIMEOUT_S) -> bool:
    """Poll GET /health until 200, or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError):
            pass
        time.sleep(_HEALTHCHECK_INTERVAL_S)
    return False


def _run_replay_for_corpus(
    *,
    corpus: str,
    corpus_dir: Path,
    rules_path: Path,
    tenant_token: str,
    base_url: str,
    out_path: Path,
    concurrency: int = 20,
) -> None:
    """Invoke scripts/replay_validation.py as a subprocess. Raises
    CalledProcessError on non-zero exit. Default concurrency=20
    matches the verified-good steady-state from load testing."""
    subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "replay_validation.py"),
            "--corpus",
            corpus,
            "--corpus-dir",
            str(corpus_dir),
            "--rules",
            str(rules_path),
            "--tenant-token",
            tenant_token,
            "--base-url",
            base_url,
            "--out",
            str(out_path),
            "--concurrency",
            str(concurrency),
        ],
        check=True,
        cwd=_REPO_ROOT,
    )


def orchestrate(
    *,
    corpus_dir: Path,
    base_rules_path: Path,
    variants_dir: Path,
    results_dir: Path,
    tenant_token: str,
    base_url: str,
    tenant_id: int,
    variants: Iterable[str] = _VARIANTS,
    corpora: Iterable[str] = _CORPORA,
) -> dict[tuple[str, str], Path]:
    """Run the variant comparison and return a map of (variant, corpus)
    -> result JSON path. The mechanism:

      1. Verify working tree clean (precondition).
      2. Generate variant YAMLs into variants_dir.
      3. For each variant:
           a. docker compose cp <variant>.yaml app:/app/app/rules.yaml
           b. docker compose restart app + healthcheck.
           c. Run replay for each corpus.
      4. Push the host's baseline rules.yaml into the container +
         restart on exit (try/finally) so the container returns to
         the baseline rule set.

    The host's app/rules.yaml is NEVER mutated by this orchestrator;
    the container's bundled file is replaced via `docker compose cp`.
    """
    if not _git_working_tree_clean():
        msg = "working tree has uncommitted changes. Run-variants requires a clean tree."
        raise RuntimeError(msg)
    paths = generate_variants(base_rules_path, variants_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[tuple[str, str], Path] = {}
    try:
        for variant in variants:
            print(f"=== variant {variant} ===", file=sys.stderr)
            _reset_tenant_state(tenant_id)
            _push_rules_to_container(paths[variant])
            _docker_compose_restart_app()
            if not _wait_for_healthy(base_url):
                msg = f"app failed healthcheck after restart on variant {variant}"
                raise RuntimeError(msg)
            for corpus in corpora:
                out_path = results_dir / f"{variant.lower()}-{corpus}.json"
                print(
                    f"  replay variant={variant} corpus={corpus} -> {out_path}",
                    file=sys.stderr,
                )
                _run_replay_for_corpus(
                    corpus=corpus,
                    corpus_dir=corpus_dir,
                    rules_path=paths[variant],
                    tenant_token=tenant_token,
                    base_url=base_url,
                    out_path=out_path,
                )
                outputs[(variant, corpus)] = out_path
    finally:
        # Always restore the container's bundled rules.yaml from the
        # host baseline + restart, so the running container returns
        # to baseline state. Each step is suppressed INDEPENDENTLY so
        # a failed push does not skip the restart; both failures are
        # logged to stderr so the operator gets a clear signal that
        # the container may be on a variant rule set.
        try:
            _push_rules_to_container(base_rules_path)
        except subprocess.CalledProcessError as exc:
            print(
                f"WARNING: failed to restore container's app/rules.yaml from "
                f"{base_rules_path}: {exc}. Container may still hold the last "
                f"variant; run `docker compose restart app` to pick up the "
                f"image-baked baseline.",
                file=sys.stderr,
            )
        try:
            _docker_compose_restart_app()
        except subprocess.CalledProcessError as exc:
            print(
                f"WARNING: failed to restart app on cleanup: {exc}. Check docker compose state.",
                file=sys.stderr,
            )
    return outputs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-dir", type=Path, default=Path("/tmp/riskd-replay/"))
    ap.add_argument("--base-rules", type=Path, default=_REPO_ROOT / "app" / "rules.yaml")
    ap.add_argument("--variants-dir", type=Path, default=Path("/tmp/rules-variants/"))
    ap.add_argument("--results-dir", type=Path, default=Path("/tmp/phase-7b-results/"))
    ap.add_argument("--tenant-token", required=True)
    ap.add_argument("--tenant-id", type=int, required=True)
    ap.add_argument("--base-url", default="http://localhost:8000")
    args = ap.parse_args(argv)

    try:
        outputs = orchestrate(
            corpus_dir=args.corpus_dir,
            base_rules_path=args.base_rules,
            variants_dir=args.variants_dir,
            results_dir=args.results_dir,
            tenant_token=args.tenant_token,
            tenant_id=args.tenant_id,
            base_url=args.base_url.rstrip("/"),
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"orchestrate error: {exc}", file=sys.stderr)
        return 2

    print(f"completed {len(outputs)} runs", file=sys.stderr)
    for (variant, corpus), out_path in outputs.items():
        print(f"  {variant} / {corpus} -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# Module-level export for callers that want to build an aggregate
# table from the result files without re-running the orchestrator.
def load_result_files(results_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for variant in _VARIANTS:
        for corpus in _CORPORA:
            path = results_dir / f"{variant.lower()}-{corpus}.json"
            if path.exists():
                with path.open(encoding="utf-8") as f:
                    out[(variant, corpus)] = json.load(f)
    return out
