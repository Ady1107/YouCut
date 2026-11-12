"""
YouCut — Main application window.

Restructured with modern collapsible sidebar, Universal Home page (auto-routing),
one configurable SectionDownloadPage driven by SectionConfig, Queue panel,
History panel, and Settings panel.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

import requests
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from clipgrab.config.settings import AppSettings
from clipgrab.core.downloader import DownloadRequest
from clipgrab.core.formats import VideoInfo
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import detect_platform, is_supported
from clipgrab.core.queue_manager import QueueItem, QueueItemStatus, QueueManager
from clipgrab.core.updater import UpdateManager
from clipgrab.core.validator import sanitize_filename, suggest_filename
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.sections import (
    ALL_SECTIONS,
    SECTION_PINTEREST_VIDEO,
    SECTION_YOUTUBE_VIDEO,
    SectionConfig,
    get_section_by_id,
    route_url_to_section,
)
from clipgrab.gui.theme import toggle_theme
from clipgrab.gui.widgets.history_panel import HistoryPanel
from clipgrab.gui.widgets.home_page import HomePage
from clipgrab.gui.widgets.queue_panel import QueuePanel
from clipgrab.gui.widgets.section_download_page import SectionDownloadPage
from clipgrab.gui.widgets.settings_panel import SettingsPanel
from clipgrab.gui.widgets.sidebar import Sidebar
from clipgrab.gui.widgets.toast_notification import ToastNotification, show_toast

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    """Main application window with modern sidebar and unified stack."""

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
        self._setup_shortcuts()
        self._restore_geometry()
        self._start_background_services()

    def _setup_window(self) -> None:
        """Configure main window title, minimum size, and icon."""
        self.setWindowTitle("YouCut — Universal Video & Media Clipper")
        self.setMinimumSize(960, 640)
        app = QApplication.instance()
        if app and not app.windowIcon().isNull():
            self.setWindowIcon(app.windowIcon())

    def _setup_ui(self) -> None:
        """Build the main UI with Sidebar and centered content area."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ─── 1. Left Sidebar ──────────────────────────────────────────────────
        self._sidebar = Sidebar(
            collapsed=getattr(self._settings, "sidebar_collapsed", False),
            expanded_groups=getattr(self._settings, "expanded_groups", None),
            last_section=getattr(self._settings, "last_section", "home"),
        )
        main_layout.addWidget(self._sidebar)

        # Subtle vertical separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        sep.setFixedWidth(1)
        main_layout.addWidget(sep)

        # ─── 2. Content Stack Area ────────────────────────────────────────────
        self._stack = QStackedWidget()

        # Page 0: Home Page
        self._home_page = HomePage(self._history_db)
        self._stack.addWidget(self._home_page)

        # Page 1: Configurable Shared Section Download Page
        self._section_page = SectionDownloadPage(self._settings)
        self._stack.addWidget(self._section_page)

        # Page 2: Queue Panel
        self._queue_panel = QueuePanel(self._queue_manager)
        self._stack.addWidget(self._queue_panel)

        # Page 3: History Panel
        self._history_panel = HistoryPanel(self._history_db)
        self._stack.addWidget(self._history_panel)

        # Page 4: Settings Panel
        self._settings_panel = SettingsPanel(self._settings, self._update_manager)
        self._stack.addWidget(self._settings_panel)

        main_layout.addWidget(self._stack, stretch=1)

        # ─── 3. Status Bar ────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setFont(QFont("Segoe UI", 9))
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # Apply initial theme to custom widgets
        self._sidebar.update_theme(self._settings.theme)
        self._home_page.update_theme(self._settings.theme)
        self._section_page.update_theme(self._settings.theme)

        # Select Home on startup
        self._sidebar.select_target("home", "home")
        self._stack.setCurrentIndex(0)

    def _setup_shortcuts(self) -> None:
        """Register application keyboard shortcuts."""
        # Ctrl+0: Home
        QShortcut(QKeySequence("Ctrl+0"), self, lambda: self._sidebar.select_target("home", "home"))
        # Ctrl+1..5: Platform groups
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self._sidebar.select_target("section", "youtube_video"))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self._sidebar.select_target("section", "instagram_reel"))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self._sidebar.select_target("section", "facebook_video"))
        QShortcut(QKeySequence("Ctrl+4"), self, lambda: self._sidebar.select_target("section", "pinterest_image"))
        QShortcut(QKeySequence("Ctrl+5"), self, lambda: self._sidebar.select_target("section", "x_video"))
        # Ctrl+B: Toggle rail
        QShortcut(QKeySequence("Ctrl+B"), self, self._sidebar.toggle_rail)

    def _connect_signals(self) -> None:
        """Wire up all inter-component signals."""
        # Sidebar navigation
        self._sidebar.navigation_requested.connect(self._on_sidebar_navigation)
        self._sidebar.rail_toggled.connect(self._on_rail_toggled)
        self._sidebar.group_toggled.connect(self._on_group_toggled)

        # Home page routing
        self._home_page.route_requested.connect(self._on_home_route_requested)
        self._home_page.status_message.connect(self._status_bar.showMessage)

        # Section page routing & controls
        self._section_page.switch_section_requested.connect(self._on_switch_section_requested)
        self._section_page.status_message.connect(self._status_bar.showMessage)

        # URL fetch signals from section page
        self._section_page.url_input.info_fetched.connect(self._on_info_fetched)
        self._section_page.url_input.fetch_failed.connect(self._on_fetch_failed)

        # Download controls in section page
        self._section_page.download_controls.add_to_queue_clicked.connect(self._on_add_to_queue)
        self._section_page.download_controls.download_now_clicked.connect(self._on_download_now)
        self._section_page.download_controls.output_folder_changed.connect(self._on_output_folder_changed)

        # Queue manager events
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
        self._queue_panel.queue_action.connect(lambda _: self._update_queue_badge())

    def _start_background_services(self) -> None:
        """Start background services (update checker, etc.) with tracked timers."""
        self._update_manager.auto_update = self._settings.auto_update_ytdlp
        self._update_manager.app_update_url = getattr(self._settings, "app_update_url", "")
        self._update_manager.set_last_check_from_config(self._settings.last_update_check)

        self._startup_update_timer = QTimer(self)
        self._startup_update_timer.setSingleShot(True)
        self._startup_update_timer.setInterval(2000)
        self._startup_update_timer.timeout.connect(self._update_manager.startup_check)
        self._startup_update_timer.start()

        self._startup_version_timer = QTimer(self)
        self._startup_version_timer.setSingleShot(True)
        self._startup_version_timer.setInterval(1000)
        self._startup_version_timer.timeout.connect(self._settings_panel.refresh_versions)
        self._startup_version_timer.start()

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
        """Clean shutdown with zero orphan processes."""
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

        if self._startup_update_timer and self._startup_update_timer.isActive():
            self._startup_update_timer.stop()
        if self._startup_version_timer and self._startup_version_timer.isActive():
            self._startup_version_timer.stop()
        if self._clipboard_timer and self._clipboard_timer.isActive():
            self._clipboard_timer.stop()

        try:
            self._update_manager.shutdown()
        except Exception as e:
            logger.error("Error shutting down UpdateManager: %s", e)

        try:
            self._section_page.url_input.shutdown()
        except Exception as e:
            logger.error("Error shutting down UrlInputWidget: %s", e)

        try:
            self._queue_manager.shutdown()
        except Exception as e:
            logger.error("Error shutting down QueueManager: %s", e)

        active_thumbs = sum(1 for t in self._thumbnail_threads if t.is_alive())
        if active_thumbs > 0:
            logger.info("Shutdown: %d background thumbnail thread(s) exiting", active_thumbs)

        try:
            ToastNotification.dismiss_all()
        except Exception as e:
            logger.warning("Error dismissing toasts: %s", e)

        try:
            self._settings_panel.shutdown()
        except Exception as e:
            logger.warning("Error shutting down SettingsPanel: %s", e)

        try:
            geo = self.geometry()
            self._settings.update(
                window_x=geo.x(),
                window_y=geo.y(),
                window_width=geo.width(),
                window_height=geo.height(),
                last_update_check=self._update_manager.get_last_check_iso(),
                sidebar_collapsed=self._sidebar.is_collapsed(),
                expanded_groups=self._sidebar.get_expanded_groups(),
                last_section=self._sidebar.get_active_target(),
            )
        except Exception as e:
            logger.error("Error saving settings on close: %s", e)

        logger.info("Shutdown complete. Application exiting.")
        event.accept()
        super().closeEvent(event)

    # ─── Navigation Handlers ──────────────────────────────────────────────────

    def _on_sidebar_navigation(self, page_type: str, target_id: str) -> None:
        """Handle navigation requests from the sidebar."""
        self._update_manager.reset_idle_timer()

        if page_type == "home":
            self._stack.setCurrentIndex(0)
            self._home_page.refresh_recent_downloads()
            self._status_bar.showMessage("Home — Paste any link")
        elif page_type == "section":
            config = get_section_by_id(target_id)
            self._section_page.configure_section(config)
            self._stack.setCurrentIndex(1)
            self._status_bar.showMessage(f"Section: {config.title}")
        elif page_type == "queue":
            self._stack.setCurrentIndex(2)
            self._status_bar.showMessage("Download Queue")
        elif page_type == "history":
            self._history_panel.refresh()
            self._stack.setCurrentIndex(3)
            self._status_bar.showMessage("Download History")
        elif page_type == "settings":
            self._stack.setCurrentIndex(4)
            self._status_bar.showMessage("Settings")

    def _on_home_route_requested(self, section_id: str, url: str, auto_fetch: bool) -> None:
        """Route from Universal Home page to Section page with prefilled URL."""
        config = get_section_by_id(section_id)
        self._sidebar.select_target("section", section_id)
        self._section_page.configure_section(config, carry_url=url, auto_fetch=auto_fetch)
        self._stack.setCurrentIndex(1)
        if url:
            self._status_bar.showMessage(f"Opening {config.title} with URL...")

    def _on_switch_section_requested(self, target_section_id: str, url: str, auto_fetch: bool) -> None:
        """Switch section from wrong-section banner or reroute dropdown."""
        config = get_section_by_id(target_section_id)
        self._sidebar.select_target("section", target_section_id)
        self._section_page.configure_section(config, carry_url=url, auto_fetch=auto_fetch)
        self._stack.setCurrentIndex(1)

    def _on_rail_toggled(self, collapsed: bool) -> None:
        self._settings.sidebar_collapsed = collapsed

    def _on_group_toggled(self, group_id: str, is_expanded: bool) -> None:
        self._settings.expanded_groups = self._sidebar.get_expanded_groups()

    # ─── Download & Fetch Pipeline Handlers ────────────────────────────────────

    def _on_info_fetched(self, video_info: VideoInfo) -> None:
        """Handle successful media info fetch in the Section page."""
        self._current_video_info = video_info

        # Auto-switch Pinterest Image -> Pinterest Video if video formats detected
        current_cfg = self._section_page.get_current_config()
        if (
            current_cfg.id == "pinterest_image"
            and (video_info.video_formats or video_info.duration > 0)
        ):
            logger.info("Pinterest pin contains video, auto-switching to Pinterest Video")
            self._sidebar.select_target("section", SECTION_PINTEREST_VIDEO.id)
            self._section_page.configure_section(SECTION_PINTEREST_VIDEO)
            show_toast(self, "Detected Video Pin — switched to Video Download", toast_type="info")

        # Populate formats
        self._section_page.format_selector.populate_formats(video_info)

        # Populate time range
        self._section_page.time_range.set_duration(video_info.duration)

        # Suggest filename
        suggested = suggest_filename(video_info.title)
        self._section_page.download_controls.set_filename(suggested)

        # Enable action buttons
        self._section_page.download_controls.set_actions_enabled(True)

        self._status_bar.showMessage(
            f"Fetched: {video_info.title} ({len(video_info.video_formats)} video, "
            f"{len(video_info.audio_formats)} audio formats)"
        )

        if video_info.thumbnail_url:
            self._cache_thumbnail(video_info)

    def _on_fetch_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        """Handle failed info fetch."""
        self._current_video_info = None
        self._section_page.format_selector.clear_formats()
        self._section_page.time_range.reset()
        self._section_page.download_controls.set_actions_enabled(False)
        self._status_bar.showMessage("Fetch failed")

        if is_extraction_error:
            self._update_manager.on_extraction_error()
            show_toast(
                self,
                "Extraction error — checking for yt-dlp update...",
                toast_type="warning",
            )

    def _on_add_to_queue(self) -> None:
        """Add current configuration to queue."""
        item = self._build_queue_item()
        if item is None:
            return

        self._queue_manager.add_item(item)
        self._update_queue_badge()
        self._status_bar.showMessage(f"Added to queue: {item.title}")
        show_toast(self, f"Added to queue: {item.title}", toast_type="success")

        # Switch to Queue
        self._sidebar.select_target("queue", "queue")

    def _on_download_now(self) -> None:
        """Add to queue and start processing immediately."""
        item = self._build_queue_item()
        if item is None:
            return

        self._queue_manager.add_item(item)
        self._update_queue_badge()

        if not self._queue_manager.is_processing:
            self._queue_manager.start_processing()

        self._status_bar.showMessage(f"Downloading: {item.title}")
        self._sidebar.select_target("queue", "queue")

    def _build_queue_item(self) -> Optional[QueueItem]:
        """Build QueueItem from section page state."""
        if self._current_video_info is None:
            show_toast(self, "Please fetch media info first", toast_type="warning")
            return None

        cfg = self._section_page.get_current_config()

        # Validate time range if clipping panel applies
        if cfg.show_clip:
            is_valid, msg = self._section_page.time_range.validate()
            if not is_valid:
                show_toast(self, msg, toast_type="error")
                return None
            start_seconds = self._section_page.time_range.get_start_seconds()
            end_seconds = self._section_page.time_range.get_end_seconds()
            start_time = self._section_page.time_range.get_start_time()
            end_time = self._section_page.time_range.get_end_time()
            frame_accurate = self._section_page.time_range.is_frame_accurate
        else:
            start_seconds = None
            end_seconds = None
            start_time = None
            end_time = None
            frame_accurate = False

        filename = self._section_page.download_controls.get_filename()
        if not filename:
            filename = suggest_filename(self._current_video_info.title)

        video_format = self._section_page.format_selector.get_selected_video_format()
        audio_format = self._section_page.format_selector.get_selected_audio_format()
        audio_only = self._section_page.format_selector.is_audio_only or cfg.audio_only
        audio_output_fmt = self._section_page.format_selector.get_audio_output_format()

        if start_time and end_time and cfg.show_clip and not self._section_page.time_range.is_full_video:
            filename = suggest_filename(
                self._current_video_info.title, start_time, end_time
            )

        output_dir = self._section_page.download_controls.get_output_folder()
        merge_fmt = getattr(self._settings, "default_merge_format", "mp4") or "mp4"

        request = DownloadRequest(
            url=self._section_page.url_input.get_url(),
            output_dir=output_dir,
            filename=sanitize_filename(filename),
            video_format=video_format,
            audio_format=audio_format,
            audio_only=audio_only,
            audio_output_format=audio_output_fmt,
            start_time=start_seconds,
            end_time=end_seconds,
            merge_format=merge_fmt if not audio_only else (audio_output_fmt.value if audio_output_fmt else "mp3"),
            frame_accurate_clipping=frame_accurate,
            convert_to_gif=self._section_page.format_selector.is_gif_selected() or cfg.id == "x_gif",
            concurrent_fragments=getattr(self._settings, "concurrent_fragments", 16),
        )

        item = QueueItem(
            url=self._section_page.url_input.get_url(),
            title=self._current_video_info.title,
            thumbnail_url=self._current_video_info.thumbnail_url,
            start_time=start_time,
            end_time=end_time,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            duration=self._current_video_info.duration,
            video_format_label=self._section_page.format_selector.get_selected_video_label(),
            audio_format_label=self._section_page.format_selector.get_selected_audio_label(),
            download_request=request,
            platform_id=cfg.platform_id,
        )

        thumb_path = self._get_thumbnail_cache_path(self._current_video_info)
        if thumb_path and os.path.isfile(thumb_path):
            item.thumbnail_path = thumb_path

        return item

    def _on_queue_item_updated(self, index: int) -> None:
        """Handle queue item update and history recording."""
        self._update_queue_badge()
        item = self._queue_manager.get_item(index)
        if item is None:
            return

        if item.status == QueueItemStatus.WARNING:
            # Don't add to History as a success!
            show_toast(
                self,
                f"Finished with warning: {item.warning_reason}",
                toast_type="warning",
                duration_ms=6000,
            )
        elif item.status == QueueItemStatus.DONE:
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
            self._home_page.refresh_recent_downloads()
        elif item.status == QueueItemStatus.FAILED:
            show_toast(
                self,
                f"Download failed: {item.error_msg[:100]}",
                toast_type="error",
                duration_ms=8000,
            )

    def _on_queue_finished(self) -> None:
        self._update_queue_badge()
        self._status_bar.showMessage("Queue finished")
        show_toast(self, "All downloads complete!", toast_type="success")

    def _on_download_ended(self) -> None:
        self._update_queue_badge()
        if not self._queue_manager.is_busy:
            self._update_manager.set_queue_busy(False)

    def _update_queue_badge(self) -> None:
        """Update the sidebar badge with active + pending count."""
        items = self._queue_manager.get_items()
        count = sum(
            1 for it in items
            if it.status in (QueueItemStatus.DOWNLOADING, QueueItemStatus.PENDING)
        )
        self._sidebar.set_queue_count(count)

    def _on_output_folder_changed(self, folder: str) -> None:
        self._settings.update(last_output_folder=folder)
        self._section_page.save_section_preference("output_folder", folder)

    def _on_theme_toggle(self) -> None:
        app = QApplication.instance()
        if app:
            new_theme = toggle_theme(app, self._settings)
            self._settings_panel.update_theme_button(new_theme)
            self._sidebar.update_theme(new_theme)
            self._home_page.update_theme(new_theme)
            self._section_page.update_theme(new_theme)

    def _on_settings_changed(self) -> None:
        self._section_page.download_controls.set_output_folder(self._settings.last_output_folder)

    def _on_ytdlp_updated(self, new_version: str) -> None:
        self._settings.update(last_ytdlp_version=new_version)
        self._settings_panel.refresh_versions()

    def _on_redownload_requested(self, entry: HistoryEntry) -> None:
        target_sec, _ = route_url_to_section(entry.url)
        self._sidebar.select_target("section", target_sec.id)
        self._section_page.configure_section(target_sec, carry_url=entry.url, auto_fetch=True)
        self._stack.setCurrentIndex(1)
        self._status_bar.showMessage(f"Re-downloading: {entry.title}")

    # ─── Thumbnail & Clipboard Helpers ────────────────────────────────────────

    def _cache_thumbnail(self, video_info: VideoInfo) -> None:
        """Download and cache the video thumbnail."""
        if not video_info.thumbnail_url:
            return

        cache_path = self._get_thumbnail_cache_path(video_info)
        if cache_path and os.path.isfile(cache_path):
            pix = QPixmap(cache_path)
            self._section_page.set_thumbnail_preview(pix)
            return

        def _download():
            try:
                resp = requests.get(video_info.thumbnail_url, timeout=10)
                resp.raise_for_status()
                if cache_path:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    pix = QPixmap(cache_path)
                    QTimer.singleShot(0, lambda: self._section_page.set_thumbnail_preview(pix))
            except Exception as e:
                logger.debug("Thumbnail cache failed: %s", e)

        import threading
        self._thumbnail_threads = [t for t in self._thumbnail_threads if t.is_alive()]
        thread = threading.Thread(target=_download, daemon=True)
        self._thumbnail_threads.append(thread)
        thread.start()

    def _get_thumbnail_cache_path(self, video_info: VideoInfo) -> Optional[str]:
        if not video_info.url:
            return None
        url_hash = hashlib.md5(video_info.url.encode()).hexdigest()[:12]
        cache_dir = self._settings.thumbnail_cache_dir
        if not cache_dir:
            return None
        return os.path.join(cache_dir, f"{url_hash}.jpg")

    def check_clipboard_for_url(self) -> None:
        """Check clipboard on launch and pre-fill Home box if link detected."""
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return

        text = clipboard.text().strip()
        if not text:
            return

        if is_supported(text):
            self._home_page.set_url(text)
            show_toast(
                self,
                "Media link detected in clipboard — ready on Home",
                toast_type="info",
                duration_ms=3000,
            )
            logger.info("Clipboard auto-detected: %s", text[:60])
