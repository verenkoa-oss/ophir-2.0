# GitHub Subscription Guide: Copilot Pro vs GitHub Pro

This document clarifies what the **GitHub Copilot Pro** subscription ($10/month) covers and what it does **not** cover, based on the account setup visible in the billing screenshots (Copilot Pro free trial + GitHub Free base plan).

---

## Your current setup

| Component | Status |
|-----------|--------|
| Base plan | **GitHub Free** |
| AI add-on | **Copilot Pro** (free trial, $10/month after May 1, 2026) |

---

## What GitHub Copilot Pro ($10/month) INCLUDES

| Feature | Included |
|---------|----------|
| AI code completion in VS Code / JetBrains / Neovim | ✅ |
| Copilot Chat (ask questions about code) | ✅ |
| Copilot on GitHub.com (PR summaries, AI code review) | ✅ |
| Multi-file suggestions and context-aware generation | ✅ |
| Copilot CLI (terminal assistant) | ✅ |

In short: **everything AI/Copilot-related** is covered by this $10.

---

## What GitHub Copilot Pro does NOT include

Copilot is an AI add-on only. It does **not** change your base GitHub plan limits.

| Feature | Copilot Pro | GitHub Free | GitHub Pro ($4/month) |
|---------|-------------|-------------|------------------------|
| Unlimited public/private repositories | ✅ same | ✅ | ✅ |
| git push / merge / pull (no limit) | ✅ same | ✅ always free | ✅ always free |
| Creating issues, PRs, branches | ✅ same | ✅ always free | ✅ always free |
| GitHub Actions minutes/month | ❌ 2,000 min (Free) | 2,000 min | **3,000 min** |
| GitHub Packages storage | ❌ 500 MB (Free) | 500 MB | **2 GB** |
| GitHub Pages (advanced features) | ❌ basic only | basic only | **advanced** |
| Protected branch rules (advanced) | ❌ | limited | ✅ |
| Code owners, required reviews | ❌ | ❌ | ✅ |
| Wiki (private repos) | ❌ | ❌ | ✅ |

---

## Key clarification: git push and merge have NO limits

`git push`, `git merge`, `git pull` — these operations are **always free and unlimited** on both GitHub Free and GitHub Pro. They are not gated by any subscription tier.

If you experience errors with push or merge, the cause is **not** a missing subscription. Common real causes:
- Branch protection rules requiring a review before merge
- Authentication problem (wrong token or SSH key)
- Conflict that must be resolved before merge
- Repository-level settings requiring status checks to pass

---

## What to upgrade to if you need more Actions or Packages

If you run out of **Actions minutes** (2,000/month) or **Packages storage** (500 MB), upgrade your **base plan** to GitHub Pro:

- Go to **Settings → Billing and plans → Upgrade to GitHub Pro** ($4/month)
- This is separate from Copilot and must be purchased independently

| Need | Solution |
|------|----------|
| More AI code help | Copilot Pro — already active ✅ |
| More Actions minutes (3,000/month) | Upgrade base plan → **GitHub Pro** |
| More Packages storage (2 GB) | Upgrade base plan → **GitHub Pro** |
| Unlimited push/merge/issues | Nothing needed — already free ✅ |

---

## Summary

> **Copilot Pro ($10/month) = AI assistant only.**
> It does not unlock extra Actions minutes, storage, or any other base GitHub limits.
> To get more compute/storage, upgrade your **base plan** to GitHub Pro ($4/month) separately.
> All core git operations (push, merge, issues, PRs) are already unlimited on GitHub Free.

---

*References:*
- [GitHub Copilot plans](https://github.com/features/copilot)
- [GitHub pricing](https://github.com/pricing)
- [Compare plans](https://github.com/pricing#compare-features)
