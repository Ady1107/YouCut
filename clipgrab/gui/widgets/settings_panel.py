"""
YouCut — Modern Settings Panel Widget.

Redesigned to match the reference design specifications:
  - Top Updates section with 3 component cards: YouCut, yt-dlp, FFmpeg.
    Each includes icon tile, version, status pill badge with dot, auto-update toggle switch,
    progress bar, and contextual action buttons.
  - Two-column lower section:
      Left: Appearance (interactive Dark / Light / System preview cards)
            Downloads (output folder with write validation, fragments stepper [− 16 +],
            default video format, default trim mode)
      Right: About (YouCut version, yt-dlp version, FFmpeg build string in monospace,
             Copy diagnostics with query-string redaction)
  - Seamless dark, light, and system theme support.
  - Fully wrapped in a smooth, responsive QScrollArea.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
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

from clipgrab.config.settings import AppSettings
from clipgrab.core.ffmpeg_utils import verify_ffmpeg
from clipgrab.core.logger import get_logger
from clipgrab.core.updater import (
    UpdateManager,
    get_installed_ffmpeg_version,
    get_installed_ytdlp_version,
)
from clipgrab.gui.theme import (
    BG_APP,
    BG_APP_LIGHT,
    BG_CARD,
    BG_CARD_LIGHT,
    BORDER_CARD,
    BORDER_CARD_LIGHT,
    COLOR_ACCENT,
    COLOR_ACCENT_HOVER,
    COLOR_SUCCESS,
    TEXT_MUTED,
    TEXT_MUTED_LIGHT,
    TEXT_PRIMARY,
    TEXT_PRIMARY_LIGHT,
    TEXT_SECONDARY,
    TEXT_SECONDARY_LIGHT,
    resolve_theme,
)
from clipgrab.gui.widgets.toast_notification import show_toast
from clipgrab import version as version_mod
from clipgrab.version import APP_VERSION

logger = get_logger("settings_panel")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOM REUSABLE UI COMPONENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QToggleSwitch(QWidget):
    """
    Pill-shaped toggle switch (44x24px) with smooth circular thumb.
    Accent coral (#FF5B4A) when ON, subtle dark/light track when OFF.
    """

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._checked = bool(checked)
        self._is_dark = True

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked != checked:
            self._checked = checked
            self.update()
            self.toggled.emit(self._checked)

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            self.setChecked(not self._checked)
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track colors
        if self._checked:
            track_color = QColor(COLOR_ACCENT)
        else:
            track_color = QColor("#232733") if self._is_dark else QColor("#DCE0E8")

        # Draw rounded pill track
        track_rect = QRectF(0, 0, 44, 24)
        path = QPainterPath()
        path.addRoundedRect(track_rect, 12, 12)
        painter.fillPath(path, track_color)

        # Draw circular white thumb (diameter 18px, 3px padding)
        thumb_x = 23.0 if self._checked else 3.0
        thumb_rect = QRectF(thumb_x, 3.0, 18.0, 18.0)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(thumb_rect)


class StatusBadge(QFrame):
    """
    Status badge pill with a 6px status dot and label.
    Supports states: "up_to_date", "updating", "available", "checking", "error".
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(8, 2, 8, 2)
        self._layout.setSpacing(6)

        self._dot = QLabel("●")
        self._dot.setFont(QFont("Segoe UI", 8))
        self._layout.addWidget(self._dot)

        self._text = QLabel("Up to date")
        self._text.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._layout.addWidget(self._text)

        self.set_status("up_to_date")

    def set_status(self, kind: str, custom_text: Optional[str] = None) -> None:
        if kind == "up_to_date":
            dot_color = "#3DDC97"
            text_color = "#3DDC97"
            bg_color = "rgba(61, 220, 151, 0.12)"
            label = custom_text or "Up to date"
        elif kind == "updating":
            dot_color = "#F59E0B"
            text_color = "#F59E0B"
            bg_color = "rgba(245, 158, 11, 0.12)"
            label = custom_text or "Updating"
        elif kind == "available":
            dot_color = "#FF5B4A"
            text_color = "#FF8C7E"
            bg_color = "rgba(255, 91, 74, 0.14)"
            label = custom_text or "Update available"
        elif kind == "error":
            dot_color = "#EF4444"
            text_color = "#EF4444"
            bg_color = "rgba(239, 68, 68, 0.12)"
            label = custom_text or "Couldn't check"
        else:  # "checking"
            dot_color = "#9BA1B0"
            text_color = "#9BA1B0"
            bg_color = "rgba(155, 161, 176, 0.12)"
            label = custom_text or "Checking..."

        self._dot.setStyleSheet(f"color: {dot_color};")
        self._text.setText(label)
        self._text.setStyleSheet(f"color: {text_color};")
        self.setStyleSheet(
            f"QFrame {{ background-color: {bg_color}; border-radius: 12px; border: none; }}"
        )


class SlimProgressBar(QWidget):
    """
    4px high sleek progress bar with accent fill.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self._value: int = 0
        self._is_dark: bool = True

    def value(self) -> int:
        return self._value

    def setValue(self, val: int) -> None:
        self._value = max(0, min(100, val))
        self.update()

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_color = QColor("#232733") if self._is_dark else QColor("#E2E5EC")
        track_rect = QRectF(0, 0, self.width(), 4)
        painter.setBrush(track_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(track_rect, 2, 2)

        if self._value > 0:
            fill_w = max(4.0, (self.width() * self._value) / 100.0)
            fill_rect = QRectF(0, 0, fill_w, 4)
            painter.setBrush(QColor(COLOR_ACCENT))
            painter.drawRoundedRect(fill_rect, 2, 2)


class StepperButton(QPushButton):
    """
    36x36 button with crisp vector minus or plus drawn via QPainter.
    """

    def __init__(self, mode: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.mode = mode  # "minus" or "plus"
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._is_dark = True
        self.set_theme(True)

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        btn_bg = "#1A1D27" if is_dark else "#F0F2F6"
        btn_hover = "#242838" if is_dark else "#E5E8EF"
        btn_border = "#2A2E3B" if is_dark else "#CCD1DC"
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 10px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:disabled {{
                opacity: 0.35;
            }}
        """)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.isEnabled():
            col = QColor(120, 120, 120, 90)
        else:
            col = QColor("#F3F4F7") if self._is_dark else QColor("#111318")

        pen = QPen(col, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        cx = self.width() / 2.0
        cy = self.height() / 2.0
        arm = 5.0

        # Minus: horizontal vector line
        painter.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))

        # Plus: also vertical vector line
        if self.mode == "plus":
            painter.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))


