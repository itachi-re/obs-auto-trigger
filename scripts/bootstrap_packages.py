#!/usr/bin/env python3
"""
bootstrap_packages.py — One-time helper to scaffold nvchecker.toml.

This script:
  1. Fetches your full package list from OBS via the API.
  2. For each package, reads its _service file from OBS and tries to detect
     the upstream source (GitHub, KDE invent, PyPI, etc.).
  3. Generates a starter nvchecker.toml you can then refine.

Usage:
  python scripts/bootstrap_packages.py \\
      --project home:itachi_re \\
      --obs-user itachi_re \\
      --obs-password YOUR_PASSWORD_OR_TOKEN \\
      --output nvchecker.toml

You only need to run this ONCE to generate the initial config.
After that, maintain nvchecker.toml by hand as you add/remove packages.

Requires: requests, lxml
  pip install requests lxml
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Optional

import requests

OBS_API = "https://api.opensuse.org"


# ─── OBS API helpers ─────────────────────────────────────────────────────────

def obs_get(path: str, session: requests.Session) -> Optional[ET.Element]:
    resp = session.get(f"{OBS_API}{path}", timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return ET.fromstring(resp.text)


def list_packages(project: str, session: requests.Session) -> list[str]:
    root = obs_get(f"/source/{project}", session)
    if root is None:
        print(f"❌ Project '{project}' not found.", file=sys.stderr)
        sys.exit(1)
    return [e.attrib["name"] for e in root.findall("entry")]


def get_service_file(project: str, package: str, session: requests.Session) -> Optional[str]:
    resp = session.get(
        f"{OBS_API}/source/{project}/{package}/_service",
        timeout=30,
    )
    if resp.status_code == 404:
        return None
    if resp.ok:
        return resp.text
    return None


# ─── Source detection heuristics ─────────────────────────────────────────────

def detect_upstream(package: str, service_xml: Optional[str]) -> dict:
    """
    Try to auto-detect the upstream source for nvchecker.
    Returns a dict of nvchecker TOML key-value pairs.
    """
    if not service_xml:
        return _unknown(package)

    try:
        root = ET.fromstring(service_xml)
    except ET.ParseError:
        return _unknown(package)

    # Collect all <param> values
    params: dict[str, str] = {}
    for svc in root.findall("service"):
        for p in svc.findall("param"):
            params[p.attrib.get("name", "")] = (p.text or "").strip()

    url = params.get("url", "")

    # ── GitHub ───────────────────────────────────────────────────────────────
    gh = re.search(r"github\.com[:/]([^/]+/[^/\s.]+?)(?:\.git)?$", url)
    if gh:
        repo = gh.group(1).rstrip("/")
        return {
            "source": "github",
            "github": repo,
            "use_max_tag": "true",
            "# _comment": f"OBS _service URL: {url}",
        }

    # ── KDE invent.kde.org (GitLab) ──────────────────────────────────────────
    kde = re.search(r"invent\.kde\.org[:/](.+?)(?:\.git)?$", url)
    if kde:
        repo = kde.group(1).rstrip("/")
        return {
            "source": "gitlab",
            "host": "https://invent.kde.org",
            "gitlab": repo,
            "use_max_tag": "true",
            "# _comment": f"OBS _service URL: {url}",
        }

    # ── freedesktop.org GitLab ───────────────────────────────────────────────
    fdo = re.search(r"gitlab\.freedesktop\.org[:/](.+?)(?:\.git)?$", url)
    if fdo:
        repo = fdo.group(1).rstrip("/")
        return {
            "source": "gitlab",
            "host": "https://gitlab.freedesktop.org",
            "gitlab": repo,
            "use_max_tag": "true",
            "# _comment": f"OBS _service URL: {url}",
        }

    # ── GNOME GitLab ─────────────────────────────────────────────────────────
    gnome = re.search(r"gitlab\.gnome\.org[:/](.+?)(?:\.git)?$", url)
    if gnome:
        repo = gnome.group(1).rstrip("/")
        return {
            "source": "gitlab",
            "host": "https://gitlab.gnome.org",
            "gitlab": repo,
            "use_max_tag": "true",
        }

    # ── PyPI ─────────────────────────────────────────────────────────────────
    if "pypi.org" in url or "pypi.python.org" in url:
        pypi_name = re.search(r"pypi\.org/project/([^/]+)", url)
        if pypi_name:
            return {"source": "pypi", "pypi": pypi_name.group(1)}

    # ── Generic git (fallback) ───────────────────────────────────────────────
    if url.endswith(".git") or "git://" in url or ("gitlab" in url and ".git" in url):
        return {
            "source": "git",
            "git": url,
            "use_max_tag": "true",
            "# _comment": "REVIEW: generic git source, verify tag pattern",
        }

    return _unknown(package, hint=url or "no URL found in _service")


def _unknown(package: str, hint: str = "") -> dict:
    return {
        "# TODO": f"Could not auto-detect upstream source. {hint}".strip(),
        "source": "cmd",
        "cmd": f'echo "REPLACE_WITH_REAL_VERSION_FOR_{package}"',
    }


# ─── TOML writer ─────────────────────────────────────────────────────────────

def render_toml(packages_config: dict[str, dict]) -> str:
    lines = [
        "# nvchecker.toml — upstream version checker config for home:itachi_re",
        "# Generated by scripts/bootstrap_packages.py — REVIEW and edit as needed.",
        "#",
        "# Docs: https://nvchecker.readthedocs.io/en/latest/usage.html",
        "",
        "[__config__]",
        'oldver = "versions.json"',
        'newver = "versions_new.json"',
        "",
        "# Optional: GitHub token for higher API rate limits",
        "# keyfile = \".nvchecker_keyfile.toml\"",
        "",
    ]

    for pkg, cfg in sorted(packages_config.items()):
        lines.append(f"[{pkg}]")
        for k, v in cfg.items():
            if k.startswith("#"):
                lines.append(f"{k} = {json_value(v)}")
            else:
                lines.append(f"{k} = {json_value(v)}")
        lines.append("")

    return "\n".join(lines)


def json_value(v) -> str:
    import json as _json
    if isinstance(v, bool):
        return "true" if v else "false"
    return _json.dumps(str(v))


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap nvchecker.toml from OBS packages")
    parser.add_argument("--project",      default="home:itachi_re", help="OBS project name")
    parser.add_argument("--obs-user",     required=True,            help="OBS username")
    parser.add_argument("--obs-password", required=True,            help="OBS password or app token")
    parser.add_argument("--output",       default="nvchecker.toml", help="Output file")
    parser.add_argument("--limit",        type=int, default=0,      help="Limit to N packages (for testing)")
    args = parser.parse_args()

    session = requests.Session()
    session.auth = (args.obs_user, args.obs_password)

    print(f"Fetching package list from {args.project} ...")
    packages = list_packages(args.project, session)
    if args.limit:
        packages = packages[:args.limit]
    print(f"  Found {len(packages)} packages")

    packages_config: dict[str, dict] = {}
    by_source: defaultdict[str, int] = defaultdict(int)

    for i, pkg in enumerate(packages, 1):
        print(f"  [{i:3}/{len(packages)}] {pkg}", end=" ", flush=True)
        svc = get_service_file(args.project, pkg, session)
        cfg = detect_upstream(pkg, svc)
        packages_config[pkg] = cfg
        src = cfg.get("source", "?")
        by_source[src] += 1
        print(f"→ {src}")

    toml_content = render_toml(packages_config)

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(toml_content)

    print()
    print(f"✅ Written to {args.output}")
    print()
    print("Source breakdown:")
    for src, count in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {src:20s}: {count}")

    todo_count = sum(1 for cfg in packages_config.values() if "# TODO" in cfg)
    if todo_count:
        print()
        print(f"⚠️  {todo_count} packages need manual review (marked # TODO in the file).")
        print("   Search for '# TODO' in nvchecker.toml and fill in the correct source.")


if __name__ == "__main__":
    import json  # noqa: F811 (used in json_value closure)
    main()
