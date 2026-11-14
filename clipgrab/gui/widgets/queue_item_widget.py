"""
YouCut — Queue item widget.

Individual queue item card showing thumbnail, title, timecodes, quality,
status badge, progress bar, and action buttons.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.queue_manager import QueueItem, QueueItemStatus
from clipgrab.core.logger import get_logger

logger = get_logger("queue_item_widget")

_STATUS_COLORS = {
    QueueItemStatus.PENDING: ("#F7B731", "⏳"),       # Warm amber
    QueueItemStatus.DOWNLOADING: ("#E63946", "⬇️"),   # Brand red
    QueueItemStatus.DONE: ("#27AE60", "✅"),
    QueueItemStatus.WARNING: ("#F77F00", "⚠️"),           # Rich green
    QueueItemStatus.FAILED: ("#E74C3C", "❌"),         # Bright red
    QueueItemStatus.CANCELLED: ("#8E8E93", "🚫"),     # Neutral gray
    QueueItemStatus.STOPPED: ("#F77F00", "⏸"),        # Brand orange
}


class QueueItemWidget(QFrame):
    """
    Widget representing a single queue item card.

    Signals:
        remove_clicked: (item_id,)
        retry_clicked: (item_id,)
        cancel_clicked: (item_id,)
    """

    remove_clicked = Signal(str)
    retry_clicked = Signal(str)
    cancel_clicked = Signal(str)
    resume_clicked = Signal(str)
    open_location_clicked = Signal(str)

    def __init__(self, item: QueueItem, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._item_id = item.id
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumHeight(80)
        self._setup_ui()
        self.update_from_item(item)

    def _setup_ui(self) -> None:
        """Set up the queue item card UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(12)

        # Thumbnail
        self._thumbnail = QLabel()
        self._thumbnail.setFixedSize(QSize(100, 56))
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail.setStyleSheet(
            "background: rgba(28, 28, 36, 0.5); border-radius: 8px; color: #666;"
        )
        self._thumbnail.setText("🎬")
        self._thumbnail.setScaledContents(True)
        layout.addWidget(self._thumbnail)

        # Info column
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        self._title_label = QLabel("Loading...")
        self._title_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._title_label.setWordWrap(True)
        self._title_label.setMaximumHeight(40)
        info_col.addWidget(self._title_label)

        details_row = QHBoxLayout()
        details_row.setSpacing(10)

        self._type_badge = QLabel("VIDEO + AUDIO")
        self._type_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._type_badge.setStyleSheet(
            "background: rgba(59, 130, 246, 0.15); color: #60A5FA; "
            "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        details_row.addWidget(self._type_badge)

        self._time_label = QLabel("")
        self._time_label.setFont(QFont("Segoe UI", 9))
        self._time_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._time_label)

        self._quality_label = QLabel("")
        self._quality_label.setFont(QFont("Segoe UI", 9))
        self._quality_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        details_row.addWidget(self._quality_label)

        details_row.addStretch()
        info_col.addLayout(details_row)

        # Progress bar (only visible during download)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumHeight(16)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        info_col.addWidget(self._progress_bar)

        # Speed/ETA row
        self._stats_label = QLabel("")
        self._stats_label.setFont(QFont("Segoe UI", 8))
        self._stats_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        self._stats_label.hide()
        info_col.addWidget(self._stats_label)

        # Error message
        self._error_label = QLabel("")
        self._error_label.setFont(QFont("Segoe UI", 8))
        self._error_label.setStyleSheet("color: #e74c3c;")
        self._error_label.setWordWrap(True)
        self._error_label.setMaximumHeight(30)
        self._error_label.hide()
        info_col.addWidget(self._error_label)

        layout.addLayout(info_col, stretch=1)

        # Status badge + action buttons
        actions_col = QVBoxLayout()
        actions_col.setSpacing(4)

        self._status_label = QLabel("⏳ Pending")
        self._status_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setMinimumWidth(120)
        actions_col.addWidget(self._status_label)

        # Action buttons
        btn_style = (
            "QPushButton { font-size: 10px; padding: 4px 12px;"
            "  border-radius: 6px; font-weight: 600; }"
        )

        self._retry_btn = QPushButton("🔄 Retry")
        self._retry_btn.setStyleSheet(btn_style)
        self._retry_btn.clicked.connect(lambda checked=False: self.retry_clicked.emit(self._item_id))
        self._retry_btn.hide()
        actions_col.addWidget(self._retry_btn)

        self._cancel_btn = QPushButton("⏸ Stop")
        self._cancel_btn.setStyleSheet(btn_style)
        self._cancel_btn.clicked.connect(lambda checked=False: self.cancel_clicked.emit(self._item_id))
        self._cancel_btn.hide()
        actions_col.addWidget(self._cancel_btn)

        self._resume_btn = QPushButton("▶ Resume")
        self._resume_btn.setStyleSheet(btn_style)
        self._resume_btn.clicked.connect(lambda checked=False: self.resume_clicked.emit(self._item_id))
        self._resume_btn.hide()
        actions_col.addWidget(self._resume_btn)
        
        self._open_location_btn = QPushButton("📁 Open Folder")
        self._open_location_btn.setStyleSheet(btn_style)
        self._open_location_btn.clicked.connect(lambda checked=False: self.open_location_clicked.emit(self._item_id))
        self._open_location_btn.hide()
        actions_col.addWidget(self._open_location_btn)

        self._remove_btn = QPushButton("🗑 Remove")
        self._remove_btn.setStyleSheet(btn_style)
        self._remove_btn.clicked.connect(lambda checked=False: self.remove_clicked.emit(self._item_id))
        actions_col.addWidget(self._remove_btn)

        layout.addLayout(actions_col)

    def update_from_item(self, item: QueueItem) -> None:
        """Update the widget display from a QueueItem's current state."""
        self._item_id = item.id
        self._title_label.setText(item.title or "Untitled")

        # Download Type Marker (AUDIO ONLY, VIDEO + AUDIO, VIDEO ONLY, GIF)
        dtype = item.get_download_type_display().upper() if hasattr(item, "get_download_type_display") else (getattr(item, "download_type", "") or "VIDEO + AUDIO")
        self._type_badge.setText(dtype)
        if "AUDIO ONLY" in dtype:
            self._type_badge.setStyleSheet(
                "background: rgba(16, 185, 129, 0.15); color: #10B981; "
                "border: 1px solid rgba(16, 185, 129, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif "VIDEO ONLY" in dtype:
            self._type_badge.setStyleSheet(
                "background: rgba(168, 85, 247, 0.15); color: #C084FC; "
                "border: 1px solid rgba(168, 85, 247, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        elif "GIF" in dtype:
            self._type_badge.setStyleSheet(
                "background: rgba(244, 63, 94, 0.15); color: #FB7185; "
                "border: 1px solid rgba(244, 63, 94, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        else:
            self._type_badge.setStyleSheet(
                "background: rgba(59, 130, 246, 0.15); color: #60A5FA; "
                "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px; font-weight: bold;"
            )
        self._type_badge.show()

        # Time range
        if item.start_time and item.end_time:
            self._time_label.setText(f"⏱ {item.start_time} → {item.end_time}")
        else:
            self._time_label.setText("⏱ Full video")

        # Quality
        self._quality_label.setText(f"📊 {item.video_format_label}")

        # Status badge
        color, icon = _STATUS_COLORS.get(
            item.status, ("#888", "?")
        )
        self._status_label.setText(f"{icon} {item.status.value}")
        self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

        # Thumbnail
        if item.thumbnail_path and os.path.isfile(item.thumbnail_path):
            pixmap = QPixmap(item.thumbnail_path)
            if not pixmap.isNull():
                self._thumbnail.setPixmap(
                    pixmap.scaled(100, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )

        # Progress
        if item.status == QueueItemStatus.DOWNLOADING:
            self._progress_bar.show()
            # If progress is 0% and no speed info yet, show indeterminate pulsing bar
            if item.progress < 0.1 and not item.speed:
                self._progress_bar.setRange(0, 0)  # Indeterminate mode (pulsing)
            else:
                self._progress_bar.setRange(0, 100)  # Normal percentage mode
                self._progress_bar.setValue(int(item.progress))
                self._progress_bar.setFormat(f"%p%  •  {dtype}")
            self._stats_label.show()
            stats_parts = []
            if item.speed:
                stats_parts.append(f"Speed: {item.speed}")
            if item.eta:
                stats_parts.append(f"ETA: {item.eta}")
            if stats_parts:
                self._stats_label.setText("  •  ".join(stats_parts))
            elif item.status_text:
                self._stats_label.setText(item.status_text)
            else:
                self._stats_label.setText("Downloading...")
            self._cancel_btn.show()
            self._retry_btn.hide()
            self._remove_btn.hide()
        elif item.status in (QueueItemStatus.DONE, QueueItemStatus.WARNING):
            self._progress_bar.show()
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)
            self._progress_bar.setFormat(f"100%  •  {dtype}")
            self._stats_label.show()
            if item.status == QueueItemStatus.WARNING and item.warning_reason:
                self._stats_label.setText(f"Warning: {item.warning_reason}")
                self._stats_label.setStyleSheet("color: #F77F00;")
            else:
                self._stats_label.setText("Completed")
                self._stats_label.setStyleSheet("color: #10B981;")
            self._cancel_btn.hide()
        else:
            self._progress_bar.hide()
            self._stats_label.hide()
            self._cancel_btn.hide()

        # Error
        if item.status == QueueItemStatus.FAILED and item.error_msg:
            self._error_label.setText(item.error_msg[:150])
            self._error_label.show()
            self._retry_btn.show()
            self._remove_btn.show()
        else:
            self._error_label.hide()

        # Button visibility
        if item.status == QueueItemStatus.PENDING:
            self._remove_btn.show()
            self._cancel_btn.show()
            self._retry_btn.hide()
            self._resume_btn.hide()
            self._open_location_btn.hide()
        elif item.status == QueueItemStatus.WARNING:
            self._progress_bar.setVisible(False)
            self._stats_label.show()
            self._stats_label.setText(f"Warning: {item.warning_reason}" if item.warning_reason else "Completed with warnings")
            self._stats_label.setStyleSheet("color: #F77F00;")
            self._cancel_btn.hide()
            self._retry_btn.hide()
            self._remove_btn.show()
            self._resume_btn.hide()
            self._open_location_btn.setVisible(bool(item.output_path))
        elif item.status == QueueItemStatus.DONE:
            self._remove_btn.show()
            self._retry_btn.hide()
            self._cancel_btn.hide()
            self._resume_btn.hide()
            self._open_location_btn.show()
        elif item.status == QueueItemStatus.CANCELLED:
            self._remove_btn.show()
            self._retry_btn.show()
            self._cancel_btn.hide()
            self._resume_btn.hide()
            self._open_location_btn.hide()
        elif item.status == QueueItemStatus.FAILED:
            self._remove_btn.show()
            self._retry_btn.show()
            self._cancel_btn.hide()
            self._resume_btn.hide()
            self._open_location_btn.hide()
        elif item.status == QueueItemStatus.DOWNLOADING:
            self._remove_btn.hide()
            self._retry_btn.hide()
            self._cancel_btn.show()
            self._resume_btn.hide()
            self._open_location_btn.hide()
        elif item.status == QueueItemStatus.STOPPED:
            self._remove_btn.show()
            self._resume_btn.show()
            self._retry_btn.hide()
            self._cancel_btn.hide()
            self._open_location_btn.hide()

    @property
    def item_id(self) -> str:
        """The ID of the queue item this widget represents."""
        return self._item_id
