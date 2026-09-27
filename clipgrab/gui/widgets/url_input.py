"""
YouCut — URL input widget with fetch button and validation.

Provides a text field for YouTube URLs, a Fetch Info button, and inline
validation feedback. Runs info fetching in a background thread.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
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
from clipgrab.core.validator import validate_youtube_url

logger = get_logger("url_input")


class UrlInputWidget(QWidget):
    """
    Widget for YouTube URL input with validation and info fetching.

    Signals:
        info_fetched: (VideoInfo,) — emitted when video info is successfully fetched
        fetch_failed: (error_message, is_extraction_error) — emitted on fetch failure
        fetch_started: () — emitted when fetch begins
    """

    info_fetched = Signal(object)
    fetch_failed = Signal(str, bool)
    fetch_started = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[DownloadWorker] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the URL input UI elements."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Label
        label = QLabel("YouTube URL")
        label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        layout.addWidget(label)

        # Input row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("https://www.youtube.com/watch?v=...")
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

        # Status label
        self._status_label = QLabel("")
        self._status_label.setFont(QFont("Segoe UI", 9))
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def get_url(self) -> str:
        """Get the current URL text."""
        return self._url_input.text().strip()

    def set_url(self, url: str) -> None:
        """Set the URL text programmatically."""
        self._url_input.setText(url)

    def set_fetching(self, fetching: bool) -> None:
        """Update UI state for fetching mode."""
        self._fetch_btn.setEnabled(not fetching)
        self._url_input.setEnabled(not fetching)
        if fetching:
            self._fetch_btn.setText("⏳ Fetching...")
            self._status_label.setText("Fetching video information...")
            self._status_label.setStyleSheet("color: #E63946;")
        else:
            self._fetch_btn.setText("🔍 Fetch Info")

    def _on_text_changed(self, text: str) -> None:
        """Validate URL as user types."""
        text = text.strip()
        if not text:
            self._fetch_btn.setEnabled(False)
            self._status_label.setText("")
            return

        is_valid, msg = validate_youtube_url(text)
        self._fetch_btn.setEnabled(is_valid)

        if is_valid:
            self._status_label.setText("✓ Valid YouTube URL")
            self._status_label.setStyleSheet("color: #27AE60;")
        else:
            self._status_label.setText(msg)
            self._status_label.setStyleSheet("color: #e74c3c;")

    def _on_paste_clicked(self) -> None:
        """Paste clipboard contents into the URL field."""
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text()
            if text:
                self._url_input.setText(text.strip())

    def _on_fetch_clicked(self) -> None:
        """Start fetching video info in a background thread."""
        url = self.get_url()
        is_valid, msg = validate_youtube_url(url)

        if not is_valid:
            self._status_label.setText(msg)
            self._status_label.setStyleSheet("color: #e74c3c;")
            return

        self.set_fetching(True)
        self.fetch_started.emit()

        # Create worker and thread
        self._fetch_thread = QThread(self)
        self._fetch_worker = DownloadWorker()
        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_worker.info_fetched.connect(self._on_info_fetched)
        self._fetch_worker.info_fetch_failed.connect(self._on_fetch_failed)
        
        self._fetch_worker.fetch_url = url
        self._fetch_thread.started.connect(self._fetch_worker.execute_fetch, Qt.ConnectionType.DirectConnection)
        self._fetch_thread.finished.connect(self._on_fetch_thread_finished)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)

        self._fetch_thread.start()

    def _on_fetch_thread_finished(self) -> None:
        """Clean up thread and worker references when thread exits."""
        self._fetch_thread = None
        self._fetch_worker = None

    def is_fetching(self) -> bool:
        """Check if an info fetch is currently running."""
        return self._fetch_thread is not None and self._fetch_thread.isRunning()

    def shutdown(self) -> None:
        """Cancel any ongoing fetch operation and wait for thread to terminate."""
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

    def _on_info_fetched(self, video_info: VideoInfo) -> None:
        """Handle successful info fetch."""
        self.set_fetching(False)
        self._status_label.setText(
            f"✓ {video_info.title} ({self._format_duration(video_info.duration)})"
        )
        self._status_label.setStyleSheet("color: #27AE60;")

        if self._fetch_thread:
            self._fetch_thread.quit()
        self.info_fetched.emit(video_info)

    def _on_fetch_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        """Handle failed info fetch."""
        self.set_fetching(False)
        self._status_label.setText(f"✗ {error_msg}")
        self._status_label.setStyleSheet("color: #e74c3c;")

        if self._fetch_thread:
            self._fetch_thread.quit()
        self.fetch_failed.emit(error_msg, is_extraction_error)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in seconds to a readable string."""
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
