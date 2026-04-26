#!/usr/bin/env python3
"""
trigger_obs.py — Diff nvchecker version files and fire OBS service-run triggers.

Usage (called by GitHub Actions):
  python scripts/trigger_obs.py \\
      --old-versions versions.json \\
      --new-versions versions_new.json \\
      --output-summary trigger_summary.json

  # Force a single package regardless of version changes:
  python scripts/trigger_obs.py --force-package plasma-desktop

Environment variables required:
  OBS_TOKEN   — OBS API token with operation=runservice scope
  OBS_PROJECT — OBS project name (default: home:itachi_re)
  DRY_RUN     — 'true' to skip actual HTTP calls (default: false)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

# ─── Configuration ──────────────────────────────────────────────────────────

OBS_API_BASE    = "https://build.opensuse.org"
TRIGGER_ENDPOINT = f"{OBS_API_BASE}/trigger/runservice"
REQUEST_TIMEOUT  = 30          # seconds per HTTP call
RETRY_COUNT      = 3           # retries on network errors
RETRY_BACKOFF    = 5           # seconds between retries
THROTTLE_DELAY   = 1.5         # seconds between OBS API calls (be polite)


# ─── OBS helper ─────────────────────────────────────────────────────────────

def trigger_obs_service(project: str, package: str, token: str, dry_run: bool) -> tuple[bool, str]:
    """
    POST to /trigger/runservice for one package.
    Returns (success: bool, message: str).
    """
    if dry_run:
        return True, "DRY RUN — skipped"

    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/xml",
    }
    params = {"project": project, "package": package}

    last_err: str = ""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(
                TRIGGER_ENDPOINT,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return True, "OK"
            elif resp.status_code == 401:
                return False, "Unauthorized — check OBS_TOKEN secret"
            elif resp.status_code == 404:
                return False, f"Package '{package}' not found in project '{project}'"
            elif resp.status_code == 400:
                return False, f"Bad request: {resp.text.strip()[:200]}"
            else:
                last_err = f"HTTP {resp.status_code}: {resp.text.strip()[:200]}"
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_BACKOFF * attempt)
                    continue
        except requests.exceptions.Timeout:
            last_err = "Request timed out"
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_BACKOFF * attempt)
                continue
        except requests.exceptions.RequestException as exc:
            last_err = str(exc)
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_BACKOFF * attempt)
                continue

    return False, f"Failed after {RETRY_COUNT} attempts: {last_err}"


# ─── Version diff ────────────────────────────────────────────────────────────

def load_json(path: str) -> dict[str, str]:
    """Load a JSON file; return empty dict if missing or empty."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        return {}
    return json.loads(content)


def find_updated_packages(old: dict[str, str], new: dict[str, str]) -> list[tuple[str, str, str]]:
    """
    Return list of (package, old_ver, new_ver) for packages where the version
    has changed (or is newly seen).
    """
    updates = []
    for pkg, new_ver in new.items():
        old_ver = old.get(pkg)
        if new_ver != old_ver:
            updates.append((pkg, old_ver or "unknown", new_ver))
    return updates


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger OBS service runs for updated packages")
    parser.add_argument("--old-versions",    default="versions.json",     help="Path to stored versions file")
    parser.add_argument("--new-versions",    default="versions_new.json", help="Path to nvchecker output (new versions)")
    parser.add_argument("--output-summary",  default="trigger_summary.json", help="Where to write JSON summary")
    parser.add_argument("--force-package",   default="",                  help="Force-trigger this single package name")
    args = parser.parse_args()

    # ── Read env ─────────────────────────────────────────────────────────────
    obs_token   = os.environ.get("OBS_TOKEN", "").strip()
    obs_project = os.environ.get("OBS_PROJECT", "home:itachi_re").strip()
    dry_run     = os.environ.get("DRY_RUN", "false").lower() == "true"

    if not obs_token:
        print("❌ OBS_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Project  : {obs_project}")
    print(f"Dry run  : {dry_run}")
    print()

    # ── Force-trigger mode ───────────────────────────────────────────────────
    if args.force_package:
        pkg = args.force_package.strip()
        print(f"Force-triggering package: {pkg}")
        ok, msg = trigger_obs_service(obs_project, pkg, obs_token, dry_run)
        status = "✅ OK" if ok else f"❌ FAILED"
        print(f"  {status}: {msg}")
        if not ok:
            sys.exit(1)
        return

    # ── Normal mode: diff old vs new versions ────────────────────────────────
    old_versions = load_json(args.old_versions)
    new_versions = load_json(args.new_versions)

    if not new_versions:
        print("⚠️  No new versions data found — nvchecker may have produced no output.")
        print("   Check nvchecker_errors.log for details.")
        _write_summary(args.output_summary, checked=0, updated=0, triggered=0, failed=0)
        return

    updates = find_updated_packages(old_versions, new_versions)

    print(f"Packages tracked  : {len(new_versions)}")
    print(f"Updates detected  : {len(updates)}")
    print()

    if not updates:
        print("✅ All packages are already up to date.")
        _write_summary(
            args.output_summary,
            checked=len(new_versions),
            updated=0,
            triggered=0,
            failed=0,
        )
        return

    # ── Trigger OBS for each updated package ─────────────────────────────────
    triggered_list: list[dict[str, Any]] = []
    failed_list:    list[dict[str, Any]] = []

    for pkg, old_ver, new_ver in updates:
        print(f"  📦 {pkg}: {old_ver}  →  {new_ver}")
        ok, msg = trigger_obs_service(obs_project, pkg, obs_token, dry_run)
        entry = {"package": pkg, "old": old_ver, "new": new_ver, "status": msg}
        if ok:
            print(f"      ✅ Triggered")
            triggered_list.append(entry)
        else:
            print(f"      ❌ {msg}")
            failed_list.append({"package": pkg, "error": msg})
        time.sleep(THROTTLE_DELAY)  # be polite to the OBS API

    print()
    print(f"Triggered : {len(triggered_list)}")
    print(f"Failed    : {len(failed_list)}")

    _write_summary(
        args.output_summary,
        checked=len(new_versions),
        updated=len(updates),
        triggered=len(triggered_list),
        failed=len(failed_list),
        updated_list=triggered_list,
        failed_list=failed_list,
    )

    if failed_list:
        sys.exit(1)


def _write_summary(path: str, **kwargs: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(kwargs, fh, indent=2)


if __name__ == "__main__":
    main()
