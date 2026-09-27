"""
YouCut — Queue panel widget.

Displays the download queue as a scrollable list of QueueItemWidgets,
with controls for starting, stopping, and clearing the queue.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import os
import subprocess

from clipgrab.core.queue_manager import QueueManager
from clipgrab.gui.widgets.queue_item_widget import QueueItemWidget
from clipgrab.core.logger import get_logger

logger = get_logger("queue_panel")


class QueuePanel(QWidget):
    """
    Panel displaying the download queue with controls.

    Signals:
        queue_action: (action_str,) — for status bar updates
    """

    queue_action = Signal(str)

    def __init__(
        self,
        queue_manager: QueueManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._queue_manager = queue_manager
        self._item_widgets: dict[str, QueueItemWidget] = {}
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        """Set up the queue panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header with controls
        header = QHBoxLayout()

        title = QLabel("📋 Download Queue")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.addWidget(title)

        header.addStretch()

        self._count_label = QLabel("0 items")
        self._count_label.setFont(QFont("Segoe UI", 10))
        self._count_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        header.addWidget(self._count_label)

        layout.addLayout(header)

        # Control buttons
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._start_btn = QPushButton("▶ Start Queue")
        self._start_btn.setMinimumHeight(34)
        self._start_btn.setFont(QFont("Segoe UI", 10))
        self._start_btn.clicked.connect(self._on_start_clicked)
        controls.addWidget(self._start_btn)

        self._stop_btn = QPushButton("⏸ Stop")
        self._stop_btn.setMinimumHeight(34)
        self._stop_btn.setFont(QFont("Segoe UI", 10))
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop_clicked)
        controls.addWidget(self._stop_btn)

        self._clear_btn = QPushButton("🗑 Clear Completed")
        self._clear_btn.setMinimumHeight(34)
        self._clear_btn.setFont(QFont("Segoe UI", 10))
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        controls.addWidget(self._clear_btn)

        self._cancel_all_btn = QPushButton("✗ Cancel All")
        self._cancel_all_btn.setMinimumHeight(34)
        self._cancel_all_btn.setFont(QFont("Segoe UI", 10))
        self._cancel_all_btn.clicked.connect(self._on_cancel_all_clicked)
        controls.addWidget(self._cancel_all_btn)

        layout.addLayout(controls)

        # Scrollable list area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()

        # Empty state message
        self._empty_label = QLabel("No items in queue.\nAdd clips from the Download tab.")
        self._empty_label.setFont(QFont("Segoe UI", 11))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #666; padding: 40px;")
        self._list_layout.insertWidget(0, self._empty_label)

        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, stretch=1)

    def _connect_signals(self) -> None:
        """Connect queue manager signals."""
        self._queue_manager.item_added.connect(self._on_item_added)
        self._queue_manager.item_removed.connect(self._on_item_removed)
        self._queue_manager.item_updated.connect(self._on_item_updated)
        self._queue_manager.queue_started.connect(
            lambda: self._update_controls(processing=True)
        )
        self._queue_manager.queue_finished.connect(
            lambda: self._update_controls(processing=False)
        )

    def _on_item_added(self, index: int) -> None:
        """Handle a new item being added to the queue."""
        item = self._queue_manager.get_item(index)
        if item is None:
            return

        widget = QueueItemWidget(item)
        widget.remove_clicked.connect(self._on_remove_clicked)
        widget.retry_clicked.connect(self._on_retry_clicked)
        widget.cancel_clicked.connect(self._on_cancel_item_clicked)
        widget.resume_clicked.connect(self._on_resume_clicked)
        widget.open_location_clicked.connect(self._on_open_location_clicked)

        self._item_widgets[item.id] = widget
        # Insert before the stretch
        self._list_layout.insertWidget(self._list_layout.count() - 1, widget)

        self._empty_label.hide()
        self._update_count()

    def _on_item_removed(self, index: int) -> None:
        """Handle an item being removed from the queue."""
        # Find and remove the widget
        items = self._queue_manager.get_items()
        current_ids = {item.id for item in items}

        for item_id in list(self._item_widgets.keys()):
            if item_id not in current_ids:
                widget = self._item_widgets.pop(item_id)
                self._list_layout.removeWidget(widget)
                widget.deleteLater()

        self._update_count()
        if not self._item_widgets:
            self._empty_label.show()

    def _on_item_updated(self, index: int) -> None:
        """Handle an item's state being updated."""
        item = self._queue_manager.get_item(index)
        if item is None:
            return

        widget = self._item_widgets.get(item.id)
        if widget:
            widget.update_from_item(item)

    def _on_start_clicked(self) -> None:
        """Start processing the queue."""
        logger.info("[DEBUG] Start button clicked")
        self._queue_manager.start_processing()
        self._update_controls(processing=True)
        self.queue_action.emit("Queue processing started")

    def _on_stop_clicked(self) -> None:
        """Stop processing the queue."""
        logger.info("[DEBUG] Stop button clicked")
        self._queue_manager.stop_processing()
        self._update_controls(processing=False)
        self.queue_action.emit("Queue processing stopped")

    def _on_clear_clicked(self) -> None:
        """Clear completed/failed/cancelled items."""
        logger.info("[DEBUG] Clear Completed button clicked")
        self._queue_manager.clear_completed()
        self.queue_action.emit("Completed items cleared")

    def _on_cancel_all_clicked(self) -> None:
        """Cancel all items."""
        logger.info("[DEBUG] Cancel All button clicked")
        self._queue_manager.cancel_all()
        self._update_controls(processing=False)
        self.queue_action.emit("All items cancelled")

    def _on_remove_clicked(self, item_id: str) -> None:
        """Remove a specific item."""
        items = self._queue_manager.get_items()
        for i, item in enumerate(items):
            if item.id == item_id:
                self._queue_manager.remove_item(i)
                break

    def _on_retry_clicked(self, item_id: str) -> None:
        """Retry a failed/cancelled item."""
        items = self._queue_manager.get_items()
        for i, item in enumerate(items):
            if item.id == item_id:
                self._queue_manager.retry_item(i)
                if not self._queue_manager.is_processing:
                    self._queue_manager.start_processing()
                    self._update_controls(processing=True)
                break

    def _on_cancel_item_clicked(self, item_id: str) -> None:
        """Cancel a specific item."""
        logger.info("[DEBUG] Per-item Cancel button clicked for %s", item_id)
        items = self._queue_manager.get_items()
        for i, item in enumerate(items):
            if item.id == item_id:
                self._queue_manager.cancel_item(i)
                break

    def _on_resume_clicked(self, item_id: str) -> None:
        """Resume a stopped item."""
        logger.info("[DEBUG] Resume button clicked for %s", item_id)
        items = self._queue_manager.get_items()
        for i, item in enumerate(items):
            if item.id == item_id:
                self._queue_manager.resume_item(i)
                break

    def _on_open_location_clicked(self, item_id: str) -> None:
        """Open file location in Explorer."""
        items = self._queue_manager.get_items()
        for item in items:
            if item.id == item_id:
                if item.output_path and os.path.exists(item.output_path):
                    subprocess.run(["explorer", "/select,", os.path.normpath(item.output_path)], check=False)
                else:
                    QMessageBox.warning(self, "File Not Found", "The downloaded file could not be found. It may have been moved or deleted.")
                break

    def _update_controls(self, processing: bool) -> None:
        """Update button states based on processing status."""
        self._start_btn.setEnabled(not processing)
        self._stop_btn.setEnabled(processing)

    def _update_count(self) -> None:
        """Update the item count label."""
        count = self._queue_manager.item_count()
        self._count_label.setText(f"{count} item{'s' if count != 1 else ''}")
