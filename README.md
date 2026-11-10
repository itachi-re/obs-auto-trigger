<div align="center">

# 🎯 OBS Auto-Trigger

**Automated upstream version tracking & OBS rebuilds for 100+ packages**

[![Upstream check](https://img.shields.io/github/actions/workflow/status/itachi-re/obs-auto-trigger/check-updates.yml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=upstream%20check)](https://github.com/itachi-re/obs-auto-trigger/actions/workflows/check-updates.yml)
[![OBS project](https://img.shields.io/badge/OBS-home%3Aitachi__re-73BA25?style=flat-square&logo=opensuse&logoColor=white)](https://build.opensuse.org/project/show/home:itachi_re)
[![Packages tracked](https://img.shields.io/badge/packages-100%2B-brightgreen?style=flat-square&logo=linux&logoColor=white)](nvchecker.toml)
[![Checks every 6 hours](https://img.shields.io/badge/checks-every%206h-informational?style=flat-square&logo=githubactions&logoColor=white)](.github/workflows/check-updates.yml)

[![Last commit](https://img.shields.io/github/last-commit/itachi-re/obs-auto-trigger?style=flat-square&logo=git&logoColor=white&label=last%20commit)](https://github.com/itachi-re/obs-auto-trigger/commits/main)
[![Commit activity](https://img.shields.io/github/commit-activity/m/itachi-re/obs-auto-trigger?style=flat-square&logo=github&logoColor=white&label=activity)](https://github.com/itachi-re/obs-auto-trigger/graphs/commit-activity)
[![Open issues](https://img.shields.io/github/issues/itachi-re/obs-auto-trigger?style=flat-square&logo=github&logoColor=white&label=issues)](https://github.com/itachi-re/obs-auto-trigger/issues)
[![Stars](https://img.shields.io/github/stars/itachi-re/obs-auto-trigger?style=flat-square&logo=github&logoColor=white&label=stars)](https://github.com/itachi-re/obs-auto-trigger/stargazers)
[![License](https://img.shields.io/github/license/itachi-re/obs-auto-trigger?style=flat-square&label=license)](LICENSE)

[![nvchecker](https://img.shields.io/badge/powered%20by-nvchecker-blue?style=flat-square)](https://nvchecker.readthedocs.io/)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![GitHub Actions](https://img.shields.io/badge/CI-GitHub%20Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)](https://github.com/features/actions)

<sub>Automated upstream version tracking for the openSUSE Build Service project <a href="https://build.opensuse.org/project/show/home:itachi_re"><code>home:itachi_re</code></a>.</sub>

</div>

---

A GitHub Actions cron job checks every upstream with [nvchecker](https://nvchecker.readthedocs.io/) every **6 hours**. The moment a new version appears, it fires an OBS service run (`obs_scm` re-fetch) for that package, so OBS rebuilds without any manual work.

```mermaid
flowchart LR
    U["🌐 Upstreams<br/>GitHub · GitLab · git · web pages"] --> NV["🔍 nvchecker"]
    subgraph GH["⚙️ GitHub Actions — this repo"]
        NV --> TR["🚀 trigger_obs.py"]
        VJ[("📄 versions.json")] <--> TR
    end
    TR -->|"POST /trigger/runservice"| OBS["📦 OBS: home:itachi_re"]
    OBS --> B["🔨 obs_scm re-fetch<br/>new tarball · spec update · rebuild"]
```

**Highlights**

- ⏱️ Checks 100+ upstreams every 6 hours, with manual runs on demand
- 🛡️ Fails safe: a bad run can never silently lose or duplicate an update
- 🧪 Built-in dry-run and single-package force-trigger modes
- 🔎 Preflight check: packages missing from OBS are skipped, never counted as failures
- 🔨 Optional build polling to wait for OBS results (for local or custom runs)
- 🔐 Least-privilege by design: no `pull_request` trigger, secrets never stored in the repo

---

## 📑 Table of Contents

<details open>
<summary><b>Click to expand / collapse</b></summary>

<br>

1. [🔧 How it works](#how-it-works)
2. [🛡️ Safety guarantees](#safety-guarantees)
3. [📋 Requirements](#requirements)
4. [🚀 One-time setup](#one-time-setup)
5. [🗓️ Day-to-day usage](#day-to-day-usage)
6. [⌨️ `trigger_obs.py` reference](#trigger_obspy-reference)
7. [⚙️ Configuring nvchecker](#configuring-nvchecker)
8. [➕➖ Adding or removing a package](#adding-or-removing-a-package)
9. [🌐 Supported upstream sources](#supported-upstream-sources)
10. [🔒 Security notes](#security-notes)
11. [🩺 Troubleshooting](#troubleshooting)
12. [🧰 Maintenance](#maintenance)
13. [📁 File reference](#file-reference)

</details>

---

## How it works

### 🧩 Components

| Component | Role |
|---|---|
| 📄 `nvchecker.toml` | Declares **where** to find the upstream version of every package |
| 📄 `versions.json` | Last-known version of each package (committed to git) |
| ⚙️ `.github/workflows/check-updates.yml` | Runs nvchecker, analyses the result, fires OBS triggers, commits `versions.json` |
| 🚀 `scripts/trigger_obs.py` | Compares old vs. new versions and calls the OBS API for every changed package |
| 🛠️ `scripts/bootstrap_packages.py` | One-time helper that generates a starter `nvchecker.toml` from your OBS package list |

### ⏱️ Workflow triggers

| Trigger | When |
|---|---|
| 🕒 **Schedule** | Every 6 hours (`0 */6 * * *`) |
| 📤 **Push to `main`** | Only when `nvchecker.toml`, `scripts/**` or `.github/workflows/**` change |
| 🖱️ **Manual** | *Actions → Run workflow* (supports dry run and force-trigger) |

### 🔄 What one run does

```mermaid
flowchart TD
    S["🚦 Start: cron · push · manual"] --> K["🔑 Write nvchecker keyfile from secret"]
    K --> N["🔍 nvchecker checks every upstream"]
    N --> A{"⚠️ More than 25%<br/>of entries errored?"}
    A -->|yes| F1["❌ Fail the run<br/>no OBS triggers<br/>versions.json untouched"]
    A -->|no| D["📊 Diff versions.json<br/>vs versions_new.json"]
    D --> P{"🔎 Package exists<br/>in OBS?"}
    P -->|no| SK["⏭️ Skip and record<br/>in skipped_list"]
    P -->|yes| T["🚀 POST /trigger/runservice"]
    T --> C{"✅ Did the trigger<br/>step finish?"}
    SK --> C
    C -->|crashed, no summary| F2["🛑 Do NOT advance<br/>versions.json"]
    C -->|yes| M["🔀 Merge:<br/>advance successes<br/>hold back failures"]
    M --> G["💾 Commit and push versions.json"]

    classDef bad fill:#fdecea,stroke:#d73a49,color:#1f2328
    classDef good fill:#e6f4ea,stroke:#2da44e,color:#1f2328
    class F1,F2 bad
    class G good
```

### 📡 The OBS API call

For each package whose version changed, `trigger_obs.py` calls:

```http
POST https://api.opensuse.org/trigger/runservice
     ?project=home:itachi_re
     &package=<PACKAGE_NAME>
Authorization: Token <OBS_TOKEN>
```

That runs the package's `_service` file. With `obs_scm`, OBS fetches the latest commit or tag from upstream, builds a new tarball, updates the version in the `.spec`, and queues a rebuild. ✨

Before each trigger, the script also does a lightweight `GET /source/{project}/{package}` to confirm the package exists. A `404` means the package is skipped (a stale `nvchecker.toml` entry can never break the pipeline). Any other response is treated as "might exist" and the trigger call surfaces the real error.

---

## Safety guarantees

The workflow is built so that a bad run **cannot silently lose or duplicate updates**.

| 🎯 Situation | ✅ Behaviour |
|---|---|
| A few upstream checks fail (rate limit, dead URL) | Annotated in the run and skipped; everything else proceeds. Failed entries retry on the next run. |
| More than `MAX_ERROR_RATIO` (default **25 %**) of entries fail, or nvchecker writes no output | The run fails **before** any OBS trigger and `versions.json` is left unchanged. |
| nvchecker returns a `null` / empty version for a package | That package is skipped and never written to `versions.json`; it retries next run. |
| A package does not exist in OBS (preflight returns 404) | Skipped and listed in `skipped_list`. It is not a failure. |
| An OBS trigger fails for one package | That package keeps its old version in `versions.json`, so it is retried next run. The others advance. |
| The trigger step crashes and writes no summary (bad token, script error) | `versions.json` is **not** advanced at all, so no update is lost. |
| Transient network errors calling OBS | Triggers are retried up to 3 times with increasing backoff. |
| Dry run | No OBS calls and no commit. |
| Force-trigger run | Triggers only the named package and does **not** commit `versions.json`. |
| Concurrent runs | Serialised by a concurrency group; a second run queues behind the first. |
| Push races with another commit | Commit first, then `git pull --rebase --autostash`, with up to 3 push attempts. |

---

## Requirements

| Requirement | Version / notes |
|---|---|
| 🐍 **Python** | **3.14** in CI (set by `PYTHON_VERSION` in the workflow). The scripts only need 3.11+ (`tomllib`, modern type hints). |
| 🔍 **nvchecker** | Installed with `pip install 'nvchecker[all]'` |
| 🌐 **requests** | Used by `trigger_obs.py` |
| 🧰 **jq** | Preinstalled on `ubuntu-latest`; used by `cmd` sources |
| 🔑 **OBS token** | `runservice` operation, covering the whole project |

---

## One-time setup

### 1️⃣ Create the OBS token

> [!NOTE]
> The token needs the `runservice` operation and must cover the **whole project** (do not bind it to a single package).

```bash
# Install osc if needed
zypper install osc          # openSUSE
# or: pip install osc

# First run walks you through configuring credentials
osc

# Create a runservice token
osc api -X POST "/person/itachi_re/token?operation=runservice"
```

The response contains the token:

```xml
<status code="ok">
  <summary>Ok</summary>
  <data name="token">abc123xyz789...LONG_TOKEN_STRING...</data>
  <data name="id">42</data>
</status>
```

> [!WARNING]
> Copy the token now. It is shown only **once**.

You can also create it in the web UI: **Profile → Manage Your Tokens → Create Token → Operation: "Run services"**.

### 2️⃣ Add secrets and variables to GitHub

In the repo go to **Settings → Secrets and variables → Actions**.

#### 🔐 Secrets (encrypted)

| Name | Required | Value |
|---|---|---|
| `OBS_TOKEN` | ✅ **yes** | The token from step 1 |
| `NVCHECKER_PAT` | 💡 recommended | A GitHub token used for upstream lookups (see below) |

#### ⚙️ Variables (plain text)

| Name | Required | Default |
|---|---|---|
| `OBS_PROJECT` | ❌ no | `home:itachi_re` |

<details>
<summary><b>ℹ️ About <code>NVCHECKER_PAT</code></b></summary>

<br>

nvchecker only needs to *read public repositories*, so create a **fine-grained personal access token** with **"Public repositories (read-only)"** and no other permissions.

If the secret is not set, the workflow falls back to the built-in `GITHUB_TOKEN` (about **1,000** API requests/hour), which is usually enough but leaves less headroom. A PAT gets **5,000** requests/hour.

</details>

### 3️⃣ Prepare `nvchecker.toml`

Either edit the included `nvchecker.toml`, or generate a starter file from your OBS project:

```bash
pip install requests lxml

python scripts/bootstrap_packages.py \
  --project home:itachi_re \
  --obs-user itachi_re \
  --obs-password YOUR_OBS_PASSWORD \
  --output nvchecker.toml
```

Packages whose upstream could not be detected are marked `# TODO`. Search for those and fill them in by hand. See [Configuring nvchecker](#configuring-nvchecker) for the format.

The file must start with a `[__config__]` block that includes the keyfile line:

```toml
[__config__]
oldver = "versions.json"
newver = "versions_new.json"
max_concurrency = 3
keyfile = ".nvchecker_keyfile.toml"
```

> [!TIP]
> The **section name** of every entry must match the OBS package name exactly. Names containing special characters need quoting, e.g. `["nicotine+"]`.

### 4️⃣ Seed `versions.json`

Before the first real run, record what is already built so the first run does not re-trigger every package. (Only needed when starting from scratch. The workflow creates an empty `versions.json` if none exists, which makes **every** package look new.)

nvchecker reads tokens from a **keyfile**, not from environment variables, so create a local one first. It is git-ignored and must never be committed:

```bash
pip install 'nvchecker[all]'

printf '[keys]\ngithub = "ghp_your_token_here"\n' > .nvchecker_keyfile.toml

nvchecker -c nvchecker.toml          # writes versions_new.json
cp versions_new.json versions.json   # use it as the baseline

git add nvchecker.toml versions.json .gitignore
git commit -m "feat: initial nvchecker setup"
git push
```

> [!TIP]
> `cmd` entries that call the GitHub API (such as `brave-browser-beta`) read `$NVCHECKER_GITHUB_TOKEN` from the shell instead. For a local seed run, also `export NVCHECKER_GITHUB_TOKEN=ghp_your_token_here`.

### 5️⃣ Push and verify

Push, then open **Actions** and wait for the next scheduled run, or click **Run workflow**. A healthy run looks like this:

```text
Project   : home:itachi_re
Dry run   : False
Preflight : enabled
Polling   : disabled

Packages tracked  : 102
Updates detected  : 2

  firedragon: v13.5.1  ->  v13.6.0
    Triggered
  plasma-smart-video-wallpaper-reborn: v2.14.1  ->  v2.15.0
    Triggered

Triggered : 2
Skipped   : 0
Failed    : 0
```

---

## Day-to-day usage

Everything is automatic. Check the **Actions** tab occasionally, or watch for the failure email.

### 🖱️ Manual runs

*Actions → Check Upstream Versions & Trigger OBS → Run workflow*

| Input | Effect |
|---|---|
| `dry_run` = `true` | Check versions and show the diff, but call no OBS API and commit nothing |
| `force_package` = `<name>` | Trigger one package immediately, without waiting for a version change (does not touch `versions.json`) |

### 📊 Reading the results

- **Run summary page** — an *nvchecker* table listing every entry that errored (with the real error message), then an *OBS Trigger Summary* with checked / updated / triggered / failed counts.
- **Log annotations** — the first 10 nvchecker errors appear as warnings on the run.
- **Artifacts** — `nvchecker-run-<N>` (kept 14 days) holds `nvchecker_output.jsonl`, `nvchecker_errors.log`, `versions_new.json` and `trigger_summary.json`. The token keyfile is **never** uploaded.

---

## `trigger_obs.py` reference

The workflow calls the script for you, but you can also run it by hand (for example after a local nvchecker run).

```bash
export OBS_TOKEN=your_token
export OBS_PROJECT=home:itachi_re      # optional, this is the default
export DRY_RUN=true                    # optional, skips all HTTP calls

python scripts/trigger_obs.py \
  --old-versions versions.json \
  --new-versions versions_new.json \
  --output-summary trigger_summary.json
```

### 🏳️ Flags

| Flag | Default | Purpose |
|---|---|---|
| `--old-versions` | `versions.json` | Stored versions file |
| `--new-versions` | `versions_new.json` | Fresh nvchecker output |
| `--output-summary` | `trigger_summary.json` | Where the JSON summary is written |
| `--force-package NAME` | *(none)* | Trigger one package regardless of version changes |
| `--skip-preflight` | off | Skip the OBS existence check (slightly faster, less safe) |
| `--poll-builds` | off | After triggering, poll OBS until all builds settle |
| `--poll-timeout SECONDS` | `300` | Maximum time to wait for builds |
| `--poll-interval SECONDS` | `15` | Time between poll cycles |

### 🌱 Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OBS_TOKEN` | ✅ | OBS API token with the `runservice` operation |
| `OBS_PROJECT` | ❌ | OBS project name (default `home:itachi_re`) |
| `DRY_RUN` | ❌ | `true` to skip all HTTP calls |

### 🔨 Build polling (optional)

> [!NOTE]
> The scheduled workflow does **not** use `--poll-builds`, so runs stay short. Use it locally, or add the flag to the trigger step if you want the run to wait for build results.

With `--poll-builds`, after all packages are triggered the script polls `GET /build/{project}/_result?package={pkg}` for every triggered package. Each repository/architecture is classified independently:

| State | Meaning |
|---|---|
| `succeeded` | Build passed |
| `failed` / `unresolvable` / `broken` | Build failed (the script exits non-zero) |
| `disabled` / `excluded` | Skipped by OBS configuration, not counted |
| `scheduled`, `building`, `finished`, `signing`, `dispatching`, `blocked` | Still in progress, keep polling |

If the timeout expires first, the remaining packages are reported as `timed_out` (also non-zero exit).

### 🧾 Summary file

`trigger_summary.json` contains:

```json
{
  "checked": 102,
  "updated": 2,
  "triggered": 2,
  "skipped": 0,
  "failed": 0,
  "updated_list":  [{"package": "...", "old": "...", "new": "...", "status": "OK"}],
  "skipped_list":  [{"package": "...", "reason": "not found in OBS"}],
  "failed_list":   [{"package": "...", "error": "..."}],
  "poll_results":  {}
}
```

The commit step reads `failed_list` to decide which packages must keep their old version.

---

## Configuring nvchecker

Each entry in `nvchecker.toml` is a section named after the OBS package:

```toml
# ─── GitHub: repos that publish Releases ───────────────────────────
[fastfetch]
source = "github"
github = "fastfetch-cli/fastfetch"
use_latest_release = true

# ─── GitHub: repos that only have tags ─────────────────────────────
[rustdesk]
source = "github"
github = "rustdesk/rustdesk"
use_max_tag = true

# ─── GitHub: repos with NO tags or releases (version = commit hash) ─
[TuxManager]
source = "github"
github = "benapetr/TuxManager"
use_commit = true

# ─── KDE invent (GitLab) ───────────────────────────────────────────
[krita]
source = "gitlab"
host = "invent.kde.org"
gitlab = "graphics/krita"
use_commit = true

# ─── Any git remote ────────────────────────────────────────────────
[gamescope]
source = "git"
git = "https://github.com/ValveSoftware/gamescope.git"
use_max_tag = true

# ─── Scrape a page ─────────────────────────────────────────────────
[cloudflare-warp]
source = "regex"
url = "https://developers.cloudflare.com/changelog/rss/index.xml"
regex = 'Cloudflare One Client for Linux \(version ([\d.]+)\)'
```

### 🐙 Which GitHub option to use

| Option | Use when | Version looks like |
|---|---|---|
| `use_latest_release` | The project publishes GitHub **Releases** | `v2.15.0` |
| `use_max_tag` | The project has **tags** but no releases | `v2.15.0` |
| `use_commit` | The project has **neither** (rolling snapshots) | commit hash |

> [!WARNING]
> If a repo has no tags, `use_max_tag` fails with an HTTP 404 on `.../git/refs/tags`. Switch to `use_commit` or `use_latest_release`.

### 🎛️ Useful modifiers

| Option | Purpose |
|---|---|
| `prefix = "v"` | Strip a leading `v` (tags `v6.1.5` → `6.1.5`) |
| `from_pattern` / `to_pattern` | Regex rewrite of the version string |
| `include_regex` / `exclude_regex` | Filter which tags or releases are considered |

📖 Full option reference: <https://nvchecker.readthedocs.io/en/latest/usage.html>

---

## Adding or removing a package

### ➕ Add

1. Add an entry to `nvchecker.toml` (section name = OBS package name).
2. Run the workflow with **`dry_run = true`** and confirm the version is detected correctly.
3. Commit. On the next run the package appears as `unknown → <version>` and an OBS trigger fires. This is expected and desired. ✅

### ➖ Remove

Delete its section from `nvchecker.toml`. Optionally remove its key from `versions.json` (a stale key is harmless).

---

## Supported upstream sources

| Source | Config | Notes |
|---|---|---|
| 🐙 **GitHub** | `source = "github"` | `use_latest_release`, `use_max_tag` or `use_commit` |
| 🎨 **KDE invent** | `source = "gitlab"` + `host = "invent.kde.org"` | Most KDE / Plasma packages |
| 🖥️ **freedesktop GitLab** | `source = "gitlab"` + `host = "gitlab.freedesktop.org"` | wayland, mesa, … |
| 🪶 **GNOME GitLab** | `source = "gitlab"` + `host = "gitlab.gnome.org"` | GNOME projects |
| 🔗 **Generic git** | `source = "git"` | Any git remote; needs `git =` (not `github =`) |
| 🐍 **PyPI** | `source = "pypi"` | Python packages |
| 📦 **AUR** | `source = "aur"` | Handy for cross-checking |
| 🔍 **Anitya** | `source = "anitya"` | release-monitoring.org projects |
| 🤖 **Android SDK** | `source = "android_sdk"` | build-tools, NDK |
| 🌐 **HTML regex** | `source = "regex"` | Scrape a download page |
| 💻 **Shell command** | `source = "cmd"` | Escape hatch; `curl` and `jq` are available on the runner |

---

## Security notes

This repository is **public**, so keep the following in mind:

- 🔑 **Secrets never appear in the repo.** The workflow only references them by name (`${{ secrets.OBS_TOKEN }}`). GitHub masks their values in logs.
- 📝 **The nvchecker keyfile is generated on the runner** from `NVCHECKER_PAT` (or `GITHUB_TOKEN`) at the start of each run and deleted at the end. Do **not** commit `.nvchecker_keyfile.toml`: a tracked copy would be overwritten with the real token on every run (which also breaks the final `git push`). It is listed in `.gitignore`.
- 📦 **Artifacts on public repos can be downloaded by anyone.** Only logs and version files are uploaded. Never add the keyfile or print tokens while debugging.
- 🛡️ **Use a least-privilege PAT** (fine-grained, public-repo read-only). A leaked token then exposes nothing sensitive. If a real token is ever committed, **revoke it immediately**; git history keeps it forever.
- 🚫 The workflow has **no `pull_request` trigger**, so forks cannot reach the secrets.

---

## Troubleshooting

> [!TIP]
> Start with the run's **summary page** (per-package errors) and the failing step's log.

| Symptom | Likely cause | Fix |
|---|---|---|
| `KeyError('git')` (or `'github'`) for an entry | Key name does not match the `source` (e.g. `github =` under `source = "git"`) | Use the key that matches the source: `git = "https://…"` |
| `HTTP 404 …/git/refs/tags` | The repo has no tags (or was renamed / made private) | Use `use_commit` or `use_latest_release`, or fix the repo name |
| `command exited without output` | A `cmd` entry printed nothing (page changed, or the site blocks CI IPs) | Run the command by hand and fix it |
| `version string not found` | A `regex` entry no longer matches the page | Update the regex |
| HTTP 403 / rate limit exceeded | Token missing, expired, or budget used up | Check the *Check GitHub API auth and rate limit* step; refresh `NVCHECKER_PAT` |
| Run fails with *"N% of entries errored"* | Over the `MAX_ERROR_RATIO` threshold, usually a token or network problem | Fix the errors listed on the summary page; nothing was triggered or committed |
| `pip install 'nvchecker[all]'` fails after a Python bump | A dependency has no wheel for the new Python yet | Set `PYTHON_VERSION` back to the previous release (see [Maintenance](#maintenance)) |
| `cannot pull with rebase: You have unstaged changes` | A tracked file was modified during the run, typically `.nvchecker_keyfile.toml` or `versions_new.json` committed to the repo | `git rm --cached <file>` and add it to `.gitignore` |
| `Could not push versions.json after 3 attempts` | Same as above, or branch protection blocks the bot | Check the step log; allow `github-actions[bot]` to push to `main` |
| Same packages re-trigger every run | `versions.json` is not advancing (the commit step is failing) or the version string format differs | Fix the commit step; compare `versions_new.json` with `versions.json` and add `prefix` if needed |
| Same packages trigger once more after a failed push | Expected: the earlier run triggered OBS but could not save the new versions | Nothing to do. It settles after one successful run |
| Package shows up in `skipped_list` | It does not exist in the OBS project (preflight returned 404) | Create the package in OBS or remove its `nvchecker.toml` entry |
| OBS trigger returns **404** | The section name does not match the OBS package name | Compare with [home:itachi_re](https://build.opensuse.org/project/show/home:itachi_re) |
| OBS trigger returns **401** | `OBS_TOKEN` is wrong or expired | Recreate the token (step 1) and update the secret |
| Tag filter has no effect | `include_pattern` / `exclude_pattern` are not nvchecker options | Use `include_regex` / `exclude_regex` |

### 🔬 Reproducing locally

```bash
nvchecker -c nvchecker.toml --logger json
```

---

## Maintenance

### 🐍 Changing the Python version

The version lives in one place, the `env:` block of `.github/workflows/check-updates.yml`:

```yaml
env:
  PYTHON_VERSION: '3.14'
```

- Keep the **quotes**, otherwise YAML can misread values such as `3.10` as the number `3.1`.
- `'3.x'` always resolves to the newest stable release, but it can pick up a release that nvchecker's dependencies do not support yet.
- Before moving to a newer release, run the workflow once with **`dry_run = true`** and check that `pip install 'nvchecker[all]'` and the nvchecker step both succeed.
- Remember to update the Python badge at the top of this README.

### 🔄 Keeping actions up to date

The workflow pins major versions of `actions/checkout`, `actions/setup-python` and `actions/upload-artifact`. Enable Dependabot for `github-actions` if you want automatic update PRs.

### 🧹 Housekeeping

- Remove packages from `nvchecker.toml` when you drop them from OBS, so they stop appearing in the summaries.
- `versions.json` only ever holds the last triggered version per package, so it stays small and does not need pruning.

---

## File reference

```text
.
├── .github/
│   └── workflows/
│       └── check-updates.yml      # cron + trigger + commit logic
├── scripts/
│   ├── trigger_obs.py             # fires OBS API calls for updated packages
│   └── bootstrap_packages.py      # one-time: generate nvchecker.toml from OBS
├── nvchecker.toml                 # WHERE to find each package's upstream version
├── versions.json                  # LAST KNOWN version of each package (git-tracked)
└── .gitignore                     # must ignore the runtime files listed below
```

### 🧪 Runtime files (generated, never committed)

<details>
<summary><b>Click to expand the runtime file table</b></summary>

<br>

| File | Created by | Purpose |
|---|---|---|
| `.nvchecker_keyfile.toml` | Workflow (from the `NVCHECKER_PAT` / `GITHUB_TOKEN` secret) | API token for nvchecker; deleted at the end of the run |
| `versions_new.json` | nvchecker | Freshly detected versions; merged into `versions.json` |
| `nvchecker_output.jsonl` / `nvchecker_errors.log` | Workflow | Raw nvchecker logs (uploaded as artifacts) |
| `trigger_summary.json` | `trigger_obs.py` | Per-package trigger results; drives the merge step |

</details>

### 🚫 Recommended `.gitignore`

```gitignore
.nvchecker_keyfile.toml
versions_new.json
nvchecker_output.jsonl
nvchecker_errors.log
trigger_summary.json
```

---

<div align="center">

### ⭐ Found this useful?

If OBS Auto-Trigger saves you time, consider giving it a star. It helps others discover it too.

[![Star this repo](https://img.shields.io/badge/%E2%AD%90%20Star-this%20repo-4d6bfe?style=for-the-badge)](https://github.com/itachi-re/obs-auto-trigger/stargazers)
[![Report an issue](https://img.shields.io/badge/%F0%9F%90%9B%20Report-an%20issue-d73a49?style=for-the-badge)](https://github.com/itachi-re/obs-auto-trigger/issues)

<sub>Built with ❤️ using <a href="https://nvchecker.readthedocs.io/">nvchecker</a> · Powered by <a href="https://github.com/features/actions">GitHub Actions</a> · Published on <a href="https://build.opensuse.org/project/show/home:itachi_re">OBS</a></sub>

</div>
