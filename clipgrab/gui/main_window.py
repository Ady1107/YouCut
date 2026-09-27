"""
YouCut — Main application window.

Assembles all widgets into a tabbed layout and wires up signal/slot connections
between the URL input, format selector, time range, download controls,
queue panel, history panel, settings panel, and the core backend.
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

import requests
from PySide6.QtCore import Qt, QTimer  # type: ignore
from PySide6.QtGui import QFont  # type: ignore
from PySide6.QtWidgets import (  # type: ignore
    QApplication,
    QFrame,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from clipgrab.config.settings import AppSettings
from clipgrab.core.downloader import DownloadRequest
from clipgrab.core.formats import VideoInfo
from clipgrab.core.logger import get_logger
from clipgrab.core.queue_manager import QueueItem, QueueManager
from clipgrab.core.updater import UpdateManager
from clipgrab.core.validator import sanitize_filename, suggest_filename
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.theme import toggle_theme
from clipgrab.gui.widgets.download_controls import DownloadControlsWidget
from clipgrab.gui.widgets.format_selector import FormatSelectorWidget
from clipgrab.gui.widgets.history_panel import HistoryPanel
from clipgrab.gui.widgets.queue_panel import QueuePanel
from clipgrab.gui.widgets.settings_panel import SettingsPanel
from clipgrab.gui.widgets.time_range_input import TimeRangeWidget
from clipgrab.gui.widgets.toast_notification import ToastNotification, show_toast
from clipgrab.gui.widgets.url_input import UrlInputWidget

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    """Main application window with tabbed layout."""

    def __init__(
        self,
        settings: AppSettings,
        history_db: HistoryDB,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._history_db = history_db
        self._current_video_info: Optional[VideoInfo] = None
        self._thumbnail_threads: list[Any] = []
        self._startup_update_timer: Optional[QTimer] = None
        self._startup_version_timer: Optional[QTimer] = None
        self._clipboard_timer: Optional[QTimer] = None

        # Core managers
        self._queue_manager = QueueManager(self)
        self._update_manager = UpdateManager(self)

        self._setup_window()
        self._setup_ui()
        self._connect_signals()
        self._restore_geometry()
        self._start_background_services()

    def _setup_window(self) -> None:
        """Configure the main window properties."""
        self.setWindowTitle("YouCut — YouTube Video Clipper")
        self.setMinimumSize(850, 580)
        app = QApplication.instance()
        if app and not app.windowIcon().isNull():
            self.setWindowIcon(app.windowIcon())

    def _setup_ui(self) -> None:
        """Build the main UI with tabs."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(14, 14, 14, 8)

        # Tab widget
        self._tabs = QTabWidget()
        self._tabs.setFont(QFont("Segoe UI", 10))
        self._tabs.setDocumentMode(True)

        # ── Download Tab ──────────────────────────────────────────────────
        download_tab = QWidget()
        download_layout = QVBoxLayout(download_tab)
        download_layout.setSpacing(10)
        download_layout.setContentsMargins(4, 6, 4, 4)

        # URL Input
        self._url_input = UrlInputWidget()
        download_layout.addWidget(self._url_input)

        # Format Selector
        self._format_selector = FormatSelectorWidget()
        download_layout.addWidget(self._format_selector)

        # Time Range
        self._time_range = TimeRangeWidget()
        download_layout.addWidget(self._time_range)

        # Download Controls
        self._download_controls = DownloadControlsWidget(
            default_output_folder=self._settings.last_output_folder
        )
        download_layout.addWidget(self._download_controls)

        download_layout.addStretch()

        # Wrap in borderless scroll area for guaranteed responsiveness at any screen resolution
        download_scroll = QScrollArea()
        download_scroll.setWidgetResizable(True)
        download_scroll.setFrameShape(QFrame.Shape.NoFrame)
        download_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        download_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        download_scroll.setWidget(download_tab)
        self._tabs.addTab(download_scroll, "⬇️ Download")

        # ── Queue Tab ─────────────────────────────────────────────────────
        self._queue_panel = QueuePanel(self._queue_manager)
        self._tabs.addTab(self._queue_panel, "📋 Queue")

        # ── History Tab ───────────────────────────────────────────────────
        self._history_panel = HistoryPanel(self._history_db)
        self._tabs.addTab(self._history_panel, "📜 History")

        # ── Settings Tab ─────────────────────────────────────────────────
        self._settings_panel = SettingsPanel(self._settings, self._update_manager)
        self._tabs.addTab(self._settings_panel, "⚙️ Settings")

        main_layout.addWidget(self._tabs)

        # Status bar
        self._status_bar = QStatusBar()
        self._status_bar.setFont(QFont("Segoe UI", 9))
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

    def _connect_signals(self) -> None:
        """Wire up all signal/slot connections."""
        # URL Input -> format/time population
        self._url_input.info_fetched.connect(self._on_info_fetched)
        self._url_input.fetch_failed.connect(self._on_fetch_failed)

        # Download controls
        self._download_controls.add_to_queue_clicked.connect(self._on_add_to_queue)
        self._download_controls.download_now_clicked.connect(self._on_download_now)
        self._download_controls.output_folder_changed.connect(self._on_output_folder_changed)

        # Queue manager
        self._queue_manager.item_updated.connect(self._on_queue_item_updated)
        self._queue_manager.queue_finished.connect(self._on_queue_finished)
        self._queue_manager.download_started.connect(
            lambda: self._update_manager.set_queue_busy(True)
        )
        self._queue_manager.download_ended.connect(self._on_download_ended)

        # Update manager
        self._update_manager.toast_message.connect(
            lambda msg: show_toast(self, msg, toast_type="info")
        )
        self._update_manager.update_installed.connect(self._on_ytdlp_updated)
        self._update_manager.app_update_available.connect(
            self._settings_panel.show_app_update_banner
        )

        # Settings panel
        self._settings_panel.theme_toggle_requested.connect(self._on_theme_toggle)
        self._settings_panel.settings_changed.connect(self._on_settings_changed)

        # History re-download
        self._history_panel.redownload_requested.connect(self._on_redownload_requested)
        self._history_panel.status_message.connect(self._status_bar.showMessage)

        # Queue panel status
        self._queue_panel.queue_action.connect(self._status_bar.showMessage)

        # Tab change: refresh history when switching to history tab
        self._tabs.currentChanged.connect(self._on_tab_changed)

    def _start_background_services(self) -> None:
        """Start background services (update checker, etc.) with tracked timers."""
        # Configure update manager from settings
        self._update_manager.auto_update = self._settings.auto_update_ytdlp
        self._update_manager.app_update_url = getattr(self._settings, "app_update_url", "")
        self._update_manager.set_last_check_from_config(
            self._settings.last_update_check
        )

        # Startup update check (deferred to not block window show)
        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.setInterval(2000)
        self._startup_update_timer.timeout.connect(self._update_manager.startup_check)
        self._startup_update_timer.start()

        # Refresh version info in settings panel
        self._startup_version_timer = QTimer(self)
        self._startup_version_timer.setSingleShot(True)
        self._startup_version_timer.setInterval(1000)
        self._startup_version_timer.timeout.connect(self._settings_panel.refresh_versions)
        self._startup_version_timer.start()

        # Check clipboard after window is shown
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(500)
        self._clipboard_timer.timeout.connect(self.check_clipboard_for_url)
        self._clipboard_timer.start()

    def _restore_geometry(self) -> None:
        """Restore window position and size from settings."""
        self.setGeometry(
            self._settings.window_x,
            self._settings.window_y,
            self._settings.window_width,
            self._settings.window_height,
        )

    def closeEvent(self, event) -> None:
        """
        Handle window close with a 100% guarantee of zero background leaks.

        Orchestrates:
          1. Active download check: If downloading, prompt confirmation dialog.
             If user rejects, close is aborted.
          2. Stopping all startup/idle timers.
          3. Shutting down update manager (stops 30-min idle QTimer, terminates
             any active update check/update subprocess tree, joins threads).
          4. Shutting down URL fetch worker & thread.
          5. Shutting down queue manager (kills yt-dlp & ffmpeg process trees
             using taskkill /F /T, cleans partial files, joins worker threads).
          6. Checking and reporting background daemon threads.
          7. Dismissing all active toast notifications and UI timers.
          8. Persisting window geometry and settings.
        """
        # 1. Check for active downloads
        if self._queue_manager.has_active_download:
            reply = QMessageBox.question(
                self,
                "Download in Progress",
                "A download is currently in progress.\n\n"
                "Do you want to cancel the download and exit YouCut?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                logger.info("Shutdown aborted by user (download in progress)")
                event.ignore()
                return

        logger.info("Shutdown: Starting clean shutdown sequence...")

        # 2. Stop startup single-shot timers if still pending
        if self._startup_update_timer and self._startup_update_timer.isActive():
            logger.info("Shutdown: Stopping pending startup update timer")
            self._startup_update_timer.stop()
        if self._startup_version_timer and self._startup_version_timer.isActive():
            logger.info("Shutdown: Stopping pending startup version timer")
            self._startup_version_timer.stop()
        if self._clipboard_timer and self._clipboard_timer.isActive():
            logger.info("Shutdown: Stopping clipboard timer")
            self._clipboard_timer.stop()

        # 3. Shutdown update manager (stops idle timer, aborts checks/updates)
        logger.info("Shutdown: Stopping update timer and update threads...")
        try:
            self._update_manager.shutdown()
        except Exception as e:
            logger.error("Error shutting down UpdateManager: %s", e)

        # 4. Shutdown URL input widget (cancels any in-flight info fetch and joins thread)
        logger.info("Shutdown: Stopping URL fetch worker and thread...")
        try:
            self._url_input.shutdown()
        except Exception as e:
            logger.error("Error shutting down UrlInputWidget: %s", e)

        # 5. Shutdown QueueManager (kills yt-dlp/ffmpeg process trees, cancels pending, joins threads)
        logger.info("Shutdown: Terminating active download process tree and workers...")
        try:
            self._queue_manager.shutdown()
        except Exception as e:
            logger.error("Error shutting down QueueManager: %s", e)

        # 6. Check thumbnail threads
        active_thumbs = sum(1 for t in self._thumbnail_threads if t.is_alive())
        if active_thumbs > 0:
            logger.info("Shutdown: %d background thumbnail daemon thread(s) will terminate on process exit", active_thumbs)

        # 7. Dismiss any active toasts and stop settings timers
        logger.info("Shutdown: Dismissing active toasts and stopping UI timers...")
        try:
            ToastNotification.dismiss_all()
        except Exception as e:
            logger.warning("Error dismissing toasts: %s", e)

        try:
            self._settings_panel.shutdown()
        except Exception as e:
            logger.warning("Error shutting down SettingsPanel: %s", e)

        # 8. Save window geometry and settings
        logger.info("Shutdown: Saving window geometry and settings...")
        try:
            geo = self.geometry()
            self._settings.update(
                window_x=geo.x(),
                window_y=geo.y(),
                window_width=geo.width(),
                window_height=geo.height(),
                last_update_check=self._update_manager.get_last_check_iso(),
            )
        except Exception as e:
            logger.error("Error saving settings on close: %s", e)

        logger.info("Shutdown: All background services stopped and threads joined.")
        logger.info("Shutdown complete. Application exiting.")

        event.accept()
        super().closeEvent(event)

    # ── Signal Handlers ───────────────────────────────────────────────────

    def _on_info_fetched(self, video_info: VideoInfo) -> None:
        """Handle successful video info fetch."""
        self._current_video_info = video_info

        # Populate format selector
        self._format_selector.populate_formats(video_info)

        # Set time range
        self._time_range.set_duration(video_info.duration)

        # Suggest filename
        suggested = suggest_filename(video_info.title)
        self._download_controls.set_filename(suggested)

        # Enable action buttons
        self._download_controls.set_actions_enabled(True)

        self._status_bar.showMessage(
            f"Fetched: {video_info.title} ({len(video_info.video_formats)} video, "
            f"{len(video_info.audio_formats)} audio formats)"
        )

        # Cache thumbnail in background
        if video_info.thumbnail_url:
            self._cache_thumbnail(video_info)

    def _on_fetch_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        """Handle failed video info fetch."""
        self._current_video_info = None
        self._format_selector.clear_formats()
        self._time_range.reset()
        self._download_controls.set_actions_enabled(False)

        self._status_bar.showMessage("Fetch failed")

        if is_extraction_error:
            self._update_manager.on_extraction_error()
            show_toast(
                self,
                "Extraction error — checking for yt-dlp update...",
                toast_type="warning",
            )

    def _on_add_to_queue(self) -> None:
        """Add the current download configuration to the queue."""
        item = self._build_queue_item()
        if item is None:
            return

        self._queue_manager.add_item(item)
        self._status_bar.showMessage(f"Added to queue: {item.title}")
        show_toast(self, f"Added to queue: {item.title}", toast_type="success")

        # Switch to queue tab
        self._tabs.setCurrentIndex(1)

    def _on_download_now(self) -> None:
        """Add to queue and start processing immediately."""
        item = self._build_queue_item()
        if item is None:
            return

        self._queue_manager.add_item(item)

        if not self._queue_manager.is_processing:
            self._queue_manager.start_processing()

        self._status_bar.showMessage(f"Downloading: {item.title}")
        self._tabs.setCurrentIndex(1)

    def _build_queue_item(self) -> Optional[QueueItem]:
        """Build a QueueItem from the current UI state."""
        if self._current_video_info is None:
            show_toast(self, "Please fetch video info first", toast_type="warning")
            return None

        # Validate time range
        is_valid, msg = self._time_range.validate()
        if not is_valid:
            show_toast(self, msg, toast_type="error")
            return None

        # Build filename
        filename = self._download_controls.get_filename()
        if not filename:
            filename = suggest_filename(self._current_video_info.title)

        # Get format selections
        video_format = self._format_selector.get_selected_video_format()
        audio_format = self._format_selector.get_selected_audio_format()
        audio_only = self._format_selector.is_audio_only
        audio_output_fmt = self._format_selector.get_audio_output_format()

        # Get time range
        start_seconds = self._time_range.get_start_seconds()
        end_seconds = self._time_range.get_end_seconds()
        start_time = self._time_range.get_start_time()
        end_time = self._time_range.get_end_time()

        # Add timecodes to filename if clipping
        if start_time and end_time and not self._time_range.is_full_video:
            filename = suggest_filename(
                self._current_video_info.title, start_time, end_time
            )

        output_dir = self._download_controls.get_output_folder()

        # Build DownloadRequest
        merge_fmt = getattr(self._settings, "default_merge_format", "mp4") or "mp4"
        request = DownloadRequest(
            url=self._url_input.get_url(),
            output_dir=output_dir,
            filename=sanitize_filename(filename),
            video_format=video_format,
            audio_format=audio_format,
            audio_only=audio_only,
            audio_output_format=audio_output_fmt,
            start_time=start_seconds,
            end_time=end_seconds,
            merge_format=merge_fmt if not audio_only else (audio_output_fmt.value if audio_output_fmt else "mp3"),
            frame_accurate_clipping=self._time_range.is_frame_accurate,
            concurrent_fragments=getattr(self._settings, "concurrent_fragments", 16),
        )

        # Build QueueItem
        item = QueueItem(
            url=self._url_input.get_url(),
            title=self._current_video_info.title,
            thumbnail_url=self._current_video_info.thumbnail_url,
            start_time=start_time,
            end_time=end_time,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            duration=self._current_video_info.duration,
            video_format_label=self._format_selector.get_selected_video_label(),
            audio_format_label=self._format_selector.get_selected_audio_label(),
            download_request=request,
        )

        # Set cached thumbnail if available
        thumb_path = self._get_thumbnail_cache_path(self._current_video_info)
        if thumb_path and os.path.isfile(thumb_path):
            item.thumbnail_path = thumb_path

        return item

    def _on_queue_item_updated(self, index: int) -> None:
        """Handle queue item state change — record to history if done."""
        item = self._queue_manager.get_item(index)
        if item is None:
            return

        from clipgrab.core.queue_manager import QueueItemStatus
        if item.status == QueueItemStatus.DONE:
            # Add to history
            self._history_db.add_entry(
                title=item.title,
                url=item.url,
                output_path=item.output_path,
                start_time=item.start_time or "",
                end_time=item.end_time or "",
                video_quality=item.video_format_label,
                audio_quality=item.audio_format_label,
                file_size=item.file_size,
                thumbnail_url=item.thumbnail_url,
            )
            show_toast(self, f"Download complete: {item.title}", toast_type="success")

        elif item.status == QueueItemStatus.FAILED:
            show_toast(
                self,
                f"Download failed: {item.error_msg[:100]}",
                toast_type="error",
                duration_ms=8000,
            )

    def _on_queue_finished(self) -> None:
        """Handle all queue items processed."""
        self._status_bar.showMessage("Queue finished")
        show_toast(self, "All downloads complete!", toast_type="success")

    def _on_download_ended(self) -> None:
        """Handle a single download ending (success or failure)."""
        if not self._queue_manager.is_busy:
            self._update_manager.set_queue_busy(False)

    def _on_output_folder_changed(self, folder: str) -> None:
        """Persist the output folder choice."""
        self._settings.update(last_output_folder=folder)

    def _on_theme_toggle(self) -> None:
        """Toggle the application theme."""
        app = QApplication.instance()
        if app:
            new_theme = toggle_theme(app, self._settings)
            self._settings_panel.update_theme_button(new_theme)

    def _on_settings_changed(self) -> None:
        """Handle any settings change."""
        self._download_controls.set_output_folder(self._settings.last_output_folder)

    def _on_ytdlp_updated(self, new_version: str) -> None:
        """Handle successful yt-dlp update."""
        self._settings.update(last_ytdlp_version=new_version)
        self._settings_panel.refresh_versions()

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab switching."""
        # Refresh history when switching to history tab (index 2)
        if index == 2:
            self._history_panel.refresh()

        # Reset idle timer on any tab switch
        self._update_manager.reset_idle_timer()

    def _on_redownload_requested(self, entry: HistoryEntry) -> None:
        """Handle re-download request from history panel."""
        # Pre-fill the URL and switch to download tab
        self._url_input.set_url(entry.url)
        self._tabs.setCurrentIndex(0)
        self._status_bar.showMessage(f"Re-download: {entry.title} — click Fetch Info to proceed")

    # ── Thumbnail Caching ─────────────────────────────────────────────────

    def _cache_thumbnail(self, video_info: VideoInfo) -> None:
        """Download and cache the video thumbnail in a background thread."""
        if not video_info.thumbnail_url:
            return

        cache_path = self._get_thumbnail_cache_path(video_info)
        if cache_path and os.path.isfile(cache_path):
            return  # Already cached

        # Download in background
        def _download():
            try:
                resp = requests.get(video_info.thumbnail_url, timeout=10)
                resp.raise_for_status()
                if cache_path:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    logger.debug("Thumbnail cached: %s", cache_path)
            except Exception as e:
                logger.debug("Thumbnail cache failed: %s", e)

        import threading
        self._thumbnail_threads = [t for t in self._thumbnail_threads if t.is_alive()]
        thread = threading.Thread(target=_download, daemon=True)
        self._thumbnail_threads.append(thread)
        thread.start()

    def _get_thumbnail_cache_path(self, video_info: VideoInfo) -> Optional[str]:
        """Get the cache file path for a video's thumbnail."""
        if not video_info.url:
            return None
        # Use URL hash as filename
        url_hash = hashlib.md5(video_info.url.encode()).hexdigest()[:12]
        cache_dir = self._settings.thumbnail_cache_dir
        if not cache_dir:
            return None
        return os.path.join(cache_dir, f"{url_hash}.jpg")

    # ── Clipboard Detection ───────────────────────────────────────────────

    def check_clipboard_for_url(self) -> None:
        """Check if clipboard contains a YouTube URL and offer to prefill."""
        from clipgrab.core.validator import validate_youtube_url

        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        text = clipboard.text().strip()
        if not text:
            return

        is_valid, _ = validate_youtube_url(text)
        if is_valid:
            self._url_input.set_url(text)
            show_toast(
                self,
                "YouTube URL detected in clipboard — pasted automatically",
                toast_type="info",
                duration_ms=3000,
            )
            logger.info("Auto-pasted URL from clipboard: %s", text[:60])
