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
    QApplication,
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


def _get_card_style(status: QueueItemStatus, is_dark: bool) -> str:
    if status == QueueItemStatus.DOWNLOADING:
        if is_dark:
            return "QueueItemWidget { background-color: rgba(245, 158, 11, 0.05); border: 1px solid rgba(245, 158, 11, 0.45); border-radius: 12px; }"
        else:
            return "QueueItemWidget { background-color: #FFFBEB; border: 1px solid #F59E0B; border-radius: 12px; }"
    elif status in (QueueItemStatus.DONE, QueueItemStatus.WARNING):
        if is_dark:
            return "QueueItemWidget { background-color: rgba(34, 197, 94, 0.05); border: 1px solid rgba(34, 197, 94, 0.45); border-radius: 12px; }"
        else:
            return "QueueItemWidget { background-color: #F0FDF4; border: 1px solid #22C55E; border-radius: 12px; }"
    elif status == QueueItemStatus.FAILED:
        if is_dark:
            return "QueueItemWidget { background-color: rgba(239, 68, 68, 0.05); border: 1px solid rgba(239, 68, 68, 0.45); border-radius: 12px; }"
        else:
            return "QueueItemWidget { background-color: #FEF2F2; border: 1px solid #EF4444; border-radius: 12px; }"
    else:
        if is_dark:
            return "QueueItemWidget { background-color: #171922; border: 1px solid rgba(255, 255, 255, 0.07); border-radius: 12px; }"
        else:
            return "QueueItemWidget { background-color: #FFFFFF; border: 1px solid #DCE0E8; border-radius: 12px; }"

_STYLE_CARD_DOWNLOADING = _get_card_style(QueueItemStatus.DOWNLOADING, is_dark=True)
_STYLE_CARD_DONE = _get_card_style(QueueItemStatus.DONE, is_dark=True)

_PROGRESS_STYLE_ORANGE = (
    "QProgressBar {"
    "  background-color: rgba(18, 20, 28, 0.85);"
    "  border: 1px solid rgba(245, 158, 11, 0.4);"
    "  border-radius: 9px;"
    "  text-align: center;"
    "  color: #FFFFFF;"
    "  font-size: 11px;"
    "  font-weight: 700;"
    "  min-height: 18px;"
    "  max-height: 18px;"
    "}"
    "QProgressBar::chunk {"
    "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F59E0B, stop:1 #FB923C);"
    "  border-radius: 8px;"
    "}"
)

