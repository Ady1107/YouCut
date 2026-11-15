# YouCut — Current Project State & Architecture

This document outlines the current state, architecture, and module structure of the YouCut project. It serves as a living reference guide for developers to understand both the high-level design and the low-level implementation details.

---

## 1. High-Level Architecture

YouCut is built using **Python 3.11+** and **PySide6** (Qt). It acts as a high-performance desktop GUI wrapper around **yt-dlp** and **FFmpeg**.

The application strictly separates the GUI layer from the core logic:
- **Core backend**: Spawns `yt-dlp.exe` and `ffmpeg.exe` as background subprocesses, tracking real-time progress via JSON streams and standard progress outputs.
- **GUI frontend**: Modular PySide6 widgets communicating with the backend via thread-safe Qt Signals. Features custom dark/light theme palettes with branded red-to-orange gradient styling.
- **Data layer**: A SQLite database (`history.db`) for tracking completed downloads, and a JSON configuration file (`config.json`) for user preferences. All stored in `~/.youcut/` with automatic migration from legacy `~/.clipgrab/`.
- **Update system**: Dual-channel update manager supporting both application version checks (querying GitHub Releases API directly with an in-app banner) and automated background `yt-dlp` updates.

### Binary Bundling Strategy
To ensure the app works standalone on Windows without requiring users to install external tools, it bundles `yt-dlp.exe` and `ffmpeg.exe` in the `assets/` directory. When packaged with PyInstaller as a standalone executable (`YouCut.exe`), these binaries are extracted to `sys._MEIPASS` and resolved seamlessly at runtime.

---

## 2. Directory Structure

