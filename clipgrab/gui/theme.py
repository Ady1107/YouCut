"""
YouCut — Theme management using custom QPalette.

Provides polished dark and light themes via QPalette (no external dependency).
pyqtdarktheme is unmaintained, so we use Fusion style + custom palettes instead.

Design language: Modern, premium aesthetic with YouCut's signature red-to-orange
accent gradient, glassmorphism-inspired cards, and smooth micro-interactions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from clipgrab.core.logger import get_logger

if TYPE_CHECKING:
    from clipgrab.config.settings import AppSettings

logger = get_logger("theme")

# ── Brand Colors ─────────────────────────────────────────────────────────────
# YouCut signature palette — warm red-to-orange gradient accent
_BRAND_RED = "#E63946"        # Primary accent
_BRAND_ORANGE = "#F77F00"     # Secondary accent (gradient end)
_BRAND_CORAL = "#EF476F"      # Tertiary / hover accent
_BRAND_WARM = "#FFB703"       # Highlight / glow

# ── Dark Palette Colors ──────────────────────────────────────────────────────
_DARK = {
    "window":           QColor(18, 18, 24),       # Deep charcoal with cool tint
    "window_text":      QColor(235, 235, 245),    # Slightly warm white
    "base":             QColor(12, 12, 18),       # Near-black for inputs
    "alt_base":         QColor(28, 28, 36),       # Card backgrounds
    "text":             QColor(235, 235, 245),
    "button":           QColor(38, 38, 48),       # Subtle button surface
    "button_text":      QColor(235, 235, 245),
    "highlight":        QColor(230, 57, 70),      # _BRAND_RED
    "highlight_text":   QColor(255, 255, 255),
    "link":             QColor(247, 127, 0),      # _BRAND_ORANGE
    "bright_text":      QColor(239, 71, 111),     # _BRAND_CORAL
    "tooltip_base":     QColor(36, 36, 44),
    "tooltip_text":     QColor(235, 235, 245),
    "placeholder":      QColor(110, 110, 130),
    "mid":              QColor(52, 52, 64),
    "dark":             QColor(8, 8, 12),
    "shadow":           QColor(0, 0, 0),
    "light":            QColor(62, 62, 74),
    "midlight":         QColor(44, 44, 56),
}

# ── Light Palette Colors ─────────────────────────────────────────────────────
_LIGHT = {
    "window":           QColor(248, 248, 252),    # Off-white, slightly cool
    "window_text":      QColor(24, 24, 32),
    "base":             QColor(255, 255, 255),
    "alt_base":         QColor(244, 244, 250),
    "text":             QColor(24, 24, 32),
    "button":           QColor(236, 236, 244),
    "button_text":      QColor(24, 24, 32),
    "highlight":        QColor(230, 57, 70),      # _BRAND_RED
    "highlight_text":   QColor(255, 255, 255),
    "link":             QColor(200, 45, 55),
    "bright_text":      QColor(180, 30, 40),
    "tooltip_base":     QColor(255, 255, 245),
    "tooltip_text":     QColor(24, 24, 32),
    "placeholder":      QColor(160, 160, 175),
    "mid":              QColor(195, 195, 210),
    "dark":             QColor(175, 175, 190),
    "shadow":           QColor(140, 140, 155),
    "light":            QColor(255, 255, 255),
    "midlight":         QColor(240, 240, 248),
}


# ── Dark Theme Stylesheet ────────────────────────────────────────────────────
_DARK_STYLESHEET = """
/* ━━━ Global Font ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
* {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}

/* ━━━ Tooltips ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QToolTip {
    background-color: #24242c;
    color: #ebebf5;
    border: 1px solid rgba(255, 255, 255, 0.08);
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 12px;
}

/* ━━━ Group Boxes (Card-style) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QGroupBox {
    background-color: rgba(28, 28, 36, 0.85);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    margin-top: 1.2em;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
    font-size: 11px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #ebebf5;
    background-color: transparent;
    font-size: 11px;
    letter-spacing: 0.3px;
}

/* ━━━ Tabs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QTabWidget::pane {
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 8px;
    background: rgba(18, 18, 24, 0.6);
    top: -1px;
}

QTabBar::tab {
    background: rgba(28, 28, 36, 0.6);
    color: rgba(235, 235, 245, 0.55);
    padding: 10px 24px;
    border: none;
    border-bottom: 2px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
    font-weight: 500;
    font-size: 10pt;
}

QTabBar::tab:selected {
    background: rgba(230, 57, 70, 0.12);
    color: #fff;
    border-bottom: 2px solid #E63946;
}

QTabBar::tab:hover:!selected {
    background: rgba(255, 255, 255, 0.05);
    color: rgba(235, 235, 245, 0.8);
}

/* ━━━ Progress Bar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QProgressBar {
    border: none;
    border-radius: 6px;
    text-align: center;
    background: rgba(255, 255, 255, 0.06);
    color: #ebebf5;
    height: 12px;
    font-size: 9px;
    font-weight: 600;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #E63946, stop:0.5 #EF476F, stop:1 #F77F00);
    border-radius: 6px;
}

/* ━━━ Text Inputs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 7px 12px;
    background: rgba(12, 12, 18, 0.7);
    color: #ebebf5;
    selection-background-color: #E63946;
    selection-color: #fff;
    font-size: 10pt;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid rgba(230, 57, 70, 0.6);
    background: rgba(12, 12, 18, 0.9);
}

QLineEdit:disabled {
    background: rgba(28, 28, 36, 0.5);
    color: rgba(235, 235, 245, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.04);
}

/* ━━━ Combo Boxes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QComboBox {
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 7px 12px;
    background: rgba(12, 12, 18, 0.7);
    color: #ebebf5;
    font-size: 10pt;
    min-height: 20px;
}

QComboBox:focus, QComboBox:on {
    border: 1px solid rgba(230, 57, 70, 0.6);
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 28px;
    border: none;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid rgba(235, 235, 245, 0.5);
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background: #1c1c24;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    color: #ebebf5;
    selection-background-color: rgba(230, 57, 70, 0.3);
    selection-color: #fff;
    padding: 4px;
    outline: none;
}

QComboBox QAbstractItemView::item {
    padding: 6px 12px;
    border-radius: 4px;
    min-height: 24px;
}

QComboBox QAbstractItemView::item:hover {
    background: rgba(255, 255, 255, 0.06);
}

/* ━━━ Buttons ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QPushButton {
    background: rgba(38, 38, 48, 0.9);
    color: #ebebf5;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 10pt;
}

QPushButton:hover {
    background: rgba(230, 57, 70, 0.15);
    border: 1px solid rgba(230, 57, 70, 0.4);
    color: #fff;
}

QPushButton:pressed {
    background: rgba(230, 57, 70, 0.35);
    border: 1px solid rgba(230, 57, 70, 0.6);
}

QPushButton:disabled {
    background: rgba(28, 28, 36, 0.5);
    color: rgba(235, 235, 245, 0.2);
    border: 1px solid rgba(255, 255, 255, 0.03);
}

/* ━━━ Check Boxes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QCheckBox {
    spacing: 8px;
    font-size: 10pt;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid rgba(255, 255, 255, 0.15);
    background: rgba(12, 12, 18, 0.5);
}

QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #E63946, stop:1 #F77F00);
    border: 2px solid #E63946;
    image: none;
}

QCheckBox::indicator:hover {
    border: 2px solid rgba(230, 57, 70, 0.5);
}

/* ━━━ Scrollbars ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 8px;
    border-radius: 4px;
    margin: 4px 0;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.12);
    border-radius: 4px;
    min-height: 40px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 0.22);
}

QScrollBar::handle:vertical:pressed {
    background: rgba(230, 57, 70, 0.5);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 8px;
    border-radius: 4px;
    margin: 0 4px;
}

QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 0.12);
    border-radius: 4px;
    min-width: 40px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(255, 255, 255, 0.22);
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* ━━━ Status Bar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QStatusBar {
    background: rgba(8, 8, 12, 0.95);
    color: rgba(235, 235, 245, 0.5);
    border-top: 1px solid rgba(255, 255, 255, 0.04);
    font-size: 9pt;
    padding: 2px 8px;
}

/* ━━━ Scroll Areas ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QScrollArea {
    border: none;
    background: transparent;
}

/* ━━━ Frames (Queue Item Cards) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QFrame[frameShape="6"] {
    background: rgba(28, 28, 36, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
}

/* ━━━ Labels ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QLabel {
    background: transparent;
    border: none;
}

/* ━━━ Time Range Component ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QFrame#timeRangeCard {
    background: rgba(22, 22, 30, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 8px;
}

QPushButton.timeNudgeBtn {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 4px;
    padding: 0 4px;
    color: rgba(235, 235, 245, 0.85);
    font-size: 8pt;
    font-weight: 500;
}

QPushButton.timeNudgeBtn:hover {
    background: rgba(230, 57, 70, 0.25);
    border-color: rgba(230, 57, 70, 0.5);
    color: #ffffff;
}

QPushButton.timeNudgeBtn:disabled {
    background: rgba(255, 255, 255, 0.02);
    border-color: rgba(255, 255, 255, 0.03);
    color: rgba(235, 235, 245, 0.2);
}

QPushButton.presetChip {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 11px;
    padding: 0 8px;
    color: rgba(235, 235, 245, 0.75);
    font-size: 8pt;
    font-weight: 500;
}

QPushButton.presetChip:hover {
    background: rgba(255, 255, 255, 0.12);
    color: #ffffff;
}

QPushButton.presetChip:disabled {
    background: rgba(255, 255, 255, 0.02);
    border-color: rgba(255, 255, 255, 0.03);
    color: rgba(235, 235, 245, 0.2);
}
"""

# ── Light Theme Stylesheet ───────────────────────────────────────────────────
_LIGHT_STYLESHEET = """
/* ━━━ Global Font ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
* {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}

/* ━━━ Tooltips ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QToolTip {
    background-color: #fff;
    color: #181820;
    border: 1px solid rgba(0, 0, 0, 0.08);
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 12px;
}

/* ━━━ Group Boxes (Card-style) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QGroupBox {
    background-color: rgba(255, 255, 255, 0.95);
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 12px;
    margin-top: 1.2em;
    padding: 16px 12px 12px 12px;
    font-weight: 600;
    font-size: 11px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: #181820;
    background-color: transparent;
    font-size: 11px;
    letter-spacing: 0.3px;
}

/* ━━━ Tabs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QTabWidget::pane {
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 8px;
    background: rgba(248, 248, 252, 0.6);
    top: -1px;
}

QTabBar::tab {
    background: rgba(236, 236, 244, 0.6);
    color: rgba(24, 24, 32, 0.5);
    padding: 10px 24px;
    border: none;
    border-bottom: 2px solid transparent;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 3px;
    font-weight: 500;
    font-size: 10pt;
}

QTabBar::tab:selected {
    background: rgba(230, 57, 70, 0.08);
    color: #181820;
    border-bottom: 2px solid #E63946;
}

QTabBar::tab:hover:!selected {
    background: rgba(0, 0, 0, 0.04);
    color: rgba(24, 24, 32, 0.7);
}

/* ━━━ Progress Bar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QProgressBar {
    border: none;
    border-radius: 6px;
    text-align: center;
    background: rgba(0, 0, 0, 0.06);
    color: #181820;
    height: 12px;
    font-size: 9px;
    font-weight: 600;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #E63946, stop:0.5 #EF476F, stop:1 #F77F00);
    border-radius: 6px;
}

/* ━━━ Text Inputs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 8px;
    padding: 7px 12px;
    background: #fff;
    color: #181820;
    selection-background-color: #E63946;
    selection-color: #fff;
    font-size: 10pt;
}

QLineEdit:focus, QTextEdit:focus {
    border: 1px solid rgba(230, 57, 70, 0.5);
    background: #fff;
}

QLineEdit:disabled {
    background: rgba(244, 244, 250, 0.7);
    color: rgba(24, 24, 32, 0.3);
    border: 1px solid rgba(0, 0, 0, 0.04);
}

/* ━━━ Combo Boxes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QComboBox {
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 8px;
    padding: 7px 12px;
    background: #fff;
    color: #181820;
    font-size: 10pt;
    min-height: 20px;
}

QComboBox:focus, QComboBox:on {
    border: 1px solid rgba(230, 57, 70, 0.5);
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 28px;
    border: none;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}

QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid rgba(24, 24, 32, 0.4);
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background: #fff;
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 8px;
    color: #181820;
    selection-background-color: rgba(230, 57, 70, 0.12);
    selection-color: #181820;
    padding: 4px;
    outline: none;
}

QComboBox QAbstractItemView::item {
    padding: 6px 12px;
    border-radius: 4px;
    min-height: 24px;
}

QComboBox QAbstractItemView::item:hover {
    background: rgba(0, 0, 0, 0.04);
}

/* ━━━ Buttons ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QPushButton {
    background: rgba(236, 236, 244, 0.95);
    color: #181820;
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 8px;
    padding: 8px 20px;
    font-weight: 600;
    font-size: 10pt;
}

QPushButton:hover {
    background: rgba(230, 57, 70, 0.08);
    border: 1px solid rgba(230, 57, 70, 0.25);
    color: #C8202E;
}

QPushButton:pressed {
    background: rgba(230, 57, 70, 0.18);
    border: 1px solid rgba(230, 57, 70, 0.4);
}

QPushButton:disabled {
    background: rgba(244, 244, 250, 0.6);
    color: rgba(24, 24, 32, 0.25);
    border: 1px solid rgba(0, 0, 0, 0.03);
}

/* ━━━ Check Boxes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QCheckBox {
    spacing: 8px;
    font-size: 10pt;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid rgba(0, 0, 0, 0.15);
    background: #fff;
}

QCheckBox::indicator:checked {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #E63946, stop:1 #F77F00);
    border: 2px solid #E63946;
    image: none;
}

QCheckBox::indicator:hover {
    border: 2px solid rgba(230, 57, 70, 0.4);
}

/* ━━━ Scrollbars ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 8px;
    border-radius: 4px;
    margin: 4px 0;
}

QScrollBar::handle:vertical {
    background: rgba(0, 0, 0, 0.1);
    border-radius: 4px;
    min-height: 40px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(0, 0, 0, 0.18);
}

QScrollBar::handle:vertical:pressed {
    background: rgba(230, 57, 70, 0.4);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}

QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 8px;
    border-radius: 4px;
    margin: 0 4px;
}

QScrollBar::handle:horizontal {
    background: rgba(0, 0, 0, 0.1);
    border-radius: 4px;
    min-width: 40px;
}

QScrollBar::handle:horizontal:hover {
    background: rgba(0, 0, 0, 0.18);
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: none;
}

/* ━━━ Status Bar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QStatusBar {
    background: rgba(244, 244, 250, 0.95);
    color: rgba(24, 24, 32, 0.5);
    border-top: 1px solid rgba(0, 0, 0, 0.04);
    font-size: 9pt;
    padding: 2px 8px;
}

/* ━━━ Scroll Areas ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QScrollArea {
    border: none;
    background: transparent;
}

/* ━━━ Frames (Queue Item Cards) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QFrame[frameShape="6"] {
    background: rgba(255, 255, 255, 0.85);
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 12px;
}

/* ━━━ Labels ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QLabel {
    background: transparent;
    border: none;
}

/* ━━━ Time Range Component ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QFrame#timeRangeCard {
    background: rgba(0, 0, 0, 0.025);
    border: 1px solid rgba(0, 0, 0, 0.06);
    border-radius: 8px;
}

QPushButton.timeNudgeBtn {
    background: rgba(0, 0, 0, 0.04);
    border: 1px solid rgba(0, 0, 0, 0.08);
    border-radius: 4px;
    padding: 0 4px;
    color: rgba(24, 24, 32, 0.85);
    font-size: 8pt;
    font-weight: 500;
}

QPushButton.timeNudgeBtn:hover {
    background: rgba(230, 57, 70, 0.15);
    border-color: rgba(230, 57, 70, 0.4);
    color: #E63946;
}

QPushButton.timeNudgeBtn:disabled {
    background: rgba(0, 0, 0, 0.02);
    border-color: rgba(0, 0, 0, 0.04);
    color: rgba(24, 24, 32, 0.25);
}

QPushButton.presetChip {
    background: rgba(0, 0, 0, 0.035);
    border: 1px solid rgba(0, 0, 0, 0.07);
    border-radius: 11px;
    padding: 0 8px;
    color: rgba(24, 24, 32, 0.7);
    font-size: 8pt;
    font-weight: 500;
}

QPushButton.presetChip:hover {
    background: rgba(0, 0, 0, 0.08);
    color: #181820;
}

QPushButton.presetChip:disabled {
    background: rgba(0, 0, 0, 0.015);
    border-color: rgba(0, 0, 0, 0.03);
    color: rgba(24, 24, 32, 0.25);
}
"""


def _build_palette(colors: dict[str, QColor]) -> QPalette:
    """Build a QPalette from a color dictionary."""
    palette = QPalette()

    role_map = {
        "window":         QPalette.ColorRole.Window,
        "window_text":    QPalette.ColorRole.WindowText,
        "base":           QPalette.ColorRole.Base,
        "alt_base":       QPalette.ColorRole.AlternateBase,
        "text":           QPalette.ColorRole.Text,
        "button":         QPalette.ColorRole.Button,
        "button_text":    QPalette.ColorRole.ButtonText,
        "highlight":      QPalette.ColorRole.Highlight,
        "highlight_text": QPalette.ColorRole.HighlightedText,
        "link":           QPalette.ColorRole.Link,
        "bright_text":    QPalette.ColorRole.BrightText,
        "tooltip_base":   QPalette.ColorRole.ToolTipBase,
        "tooltip_text":   QPalette.ColorRole.ToolTipText,
        "placeholder":    QPalette.ColorRole.PlaceholderText,
        "mid":            QPalette.ColorRole.Mid,
        "dark":           QPalette.ColorRole.Dark,
        "shadow":         QPalette.ColorRole.Shadow,
        "light":          QPalette.ColorRole.Light,
        "midlight":       QPalette.ColorRole.Midlight,
    }

    for key, role in role_map.items():
        if key in colors:
            palette.setColor(role, colors[key])

    # Disabled state — desaturated versions
    for key, role in role_map.items():
        if key in colors:
            color = QColor(colors[key])
            # Reduce saturation and increase lightness for disabled
            h, s, l, a = color.getHsl()
            disabled_color = QColor.fromHsl(h, max(0, s - 80), l, max(a, 180))
            palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_color)

    return palette


def apply_theme(app: QApplication, theme_name: str) -> None:
    """
    Apply a dark or light theme to the application.

    Uses Fusion style as the base with a custom QPalette and stylesheet overlay.

    Args:
        app: The QApplication instance.
        theme_name: Either "dark" or "light".
    """
    theme_name = theme_name.lower()
    if theme_name not in ("dark", "light"):
        theme_name = "dark"

    app.setStyle("Fusion")

    if theme_name == "dark":
        palette = _build_palette(_DARK)
        stylesheet = _DARK_STYLESHEET
    else:
        palette = _build_palette(_LIGHT)
        stylesheet = _LIGHT_STYLESHEET

    app.setPalette(palette)
    app.setStyleSheet(stylesheet)
    app.setProperty("current_theme", theme_name)

    logger.info("Theme applied: %s", theme_name)


def toggle_theme(app: QApplication, settings: AppSettings) -> str:
    """
    Toggle between dark and light themes and persist the choice.

    Args:
        app: The QApplication instance.
        settings: The AppSettings instance to persist the choice.

    Returns:
        The new theme name ("dark" or "light").
    """
    current = settings.theme
    new_theme = "light" if current == "dark" else "dark"
    apply_theme(app, new_theme)
    settings.update(theme=new_theme)
    logger.info("Theme toggled: %s -> %s", current, new_theme)
    return new_theme


def get_accent_color() -> QColor:
    """Get the primary brand accent color."""
    return QColor(230, 57, 70)  # _BRAND_RED


def get_accent_gradient_css() -> str:
    """Get the CSS gradient string for the brand accent."""
    return "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E63946, stop:1 #F77F00)"
