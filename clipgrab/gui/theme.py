"""
YouCut — Design Tokens and Modern Theme System.

Central source of truth for:
- Design Tokens (surfaces, borders, accents, text levels, radii, spacing, metrics)
- Bundled Inter font family (SIL OFL) initialization with fallback to Segoe UI
- Consistent dark theme stylesheets sampled from reference designs
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from clipgrab.core.logger import get_logger

if TYPE_CHECKING:
    from clipgrab.config.settings import AppSettings

logger = get_logger("theme")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DESIGN TOKENS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ── Surfaces ─────────────────────────────────────────────────────────────────
BG_APP = "#0B0C10"              # Base application window background
BG_SIDEBAR = "#0F1116"          # Sidebar background
BG_CARD = "#14161D"             # Card container surface
BG_CARD_ALT = "#1A1D27"         # Embedded card surface / secondary containers
BG_INPUT = "#0F1116"            # Form input / text field background
BG_INPUT_HOVER = "#13151D"
BG_PILL = "#1D202A"             # Unselected pill / chip surface
BG_PILL_HOVER = "#252936"
BG_PILL_SELECTED = "#2A1B19"    # Tinted accent background for selected chips
BG_BADGE = "#22171A"            # Notification / status pill badge background
BG_TOOLTIP = "#1F222D"

# ── Borders ──────────────────────────────────────────────────────────────────
BORDER_CARD = "#232733"         # Card container border
BORDER_CARD_SUBTLE = "#1C1F2B"  # Sub-card inner border
BORDER_STRONG = "#2A2E3B"       # Strong emphasis border
BORDER_INPUT = "#232733"        # Form input border
BORDER_INPUT_FOCUS = "#FF5B4A"  # Focused input accent ring
BORDER_DIVIDER = "#1B1E29"      # Hairline separators
BORDER_PILL = "#2A2E3D"         # Default chip border
BORDER_PILL_SELECTED = "#FF5B4A"# Selected chip accent outline

# ── Brand Accent ─────────────────────────────────────────────────────────────
COLOR_ACCENT = "#FF5B4A"        # Reference Coral / Red-Orange accent
COLOR_ACCENT_HOVER = "#FF6D5D"
COLOR_ACCENT_PRESSED = "#E64837"
COLOR_ACCENT_TINT = "#2A1B19"
COLOR_ACCENT_TEXT = "#FF8C7E"
COLOR_ACCENT_TINT_HEAVY = "rgba(255, 91, 74, 0.25)"
COLOR_ACCENT_GRADIENT = "qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF6D5D, stop:1 #E64837)"

# ── Feedback / State Colors ──────────────────────────────────────────────────
COLOR_SUCCESS = "#3DDC97"       # Emerald green ("Video found", status dot)
COLOR_SUCCESS_BG = "rgba(61, 220, 151, 0.12)"
COLOR_WARNING = "#F59E0B"
COLOR_WARNING_BG = "rgba(245, 158, 11, 0.12)"
COLOR_ERROR = "#EF4444"         # Error red
COLOR_ERROR_BG = "rgba(239, 68, 68, 0.12)"

# ── Text Levels ──────────────────────────────────────────────────────────────
TEXT_PRIMARY = "#F3F4F7"        # Main titles, values, selected states
TEXT_SECONDARY = "#9BA1B0"      # Subtitles, labels, descriptions
TEXT_MUTED = "#6F7585"          # Disabled text, placeholders, timeline ticks
TEXT_TERTIARY = "#787F90"       # Inactive buttons, secondary badges

# ── Light Theme Surfaces & Tokens ─────────────────────────────────────────────
BG_APP_LIGHT = "#F4F5F8"              # Base application window background
BG_SIDEBAR_LIGHT = "#E9EBF0"          # Sidebar background
BG_CARD_LIGHT = "#FFFFFF"             # Card container surface
BG_CARD_ALT_LIGHT = "#F0F2F6"         # Secondary card surface
BG_INPUT_LIGHT = "#FFFFFF"            # Input background
BG_INPUT_HOVER_LIGHT = "#F8F9FA"
BG_PILL_LIGHT = "#E2E5EC"             # Unselected pill surface
BG_PILL_HOVER_LIGHT = "#D5D9E3"
BG_PILL_SELECTED_LIGHT = "#FFE8E5"    # Tinted accent background for selected chips
BG_BADGE_LIGHT = "#FFE8E5"
BG_TOOLTIP_LIGHT = "#FFFFFF"

BORDER_CARD_LIGHT = "#DCE0E8"
BORDER_CARD_SUBTLE_LIGHT = "#E5E8EF"
BORDER_STRONG_LIGHT = "#CCD1DC"
BORDER_INPUT_LIGHT = "#CCD1DC"
BORDER_INPUT_FOCUS_LIGHT = "#FF5B4A"
BORDER_DIVIDER_LIGHT = "#E2E5EC"
BORDER_PILL_LIGHT = "#CCD1DC"
BORDER_PILL_SELECTED_LIGHT = "#FF5B4A"

TEXT_PRIMARY_LIGHT = "#111318"
TEXT_SECONDARY_LIGHT = "#5A6170"
TEXT_MUTED_LIGHT = "#8A92A2"
TEXT_TERTIARY_LIGHT = "#787F90"
SIDEBAR_WIDTH = 190
SIDEBAR_COLLAPSED_WIDTH = 64
RIGHT_COL_WIDTH = 300
PAGE_PADDING = 20
GRID_GAP = 16
MIN_WINDOW_WIDTH = 900
MIN_WINDOW_HEIGHT = 600

RADIUS_CARD = 20
RADIUS_FIELD = 12
RADIUS_BUTTON = 10
RADIUS_BADGE = 8
RADIUS_PILL = 9999              # Fully rounded

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FONT INITIALIZATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_font_initialized = False
_primary_font_family = "Inter"
FONT_FAMILIES = ["Inter", "Nirmala UI", "Segoe UI"]


def init_fonts() -> str:
    """
    Bundle Inter (SIL OFL) via QFontDatabase, falling back to Segoe UI.
    Loads Regular (400), Medium (500), SemiBold (600), and Bold (700).
    """
    global _font_initialized, _primary_font_family
    if _font_initialized:
        return _primary_font_family

    from clipgrab.core.ffmpeg_utils import _get_assets_dir
    assets_dir = _get_assets_dir()
    fonts_dir = os.path.join(assets_dir, "fonts")
    if not os.path.isdir(fonts_dir):
        # Check relative path
        alt_fonts = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")
        if os.path.isdir(alt_fonts):
            fonts_dir = alt_fonts

    loaded = 0
    if os.path.isdir(fonts_dir):
        for font_file in ("Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf"):
            font_path = os.path.join(fonts_dir, font_file)
            if os.path.isfile(font_path):
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id >= 0:
                    loaded += 1

    # Verify Inter family availability
    families = QFontDatabase.families()
    if "Inter" in families and loaded > 0:
        _primary_font_family = "Inter"
        logger.info("Loaded Inter font family (%d weights)", loaded)
    elif "Segoe UI" in families:
        _primary_font_family = "Segoe UI"
        logger.info("Inter not found, falling back to Segoe UI")
    else:
        _primary_font_family = "sans-serif"

    _font_initialized = True
    QFont.insertSubstitutions("Inter", ["Nirmala UI", "Segoe UI"])
    from PySide6.QtCore import QLoggingCategory
    QLoggingCategory.setFilterRules("qt.text.font.db.warning=false\nqt.text.font.db=false")
    return _primary_font_family


def get_font(
    pixel_size: int = 14,
    weight: QFont.Weight = QFont.Weight.Normal,
    point_size: Optional[int] = None,
) -> QFont:
    """
    Helper to return an application font with Inter and fallback chain.
    Includes Nirmala UI for Devanagari script OpenType support.
    """
    init_fonts()
    font = QFont()
    font.setFamilies(FONT_FAMILIES)
    if point_size is not None:
        font.setPointSize(point_size)
    else:
        font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# QPALETTE CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DARK_PALETTE_COLORS = {
    "window":           QColor(BG_APP),
    "window_text":      QColor(TEXT_PRIMARY),
    "base":             QColor(BG_INPUT),
    "alt_base":         QColor(BG_CARD),
    "text":             QColor(TEXT_PRIMARY),
    "button":           QColor(BG_PILL),
    "button_text":      QColor(TEXT_PRIMARY),
    "highlight":        QColor(COLOR_ACCENT),
    "highlight_text":   QColor("#FFFFFF"),
    "link":             QColor(COLOR_ACCENT),
    "bright_text":      QColor("#FFFFFF"),
    "tooltip_base":     QColor(BG_TOOLTIP),
    "tooltip_text":     QColor(TEXT_PRIMARY),
    "placeholder":      QColor(TEXT_MUTED),
    "mid":              QColor(BORDER_CARD),
    "dark":             QColor(BG_APP),
    "shadow":           QColor("#000000"),
    "light":            QColor(BG_CARD_ALT),
    "midlight":         QColor(BG_PILL),
}

_LIGHT_PALETTE_COLORS = {
    "window":           QColor(BG_APP_LIGHT),
    "window_text":      QColor(TEXT_PRIMARY_LIGHT),
    "base":             QColor(BG_INPUT_LIGHT),
    "alt_base":         QColor(BG_CARD_LIGHT),
    "text":             QColor(TEXT_PRIMARY_LIGHT),
    "button":           QColor(BG_PILL_LIGHT),
    "button_text":      QColor(TEXT_PRIMARY_LIGHT),
    "highlight":        QColor(COLOR_ACCENT),
    "highlight_text":   QColor("#FFFFFF"),
    "link":             QColor(COLOR_ACCENT),
    "bright_text":      QColor("#FFFFFF"),
    "tooltip_base":     QColor(BG_TOOLTIP_LIGHT),
    "tooltip_text":     QColor(TEXT_PRIMARY_LIGHT),
    "placeholder":      QColor(TEXT_MUTED_LIGHT),
    "mid":              QColor(BORDER_CARD_LIGHT),
    "dark":             QColor(BG_SIDEBAR_LIGHT),
    "shadow":           QColor("#B0B5C0"),
    "light":            QColor(BG_CARD_ALT_LIGHT),
    "midlight":         QColor(BG_PILL_LIGHT),
}


def _build_palette(colors: dict[str, QColor]) -> QPalette:
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
        "light":            QPalette.ColorRole.Light,
        "midlight":       QPalette.ColorRole.Midlight,
    }

    for key, role in role_map.items():
        if key in colors:
            palette.setColor(role, colors[key])

    for key, role in role_map.items():
        if key in colors:
            color = QColor(colors[key])
            h, s, l, a = color.getHsl()
            disabled_color = QColor.fromHsl(h, max(0, s - 60), l, max(a, 160))
            palette.setColor(QPalette.ColorGroup.Disabled, role, disabled_color)

    return palette


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# APPLICATION STYLESHEET
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_assets_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
_chevron_svg_path = os.path.abspath(os.path.join(_assets_dir, "chevron-down.svg")).replace("\\", "/")
if not os.path.isfile(_chevron_svg_path):
    _alt = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icons", "chevron-down.svg")).replace("\\", "/")
    if os.path.isfile(_alt):
        _chevron_svg_path = _alt

def _generate_stylesheet(
    bg_app: str,
    bg_card: str,
    bg_input: str,
    bg_input_hover: str,
    bg_pill: str,
    bg_pill_hover: str,
    bg_pill_selected: str,
    bg_card_alt: str,
    bg_tooltip: str,
    border_card: str,
    border_input: str,
    border_input_focus: str,
    border_divider: str,
    border_pill: str,
    border_pill_selected: str,
    text_primary: str,
    text_secondary: str,
    text_muted: str,
) -> str:
    return f"""