```text
YouCut/
├── YouCut.iss                       # Inno Setup Windows installer script
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
1. **Application Updates**: Background `AppUpdateChecker` queries the GitHub Releases API (`api.github.com/repos/Ady1107/YouCut/releases/latest`) and compares the latest release `tag_name` against `APP_VERSION`. Detects higher versions and surfaces an interactive update banner in the Settings panel with a 1-click installer download button.
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


---

## 5. UI Restructure & Platform Polish (Branch: feat/multi-platform-support)

### A. Universal Sidebar & Multi-Platform Navigation (Completed & Gate-Verified)
- **Sidebar Navigation**: Collapsible hierarchy for YouTube, Instagram, Facebook, Pinterest, and X/Twitter. Supports 56px icon-only rail with Segoe UI Emoji glyphs, persistent state in AppSettings, and keyboard shortcuts (Ctrl+0..Ctrl+5, Ctrl+B).
- **Universal Home Page**: Universal paste input with auto-routing on Enter/Paste, detection chips, 5 platform fallback buttons for unrecognized links, platform explore cards, and last 5 recent downloads with 1-click re-download and file opening.
- **Configurable Section Page**: Single shared download view driven by SectionConfig dynamically adapting URL labels, hints, and hiding non-applicable panels without duplicate pages.
- **Visual Polish**: Fixed 'Past' text clipping, literal ampersand in 'Quality & Format', disabled empty dropdown placeholders ('Fetch a link first'), and centered ~1100px width constraint on wide displays.
- **Gate Verification**: 12 required screenshots generated and verified. Live clipped download from YouTube (780KB) and Facebook (520KB) verified passing.

### B. Platform & Infrastructure Bugs (Completed & Verified)
1. **Diagnosis Completed (Logged at INFO Level)**:
   - **Instagram Reel Clip** (https://www.instagram.com/reel/DLmnYEKvWtS/?utm_source=ig_web_copy_link&vrfl=NTc4MTIwNjQ2YQ==, range 2.0s-7.0s):
     - Fast (stream-copy): Starts on a non-keyframe (iskey:0 type:P), causing frozen/black opening frames in players; duration delta +0.04s.
     - Exact (--force-keyframes-at-cuts): Starts on a keyframe (iskey:1 type:I), exact 5.00s duration (0.00s delta <= 0.3s tolerance requirement), perfectly synced audio/video with zero frozen lead-in.
   - **Facebook Post** (https://www.facebook.com/share/p/1GTYWmuJub/):
     - Redirects to permalink.php. yt-dlp exited with code 1 (HTTP 404: Not Found) because it is a photo/text post without a video stream.
   - **X GIF** (https://x.com/galaxyacademy32/status/2111449002311065787?s=20):
     - Silent H.264 MP4 with audio_ext: 'none', acodec=None, vcodec=None, height=None. Format parser filtered it out, leaving 0 formats. ffmpeg two-pass palette conversion generates high-quality animated GIF (969.5 KB, 216x216, 15 fps).
   - **Infrastructure**:
     - yt-dlp version check: String comparison ('2026.8.19' != '2026.08.19') triggered false update prompts. Resolved using packaging.version.parse().
     - Single-instance guard: Absence of QLockFile allowed dual instances to run simultaneously and interleave log output.
     - Logger duplicate lines: logger.propagate = False eliminates duplicate console/file logs.

2. **Code Fixes Implemented**:
   - clipgrab/gui/widgets/time_range_input.py:
     - Added 'Trim mode: Auto / Fast / Exact' dropdown (_trim_mode_combo) with live badge (_trim_mode_badge).
     - Auto resolves to Exact (--force-keyframes-at-cuts) for sources <= 600s (10 min), Fast (stream-copy) for longer. Properties trim_mode and is_frame_accurate exposed.
   - clipgrab/core/formats.py:
     - Added silent/single-stream fallback in parse_video_formats() to handle Twitter/X GIFs and audio-less media.
     - Enhanced display_label() to gracefully format resolution when height <= 0.
   - clipgrab/gui/widgets/format_selector.py:
     - Automatically hides audio dropdown and audio-only checkbox when len(audio_formats) == 0.
     - Displays output choice dropdown 'MP4 (original) / GIF' with helper is_gif_selected() and set_gif_mode().
   - clipgrab/gui/widgets/section_download_page.py:
     - Wires set_gif_mode(config.id == 'x_gif') dynamically on section navigation.
   - clipgrab/gui/widgets/url_input.py:
     - For Facebook posts without video: displays 'X This post has no video. Photo and text posts: Coming soon.'.
     - For login-gated posts: classifies kind (Private Group, Friends-only, etc.) and surfaces 'Facebook [kind]: Login required. Raw error: {raw_error}'.
   - clipgrab/core/downloader.py:
     - Fixed request_cancel() to set self._cancel_requested = True prior to process kill so .part files are preserved and status becomes STOPPED.
     - Added _last_executed_command tracking via get_last_executed_command().
     - Implemented post-download validation: checks file existence, non-empty, video stream present, and duration tolerance (1.0s Exact, 5.0s Fast). Emits download_finished_with_warning on mismatch.
     - Implemented ffmpeg two-pass GIF conversion (palettegen + paletteuse) when request.convert_to_gif is true, with size warning if > 10MB.
   - clipgrab/core/queue_manager.py & clipgrab/gui/widgets/queue_item_widget.py:
     - Added QueueItemStatus.WARNING = 'Finished with warning' with orange warning badge and warning_reason.
   - clipgrab/gui/main_window.py:
     - Skips adding warning-status items to History as success and displays a warning toast.
   - clipgrab/core/updater.py:
     - Uses packaging.version.parse() (2026.08.19 == 2026.8.19).
     - Guards perform_update() against updating when versions are equal/newer, queue is busy, or another instance is running.
   - clipgrab/core/logger.py:
     - Added logger.propagate = False to prevent duplicate console/file logs. Logs handler count before and after initialization.
   - clipgrab/main.py:
     - Added QLockFile at ~/.youcut/youcut.lock.
     - Added QLocalServer IPC (YouCut_SingleInstance_IPC): second launch sends ACTIVATE, focuses/raises primary window, and exits 0.
   - clipgrab/gui/widgets/settings_panel.py:
     - Added 'Copy Diagnostics' button that redacts query strings (?[REDACTED_QUERY]) and copies to system clipboard.
     - Note: Login-required or private content is not supported.

3. **GUI Verification Suite (Automated Real QTest)**:
   - scratch/test_full_suite.py executed and PASSED 100%:
     - [PASS] Facebook post clearly flagged: 'This post has no video. Photo and text posts: Coming soon.'
     - [PASS] X GIF: audio dropdown hidden, output format dropdown visible with GIF selected.
     - [PASS] Instagram Reel Trimming: Auto mode correctly resolved to Exact for reel.
     - [PASS] Settings > Copy Diagnostics: generated report with redactions and verified clipboard.
     - [PASS] Single-instance guard: second launch exited 0 with 'Another instance of YouCut is already running. Focused existing window.'

