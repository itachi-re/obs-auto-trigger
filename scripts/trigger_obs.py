#!/usr/bin/env python3
"""
trigger_obs.py -- Diff nvchecker version files and fire OBS service-run triggers.

Usage (called by GitHub Actions):
  python scripts/trigger_obs.py \\
      --old-versions versions.json \\
      --new-versions versions_new.json \\
      --output-summary trigger_summary.json

  # Force a single package regardless of version changes:
  python scripts/trigger_obs.py --force-package plasma-desktop

  # Trigger + wait for build results (timeout 10 min, poll every 20 s):
  python scripts/trigger_obs.py --poll-builds --poll-timeout 600 --poll-interval 20

  # Skip the OBS existence preflight (slightly faster, less safe):
  python scripts/trigger_obs.py --skip-preflight

Environment variables required:
  OBS_TOKEN   -- OBS API token with operation=runservice scope
  OBS_PROJECT -- OBS project name (default: home:itachi_re)
  DRY_RUN     -- 'true' to skip actual HTTP calls (default: false)

Preflight check (on by default):
  Before each trigger the script calls GET /source/{project}/{package} to
  confirm the package exists in OBS.  Packages that return 404 are silently
  skipped and recorded in the summary as "skipped_list" -- they never count
  as failures, so a stale nvchecker entry can never break your CI pipeline.

Build polling (opt-in, --poll-builds):
  After ALL packages have been triggered the script enters a polling loop,
  calling GET /build/{project}/_result?package={pkg} for every in-flight
  package.  Each repo/arch is checked independently; a package is "done"
  only when every repo/arch reaches a terminal state:

    succeeded               -> build passed
    failed / unresolvable   -> build failed  (exits non-zero)
    broken                  -> spec/source error (exits non-zero)
    disabled / excluded     -> skipped by OBS config (not counted)

  Non-terminal states (scheduled, building, finished, signing, dispatching,
  blocked) keep the package in the pending set.  If --poll-timeout expires
  before all packages settle, those packages are reported as "timed_out".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OBS_API_BASE     = "https://build.opensuse.org"
TRIGGER_ENDPOINT = f"{OBS_API_BASE}/trigger/runservice"
REQUEST_TIMEOUT  = 30   # seconds per HTTP call
RETRY_COUNT      = 3    # retries on transient network errors
RETRY_BACKOFF    = 5    # seconds between retries (multiplied by attempt number)
THROTTLE_DELAY   = 0.5  # seconds between successive OBS API calls

# Build-state classification used by the polling logic.
# "finished" is a short OBS-internal transitional state before the result is
# committed -- treat it as non-terminal so we never stop polling too early.
_BUILD_OK       = frozenset({"succeeded"})
_BUILD_FAIL     = frozenset({"failed", "unresolvable", "broken"})
_BUILD_SKIP     = frozenset({"disabled", "excluded"})  # OBS config, not our problem
_BUILD_TERMINAL = _BUILD_OK | _BUILD_FAIL | _BUILD_SKIP

# ---------------------------------------------------------------------------
# Low-level OBS API helpers
# ---------------------------------------------------------------------------

def _obs_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Token {token}", "Accept": "application/xml"}


def _obs_get(
    url: str, token: str, params: dict | None = None
) -> requests.Response:
    """Single GET with a short timeout; caller handles retries."""
    return requests.get(
        url,
        headers=_obs_headers(token),
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Feature 1: Package existence preflight
# ---------------------------------------------------------------------------

def check_package_exists(
    project: str, package: str, token: str, dry_run: bool
) -> bool:
    """
    Return True if the package exists in the OBS project.

    Uses GET /source/{project}/{package} -- a lightweight metadata call that
    does not start any build.  On dry-run always returns True so the rest of
    the pipeline still exercises the trigger logic.
    """
    if dry_run:
        return True

    url = f"{OBS_API_BASE}/source/{project}/{package}"
    try:
        resp = _obs_get(url, token)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        # For anything else (auth error, server error) we assume the package
        # *might* exist and let the trigger call surface the real error.
        return True
    except requests.exceptions.RequestException:
        # Network issue -- optimistically proceed; the trigger will fail loudly.
        return True


# ---------------------------------------------------------------------------
# Feature 2: OBS service trigger
# ---------------------------------------------------------------------------

def trigger_obs_service(
    project: str, package: str, token: str, dry_run: bool
) -> tuple[bool, str]:
    """
    POST to /trigger/runservice for one package.
    Returns (success, message).
    """
    if dry_run:
        return True, "DRY RUN -- skipped"

    params   = {"project": project, "package": package}
    last_err = ""

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(
                TRIGGER_ENDPOINT,
                headers=_obs_headers(token),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                return True, "OK"
            if resp.status_code == 401:
                return False, "Unauthorized -- check OBS_TOKEN secret"
            if resp.status_code == 404:
                return False, f"Package '{package}' not found in project '{project}'"
            if resp.status_code == 400:
                return False, f"Bad request: {resp.text.strip()[:200]}"
            last_err = f"HTTP {resp.status_code}: {resp.text.strip()[:200]}"
        except requests.exceptions.Timeout:
            last_err = "Request timed out"
        except requests.exceptions.RequestException as exc:
            last_err = str(exc)

        if attempt < RETRY_COUNT:
            time.sleep(RETRY_BACKOFF * attempt)

    return False, f"Failed after {RETRY_COUNT} attempts: {last_err}"


# ---------------------------------------------------------------------------
# Feature 3: Build result polling
# ---------------------------------------------------------------------------

def _parse_build_states(xml_text: str) -> dict[str, str]:
    """
    Parse a /build/{project}/_result XML response into a flat dict:
        {"openSUSE_Tumbleweed/x86_64": "building", ...}

    OBS returns one <result> element per repository/arch combination.
    The <status code="..."> child for the requested package carries the state.
    """
    states: dict[str, str] = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return states

    for result in root.findall("result"):
        repo = result.get("repository", "")
        arch = result.get("arch", "")
        key  = f"{repo}/{arch}"
        # The <status> element may be absent when OBS hasn't scheduled the
        # package yet; treat that as "scheduled".
        status_el = result.find("status")
        code = (
            status_el.get("code", "scheduled")
            if status_el is not None
            else "scheduled"
        )
        states[key] = code

    return states


def _fetch_build_states(
    project: str, package: str, token: str
) -> tuple[bool, dict[str, str]]:
    """
    Fetch current build states for one package across all repos/arches.
    Returns (ok, states_dict).  ok=False means the HTTP call itself failed.
    """
    url = f"{OBS_API_BASE}/build/{project}/_result"
    try:
        resp = _obs_get(url, token, params={"package": package, "view": "status"})
        if resp.status_code != 200:
            return False, {}
        return True, _parse_build_states(resp.text)
    except requests.exceptions.RequestException:
        return False, {}


def _all_terminal(states: dict[str, str]) -> bool:
    """True when every repo/arch has reached a terminal state."""
    return bool(states) and all(s in _BUILD_TERMINAL for s in states.values())


def _overall_verdict(states: dict[str, str]) -> str:
    """
    Collapse per-repo/arch states into one word:
      "succeeded" -- all non-skip entries succeeded, or every repo is disabled/excluded
      "failed"    -- at least one entry is in _BUILD_FAIL
      "pending"   -- not all entries are terminal yet
    """
    codes = set(states.values())
    if codes & _BUILD_FAIL:
        return "failed"
    active = codes - _BUILD_SKIP
    # If every repo/arch is disabled or excluded there is nothing to build --
    # not a failure; treat the same as a clean success.
    if not active:
        return "succeeded"
    if all(c in _BUILD_OK for c in active):
        return "succeeded"
    return "pending"


def poll_build_results(
    project: str,
    packages: list[str],
    token: str,
    timeout: int,
    interval: int,
    dry_run: bool,
) -> dict[str, dict[str, Any]]:
    """
    Poll OBS build results for *packages* until all settle or *timeout* expires.

    Returns a dict keyed by package name:
      {
        "verdict":  "succeeded" | "failed" | "timed_out" | "dry_run",
        "states":   {"repo/arch": "state_code", ...},
        "elapsed":  seconds_waited,
      }

    Strategy: trigger all packages first (caller's job), then poll them as a
    batch so OBS can process them in parallel while we wait.
    """
    if dry_run:
        return {
            pkg: {"verdict": "dry_run", "states": {}, "elapsed": 0}
            for pkg in packages
        }

    pending = set(packages)
    results: dict[str, dict[str, Any]] = {}
    started = time.monotonic()

    print(
        f"\n  Polling {len(pending)} package(s) "
        f"(timeout={timeout}s, interval={interval}s) ..."
    )

    while pending:
        elapsed = time.monotonic() - started

        if elapsed >= timeout:
            for pkg in sorted(pending):
                results[pkg] = {
                    "verdict": "timed_out",
                    "states":  {},
                    "elapsed": int(elapsed),
                }
                print(f"    {pkg}: TIMEOUT after {int(elapsed)}s")
            break

        time.sleep(interval)
        elapsed  = time.monotonic() - started
        settled: set[str] = set()

        for pkg in sorted(pending):
            ok, states = _fetch_build_states(project, pkg, token)

            if not ok:
                # Transient fetch error -- skip this round, retry next tick.
                print(f"    {pkg}: fetch error, will retry ...")
                time.sleep(THROTTLE_DELAY)
                continue

            if _all_terminal(states):
                verdict = _overall_verdict(states)
                results[pkg] = {
                    "verdict": verdict,
                    "states":  states,
                    "elapsed": int(elapsed),
                }
                detail = ", ".join(f"{k}={v}" for k, v in sorted(states.items()))
                label  = "OK  " if verdict == "succeeded" else "FAIL"
                print(f"    {pkg}: {label} [{detail}] ({int(elapsed)}s)")
                settled.add(pkg)
            else:
                # Show only the still-building repo/arches so output stays concise.
                in_progress = {
                    k: v for k, v in states.items() if v not in _BUILD_TERMINAL
                }
                detail = ", ".join(f"{k}={v}" for k, v in sorted(in_progress.items()))
                print(f"    {pkg}: ... [{detail}] ({int(elapsed)}s)")

            time.sleep(THROTTLE_DELAY)

        pending -= settled

    return results


# ---------------------------------------------------------------------------
# Version loading (format-agnostic)
# ---------------------------------------------------------------------------

def _extract_version(value: Any) -> str:
    """
    Normalise a single package entry to a plain version string.

    nvchecker v2 stores each entry as a dict:
        {"version": "1.9.2602", "url": "...", ...}
    Legacy / hand-written files store a bare string:
        "1.9.2602"
    Anything else is str()-coerced so comparisons never raise.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        ver = value.get("version")
        if ver is not None:
            return str(ver)
        # dict present but no "version" key -- stringify whole entry so we can
        # at least detect changes without crashing.
        return json.dumps(value, sort_keys=True)
    return str(value)


def load_versions(path: str) -> dict[str, str]:
    """
    Load a versions file and return a normalised {package: version_string} dict.

    Understands two formats:

    1. Flat / legacy:
       {"DirectXShaderCompiler": "1.9.2601", "android-apktool": "3.0.1"}

    2. nvchecker v2 envelope (produced by ``nvchecker --format json``):
       {
           "version": 2,
           "data": {
               "DirectXShaderCompiler": {"version": "1.9.2602", ...},
               "android-apktool":       {"version": "3.0.2"}
           }
       }

    Both are normalised to flat {pkg: "ver"} so the rest of the script is
    completely format-agnostic.
    """
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        return {}

    raw: Any = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError(
            f"{path}: expected a JSON object at the top level, "
            f"got {type(raw).__name__}"
        )

    # Detect nvchecker v2 envelope:
    #   top-level "version" is an int  AND  "data" is a dict
    if isinstance(raw.get("version"), int) and isinstance(raw.get("data"), dict):
        payload: dict[str, Any] = raw["data"]
    else:
        payload = raw

    return {pkg: _extract_version(val) for pkg, val in payload.items()}


def find_updated_packages(
    old: dict[str, str], new: dict[str, str]
) -> list[tuple[str, str, str]]:
    """
    Return (package, old_ver, new_ver) for every package whose version changed
    or that is newly seen.
    """
    updates = []
    for pkg, new_ver in new.items():
        old_ver = old.get(pkg)
        if new_ver != old_ver:
            updates.append((pkg, old_ver or "unknown", new_ver))
    return updates


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _write_summary(path: str, **kwargs: Any) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(kwargs, fh, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger OBS service runs for packages updated by nvchecker"
    )
    parser.add_argument(
        "--old-versions", default="versions.json",
        help="Path to stored versions file",
    )
    parser.add_argument(
        "--new-versions", default="versions_new.json",
        help="Path to nvchecker output (new versions)",
    )
    parser.add_argument(
        "--output-summary", default="trigger_summary.json",
        help="Where to write the JSON summary",
    )
    parser.add_argument(
        "--force-package", default="",
        help="Force-trigger this single package name regardless of version changes",
    )
    # Preflight
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="Skip the OBS package-existence check before triggering",
    )
    # Build polling
    parser.add_argument(
        "--poll-builds", action="store_true",
        help="After triggering, poll OBS until all builds settle",
    )
    parser.add_argument(
        "--poll-timeout", type=int, default=300,
        help="Maximum seconds to wait for builds (default: 300)",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=15,
        help="Seconds between poll cycles (default: 15)",
    )
    args = parser.parse_args()

    # -- Environment ----------------------------------------------------------
    obs_token   = os.environ.get("OBS_TOKEN", "").strip()
    obs_project = os.environ.get("OBS_PROJECT", "home:itachi_re").strip()
    dry_run     = os.environ.get("DRY_RUN", "false").lower() == "true"

    if not obs_token:
        print("ERROR: OBS_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Project   : {obs_project}")
    print(f"Dry run   : {dry_run}")
    print(f"Preflight : {'disabled' if args.skip_preflight else 'enabled'}")
    print(f"Polling   : {'enabled' if args.poll_builds else 'disabled'}")
    print()

    # -- Force-trigger mode ---------------------------------------------------
    if args.force_package:
        pkg = args.force_package.strip()
        print(f"Force-triggering: {pkg}")

        if not args.skip_preflight:
            if not check_package_exists(obs_project, pkg, obs_token, dry_run):
                print(
                    f"  SKIP: package '{pkg}' not found "
                    f"in OBS project '{obs_project}'"
                )
                sys.exit(1)

        ok, msg = trigger_obs_service(obs_project, pkg, obs_token, dry_run)
        print(f"  {'OK' if ok else 'FAILED'}: {msg}")

        if ok and args.poll_builds:
            poll_build_results(
                obs_project, [pkg], obs_token,
                args.poll_timeout, args.poll_interval, dry_run,
            )

        if not ok:
            sys.exit(1)
        return

    # -- Normal mode: diff old vs new versions --------------------------------
    old_versions = load_versions(args.old_versions)
    new_versions = load_versions(args.new_versions)

    if not new_versions:
        print("WARNING: No new versions data found -- nvchecker may have produced no output.")
        print("         Check nvchecker_errors.log for details.")
        _write_summary(
            args.output_summary,
            checked=0, updated=0, triggered=0, failed=0, skipped=0,
        )
        return

    updates = find_updated_packages(old_versions, new_versions)

    print(f"Packages tracked  : {len(new_versions)}")
    print(f"Updates detected  : {len(updates)}")
    print()

    if not updates:
        print("All packages are already up to date.")
        _write_summary(
            args.output_summary,
            checked=len(new_versions), updated=0, triggered=0, failed=0, skipped=0,
        )
        return

    # -- Preflight + trigger pass ---------------------------------------------
    triggered_list: list[dict[str, Any]] = []
    failed_list:    list[dict[str, Any]] = []
    skipped_list:   list[dict[str, Any]] = []

    for pkg, old_ver, new_ver in updates:
        print(f"  {pkg}: {old_ver}  ->  {new_ver}")

        # Preflight: confirm the package actually exists in OBS before we
        # bother the trigger endpoint.
        if not args.skip_preflight:
            exists = check_package_exists(obs_project, pkg, obs_token, dry_run)
            time.sleep(THROTTLE_DELAY)
            if not exists:
                print(f"    SKIP: not found in OBS project '{obs_project}'")
                skipped_list.append({"package": pkg, "reason": "not found in OBS"})
                continue

        ok, msg = trigger_obs_service(obs_project, pkg, obs_token, dry_run)
        entry = {"package": pkg, "old": old_ver, "new": new_ver, "status": msg}
        if ok:
            print(f"    Triggered")
            triggered_list.append(entry)
        else:
            print(f"    FAILED: {msg}")
            failed_list.append({"package": pkg, "error": msg})

        time.sleep(THROTTLE_DELAY)

    print()
    print(f"Triggered : {len(triggered_list)}")
    print(f"Skipped   : {len(skipped_list)}")
    print(f"Failed    : {len(failed_list)}")

    # -- Optional build polling -----------------------------------------------
    poll_results: dict[str, Any] = {}
    build_failures: list[str]    = []

    if args.poll_builds and triggered_list:
        packages_to_poll = [e["package"] for e in triggered_list]
        raw_poll = poll_build_results(
            obs_project, packages_to_poll, obs_token,
            args.poll_timeout, args.poll_interval, dry_run,
        )
        poll_results = raw_poll

        for pkg, info in raw_poll.items():
            if info["verdict"] in ("failed", "timed_out"):
                build_failures.append(pkg)

        succeeded_b = sum(1 for i in raw_poll.values() if i["verdict"] == "succeeded")
        failed_b    = sum(1 for i in raw_poll.values() if i["verdict"] == "failed")
        timed_out_b = sum(1 for i in raw_poll.values() if i["verdict"] == "timed_out")

        print()
        print(f"Build succeeded : {succeeded_b}")
        print(f"Build failed    : {failed_b}")
        print(f"Build timed out : {timed_out_b}")

    # -- Write summary --------------------------------------------------------
    _write_summary(
        args.output_summary,
        checked=len(new_versions),
        updated=len(updates),
        triggered=len(triggered_list),
        skipped=len(skipped_list),
        failed=len(failed_list),
        updated_list=triggered_list,
        skipped_list=skipped_list,
        failed_list=failed_list,
        poll_results=poll_results,
    )

    if failed_list or build_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
