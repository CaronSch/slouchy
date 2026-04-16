#!/usr/bin/env python3
"""Collect GitHub traffic and release-download metrics for this repository."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_BASE = "https://api.github.com"


def _api_get(path: str, token: str):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "slouchy-metrics-bot",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"GitHub API failed for {path}: {exc.code} {body}") from exc


def _release_downloads(releases: list[dict]) -> dict:
    assets = []
    total_downloads = 0
    dmg_downloads = 0

    for release in releases:
        tag = release.get("tag_name")
        for asset in release.get("assets", []):
            count = int(asset.get("download_count", 0))
            name = asset.get("name", "")
            total_downloads += count
            if name.lower().endswith(".dmg"):
                dmg_downloads += count
            assets.append(
                {
                    "tag_name": tag,
                    "name": name,
                    "download_count": count,
                    "size": int(asset.get("size", 0)),
                    "updated_at": asset.get("updated_at"),
                    "browser_download_url": asset.get("browser_download_url"),
                }
            )

    return {
        "total_asset_downloads": total_downloads,
        "total_dmg_downloads": dmg_downloads,
        "assets": sorted(assets, key=lambda a: a["download_count"], reverse=True),
    }


def main() -> int:
    token = os.environ.get("GH_METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    using_fallback_actions_token = bool(
        not os.environ.get("GH_METRICS_TOKEN") and os.environ.get("GITHUB_TOKEN")
    )
    repository = os.environ.get("GITHUB_REPOSITORY")

    if not token:
        print("GH_METRICS_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
        return 1
    if not repository or "/" not in repository:
        print("GITHUB_REPOSITORY must be in owner/repo format", file=sys.stderr)
        return 1

    owner, repo = repository.split("/", 1)
    now = datetime.now(timezone.utc)

    try:
        views = _api_get(f"/repos/{owner}/{repo}/traffic/views", token)
        clones = _api_get(f"/repos/{owner}/{repo}/traffic/clones", token)
        referrers = _api_get(f"/repos/{owner}/{repo}/traffic/popular/referrers", token)
        paths = _api_get(f"/repos/{owner}/{repo}/traffic/popular/paths", token)
    except RuntimeError as exc:
        message = str(exc)
        if using_fallback_actions_token and "Resource not accessible by integration" in message:
            print(
                (
                    "Traffic API is blocked for default Actions token. "
                    "Create repo secret GH_METRICS_TOKEN with a fine-grained PAT "
                    "(Repository permissions: Administration=Read, Contents=Read) "
                    "and rerun this workflow."
                ),
                file=sys.stderr,
            )
        raise
    releases = _api_get(f"/repos/{owner}/{repo}/releases?per_page=30", token)

    payload = {
        "generated_at_utc": now.isoformat(),
        "repository": repository,
        "traffic": {
            "views": views,
            "clones": clones,
            "popular_referrers": referrers,
            "popular_paths": paths,
        },
        "release_downloads": _release_downloads(releases),
    }

    base = pathlib.Path(os.environ.get("METRICS_OUTPUT_DIR", "metrics/github"))
    daily = base / "snapshots" / f"{now.date().isoformat()}.json"
    latest = base / "latest.json"
    base.mkdir(parents=True, exist_ok=True)
    daily.parent.mkdir(parents=True, exist_ok=True)

    daily.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Wrote metrics snapshots: {daily} and {latest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
