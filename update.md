# YouCut — Software Update & Release Guide

This guide explains how the YouCut automated update system works and provides step-by-step instructions on how to publish updates to all distributed copies of your software.

---

## 1. How the Update System Works

YouCut uses a dual-layer update architecture designed to ensure zero downtime and minimal friction:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        YouCut Update System                            │
├───────────────────────────────────┬────────────────────────────────────┤
│ 1. Application Layer (YouCut.exe) │ 2. Video Engine Layer (yt-dlp)     │
├───────────────────────────────────┼────────────────────────────────────┤
│ • Queries GitHub Releases API     │ • Checks PyPI API in background    │
│ • Compares semantic version tags  │ • Auto-updates binary in-place     │
│ • Displays banner in Settings     │ • Persists in ~/.youcut/bin/       │
│ • Direct 1-click download link    │ • Ensures downloads never break    │
└───────────────────────────────────┴────────────────────────────────────┘
```

When a user opens YouCut (or clicks **"Check for Updates Now"** in the **Settings** panel):
1. YouCut queries `https://api.github.com/repos/Ady1107/YouCut/releases/latest` directly.
2. It extracts `tag_name` (e.g. `v1.0.1`), release notes (`body`), and the direct installer asset URL (`YouCut-Setup-*.exe`).
3. If the release version is strictly newer than `APP_VERSION` (using proper semver comparison via `packaging.version`), a prominent banner appears in the **Settings** tab.
4. The banner displays the version bump, your release notes, and a **"⬇️ Download YouCut Update"** button that directly downloads the latest setup installer.
5. User settings and download history (`~/.youcut/`) are completely preserved across updates.

---

## 2. Automated Release Workflow (Zero-Maintenance)

The update system is connected directly to GitHub Releases. There are **no manual file hosting steps, no gists, and no separate version.json files**.

### How to Release a New Version (e.g., v1.0.1):

Only two quick commands are required to publish an update:

#### Step 1: Bump `APP_VERSION`
In `clipgrab/version.py`, update the version string:
```python
APP_VERSION = "1.0.1"
```

#### Step 2: Commit, Tag, and Push
```powershell
git add clipgrab/version.py
git commit -m "chore: bump version to v1.0.1"
git push
git tag v1.0.1
git push origin v1.0.1
```

### What Happens Automatically:
1. GitHub Actions (`.github/workflows/release.yml`) immediately triggers on the `v1.0.1` tag.
2. The CI runner:
   - Sets up Python 3.11 and installs build dependencies.
   - Downloads the latest official static `ffmpeg.exe` and `yt-dlp.exe` binaries.
   - Builds the fast `--onedir` standalone executable via PyInstaller.
   - Compiles `YouCut-Setup-v1.0.1.exe` via Inno Setup with desktop and Start Menu shortcuts.
   - Generates `YouCut-Portable-v1.0.1.zip` for portable usage.
   - Publishes the official GitHub Release with release notes and attaches both artifacts.
3. **All active users of YouCut will immediately detect the update upon their next launch!**

---

## 3. yt-dlp Automatic Background Updates

In addition to app updates, YouTube frequently modifies its internal video streaming protocols. YouCut protects against broken downloads with automatic yt-dlp updates:
- Runs in the background every 30 minutes (respects a 1-hour cooldown).
- Installs the updated binary into `%USERPROFILE%\.youcut\bin\yt-dlp.exe`.
- Because user-level binaries persist across restarts, users never have to manually update yt-dlp.
- Can be toggled on/off in **Settings > Software & Component Updates**.
