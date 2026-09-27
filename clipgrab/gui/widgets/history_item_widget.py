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

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.data.history_db import HistoryEntry
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

    def __init__(self, entry: HistoryEntry, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entry_id = entry.id
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumHeight(60)
        self._setup_ui()
        self.update_from_entry(entry)

    def _setup_ui(self) -> None:
        """Set up the history item UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(12)

        # Info column
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        self._title_label = QLabel("")
        self._title_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._title_label.setWordWrap(True)
        self._title_label.setMaximumHeight(36)
        info_col.addWidget(self._title_label)

        details_row = QHBoxLayout()
        details_row.setSpacing(16)

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
        self._entry_id = entry.id
        self._output_path = entry.output_path

        self._title_label.setText(entry.title or "Untitled")

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
