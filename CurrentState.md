# YouCut — Current Project State & Architecture

This document outlines the current state, architecture, and module structure of the YouCut project. It serves as a living reference guide for developers to understand both the high-level design and the low-level implementation details.

---

## 1. High-Level Architecture

YouCut is built using **Python 3.11+** and **PySide6** (Qt). It acts as a high-performance desktop GUI wrapper around **yt-dlp** and **FFmpeg**.

The application strictly separates the GUI layer from the core logic:
- **Core backend**: Spawns `yt-dlp.exe` and `ffmpeg.exe` as background subprocesses, tracking real-time progress via JSON streams and standard progress outputs.
- **GUI frontend**: Modular PySide6 widgets communicating with the backend via thread-safe Qt Signals. Features custom dark/light theme palettes with branded red-to-orange gradient styling.
- **Data layer**: A SQLite database (`history.db`) for tracking completed downloads, and a JSON configuration file (`config.json`) for user preferences. All stored in `~/.youcut/` with automatic migration from legacy `~/.clipgrab/`.
- **Update system**: Dual-channel update manager supporting both application version checks (via hosted `version.json` with an in-app banner) and automated background `yt-dlp` updates.

### Binary Bundling Strategy
To ensure the app works standalone on Windows without requiring users to install external tools, it bundles `yt-dlp.exe` and `ffmpeg.exe` in the `assets/` directory. When packaged with PyInstaller as a standalone executable (`YouCut.exe`), these binaries are extracted to `sys._MEIPASS` and resolved seamlessly at runtime.

---

## 2. Directory Structure

```text
YouCut/
├── YouCut.exe                       # Standalone portable executable
├── YouCut.iss                       # Inno Setup Windows installer script
├── version.json                     # Release check schema for app updates
├── update.md                        # Update publishing guide
├── README.md                        # Documentation and instructions
├── CurrentState.md                  # This architecture document
│
└── clipgrab/                        # Application package
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
    │   ├── logger.py                # Rotating file and console logger (~/.youcut/youcut.log)
    │   ├── validator.py             # URL sanitization and timestamp validation
    │   ├── formats.py               # yt-dlp format stream representations
    │   ├── ffmpeg_utils.py          # Binary path resolution & integrity checks
    │   ├── downloader.py            # QProcess worker executing yt-dlp/ffmpeg
    │   ├── queue_manager.py         # Sequential queue controller
    │   └── updater.py               # App update & yt-dlp background update engine
    │
    ├── data/                        # Persistent database
    │   └── history_db.py            # SQLite wrapper for download history (~/.youcut/history.db)
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

## 3. Subsystem Architecture

### Process Management & Concurrency
Qt requires that all GUI updates occur on the main event thread. Heavy tasks are delegated to background workers:
- **`DownloadWorker`**: Inherits from `QObject` and executes `yt-dlp.exe` via `QProcess`.
  - **Deadlock Prevention**: Process standard output and error are read through asynchronous Qt slots.
  - **Progress Parsing**: Parses JSON progress outputs from `--progress-template "%(progress)j"` for full downloads, and regex-parses native `ffmpeg` time outputs (`time=...`) for partial clip downloads.
  - **Watchdog Timer**: Monitors throughput and kills the process if stalled for >90 seconds, clearing partial stub files.
- **`QueueManager`**: Central sequential queue controller ensuring one download runs at a time to prevent network congestion or rate limiting. Automatically schedules the next item on finish/failure.
- **Process Termination & Resume**: Cleanly stops running processes and preserves `.part` files on disk, enabling seamless resume.

### Theming System
YouCut employs a custom-engineered `QPalette` design system paired with surgical QSS styles:
- **Aesthetic**: Premium dark mode by default with vibrant red-to-orange gradient accents (`#E63946` to `#F77F00`).
- **Dynamic Switcher**: Supports instant toggle between Dark and Light themes at runtime.

### Update Pipeline
1. **Application Updates**: Background `AppUpdateChecker` compares `version.json` against `APP_VERSION`. Detects higher versions and surfaces an interactive update banner in the Settings panel with a 1-click download button.
2. **Component Updates**: Autonomous background checks run every 30 minutes to update `yt-dlp.exe` into `~/.youcut/bin/` so video extraction never breaks.

---

## 4. Current Status

**All Development Milestones Complete & Verified**:
- [x] High-performance trimming and stream-copy section clipping (fast default without re-encoding)
- [x] QProcess-based DownloadWorker architecture with 90s stall watchdog
- [x] Full process tree termination (`taskkill /F /T`) for stop, cancel, and close mid-download
- [x] Download resume support with `.part` and `.ytdl` file preservation
- [x] Clean shutdown audit (`closeEvent`, timer cleanup, thread termination, zero orphan processes)
- [x] Configurable concurrent fragments setting in UI (1–32)
- [x] Queue management with pause/resume support
- [x] Multi-resolution transparent app icon (16x16 up to 256x256)
- [x] Windows Taskbar `AppUserModelID` (`shubh.youcut.clipper.app`) integration
- [x] PyInstaller `--onedir` distribution (`dist/YouCut/`) for instant startup
- [x] Inno Setup 6 installer (`dist/YouCut-Setup-v1.0.0.exe`) packaging onedir output
- [x] Complete project renaming to YouCut across all modules, documentation, and metadata
