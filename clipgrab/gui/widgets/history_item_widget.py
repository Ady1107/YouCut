"""
YouCut — History item widget.

Individual history row showing title, date, timecodes, quality, file size,
file-exists indicator, and action buttons.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.thumbnail_service import ThumbnailService
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.core.logger import get_logger

logger = get_logger("history_item_widget")


class HistoryItemWidget(QFrame):
    """
    Widget representing a single history entry row.

    Signals:
        open_location_clicked: (output_path,)
        redownload_clicked: (entry_id,)
        remove_clicked: (entry_id,)
    """

    open_location_clicked = Signal(str)
    redownload_clicked = Signal(int)
    remove_clicked = Signal(int)

    def __init__(self, entry: HistoryEntry, db: Optional[HistoryDB] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._entry = entry
        self._entry_id = entry.id
        self._is_dark = True
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumHeight(88)
        app = QApplication.instance()
        self._is_dark = (app.property("current_theme") != "light") if app else True
        self._setup_ui()
        self.update_theme(self._is_dark)
        self.update_from_entry(entry)

    def update_theme(self, is_dark: bool) -> None:
        """Update history card styles for dark/light theme."""
        self._is_dark = is_dark
        bg = "#171922" if is_dark else "#FFFFFF"
        border = "rgba(255, 255, 255, 0.07)" if is_dark else "#DCE0E8"
        hover_bg = "#1B1E2B" if is_dark else "#F8F9FB"
        hover_border = "rgba(255, 255, 255, 0.12)" if is_dark else "#CCD1DC"
        title_col = "#F3F4F7" if is_dark else "#111318"
        meta_col = "rgba(235, 235, 245, 0.45)" if is_dark else "#5A6170"

        self.setStyleSheet(
            f"HistoryItemWidget {{ background-color: {bg}; border: 1px solid {border}; border-radius: 12px; }}"
            f"HistoryItemWidget:hover {{ background-color: {hover_bg}; border: 1px solid {hover_border}; }}"
        )
        if hasattr(self, "_title_label"):
            self._title_label.setStyleSheet(f"color: {title_col};")
        if hasattr(self, "_date_label"):
            self._date_label.setStyleSheet(f"color: {meta_col};")
        if hasattr(self, "_time_label"):
            self._time_label.setStyleSheet(f"color: {meta_col};")
        if hasattr(self, "_quality_label"):
            self._quality_label.setStyleSheet(f"color: {meta_col};")
        if hasattr(self, "_size_label"):
            self._size_label.setStyleSheet(f"color: {meta_col};")

        # Refresh thumbnail for new theme if needed
        if hasattr(self, "_entry") and self._entry:
            pix = ThumbnailService.get_instance().load_thumbnail(
                self._entry,
                QSize(128, 72),
                self._on_thumbnail_ready,
                is_dark=self._is_dark,
                db=self._db,
            )
            if hasattr(self, "_thumb_label") and not pix.isNull():
                self._thumb_label.setPixmap(pix)

    def _setup_ui(self) -> None:
        """Set up the history item UI with left 16:9 thumbnail, center info, right actions."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        # 16:9 thumbnail (~128x72, rounded 8px, cropped to fill)
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(128, 72)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setStyleSheet("border-radius: 8px; background: transparent;")
        layout.addWidget(self._thumb_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # Info column
        info_col = QVBoxLayout()
        info_col.setSpacing(4)

        # Top row: Platform badge + Type badge + Title
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        self._platform_badge = QLabel("YOUTUBE")
        self._platform_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._platform_badge.setStyleSheet(
            "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
            "border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        title_row.addWidget(self._platform_badge)

        self._type_badge = QLabel("VIDEO")
        self._type_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._type_badge.setStyleSheet(
            "background: rgba(59, 130, 246, 0.15); color: #60A5FA; "
            "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        title_row.addWidget(self._type_badge)

        self._title_label = QLabel("")
        self._title_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._title_label.setWordWrap(True)
        self._title_label.setMaximumHeight(36)
        title_row.addWidget(self._title_label, stretch=1)

        info_col.addLayout(title_row)

        details_row = QHBoxLayout()
        details_row.setSpacing(14)

        self._date_label = QLabel("")
        self._date_label.setFont(QFont("Segoe UI", 9))
        self._date_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._date_label)

        self._time_label = QLabel("")
        self._time_label.setFont(QFont("Segoe UI", 9))
        self._time_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._time_label)

        self._quality_label = QLabel("")
        self._quality_label.setFont(QFont("Segoe UI", 9))
        self._quality_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._quality_label)

        self._size_label = QLabel("")
        self._size_label.setFont(QFont("Segoe UI", 9))
        self._size_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._size_label)

        self._file_status_label = QLabel("")
        self._file_status_label.setFont(QFont("Segoe UI", 9))
        details_row.addWidget(self._file_status_label)

        details_row.addStretch()
        info_col.addLayout(details_row)

        layout.addLayout(info_col, stretch=1)

        # Action buttons
        actions_col = QVBoxLayout()
        actions_col.setSpacing(4)

        btn_style = "font-size: 10px; padding: 3px 8px;"

        self._open_btn = QPushButton("📂 Open Location")
        self._open_btn.setStyleSheet(btn_style)
        self._open_btn.clicked.connect(self._on_open_clicked)
        actions_col.addWidget(self._open_btn)

        self._redownload_btn = QPushButton("🔄 Re-download")
        self._redownload_btn.setStyleSheet(btn_style)
        self._redownload_btn.clicked.connect(
            lambda: self.redownload_clicked.emit(self._entry_id)
        )
        actions_col.addWidget(self._redownload_btn)

        self._remove_btn = QPushButton("🗑 Remove")
        self._remove_btn.setStyleSheet(btn_style)
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._entry_id)
        )
        actions_col.addWidget(self._remove_btn)

        layout.addLayout(actions_col)

    def update_from_entry(self, entry: HistoryEntry) -> None:
        """Update the widget from a HistoryEntry."""
        self._entry = entry
        self._entry_id = entry.id
        self._output_path = entry.output_path

        # Asynchronously load or retrieve thumbnail
        pix = ThumbnailService.get_instance().load_thumbnail(
            entry,
            QSize(128, 72),
            self._on_thumbnail_ready,
            is_dark=self._is_dark,
            db=self._db,
        )
        if hasattr(self, "_thumb_label") and not pix.isNull():
            self._thumb_label.setPixmap(pix)

        self._title_label.setText(entry.title or "Untitled")

        # Platform badge detection
        from clipgrab.core.platforms import detect_platform
        plat = detect_platform(entry.url)
        plat_id = plat.id.lower() if plat else "youtube"

        if plat_id == "instagram":
            self._platform_badge.setText("INSTAGRAM")
            self._platform_badge.setStyleSheet(
                "background: rgba(236, 72, 153, 0.15); color: #F472B6; "
                "border: 1px solid rgba(236, 72, 153, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif plat_id == "facebook":
            self._platform_badge.setText("FACEBOOK")
            self._platform_badge.setStyleSheet(
                "background: rgba(59, 130, 246, 0.15); color: #60A5FA; "
                "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif plat_id in ("twitter_x", "x"):
            self._platform_badge.setText("X / TWITTER")
            self._platform_badge.setStyleSheet(
                "background: rgba(148, 163, 184, 0.15); color: #E2E8F0; "
                "border: 1px solid rgba(148, 163, 184, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif plat_id == "pinterest":
            self._platform_badge.setText("PINTEREST")
            self._platform_badge.setStyleSheet(
                "background: rgba(225, 29, 72, 0.15); color: #FB7185; "
                "border: 1px solid rgba(225, 29, 72, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        else:
            self._platform_badge.setText("YOUTUBE")
            self._platform_badge.setStyleSheet(
                "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
                "border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )

        # File type badge (Video, Audio, GIF)
        out_lower = (entry.output_path or "").lower()
        vq_lower = (entry.video_quality or "").lower()
        if out_lower.endswith(".gif") or "gif" in vq_lower:
            self._type_badge.setText("GIF")
            self._type_badge.setStyleSheet(
                "background: rgba(244, 63, 94, 0.15); color: #FB7185; "
                "border: 1px solid rgba(244, 63, 94, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif any(out_lower.endswith(ext) for ext in (".mp3", ".m4a", ".wav", ".aac", ".opus", ".flac")) or "audio only" in vq_lower or not entry.video_quality:
            self._type_badge.setText("AUDIO")
            self._type_badge.setStyleSheet(
                "background: rgba(16, 185, 129, 0.15); color: #10B981; "
                "border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        else:
            self._type_badge.setText("VIDEO")
            self._type_badge.setStyleSheet(
                "background: rgba(59, 130, 246, 0.15); color: #60A5FA; "
                "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )

        # Date
        if entry.downloaded_at:
            self._date_label.setText(f"📅 {entry.downloaded_at[:16]}")
        else:
            self._date_label.setText("")

        # Time range
        if entry.start_time and entry.end_time:
            self._time_label.setText(f"⏱ {entry.start_time} → {entry.end_time}")
        else:
            self._time_label.setText("⏱ Full video")

        # Quality
        quality_parts = []
        if entry.video_quality:
            quality_parts.append(entry.video_quality)
        if entry.audio_quality:
            quality_parts.append(entry.audio_quality)
        self._quality_label.setText(
            f"📊 {' | '.join(quality_parts)}" if quality_parts else ""
        )

        # File size
        if entry.file_size > 0:
            self._size_label.setText(f"💾 {self._format_size(entry.file_size)}")
        else:
            self._size_label.setText("")

        # File exists check
        if entry.file_exists:
            self._file_status_label.setText("✓ File exists")
            self._file_status_label.setStyleSheet("color: #2ecc71;")
            self._open_btn.setEnabled(True)
        else:
            self._file_status_label.setText("✗ File not found")
            self._file_status_label.setStyleSheet("color: #e74c3c;")
            self._open_btn.setEnabled(False)

    def _on_open_clicked(self) -> None:
        """Open the file location in Explorer."""
        if self._output_path and os.path.isfile(self._output_path):
            try:
                # Open Explorer and select the file
                subprocess.run(
                    ["explorer", "/select,", os.path.normpath(self._output_path)],
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                    check=False,
                )
            except OSError as e:
                logger.error("Failed to open file location: %s", e)
        elif self._output_path:
            QMessageBox.warning(self, "File Not Found", "The downloaded file could not be found. It may have been moved or deleted.")

    def _on_thumbnail_ready(self, entry_id: int, pixmap: QPixmap) -> None:
        """Callback invoked on GUI thread when background worker loads thumbnail."""
        if self._entry_id == entry_id and hasattr(self, "_thumb_label") and not pixmap.isNull():
            self._thumb_label.setPixmap(pixmap)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format file size in bytes to human-readable string."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        if size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    @property
    def entry_id(self) -> int:
        """The ID of the history entry this widget represents."""
        return self._entry_id
