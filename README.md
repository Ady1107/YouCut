# YouCut — YouTube Video/Audio Clipper & Downloader

[![Release](https://img.shields.io/badge/release-v1.0.0-E63946?style=flat-square)](https://github.com/Ady1107/YouCut/releases)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6?style=flat-square)](https://www.microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

A modern desktop application for downloading and clipping YouTube videos with precision trimming, format selection, queue management, and dark/light themes. Built with Python and PySide6.

---

## Features

- **Download & Clip**: Download full videos or precision-clipped time ranges from YouTube
- **Stream-Copy Fast Path**: Default clip downloads use keyframe-accurate stream copying without transcoding
- **Format Selection**: Choose from all available video and audio qualities (fetched in real-time via yt-dlp)
- **Audio-Only Mode**: Extract pure audio as MP3, M4A, or WAV with automatic tagging
- **Download Queue**: Queue multiple clips with per-item progress, pause/resume, retry, and cancellation
- **Download History**: Searchable SQLite database of all completed downloads with direct file opening
- **yt-dlp Auto-Update**: Background update engine with persistent self-update capability
- **Software Update Alerts**: Integrated GitHub version checking with in-app download banner
- **Modern UI & Theming**: Polished dark and light themes with custom accents and toast alerts
- **Clipboard Detection**: Auto-detects YouTube URLs from your clipboard on launch

---

## Requirements

- Windows 10 or Windows 11 (64-bit)
- Python 3.11+ (only needed when running from source; the standalone installer needs no Python)
- **ffmpeg.exe** (bundled in releases)
- **yt-dlp.exe** (bundled in releases)

---

## Running from Source

### 1. Clone the Repository

```bash
git clone https://github.com/Ady1107/YouCut.git
cd YouCut
```

### 2. Install Dependencies

```bash
pip install -r clipgrab/requirements.txt
```

### 3. Ensure Bundled Binaries are Present

Place `ffmpeg.exe` and `yt-dlp.exe` in `clipgrab/assets/`:
- `clipgrab/assets/ffmpeg.exe`
- `clipgrab/assets/yt-dlp.exe`

### 4. Run YouCut

```bash
python clipgrab/main.py
```

---

## Building Standalone `YouCut.exe`

To package YouCut into a high-performance standalone distribution folder (`--onedir`) that starts up instantly on any Windows PC without Python:

```powershell
python -m PyInstaller clipgrab/build.spec --clean --distpath dist
```

### Output:
- `dist/YouCut/` (Distribution folder containing `YouCut.exe` and `_internal/` dependencies)
- Bundles Python, PySide6, `ffmpeg.exe`, `yt-dlp.exe`, icons, and all dependencies. Instant launch with zero temp-folder extraction overhead.

---

## Building the Windows Installer (`YouCut-Setup-v1.0.0.exe`)

YouCut includes an Inno Setup 6 script (`YouCut.iss`) that creates a professional Windows installer:

```powershell
# If Inno Setup is installed per-user:
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" YouCut.iss

# Or if installed system-wide:
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" YouCut.iss
```

### Installer Highlights:
- Installs to `%LOCALAPPDATA%\Programs\YouCut` (no administrator privileges / UAC prompt required).
- Creates Desktop and Start Menu shortcuts with proper `AppUserModelID` (`shubh.youcut.clipper.app`) to ensure the taskbar and Start Menu show the crisp application icon.
- Uninstaller automatically cleans up installed files and prompts whether to delete settings and history.

---

## Project Structure

```text
YouCut/
├── YouCut.iss                       # Inno Setup Windows installer script
├── update.md                        # Step-by-step update and distribution guide
├── README.md                        # Documentation and instructions
├── CurrentState.md                  # Project architecture and development state
│
└── clipgrab/                        # Application core source package
    ├── main.py                      # Application entry point & AppUserModelID setup
    ├── version.py                   # Version constant & release check endpoint
    ├── requirements.txt             # Python dependencies
    ├── build.spec                   # PyInstaller onedir (--onedir) build specification
    │
    ├── assets/                      # External binaries & graphics
    │   ├── ffmpeg.exe               # Bundled FFmpeg binary
    │   ├── yt-dlp.exe               # Bundled yt-dlp binary
    │   └── icons/                   # Multi-resolution icons (youcut.ico, youcut.png)
    │
    ├── config/                      # User configuration
    │   └── settings.py              # AppSettings with JSON persistence & auto-migration
    │
    ├── core/                        # Core backend engines & workers
    │   ├── logger.py                # Rotating file and console logger
    │   ├── validator.py             # URL sanitization and timestamp validation
    │   ├── formats.py               # yt-dlp format stream representations
    │   ├── ffmpeg_utils.py          # Binary path resolution & integrity checks
    │   ├── downloader.py            # QProcess worker executing yt-dlp/ffmpeg
    │   ├── queue_manager.py         # Sequential queue controller
    │   └── updater.py               # App update & yt-dlp background update engine
    │
    ├── data/                        # Persistent database
    │   └── history_db.py            # SQLite wrapper for download history
    │
    └── gui/                         # PySide6 user interface
        ├── main_window.py           # Top-level QMainWindow with tab layout
        ├── theme.py                 # Dark and light QPalette + CSS styling
        └── widgets/                 # Modular GUI components
            ├── url_input.py         # URL input bar & video metadata fetcher
            ├── format_selector.py   # Quality & audio/video format selector
            ├── time_range_input.py  # Precision trimming & timeline controls
            ├── download_controls.py # Destination path & download trigger
            ├── queue_panel.py       # Download queue manager view
            ├── queue_item_widget.py # Individual queue item with progress/controls
            ├── history_panel.py     # Searchable download history list
            ├── history_item_widget.py # Individual history entry card
            ├── settings_panel.py    # Preferences, theme switch, & update banner
            └── toast_notification.py # Animated non-blocking toast popups
```

---

## User Data Locations

All user configuration, history, and cache are stored in `~/.youcut/` (automatically migrated from legacy `~/.clipgrab/` if present):

| File / Folder | Purpose |
|---------------|---------|
| `~/.youcut/config.json` | User preferences (theme, default output folder, update settings) |
| `~/.youcut/history.db` | SQLite database storing download history |
| `~/.youcut/youcut.log` | Rotating application logs (5MB × 3 backups) |
| `~/.youcut/bin/yt-dlp.exe` | Persistent self-updated yt-dlp binary |
| `~/.youcut/thumbnails/` | Cached video thumbnails |

---

## Releasing Updates

See [update.md](file:///c:/Users/adysi/OneDrive/Documents/Code/Antigravity/Youtube%20specific%20clip%20downloader/update.md) for full instructions on how to publish updates using GitHub Releases or GitHub Gists so all distributed copies of YouCut receive update alerts automatically.

---

## License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

*Disclaimer: This software is intended for personal use and downloading content you have the right to access. YouTube's Terms of Service apply to all downloaded media.*
