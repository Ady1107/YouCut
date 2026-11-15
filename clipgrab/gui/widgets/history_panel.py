"""
YouCut — History panel widget.

Displays download history with search/filter, scrollable list,
and per-entry actions (open location, re-download, remove).
"""
from __future__ import annotations

from clipgrab.gui.icons import get_svg_icon


from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
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
from clipgrab.core.thumbnail_service import ThumbnailService
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.widgets.history_item_widget import HistoryItemWidget
from clipgrab.gui.widgets.toast_notification import show_toast
from clipgrab.core.logger import get_logger

logger = get_logger("history_panel")


class ClearHistoryDialog(QDialog):
    """
    Confirmation dialog for clearing history with optional deletion of downloaded files.
    """

    def __init__(
        self,
        count: int,
        is_dark: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clear All History")
        self.setModal(True)
        self.setFixedWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title_lbl = QLabel("Clear all history?")
        title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(f"This removes all {count} entries from History.")
        desc_lbl.setFont(QFont("Segoe UI", 10))
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        self._delete_files_cb = QCheckBox("Also delete the downloaded files from disk")
        self._delete_files_cb.setFont(QFont("Segoe UI", 9))
        self._delete_files_cb.setChecked(False)  # UNCHECKED by default
        layout.addWidget(self._delete_files_cb)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setFont(QFont("Segoe UI", 9))
        cancel_btn.setFixedHeight(34)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        clear_btn = QPushButton("Clear all")
        clear_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        clear_btn.setFixedHeight(34)
        clear_btn.clicked.connect(self.accept)
        btn_row.addWidget(clear_btn)

        layout.addLayout(btn_row)

        bg = "#181A24" if is_dark else "#FFFFFF"
        text_col = "#F3F4F7" if is_dark else "#111318"
        desc_col = "#9DA3B4" if is_dark else "#5A6170"
        border_col = "rgba(255, 255, 255, 0.1)" if is_dark else "#DCE0E8"
        cancel_bg = "#232736" if is_dark else "#F0F2F6"
        cancel_border = "#2E3346" if is_dark else "#CCD1DC"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {bg};
                border: 1px solid {border_col};
                border-radius: 12px;
            }}
            QLabel {{
                color: {text_col};
            }}
            QCheckBox {{
                color: {text_col};
                spacing: 8px;
            }}
            QPushButton {{
                padding: 0 16px;
                border-radius: 6px;
            }}
        """)
        desc_lbl.setStyleSheet(f"color: {desc_col};")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {cancel_bg};
                color: {text_col};
                border: 1px solid {cancel_border};
            }}
            QPushButton:hover {{
                background-color: {"#2A2F42" if is_dark else "#E4E7EE"};
            }}
        """)
        clear_btn.setStyleSheet("""
            QPushButton {{
                background-color: #EF4444;
                color: #FFFFFF;
                border: none;
            }}
            QPushButton:hover {{
                background-color: #DC2626;
            }}
        """)

    def should_delete_files(self) -> bool:
        return self._delete_files_cb.isChecked()


