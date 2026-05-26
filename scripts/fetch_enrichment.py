"""Refresh the IP enrichment source files.

Out-of-process (ECS scheduled task in production, local cron in dev).
The app reads from `--data-dir` at lifespan startup; missing files are
tolerated but produce empty enrichment results.

Sources (see `.ai/decisions.md` § IP enrichment for full URLs +
license terms):
  - MaxMind GeoLite2 City + ASN (license-keyed, weekly)
  - FireHOL Level 1 + Level 2 (public, daily)
  - IP2Proxy LITE PX11 (token-gated, monthly)
  - Cloud provider CIDRs (AWS / GCP / Azure / Cloudflare, public)

Usage:
  python scripts/fetch_enrichment.py --data-dir ./data/enrichment
  python scripts/fetch_enrichment.py --data-dir ./data/enrichment --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

_FIREHOL_BASE = (
    "https://raw.githubusercontent.com/firehol/blocklist-ipsets/master"
)
_CLOUD_URLS = {
    "aws": "https://ip-ranges.amazonaws.com/ip-ranges.json",
    "gcp": "https://www.gstatic.com/ipranges/cloud.json",
    "cloudflare": "https://www.cloudflare.com/ips-v4",
}


def _download(url: str, dest: Path, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would download {url} → {dest}")
        return
    print(f"download {url} → {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)


def _fetch_firehol(data_dir: Path, *, dry_run: bool) -> None:
    for level in ("level1", "level2"):
        url = f"{_FIREHOL_BASE}/firehol_{level}.netset"
        _download(url, data_dir / f"firehol_{level}.netset", dry_run=dry_run)


def _fetch_cloud_cidrs(data_dir: Path, *, dry_run: bool) -> None:
    for provider, url in _CLOUD_URLS.items():
        ext = "json" if url.endswith(".json") else "cidr"
        # Cloudflare returns a plain newline-separated list (.cidr); AWS/GCP
        # return JSON. The Enricher reads .cidr; JSON sources need a parse
        # step which lands when Azure parsing is added.
        if ext == "json":
            _download(url, data_dir / f"{provider}.json", dry_run=dry_run)
        else:
            _download(url, data_dir / f"{provider}.cidr", dry_run=dry_run)


def _fetch_maxmind(data_dir: Path, license_key: str | None, *, dry_run: bool) -> None:
    if not license_key:
        print("[skip] MAXMIND_LICENSE_KEY not set — skipping MaxMind download")
        return
    for edition in ("City", "ASN"):
        url = (
            f"https://download.maxmind.com/app/geoip_download"
            f"?edition_id=GeoLite2-{edition}"
            f"&license_key={license_key}"
            f"&suffix=tar.gz"
        )
        dest = data_dir / f"GeoLite2-{edition}.tar.gz"
        _download(url, dest, dry_run=dry_run)
        if not dry_run:
            # MaxMind tarball contains GeoLite2-<edition>_YYYYMMDD/GeoLite2-<edition>.mmdb
            # — extract the .mmdb to data_dir.
            with tarfile.open(dest) as tar:
                for member in tar.getmembers():
                    if member.name.endswith(f"GeoLite2-{edition}.mmdb"):
                        member.name = f"GeoLite2-{edition}.mmdb"
                        tar.extract(member, data_dir)
                        break
            dest.unlink()


def _fetch_ip2proxy(data_dir: Path, token: str | None, *, dry_run: bool) -> None:
    if not token:
        print("[skip] IP2PROXY_DOWNLOAD_TOKEN not set — skipping IP2Proxy download")
        return
    url = (
        f"https://www.ip2location.com/download/"
        f"?token={token}&file=PX11LITEBIN"
    )
    _download(url, data_dir / "IP2PROXY-LITE-PX11.BIN", dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh IP enrichment sources.")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    _fetch_firehol(args.data_dir, dry_run=args.dry_run)
    _fetch_cloud_cidrs(args.data_dir, dry_run=args.dry_run)
    _fetch_maxmind(
        args.data_dir,
        os.environ.get("MAXMIND_LICENSE_KEY"),
        dry_run=args.dry_run,
    )
    _fetch_ip2proxy(
        args.data_dir,
        os.environ.get("IP2PROXY_DOWNLOAD_TOKEN"),
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
