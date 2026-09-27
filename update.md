# YouCut — Software Update & Distribution Guide

This guide explains how the YouCut update system works and provides step-by-step instructions on how to publish updates to all distributed copies of your software.

---

## 1. How the Update System Works

YouCut uses a dual-layer update architecture designed to ensure zero downtime and minimal friction:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        YouCut Update System                            │
├───────────────────────────────────┬────────────────────────────────────┤
│ 1. Application Layer (YouCut.exe) │ 2. Video Engine Layer (yt-dlp)     │
├───────────────────────────────────┼────────────────────────────────────┤
│ • Checks remote `version.json`    │ • Checks PyPI API in background    │
│ • Detects new app releases        │ • Auto-updates binary in-place     │
│ • Displays banner in Settings     │ • Persists in `~/.youcut/bin/`     │
│ • Direct 1-click download link    │ • Ensures downloads never break    │
└───────────────────────────────────┴────────────────────────────────────┘
```

When a user opens YouCut (or clicks **"Check for Updates Now"** in the **Settings** panel):
1. YouCut queries your remote `version.json` URL.
2. If the remote version (e.g. `1.1.0`) is higher than the installed version (e.g. `1.0.0`), a prominent banner appears in the **Settings** tab.
3. The banner displays the version bump, your release notes, and a **"⬇️ Download YouCut Update"** button that opens the download link directly in their browser.
4. User settings and download history (`~/.youcut/`) are completely preserved when replacing `YouCut.exe`.

---

## 2. One-Time Setup: Connecting Your Update URL

Before distributing `YouCut.exe`, you need to host your `version.json` online so all distributed copies know where to check for updates.

### Option A: GitHub Gist (Recommended — Free, instant, zero maintenance)

1. Go to [gist.github.com](https://gist.github.com).
2. Enter `version.json` as the file name.
3. Paste the contents of your local `version.json`:
   ```json
   {
     "version": "1.0.0",
     "release_date": "2026-09-25",
     "release_notes": "Initial release of YouCut v1.0.0 — Precision YouTube clipper, queue manager, and modern dark/light UI.",
     "download_url": "https://github.com/Ady1107/YouCut/releases/download/v1.0.0/YouCut-Setup-v1.0.0.exe",
     "min_required_version": "1.0.0"
   }
   ```
4. Click **Create secret gist** (or **Create public gist**).
5. Click the **"Raw"** button in the top right of the gist code box.
6. Copy the URL from your browser address bar. It will look like:
   `https://gist.githubusercontent.com/raw/Ady1107/.../version.json`
7. Open `clipgrab/version.py` in your code editor and paste that URL into `VERSION_CHECK_URL`:
   ```python
   VERSION_CHECK_URL = "https://gist.githubusercontent.com/raw/Ady1107/.../version.json"
   ```

### Option B: GitHub Repository (GitHub Releases)

If you host the code in a public GitHub repository (`github.com/Ady1107/YouCut`):
1. Put `version.json` in the root of your `main` branch.
2. The raw URL will be:
   `https://raw.githubusercontent.com/Ady1107/YouCut/main/version.json`
3. Paste this URL into `clipgrab/version.py`.

---

## 3. Step-by-Step: How to Release a New Update

Whenever you add new features, fix bugs, or release a new version, follow these 4 steps:

### Step 1: Bump the App Version
Open `clipgrab/version.py` and update the version string:
```python
# Change from "1.0.0" to your new version:
APP_VERSION = "1.0.1"
```

### Step 2: Build the Standalone `YouCut.exe`
Run PyInstaller using the project spec file:
```powershell
pyinstaller clipgrab/build.spec --clean --distpath dist
```
This packages everything (Python runtime, PySide6, FFmpeg, yt-dlp, assets, and icons) into a single, self-contained `dist/YouCut.exe`.

### Step 3: Upload the New `YouCut.exe`
Upload `dist/YouCut.exe` to your hosting location:
- **GitHub Releases** (Recommended): Create a new Release (e.g. tag `v1.0.1`) and attach `YouCut.exe` as a binary asset. Copy the direct download link for the asset.
- **Direct Web Host / Cloud Storage**: Any direct download URL (e.g. Dropbox direct link, AWS S3, personal server).

### Step 4: Update Online `version.json`
Edit your hosted `version.json` (on GitHub Gist or your repo) to reflect the new release:
```json
{
  "version": "1.0.1",
  "release_date": "2026-10-01",
  "release_notes": "Added support for chapter detection, faster trimming, and bug fixes.",
  "download_url": "https://github.com/Ady1107/YouCut/releases/download/v1.0.1/YouCut-Setup-v1.0.1.exe",
  "min_required_version": "1.0.0"
}
```

**That's it!** All distributed copies of YouCut will immediately see the update banner when they start or check for updates.

---

## 4. How Users Receive and Apply Updates

1. **Automatic Detection**: On app launch, YouCut performs an asynchronous, non-blocking check. If a new version is detected, a toast notification alerts the user:
   > *"YouCut v1.0.1 is available! Check Settings to download."*
2. **Manual Detection**: The user can open **Settings** tab anytime and click **"🔍 Check for Updates Now"**.
3. **Download & Run**:
   - The user clicks **"⬇️ Download YouCut Update"** inside the Settings banner.
   - The new `YouCut.exe` downloads to their computer.
   - They replace their old `YouCut.exe` with the new one.
   - All existing user preferences, downloads folder settings, theme preference, and download history in `%USERPROFILE%\.youcut` are automatically preserved!

---

## 5. Testing the Update Notification Locally

You can test the update alert without recompiling or hosting anything online:

1. Open `%USERPROFILE%\.youcut\config.json`.
2. Add or update the `"app_update_url"` field pointing to a local or test JSON file/server:
   ```json
   {
     "app_update_url": "https://raw.githubusercontent.com/.../version.json"
   }
   ```
3. Ensure that the test `version.json` has a version higher than `APP_VERSION` (e.g. `"version": "9.9.9"`).
4. Run `YouCut.exe`, switch to **Settings**, and click **"Check for Updates Now"**.
5. You will see the update banner appear immediately with your test release notes and download button!

---

## 6. yt-dlp Automatic Background Updates

In addition to app updates, YouTube often changes its internal video streaming protocols. YouCut protects against broken downloads with automatic yt-dlp updates:
- Runs in the background every 30 minutes (respects a 1-hour cooldown).
- Installs the updated binary into `%USERPROFILE%\.youcut\bin\yt-dlp.exe`.
- Because user-level binaries persist across restarts, users never have to manually update yt-dlp.
- Can be toggled on/off in **Settings > Software & Component Updates**.