class HistoryPanel(QWidget):
    """
    Panel displaying download history with search and per-entry actions.

    Signals:
        redownload_requested: (HistoryEntry,) — user wants to re-download with same settings
        status_message: (str,) — for status bar
    """

    redownload_requested = Signal(object)
    status_message = Signal(str)
    history_cleared = Signal()

    def __init__(
        self,
        history_db: HistoryDB,
        active_paths_provider: Optional[Callable[[], set[str]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._db = history_db
        self._active_paths_provider = active_paths_provider
        self._is_dark = True
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

        self._clear_all_btn = QPushButton("Clear all")
        self._clear_all_btn.setIcon(get_svg_icon("trash", color="#EF4444", size=14))
        self._clear_all_btn.setFixedHeight(36)
        self._clear_all_btn.setMinimumWidth(95)
        self._clear_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_all_btn.clicked.connect(self._on_clear_all_clicked)
        filter_row.addWidget(self._clear_all_btn)

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

        # Update Clear all button enabled state
        has_items = self._db.get_count() > 0
        if hasattr(self, "_clear_all_btn"):
            self._clear_all_btn.setEnabled(has_items)

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
            widget = HistoryItemWidget(entry, db=self._db)
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
        ThumbnailService.get_instance().invalidate_entry(entry_id)
        self._db.delete_entry(entry_id)
        self.refresh()
        self.status_message.emit("Entry removed from history")

    def _on_clear_all_clicked(self) -> None:
        """Prompt confirmation and clear all download history with optional file deletion."""
        total_count = self._db.get_count()
        if total_count == 0:
            return

        dialog = ClearHistoryDialog(count=total_count, is_dark=self._is_dark, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        delete_files = dialog.should_delete_files()

        # Retrieve active paths from provider so running/queued downloads are strictly preserved
        active_paths: set[str] = set()
        if self._active_paths_provider:
            try:
                active_paths = self._active_paths_provider()
            except Exception as e:
                logger.warning("Error fetching active paths: %s", e)

        try:
            entries_deleted, files_deleted = self._db.clear_all(
                delete_files=delete_files,
                active_paths=active_paths,
            )
            # Invalidate all cached thumbnails
            ThumbnailService.get_instance().clear_cache()

            # Refresh local UI
            self.refresh()
            self._count_label.setText("0 entries")
            if hasattr(self, "_clear_all_btn"):
                self._clear_all_btn.setEnabled(False)

            # Signal to refresh Home Recent Downloads
            self.history_cleared.emit()

            if delete_files:
                msg = f"History cleared ({files_deleted} file{'s' if files_deleted != 1 else ''} deleted from disk)"
            else:
                msg = "History cleared"

            show_toast(self, msg, toast_type="success")
            self.status_message.emit(msg)

        except Exception as e:
            logger.error("Failed to clear history: %s", e)
            show_toast(self, f"Error clearing history: {e}", toast_type="error")

    def update_theme(self, theme_name: str) -> None:
        """Update history panel elements for dark/light theme."""
        is_dark = (theme_name == "dark")
        self._is_dark = is_dark
        count_col = "rgba(235, 235, 245, 0.45)" if is_dark else "#5A6170"
        empty_col = "#666666" if is_dark else "#8A92A2"
        if hasattr(self, "_count_label"):
            self._count_label.setStyleSheet(f"color: {count_col};")
        if hasattr(self, "_empty_label"):
            self._empty_label.setStyleSheet(f"color: {empty_col}; padding: 40px;")

        danger_bg = "rgba(239, 68, 68, 0.10)" if is_dark else "rgba(239, 68, 68, 0.08)"
        danger_border = "rgba(239, 68, 68, 0.35)" if is_dark else "rgba(239, 68, 68, 0.45)"
        danger_hover = "rgba(239, 68, 68, 0.20)" if is_dark else "rgba(239, 68, 68, 0.15)"
        danger_hover_border = "rgba(239, 68, 68, 0.55)" if is_dark else "rgba(239, 68, 68, 0.65)"
        disabled_col = "rgba(157, 163, 180, 0.35)" if is_dark else "#B8BEC9"
        disabled_border = "rgba(157, 163, 180, 0.2)" if is_dark else "#DCE0E8"

        if hasattr(self, "_clear_all_btn"):
            self._clear_all_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {danger_bg};
                    color: #EF4444;
                    border: 1px solid {danger_border};
                    border-radius: 6px;
                    font-weight: 600;
                    font-size: 11px;
                    padding: 0 10px;
                }}
                QPushButton:hover {{
                    background-color: {danger_hover};
                    border: 1px solid {danger_hover_border};
                }}
                QPushButton:disabled {{
                    background-color: transparent;
                    color: {disabled_col};
                    border: 1px solid {disabled_border};
                }}
            """)

        for widget in self._item_widgets:
            widget.update_theme(is_dark)
