# OBS Auto-Trigger — Automated Version Bumps for home:itachi_re

A single GitHub repository that monitors upstream versions of **all 80+ packages**
in your openSUSE OBS project and automatically fires an OBS service run
(`obs_scm` re-fetch) the moment a new version is detected.

```
Upstream (GitHub / KDE invent / freedesktop…)
         │  new tag / release
         ▼
   nvchecker checks versions every 6 h  (GitHub Actions cron)
         │  version changed?
         ▼
   trigger_obs.py  →  POST /trigger/runservice  →  OBS re-fetches sources
         │
         ▼
   versions.json updated & committed back to this repo
```

```
                ┌──────────────────────────┐
                │   GitHub "control repo"  │
                │  (your single repo)      │
                └────────────┬─────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                     │
 Upstream APIs       Version tracking      Scheduler (cron)
 (GitHub releases)   (JSON/YAML DB)        (GitHub Actions)
        │                    │                     │
        └──────────────┬─────┴─────────────────────┘
                       │                   │
               Detect new versions         │
                       │                   │
                       └───────┬───────────┘
                               │
                      Trigger OBS API
                               │
                ┌──────────────▼──────────────┐
                │   Open Build Service (OBS)  │
                │ osc service rr / rebuild    │
                └─────────────────────────────┘
```
---

## Table of Contents

