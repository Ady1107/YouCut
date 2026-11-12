"""
YouCut — Universal Home Page (Paste & Routing Hub).

Provides:
- Universal paste & Go URL box
- Auto-routing to matching sub-sections with animated detection chip
- Unrecognized URL manual platform selector
- Clickable platform quick-tiles
- Recent downloads from SQLite history with Open File and Re-download actions
- Full Dark & Light theme adaptation
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import DownloadEngine, detect_platform
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.sections import (
    SECTION_FACEBOOK_VIDEO,
    SECTION_INSTAGRAM_REEL,
    SECTION_PINTEREST_IMAGE,
    SECTION_X_VIDEO,
    SECTION_YOUTUBE_VIDEO,
    route_url_to_section,
)

logger = get_logger("home_page")


class PlatformTileCard(QFrame):
    """Clickable platform quick-tile card."""

    clicked = Signal(str)  # primary_section_id

    def __init__(
        self,
        name: str,
        icon: str,
        description: str,
        accent_color: str,
        section_id: str,
        is_dark: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.section_id = section_id
        self.accent_color = accent_color
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(76)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        icon_lbl = QLabel(icon)
        icon_lbl.setFont(QFont("Segoe UI", 20))
        layout.addWidget(icon_lbl)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self._name_lbl = QLabel(name)
        self._name_lbl.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        text_layout.addWidget(self._name_lbl)

        self._desc_lbl = QLabel(description)
        self._desc_lbl.setFont(QFont("Segoe UI", 8))
        self._desc_lbl.setWordWrap(True)
        text_layout.addWidget(self._desc_lbl)

        layout.addLayout(text_layout, stretch=1)
        self.update_theme(is_dark)

    def update_theme(self, is_dark: bool) -> None:
        bg = "rgba(28, 28, 36, 0.9)" if is_dark else "rgba(255, 255, 255, 0.95)"
        border = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.1)"
        hover_bg = "rgba(45, 45, 58, 0.98)" if is_dark else "rgba(240, 240, 248, 0.98)"
        name_col = "#ffffff" if is_dark else "#181820"
        desc_col = "rgba(235, 235, 245, 0.7)" if is_dark else "rgba(24, 24, 32, 0.7)"

        self.setStyleSheet(
            f"PlatformTileCard {{"
            f"  background: {bg};"
            f"  border: 1px solid {border};"
            f"  border-radius: 10px;"
            f"}}"
            f"PlatformTileCard:hover {{"
            f"  border: 1px solid {self.accent_color};"
            f"  background: {hover_bg};"
            f"}}"
        )
        self._name_lbl.setStyleSheet(f"color: {name_col};")
        self._desc_lbl.setStyleSheet(f"color: {desc_col};")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.section_id)
        super().mousePressEvent(event)


class RecentDownloadCard(QFrame):
    """Recent download history item card with Open File and Re-download actions."""

    redownload_clicked = Signal(str)  # url
    open_file_clicked = Signal(str)   # output_path

    def __init__(self, entry: HistoryEntry, is_dark: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.entry = entry
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(54)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # File icon
        icon_lbl = QLabel("🎬" if not entry.audio_quality or entry.video_quality else "🎵")
        icon_lbl.setFont(QFont("Segoe UI", 13))
        layout.addWidget(icon_lbl)

        # Title & metadata
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        self._title_lbl = QLabel(entry.title or entry.url)
        self._title_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        info_layout.addWidget(self._title_lbl)

        details = []
        if entry.video_quality and entry.video_quality != "Best available":
            details.append(entry.video_quality)
        elif entry.audio_quality:
            details.append(entry.audio_quality)
        if entry.file_size > 0:
            mb = entry.file_size / (1024 * 1024)
            details.append(f"{mb:.1f} MB")
        if entry.downloaded_at:
            details.append(entry.downloaded_at[:16])

        self._meta_lbl = QLabel(" · ".join(details) if details else entry.url[:60])
        self._meta_lbl.setFont(QFont("Segoe UI", 8))
        info_layout.addWidget(self._meta_lbl)

        layout.addLayout(info_layout, stretch=1)

        # Open File Button
        open_btn = QPushButton("📁 Open")
        open_btn.setFont(QFont("Segoe UI", 8))
        open_btn.setMinimumHeight(28)
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(lambda: self.open_file_clicked.emit(entry.output_path))
        layout.addWidget(open_btn)

        # Re-download Button
        redownload_btn = QPushButton("🔄 Re-download")
        redownload_btn.setFont(QFont("Segoe UI", 8))
        redownload_btn.setMinimumHeight(28)
        redownload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        redownload_btn.clicked.connect(lambda: self.redownload_clicked.emit(entry.url))
        layout.addWidget(redownload_btn)

        self.update_theme(is_dark)

    def update_theme(self, is_dark: bool) -> None:
        bg = "rgba(28, 28, 36, 0.85)" if is_dark else "rgba(255, 255, 255, 0.95)"
        border = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.1)"
        hover_bg = "rgba(42, 42, 54, 0.95)" if is_dark else "rgba(240, 240, 248, 0.98)"
        t_col = "#ffffff" if is_dark else "#181820"
        m_col = "rgba(235, 235, 245, 0.6)" if is_dark else "rgba(24, 24, 32, 0.65)"

        self.setStyleSheet(
            f"RecentDownloadCard {{ background: {bg}; border: 1px solid {border}; border-radius: 8px; }}"
            f"RecentDownloadCard:hover {{ background: {hover_bg}; }}"
        )
        self._title_lbl.setStyleSheet(f"color: {t_col};")
        self._meta_lbl.setStyleSheet(f"color: {m_col};")


class HomePage(QWidget):
    """
    Universal Home Page Widget.

    Signals:
        route_requested: (section_id, url, auto_fetch)
        status_message: (message_str)
    """

    route_requested = Signal(str, str, bool)
    status_message = Signal(str)

    def __init__(self, history_db: HistoryDB, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._history_db = history_db
        self._is_dark = True
        self._tile_cards: list[PlatformTileCard] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        scroll_content = QWidget()
        scroll_layout = QHBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        # Centered container constrained to 1050px
        container = QWidget()
        container.setMaximumWidth(1050)
        self._main_layout = QVBoxLayout(container)
        self._main_layout.setContentsMargins(20, 16, 20, 20)
        self._main_layout.setSpacing(14)
        self._main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # ─── 1. Hero Header ───────────────────────────────────────────────────
        hero_layout = QVBoxLayout()
        hero_layout.setSpacing(4)
        hero_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel("Universal Media Downloader & Clipper")
        self._title_lbl.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self._title_lbl)

        self._sub_lbl = QLabel("Paste any YouTube, Instagram or Facebook link")
        self._sub_lbl.setFont(QFont("Segoe UI", 10))
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero_layout.addWidget(self._sub_lbl)

        self._main_layout.addLayout(hero_layout)

        # ─── 2. Universal Paste Box ───────────────────────────────────────────
        self._url_card = QFrame()
        url_layout = QVBoxLayout(self._url_card)
        url_layout.setContentsMargins(12, 10, 12, 10)
        url_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(
            "Paste link here... (e.g. youtube.com/watch?v=..., instagram.com/reel/..., pin.it/...)"
        )
        self._url_input.setFont(QFont("Segoe UI", 10))
        self._url_input.setMinimumHeight(42)
        self._url_input.returnPressed.connect(self._on_go_clicked)
        self._url_input.textChanged.connect(self._on_text_changed)
        input_row.addWidget(self._url_input, stretch=1)

        paste_btn = QPushButton("📋 Paste")
        paste_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        paste_btn.setMinimumHeight(42)
        paste_btn.setMinimumWidth(92)
        paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        paste_btn.clicked.connect(self._on_paste_clicked)
        input_row.addWidget(paste_btn)

        self._go_btn = QPushButton("Go ➜")
        self._go_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._go_btn.setMinimumHeight(42)
        self._go_btn.setFixedWidth(96)
        self._go_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._go_btn.setStyleSheet(
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
            "  color: white;"
            "  border: none;"
            "  border-radius: 8px;"
            "  font-weight: 700;"
            "}"
            "QPushButton:hover {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #D12D3A, stop:1 #E06F00);"
            "}"
        )
        self._go_btn.clicked.connect(self._on_go_clicked)
        input_row.addWidget(self._go_btn)

        url_layout.addLayout(input_row)

        # Detection chip label (initially hidden)
        self._chip_lbl = QLabel("")
        self._chip_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._chip_lbl.setStyleSheet(
            "color: #ffffff; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
            "border-radius: 6px; padding: 4px 10px;"
        )
        self._chip_lbl.hide()
        url_layout.addWidget(self._chip_lbl)

        # Unrecognized platform fallback container (initially hidden)
        self._unrecognized_box = QFrame()
        self._unrecognized_box.setStyleSheet(
            "QFrame {"
            "  background: rgba(230, 57, 70, 0.08);"
            "  border: 1px dashed rgba(230, 57, 70, 0.4);"
            "  border-radius: 8px;"
            "}"
        )
        unrec_layout = QVBoxLayout(self._unrecognized_box)
        unrec_layout.setContentsMargins(12, 10, 12, 10)
        unrec_layout.setSpacing(8)

        unrec_msg = QLabel("⚠️ Couldn't recognize this link format. Choose a platform to try anyway:")
        unrec_msg.setFont(QFont("Segoe UI", 9))
        unrec_layout.addWidget(unrec_msg)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)

        platforms_data = [
            ("▶️ YouTube", SECTION_YOUTUBE_VIDEO.id),
            ("📸 Instagram", SECTION_INSTAGRAM_REEL.id),
            ("📘 Facebook", SECTION_FACEBOOK_VIDEO.id),
        ]
        for p_name, p_sec in platforms_data:
            p_btn = QPushButton(p_name)
            p_btn.setFont(QFont("Segoe UI", 9))
            p_btn.setMinimumHeight(30)
            p_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            p_btn.clicked.connect(
                lambda checked=False, s=p_sec: self._send_to_section(s)
            )
            btns_row.addWidget(p_btn)

        unrec_layout.addLayout(btns_row)
        self._unrecognized_box.hide()
        url_layout.addWidget(self._unrecognized_box)

        self._main_layout.addWidget(self._url_card)

        # ─── 3. Platform Quick-Tiles ──────────────────────────────────────────
        self._explore_header = QLabel("Explore Platforms")
        self._explore_header.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._main_layout.addWidget(self._explore_header)

        tiles_widget = QWidget()
        tiles_grid = QGridLayout(tiles_widget)
        tiles_grid.setContentsMargins(0, 0, 0, 0)
        tiles_grid.setSpacing(10)

        tiles_info = [
            ("YouTube", "▶️", "Videos, Shorts & Clips", "#E63946", SECTION_YOUTUBE_VIDEO.id),
            ("Instagram", "📸", "Reels, Posts, Stories & Carousels", "#E1306C", SECTION_INSTAGRAM_REEL.id),
            ("Facebook", "📘", "Watch, Reels, Page Videos & Shares", "#1877F2", SECTION_FACEBOOK_VIDEO.id),
        ]

        self._tile_cards.clear()
        for i, (p_name, p_icon, p_desc, p_color, p_sec) in enumerate(tiles_info):
            card = PlatformTileCard(p_name, p_icon, p_desc, p_color, p_sec, is_dark=self._is_dark, parent=tiles_widget)
            card.clicked.connect(self._on_tile_clicked)
            self._tile_cards.append(card)
            row = i // 2
            col = i % 2
            if i == 2:
                tiles_grid.addWidget(card, row, col, 1, 2)
            else:
                tiles_grid.addWidget(card, row, col)

        self._main_layout.addWidget(tiles_widget)

        # ─── 4. Recent Downloads Section ──────────────────────────────────────
        recent_header_row = QHBoxLayout()
        self._recent_header = QLabel("Recent Downloads")
        self._recent_header.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        recent_header_row.addWidget(self._recent_header)
        recent_header_row.addStretch()

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setFont(QFont("Segoe UI", 8))
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self.refresh_recent_downloads)
        recent_header_row.addWidget(refresh_btn)
        self._main_layout.addLayout(recent_header_row)

        self._recent_container_widget = QWidget()
        self._recent_layout = QVBoxLayout(self._recent_container_widget)
        self._recent_layout.setContentsMargins(0, 0, 0, 0)
        self._recent_layout.setSpacing(6)
        self._main_layout.addWidget(self._recent_container_widget)

        self._main_layout.addStretch()

        scroll_layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignHCenter)
        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll)

        self.update_theme("dark")

    def set_url(self, url: str) -> None:
        self._url_input.setText(url)

    def get_url(self) -> str:
        return self._url_input.text().strip()

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text().strip()
            if text:
                self._url_input.setText(text)
                self._process_url(text)

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._unrecognized_box.hide()
            self._chip_lbl.hide()

    def _on_go_clicked(self) -> None:
        url = self.get_url()
        if url:
            self._process_url(url)

    def _process_url(self, url: str) -> None:
        platform = detect_platform(url)

        if platform.engine == DownloadEngine.UNSUPPORTED:
            self._chip_lbl.hide()
            self._unrecognized_box.show()
            self.status_message.emit("Couldn't recognize link platform.")
            return

        self._unrecognized_box.hide()
        target_sec, desc = route_url_to_section(url)

        self._chip_lbl.setText(f"✓ Detected: {desc} — Opening section...")
        self._chip_lbl.show()

        QTimer.singleShot(250, lambda: self._emit_route(target_sec.id, url))

    def _emit_route(self, section_id: str, url: str) -> None:
        self._chip_lbl.hide()
        self._url_input.clear()
        self.route_requested.emit(section_id, url, True)

    def _send_to_section(self, section_id: str) -> None:
        url = self.get_url()
        self._unrecognized_box.hide()
        self._url_input.clear()
        self.route_requested.emit(section_id, url, True)

    def _on_tile_clicked(self, section_id: str) -> None:
        self.route_requested.emit(section_id, "", False)

    def refresh_recent_downloads(self) -> None:
        """Reload the last 5 downloads cleanly with synchronous unparenting."""
        while self._recent_layout.count():
            item = self._recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        entries = self._history_db.get_all(limit=5)
        if not entries:
            empty_lbl = QLabel("No downloads yet. Paste a link above to get started!")
            empty_lbl.setFont(QFont("Segoe UI", 9))
            empty_lbl.setStyleSheet("color: rgba(235, 235, 245, 0.45); padding: 8px 4px;")
            self._recent_layout.addWidget(empty_lbl)
            return

        for entry in entries:
            card = RecentDownloadCard(entry, is_dark=self._is_dark, parent=self._recent_container_widget)
            card.redownload_clicked.connect(self._on_redownload_entry)
            card.open_file_clicked.connect(self._on_open_file_entry)
            self._recent_layout.addWidget(card)

    def _on_redownload_entry(self, url: str) -> None:
        target_sec, _ = route_url_to_section(url)
        self.route_requested.emit(target_sec.id, url, True)

    def _on_open_file_entry(self, output_path: str) -> None:
        if not output_path:
            self.status_message.emit("No file path recorded.")
            return

        if os.path.isfile(output_path):
            try:
                os.startfile(output_path)
            except Exception as e:
                logger.warning("Error opening file %s: %s", output_path, e)
                try:
                    subprocess.run(["explorer", f"/select,{output_path}"], check=False)
                except Exception:
                    pass
        elif os.path.isdir(os.path.dirname(output_path)):
            try:
                os.startfile(os.path.dirname(output_path))
            except Exception as e:
                logger.warning("Error opening folder: %s", e)
        else:
            self.status_message.emit(f"File not found on disk: {os.path.basename(output_path)}")

    def update_theme(self, theme_name: str) -> None:
        """Update Home page elements for dark/light theme."""
        self._is_dark = (theme_name == "dark")
        text_col = "#ffffff" if self._is_dark else "#181820"
        sub_col = "rgba(235, 235, 245, 0.7)" if self._is_dark else "rgba(24, 24, 32, 0.65)"
        card_bg = "rgba(24, 24, 32, 0.95)" if self._is_dark else "rgba(255, 255, 255, 0.95)"
        border_col = "rgba(255, 255, 255, 0.1)" if self._is_dark else "rgba(0, 0, 0, 0.1)"

        self._title_lbl.setStyleSheet(f"color: {text_col};")
        self._sub_lbl.setStyleSheet(f"color: {sub_col};")
        self._explore_header.setStyleSheet(f"color: {text_col}; margin-top: 2px;")
        self._recent_header.setStyleSheet(f"color: {text_col}; margin-top: 4px;")

        self._url_card.setStyleSheet(
            f"QFrame {{ background: {card_bg}; border: 1px solid {border_col}; border-radius: 12px; }}"
        )

        for card in self._tile_cards:
            card.update_theme(self._is_dark)

        self.refresh_recent_downloads()
