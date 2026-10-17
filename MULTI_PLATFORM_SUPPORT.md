# Multi-Platform Support Specification & Pull Request Guide

**Branch**: `feat/multi-platform-support`  
**Base**: `main`  
**Repository**: [Ady1107/YouCut](https://github.com/Ady1107/YouCut)  
**PR Creation URL**: [Open Pull Request](https://github.com/Ady1107/YouCut/pull/new/feat/multi-platform-support)

---

## 1. Executive Summary

This feature branch expands YouCut beyond YouTube to become a modern, multi-platform media downloader. It introduces a modular platform architecture capable of identifying, validating, extracting metadata, and downloading content from:

- **YouTube** (Videos, Shorts, Live streams, Playlists, YouTube Music)
- **Instagram** (Reels, Static Posts, IGTV, Carousels, Stories)
- **Facebook** (Watch, Reels, Page Videos, Mobile links, `fb.watch` short links)
- **X / Twitter** (`x.com`, `twitter.com`, `t.co` short links)
- **Pinterest** (Pins, `pin.it` short links, country domains)

---

## 2. Platform Support Matrix

| Platform | Engine | Supported Media Types | Notes & Specific Handling |
| :--- | :--- | :--- | :--- |
| **YouTube** | `yt-dlp` | Video, Audio, Shorts, Live | Uses `--no-playlist` so single-video queries don't expand playlists. Supports full timestamp clipping and fast stream copying. |
| **Instagram** | `yt-dlp` | Reels, Posts, IGTV, Carousels | Omit `--no-playlist` so carousel posts return their full entries list. Dedicated detection for login-gated content. |
| **Facebook** | `yt-dlp` | Watch, Reels, Videos, Mobile links | Public videos fully supported. Handles mobile (`m.facebook.com`) and modern share URLs (`/share/v/`). |
| **X / Twitter** | `yt-dlp` | Video Tweets, GIFs | Supports `x.com`, `twitter.com`, and `t.co` redirects. GIFs downloaded as loopable video files. |
| **Pinterest** | Dedicated HTTP (Phase 4) | Image Pins, Video Pins | Detected via regex. Dedicated HTTP engine architecture to safely handle Pinterest layout variations. |

---

## 3. Architecture & Key Changes

### A. New Platform Registry (`clipgrab/core/platforms.py`)
- **`PlatformConfig` Data Class**: Stores platform identifier, display name, UI icon, compiled regex patterns, supported `ContentKind` sets, engine type (`DownloadEngine`), and platform-specific extraction notes.
- **`detect_platform(url: str) -> PlatformConfig`**: Evaluates regex patterns in deterministic order (specific platforms first, broad YouTube catch last).
- **`guess_content_kind(url: str)`**: Pre-fetch heuristic classification (e.g. reels, shorts, stories) for early UI decisions.

### B. Core Downloader Optimization (`clipgrab/core/downloader.py`)
- Added `fetch_platform_id` to `DownloadWorker`.
- **Dynamic `--no-playlist`**: For YouTube, `--no-playlist` is retained to prevent unintentional playlist expansion. For Instagram and other platforms, `--no-playlist` is omitted to enable multi-item carousel post extraction.
- **Login Error Classification**: Detects authentication barriers (`login required`, `private video`, `authentication required`) and cleanly presents helpful messaging rather than raw tracebacks.

### C. Queue & Data Layer Integration (`clipgrab/core/queue_manager.py`)
- Added `platform_id: str = "youtube"` to `QueueItem` metadata.
- Persists platform provenance across queue items and history records for platform badge rendering.

### D. UI/UX Enhancements (`clipgrab/gui/widgets/url_input.py` & `main_window.py`)
- **Real-Time Platform Badge**: Displays a branded platform badge beside the URL input upon valid URL entry.
- **User-Friendly Status Feedback**: Clear checkmark with platform name on detection (`✓ Instagram URL detected`).
- **Graceful Error Surfacing**: Non-supported URLs, Pinterest Phase 4 notifications, and private/login-required accounts provide clear guidance.

---

## 4. Verification & Testing Checklist

- [x] **YouTube URL Detection**: `https://www.youtube.com/watch?v=...`, `https://youtu.be/...`, `https://youtube.com/shorts/...`
- [x] **Instagram URL Detection**: `https://www.instagram.com/reel/...`, `https://www.instagram.com/p/...`
- [x] **Facebook URL Detection**: `https://www.facebook.com/watch/...`, `https://fb.watch/...`
- [x] **X/Twitter URL Detection**: `https://x.com/.../status/...`, `https://twitter.com/...`
- [x] **Pinterest URL Detection**: `https://www.pinterest.com/pin/...`, `https://pin.it/...`
- [x] **Invalid/Unsupported URLs**: Returns clear message listing supported platforms.
- [x] **Build & Packaging Integrity**: No new external runtime dependencies; fully compatible with bundled `yt-dlp` and `ffmpeg`.

---

## 5. Pull Request Summary Template

```markdown
### Description
Adds multi-platform URL detection and download support to YouCut.

### Platforms Supported
- YouTube (video, audio, shorts, live)
- Instagram (reels, posts, IGTV, carousels)
- Facebook (watch, reels, videos, share links)
- X / Twitter (x.com, twitter.com video & GIF tweets)
- Pinterest (URL detection and engine routing ready for Phase 4)

### Changes
- Created `clipgrab/core/platforms.py` with registry and regex detection
- Updated `clipgrab/core/downloader.py` with platform-aware `--no-playlist` handling
- Updated `clipgrab/core/queue_manager.py` with `platform_id` metadata
- Updated `clipgrab/gui/widgets/url_input.py` with platform badge and smart error feedback
- Connected detected platform through `clipgrab/gui/main_window.py`
```
