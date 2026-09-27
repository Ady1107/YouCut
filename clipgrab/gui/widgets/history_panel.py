"""
YouCut — History panel widget.

Displays download history with search/filter, scrollable list,
and per-entry actions (open location, re-download, remove).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.widgets.history_item_widget import HistoryItemWidget
from clipgrab.core.logger import get_logger

logger = get_logger("history_panel")


class HistoryPanel(QWidget):
    """
    Panel displaying download history with search and per-entry actions.

    Signals:
        redownload_requested: (HistoryEntry,) — user wants to re-download with same settings
        status_message: (str,) — for status bar
    """

    redownload_requested = Signal(object)
    status_message = Signal(str)

    def __init__(
        self,
        history_db: HistoryDB,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._db = history_db
        self._item_widgets: list[HistoryItemWidget] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the history panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Header
        header = QHBoxLayout()

        title = QLabel("📜 Download History")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.addWidget(title)

        header.addStretch()

        self._count_label = QLabel("")
        self._count_label.setFont(QFont("Segoe UI", 10))
        self._count_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        header.addWidget(self._count_label)

        layout.addLayout(header)

        # Search bar
        search_row = QHBoxLayout()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍 Search by title...")
        self._search_input.setMinimumHeight(34)
        self._search_input.setFont(QFont("Segoe UI", 10))
        self._search_input.textChanged.connect(self._on_search)
        search_row.addWidget(self._search_input)

        self._refresh_btn = QPushButton("🔄 Refresh")
        self._refresh_btn.setMinimumHeight(34)
        self._refresh_btn.clicked.connect(self.refresh)
        search_row.addWidget(self._refresh_btn)

        layout.addLayout(search_row)

        # Scrollable list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)
        self._list_layout.addStretch()

        # Empty state
        self._empty_label = QLabel(
            "No download history yet.\nCompleted downloads will appear here."
        )
        self._empty_label.setFont(QFont("Segoe UI", 11))
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #666; padding: 40px;")
        self._list_layout.insertWidget(0, self._empty_label)

        scroll.setWidget(self._list_container)
        layout.addWidget(scroll, stretch=1)

    def refresh(self) -> None:
        """Reload the history list from the database."""
        logger.info("[DEBUG] History Refresh button clicked")
        query = self._search_input.text().strip()
        if query:
            entries = self._db.search(query)
        else:
            entries = self._db.get_all()

        self._populate_list(entries)

    def _on_search(self, text: str) -> None:
        """Handle search input changes."""
        text = text.strip()
        if text:
            entries = self._db.search(text)
        else:
            entries = self._db.get_all()
        self._populate_list(entries)

    def _populate_list(self, entries: list[HistoryEntry]) -> None:
        """Replace the list contents with the given entries."""
        # Clear existing widgets
        for widget in self._item_widgets:
            self._list_layout.removeWidget(widget)
            widget.deleteLater()
        self._item_widgets.clear()

        if not entries:
            self._empty_label.show()
            self._count_label.setText("0 entries")
            return

        self._empty_label.hide()
        self._count_label.setText(
            f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'}"
        )

        for entry in entries:
            widget = HistoryItemWidget(entry)
            widget.redownload_clicked.connect(self._on_redownload)
            widget.remove_clicked.connect(self._on_remove)
            self._item_widgets.append(widget)
            # Insert before the stretch
            self._list_layout.insertWidget(
                self._list_layout.count() - 1, widget
            )

    def _on_redownload(self, entry_id: int) -> None:
        """Handle re-download request."""
        entry = self._db.get_entry(entry_id)
        if entry:
            self.redownload_requested.emit(entry)
            self.status_message.emit(f"Re-queued: {entry.title}")

    def _on_remove(self, entry_id: int) -> None:
        """Remove a history entry (does not delete the file)."""
        self._db.delete_entry(entry_id)
        self.refresh()
        self.status_message.emit("Entry removed from history")
