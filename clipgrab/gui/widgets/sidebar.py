"""
YouCut — Modern Sidebar Navigation Widget.

Matches reference design (Images 2-4):
- Logo: coral badge + vector scissors + 'YouCut' title
- Nav Item: Home
- PLATFORMS section label
- 3 Platforms: YouTube (single entry), Instagram, Facebook
- Divider
- Utility items: Queue (with active/pending badge), History, Settings
- Status Pill at bottom-left (Ready to clip, Fetching info, Downloading n of m, Update available)
- Fixed logical width: 190px
- Zero emoji: 100% vector SVG icons from Lucide & Simple Icons
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clipgrab.gui.icons import get_svg_icon, get_svg_pixmap
from clipgrab.gui.theme import (
    BG_APP,
    BG_CARD,
    BG_PILL,
    BORDER_CARD,
    BORDER_DIVIDER,
    COLOR_ACCENT,
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    SIDEBAR_COLLAPSED_WIDTH,
    SIDEBAR_WIDTH,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_font,
)


class SidebarNavButton(QPushButton):
    """Modern pill navigation button for sidebar items supporting expanded and collapsed rail modes."""

    def __init__(
        self,
        nav_id: str,
        title: str,
        icon_name: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.nav_id = nav_id
        self._title = title
        self._icon_name = icon_name
        self._is_active = False
        self._is_collapsed = False

        self.setText(f"  {title}")
        self.setFont(get_font(point_size=10, weight=QFont.Weight.Medium))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_appearance()

    def set_collapsed(self, collapsed: bool) -> None:
        if self._is_collapsed != collapsed:
            self._is_collapsed = collapsed
            if collapsed:
                self.setText("")
                self.setToolTip(self._title)
            else:
                self.setText(f"  {self._title}")
                self.setToolTip("")
            self._update_appearance()

    def set_active(self, active: bool) -> None:
        if self._is_active != active:
            self._is_active = active
            self._update_appearance()

    def is_active(self) -> bool:
        return self._is_active

    def _update_appearance(self) -> None:
        align = "center" if self._is_collapsed else "left"
        pad_left = "0px" if self._is_collapsed else "12px"
        icon_size = 20 if self._is_collapsed else 18

        if self._is_active:
            self.setIcon(get_svg_icon(self._icon_name, color=COLOR_ACCENT, size=icon_size))
            self.setFont(get_font(point_size=10, weight=QFont.Weight.DemiBold))
            self.setStyleSheet(
                "QPushButton {"
                f"  background-color: #171923;"
                f"  color: {TEXT_PRIMARY};"
                f"  border: 1px solid #282C3C;"
                f"  border-radius: 10px;"
                f"  text-align: {align};"
                f"  padding-left: {pad_left};"
                "}"
            )
        else:
            self.setIcon(get_svg_icon(self._icon_name, color=TEXT_SECONDARY, size=icon_size))
            self.setFont(get_font(point_size=10, weight=QFont.Weight.Medium))
            self.setStyleSheet(
                "QPushButton {"
                "  background-color: transparent;"
                f"  color: {TEXT_SECONDARY};"
                "  border: 1px solid transparent;"
                "  border-radius: 10px;"
                f"  text-align: {align};"
                f"  padding-left: {pad_left};"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(255, 255, 255, 0.04);"
                f"  color: {TEXT_PRIMARY};"
                f"  border: 1px solid #1E212D;"
                "}"
            )


class StatusPill(QFrame):
    """
    Bottom-left status pill indicator.
    States:
    - Ready / Ready to clip (green dot)
    - Fetching info... (amber dot)
    - Downloading n of m (accent coral dot)
    - Update available (accent coral dot)
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        # Status dot
        self._dot_lbl = QLabel("●")
        self._dot_lbl.setFont(get_font(point_size=8, weight=QFont.Weight.Bold))
        self._dot_lbl.setStyleSheet(f"color: {COLOR_SUCCESS};")
        layout.addWidget(self._dot_lbl)

        # Status text
        self._text_lbl = QLabel("Ready to clip")
        self._text_lbl.setFont(get_font(point_size=9, weight=QFont.Weight.Medium))
        self._text_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(self._text_lbl, stretch=1)

        self.setStyleSheet(
            "StatusPill {"
            "  background-color: rgba(255, 255, 255, 0.03);"
            f"  border: 1px solid #1E222F;"
            "  border-radius: 16px;"
            "}"
        )

    def set_status(self, text: str, state: str = "ready") -> None:
        """
        Update status message and dot color.
        state: 'ready', 'fetching', 'downloading', 'update', 'error'
        """
        color_map = {
            "ready": COLOR_SUCCESS,
            "fetching": COLOR_WARNING,
            "downloading": COLOR_ACCENT,
            "update": COLOR_ACCENT,
            "error": COLOR_ERROR,
        }
        dot_color = color_map.get(state, COLOR_SUCCESS)
        self._dot_lbl.setStyleSheet(f"color: {dot_color};")
        self._text_lbl.setText(text)

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse status pill to dot only with tooltip."""
        if collapsed:
            self._text_lbl.hide()
            self.setToolTip(self._text_lbl.text())
        else:
            self._text_lbl.show()
            self.setToolTip("")


class Sidebar(QWidget):
    """
    Main Sidebar navigation component matching reference design.
    Emits navigation_requested(page_id: str).
    """

    navigation_requested = Signal(str)  # page_id ("home", "youtube", "instagram", "facebook", "queue", "history", "settings")

    def __init__(
        self,
        last_section: str = "home",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedWidth(SIDEBAR_WIDTH)
        self._active_id = "home"
        self._nav_buttons: dict[str, SidebarNavButton] = {}

        self._setup_ui()
        self.select_page(last_section if last_section in self._nav_buttons else "home")

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)

        # ── 1. Top Logo ──────────────────────────────────────────────────────
        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(2, 0, 4, 14)
        logo_row.setSpacing(10)

        # Coral logo square with white scissors icon
        logo_badge = QLabel()
        logo_badge.setFixedSize(30, 30)
        logo_badge.setStyleSheet(
            f"background-color: {COLOR_ACCENT}; border-radius: 8px;"
        )
        logo_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_badge.setPixmap(get_svg_pixmap("scissors", color="#FFFFFF", size=18))
        logo_row.addWidget(logo_badge)

        self._logo_title = QLabel("YouCut")
        self._logo_title.setFont(get_font(point_size=12, weight=QFont.Weight.Bold))
        self._logo_title.setStyleSheet(f"color: {TEXT_PRIMARY}; letter-spacing: 0.3px;")
        logo_row.addWidget(self._logo_title, stretch=1)

        layout.addLayout(logo_row)

        # ── 2. Home Button ───────────────────────────────────────────────────
        home_btn = SidebarNavButton("home", "Home", "home")
        home_btn.clicked.connect(lambda: self.select_page("home"))
        self._nav_buttons["home"] = home_btn
        layout.addWidget(home_btn)

        layout.addSpacing(10)

        # ── 3. PLATFORMS Section Label ───────────────────────────────────────
        self._platforms_lbl = QLabel("PLATFORMS")
        self._platforms_lbl.setFont(get_font(point_size=8, weight=QFont.Weight.Bold))
        self._platforms_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; letter-spacing: 1.2px; padding-left: 6px;"
        )
        layout.addWidget(self._platforms_lbl)
        layout.addSpacing(2)

        # ── 4. Platform Items (YouTube, Instagram, Facebook) ─────────────────
        platform_items = [
            ("youtube", "YouTube", "youtube"),
            ("instagram", "Instagram", "instagram"),
            ("facebook", "Facebook", "facebook"),
        ]
        for p_id, p_name, p_icon in platform_items:
            btn = SidebarNavButton(p_id, p_name, p_icon)
            btn.clicked.connect(lambda checked=False, pid=p_id: self.select_page(pid))
            self._nav_buttons[p_id] = btn
            layout.addWidget(btn)

        layout.addSpacing(12)

        # ── 5. Divider ───────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background-color: {BORDER_DIVIDER};")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        layout.addSpacing(8)

        # ── 6. Bottom Navigation (Queue, History, Settings) ──────────────────
        # Queue with badge
        queue_container = QWidget()
        q_layout = QHBoxLayout(queue_container)
        q_layout.setContentsMargins(0, 0, 0, 0)
        q_layout.setSpacing(4)

        queue_btn = SidebarNavButton("queue", "Queue", "queue")
        queue_btn.clicked.connect(lambda: self.select_page("queue"))
        self._nav_buttons["queue"] = queue_btn
        q_layout.addWidget(queue_btn, stretch=1)

        self._queue_badge = QLabel("")
        self._queue_badge.setFont(get_font(point_size=8, weight=QFont.Weight.Bold))
        self._queue_badge.setStyleSheet(
            f"background-color: {COLOR_ACCENT}; color: #FFFFFF; border-radius: 9px; padding: 2px 7px;"
        )
        self._queue_badge.hide()
        q_layout.addWidget(self._queue_badge)
        layout.addWidget(queue_container)

        # History
        hist_btn = SidebarNavButton("history", "History", "history")
        hist_btn.clicked.connect(lambda: self.select_page("history"))
        self._nav_buttons["history"] = hist_btn
        layout.addWidget(hist_btn)

        # Settings
        set_btn = SidebarNavButton("settings", "Settings", "settings")
        set_btn.clicked.connect(lambda: self.select_page("settings"))
        self._nav_buttons["settings"] = set_btn
        layout.addWidget(set_btn)

        # ── 7. Flexible Spacer ───────────────────────────────────────────────
        layout.addStretch(1)

        # ── 8. Status Pill at bottom-left ────────────────────────────────────
        self._status_pill = StatusPill()
        layout.addWidget(self._status_pill)

    def select_page(self, page_id: str, notify: bool = True) -> None:
        """Select active page and emit navigation signal if notify is True."""
        # Normalize target ID (e.g. if a sub-section was requested)
        if page_id.startswith("youtube"):
            page_id = "youtube"
        elif page_id.startswith("instagram"):
            page_id = "instagram"
        elif page_id.startswith("facebook"):
            page_id = "facebook"

        changed = (self._active_id != page_id)
        self._active_id = page_id
        for nav_id, btn in self._nav_buttons.items():
            btn.set_active(nav_id == page_id)

        if notify and changed:
            self.navigation_requested.emit(page_id)

    def get_active_page(self) -> str:
        return self._active_id

    def set_collapsed(self, collapsed: bool) -> None:
        """Toggle between expanded (190px) and collapsed rail (64px) mode."""
        self.setFixedWidth(SIDEBAR_COLLAPSED_WIDTH if collapsed else SIDEBAR_WIDTH)
        if hasattr(self, "_logo_title"):
            self._logo_title.setVisible(not collapsed)
        if hasattr(self, "_platforms_lbl"):
            self._platforms_lbl.setVisible(not collapsed)
        if hasattr(self, "_status_pill"):
            self._status_pill.set_collapsed(collapsed)
        for btn in self._nav_buttons.values():
            btn.set_collapsed(collapsed)
        pad = 6 if collapsed else 12
        if self.layout():
            self.layout().setContentsMargins(pad, 16, pad, 16)

    def is_collapsed(self) -> bool:
        return self.width() <= SIDEBAR_COLLAPSED_WIDTH

    def set_queue_count(self, count: int) -> None:
        """Update active/pending badge on Queue button."""
        if count > 0:
            self._queue_badge.setText(str(count))
            self._queue_badge.show()
        else:
            self._queue_badge.hide()

    def set_status(self, text: str, state: str = "ready") -> None:
        """Update bottom-left status pill."""
        self._status_pill.set_status(text, state=state)

    def update_theme(self, theme_name: str) -> None:
        """Stub for theme updates — dark theme active."""
        pass
