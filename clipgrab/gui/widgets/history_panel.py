"""
YouCut — History panel widget.

Displays download history with search/filter, scrollable list,
and per-entry actions (open location, re-download, remove).
"""
from __future__ import annotations

from clipgrab.gui.icons import get_svg_icon


from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.platforms import detect_platform
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
        """Set up the history panel UI with responsive container sizing."""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Centered container capped at 1320px for big screens
        center_row = QHBoxLayout()
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        container = QWidget()
        container.setMaximumWidth(1320)
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header
        header = QHBoxLayout()

        title = QLabel("Download History")
        title.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header.addWidget(title)

        header.addStretch()

        self._count_label = QLabel("")
        self._count_label.setFont(QFont("Segoe UI", 10))
        self._count_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        header.addWidget(self._count_label)

        layout.addLayout(header)

        # Search and filters row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search by title or URL...")
        self._search_input.setFixedHeight(36)
        self._search_input.setMinimumWidth(160)
        self._search_input.setFont(QFont("Segoe UI", 10))
        self._search_input.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._search_input, stretch=2)

        # Platform filter combo (YouTube, Instagram, Facebook)
        self._platform_filter = QComboBox()
        self._platform_filter.setFixedHeight(36)
        self._platform_filter.setMinimumWidth(130)
        self._platform_filter.addItem("All Platforms", "all")
        self._platform_filter.addItem("YouTube", "youtube")
        self._platform_filter.addItem("Instagram", "instagram")
        self._platform_filter.addItem("Facebook", "facebook")
        self._platform_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._platform_filter)

        # File type filter combo
        self._type_filter = QComboBox()
        self._type_filter.setFixedHeight(36)
        self._type_filter.setMinimumWidth(120)
        self._type_filter.addItem("All Types", "all")
        self._type_filter.addItem("Video", "video")
        self._type_filter.addItem("Audio", "audio")
        self._type_filter.addItem("GIF", "gif")
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._type_filter)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.setIcon(get_svg_icon("refresh", color="#9DA3B4", size=14))
        self._refresh_btn.setFixedHeight(36)
        self._refresh_btn.setMinimumWidth(90)
        self._refresh_btn.clicked.connect(self.refresh)
        filter_row.addWidget(self._refresh_btn)

        layout.addLayout(filter_row)

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

        center_row.addWidget(container)
        outer_layout.addLayout(center_row)

    def refresh(self) -> None:
        """Reload the history list applying current search and filters."""
        logger.info("[DEBUG] History Refresh button clicked")
        self._apply_filters()

    def _on_filter_changed(self) -> None:
        """Handle search text or combo box selection changes."""
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Filter entries from database according to search text, platform, and type."""
        all_entries = self._db.get_all(limit=1000)
        query = self._search_input.text().strip().lower()
        selected_plat = self._platform_filter.currentData() or "all"
        selected_type = self._type_filter.currentData() or "all"

        filtered: list[HistoryEntry] = []

        for entry in all_entries:
            # 1. Search text filter (matches title or URL)
            if query:
                title_match = query in (entry.title or "").lower()
                url_match = query in (entry.url or "").lower()
                if not (title_match or url_match):
                    continue

            # 2. Platform filter
            if selected_plat != "all":
                plat = detect_platform(entry.url)
                p_id = plat.id.lower() if plat else "youtube"
                if selected_plat == "youtube" and p_id != "youtube":
                    continue
                elif selected_plat == "instagram" and p_id != "instagram":
                    continue
                elif selected_plat == "facebook" and p_id != "facebook":
                    continue

            # 3. File type filter
            if selected_type != "all":
                out_lower = (entry.output_path or "").lower()
                vq_lower = (entry.video_quality or "").lower()
                is_gif = out_lower.endswith(".gif") or "gif" in vq_lower
                is_audio = (
                    any(out_lower.endswith(ext) for ext in (".mp3", ".m4a", ".wav", ".aac", ".opus", ".flac"))
                    or "audio only" in vq_lower
                    or (not entry.video_quality and not is_gif)
                )
                is_video = not is_gif and not is_audio

                if selected_type == "video" and not is_video:
                    continue
                elif selected_type == "audio" and not is_audio:
                    continue
                elif selected_type == "gif" and not is_gif:
                    continue

            filtered.append(entry)

        self._populate_list(filtered, total_count=len(all_entries))

    def _populate_list(self, entries: list[HistoryEntry], total_count: Optional[int] = None) -> None:
        """Replace the list contents with the given entries."""
        # Clear existing widgets
        for widget in self._item_widgets:
            self._list_layout.removeWidget(widget)
            widget.deleteLater()
        self._item_widgets.clear()

        if total_count is None:
            total_count = len(entries)

        if not entries:
            self._empty_label.show()
            if total_count > 0:
                self._empty_label.setText("No history entries match your filter criteria.")
                self._count_label.setText(f"0 of {total_count} entries")
            else:
                self._empty_label.setText("No download history yet.\nCompleted downloads will appear here.")
                self._count_label.setText("0 entries")
            return

        self._empty_label.hide()
        if total_count > len(entries):
            self._count_label.setText(f"{len(entries)} of {total_count} entries")
        else:
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
