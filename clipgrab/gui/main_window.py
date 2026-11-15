"""
YouCut — Main application window.

Restructured with modern fixed sidebar (Images 2-4), QStackedWidget navigation
strictly by section id (never by index), universal Home page, YouTube clipper page shell,
Instagram and Facebook section pages, Queue, History, and Settings.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

import requests
from PySide6.QtCore import QPoint, Qt, QTimer, QMetaObject, Q_ARG, Slot
from PySide6.QtGui import QFont, QKeyEvent, QKeySequence, QPixmap, QResizeEvent, QShortcut
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
    SectionConfig,
    get_section_by_id,
    route_url_to_section,
)
from clipgrab.gui.theme import (
    BG_APP,
    BORDER_DIVIDER,
    BORDER_DIVIDER_LIGHT,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    apply_theme,
    toggle_theme,
)
from clipgrab.core.thumbnail_service import ThumbnailService
from clipgrab.gui.widgets.history_panel import HistoryPanel
from clipgrab.gui.widgets.home_page import HomePage
from clipgrab.gui.widgets.queue_panel import QueuePanel
from clipgrab.gui.widgets.social_page import SocialDownloadPage
from clipgrab.gui.widgets.settings_panel import SettingsPanel
from clipgrab.gui.widgets.sidebar import Sidebar
from clipgrab.gui.widgets.toast_notification import ToastNotification, show_toast
from clipgrab.gui.widgets.youtube_page import YouTubePage

logger = get_logger("main_window")


class MainWindow(QMainWindow):
    """Main application window with modern sidebar and unified stack navigation by section id."""

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
        self._apply_theme_to_all_pages(getattr(self._settings, "theme", "dark"))
        self._start_background_services()

    def _setup_window(self) -> None:
        """Configure main window title, minimum size, and icon."""
        self.setWindowTitle("YouCut — Universal Video & Media Clipper")
        self.resize(1176, 784)
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        app = QApplication.instance()
        if app and not app.windowIcon().isNull():
            self.setWindowIcon(app.windowIcon())

        # Debounced resize timer (50ms) for responsive layout breakpoints
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(50)
        self._resize_timer.timeout.connect(self._on_debounced_resize)

    def _setup_ui(self) -> None:
        """Build the main UI with Sidebar and QStackedWidget navigation by section id."""
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ─── 1. Left Sidebar ──────────────────────────────────────────────────
        initial_section = getattr(self._settings, "last_section", "home")
        if initial_section not in ("home", "youtube", "instagram", "facebook", "queue", "history", "settings"):
            initial_section = "home"

        self._sidebar = Sidebar(last_section=initial_section)
        main_layout.addWidget(self._sidebar)

        # Subtle vertical separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet(f"background-color: {BORDER_DIVIDER};")
        sep.setFixedWidth(1)
        main_layout.addWidget(sep)
        self._sep = sep

        # ─── 2. Content Stack Area (Navigation by Section ID) ─────────────────
        self._stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}

        # Page instances
        self._home_page = HomePage(self._history_db)
        self._youtube_page = YouTubePage(self._settings)

        self._instagram_page = SocialDownloadPage("instagram", self._settings)
        self._facebook_page = SocialDownloadPage("facebook", self._settings)

        self._queue_panel = QueuePanel(self._queue_manager)
        self._history_panel = HistoryPanel(self._history_db, active_paths_provider=self._get_active_download_paths)
        self._settings_panel = SettingsPanel(self._settings, self._update_manager)

        # Register pages strictly by section id
        self._pages["home"] = self._home_page
        self._pages["youtube"] = self._youtube_page
        self._pages["instagram"] = self._instagram_page
        self._pages["facebook"] = self._facebook_page
        self._pages["queue"] = self._queue_panel
        self._pages["history"] = self._history_panel
        self._pages["settings"] = self._settings_panel

        for page in self._pages.values():
            self._stack.addWidget(page)

        main_layout.addWidget(self._stack, stretch=1)

        # ─── 3. Status Bar ────────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setFont(QFont("Segoe UI", 9))
        self._status_bar.setMinimumHeight(26)
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Ready")

        # Initial navigation to requested/saved section
        self.show_page(initial_section)

    def _setup_shortcuts(self) -> None:
        """Register application keyboard shortcuts."""
        # Ctrl+0: Home
        QShortcut(QKeySequence("Ctrl+0"), self, lambda: self.show_page("home"))
        # Ctrl+1..3: Platforms
        QShortcut(QKeySequence("Ctrl+1"), self, lambda: self.show_page("youtube"))
        QShortcut(QKeySequence("Ctrl+2"), self, lambda: self.show_page("instagram"))
        QShortcut(QKeySequence("Ctrl+3"), self, lambda: self.show_page("facebook"))

    def _connect_signals(self) -> None:
        """Wire up all inter-component signals."""
        # Sidebar navigation
        self._sidebar.navigation_requested.connect(self.show_page)

        # Home page routing
        self._home_page.route_requested.connect(self._on_home_route_requested)
        self._home_page.status_message.connect(self._status_bar.showMessage)

        # YouTube page signals
        self._youtube_page.status_message.connect(self._status_bar.showMessage)
        self._youtube_page.status_pill_update.connect(self._sidebar.set_status)
        self._youtube_page.add_to_queue_requested.connect(self._on_youtube_add_to_queue)
        self._youtube_page.download_requested.connect(self._on_youtube_download_now)
        self._youtube_page.switch_platform_requested.connect(self.show_page)
        self._youtube_page.info_fetched.connect(self._on_youtube_info_fetched)

        # Instagram & Facebook page signals
        for p_page in (self._instagram_page, self._facebook_page):
            p_page.download_requested.connect(self._on_social_download_now)
            p_page.status_message.connect(self._status_bar.showMessage)
            p_page.switch_platform_requested.connect(self.show_page)
            p_page.info_fetched.connect(self._on_social_info_fetched)

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
        self._update_manager.app_update_available.connect(self._on_app_update_available)
        self._update_manager.has_update_available.connect(self._sidebar.set_settings_badge)

        # Settings panel
        self._settings_panel.theme_changed.connect(self._on_theme_changed)
        self._settings_panel.settings_changed.connect(self._on_settings_changed)

        # History signals
        self._history_panel.redownload_requested.connect(self._on_redownload_requested)
        self._history_panel.status_message.connect(self._status_bar.showMessage)
        self._history_panel.history_cleared.connect(self._home_page.refresh_recent_downloads)

        # Queue panel status
        self._queue_panel.queue_action.connect(self._status_bar.showMessage)
        self._queue_panel.queue_action.connect(lambda _: self._update_queue_badge())

    def _start_background_services(self) -> None:
        """Start background services with tracked timers."""
        self._update_manager.set_settings(self._settings)
        self._update_manager.auto_update = self._settings.auto_update_ytdlp
        self._update_manager.auto_update_app = self._settings.auto_update_app
        self._update_manager.auto_update_ffmpeg = self._settings.auto_update_ffmpeg
        self._update_manager.app_update_url = getattr(self._settings, "app_update_url", "")
        self._update_manager.set_last_check_from_config(self._settings.last_update_check)

        # Show cached badges instantly on startup
        self._update_manager.load_cached_badges()
        self._sidebar.set_settings_badge(self._update_manager._has_any_update_available())

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

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Handle resize events with debounced breakpoint recalculation."""
        super().resizeEvent(event)
        if hasattr(self, "_resize_timer"):
            self._resize_timer.start()

    def _on_debounced_resize(self) -> None:
        """Evaluate breakpoints (Wide >=1280, Medium 960-1279, Narrow <960) and notify sidebar/pages."""
        w = self.width()
        if w >= 1280:
            bp = "wide"
            if hasattr(self, "_sidebar"):
                self._sidebar.set_collapsed(False)
        elif w >= 960:
            bp = "medium"
            if hasattr(self, "_sidebar"):
                self._sidebar.set_collapsed(True)
        else:
            bp = "narrow"
            if hasattr(self, "_sidebar"):
                self._sidebar.set_collapsed(True)

        if hasattr(self, "_pages"):
            for page in self._pages.values():
                if hasattr(page, "set_breakpoint"):
                    page.set_breakpoint(bp, w)

    def _restore_geometry(self) -> None:
        """Restore window position and size from settings, clamped to visible screen area."""
        x = getattr(self._settings, "window_x", 100)
        y = getattr(self._settings, "window_y", 100)
        w = max(getattr(self._settings, "window_width", 1176), MIN_WINDOW_WIDTH)
        h = max(getattr(self._settings, "window_height", 784), MIN_WINDOW_HEIGHT)

        # Detect screen containing (x, y) or fallback to primary screen
        screen = QApplication.screenAt(QPoint(x, y))
        if not screen:
            screen = QApplication.primaryScreen()

        if screen:
            avail = screen.availableGeometry()
            # Ensure window is not larger than screen
            w = min(w, avail.width())
            h = min(h, avail.height())
            w = max(w, MIN_WINDOW_WIDTH)
            h = max(h, MIN_WINDOW_HEIGHT)
            # Clamp position within screen bounds with at least titlebar visible
            x = max(avail.left(), min(x, avail.right() - 100))
            y = max(avail.top(), min(y, avail.bottom() - 100))
            self.setGeometry(x, y, w, h)
        else:
            self.setGeometry(x, y, w, h)

        if getattr(self._settings, "window_maximized", False):
            self.showMaximized()

        # Trigger initial breakpoint evaluation
        QTimer.singleShot(0, self._on_debounced_resize)

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
            self._youtube_page._shutdown_fetch_worker()
        except Exception as e:
            logger.error("Error shutting down YouTube fetch worker: %s", e)

        try:
            self._instagram_page.shutdown()
            self._facebook_page.shutdown()
        except Exception as e:
            logger.error("Error shutting down social pages: %s", e)

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
            is_max = self.isMaximized()
            if is_max:
                normal_geo = self.normalGeometry()
                win_x = normal_geo.x()
                win_y = normal_geo.y()
                win_w = normal_geo.width()
                win_h = normal_geo.height()
            else:
                geo = self.geometry()
                win_x = geo.x()
                win_y = geo.y()
                win_w = geo.width()
                win_h = geo.height()

            self._settings.update(
                window_x=win_x,
                window_y=win_y,
                window_width=win_w,
                window_height=win_h,
                window_maximized=is_max,
                last_update_check=self._update_manager.get_last_check_iso(),
                last_section=self._sidebar.get_active_page(),
            )
        except Exception as e:
            logger.error("Error saving settings on close: %s", e)

        logger.info("Shutdown complete. Application exiting.")
        event.accept()
        super().closeEvent(event)

    # ─── Navigation Handlers (Strictly by Section ID) ─────────────────────────

    def show_page(self, page_id: str) -> None:
        """Navigate to page identified by page_id."""
        self._update_manager.reset_idle_timer()

        # Normalize target ID
        norm_id = page_id.lower()
        if norm_id.startswith("youtube"):
            norm_id = "youtube"
        elif norm_id.startswith("instagram"):
            norm_id = "instagram"
        elif norm_id.startswith("facebook"):
            norm_id = "facebook"

        if norm_id not in self._pages:
            logger.warning("Unknown section ID requested: %s", page_id)
            norm_id = "home"

        target_widget = self._pages[norm_id]
        self._stack.setCurrentWidget(target_widget)
        self._sidebar.select_page(norm_id, notify=False)

        # Status bar messaging per page
        messages = {
            "home": "Home — Paste any link",
            "youtube": "YouTube — Video & Audio Clipper",
            "instagram": "Instagram — Reel & Post Download",
            "facebook": "Facebook — Video & Reel Download",
            "queue": "Download Queue",
            "history": "Download History",
            "settings": "Settings",
        }
        self._status_bar.showMessage(messages.get(norm_id, "Ready"))

        if norm_id == "home":
            self._home_page.refresh_recent_downloads()
        elif norm_id == "history":
            self._history_panel.refresh()

    def _on_home_route_requested(self, section_id: str, url: str, auto_fetch: bool) -> None:
        """Route from Universal Home page to platform page with prefilled URL."""
        norm_id = section_id.lower()
        if norm_id.startswith("youtube"):
            norm_id = "youtube"
            self.show_page("youtube")
            if url:
                self._youtube_page.set_url(url, auto_fetch=auto_fetch)
        elif norm_id.startswith("instagram"):
            norm_id = "instagram"
            self.show_page("instagram")
            if url:
                self._instagram_page.set_url(url)
        elif norm_id.startswith("facebook"):
            norm_id = "facebook"
            self.show_page("facebook")
            if url:
                self._facebook_page.set_url(url)
        else:
            self.show_page(norm_id)

    def _on_switch_section_requested(self, target_section_id: str, url: str, auto_fetch: bool) -> None:
        """Switch section from wrong-section banner."""
        self._on_home_route_requested(target_section_id, url, auto_fetch)

    # ─── Download & Fetch Pipeline Handlers ────────────────────────────────────

    def _on_social_info_fetched(self, video_info: VideoInfo) -> None:
        """Handle successful media info fetch from Instagram/Facebook."""
        self._current_video_info = video_info
        if video_info.thumbnail_url:
            self._cache_thumbnail(video_info)
        self._status_bar.showMessage(f"Fetched: {video_info.title}")

    def _on_social_download_now(self, req: DownloadRequest, info: VideoInfo) -> None:
        """Add social download request to queue and start downloading immediately."""
        video_label = ""
        if req.video_format:
            video_label = req.video_format.display_label() if hasattr(req.video_format, "display_label") else getattr(req.video_format, "resolution", "")
        audio_label = ""
        if req.audio_format:
            audio_label = req.audio_format.display_label() if hasattr(req.audio_format, "display_label") else getattr(req.audio_format, "abr", "")

        if req.audio_only and not audio_label:
            audio_label = req.merge_format.upper() if req.merge_format else "Audio"
        if not req.audio_only and not video_label:
            video_label = "1080p"

        out_ext = req.merge_format if req.merge_format else ("mp4" if not req.audio_only else "mp3")
        fname = req.filename if req.filename.endswith(f".{out_ext}") else f"{req.filename}.{out_ext}"
        out_path = os.path.join(req.output_dir, fname)

        if getattr(req, "convert_to_gif", False):
            download_type = "GIF"
        elif req.audio_only:
            download_type = "AUDIO ONLY"
        elif req.video_format and not req.audio_format and not getattr(req, "merge_format", None):
            download_type = "VIDEO ONLY"
        else:
            download_type = "VIDEO + AUDIO"

        item = QueueItem(
            url=req.url,
            title=info.title,
            thumbnail_url=info.thumbnail_url,
            duration=info.duration,
            video_format_label=video_label,
            audio_format_label=audio_label,
            output_path=out_path,
            download_request=req,
            download_type=download_type,
            platform_id=req.platform_id,
        )
        thumb_path = self._get_thumbnail_cache_path(info)
        if thumb_path and os.path.isfile(thumb_path):
            item.thumbnail_path = thumb_path

        self._queue_manager.add_item(item)
        self._update_queue_badge()
        if not self._queue_manager.is_processing:
            self._queue_manager.start_processing()
        self._status_bar.showMessage(f"Downloading: {item.title}")
        self.show_page("queue")

    def _on_youtube_info_fetched(self, video_info: VideoInfo) -> None:
        self._current_video_info = video_info
        if video_info.thumbnail_url:
            self._cache_thumbnail(video_info)

    def _on_youtube_add_to_queue(self, payload: dict) -> None:
        req = payload["request"]
        info: VideoInfo = payload["video_info"]
        start_sec = payload.get("start_seconds")
        end_sec = payload.get("end_seconds")
        from clipgrab.gui.widgets.youtube_page import _format_time

        video_label = ""
        if req.video_format:
            video_label = req.video_format.display_label() if hasattr(req.video_format, "display_label") else getattr(req.video_format, "resolution", "")
        audio_label = ""
        if req.audio_format:
            audio_label = req.audio_format.display_label() if hasattr(req.audio_format, "display_label") else getattr(req.audio_format, "abr", "")

        if req.audio_only and not audio_label:
            audio_label = req.merge_format.upper() if req.merge_format else "Audio"
        if not req.audio_only and not video_label:
            video_label = "Best available"

        out_ext = req.merge_format if req.merge_format else ("mp4" if not req.audio_only else "mp3")
        fname = req.filename if req.filename.endswith(f".{out_ext}") else f"{req.filename}.{out_ext}"
        out_path = os.path.join(req.output_dir, fname)

        if getattr(req, "convert_to_gif", False):
            download_type = "GIF"
        elif req.audio_only:
            download_type = "AUDIO ONLY"
        elif req.video_format and not req.audio_format and not getattr(req, "merge_format", None):
            download_type = "VIDEO ONLY"
        else:
            download_type = "VIDEO + AUDIO"

        item = QueueItem(
            url=req.url,
            title=info.title,
            thumbnail_url=info.thumbnail_url,
            start_time=_format_time(start_sec) if start_sec is not None else None,
            end_time=_format_time(end_sec) if end_sec is not None else None,
            start_seconds=start_sec,
            end_seconds=end_sec,
            duration=info.duration,
            video_format_label=video_label,
            audio_format_label=audio_label,
            output_path=out_path,
            download_request=req,
            download_type=download_type,
            platform_id="youtube",
        )
        thumb_path = self._get_thumbnail_cache_path(info)
        if thumb_path and os.path.isfile(thumb_path):
            item.thumbnail_path = thumb_path

        self._queue_manager.add_item(item)
        self._update_queue_badge()
        self._status_bar.showMessage(f"Added to queue: {item.title}")
        show_toast(self, f"Added to queue: {item.title}", toast_type="success")
        self.show_page("queue")

    def _on_youtube_download_now(self, payload: dict) -> None:
        req = payload["request"]
        info: VideoInfo = payload["video_info"]
        start_sec = payload.get("start_seconds")
        end_sec = payload.get("end_seconds")
        from clipgrab.gui.widgets.youtube_page import _format_time

        video_label = ""
        if req.video_format:
            video_label = req.video_format.display_label() if hasattr(req.video_format, "display_label") else getattr(req.video_format, "resolution", "")
        audio_label = ""
        if req.audio_format:
            audio_label = req.audio_format.display_label() if hasattr(req.audio_format, "display_label") else getattr(req.audio_format, "abr", "")

        if req.audio_only and not audio_label:
            audio_label = req.merge_format.upper() if req.merge_format else "Audio"
        if not req.audio_only and not video_label:
            video_label = "Best available"

        out_ext = req.merge_format if req.merge_format else ("mp4" if not req.audio_only else "mp3")
        fname = req.filename if req.filename.endswith(f".{out_ext}") else f"{req.filename}.{out_ext}"
        out_path = os.path.join(req.output_dir, fname)

        if getattr(req, "convert_to_gif", False):
            download_type = "GIF"
        elif req.audio_only:
            download_type = "AUDIO ONLY"
        elif req.video_format and not req.audio_format and not getattr(req, "merge_format", None):
            download_type = "VIDEO ONLY"
        else:
            download_type = "VIDEO + AUDIO"

        item = QueueItem(
            url=req.url,
            title=info.title,
            thumbnail_url=info.thumbnail_url,
            start_time=_format_time(start_sec) if start_sec is not None else None,
            end_time=_format_time(end_sec) if end_sec is not None else None,
            start_seconds=start_sec,
            end_seconds=end_sec,
            duration=info.duration,
            video_format_label=video_label,
            audio_format_label=audio_label,
            output_path=out_path,
            download_request=req,
            download_type=download_type,
            platform_id="youtube",
        )
        thumb_path = self._get_thumbnail_cache_path(info)
        if thumb_path and os.path.isfile(thumb_path):
            item.thumbnail_path = thumb_path

        self._queue_manager.add_item(item)
        self._update_queue_badge()
        if not self._queue_manager.is_processing:
            self._queue_manager.start_processing()
        self._status_bar.showMessage(f"Downloading: {item.title}")
        self.show_page("queue")



    def _on_queue_item_updated(self, index: int) -> None:
        """Handle queue item update and history recording."""
        self._update_queue_badge()
        item = self._queue_manager.get_item(index)
        if item is None:
            return

        if item.status == QueueItemStatus.DOWNLOADING:
            idx = index + 1
            total = self._queue_manager.item_count()
            self._sidebar.set_status(f"Downloading {idx} of {total}", state="downloading")
        elif item.status == QueueItemStatus.DONE:
            thumb_path = ThumbnailService.get_instance().cache_thumbnail_for_completed_item(
                item_id=item.id,
                thumbnail_url=item.thumbnail_url,
                output_path=item.output_path,
            )
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
                thumbnail_path=thumb_path or "",
            )
            show_toast(self, f"Download complete: {item.title}", toast_type="success")
            self._home_page.refresh_recent_downloads()
        elif item.status == QueueItemStatus.FAILED:
            show_toast(self, f"Download failed: {item.error_msg[:100]}", toast_type="error", duration_ms=8000)

    def _on_queue_finished(self) -> None:
        self._update_queue_badge()
        self._sidebar.set_status("Ready to clip", state="ready")
        self._status_bar.showMessage("Queue finished")
        show_toast(self, "All downloads complete!", toast_type="success")

    def _on_download_ended(self) -> None:
        self._update_queue_badge()
        if not self._queue_manager.is_busy:
            self._update_manager.set_queue_busy(False)
            self._sidebar.set_status("Ready to clip", state="ready")

    def _update_queue_badge(self) -> None:
        """Update sidebar badge with active + pending count."""
        items = self._queue_manager.get_items()
        count = sum(
            1 for it in items
            if it.status in (QueueItemStatus.DOWNLOADING, QueueItemStatus.PENDING)
        )
        self._sidebar.set_queue_count(count)

    def _get_active_download_paths(self) -> set[str]:
        """Collect output paths of currently downloading or pending queue items to prevent deletion."""
        items = self._queue_manager.get_items()
        return {
            os.path.normpath(it.output_path).lower()
            for it in items
            if it.output_path and it.status in (QueueItemStatus.DOWNLOADING, QueueItemStatus.PENDING)
        }

    def _on_app_update_available(self, version: str, notes: str, url: str) -> None:
        self._sidebar.set_status("Update available", state="update")
        self._settings_panel.show_app_update_banner(version, notes, url)

    def _on_theme_toggle(self) -> None:
        app = QApplication.instance()
        if app:
            new_theme = toggle_theme(app, self._settings)
            self._apply_theme_to_all_pages(new_theme)

    def _on_theme_changed(self, theme_key: str) -> None:
        app = QApplication.instance()
        if app:
            effective_theme = toggle_theme(app, self._settings, new_theme=theme_key)
            self._apply_theme_to_all_pages(effective_theme)
            from clipgrab.gui.theme import set_window_title_bar_theme
            set_window_title_bar_theme(self, effective_theme != "light")

    def _apply_theme_to_all_pages(self, theme_name: str) -> None:
        """Apply theme color and styling updates across all pages and widgets."""
        is_dark = (theme_name == "dark")
        sep_color = BORDER_DIVIDER if is_dark else BORDER_DIVIDER_LIGHT
        if hasattr(self, "_sep") and self._sep:
            self._sep.setStyleSheet(f"background-color: {sep_color};")
        if hasattr(self, "_sidebar") and self._sidebar:
            self._sidebar.update_theme(theme_name)
        if hasattr(self, "_settings_panel") and self._settings_panel:
            self._settings_panel.update_theme(theme_name)
        for page in getattr(self, "_pages", {}).values():
            if page is not getattr(self, "_settings_panel", None) and hasattr(page, "update_theme"):
                page.update_theme(theme_name)

    def _on_settings_changed(self) -> None:
        folder = self._settings.last_output_folder
        if hasattr(self, "_instagram_page"):
            self._instagram_page._full_folder_path = folder
            self._instagram_page._folder_input.setText(folder)
        if hasattr(self, "_facebook_page"):
            self._facebook_page._full_folder_path = folder
            self._facebook_page._folder_input.setText(folder)
        if hasattr(self, "_youtube_page"):
            self._youtube_page._full_folder_path = folder
            self._youtube_page._update_text_elisions()

    def _on_ytdlp_updated(self, new_version: str) -> None:
        self._settings.update(last_ytdlp_version=new_version)
        self._settings_panel.refresh_versions()

    def _on_redownload_requested(self, entry: HistoryEntry) -> None:
        target_sec, _ = route_url_to_section(entry.url)
        self._on_home_route_requested(target_sec.id, entry.url, True)
        self._status_bar.showMessage(f"Re-downloading: {entry.title}")

    # ─── Thumbnail & Clipboard Helpers ────────────────────────────────────────

    def _cache_thumbnail(self, video_info: VideoInfo) -> None:
        if not video_info.thumbnail_url:
            return
        cache_path = self._get_thumbnail_cache_path(video_info)
        if cache_path and os.path.isfile(cache_path):
            pix = QPixmap(cache_path)
            cur = self._stack.currentWidget()
            if hasattr(cur, "set_thumbnail_preview"):
                cur.set_thumbnail_preview(pix)
            return

        def _download():
            try:
                resp = requests.get(video_info.thumbnail_url, timeout=10)
                resp.raise_for_status()
                if cache_path:
                    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    QMetaObject.invokeMethod(
                        self,
                        "_apply_thumbnail_path",
                        Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, cache_path),
                    )
            except Exception as e:
                logger.debug("Thumbnail cache failed: %s", e)

        import threading
        self._thumbnail_threads = [t for t in self._thumbnail_threads if t.is_alive()]
        thread = threading.Thread(target=_download, daemon=True)
        self._thumbnail_threads.append(thread)
        thread.start()

    @Slot(str)
    def _apply_thumbnail_path(self, path: str) -> None:
        """Create and apply QPixmap strictly on the main GUI thread."""
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                cur = self._stack.currentWidget()
                if hasattr(cur, "set_thumbnail_preview"):
                    cur.set_thumbnail_preview(pix)

    def _apply_thumbnail(self, pix: QPixmap) -> None:
        cur = self._stack.currentWidget()
        if hasattr(cur, "set_thumbnail_preview"):
            cur.set_thumbnail_preview(pix)

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
            show_toast(self, "Media link detected in clipboard — ready on Home", toast_type="info", duration_ms=3000)
            logger.info("Clipboard auto-detected: %s", text[:60])

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Route clipping shortcuts (I, O, Space) when not editing input fields."""
        focus = QApplication.focusWidget()
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            super().keyPressEvent(event)
            return

        if hasattr(self, "_youtube_page") and self._stack.currentWidget() == self._youtube_page:
            if event.key() == Qt.Key.Key_I:
                self._youtube_page._on_shortcut_in_point()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_O:
                self._youtube_page._on_shortcut_out_point()
                event.accept()
                return
            elif event.key() == Qt.Key.Key_Space:
                self._youtube_page._toggle_play_pause()
                event.accept()
                return

        super().keyPressEvent(event)
