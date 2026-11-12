"""
YouCut — Navigation Sidebar Widget.

Replaces the top tab bar with a collapsible, multi-platform sidebar
featuring collapsible platform groups, icon-only rail mode toggle,
Queue badge with active/pending count, and brand gradient active states.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.gui.sections import (
    ALL_SECTIONS,
    PLATFORM_GROUPS,
    PlatformGroup,
    SectionConfig,
    VISIBLE_PLATFORM_GROUPS,
    get_section_by_id,
)


class SidebarItemButton(QPushButton):
    """Sub-section navigation button inside a platform group."""

    def __init__(self, section: SectionConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.section = section
        self.setText(f"{section.icon}  {section.title}")
        self.setFont(QFont("Segoe UI", 9))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._is_active = False
        self._update_style()

    def set_active(self, active: bool) -> None:
        if self._is_active != active:
            self._is_active = active
            self._update_style()

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet(
                "QPushButton {"
                "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
                "  color: #ffffff;"
                "  border: none;"
                "  border-radius: 6px;"
                "  font-weight: 600;"
                "  text-align: left;"
                "  padding-left: 18px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "QPushButton {"
                "  background: transparent;"
                "  color: rgba(235, 235, 245, 0.7);"
                "  border: none;"
                "  border-radius: 6px;"
                "  font-weight: 400;"
                "  text-align: left;"
                "  padding-left: 18px;"
                "}"
                "QPushButton:hover {"
                "  background: rgba(255, 255, 255, 0.06);"
                "  color: #ffffff;"
                "}"
            )


class SidebarGroupHeader(QWidget):
    """Clickable header for collapsible platform groups."""

    toggled = Signal(str, bool)  # (group_id, is_expanded)
    clicked = Signal(str)        # group_id

    def __init__(self, group: PlatformGroup, is_expanded: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.group = group
        self.is_expanded = is_expanded
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Arrow indicator
        self._arrow_lbl = QLabel("▼" if self.is_expanded else "▶")
        self._arrow_lbl.setFont(QFont("Segoe UI", 8))
        self._arrow_lbl.setStyleSheet("color: rgba(235, 235, 245, 0.5);")
        layout.addWidget(self._arrow_lbl)

        # Platform Icon
        self._icon_lbl = QLabel(self.group.icon)
        self._icon_lbl.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self._icon_lbl)

        # Platform Name
        self._name_lbl = QLabel(self.group.name)
        self._name_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._name_lbl.setStyleSheet("color: #ebebf5;")
        layout.addWidget(self._name_lbl, stretch=1)

        # Accent color dot
        self._dot = QLabel("●")
        self._dot.setFont(QFont("Segoe UI", 7))
        self._dot.setStyleSheet(f"color: {self.group.accent_color}; margin-right: 4px;")
        layout.addWidget(self._dot)

    def set_expanded(self, expanded: bool) -> None:
        self.is_expanded = expanded
        self._arrow_lbl.setText("▼" if self.is_expanded else "▶")

    def toggle(self) -> None:
        self.set_expanded(not self.is_expanded)
        self.toggled.emit(self.group.id, self.is_expanded)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle()
            self.clicked.emit(self.group.id)
        super().mousePressEvent(event)


class Sidebar(QWidget):
    """
    Main Navigation Sidebar Widget.

    Supports:
    - Full width mode (~210-230px) with collapsible groups
    - Rail mode (~58px) with icon-only navigation
    - Dynamic queue badge with pending/active count
    - Persistent expanded groups & rail state
    """

    navigation_requested = Signal(str, str)  # (page_type, target_id)
    rail_toggled = Signal(bool)              # is_collapsed
    group_toggled = Signal(str, bool)        # (group_id, is_expanded)

    def __init__(
        self,
        collapsed: bool = False,
        expanded_groups: Optional[list[str]] = None,
        last_section: str = "home",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._is_collapsed = collapsed
        self._expanded_groups = set(
            expanded_groups
            if expanded_groups is not None
            else [g.id for g in VISIBLE_PLATFORM_GROUPS]
        )
        self._active_target = last_section or "home"
        self._active_page_type = "home" if self._active_target == "home" else (
            "section" if self._active_target in ALL_SECTIONS else self._active_target
        )

        self._item_buttons: dict[str, SidebarItemButton] = {}
        self._group_headers: dict[str, SidebarGroupHeader] = {}
        self._group_containers: dict[str, QWidget] = {}
        self._rail_buttons: dict[str, QPushButton] = {}
        self._queue_badge_lbl: Optional[QLabel] = None
        self._rail_queue_btn: Optional[QPushButton] = None

        self._setup_ui()
        self.set_collapsed(self._is_collapsed)

    def _setup_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 8, 6, 8)
        main_layout.setSpacing(4)

        # ─── Top Header (Logo + Rail Toggle) ──────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setContentsMargins(4, 2, 4, 6)

        self._brand_lbl = QLabel("✂️ YouCut")
        self._brand_lbl.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._brand_lbl.setStyleSheet(
            "color: #ffffff; letter-spacing: 0.5px;"
        )
        header_row.addWidget(self._brand_lbl, stretch=1)

        self._rail_toggle_btn = QToolButton()
        self._rail_toggle_btn.setText("◀")
        self._rail_toggle_btn.setToolTip("Collapse sidebar to rail (Ctrl+B)")
        self._rail_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_toggle_btn.setFixedSize(26, 26)
        self._rail_toggle_btn.setStyleSheet(
            "QToolButton {"
            "  background: rgba(255, 255, 255, 0.05);"
            "  color: rgba(235, 235, 245, 0.7);"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 4px;"
            "}"
            "QToolButton:hover {"
            "  background: rgba(255, 255, 255, 0.12);"
            "  color: #ffffff;"
            "}"
        )
        self._rail_toggle_btn.clicked.connect(self.toggle_rail)
        header_row.addWidget(self._rail_toggle_btn)
        main_layout.addLayout(header_row)

        # ─── Home Button ──────────────────────────────────────────────────────
        self._home_btn = QPushButton("🏠  Home")
        self._home_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._home_btn.setMinimumHeight(34)
        self._home_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._home_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._home_btn.clicked.connect(lambda: self.select_target("home", "home"))
        main_layout.addWidget(self._home_btn)

        # Divider
        self._div1 = QFrame()
        self._div1.setFrameShape(QFrame.Shape.HLine)
        self._div1.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        self._div1.setFixedHeight(1)
        main_layout.addWidget(self._div1)

        # ─── Scroll Area for Platform Groups ──────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        groups_widget = QWidget()
        self._groups_layout = QVBoxLayout(groups_widget)
        self._groups_layout.setContentsMargins(0, 2, 0, 2)
        self._groups_layout.setSpacing(2)

        for group in VISIBLE_PLATFORM_GROUPS:
            is_exp = group.id in self._expanded_groups
            header = SidebarGroupHeader(group, is_expanded=is_exp)
            header.toggled.connect(self._on_group_toggled)
            self._group_headers[group.id] = header
            self._groups_layout.addWidget(header)

            container = QWidget()
            cont_layout = QVBoxLayout(container)
            cont_layout.setContentsMargins(8, 0, 0, 4)
            cont_layout.setSpacing(1)

            for sec in group.sections:
                btn = SidebarItemButton(sec)
                btn.clicked.connect(
                    lambda checked=False, s_id=sec.id: self.select_target("section", s_id)
                )
                self._item_buttons[sec.id] = btn
                cont_layout.addWidget(btn)

            container.setVisible(is_exp)
            self._group_containers[group.id] = container
            self._groups_layout.addWidget(container)

        self._groups_layout.addStretch()
        self._scroll.setWidget(groups_widget)
        main_layout.addWidget(self._scroll, stretch=1)

        # ─── Rail-only container (shown when collapsed) ───────────────────────
        self._rail_widget = QWidget()
        rail_layout = QVBoxLayout(self._rail_widget)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(6)

        for group in VISIBLE_PLATFORM_GROUPS:
            r_btn = QPushButton(group.icon)
            r_btn.setFont(QFont("Segoe UI Emoji", 13))
            r_btn.setToolTip(f"{group.name} ({group.shortcut})")
            r_btn.setFixedSize(38, 36)
            r_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            primary_sec = group.sections[0].id
            r_btn.clicked.connect(
                lambda checked=False, sid=primary_sec: self.select_target("section", sid)
            )
            r_btn.setStyleSheet(
                "QPushButton {"
                "  background: rgba(255, 255, 255, 0.06);"
                "  border: 1px solid rgba(255, 255, 255, 0.1);"
                "  border-radius: 8px;"
                "  font-size: 14pt;"
                "  color: #ffffff;"
                "}"
                "QPushButton:hover {"
                f"  border-color: {group.accent_color};"
                "  background: rgba(255, 255, 255, 0.14);"
                "}"
            )
            self._rail_buttons[group.id] = r_btn
            rail_layout.addWidget(r_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        rail_layout.addStretch()
        self._rail_widget.hide()
        main_layout.addWidget(self._rail_widget, stretch=1)

        # ─── Divider ──────────────────────────────────────────────────────────
        self._div2 = QFrame()
        self._div2.setFrameShape(QFrame.Shape.HLine)
        self._div2.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        self._div2.setFixedHeight(1)
        main_layout.addWidget(self._div2)

        # ─── Bottom Items (Queue · History · Settings) ────────────────────────
        self._bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(self._bottom_widget)
        bottom_layout.setContentsMargins(0, 2, 0, 0)
        bottom_layout.setSpacing(2)

        # Queue button with badge layout
        queue_container = QWidget()
        q_row = QHBoxLayout(queue_container)
        q_row.setContentsMargins(0, 0, 0, 0)
        q_row.setSpacing(4)

        self._queue_btn = QPushButton("📥  Queue")
        self._queue_btn.setFont(QFont("Segoe UI", 9))
        self._queue_btn.setMinimumHeight(32)
        self._queue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._queue_btn.clicked.connect(lambda: self.select_target("queue", "queue"))
        q_row.addWidget(self._queue_btn, stretch=1)

        self._queue_badge_lbl = QLabel("")
        self._queue_badge_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._queue_badge_lbl.setStyleSheet(
            "background: #E63946; color: white; border-radius: 9px; padding: 1px 6px;"
        )
        self._queue_badge_lbl.hide()
        q_row.addWidget(self._queue_badge_lbl)
        bottom_layout.addWidget(queue_container)

        # History button
        self._history_btn = QPushButton("📜  History")
        self._history_btn.setFont(QFont("Segoe UI", 9))
        self._history_btn.setMinimumHeight(32)
        self._history_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._history_btn.clicked.connect(lambda: self.select_target("history", "history"))
        bottom_layout.addWidget(self._history_btn)

        # Settings button
        self._settings_btn = QPushButton("⚙️  Settings")
        self._settings_btn.setFont(QFont("Segoe UI", 9))
        self._settings_btn.setMinimumHeight(32)
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(lambda: self.select_target("settings", "settings"))
        bottom_layout.addWidget(self._settings_btn)

        main_layout.addWidget(self._bottom_widget)

        # Rail Bottom Icons
        self._rail_bottom = QWidget()
        rail_b_layout = QVBoxLayout(self._rail_bottom)
        rail_b_layout.setContentsMargins(0, 2, 0, 0)
        rail_b_layout.setSpacing(6)

        bottom_rail_style = (
            "QPushButton {"
            "  background: rgba(255, 255, 255, 0.06);"
            "  border: 1px solid rgba(255, 255, 255, 0.1);"
            "  border-radius: 8px;"
            "  font-size: 14pt;"
            "  color: #ffffff;"
            "}"
            "QPushButton:hover { background: rgba(255, 255, 255, 0.14); }"
        )

        self._rail_queue_btn = QPushButton("📥")
        self._rail_queue_btn.setFont(QFont("Segoe UI Emoji", 13))
        self._rail_queue_btn.setToolTip("Queue")
        self._rail_queue_btn.setFixedSize(38, 36)
        self._rail_queue_btn.setStyleSheet(bottom_rail_style)
        self._rail_queue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_queue_btn.clicked.connect(lambda: self.select_target("queue", "queue"))
        rail_b_layout.addWidget(self._rail_queue_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._rail_hist_btn = QPushButton("📜")
        self._rail_hist_btn.setFont(QFont("Segoe UI Emoji", 13))
        self._rail_hist_btn.setToolTip("History")
        self._rail_hist_btn.setFixedSize(38, 36)
        self._rail_hist_btn.setStyleSheet(bottom_rail_style)
        self._rail_hist_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_hist_btn.clicked.connect(lambda: self.select_target("history", "history"))
        rail_b_layout.addWidget(self._rail_hist_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._rail_set_btn = QPushButton("⚙️")
        self._rail_set_btn.setFont(QFont("Segoe UI Emoji", 13))
        self._rail_set_btn.setToolTip("Settings")
        self._rail_set_btn.setFixedSize(38, 36)
        self._rail_set_btn.setStyleSheet(bottom_rail_style)
        self._rail_set_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rail_set_btn.clicked.connect(lambda: self.select_target("settings", "settings"))
        rail_b_layout.addWidget(self._rail_set_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._rail_bottom.hide()
        main_layout.addWidget(self._rail_bottom)

        self._update_selection_styles()

    def set_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand the sidebar to icon-only rail."""
        self._is_collapsed = collapsed
        if collapsed:
            self.setFixedWidth(56)
            self._brand_lbl.hide()
            self._rail_toggle_btn.setText("▶")
            self._home_btn.setText("🏠")
            self._home_btn.setToolTip("Home (Ctrl+0)")
            self._scroll.hide()
            self._rail_widget.show()
            self._bottom_widget.hide()
            self._rail_bottom.show()
        else:
            self.setFixedWidth(225)
            self._brand_lbl.show()
            self._rail_toggle_btn.setText("◀")
            self._home_btn.setText("🏠  Home")
            self._home_btn.setToolTip("")
            for gid, h in self._group_headers.items():
                h.show()
                self._group_containers[gid].setVisible(gid in self._expanded_groups)
            self._scroll.show()
            self._rail_widget.hide()
            self._bottom_widget.show()
            self._rail_bottom.hide()

        self._rail_toggle_btn.setToolTip(
            "Expand sidebar" if collapsed else "Collapse sidebar to rail (Ctrl+B)"
        )
        self.rail_toggled.emit(collapsed)
        self._update_selection_styles()

    def toggle_rail(self) -> None:
        """Toggle between expanded and icon-only rail."""
        self.set_collapsed(not self._is_collapsed)

    def _on_group_toggled(self, group_id: str, expanded: bool) -> None:
        """Handle individual platform group expanding or collapsing."""
        if expanded:
            self._expanded_groups.add(group_id)
        else:
            self._expanded_groups.discard(group_id)

        if group_id in self._group_containers and not self._is_collapsed:
            self._group_containers[group_id].setVisible(expanded)

        self.group_toggled.emit(group_id, expanded)

    def select_target(self, page_type: str, target_id: str) -> None:
        """Set active navigation target and emit navigation signal."""
        self._active_page_type = page_type
        self._active_target = target_id

        # If selecting a section, make sure its parent platform group is expanded
        if page_type == "section" and target_id in ALL_SECTIONS:
            sec = ALL_SECTIONS[target_id]
            if sec.platform_id not in self._expanded_groups:
                self._expanded_groups.add(sec.platform_id)
                if not self._is_collapsed and sec.platform_id in self._group_headers:
                    self._group_headers[sec.platform_id].set_expanded(True)
                    self._group_containers[sec.platform_id].show()
                self.group_toggled.emit(sec.platform_id, True)

        self._update_selection_styles()
        self.navigation_requested.emit(page_type, target_id)

    def set_queue_count(self, count: int) -> None:
        """Update the Queue badge count."""
        if count > 0:
            if self._queue_badge_lbl:
                self._queue_badge_lbl.setText(str(count))
                self._queue_badge_lbl.show()
            if self._rail_queue_btn:
                self._rail_queue_btn.setText(f"📥{count}")
        else:
            if self._queue_badge_lbl:
                self._queue_badge_lbl.hide()
            if self._rail_queue_btn:
                self._rail_queue_btn.setText("📥")

    def get_expanded_groups(self) -> list[str]:
        return list(self._expanded_groups)

    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def get_active_target(self) -> str:
        return self._active_target


    def update_theme(self, theme_name: str) -> None:
        """Update styling dynamically for dark or light theme."""
        self._current_theme = theme_name
        is_dark = (theme_name == "dark")

        # Top brand text
        self._brand_lbl.setStyleSheet(
            "color: #ffffff; letter-spacing: 0.5px;" if is_dark else "color: #181820; letter-spacing: 0.5px;"
        )

        # Dividers
        div_color = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"
        self._div1.setStyleSheet(f"background-color: {div_color};")
        self._div2.setStyleSheet(f"background-color: {div_color};")

        # Group headers
        header_text_color = "#ebebf5" if is_dark else "#181820"
        arrow_color = "rgba(235, 235, 245, 0.5)" if is_dark else "rgba(24, 24, 32, 0.5)"
        for h in self._group_headers.values():
            h._name_lbl.setStyleSheet(f"color: {header_text_color};")
            h._arrow_lbl.setStyleSheet(f"color: {arrow_color};")

        # Rail buttons
        r_bg = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.06)"
        r_border = "rgba(255, 255, 255, 0.15)" if is_dark else "rgba(0, 0, 0, 0.12)"
        r_color = "#ffffff" if is_dark else "#181820"
        for gid, r_btn in self._rail_buttons.items():
            grp = next((g for g in PLATFORM_GROUPS if g.id == gid), None)
            accent = grp.accent_color if grp else "#E63946"
            r_btn.setStyleSheet(
                f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }}"
                f"QPushButton:hover {{ border-color: {accent}; background: rgba(230, 57, 70, 0.18); }}"
            )

        for b_btn in (self._rail_queue_btn, self._rail_hist_btn, self._rail_set_btn):
            if b_btn:
                b_btn.setStyleSheet(
                    f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }}"
                    f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.14) if is_dark else rgba(0, 0, 0, 0.10); }}"
                )

        self._update_selection_styles()

    def _update_selection_styles(self) -> None:
        """Apply active brand gradient style to selected button."""
        is_dark = getattr(self, "_current_theme", "dark") == "dark"
        text_col = "rgba(235, 235, 245, 0.75)" if is_dark else "rgba(24, 24, 32, 0.75)"
        hover_bg = "rgba(255, 255, 255, 0.06)" if is_dark else "rgba(0, 0, 0, 0.05)"
        hover_col = "#ffffff" if is_dark else "#000000"

        gradient_style = (
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
            "  color: #ffffff;"
            "  border: none;"
            "  border-radius: 6px;"
            "  font-weight: 600;"
            "}"
        )
        default_style = (
            f"QPushButton {{"
            f"  background: transparent;"
            f"  color: {text_col};"
            f"  border: none;"
            f"  border-radius: 6px;"
            f"  font-weight: 400;"
            f"  text-align: left;"
            f"  padding-left: 10px;"
            f"}}"
            f"QPushButton:hover {{"
            f"  background: {hover_bg};"
            f"  color: {hover_col};"
            f"}}"
        )

        # Home
        if self._active_page_type == "home":
            if self._is_collapsed:
                self._home_btn.setStyleSheet(
                    "QPushButton {"
                    "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
                    "  color: #ffffff;"
                    "  border: none;"
                    "  border-radius: 6px;"
                    "  font-weight: 600;"
                    "  padding: 0px;"
                    "  text-align: center;"
                    "  font-size: 13pt;"
                    "  font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif;"
                    "}"
                )
            else:
                self._home_btn.setStyleSheet(gradient_style)
        else:
            if self._is_collapsed:
                self._home_btn.setStyleSheet(
                    f"QPushButton {{"
                    f"  background: rgba(255, 255, 255, 0.03);"
                    f"  color: {r_color};"
                    f"  border: 1px solid rgba(255, 255, 255, 0.05);"
                    f"  border-radius: 6px;"
                    f"  font-weight: 500;"
                    f"  padding: 0px;"
                    f"  text-align: center;"
                    f"  font-size: 13pt;"
                    f"  font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif;"
                    f"}}"
                    f"QPushButton:hover {{ background: rgba(255, 255, 255, 0.08); }}"
                )
            else:
                self._home_btn.setStyleSheet(
                    "QPushButton {"
                    "  background: rgba(255, 255, 255, 0.03);"
                    "  color: rgba(235, 235, 245, 0.85);"
                    "  border: 1px solid rgba(255, 255, 255, 0.05);"
                    "  border-radius: 6px;"
                    "  font-weight: 500;"
                    "  text-align: left;"
                    "  padding-left: 10px;"
                    "}"
                    "QPushButton:hover {"
                    "  background: rgba(255, 255, 255, 0.08);"
                    "  color: #ffffff;"
                    "}"
                )

        # Sub-sections
        for sid, btn in self._item_buttons.items():
            btn.set_active(self._active_page_type == "section" and self._active_target == sid)

        # Bottom items
        self._queue_btn.setStyleSheet(
            gradient_style if self._active_page_type == "queue" else default_style
        )
        self._history_btn.setStyleSheet(
            gradient_style if self._active_page_type == "history" else default_style
        )
        self._settings_btn.setStyleSheet(
            gradient_style if self._active_page_type == "settings" else default_style
        )

        # Update rail buttons active states
        rail_gradient_style = (
            "QPushButton {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00);"
            "  color: #ffffff;"
            "  border: none;"
            "  border-radius: 8px;"
            "  font-size: 13pt;"
            "  padding: 0px;"
            "  text-align: center;"
            "  font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif;"
            "}"
        )
        r_bg = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.06)"
        r_border = "rgba(255, 255, 255, 0.15)" if is_dark else "rgba(0, 0, 0, 0.12)"
        r_color = "#ffffff" if is_dark else "#181820"

        active_platform_id = None
        if self._active_page_type == "section" and self._active_target in ALL_SECTIONS:
            active_platform_id = ALL_SECTIONS[self._active_target].platform_id

        for gid, r_btn in self._rail_buttons.items():
            if active_platform_id == gid:
                r_btn.setStyleSheet(rail_gradient_style)
            else:
                grp = next((g for g in PLATFORM_GROUPS if g.id == gid), None)
                accent = grp.accent_color if grp else "#E63946"
                r_btn.setStyleSheet(
                    f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }}"
                    f"QPushButton:hover {{ border-color: {accent}; background: rgba(230, 57, 70, 0.18); }}"
                )

        if self._rail_queue_btn:
            self._rail_queue_btn.setStyleSheet(
                rail_gradient_style if self._active_page_type == "queue" else
                f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }} QPushButton:hover {{ background: rgba(255,255,255,0.14) if is_dark else rgba(0,0,0,0.10); }}"
            )
        if self._rail_hist_btn:
            self._rail_hist_btn.setStyleSheet(
                rail_gradient_style if self._active_page_type == "history" else
                f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }} QPushButton:hover {{ background: rgba(255,255,255,0.14) if is_dark else rgba(0,0,0,0.10); }}"
            )
        if self._rail_set_btn:
            self._rail_set_btn.setStyleSheet(
                rail_gradient_style if self._active_page_type == "settings" else
                f"QPushButton {{ background: {r_bg}; border: 1px solid {r_border}; border-radius: 8px; font-size: 13pt; color: {r_color}; padding: 0px; text-align: center; font-family: 'Segoe UI Emoji', 'Segoe UI', sans-serif; }} QPushButton:hover {{ background: rgba(255,255,255,0.14) if is_dark else rgba(0,0,0,0.10); }}"
            )