_PROGRESS_STYLE_GREEN = (
    "QProgressBar {"
    "  background-color: rgba(18, 20, 28, 0.85);"
    "  border: 1px solid rgba(34, 197, 94, 0.4);"
    "  border-radius: 9px;"
    "  text-align: center;"
    "  color: #FFFFFF;"
    "  font-size: 11px;"
    "  font-weight: 700;"
    "  min-height: 18px;"
    "  max-height: 18px;"
    "}"
    "QProgressBar::chunk {"
    "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10B981, stop:1 #22C55E);"
    "  border-radius: 8px;"
    "}"
)


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
        self._last_item: Optional[QueueItem] = None
        app = QApplication.instance()
        self._is_dark = (app.property("current_theme") != "light") if app else True
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(1)
        self.setMinimumHeight(80)
        self._setup_ui()
        self.update_theme(self._is_dark)
        self.update_from_item(item)

    def update_theme(self, is_dark: bool) -> None:
        """Update queue item styles for dark/light theme."""
        self._is_dark = is_dark
        title_col = "#F3F4F7" if is_dark else "#111318"
        meta_col = "rgba(235, 235, 245, 0.45)" if is_dark else "#5A6170"
        thumb_bg = "rgba(28, 28, 36, 0.5)" if is_dark else "#E9EBF0"
        if hasattr(self, "_title_label"):
            self._title_label.setStyleSheet(f"color: {title_col};")
        if hasattr(self, "_time_label"):
            self._time_label.setStyleSheet(f"color: {meta_col};")
        if hasattr(self, "_quality_label"):
            self._quality_label.setStyleSheet(f"color: {meta_col};")
        if hasattr(self, "_thumbnail") and not self._thumbnail.pixmap():
            self._thumbnail.setStyleSheet(f"background: {thumb_bg}; border-radius: 8px; color: #666;")
        if self._last_item:
            self.setStyleSheet(_get_card_style(self._last_item.status, self._is_dark))

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
        details_row.setSpacing(8)

        # Platform badge (YouTube, Instagram, Facebook, etc.)
        self._platform_badge = QLabel("YOUTUBE")
        self._platform_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._platform_badge.setStyleSheet(
            "background: rgba(239, 68, 68, 0.15); color: #EF4444; "
            "border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 4px; padding: 1px 6px; font-size: 10px;"
        )
        details_row.addWidget(self._platform_badge)

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

        # ── Platform marker badge ─────────────────────────────────────────────
        plat_id = (getattr(item, "platform_id", "") or "").lower()
        if (not plat_id or plat_id == "youtube") and item.url:
            from clipgrab.core.platforms import detect_platform
            p = detect_platform(item.url)
            if p and p.id != "unsupported":
                plat_id = p.id.lower()

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
        self._platform_badge.show()

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

        # Thumbnail
        if item.thumbnail_path and os.path.isfile(item.thumbnail_path):
            pixmap = QPixmap(item.thumbnail_path)
            if not pixmap.isNull():
                self._thumbnail.setPixmap(
                    pixmap.scaled(100, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )

        self._last_item = item

        # ── State Highlighting & Progress ──────────────────────────────────────
        self.setStyleSheet(_get_card_style(item.status, self._is_dark))

        if item.status == QueueItemStatus.DOWNLOADING:
            # Highlight orange for active downloading
            self._status_label.setText("⬇ Downloading")
            self._status_label.setStyleSheet("color: #F59E0B; font-weight: bold;")

            self._progress_bar.setStyleSheet(_PROGRESS_STYLE_ORANGE)
            self._progress_bar.show()
            if item.progress < 0.1 and not item.speed:
                self._progress_bar.setRange(0, 0)
            else:
                self._progress_bar.setRange(0, 100)
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
                self._stats_label.setStyleSheet("color: #FBBF24;")
            elif item.status_text:
                self._stats_label.setText(item.status_text)
                self._stats_label.setStyleSheet("color: #FBBF24;")
            else:
                self._stats_label.setText("Downloading...")
                self._stats_label.setStyleSheet("color: #FBBF24;")
            self._cancel_btn.show()
            self._retry_btn.hide()
            self._remove_btn.hide()
        elif item.status in (QueueItemStatus.DONE, QueueItemStatus.WARNING):
            # Highlight green for downloaded
            self._status_label.setText("✓ Done" if item.status == QueueItemStatus.DONE else "✓ Done (Warn)")
            self._status_label.setStyleSheet("color: #22C55E; font-weight: bold;")

            self._progress_bar.setStyleSheet(_PROGRESS_STYLE_GREEN)
            self._progress_bar.show()
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(100)
            self._progress_bar.setFormat(f"100%  •  {dtype}")
            self._stats_label.show()
            if item.status == QueueItemStatus.WARNING and item.warning_reason:
                self._stats_label.setText(f"Completed: {item.warning_reason}")
                self._stats_label.setStyleSheet("color: #22C55E;")
            else:
                self._stats_label.setText("Completed")
                self._stats_label.setStyleSheet("color: #22C55E;")
            self._cancel_btn.hide()
            self._open_location_btn.show()
            self._remove_btn.show()
        else:
            if item.status == QueueItemStatus.FAILED:
                self._status_label.setText("✕ Failed")
                self._status_label.setStyleSheet("color: #EF4444; font-weight: bold;")
            elif item.status == QueueItemStatus.PENDING:
                self._status_label.setText("⏳ Pending")
                self._status_label.setStyleSheet("color: #F7B731; font-weight: bold;")
            else:
                color, icon = _STATUS_COLORS.get(item.status, ("#888", "?"))
                self._status_label.setText(f"{icon} {item.status.value}")
                self._status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

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
