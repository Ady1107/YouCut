"""
YouCut — URL input widget with multi-platform detection, fetch, and validation.

Supports YouTube, Instagram, Facebook, X/Twitter (all via yt-dlp), and Pinterest
(separate engine — Phase 4).  URL validation is platform-aware: any recognised
platform URL is accepted; unrecognised URLs show a clear 'not supported' message.

Shows a small platform badge (emoji + name) once a URL is detected.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.downloader import DownloadWorker
from clipgrab.core.formats import VideoInfo
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import (
    DownloadEngine,
    PlatformConfig,
    PLATFORM_UNSUPPORTED,
    detect_platform,
    is_supported,
)

logger = get_logger("url_input")


class UrlInputWidget(QWidget):
    """
    Widget for multi-platform URL input with detection badge and info fetching.

    Supports any URL matched by platforms.detect_platform():
      - YouTube, Instagram, Facebook, X/Twitter  → yt-dlp fetch
      - Pinterest                                 → dedicated engine (Phase 4)
      - Anything else                             → unsupported message

    Signals:
        info_fetched:  (VideoInfo,) — successful yt-dlp info fetch
        fetch_failed:  (error_message, is_extraction_error) — fetch failure
        fetch_started: () — fetch has begun
    """

    info_fetched  = Signal(object)
    fetch_failed  = Signal(str, bool)
    fetch_started = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[DownloadWorker] = None
        self._current_platform: PlatformConfig = PLATFORM_UNSUPPORTED
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        """Build the URL input UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Row 1: label + platform badge
        header_row = QHBoxLayout()
        self._label = QLabel("Video URL")
        self._label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        header_row.addWidget(self._label)

        self._platform_badge = QLabel("")
        self._platform_badge.setFont(QFont("Segoe UI", 9))
        self._platform_badge.setStyleSheet(
            "color: #E63946; font-weight: 600; padding: 2px 6px;"
            "background: rgba(230,57,70,0.12); border-radius: 6px;"
        )
        self._platform_badge.hide()
        header_row.addWidget(self._platform_badge)
        header_row.addStretch()
        layout.addLayout(header_row)

        # Row 2: URL field + Paste + Fetch buttons
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(
            "https://youtube.com/watch?v=...   or   instagram.com/reel/...   or   x.com/..."
        )
        self._url_input.setMinimumHeight(36)
        self._url_input.setFont(QFont("Segoe UI", 10))
        self._url_input.returnPressed.connect(self._on_fetch_clicked)
        self._url_input.textChanged.connect(self._on_text_changed)
        input_row.addWidget(self._url_input, stretch=1)

        self._paste_btn = QPushButton("📋 Paste")
        self._paste_btn.setMinimumHeight(36)
        self._paste_btn.setFixedWidth(80)
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        input_row.addWidget(self._paste_btn)

        self._fetch_btn = QPushButton("🔍 Fetch Info")
        self._fetch_btn.setMinimumHeight(36)
        self._fetch_btn.setFixedWidth(120)
        self._fetch_btn.setEnabled(False)
        self._fetch_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #E63946, stop:1 #F77F00); color: white;"
            "  border: none; border-radius: 8px; font-weight: 600; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #D12D3A, stop:1 #E06F00); }"
            "QPushButton:disabled { background: rgba(28, 28, 36, 0.5);"
            "  color: rgba(235, 235, 245, 0.2); border: 1px solid rgba(255, 255, 255, 0.03); }"
        )
        self._fetch_btn.clicked.connect(self._on_fetch_clicked)
        input_row.addWidget(self._fetch_btn)

        layout.addLayout(input_row)

        # Row 3: status / validation message
        self._status_label = QLabel("")
        self._status_label.setFont(QFont("Segoe UI", 9))
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_url(self) -> str:
        """Return the current URL text (stripped)."""
        return self._url_input.text().strip()

    def set_url(self, url: str) -> None:
        """Set the URL field programmatically."""
        self._url_input.setText(url)

    def get_current_platform(self) -> PlatformConfig:
        """Return the PlatformConfig last detected for the current URL."""
        return self._current_platform

    def set_fetching(self, fetching: bool) -> None:
        """Toggle fetching UI state."""
        self._fetch_btn.setEnabled(not fetching)
        self._url_input.setEnabled(not fetching)
        if fetching:
            self._fetch_btn.setText("⏳ Fetching...")
            self._status_label.setText("Fetching video information...")
            self._status_label.setStyleSheet("color: #E63946;")
        else:
            self._fetch_btn.setText("🔍 Fetch Info")

    def is_fetching(self) -> bool:
        """True if a fetch is currently running."""
        return self._fetch_thread is not None and self._fetch_thread.isRunning()

    def shutdown(self) -> None:
        """Cancel any in-flight fetch and wait for thread to finish."""
        logger.info("Shutdown: Stopping URL fetch worker and thread...")
        if self._fetch_worker:
            try:
                self._fetch_worker.request_cancel()
            except Exception as e:
                logger.warning("Error requesting fetch worker cancel: %s", e)

        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            if not self._fetch_thread.wait(2000):
                logger.warning("Shutdown: Fetch thread did not exit in 2s, terminating...")
                self._fetch_thread.terminate()
                self._fetch_thread.wait(1000)

        self._fetch_thread = None
        self._fetch_worker = None
        self.set_fetching(False)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_platform_badge(self, platform: PlatformConfig) -> None:
        """Show / hide the platform badge based on detection result."""
        self._current_platform = platform
        if platform.engine == DownloadEngine.UNSUPPORTED:
            self._platform_badge.hide()
        else:
            self._platform_badge.setText(f"{platform.icon} {platform.display_name}")
            self._platform_badge.show()

    def _on_text_changed(self, text: str) -> None:
        """Detect platform and validate URL as the user types."""
        text = text.strip()
        if not text:
            self._fetch_btn.setEnabled(False)
            self._status_label.setText("")
            self._platform_badge.hide()
            self._current_platform = PLATFORM_UNSUPPORTED
            return

        platform = detect_platform(text)
        self._update_platform_badge(platform)

        if platform.engine == DownloadEngine.UNSUPPORTED:
            self._fetch_btn.setEnabled(False)
            self._status_label.setText(
                "❌ Not a supported URL.  Supported: YouTube, Instagram, "
                "Facebook, X/Twitter, Pinterest."
            )
            self._status_label.setStyleSheet("color: #e74c3c;")
            return

        if platform.engine == DownloadEngine.PINTEREST_HTTP:
            # Pinterest engine (Phase 4) — show detected but no fetch yet
            self._fetch_btn.setEnabled(True)
            self._status_label.setText(
                f"✓ {platform.display_name} URL detected  "
                "(Pinterest downloader — image/video)"
            )
            self._status_label.setStyleSheet("color: #27AE60;")
            return

        # yt-dlp platforms
        self._fetch_btn.setEnabled(True)
        self._status_label.setText(f"✓ {platform.display_name} URL detected")
        self._status_label.setStyleSheet("color: #27AE60;")

    def _on_paste_clicked(self) -> None:
        """Paste clipboard contents into the URL field."""
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            if text:
                self._url_input.setText(text.strip())

    def _on_fetch_clicked(self) -> None:
        """Start fetching info in a background thread."""
        url = self.get_url()
        platform = detect_platform(url)

        if platform.engine == DownloadEngine.UNSUPPORTED:
            self._status_label.setText(
                "❌ Cannot fetch: this URL is not from a supported platform."
            )
            self._status_label.setStyleSheet("color: #e74c3c;")
            return

        if platform.engine == DownloadEngine.PINTEREST_HTTP:
            # Pinterest fetch will be wired in Phase 4
            self._status_label.setText(
                "Pinterest downloader is coming in a future update. "
                "Stay tuned!"
            )
            self._status_label.setStyleSheet("color: #F77F00;")
            return

        # ── yt-dlp platforms (YouTube, Instagram, Facebook, X) ──
        self.set_fetching(True)
        self.fetch_started.emit()

        self._fetch_thread = QThread(self)
        self._fetch_worker = DownloadWorker()
        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_worker.info_fetched.connect(self._on_info_fetched)
        self._fetch_worker.info_fetch_failed.connect(self._on_fetch_failed)

        self._fetch_worker.fetch_url = url
        self._fetch_worker.fetch_platform_id = platform.id  # pass platform so --no-playlist is correct
        self._fetch_thread.started.connect(
            self._fetch_worker.execute_fetch, Qt.ConnectionType.DirectConnection
        )
        self._fetch_thread.finished.connect(self._on_fetch_thread_finished)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)

        self._fetch_thread.start()

    def _on_fetch_thread_finished(self) -> None:
        """Clean up thread/worker references after thread exits."""
        self._fetch_thread = None
        self._fetch_worker = None

    def _on_info_fetched(self, video_info: VideoInfo) -> None:
        """Handle successful info fetch."""
        self.set_fetching(False)
        platform = self._current_platform
        source = platform.display_name if platform.engine != DownloadEngine.UNSUPPORTED else "Video"
        self._status_label.setText(
            f"✓ {source}: {video_info.title}  "
            f"({self._format_duration(video_info.duration)})"
        )
        self._status_label.setStyleSheet("color: #27AE60;")

        if self._fetch_thread:
            self._fetch_thread.quit()

        self.info_fetched.emit(video_info)

    def _on_fetch_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        """Handle fetch failure with login-required classification."""
        self.set_fetching(False)

        # Detect login-required errors from yt-dlp and show a friendlier message
        login_keywords = [
            "login required", "sign in", "private video",
            "this video is private", "login_required",
            "you need to log in", "authentication required",
        ]
        error_lower = error_msg.lower()
        is_login_error = any(kw in error_lower for kw in login_keywords)

        if is_login_error:
            platform_name = self._current_platform.display_name
            self._status_label.setText(
                f"🔒 {platform_name}: This content is private or login-gated.\n"
                "Private content requires cookie-based login. "
                "A 'Settings → Accounts' section for cookie import is planned for a future update."
            )
            self._status_label.setStyleSheet("color: #F77F00;")
            # Emit fetch_failed with is_extraction_error=False so we don't trigger yt-dlp update
            if self._fetch_thread:
                self._fetch_thread.quit()
            self.fetch_failed.emit(error_msg, False)
            return

        self._status_label.setText(f"✗ {error_msg}")
        self._status_label.setStyleSheet("color: #e74c3c;")

        if self._fetch_thread:
            self._fetch_thread.quit()

        self.fetch_failed.emit(error_msg, is_extraction_error)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds to a readable HhMmSs string."""
        if seconds <= 0:
            return "unknown duration"
        h = int(seconds) // 3600
        m = (int(seconds) % 3600) // 60
        s = int(seconds) % 60
        if h > 0:
            return f"{h}h {m}m {s}s"
        if m > 0:
            return f"{m}m {s}s"
        return f"{s}s"
