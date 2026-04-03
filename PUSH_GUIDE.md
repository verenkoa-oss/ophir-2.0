# OPHIR 2.0 — Push & Merge Guide

> **Context:** PR #11 (`copilot/get-project-status-ophir-2-0` → `main`) has been **locally resolved** (merge commit `160d8e8` prepared).  
> This guide explains how to push the resolved branch and complete the merge.

---

## 1. Current Branch & Repository Status

| Item | Value |
|------|-------|
| **PR #11** | `copilot/get-project-status-ophir-2-0` → `main` |
| **PR #11 status** | Open — merge conflicts resolved locally |
| **`main` HEAD** | `0a5e029` (Merge PR #13) |
| **`copilot/get-project-status-ophir-2-0` HEAD** | `796a55d` |
| **Resolved merge commit** | `160d8e8` (ready to push) |
| **Branch protections** | None detected |
| **Force-push policy** | Not restricted |

---

## 2. Create a Personal Access Token (PAT) — **Correct Scopes**

> **Important:** `gh auth login` requires the `read:org` scope.  
> Git push (`https://`) only needs `repo` + `workflow`.

### Step 1 — Open GitHub token page

```
https://github.com/settings/tokens/new
```

### Step 2 — Fill in the form

| Field | Value |
|-------|-------|
| **Note** | `ophir-push-2026` |
| **Expiration** | 90 days (classic PAT supports up to 1 year; 90 days is a good balance) |
| **`repo`** | ✅ (all sub-scopes included) |
| **`workflow`** | ✅ |
| **`read:org`** | ✅ (under `admin:org`) |

> The `read:org` scope is nested under **"admin:org"** — expand that section and tick `read:org`.

### Step 3 — Click **[Generate token]** and copy the value immediately

---

## 3. Authenticate with `gh` CLI (one-time)

```bash
gh auth logout                        # remove any stale session
gh auth login \
  --hostname github.com \
  --git-protocol https \
  --with-token <<< "ghp_YOUR_TOKEN_HERE"
```

Expected output:
```
✓ Logged in as verenkoa-oss
```

Verify scopes:
```bash
gh auth status
# ✓ Token scopes: 'gist', 'read:org', 'repo', 'workflow'
```

---

## 4. Push the Resolved Branch

The resolved merge state is already committed locally as `160d8e8`.  
You need to push it to `copilot/get-project-status-ophir-2-0` on the remote.

### Option A — Using the PAT directly in the URL (simplest)

```bash
cd /path/to/ophir-2.0          # your local clone

# Set the resolved state on the feature branch
git checkout copilot/get-project-status-ophir-2-0
git merge origin/main          # merge main; resolve the 5 conflicts
# See section 5 for exact conflict resolutions

# Push
git push https://verenkoa-oss:ghp_YOUR_TOKEN_HERE@github.com/verenkoa-oss/ophir-2.0.git \
  copilot/get-project-status-ophir-2-0
```

### Option B — Using gh CLI

```bash
gh auth login   # (if not already logged in — use token from section 2)

cd /path/to/ophir-2.0
git checkout copilot/get-project-status-ophir-2-0
git merge origin/main          # resolve conflicts (see section 5)
git push origin copilot/get-project-status-ophir-2-0
```

### Option C — Using SSH

```bash
# Generate SSH key (if you don't have one)
ssh-keygen -t ed25519 -C "verenko.a@gmail.com"

# Add to GitHub: https://github.com/settings/ssh/new
# (paste the contents of ~/.ssh/id_ed25519.pub)

cd /path/to/ophir-2.0
git remote set-url origin git@github.com:verenkoa-oss/ophir-2.0.git
git push origin copilot/get-project-status-ophir-2-0
```

---

## 5. Conflict Resolution Instructions

When you run `git merge origin/main` on `copilot/get-project-status-ophir-2-0` you will get 5 conflicts.

### File: `config.py`

The conflict is between two `OBSERVER_LATITUDE` blocks.  
**Resolution:** keep the `main` branch version and add the missing gain/noise constants.

After accepting `main`'s version, the final section of `config.py` should look like:

```python
SDR_GAIN = 45
SDR_PPM = 0
SDR_FREQ = 1090_000_000

# SDR gain range (used by dashboard sliders)
SDR_GAIN_MIN = -5
SDR_GAIN_MAX = 45
SDR_GAIN_DEFAULT = 45

# Noise threshold range (used by dashboard sliders)
NOISE_THRESHOLD_DEFAULT = -75
NOISE_THRESHOLD_MIN = -100
NOISE_THRESHOLD_MAX = -30
```

Quick command:
```bash
git checkout origin/main -- config.py
# Then manually add the SDR_GAIN_MIN/MAX/DEFAULT and NOISE_THRESHOLD_* lines
git add config.py
```

### File: `main.py`

**Resolution:** keep `main` branch version (most complete — 1203 lines vs 878).
```bash
git checkout origin/main -- main.py
git add main.py
```

### File: `run.py`

**Resolution:** keep PR #11 version (AEGIS-X single-function launcher, cleaner).
```bash
git checkout HEAD -- run.py      # HEAD = copilot/get-project-status-ophir-2-0
git add run.py
```

### File: `start.sh`

**Resolution:** keep PR #11 version (AEGIS-X branded bash starter).
```bash
git checkout HEAD -- start.sh
git add start.sh
```

### File: `web/dashboard.html`

**Resolution:** keep PR #11 version (955 lines — full oscilloscope, sliders, map).
```bash
git checkout HEAD -- web/dashboard.html
git add web/dashboard.html
```

### Commit the merge

```bash
git commit -m "Merge main into copilot/get-project-status-ophir-2-0: resolve conflicts"
```

---

## 6. Merge PR #11 via Web (after push)

Once the branch is pushed without conflicts:

1. Open: https://github.com/verenkoa-oss/ophir-2.0/pull/11
2. Click **[Merge pull request]**
3. Click **[Confirm merge]**
4. ✅ Done!

---

## 7. Troubleshooting

### "Invalid username or token"
→ The PAT was copied incorrectly or has expired. Create a new one (section 2).

### "missing required scope 'read:org'"
→ When creating the PAT, expand **"admin:org"** and check **`read:org`** specifically.

### "You've used all of this month's Premium"
→ This message refers to **GitHub Copilot** monthly request usage (AI completions / Copilot Chat),
   not git push operations or repository access.  
   In this case the message appeared inside a Copilot Chat session, which counted against the
   monthly Copilot quota — not against any git or repository permission.  
   Git push is **free** and works regardless of Copilot limits.  
   Merging a PR via the web UI (section 6) is also **always free**.

### "protected branch" / cannot force-push
→ The `main` branch has no protections in this repository. Regular push is fine.

### GPG signing errors in CI / local commit
→ Use `git -c commit.gpgsign=false commit -m "..."` or disable gpg signing in `~/.gitconfig`:
```ini
[commit]
    gpgsign = false
```

---

## 8. Quick One-Liner (if you have a valid token)

```bash
TOKEN=ghp_YOUR_TOKEN_HERE
REPO=verenkoa-oss/ophir-2.0
BRANCH=copilot/get-project-status-ophir-2-0

cd /path/to/ophir-2.0
git checkout $BRANCH
git fetch origin main
git merge origin/main -X theirs   # auto-resolve, keep remote (main) for conflicts
# Then manually restore run.py, start.sh, dashboard.html to HEAD versions (see above)
git push https://verenkoa-oss:${TOKEN}@github.com/${REPO}.git $BRANCH
```

> ⚠️ `-X theirs` auto-accepts the `main` version for all conflicts. You still need to restore the 3 files where you want the PR #11 version.