class FragmentStepper(QWidget):
    """
    Compact stepper [ − ] 16 [ + ] (36x36 buttons, crisp vector −/+ drawing via QPainter, range 1-32).
    """

    valueChanged = Signal(int)

    def __init__(self, initial_val: int = 16, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._value = max(1, min(32, initial_val))
        self._is_dark = True
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._btn_minus = StepperButton("minus")
        self._btn_minus.clicked.connect(self._on_minus)
        layout.addWidget(self._btn_minus)

        self._val_label = QLabel(str(self._value))
        self._val_label.setFixedSize(40, 36)
        self._val_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        layout.addWidget(self._val_label)

        self._btn_plus = StepperButton("plus")
        self._btn_plus.clicked.connect(self._on_plus)
        layout.addWidget(self._btn_plus)

        self.set_theme(True)
        self._update_buttons()

    def value(self) -> int:
        return self._value

    def setValue(self, val: int) -> None:
        val = max(1, min(32, val))
        if self._value != val:
            self._value = val
            self._val_label.setText(str(self._value))
            self._update_buttons()
            self.valueChanged.emit(self._value)

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._btn_minus.set_theme(is_dark)
        self._btn_plus.set_theme(is_dark)
        lbl_color = "#F3F4F7" if is_dark else "#111318"
        self._val_label.setStyleSheet(f"color: {lbl_color}; font-weight: 700; background: transparent; border: none;")

    def _on_minus(self) -> None:
        if self._value > 1:
            self.setValue(self._value - 1)

    def _on_plus(self) -> None:
        if self._value < 32:
            self.setValue(self._value + 1)

    def _update_buttons(self) -> None:
        self._btn_minus.setEnabled(self._value > 1)
        self._btn_plus.setEnabled(self._value < 32)


class ThemePreviewCard(QFrame):
    """
    Card containing a miniature UI mockup and a radio selection indicator.
    Supports 'dark', 'light', and 'system' themes.
    """

    selected = Signal(str)

    def __init__(self, theme_key: str, label_text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.theme_key = theme_key
        self.label_text = label_text
        self._is_selected: bool = False
        self._is_app_dark: bool = True

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Miniature preview box
        self._preview_box = QFrame()
        self._preview_box.setFixedHeight(62)
        self._setup_preview_box()
        layout.addWidget(self._preview_box)

        # Radio row
        radio_row = QHBoxLayout()
        radio_row.setContentsMargins(2, 0, 2, 0)
        radio_row.setSpacing(6)

        self._radio_dot = QLabel("○")
        self._radio_dot.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        radio_row.addWidget(self._radio_dot)

        self._radio_label = QLabel(label_text)
        self._radio_label.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        radio_row.addWidget(self._radio_label)
        radio_row.addStretch()

        layout.addLayout(radio_row)
        self._refresh_styles()

    def _setup_preview_box(self) -> None:
        box_layout = QVBoxLayout(self._preview_box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        box_layout.setSpacing(6)

        top_line = QFrame()
        top_line.setFixedHeight(4)
        top_line.setFixedWidth(24)
        top_line.setStyleSheet(f"background-color: {COLOR_ACCENT}; border-radius: 2px;")
        box_layout.addWidget(top_line)

        bar = QFrame()
        bar.setFixedHeight(8)
        bar.setStyleSheet("background-color: rgba(140, 145, 160, 0.25); border-radius: 3px;")
        box_layout.addWidget(bar)
        box_layout.addStretch()

    def set_selected(self, is_selected: bool) -> None:
        self._is_selected = is_selected
        self._refresh_styles()

    def set_app_theme(self, is_dark: bool) -> None:
        self._is_app_dark = is_dark
        self._refresh_styles()

    def _refresh_styles(self) -> None:
        # Style miniature preview box
        if self.theme_key == "dark":
            self._preview_box.setStyleSheet(
                "background-color: #101218; border-radius: 8px; border: 1px solid #232733;"
            )
        elif self.theme_key == "light":
            self._preview_box.setStyleSheet(
                "background-color: #FFFFFF; border-radius: 8px; border: 1px solid #DCE0E8;"
            )
        else:  # "system" split half dark / half light
            self._preview_box.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #101218, stop:0.5 #101218, stop:0.501 #FFFFFF, stop:1 #FFFFFF);"
                "border-radius: 8px; border: 1px solid #232733;"
            )

        card_bg = "#1A1D27" if self._is_app_dark else "#F8F9FA"
        text_color = "#F3F4F7" if self._is_app_dark else "#111318"

        if self._is_selected:
            border_style = f"2px solid {COLOR_ACCENT}"
            self._radio_dot.setText("◉")
            self._radio_dot.setStyleSheet(f"color: {COLOR_ACCENT};")
        else:
            border_color = "#2A2E3B" if self._is_app_dark else "#DCE0E8"
            border_style = f"1px solid {border_color}"
            self._radio_dot.setText("○")
            self._radio_dot.setStyleSheet("color: #6F7585;" if self._is_app_dark else "color: #8A92A2;")

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {card_bg};
                border: {border_style};
                border-radius: 14px;
            }}
        """)
        self._radio_label.setStyleSheet(f"color: {text_color}; border: none; background: transparent;")
        self._radio_dot.setStyleSheet(self._radio_dot.styleSheet() + "border: none; background: transparent;")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.theme_key)
            event.accept()
        else:
            super().mousePressEvent(event)


class ComponentUpdateCard(QFrame):
    """
    Card for updating a specific component (YouCut, yt-dlp, or FFmpeg).
    """

    action_clicked = Signal(str)       # "check", "update", "cancel", "retry"
    auto_update_toggled = Signal(bool)
    whats_new_clicked = Signal(str)

    def __init__(
        self,
        title: str,
        icon_type: str,
        initial_version: str,
        toggle_subtitle: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.title = title
        self.icon_type = icon_type
        self._whats_new_url = ""
        self._current_state = "up_to_date"
        self._is_dark = True

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        # ── Top Row: Icon + Name/Version + StatusBadge ────────────────────────
        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self._icon_tile = QLabel()
        self._icon_tile.setFixedSize(42, 42)
        self._icon_tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_tile.setFont(QFont("Segoe UI Emoji", 14, QFont.Weight.Bold))
        self._setup_icon_tile()
        top_row.addWidget(self._icon_tile)

        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        self._name_label = QLabel(title)
        self._name_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        name_col.addWidget(self._name_label)

        self._version_label = QLabel(initial_version)
        self._version_label.setFont(QFont("Segoe UI", 9))
        name_col.addWidget(self._version_label)
        top_row.addLayout(name_col)

        top_row.addStretch()

        self._badge = StatusBadge()
        top_row.addWidget(self._badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top_row)

        # ── Status info & progress row ────────────────────────────────────────
        self._info_col = QVBoxLayout()
        self._info_col.setSpacing(4)

        info_header_row = QHBoxLayout()
        self._status_text = QLabel("✓ You're on the latest version.")
        self._status_text.setFont(QFont("Segoe UI", 9))
        info_header_row.addWidget(self._status_text)
        info_header_row.addStretch()

        self._whats_new_btn = QPushButton("What's new")
        self._whats_new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._whats_new_btn.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        self._whats_new_btn.setStyleSheet(f"color: {COLOR_ACCENT}; border: none; background: transparent;")
        self._whats_new_btn.clicked.connect(self._on_whats_new)
        self._whats_new_btn.hide()
        info_header_row.addWidget(self._whats_new_btn)
        self._info_col.addLayout(info_header_row)

        # Progress bar (hidden by default)
        self._progress_bar = SlimProgressBar()
        self._progress_bar.hide()
        self._info_col.addWidget(self._progress_bar)

        self._progress_label = QLabel("")
        self._progress_label.setFont(QFont("Segoe UI", 8))
        self._progress_label.hide()
        self._info_col.addWidget(self._progress_label)

        layout.addLayout(self._info_col)

        # ── Toggle Switch Row ─────────────────────────────────────────────────
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(10)

        self._toggle = QToggleSwitch(checked=True)
        self._toggle.toggled.connect(self.auto_update_toggled.emit)
        toggle_row.addWidget(self._toggle, alignment=Qt.AlignmentFlag.AlignVCenter)

        toggle_text_col = QVBoxLayout()
        toggle_text_col.setSpacing(1)
        self._toggle_title = QLabel("Update automatically on startup")
        self._toggle_title.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        toggle_text_col.addWidget(self._toggle_title)

        self._toggle_subtitle = QLabel(toggle_subtitle)
        self._toggle_subtitle.setFont(QFont("Segoe UI", 8))
        toggle_text_col.addWidget(self._toggle_subtitle)
        toggle_row.addLayout(toggle_text_col)
        toggle_row.addStretch()

        layout.addLayout(toggle_row)

        # ── Bottom Action Button ──────────────────────────────────────────────
        self._action_btn = QPushButton("Check for updates")
        self._action_btn.setFixedHeight(38)
        self._action_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.clicked.connect(self._on_action_clicked)
        layout.addWidget(self._action_btn)

        self.set_state("up_to_date")

    def _setup_icon_tile(self) -> None:
        if self.icon_type == "youcut":
            self._icon_tile.setText("✂")
            self._icon_tile.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #FF6D5D, stop:1 #E64837);"
                f"color: white; border-radius: 12px; border: none;"
            )
        elif self.icon_type == "ytdlp":
            self._icon_tile.setText("↓")
            self._icon_tile.setStyleSheet(
                "background-color: #1A1D27; color: #F3F4F7; border-radius: 12px; border: 1px solid #2A2E3B;"
            )
        else:  # "ffmpeg"
            self._icon_tile.setText("🎞")
            self._icon_tile.setStyleSheet(
                "background-color: #1A1D27; color: #F3F4F7; border-radius: 12px; border: 1px solid #2A2E3B;"
            )

    def set_version(self, text: str) -> None:
        self._version_label.setText(text)

    def set_status_message(self, text: str) -> None:
        self._status_text.setText(text)

    def set_progress(self, percent: int, speed_str: str, version_tag: str = "") -> None:
        self._progress_bar.show()
        self._progress_bar.setValue(percent)
        self._progress_label.show()
        prefix = f"Downloading {version_tag} · " if version_tag else "Downloading · "
        self._progress_label.setText(f"{prefix}{percent}% · {speed_str}")

    def set_whats_new_url(self, url: str) -> None:
        self._whats_new_url = url
        if url:
            self._whats_new_btn.show()
        else:
            self._whats_new_btn.hide()

    def set_toggle_checked(self, checked: bool) -> None:
        self._toggle.setChecked(checked)

    def set_state(self, state: str, custom_info: Optional[str] = None) -> None:
        self._current_state = state
        self._badge.set_status(state)

        if state == "up_to_date":
            self._progress_bar.hide()
            self._progress_label.hide()
            self._status_text.setText(custom_info or "✓ You're on the latest version.")
            self._action_btn.setText("🔄 Check for updates")
            self._set_button_style("secondary")
        elif state == "available":
            self._progress_bar.hide()
            self._progress_label.hide()
            self._status_text.setText(custom_info or "New build available")
            self._action_btn.setText("⬇ Update now")
            self._set_button_style("primary")
        elif state == "updating":
            self._progress_bar.show()
            self._progress_label.show()
            self._status_text.setText(custom_info or "Updating component...")
            self._action_btn.setText("✕ Cancel update")
            self._set_button_style("secondary")
        elif state == "error":
            self._progress_bar.hide()
            self._progress_label.hide()
            self._status_text.setText(custom_info or "Couldn't reach update server.")
            self._action_btn.setText("🔄 Retry check")
            self._set_button_style("secondary")
        else:  # "checking"
            self._progress_bar.hide()
            self._progress_label.hide()
            self._status_text.setText("Checking for updates...")
            self._action_btn.setText("⏳ Checking...")
            self._set_button_style("secondary")

    def _set_button_style(self, kind: str) -> None:
        if kind == "primary":
            self._action_btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF6D5D, stop:1 #E64837);
                    color: white;
                    border: none;
                    border-radius: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF7E70, stop:1 #EF5545);
                }}
            """)
        else:
            bg = "#1A1D27" if self._is_dark else "#F0F2F6"
            hover = "#242838" if self._is_dark else "#E5E8EF"
            border = "#2A2E3B" if self._is_dark else "#CCD1DC"
            text = "#F3F4F7" if self._is_dark else "#111318"
            self._action_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg};
                    color: {text};
                    border: 1px solid {border};
                    border-radius: 12px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background-color: {hover};
                }}
            """)

    def set_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._toggle.set_theme(is_dark)
        self._progress_bar.set_theme(is_dark)

        card_bg = BG_CARD if is_dark else BG_CARD_LIGHT
        card_border = BORDER_CARD if is_dark else BORDER_CARD_LIGHT
        text_primary = "#F3F4F7" if is_dark else "#111318"
        text_muted = "#9BA1B0" if is_dark else "#5A6170"

        self.setStyleSheet(f"""
            ComponentUpdateCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 20px;
            }}
        """)
        self._name_label.setStyleSheet(f"color: {text_primary}; border: none; background: transparent;")
        self._version_label.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")
        self._status_text.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")
        self._progress_label.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")
        self._toggle_title.setStyleSheet(f"color: {text_primary}; border: none; background: transparent;")
        self._toggle_subtitle.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")

        if self.icon_type != "youcut":
            tile_bg = "#1A1D27" if is_dark else "#F0F2F6"
            tile_border = "#2A2E3B" if is_dark else "#CCD1DC"
            tile_color = "#F3F4F7" if is_dark else "#111318"
            self._icon_tile.setStyleSheet(
                f"background-color: {tile_bg}; color: {tile_color}; border-radius: 12px; border: 1px solid {tile_border};"
            )

        btn_kind = "primary" if self._current_state == "available" else "secondary"
        self._set_button_style(btn_kind)

    def _on_action_clicked(self) -> None:
        if self._current_state == "updating":
            self.action_clicked.emit("cancel")
        elif self._current_state == "available":
            self.action_clicked.emit("update")
        elif self._current_state == "error":
            self.action_clicked.emit("retry")
        else:
            self.action_clicked.emit("check")

    def _on_whats_new(self) -> None:
        if self._whats_new_url:
            QDesktopServices.openUrl(QUrl(self._whats_new_url))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SETTINGS PANEL MAIN WIDGET
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SettingsPanel(QWidget):
    """
    Settings panel matching the new 1440x1100 reference design.

    Signals:
        theme_toggle_requested: ()
        theme_changed: (str) — "dark", "light", or "system"
        settings_changed: ()
    """

    theme_toggle_requested = Signal()
    theme_changed = Signal(str)
    settings_changed = Signal()

    def __init__(
        self,
        settings: AppSettings,
        update_manager: UpdateManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._update_manager = update_manager
        self._is_dark: bool = True
        self._app_download_url: str = ""

        self._setup_ui()
        self._connect_signals()
        self._load_from_settings()

    def _setup_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Scroll area container
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Centered container for max-width bounding (1100px)
        self._scroll_container = QWidget()
        scroll_container_layout = QHBoxLayout(self._scroll_container)
        scroll_container_layout.setContentsMargins(0, 0, 0, 0)
        scroll_container_layout.setSpacing(0)

        self._content_widget = QWidget()
        self._content_widget.setMaximumWidth(1100)
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setContentsMargins(32, 28, 32, 32)
        self._content_layout.setSpacing(24)

        scroll_container_layout.addStretch(1)
        scroll_container_layout.addWidget(self._content_widget, stretch=0)
        scroll_container_layout.addStretch(1)

        # ── Page Header ───────────────────────────────────────────────────────
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)

        self._title_label = QLabel("Settings")
        self._title_label.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        header_layout.addWidget(self._title_label)

        self._subtitle_label = QLabel("Make YouCut work the way you like.")
        self._subtitle_label.setFont(QFont("Segoe UI", 11))
        header_layout.addWidget(self._subtitle_label)

        self._content_layout.addLayout(header_layout)

        # ── Section 1: Updates ────────────────────────────────────────────────
        updates_header_row = QHBoxLayout()
        updates_header_row.setSpacing(12)

        updates_title_col = QVBoxLayout()
        updates_title_col.setSpacing(2)
        self._updates_title = QLabel("Updates")
        self._updates_title.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        updates_title_col.addWidget(self._updates_title)

        self._updates_sub = QLabel("Keep YouCut, yt-dlp and FFmpeg current so downloads keep working.")
        self._updates_sub.setFont(QFont("Segoe UI", 10))
        updates_title_col.addWidget(self._updates_sub)
        updates_header_row.addLayout(updates_title_col)

        updates_header_row.addStretch()

        self._last_checked_label = QLabel("Checked today, 14:02")
        self._last_checked_label.setFont(QFont("Segoe UI", 9))
        updates_header_row.addWidget(self._last_checked_label)

        self._check_all_btn = QPushButton("🔄 Check all for updates")
        self._check_all_btn.setFixedHeight(38)
        self._check_all_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._check_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._check_all_btn.clicked.connect(self._on_check_all_clicked)
        updates_header_row.addWidget(self._check_all_btn)

        self._content_layout.addLayout(updates_header_row)

        # Update cards (3 columns)
        self._update_cards_layout = QHBoxLayout()
        self._update_cards_layout.setSpacing(18)

        # Card 1: YouCut
        self._card_youcut = ComponentUpdateCard(
            title="YouCut",
            icon_type="youcut",
            initial_version=f"Version {APP_VERSION}",
            toggle_subtitle="The app checks each time it starts",
        )
        self._card_youcut.action_clicked.connect(self._on_youcut_action)
        self._card_youcut.auto_update_toggled.connect(self._on_youcut_toggle)
        self._update_cards_layout.addWidget(self._card_youcut, 1)

        # Card 2: yt-dlp
        ytdlp_ver = get_installed_ytdlp_version()
        self._card_ytdlp = ComponentUpdateCard(
            title="yt-dlp",
            icon_type="ytdlp",
            initial_version=f"Version {ytdlp_ver}",
            toggle_subtitle="Recommended, YouTube changes often",
        )
        self._card_ytdlp.action_clicked.connect(self._on_ytdlp_action)
        self._card_ytdlp.auto_update_toggled.connect(self._on_ytdlp_toggle)
        self._update_cards_layout.addWidget(self._card_ytdlp, 1)

        # Card 3: FFmpeg
        ffmpeg_ver = get_installed_ffmpeg_version()
        ffmpeg_disp = ffmpeg_ver if len(ffmpeg_ver) < 22 else ffmpeg_ver[:20] + ".."
        self._card_ffmpeg = ComponentUpdateCard(
            title="FFmpeg",
            icon_type="ffmpeg",
            initial_version=ffmpeg_disp,
            toggle_subtitle="The app checks each time it starts",
        )
        self._card_ffmpeg.action_clicked.connect(self._on_ffmpeg_action)
        self._card_ffmpeg.auto_update_toggled.connect(self._on_ffmpeg_toggle)
        self._update_cards_layout.addWidget(self._card_ffmpeg, 1)

        self._content_layout.addLayout(self._update_cards_layout)

        # ── Section 2: Two Equal Columns ──────────────────────────────────────
        self._two_col_layout = QHBoxLayout()
        self._two_col_layout.setSpacing(20)

        # ──── Left Column: Appearance + Downloads ─────────────────────────────
        self._left_col = QVBoxLayout()
        self._left_col.setSpacing(20)

        # Appearance Card
        self._appearance_card = QFrame()
        self._setup_appearance_card()
        self._left_col.addWidget(self._appearance_card)

        # Downloads Card
        self._downloads_card = QFrame()
        self._setup_downloads_card()
        self._left_col.addWidget(self._downloads_card)

        self._two_col_layout.addLayout(self._left_col, stretch=1)

        # ──── Right Column: About Card ────────────────────────────────────────
        self._right_col = QVBoxLayout()
        self._right_col.setSpacing(20)

        self._about_card = QFrame()
        self._setup_about_card()
        self._right_col.addWidget(self._about_card)
        self._right_col.addStretch()

        self._two_col_layout.addLayout(self._right_col, stretch=1)

        self._content_layout.addLayout(self._two_col_layout)
        self._content_layout.addStretch()

        self._scroll.setWidget(self._scroll_container)
        root_layout.addWidget(self._scroll)

        self.update_theme(getattr(self._settings, "theme", "dark"))

    # ── Appearance Card Setup ─────────────────────────────────────────────────

    def _setup_appearance_card(self) -> None:
        layout = QVBoxLayout(self._appearance_card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        self._app_card_title = QLabel("Appearance")
        self._app_card_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header_col.addWidget(self._app_card_title)

        self._app_card_sub = QLabel("Choose how YouCut looks.")
        self._app_card_sub.setFont(QFont("Segoe UI", 9))
        header_col.addWidget(self._app_card_sub)
        layout.addLayout(header_col)

        # 3 Preview Cards: Dark, Light, System
        theme_row = QHBoxLayout()
        theme_row.setSpacing(12)

        self._theme_card_dark = ThemePreviewCard("dark", "Dark")
        self._theme_card_dark.selected.connect(self._on_theme_card_selected)
        theme_row.addWidget(self._theme_card_dark)

        self._theme_card_light = ThemePreviewCard("light", "Light")
        self._theme_card_light.selected.connect(self._on_theme_card_selected)
        theme_row.addWidget(self._theme_card_light)

        self._theme_card_system = ThemePreviewCard("system", "System")
        self._theme_card_system.selected.connect(self._on_theme_card_selected)
        theme_row.addWidget(self._theme_card_system)

        layout.addLayout(theme_row)

    # ── Downloads Card Setup ──────────────────────────────────────────────────

    def _setup_downloads_card(self) -> None:
        layout = QVBoxLayout(self._downloads_card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        self._dl_card_title = QLabel("Downloads")
        self._dl_card_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header_col.addWidget(self._dl_card_title)

        self._dl_card_sub = QLabel("Defaults used for every new download.")
        self._dl_card_sub.setFont(QFont("Segoe UI", 9))
        header_col.addWidget(self._dl_card_sub)
        layout.addLayout(header_col)

        # Default output folder
        folder_col = QVBoxLayout()
        folder_col.setSpacing(6)
        self._folder_lbl = QLabel("Default output folder")
        self._folder_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        folder_col.addWidget(self._folder_lbl)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)

        self._folder_input = QLineEdit()
        self._folder_input.setFixedHeight(38)
        self._folder_input.setFont(QFont("Segoe UI", 9))
        self._folder_input.editingFinished.connect(self._on_folder_edited)
        folder_row.addWidget(self._folder_input, stretch=1)

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setFixedHeight(38)
        self._browse_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._browse_btn)

        folder_col.addLayout(folder_row)
        layout.addLayout(folder_col)

        # Two-column row: Fragments stepper + Default video format
        mid_row = QHBoxLayout()
        mid_row.setSpacing(16)

        # Fragments column
        frag_col = QVBoxLayout()
        frag_col.setSpacing(6)
        self._frag_lbl = QLabel("Concurrent download fragments")
        self._frag_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        frag_col.addWidget(self._frag_lbl)

        self._frag_stepper = FragmentStepper(initial_val=getattr(self._settings, "concurrent_fragments", 16))
        self._frag_stepper.valueChanged.connect(self._on_frag_changed)
        frag_col.addWidget(self._frag_stepper, alignment=Qt.AlignmentFlag.AlignLeft)

        self._frag_caption = QLabel("More fragments download faster.")
        self._frag_caption.setFont(QFont("Segoe UI", 8))
        frag_col.addWidget(self._frag_caption)
        mid_row.addLayout(frag_col, stretch=1)

        # Video format column
        fmt_col = QVBoxLayout()
        fmt_col.setSpacing(6)
        self._fmt_lbl = QLabel("Default video format")
        self._fmt_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        fmt_col.addWidget(self._fmt_lbl)

        self._format_combo = QComboBox()
        self._format_combo.setFixedHeight(38)
        self._format_combo.setFont(QFont("Segoe UI", 9))
        self._format_combo.addItems(["MP4", "MKV", "WebM"])
        self._format_combo.currentTextChanged.connect(self._on_format_changed)
        fmt_col.addWidget(self._format_combo)
        fmt_col.addStretch()
        mid_row.addLayout(fmt_col, stretch=1)

        layout.addLayout(mid_row)

        # Default trim mode row
        trim_col = QVBoxLayout()
        trim_col.setSpacing(6)
        self._trim_lbl = QLabel("Default trim mode")
        self._trim_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        trim_col.addWidget(self._trim_lbl)

        self._trim_combo = QComboBox()
        self._trim_combo.setFixedHeight(38)
        self._trim_combo.setFont(QFont("Segoe UI", 9))
        self._trim_combo.addItems(["Auto (recommended)", "Fast (keyframe cut)", "Exact (frame-accurate)"])
        self._trim_combo.currentTextChanged.connect(self._on_trim_changed)
        trim_col.addWidget(self._trim_combo)

        layout.addLayout(trim_col)

    # ── About Card Setup ──────────────────────────────────────────────────────

    def _setup_about_card(self) -> None:
        layout = QVBoxLayout(self._about_card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header_col = QVBoxLayout()
        header_col.setSpacing(2)
        self._about_title = QLabel("About")
        self._about_title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        header_col.addWidget(self._about_title)

        self._about_sub = QLabel("Version information for support.")
        self._about_sub.setFont(QFont("Segoe UI", 9))
        header_col.addWidget(self._about_sub)
        layout.addLayout(header_col)

        # Version table
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setColumnStretch(1, 1)

        self._lbl_v_youcut_name = QLabel("YouCut")
        self._lbl_v_youcut_name.setFont(QFont("Segoe UI", 9))
        self._lbl_v_youcut_val = QLabel(f"v{APP_VERSION}")
        self._lbl_v_youcut_val.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        grid.addWidget(self._lbl_v_youcut_name, 0, 0)
        grid.addWidget(self._lbl_v_youcut_val, 0, 1)

        self._lbl_v_ytdlp_name = QLabel("yt-dlp")
        self._lbl_v_ytdlp_name.setFont(QFont("Segoe UI", 9))
        self._lbl_v_ytdlp_val = QLabel(f"v{get_installed_ytdlp_version()}")
        self._lbl_v_ytdlp_val.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        grid.addWidget(self._lbl_v_ytdlp_name, 1, 0)
        grid.addWidget(self._lbl_v_ytdlp_val, 1, 1)

        self._lbl_v_ffmpeg_name = QLabel("FFmpeg")
        self._lbl_v_ffmpeg_name.setFont(QFont("Segoe UI", 9))
        self._lbl_v_ffmpeg_val = QLabel(get_installed_ffmpeg_version())
        self._lbl_v_ffmpeg_val.setFont(QFont("Consolas", 9))
        grid.addWidget(self._lbl_v_ffmpeg_name, 2, 0)
        grid.addWidget(self._lbl_v_ffmpeg_val, 2, 1)

        layout.addLayout(grid)
        layout.addSpacing(6)

        self._copy_diag_btn = QPushButton("📋 Copy diagnostics")
        self._copy_diag_btn.setFixedHeight(38)
        self._copy_diag_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._copy_diag_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_diag_btn.clicked.connect(self._on_copy_diagnostics)
        layout.addWidget(self._copy_diag_btn)

    # ── Signal Connections ────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._update_manager.app_status_changed.connect(self._on_manager_app_status)
        self._update_manager.ytdlp_status_changed.connect(self._on_manager_ytdlp_status)
        self._update_manager.ytdlp_progress.connect(self._on_manager_ytdlp_progress)
        self._update_manager.ffmpeg_status_changed.connect(self._on_manager_ffmpeg_status)
        self._update_manager.ffmpeg_progress.connect(self._on_manager_ffmpeg_progress)
        self._update_manager.all_checks_completed.connect(self._on_manager_all_checks_done)

    # ── State Loading ─────────────────────────────────────────────────────────

    def _load_from_settings(self) -> None:
        # Last checked string
        last_checked = getattr(self._settings, "last_checked_all", "")
        if not last_checked:
            last_checked = f"Checked today, {datetime.now().strftime('%H:%M')}"
        self._last_checked_label.setText(last_checked)

        # Output folder
        self._folder_input.setText(self._settings.last_output_folder)

        # Stepper
        self._frag_stepper.setValue(getattr(self._settings, "concurrent_fragments", 16))

        # Format
        fmt = getattr(self._settings, "default_video_format", "mp4").upper()
        idx = self._format_combo.findText(fmt)
        if idx >= 0:
            self._format_combo.setCurrentIndex(idx)
        else:
            self._format_combo.setCurrentText("MP4")

        # Trim mode
        mode = getattr(self._settings, "default_trim_mode", "auto")
        mode_rev_map = {"auto": 0, "fast": 1, "exact": 2}
        self._trim_combo.setCurrentIndex(mode_rev_map.get(mode, 0))

        # Toggles
        self._card_youcut.set_toggle_checked(getattr(self._settings, "auto_update_app", True))
        self._card_ytdlp.set_toggle_checked(getattr(self._settings, "auto_update_ytdlp", True))
        self._card_ffmpeg.set_toggle_checked(getattr(self._settings, "auto_update_ffmpeg", False))

        # Theme selection
        cur_theme = getattr(self._settings, "theme", "dark")
        self._update_theme_cards_selection(cur_theme)

    # ── Theme Management ──────────────────────────────────────────────────────

    def update_theme(self, theme_name: str) -> None:
        """Update styling for dark or light theme."""
        effective = resolve_theme(theme_name)
        self._is_dark = (effective == "dark")

        # Backgrounds and borders
        app_bg = BG_APP if self._is_dark else BG_APP_LIGHT
        card_bg = BG_CARD if self._is_dark else BG_CARD_LIGHT
        card_border = BORDER_CARD if self._is_dark else BORDER_CARD_LIGHT

        # Text colors
        text_primary = "#F3F4F7" if self._is_dark else "#111318"
        text_muted = "#9BA1B0" if self._is_dark else "#5A6170"

        self.setStyleSheet(f"background-color: {app_bg};")
        self._content_widget.setStyleSheet(f"background-color: {app_bg};")

        self._title_label.setStyleSheet(f"color: {text_primary};")
        self._subtitle_label.setStyleSheet(f"color: {text_muted};")

        self._updates_title.setStyleSheet(f"color: {text_primary};")
        self._updates_sub.setStyleSheet(f"color: {text_muted};")
        self._last_checked_label.setStyleSheet(f"color: {text_muted};")

        # Check all button
        btn_bg = "#1A1D27" if self._is_dark else "#F0F2F6"
        btn_border = "#2A2E3B" if self._is_dark else "#CCD1DC"
        btn_hover = "#242838" if self._is_dark else "#E5E8EF"
        self._check_all_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_primary};
                border: 1px solid {btn_border};
                border-radius: 12px;
                padding: 0 14px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
        """)

        # Sub-cards
        for card in (self._appearance_card, self._downloads_card, self._about_card):
            card.setStyleSheet(f"""
                QFrame {{
                    background-color: {card_bg};
                    border: 1px solid {card_border};
                    border-radius: 20px;
                }}
            """)

        # Labels inside sub-cards
        for lbl in (self._app_card_title, self._dl_card_title, self._about_title,
                    self._folder_lbl, self._frag_lbl, self._fmt_lbl, self._trim_lbl):
            lbl.setStyleSheet(f"color: {text_primary}; border: none; background: transparent;")

        for lbl in (self._app_card_sub, self._dl_card_sub, self._about_sub, self._frag_caption):
            lbl.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")

        for lbl in (self._lbl_v_youcut_name, self._lbl_v_ytdlp_name, self._lbl_v_ffmpeg_name):
            lbl.setStyleSheet(f"color: {text_muted}; border: none; background: transparent;")

        for lbl in (self._lbl_v_youcut_val, self._lbl_v_ytdlp_val, self._lbl_v_ffmpeg_val):
            lbl.setStyleSheet(f"color: {text_primary}; border: none; background: transparent;")

        # Inputs and buttons
        input_bg = "#0F1116" if self._is_dark else "#FFFFFF"
        input_border = "#232733" if self._is_dark else "#CCD1DC"
        self._folder_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {input_bg};
                color: {text_primary};
                border: 1px solid {input_border};
                border-radius: 10px;
                padding: 0 12px;
            }}
        """)

        self._browse_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_primary};
                border: 1px solid {btn_border};
                border-radius: 10px;
                padding: 0 16px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
        """)

        self._copy_diag_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {btn_bg};
                color: {text_primary};
                border: 1px solid {btn_border};
                border-radius: 10px;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
        """)

        combo_style = f"""
            QComboBox {{
                background-color: {input_bg};
                color: {text_primary};
                border: 1px solid {input_border};
                border-radius: 10px;
                padding: 0 12px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {card_bg};
                color: {text_primary};
                border: 1px solid {card_border};
                selection-background-color: rgba(255, 91, 74, 0.2);
            }}
        """
        self._format_combo.setStyleSheet(combo_style)
        self._trim_combo.setStyleSheet(combo_style)

        # Propagate to child widgets
        self._card_youcut.set_theme(self._is_dark)
        self._card_ytdlp.set_theme(self._is_dark)
        self._card_ffmpeg.set_theme(self._is_dark)
        self._frag_stepper.set_theme(self._is_dark)

        for tc in (self._theme_card_dark, self._theme_card_light, self._theme_card_system):
            tc.set_app_theme(self._is_dark)

    def _update_theme_cards_selection(self, selected_theme: str) -> None:
        self._theme_card_dark.set_selected(selected_theme == "dark")
        self._theme_card_light.set_selected(selected_theme == "light")
        self._theme_card_system.set_selected(selected_theme == "system")

    def _on_theme_card_selected(self, theme_key: str) -> None:
        self._settings.update(theme=theme_key)
        self._update_theme_cards_selection(theme_key)
        self.theme_changed.emit(theme_key)
        self.settings_changed.emit()

    # ── Folder & Download Option Handlers ─────────────────────────────────────

    def _on_browse_folder(self) -> None:
        current_folder = self._settings.last_output_folder or str(Path.home() / "Downloads")
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Default Output Folder",
            current_folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self._apply_output_folder(folder)

    def _on_folder_edited(self) -> None:
        text = self._folder_input.text().strip()
        if text and text != self._settings.last_output_folder:
            self._apply_output_folder(text)

    def _apply_output_folder(self, folder_path: str) -> None:
        p = Path(folder_path).resolve()
        # Validation: folder must exist (or be creatable) and have write access
        if not p.is_dir():
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception:
                show_toast(self.window() if self.window() else self, "Selected folder could not be created or accessed.", toast_type="error")
                self._folder_input.setText(self._settings.last_output_folder)
                return

        if not os.access(str(p), os.W_OK):
            show_toast(self.window() if self.window() else self, "Selected folder is read-only or not writable.", toast_type="error")
            self._folder_input.setText(self._settings.last_output_folder)
            return

        self._folder_input.setText(str(p))
        self._settings.update(last_output_folder=str(p))
        self.settings_changed.emit()
        show_toast(self.window() if self.window() else self, "Default output folder updated.", toast_type="success")

    def _on_frag_changed(self, value: int) -> None:
        self._settings.update(concurrent_fragments=value)
        self.settings_changed.emit()

    def _on_format_changed(self, text: str) -> None:
        fmt = text.lower()
        self._settings.update(default_video_format=fmt, default_merge_format=fmt)
        self.settings_changed.emit()

    def _on_trim_changed(self, text: str) -> None:
        mode_map = {
            "Auto (recommended)": "auto",
            "Fast (keyframe cut)": "fast",
            "Exact (frame-accurate)": "exact",
        }
        mode = mode_map.get(text, "auto")
        self._settings.update(default_trim_mode=mode)
        self.settings_changed.emit()

    # ── Update Toggles ────────────────────────────────────────────────────────

    def _on_youcut_toggle(self, checked: bool) -> None:
        self._settings.update(auto_update_app=checked)
        self._update_manager.auto_update_app = checked
        self.settings_changed.emit()

    def _on_ytdlp_toggle(self, checked: bool) -> None:
        self._settings.update(auto_update_ytdlp=checked)
        self._update_manager.auto_update = checked
        self.settings_changed.emit()

    def _on_ffmpeg_toggle(self, checked: bool) -> None:
        self._settings.update(auto_update_ffmpeg=checked)
        self._update_manager.auto_update_ffmpeg = checked
        self.settings_changed.emit()

    # ── Update Actions ────────────────────────────────────────────────────────

    def _on_check_all_clicked(self) -> None:
        self._check_all_btn.setEnabled(False)
        self._check_all_btn.setText("⏳ Checking...")
        self._update_manager.check_all(force=True)

    def _on_youcut_action(self, action: str) -> None:
        if action == "update":
            if getattr(sys, "frozen", False):
                self._update_manager.install_frozen_app_update(self._app_download_url)
            else:
                url = self._app_download_url or getattr(self._settings, "app_update_url", "") or "https://github.com/shubh-creator/YouCut/releases/latest"
                QDesktopServices.openUrl(QUrl(url))
        else:
            self._card_youcut.set_state("checking")
            self._update_manager.check_app(force=True, is_manual=True)

    def _on_ytdlp_action(self, action: str) -> None:
        if action == "update":
            self._card_ytdlp.set_state("updating")
            self._update_manager.update_ytdlp()
        elif action == "cancel":
            self._update_manager.cancel_ytdlp_update()
        else:
            self._card_ytdlp.set_state("checking")
            self._update_manager.check_ytdlp(force=True)

    def _on_ffmpeg_action(self, action: str) -> None:
        if action == "update":
            self._card_ffmpeg.set_state("updating")
            self._update_manager.update_ffmpeg()
        elif action == "cancel":
            self._update_manager.cancel_ffmpeg_update()
        else:
            self._card_ffmpeg.set_state("checking")
            self._update_manager.check_ffmpeg(force=True)

    # ── Manager Signal Listeners ──────────────────────────────────────────────

    def _on_manager_app_status(self, status: str, version: str, notes: str, url: str) -> None:
        if url:
            self._app_download_url = url
        if status == "available":
            self._card_youcut.set_state("available", f"New version v{version} available")
            self._card_youcut.set_whats_new_url(url)
        elif status == "up_to_date":
            self._card_youcut.set_state("up_to_date", "✓ You're on the latest version.")
        elif status == "error":
            self._card_youcut.set_state("error", "Couldn't reach GitHub Releases.")
        else:
            self._card_youcut.set_state("checking")

    def _on_manager_ytdlp_status(self, status: str, current_ver: str, latest_ver: str) -> None:
        self._card_ytdlp.set_version(f"Version {current_ver}")
        self._lbl_v_ytdlp_val.setText(f"v{current_ver}")
        if status == "available":
            self._card_ytdlp.set_state("available", f"New version v{latest_ver} available")
        elif status == "up_to_date":
            self._card_ytdlp.set_state("up_to_date", "✓ You're on the latest version.")
        elif status == "updating":
            self._card_ytdlp.set_state("updating", "Downloading update...")
        elif status == "error":
            self._card_ytdlp.set_state("error", "Couldn't check PyPI.")
        else:
            self._card_ytdlp.set_state("checking")

    def _on_manager_ytdlp_progress(self, percent: int, speed: str) -> None:
        self._card_ytdlp.set_progress(percent, speed)

    def _on_manager_ffmpeg_status(self, status: str, current_ver: str, latest_ver: str, size: str, rel_url: str) -> None:
        disp = current_ver if len(current_ver) < 22 else current_ver[:20] + ".."
        self._card_ffmpeg.set_version(disp)
        self._lbl_v_ffmpeg_val.setText(current_ver)
        if rel_url:
            self._card_ffmpeg.set_whats_new_url(rel_url)

        if status == "available":
            info = f"New build {latest_ver} · {size}" if size else f"New build {latest_ver}"
            self._card_ffmpeg.set_state("available", info)
        elif status == "up_to_date":
            self._card_ffmpeg.set_state("up_to_date", "✓ You're on the latest version.")
        elif status == "updating":
            self._card_ffmpeg.set_state("updating", "Downloading build...")
        elif status == "error":
            self._card_ffmpeg.set_state("error", "Couldn't check GitHub.")
        else:
            self._card_ffmpeg.set_state("checking")

    def _on_manager_ffmpeg_progress(self, percent: int, speed: str) -> None:
        self._card_ffmpeg.set_progress(percent, speed)

    def _on_manager_all_checks_done(self, checked_str: str) -> None:
        self._last_checked_label.setText(checked_str)
        self._settings.update(last_checked_all=checked_str)
        self._check_all_btn.setEnabled(True)
        self._check_all_btn.setText("🔄 Check all for updates")

    # ── Diagnostics Copy ─────────────────────────────────────────────────────

    def _on_copy_diagnostics(self) -> None:
        ytdlp_ver = get_installed_ytdlp_version()
        ffmpeg_ver = get_installed_ffmpeg_version()

        from clipgrab.core.downloader import get_last_executed_command
        last_cmd = get_last_executed_command() or "None"

        env_home = os.environ.get("YOUCUT_HOME")
        base_dir = Path(env_home).resolve() if env_home else Path.home() / ".youcut"
        log_file = base_dir / "youcut.log"
        log_lines = []
        if log_file.is_file():
            try:
                with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                    log_lines = all_lines[-200:]
            except Exception as e:
                log_lines = [f"Failed to read log file: {e}\n"]

        def redact_text(text: str) -> str:
            # Redact URL query strings (?key=val...)
            return re.sub(
                r'(https?://[^\s"\'<>]+?\?)([^\s"\'<>]+)',
                r'\1[REDACTED_QUERY]',
                text
            )

        diag_report = [
            "=== YOUCUT DIAGNOSTICS ===",
            f"App Version: v{APP_VERSION}",
            f"yt-dlp Version: {ytdlp_ver}",
            f"FFmpeg Version: {ffmpeg_ver}",
            f"Platform / OS: {sys.platform}",
            "",
            "=== LAST EXECUTED COMMAND ===",
            redact_text(last_cmd),
            "",
            f"=== RECENT LOGS (LAST {len(log_lines)} LINES) ===",
        ]
        diag_report.extend([redact_text(line.rstrip()) for line in log_lines])

        full_text = "\n".join(diag_report)
        cb = QApplication.clipboard()
        if cb:
            cb.setText(full_text)
            show_toast(self.window() if self.window() else self, "Diagnostics copied to clipboard!", toast_type="success")

    # ── Public API for MainWindow ─────────────────────────────────────────────

    def show_app_update_banner(self, version: str, notes: str, url: str) -> None:
        self._app_download_url = url
        self._card_youcut.set_state("available", f"New version v{version} available")
        self._card_youcut.set_whats_new_url(url)

    def refresh_versions(self) -> None:
        # App version
        current_v = getattr(version_mod, "APP_VERSION", APP_VERSION)
        self._lbl_v_youcut_val.setText(f"v{current_v}")
        self._card_youcut.set_version(f"Version {current_v}")

        # yt-dlp version
        ytdlp_version = get_installed_ytdlp_version()
        self._lbl_v_ytdlp_val.setText(f"v{ytdlp_version}")
        self._card_ytdlp.set_version(f"Version {ytdlp_version}")

        # FFmpeg version
        ffmpeg_ver = get_installed_ffmpeg_version()
        self._lbl_v_ffmpeg_val.setText(ffmpeg_ver)
        disp = ffmpeg_ver if len(ffmpeg_ver) < 22 else ffmpeg_ver[:20] + ".."
        self._card_ffmpeg.set_version(disp)

    def shutdown(self) -> None:
        """Called when application shuts down."""
        pass