1. [How it works](#how-it-works)
2. [One-time setup](#one-time-setup)
   - [Step 1 — Create the OBS token](#step-1--create-the-obs-token)
   - [Step 2 — Add secrets & variables to GitHub](#step-2--add-secrets--variables-to-github)
   - [Step 3 — Bootstrap nvchecker.toml](#step-3--bootstrap-nvcheckertoml)
   - [Step 4 — Populate versions.json with current versions](#step-4--populate-versionsjson-with-current-versions)
   - [Step 5 — Push & verify](#step-5--push--verify)
3. [Day-to-day usage](#day-to-day-usage)
4. [Adding a new package](#adding-a-new-package)
5. [Supported upstream sources](#supported-upstream-sources)
6. [Troubleshooting](#troubleshooting)
7. [File reference](#file-reference)

---

## How it works

| Component | Role |
|---|---|
| `nvchecker.toml` | Declares where to find the upstream version of every package |
| `versions.json` | Stores the last-known version of each package (committed to git) |
| `.github/workflows/check-updates.yml` | Cron job: runs nvchecker, diffs versions, fires OBS triggers |
| `scripts/trigger_obs.py` | Calls `POST /trigger/runservice` on OBS for every updated package |
| `scripts/bootstrap_packages.py` | One-time helper to generate a starter `nvchecker.toml` from your OBS package list |

**nvchecker** reads `nvchecker.toml`, checks each upstream, and writes
`versions_new.json`. `trigger_obs.py` compares `versions.json` (old) with
`versions_new.json` (new), and for every package whose version changed it calls:

```
POST https://build.opensuse.org/trigger/runservice
     ?project=home:itachi_re
     &package=<PACKAGE_NAME>
Authorization: Token <OBS_TOKEN>
```

This triggers the `_service` file in that OBS package to run — which for
`obs_scm` means OBS fetches the latest commit/tag from the upstream SCM,
creates a new tarball, updates the version in the `.spec` file, and queues
a rebuild.

---

## One-time setup

### Step 1 — Create the OBS token

The token must have `operation=runservice` scope.
Create it **once** with `osc`:

```bash
# Install osc if not already installed
zypper install osc          # on openSUSE
# or: pip install osc

# Configure osc (stores credentials in ~/.config/osc/oscrc)
osc                          # just run it once and follow prompts

# Create a project-wide runservice token
osc api -X POST \
  "/person/itachi_re/token?operation=runservice"
```

The response looks like:

```xml
<status code="ok">
  <summary>Ok</summary>
  <data name="token">abc123xyz789...LONG_TOKEN_STRING...</data>
  <data name="id">42</data>
</status>
```

**Copy the token string** — you will not see it again.
You can also create it from the OBS Web UI:

> Profile → Manage Your Tokens → Create Token → Operation: "Run services"

> ⚠️ **Do NOT create a token bound to a specific package** — you need one
> token that works for the whole project.

---

### Step 2 — Add secrets & variables to GitHub

In your GitHub repository go to **Settings → Secrets and variables → Actions**.

#### Secrets (encrypted):

| Name | Value |
|---|---|
| `OBS_TOKEN` | The long token string from Step 1 |

#### Variables (plain text, optional):

| Name | Value | Default |
|---|---|---|
| `OBS_PROJECT` | `home:itachi_re` | `home:itachi_re` |

---

### Step 3 — Bootstrap nvchecker.toml

You can either:

**Option A — Use the included `nvchecker.toml` as a starting point.**
It already has entries for the standard KDE Plasma 6 and KDE Frameworks
packages. Delete/add entries to match your actual package list.

**Option B — Auto-generate from your OBS package list** using the bootstrap
script:

```bash
# Install dependencies
pip install requests lxml

# Run the bootstrapper (reads your _service files to detect upstream URLs)
python scripts/bootstrap_packages.py \
  --project home:itachi_re \
  --obs-user itachi_re \
  --obs-password YOUR_OBS_PASSWORD \
  --output nvchecker.toml
```

This will produce a `nvchecker.toml` with one section per package.
Packages whose upstream it couldn't detect are marked with `# TODO` —
search for those and fill them in manually.

**Each package entry looks like this:**

```toml
# KDE invent.kde.org (GitLab)
[plasma-desktop]
source = "gitlab"
host = "https://invent.kde.org"
gitlab = "plasma/plasma-desktop"
use_max_tag = true

# GitHub with releases
[some-app]
source = "github"
github = "owner/repo"
use_latest_release = true

# PyPI
[python-foo]
source = "pypi"
pypi = "foo"
```

> The **section name** (e.g. `[plasma-desktop]`) **must exactly match**
> the package name in OBS. If your OBS package is named `plasma5-desktop`,
> the section must be `[plasma5-desktop]`.

---

### Step 4 — Populate versions.json with current versions

Before the first real run, seed `versions.json` so the system knows what's
already built and doesn't re-trigger everything at once.

```bash
# Install nvchecker locally
pip install 'nvchecker[all]'

# Export your GitHub token so nvchecker can use the GitHub API
export NVCHECKER_GITHUB_TOKEN=ghp_your_token_here

# Run nvchecker — this writes versions_new.json
nvchecker -c nvchecker.toml

# Use the freshly-checked versions as the baseline
cp versions_new.json versions.json

# Commit both files
git add nvchecker.toml versions.json .gitignore
git commit -m "feat: initial nvchecker setup"
git push
```

---

### Step 5 — Push & verify

After pushing, go to **Actions** in your GitHub repo and either:
- Wait for the next scheduled run (every 6 hours), or
- Click **Run workflow** → **Run workflow** to trigger it manually.

Check the workflow log for output like:

```
Packages tracked  : 83
Updates detected  : 2

  📦 plasma-desktop: 6.1.4 → 6.1.5
      ✅ Triggered
  📦 kwin: 6.1.4 → 6.1.5
      ✅ Triggered

Triggered : 2
Failed    : 0
```

---

## Day-to-day usage

Everything runs automatically. The workflow runs every 6 hours.

**Manual force-trigger** — you can trigger a specific package right now from
the GitHub Actions UI without waiting for a version change:

1. Go to **Actions → Check Upstream Versions & Trigger OBS**
2. Click **Run workflow**
3. Fill in **Force-trigger a specific package name** (e.g. `plasma-desktop`)
4. Click **Run workflow**

**Dry run** — check what would be triggered without actually calling OBS:

1. Run workflow → set **Dry run** to `true`

---

## Adding a new package

1. Add a new entry to `nvchecker.toml`:

   ```toml
   [my-new-package]
   source = "github"
   github = "owner/repo"
   use_latest_release = true
   ```

2. Run nvchecker locally or trigger the workflow with **dry run** first to
   verify the version is detected correctly.

3. Commit `nvchecker.toml`. The next scheduled run will pick it up.
   On first run, the new package version will appear as "new" (old version
   = unknown) and an OBS trigger will fire. This is expected and desired.

---

## Supported upstream sources

| Source | Key | Notes |
|---|---|---|
| KDE invent.kde.org | `source = "gitlab"` + `host = "https://invent.kde.org"` | Most KDE/Plasma packages |
| freedesktop GitLab | `source = "gitlab"` + `host = "https://gitlab.freedesktop.org"` | wayland, mesa, etc. |
| GNOME GitLab | `source = "gitlab"` + `host = "https://gitlab.gnome.org"` | |
| GitHub | `source = "github"` | Use `use_latest_release` or `use_max_tag` |
| PyPI | `source = "pypi"` | Python packages |
| AUR | `source = "aur"` | Useful for cross-checking |
| Generic git tags | `source = "git"` | Any git repo |
| HTML regex | `source = "regex"` | Scrape a download page |
| Shell command | `source = "cmd"` | Ultimate escape hatch |

Full documentation: https://nvchecker.readthedocs.io/en/latest/usage.html

**Tag prefix stripping** — many repos use tags like `v6.1.5` while the spec
uses `6.1.5`. Add `prefix = "v"` to strip the prefix:

```toml
[libfoo]
source = "github"
github = "owner/libfoo"
use_max_tag = true
prefix = "v"
```

---

## Troubleshooting

### nvchecker returns no versions / errors

Check `nvchecker_errors.log` in the workflow artifacts. Common issues:

- **GitHub rate limit** — `GITHUB_TOKEN` is provided automatically but has
  a 1000 req/hr limit for workflow tokens. For heavy use, create a personal
  access token and store it as `MY_GITHUB_PAT` secret, then change the
  workflow to use it.
- **Tag pattern wrong** — use `nvchecker --logger json -c nvchecker.toml`
  locally to debug. Add `include_pattern` or `exclude_pattern` if needed.
- **`use_latest_tag` requires a PAT** — this uses the GitHub GraphQL API.
  Switch to `use_max_tag` (REST API) which works with `GITHUB_TOKEN`.

### OBS trigger returns 404

The package name in `nvchecker.toml` doesn't match the OBS package name.
Check your OBS project at:
```
https://build.opensuse.org/project/show/home:itachi_re
```

### OBS trigger returns 401

Your `OBS_TOKEN` secret is wrong or expired. Re-create the token:
```bash
osc api -X POST "/person/itachi_re/token?operation=runservice"
```
Then update the `OBS_TOKEN` secret in GitHub.

### A package keeps triggering even though it's up to date

The version string nvchecker returns doesn't match what's in `versions.json`.
Check what nvchecker produces:
```bash
nvchecker -c nvchecker.toml
cat versions_new.json
```
If the format differs (e.g. `v6.1.5` vs `6.1.5`), add `prefix = "v"` to
strip the prefix, then re-seed `versions.json`.

---

## File reference

```
.
├── .github/
│   └── workflows/
│       └── check-updates.yml      ← GitHub Actions cron + trigger logic
├── scripts/
│   ├── trigger_obs.py             ← Fires OBS API calls for updated packages
│   └── bootstrap_packages.py     ← One-time: generates nvchecker.toml from OBS
├── nvchecker.toml                 ← WHERE to find the upstream version of each package
├── versions.json                  ← LAST KNOWN version of each package (git-tracked)
├── .nvchecker_keyfile.toml        ← Local API keys (git-IGNORED, never commit)
└── .gitignore
```

`versions_new.json` is written by nvchecker at runtime and is **not** committed
to git (listed in `.gitignore`). After each successful run, its contents are
merged into `versions.json` and that file is committed.