/* ━━━ Global Application Typography & Resets ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
* {{
    font-family: 'Inter', 'Nirmala UI', 'Segoe UI', -apple-system, sans-serif;
    outline: none;
}}

QMainWindow, QWidget#centralWidget {{
    background-color: {bg_app};
    color: {text_primary};
}}

/* ━━━ Tooltips ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QToolTip {{
    background-color: {bg_tooltip};
    color: {text_primary};
    border: 1px solid {border_card};
    padding: 6px 10px;
    border-radius: 8px;
    font-size: 11px;
}}

/* ━━━ Card Container (QFrame.card) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QFrame.card, QFrame#cardContainer {{
    background-color: {bg_card};
    border: 1px solid {border_card};
    border-radius: {RADIUS_CARD}px;
}}

/* ━━━ Text Inputs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    border: 1px solid {border_input};
    border-radius: {RADIUS_FIELD}px;
    padding: 8px 14px;
    background-color: {bg_input};
    color: {text_primary};
    selection-background-color: {COLOR_ACCENT};
    selection-color: #FFFFFF;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus {{
    border: 1px solid {border_input_focus};
    background-color: {bg_input_hover};
}}

QLineEdit:disabled {{
    background-color: {bg_input};
    color: {text_muted};
    border: 1px solid {border_divider};
}}

/* ━━━ Combo Boxes ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QComboBox {{
    border: 1px solid {border_input};
    border-radius: {RADIUS_FIELD}px;
    padding: 8px 14px;
    background-color: {bg_input};
    color: {text_primary};
    font-size: 13px;
    min-height: 22px;
}}

QComboBox:focus, QComboBox:on {{
    border: 1px solid {border_input_focus};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: right center;
    width: 28px;
    border: none;
}}

QComboBox::down-arrow {{
    image: url('{_chevron_svg_path}');
    width: 12px;
    height: 12px;
    margin-right: 10px;
}}

QComboBox QAbstractItemView {{
    background-color: {bg_card};
    border: 1px solid {border_card};
    border-radius: 10px;
    color: {text_primary};
    selection-background-color: {COLOR_ACCENT_TINT};
    selection-color: #FFFFFF;
    padding: 6px;
    outline: none;
}}

QComboBox QAbstractItemView::item {{
    padding: 8px 12px;
    border-radius: 6px;
    min-height: 24px;
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: {bg_pill};
}}

/* ━━━ Buttons ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QPushButton.btnPrimary, QPushButton#primaryBtn {{
    background-color: {COLOR_ACCENT};
    color: #FFFFFF;
    border: none;
    border-radius: {RADIUS_BUTTON}px;
    padding: 10px 20px;
    font-weight: 600;
    font-size: 13px;
}}

QPushButton.btnPrimary:hover, QPushButton#primaryBtn:hover {{
    background-color: {COLOR_ACCENT_HOVER};
}}

QPushButton.btnPrimary:pressed, QPushButton#primaryBtn:pressed {{
    background-color: {COLOR_ACCENT_PRESSED};
}}

QPushButton.btnPrimary:disabled, QPushButton#primaryBtn:disabled {{
    background-color: {bg_pill};
    color: {text_muted};
    border: 1px solid {border_divider};
}}

QPushButton {{
    background-color: {bg_pill};
    color: {text_primary};
    border: 1px solid {border_pill};
    border-radius: {RADIUS_BUTTON}px;
    padding: 8px 16px;
    font-weight: 500;
    font-size: 12px;
}}

QPushButton:hover {{
    background-color: {bg_pill_hover};
    border-color: {border_card};
}}

QPushButton:pressed {{
    background-color: {bg_card_alt};
}}

QPushButton:disabled {{
    background-color: {bg_input};
    color: {text_muted};
    border: 1px solid {border_divider};
}}

QPushButton.chip {{
    background-color: {bg_pill};
    color: {text_secondary};
    border: 1px solid {border_pill};
    border-radius: 9999px;
    padding: 6px 14px;
    font-weight: 500;
    font-size: 12px;
}}

QPushButton.chip:hover {{
    background-color: {bg_pill_hover};
    color: {text_primary};
}}

QPushButton.chip[checked="true"], QPushButton.chip:checked {{
    background-color: {bg_pill_selected};
    color: #FFFFFF;
    border: 1px solid {border_pill_selected};
    font-weight: 600;
}}

/* ━━━ Progress Bars ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QProgressBar {{
    background-color: {bg_input};
    border: 1px solid {border_input};
    border-radius: 9px;
    text-align: center;
    color: #FFFFFF;
    font-size: 11px;
    font-weight: 700;
    min-height: 18px;
    max-height: 18px;
}}

QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10B981, stop:1 #22C55E);
    border-radius: 8px;
}}

/* ━━━ Scrollbars ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 6px;
    border-radius: 3px;
    margin: 4px 0;
}}

QScrollBar::handle:vertical {{
    background: {border_card};
    border-radius: 3px;
    min-height: 40px;
}}

QScrollBar::handle:vertical:hover {{
    background: {border_pill};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 6px;
    border-radius: 3px;
    margin: 0 4px;
}}

QScrollBar::handle:horizontal {{
    background: {border_card};
    border-radius: 3px;
    min-width: 40px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {border_pill};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}

QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: none;
}}

/* ━━━ Status Bar ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */
QStatusBar {{
    background-color: {bg_app};
    color: {text_muted};
    border-top: 1px solid {border_divider};
    font-size: 11px;
    padding: 2px 8px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}
"""

DARK_STYLESHEET = _generate_stylesheet(
    bg_app=BG_APP,
    bg_card=BG_CARD,
    bg_input=BG_INPUT,
    bg_input_hover=BG_INPUT_HOVER,
    bg_pill=BG_PILL,
    bg_pill_hover=BG_PILL_HOVER,
    bg_pill_selected=BG_PILL_SELECTED,
    bg_card_alt=BG_CARD_ALT,
    bg_tooltip=BG_TOOLTIP,
    border_card=BORDER_CARD,
    border_input=BORDER_INPUT,
    border_input_focus=BORDER_INPUT_FOCUS,
    border_divider=BORDER_DIVIDER,
    border_pill=BORDER_PILL,
    border_pill_selected=BORDER_PILL_SELECTED,
    text_primary=TEXT_PRIMARY,
    text_secondary=TEXT_SECONDARY,
    text_muted=TEXT_MUTED,
)

LIGHT_STYLESHEET = _generate_stylesheet(
    bg_app=BG_APP_LIGHT,
    bg_card=BG_CARD_LIGHT,
    bg_input=BG_INPUT_LIGHT,
    bg_input_hover=BG_INPUT_HOVER_LIGHT,
    bg_pill=BG_PILL_LIGHT,
    bg_pill_hover=BG_PILL_HOVER_LIGHT,
    bg_pill_selected=BG_PILL_SELECTED_LIGHT,
    bg_card_alt=BG_CARD_ALT_LIGHT,
    bg_tooltip=BG_TOOLTIP_LIGHT,
    border_card=BORDER_CARD_LIGHT,
    border_input=BORDER_INPUT_LIGHT,
    border_input_focus=BORDER_INPUT_FOCUS_LIGHT,
    border_divider=BORDER_DIVIDER_LIGHT,
    border_pill=BORDER_PILL_LIGHT,
    border_pill_selected=BORDER_PILL_SELECTED_LIGHT,
    text_primary=TEXT_PRIMARY_LIGHT,
    text_secondary=TEXT_SECONDARY_LIGHT,
    text_muted=TEXT_MUTED_LIGHT,
)


def apply_theme(app: QApplication, theme_name: str = "dark") -> None:
    """
    Apply dark or light theme to the application.
    """
    init_fonts()
    app.setStyle("Fusion")

    if theme_name == "light":
        palette = _build_palette(_LIGHT_PALETTE_COLORS)
        app.setPalette(palette)
        app.setStyleSheet(LIGHT_STYLESHEET)
        app.setProperty("current_theme", "light")
        logger.info("Light theme and Inter typography applied")
    else:
        palette = _build_palette(_DARK_PALETTE_COLORS)
        app.setPalette(palette)
        app.setStyleSheet(DARK_STYLESHEET)
        app.setProperty("current_theme", "dark")
        logger.info("Dark theme and Inter typography applied")

    app_font = QFont()
    app_font.setFamilies(FONT_FAMILIES)
    app_font.setPixelSize(14)
    app.setFont(app_font)


def toggle_theme(app: QApplication, settings: AppSettings) -> str:
    """
    Toggle between dark and light themes, saving to settings.
    """
    cur_theme = getattr(settings, "theme", "dark")
    new_theme = "light" if cur_theme == "dark" else "dark"
    apply_theme(app, new_theme)
    settings.update(theme=new_theme)
    return new_theme


def get_accent_color() -> QColor:
    return QColor(COLOR_ACCENT)


def get_accent_gradient_css() -> str:
    return COLOR_ACCENT_GRADIENT
