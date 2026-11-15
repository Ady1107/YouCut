"""
YouCut — Unified Social Download Page for Instagram and Facebook.

Implements the clean, modern, player-free UI from the design specifications:
- Header tile with platform icon (Instagram gradient squircle / Facebook blue tile),
  title, subtitle, and content type selector pills (Reel/Post for IG, Reel/Video/Post for FB).
- URL input bar with link icon, platform chip, checkmark on valid input, Paste button,
  and Coral accent "Fetch info" button.
- Dismissible wrong-platform alert banner.
- Empty state card with circular download icon, supported domain pills, and 3-step guide.
- Fetched info card with green status badge, duration pill, resolution pill, title, and metadata.
- Options card with Video | Audio only segmented toggle, quality dropdown (SVG chevron),
  format chips (MP4/MOV/GIF or MP3/M4A/WAV), filename with extension label, and folder selector.
- Direct download action area with format/size summary and primary "Download video/audio" button.
- Step-by-step INFO logging and robust background fetch worker with watchdog timeout.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
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
from clipgrab.core.downloader import DownloadRequest, DownloadWorker
from clipgrab.core.formats import AudioFormat, AudioOutputFormat, VideoFormat, VideoInfo, parse_video_info
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import DownloadEngine, detect_platform
from clipgrab.core.validator import clean_media_url, sanitize_filename, suggest_filename
from clipgrab.gui.icons import get_svg_icon, get_svg_pixmap
from clipgrab.gui.theme import (
    BG_APP,
    BG_CARD,
    BG_CARD_ALT,
    BG_INPUT,
    BG_PILL,
    BORDER_CARD,
    BORDER_CARD_SUBTLE,
    BORDER_DIVIDER,
    BORDER_INPUT,
    BORDER_PILL,
    COLOR_ACCENT,
    COLOR_ERROR,
    COLOR_ERROR_BG,
    COLOR_SUCCESS,
    COLOR_WARNING,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_font,
)
from clipgrab.gui.widgets.flow_layout import FlowLayout

logger = get_logger("social_page")


def _format_time(seconds: int) -> str:
    """Format seconds into M:SS or H:MM:SS."""
    if seconds < 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class SocialChipButton(QPushButton):
    """Output format chip button (MP4, MOV, GIF, MP3, M4A, WAV)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._is_active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(32)
        self.setFixedWidth(64)
        self.setFont(get_font(pixel_size=12, weight=QFont.Weight.DemiBold))
        self._update_style()

    def set_active(self, active: bool) -> None:
        if self._is_active != active:
            self._is_active = active
            self._update_style()

    def is_active(self) -> bool:
        return self._is_active

    def _update_style(self) -> None:
        app = QApplication.instance()
        is_dark = (app.property("current_theme") != "light") if app else True
        if self._is_active:
            self.setStyleSheet(
                "QPushButton {"
                f"  background-color: rgba(255, 92, 77, 0.08);"
                f"  color: {COLOR_ACCENT};"
                f"  border: 1px solid {COLOR_ACCENT};"
                "  border-radius: 8px;"
                "  font-weight: 600;"
                "}"
            )
        else:
            bg = BG_CARD_ALT if is_dark else "#EEF1F6"
            fg = TEXT_SECONDARY if is_dark else "#111318"
            border = BORDER_CARD_SUBTLE if is_dark else "#D8DCE5"
            hover_bg = "#262A37" if is_dark else "#E2E6EE"
            self.setStyleSheet(
                "QPushButton {"
                f"  background-color: {bg};"
                f"  color: {fg};"
                f"  border: 1px solid {border};"
                "  border-radius: 8px;"
                "}"
                f"QPushButton:hover {{"
                f"  background-color: {hover_bg};"
                f"  color: {fg};"
                f"}}"
            )


class SocialSegmentedToggle(QFrame):
    """Segmented Video | Audio only mode switch."""

    toggled = Signal(str)  # "video" or "audio"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("socialSegmentedToggle")
        self.setFixedHeight(38)
        self._mode = "video"

        self.setStyleSheet(
            f"QFrame#socialSegmentedToggle {{ background-color: {BG_INPUT}; border-radius: 12px; border: 1px solid {BORDER_INPUT}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(4)

        self._btn_video = QPushButton("Video")
        self._btn_video.setFixedHeight(32)
        self._btn_video.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))
        self._btn_video.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_video.clicked.connect(lambda: self.set_mode("video"))
        layout.addWidget(self._btn_video)

        self._btn_audio = QPushButton("Audio only")
        self._btn_audio.setFixedHeight(32)
        self._btn_audio.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
        self._btn_audio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_audio.clicked.connect(lambda: self.set_mode("audio"))
        layout.addWidget(self._btn_audio)

        self._update_appearance()

    def set_mode(self, mode: str) -> None:
        if self._mode != mode:
            self._mode = mode
            self._update_appearance()
            self.toggled.emit(mode)

    def get_mode(self) -> str:
        return self._mode

    def _update_appearance(self) -> None:
        app = QApplication.instance()
        is_dark = (app.property("current_theme") != "light") if app else True
        container_bg = BG_INPUT if is_dark else "#EEF1F6"
        container_border = BORDER_INPUT if is_dark else "#D8DCE5"
        self.setStyleSheet(
            f"QFrame#socialSegmentedToggle {{ background-color: {container_bg}; border-radius: 12px; border: 1px solid {container_border}; }}"
        )
        active_style = (
            f"background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 10px;"
        )
        inactive_fg = TEXT_SECONDARY if is_dark else "#5A6170"
        inactive_style = (
            f"background-color: transparent; color: {inactive_fg}; border: none; border-radius: 10px;"
        )
        if self._mode == "video":
            self._btn_video.setStyleSheet(active_style)
            self._btn_video.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))
            self._btn_audio.setStyleSheet(inactive_style)
            self._btn_audio.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
        else:
            self._btn_video.setStyleSheet(inactive_style)
            self._btn_video.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
            self._btn_audio.setStyleSheet(active_style)
            self._btn_audio.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))


class SocialDownloadPage(QWidget):
    """
    Modern, plain social download page for Instagram or Facebook.
    Features instant link fetching, metadata card, quality options, and direct downloading.
    """

    download_requested = Signal(object, object)  # (DownloadRequest, VideoInfo)
    add_to_queue_requested = Signal(object, object)
    status_message = Signal(str)
    status_pill_update = Signal(str, str)
    switch_platform_requested = Signal(str)
    info_fetched = Signal(object)

    def __init__(
        self,
        platform_id: str,
        settings: Optional[AppSettings] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._platform_id = platform_id.lower()
        self._settings = settings
        self._video_info: Optional[VideoInfo] = None
        self._is_audio_only = False
        self._selected_format = "MP4"
        self._full_video_title = ""
        self._full_folder_path = (
            getattr(self._settings, "last_output_folder", None)
            if self._settings and getattr(self._settings, "last_output_folder", None)
            else os.path.join(os.path.expanduser("~"), "Downloads")
        )

        # Background worker state
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[DownloadWorker] = None
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.setInterval(90000)  # 90s timeout
        self._watchdog_timer.timeout.connect(self._on_fetch_timeout)

        # Platform-specific tokens
        self._init_platform_config()
        self._setup_ui()
        self.set_empty_state()

    def _init_platform_config(self) -> None:
        if self._platform_id == "instagram":
            self._platform_name = "Instagram"
            self._icon_name = "instagram"
            self._badge_color = "#E1306C"
            self._badge_bg = "qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #833AB4, stop:0.5 #FD1D1D, stop:1 #FCB045)"
            self._subtitle_text = "Download Instagram reels. Paste a link, fetch its details, then save."
            self._content_types = ["Reel"]
            self._placeholder = "Paste an Instagram reel or video URL..."
            self._domain_chips = ["instagram.com/reel/", "instagram.com/tv/"]
            self._type_singular = "Reel"
        else:
            self._platform_name = "Facebook"
            self._icon_name = "facebook"
            self._badge_color = "#1877F2"
            self._badge_bg = "#1877F2"
            self._subtitle_text = "Download Facebook reels and videos. Paste a link, fetch its details, then save."
            self._content_types = ["Reel", "Video"]
            self._placeholder = "Paste a Facebook reel, video, or watch URL..."
            self._domain_chips = ["facebook.com/reel/", "facebook.com/watch/", "fb.watch/"]
            self._type_singular = "Video"

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        container = QWidget()
        center_row = QHBoxLayout(container)
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self._content_box = QWidget()
        self._content_box.setMaximumWidth(960)
        self._content_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        page_layout = QVBoxLayout(self._content_box)
        page_layout.setContentsMargins(20, 16, 20, 20)
        page_layout.setSpacing(14)

        # ── 1. Header Row (Platform Tile, Title, Subtitle, and Content Type Pills) ───
        header_row = QHBoxLayout()
        header_row.setSpacing(14)

        # Platform Icon Badge
        icon_tile = QFrame()
        icon_tile.setFixedSize(48, 48)
        icon_tile.setStyleSheet(f"background: {self._badge_bg}; border-radius: 12px;")
        tile_layout = QVBoxLayout(icon_tile)
        tile_layout.setContentsMargins(0, 0, 0, 0)
        tile_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(get_svg_pixmap(self._icon_name, color="#FFFFFF", size=24))
        tile_layout.addWidget(icon_lbl)
        header_row.addWidget(icon_tile)

        # Title & Subtitle Column
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._title_lbl = QLabel(self._platform_name)
        self._title_lbl.setFont(get_font(pixel_size=20, weight=QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")
        title_col.addWidget(self._title_lbl)

        self._sub_lbl = QLabel(self._subtitle_text)
        self._sub_lbl.setFont(get_font(pixel_size=12))
        self._sub_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        title_col.addWidget(self._sub_lbl)
        header_row.addLayout(title_col, stretch=1)

        # Content Type Selector Pills
        self._type_buttons: dict[str, QPushButton] = {}
        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        for t_name in self._content_types:
            btn = QPushButton(t_name)
            btn.setFixedHeight(30)
            btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, n=t_name: self._set_content_type(n))
            type_row.addWidget(btn)
            self._type_buttons[t_name] = btn
        header_row.addLayout(type_row)
        page_layout.addLayout(header_row)

        self._active_type = self._content_types[0]
        self._update_type_pill_styles()

        # ── 2. URL Input Bar ──────────────────────────────────────────────────
        url_bar_row = QHBoxLayout()
        url_bar_row.setSpacing(10)

        self._url_input_box = QFrame()
        self._url_input_box.setObjectName("urlInputBox")
        self._url_input_box.setFixedHeight(44)
        self._url_input_box.setStyleSheet(
            f"QFrame#urlInputBox {{ background-color: {BG_INPUT}; border: 1px solid {BORDER_INPUT}; border-radius: 12px; }}"
        )
        url_box_layout = QHBoxLayout(self._url_input_box)
        url_box_layout.setContentsMargins(12, 0, 12, 0)
        url_box_layout.setSpacing(8)

        link_icon_lbl = QLabel()
        link_icon_lbl.setPixmap(get_svg_pixmap("link", color=TEXT_SECONDARY, size=16))
        url_box_layout.addWidget(link_icon_lbl)

        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText(self._placeholder)
        self._url_input.setFont(get_font(pixel_size=14))
        self._url_input.setStyleSheet("QLineEdit { background: transparent; border: none; color: #FFFFFF; padding: 0; }")
        self._url_input.returnPressed.connect(self._on_fetch_clicked)
        self._url_input.textChanged.connect(self._on_url_text_changed)
        url_box_layout.addWidget(self._url_input, stretch=1)

        self._platform_chip = QLabel(self._platform_name)
        self._platform_chip.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        chip_border = "#5C1F38" if self._platform_id == "instagram" else "#1F385C"
        chip_bg = "#2F1721" if self._platform_id == "instagram" else "#17212F"
        chip_fg = "#FF79C6" if self._platform_id == "instagram" else "#58A6FF"
        self._platform_chip.setStyleSheet(
            f"background-color: {chip_bg}; color: {chip_fg}; border: 1px solid {chip_border}; border-radius: 6px; padding: 2px 8px;"
        )
        self._platform_chip.hide()
        url_box_layout.addWidget(self._platform_chip)

        self._valid_check = QLabel()
        self._valid_check.setPixmap(get_svg_pixmap("check", color=COLOR_SUCCESS, size=14))
        self._valid_check.hide()
        url_box_layout.addWidget(self._valid_check)

        url_bar_row.addWidget(self._url_input_box, stretch=1)

        self._paste_btn = QPushButton("Paste")
        self._paste_btn.setFixedHeight(44)
        self._paste_btn.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
        self._paste_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paste_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_CARD}; border-radius: 10px; padding: 0 16px; }}"
            f"QPushButton:hover {{ background-color: #1C1F2B; }}"
        )
        self._paste_btn.clicked.connect(self._on_paste_clicked)
        url_bar_row.addWidget(self._paste_btn)

        self._fetch_btn = QPushButton("Fetch info")
        self._fetch_btn.setFixedHeight(44)
        self._fetch_btn.setFont(get_font(pixel_size=14, weight=QFont.Weight.DemiBold))
        self._fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fetch_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 12px; padding: 0 18px; min-width: 90px; }}"
            f"QPushButton:hover {{ background-color: #FF6957; }}"
            f"QPushButton:disabled {{ background-color: #351C21; color: {TEXT_MUTED}; }}"
        )
        self._fetch_btn.clicked.connect(self._on_fetch_clicked)
        url_bar_row.addWidget(self._fetch_btn)

        page_layout.addLayout(url_bar_row)

        # ── 3. Wrong-Platform Alert Banner ────────────────────────────────────
        self._wrong_platform_banner = QFrame()
        self._wrong_platform_banner.setObjectName("wrongPlatformBanner")
        self._wrong_platform_banner.setStyleSheet(
            f"QFrame#wrongPlatformBanner {{ background-color: rgba(245, 158, 11, 0.12); border: 1px solid {COLOR_WARNING}; border-radius: 10px; padding: 4px; }}"
        )
        wp_layout = QHBoxLayout(self._wrong_platform_banner)
        wp_layout.setContentsMargins(12, 8, 12, 8)
        wp_layout.setSpacing(10)

        wp_icon = QLabel()
        wp_icon.setPixmap(get_svg_pixmap("alert-triangle", color=COLOR_WARNING, size=16))
        wp_layout.addWidget(wp_icon)

        self._wp_label = QLabel("This URL belongs to another platform.")
        self._wp_label.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
        self._wp_label.setStyleSheet("color: #FBBF24;")
        wp_layout.addWidget(self._wp_label, stretch=1)

        self._wp_switch_btn = QPushButton("Open in Section")
        self._wp_switch_btn.setFixedHeight(28)
        self._wp_switch_btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.DemiBold))
        self._wp_switch_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_WARNING}; color: #000000; border: none; border-radius: 6px; padding: 0 10px; }}"
        )
        self._wp_target_id = "home"
        self._wp_switch_btn.clicked.connect(lambda: self.switch_platform_requested.emit(self._wp_target_id))
        wp_layout.addWidget(self._wp_switch_btn)

        wp_dismiss_btn = QPushButton()
        wp_dismiss_btn.setFixedSize(24, 24)
        wp_dismiss_btn.setIcon(get_svg_icon("x", color=TEXT_SECONDARY, size=14))
        wp_dismiss_btn.setStyleSheet("background: transparent; border: none;")
        wp_dismiss_btn.clicked.connect(self._wrong_platform_banner.hide)
        wp_layout.addWidget(wp_dismiss_btn)

        self._wrong_platform_banner.hide()
        page_layout.addWidget(self._wrong_platform_banner)

        # ── 4. Empty State Card ───────────────────────────────────────────────
        self._empty_card = QFrame()
        self._empty_card.setObjectName("emptyCard")
        self._empty_card.setStyleSheet(
            f"QFrame#emptyCard {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD}; border-radius: 16px; }}"
        )
        empty_layout = QVBoxLayout(self._empty_card)
        empty_layout.setContentsMargins(32, 36, 32, 36)
        empty_layout.setSpacing(16)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Download circular badge
        self._badge_circle = QFrame()
        self._badge_circle.setObjectName("badgeCircle")
        self._badge_circle.setFixedSize(56, 56)
        self._badge_circle.setStyleSheet(f"QFrame#badgeCircle {{ background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 28px; }}")
        bc_layout = QVBoxLayout(self._badge_circle)
        bc_layout.setContentsMargins(0, 0, 0, 0)
        bc_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bc_icon = QLabel()
        bc_icon.setPixmap(get_svg_pixmap("download", color=COLOR_ACCENT, size=24))
        bc_layout.addWidget(bc_icon)
        empty_layout.addWidget(self._badge_circle, alignment=Qt.AlignmentFlag.AlignCenter)

        self._empty_heading = QLabel(f"Paste a {self._platform_name} link to get started")
        self._empty_heading.setFont(get_font(pixel_size=18, weight=QFont.Weight.Bold))
        self._empty_heading.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self._empty_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self._empty_heading)

        self._empty_desc = QLabel(
            f"Copy the link of a {self._platform_name.lower()} reel or video from your browser or app, paste it above, and press Fetch info."
        )
        self._empty_desc.setFont(get_font(pixel_size=13))
        self._empty_desc.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._empty_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_desc.setWordWrap(True)
        empty_layout.addWidget(self._empty_desc)

        # Supported domain pills
        domains_container = QWidget()
        domains_flow = FlowLayout(domains_container, margin=0, h_spacing=8, v_spacing=6)
        self._domain_pills: list[QLabel] = []
        for dom in self._domain_chips:
            dpill = QLabel(dom)
            dpill.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
            dpill.setStyleSheet(
                f"background-color: {BG_PILL}; color: {TEXT_MUTED}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 4px 10px;"
            )
            domains_flow.addWidget(dpill)
            self._domain_pills.append(dpill)
        empty_layout.addWidget(domains_container, alignment=Qt.AlignmentFlag.AlignCenter)

        # 3-Step Guide Container
        self._steps_box = QFrame()
        self._steps_box.setObjectName("stepsBox")
        self._steps_box.setStyleSheet(f"QFrame#stepsBox {{ background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 12px; }}")
        steps_layout = QHBoxLayout(self._steps_box)
        steps_layout.setContentsMargins(18, 16, 18, 16)
        steps_layout.setSpacing(16)

        steps_data = [
            ("1", "Paste the link", f"Copy from {self._platform_name} and paste in the input box above"),
            ("2", "Fetch its details", "We analyze the video, quality options, and available audio tracks"),
            ("3", "Download video or audio", "Choose your format and save directly to your computer"),
        ]
        self._step_badges: list[QLabel] = []
        self._step_titles: list[QLabel] = []
        self._step_descs: list[QLabel] = []
        for num, title, detail in steps_data:
            step_item = QHBoxLayout()
            step_item.setSpacing(10)

            num_badge = QLabel(num)
            num_badge.setFixedSize(26, 26)
            num_badge.setFont(get_font(pixel_size=12, weight=QFont.Weight.Bold))
            num_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_badge.setStyleSheet(
                f"background-color: {BG_PILL}; color: {COLOR_ACCENT}; border: 1px solid {BORDER_PILL}; border-radius: 13px;"
            )
            step_item.addWidget(num_badge, alignment=Qt.AlignmentFlag.AlignTop)
            self._step_badges.append(num_badge)

            st_col = QVBoxLayout()
            st_col.setSpacing(2)
            st_title = QLabel(title)
            st_title.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))
            st_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
            st_col.addWidget(st_title)
            self._step_titles.append(st_title)

            st_desc = QLabel(detail)
            st_desc.setFont(get_font(pixel_size=11))
            st_desc.setStyleSheet(f"color: {TEXT_MUTED};")
            st_desc.setWordWrap(True)
            st_col.addWidget(st_desc)
            self._step_descs.append(st_desc)

            step_item.addLayout(st_col, stretch=1)
            steps_layout.addLayout(step_item, stretch=1)

        empty_layout.addWidget(self._steps_box)
        page_layout.addWidget(self._empty_card)

        # ── 5. Fetched State: Info Card ───────────────────────────────────────
        self._info_card = QFrame()
        self._info_card.setObjectName("socialInfoCard")
        self._info_card.setStyleSheet(
            f"QFrame#socialInfoCard {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD}; border-radius: 16px; }}"
        )
        info_layout = QVBoxLayout(self._info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(8)

        # Header with badge and pills
        info_top = QHBoxLayout()
        info_top.setSpacing(8)

        self._status_badge = QLabel(f"● {self._type_singular} found")
        self._status_badge.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))
        self._status_badge.setStyleSheet(f"color: {COLOR_SUCCESS};")
        info_top.addWidget(self._status_badge)
        info_top.addStretch()

        self._dur_pill = QLabel("00:00")
        self._dur_pill.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._dur_pill.setStyleSheet(
            f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border-radius: 6px; padding: 3px 8px; border: 1px solid {BORDER_PILL};"
        )
        info_top.addWidget(self._dur_pill)

        self._res_pill = QLabel("1080p")
        self._res_pill.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._res_pill.setStyleSheet(
            f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border-radius: 6px; padding: 3px 8px; border: 1px solid {BORDER_PILL};"
        )
        info_top.addWidget(self._res_pill)
        info_layout.addLayout(info_top)

        # Title
        self._video_title = QLabel("Retrieving details...")
        self._video_title.setFont(get_font(pixel_size=16, weight=QFont.Weight.DemiBold))
        self._video_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self._video_title.setWordWrap(True)
        info_layout.addWidget(self._video_title)

        # Subtitle / author
        self._video_meta = QLabel("")
        self._video_meta.setFont(get_font(pixel_size=12))
        self._video_meta.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._video_meta.setWordWrap(True)
        info_layout.addWidget(self._video_meta)

        # Error action buttons (error state)
        self._error_btns_row = QWidget()
        err_layout = QHBoxLayout(self._error_btns_row)
        err_layout.setContentsMargins(0, 0, 0, 0)
        err_layout.setSpacing(10)

        self._retry_btn = QPushButton("Retry Fetch")
        self._retry_btn.setFixedHeight(32)
        self._retry_btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.DemiBold))
        self._retry_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 8px; padding: 0 14px; }}"
        )
        self._retry_btn.clicked.connect(self._on_fetch_clicked)
        err_layout.addWidget(self._retry_btn)
        err_layout.addStretch()

        self._error_btns_row.hide()
        info_layout.addWidget(self._error_btns_row)

        self._info_card.hide()
        page_layout.addWidget(self._info_card)

        # ── 6. Fetched State: Options Card (Quality, Format, Save) ───────────
        self._options_card = QFrame()
        self._options_card.setObjectName("socialOptionsCard")
        self._options_card.setStyleSheet(
            f"QFrame#socialOptionsCard {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD}; border-radius: 16px; }}"
        )
        opt_layout = QVBoxLayout(self._options_card)
        opt_layout.setContentsMargins(18, 16, 18, 16)
        opt_layout.setSpacing(12)

        self._opt_title = QLabel("Quality and format")
        self._opt_title.setFont(get_font(pixel_size=16, weight=QFont.Weight.DemiBold))
        self._opt_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        opt_layout.addWidget(self._opt_title)

        self._mode_toggle = SocialSegmentedToggle()
        self._mode_toggle.toggled.connect(self._on_mode_toggled)
        opt_layout.addWidget(self._mode_toggle)

        self._quality_lbl = QLabel("Video quality")
        self._quality_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._quality_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        opt_layout.addWidget(self._quality_lbl)

        self._quality_combo = QComboBox()
        self._quality_combo.setFixedHeight(36)
        self._quality_combo.setFont(get_font(pixel_size=13))
        self._quality_combo.addItem("Best available · 1080p")
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        opt_layout.addWidget(self._quality_combo)

        self._fmt_lbl = QLabel("Output format")
        self._fmt_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fmt_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        opt_layout.addWidget(self._fmt_lbl)

        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(8)
        self._format_chips: dict[str, SocialChipButton] = {}
        self._build_format_chips(["MP4", "MOV", "GIF"])
        opt_layout.addLayout(self._chips_row)

        # Save section inside Options
        self._save_lbl = QLabel("Save")
        self._save_lbl.setFont(get_font(pixel_size=14, weight=QFont.Weight.DemiBold))
        self._save_lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; margin-top: 6px;")
        opt_layout.addWidget(self._save_lbl)

        self._fn_lbl = QLabel("File name")
        self._fn_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fn_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        opt_layout.addWidget(self._fn_lbl)

        self._fn_box = QFrame()
        self._fn_box.setFixedHeight(36)
        self._fn_box.setStyleSheet(f"QFrame {{ background-color: {BG_INPUT}; border: 1px solid {BORDER_INPUT}; border-radius: 10px; }}")
        fn_box_layout = QHBoxLayout(self._fn_box)
        fn_box_layout.setContentsMargins(10, 0, 10, 0)
        fn_box_layout.setSpacing(4)

        self._filename_input = QLineEdit()
        self._filename_input.setFont(get_font(pixel_size=13))
        self._filename_input.setStyleSheet("background: transparent; border: none; color: #FFFFFF;")
        self._filename_input.setPlaceholderText("Auto-generated from title")
        fn_box_layout.addWidget(self._filename_input, stretch=1)

        self._ext_lbl = QLabel(".mp4")
        self._ext_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._ext_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        fn_box_layout.addWidget(self._ext_lbl)
        opt_layout.addWidget(self._fn_box)

        self._fd_lbl = QLabel("Folder")
        self._fd_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fd_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        opt_layout.addWidget(self._fd_lbl)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)

        self._folder_input = QLineEdit(self._full_folder_path)
        self._folder_input.setFixedHeight(36)
        self._folder_input.setReadOnly(True)
        self._folder_input.setFont(get_font(pixel_size=12))
        self._folder_input.setToolTip(self._full_folder_path)
        self._folder_input.setStyleSheet(
            f"QLineEdit {{ background-color: {BG_INPUT}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_INPUT}; border-radius: 10px; padding: 0 10px; }}"
        )
        folder_row.addWidget(self._folder_input, stretch=1)

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setFixedHeight(36)
        self._browse_btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_PILL}; border-radius: 10px; padding: 0 14px; }}"
            f"QPushButton:hover {{ background-color: #262A37; }}"
        )
        self._browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._browse_btn)
        opt_layout.addLayout(folder_row)

        self._options_card.hide()
        page_layout.addWidget(self._options_card)

        # ── 7. Action Area: Summary & Download Button ────────────────────────
        self._actions_box = QWidget()
        actions_layout = QHBoxLayout(self._actions_box)
        actions_layout.setContentsMargins(0, 4, 0, 0)
        actions_layout.setSpacing(12)

        self._summary_lbl = QLabel("")
        self._summary_lbl.setFont(get_font(pixel_size=13, weight=QFont.Weight.Medium))
        self._summary_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        actions_layout.addWidget(self._summary_lbl)
        actions_layout.addStretch()

        self._download_btn = QPushButton(f"  Download {self._type_singular.lower()}")
        self._download_btn.setIcon(get_svg_icon("download", color="#FFFFFF", size=16))
        self._download_btn.setFixedHeight(44)
        self._download_btn.setFont(get_font(pixel_size=14, weight=QFont.Weight.DemiBold))
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 12px; font-weight: 600; padding: 0 24px; }}"
            f"QPushButton:hover {{ background-color: #FF6957; }}"
            f"QPushButton:disabled {{ background-color: #351C21; color: {TEXT_MUTED}; }}"
        )
        self._download_btn.clicked.connect(self._on_download_clicked)
        actions_layout.addWidget(self._download_btn)

        self._actions_box.hide()
        page_layout.addWidget(self._actions_box)

        page_layout.addStretch(1)
        center_row.addWidget(self._content_box)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

    def _set_content_type(self, t_name: str) -> None:
        self._active_type = t_name
        self._update_type_pill_styles()
        self._update_download_button_label()

    def _update_type_pill_styles(self) -> None:
        for name, btn in self._type_buttons.items():
            if name == self._active_type:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: rgba(255, 92, 77, 0.12); color: {COLOR_ACCENT}; border: 1px solid {COLOR_ACCENT}; border-radius: 15px; padding: 0 12px; font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 15px; padding: 0 12px; }}"
                    f"QPushButton:hover {{ background-color: #262A37; color: {TEXT_PRIMARY}; }}"
                )

    def _build_format_chips(self, formats: list[str]) -> None:
        while self._chips_row.count():
            item = self._chips_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._format_chips.clear()

        for fmt in formats:
            chip = SocialChipButton(fmt)
            chip.clicked.connect(lambda checked=False, f=fmt: self._on_format_chip_clicked(f))
            self._chips_row.addWidget(chip)
            self._format_chips[fmt] = chip

        self._chips_row.addStretch()
        if formats:
            self._on_format_chip_clicked(formats[0])

    def _on_format_chip_clicked(self, fmt: str) -> None:
        self._selected_format = fmt
        for name, chip in self._format_chips.items():
            chip.set_active(name == fmt)
        ext = fmt.lower()
        if hasattr(self, "_ext_lbl"):
            self._ext_lbl.setText(f".{ext}")
        self._update_summary_label()

    def _on_mode_toggled(self, mode: str) -> None:
        self._is_audio_only = (mode == "audio")
        if self._is_audio_only:
            self._quality_lbl.setText("Audio quality")
            self._build_format_chips(["MP3", "M4A", "WAV"])
            self._rebuild_quality_combo_for_audio()
        else:
            self._quality_lbl.setText("Video quality")
            self._build_format_chips(["MP4", "MOV", "GIF"])
            self._rebuild_quality_combo_for_video()
        self._update_download_button_label()

    def _update_download_button_label(self) -> None:
        if self._is_audio_only:
            self._download_btn.setText("  Download audio")
        else:
            self._download_btn.setText(f"  Download {self._active_type.lower()}")

    def _update_summary_label(self) -> None:
        if not hasattr(self, "_summary_lbl") or not hasattr(self, "_quality_combo"):
            return
        quality = self._quality_combo.currentText().split("·")[0].strip() if self._quality_combo.count() else ""
        size_est = "≈ 18 MB"
        self._summary_lbl.setText(f"{self._selected_format} · {quality} · {size_est}")

    def _on_quality_changed(self) -> None:
        self._update_summary_label()

    def _rebuild_quality_combo_for_video(self) -> None:
        self._quality_combo.clear()
        if self._video_info and self._video_info.video_formats:
            for vf in self._video_info.video_formats[:6]:
                fps_s = f" {vf.fps}fps" if vf.fps and vf.fps > 30 else ""
                vcodec_s = f" ({vf.vcodec})" if vf.vcodec and "none" not in vf.vcodec.lower() else ""
                self._quality_combo.addItem(f"{vf.resolution}{fps_s}{vcodec_s}", userData=vf)
        else:
            self._quality_combo.addItem("1080p (best) · ~18 MB")
            self._quality_combo.addItem("720p · ~12 MB")
            self._quality_combo.addItem("480p · ~7 MB")
        self._update_summary_label()

    def _rebuild_quality_combo_for_audio(self) -> None:
        self._quality_combo.clear()
        if self._video_info and self._video_info.audio_formats:
            for af in self._video_info.audio_formats[:4]:
                abr_s = f" ({af.abr}kbps)" if af.abr else ""
                acodec_s = af.acodec if af.acodec and "none" not in af.acodec.lower() else "audio"
                self._quality_combo.addItem(f"{acodec_s}{abr_s}", userData=af)
        else:
            self._quality_combo.addItem("Best available · 320 kbps")
            self._quality_combo.addItem("Standard · 192 kbps")
            self._quality_combo.addItem("Compact · 128 kbps")
        self._update_summary_label()

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text().strip()
            if text:
                cleaned = clean_media_url(text)
                self._url_input.setText(cleaned)
                self._on_fetch_clicked()

    def _on_url_text_changed(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            self._platform_chip.hide()
            self._valid_check.hide()
            self._wrong_platform_banner.hide()
            return

        plat = detect_platform(clean)
        plat_id = plat.id.lower() if plat else "unsupported"
        if plat_id == self._platform_id:
            self._platform_chip.show()
            self._valid_check.show()
            self._wrong_platform_banner.hide()
            # Auto-select type pill based on URL pattern
            clean_lower = clean.lower()
            if "/reel" in clean_lower or "/share/r" in clean_lower or "/share/reel" in clean_lower:
                self._set_content_type("Reel")
            elif "/watch" in clean_lower or "fb.watch" in clean_lower or "/video" in clean_lower or "/share/v" in clean_lower:
                self._set_content_type("Video" if "Video" in self._type_buttons else "Reel")
            elif "/p/" in clean_lower or "/post" in clean_lower or "/share/p" in clean_lower:
                self._set_content_type("Video" if "Video" in self._type_buttons else "Reel")
        elif plat.engine != DownloadEngine.UNSUPPORTED and plat_id != "unsupported":
            self._platform_chip.hide()
            self._valid_check.hide()
            self._wp_target_id = plat_id
            other_name = plat.display_name if plat else "another platform"
            self._wp_label.setText(f"This URL belongs to {other_name}.")
            self._wp_switch_btn.setText(f"Open in {other_name}")
            self._wrong_platform_banner.show()
        else:
            self._platform_chip.hide()
            self._valid_check.hide()
            self._wrong_platform_banner.hide()

    def _on_browse_folder(self) -> None:
        init_dir = self._full_folder_path or self._folder_input.text()
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder", init_dir)
        if folder:
            self._full_folder_path = folder
            self._folder_input.setText(folder)
            self._folder_input.setToolTip(folder)
            if self._settings:
                self._settings.update(last_output_folder=folder)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATES MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_empty_state(self) -> None:
        self._video_info = None
        self._url_input.clear()
        self._platform_chip.hide()
        self._valid_check.hide()
        self._wrong_platform_banner.hide()
        self._empty_card.show()
        self._info_card.hide()
        self._options_card.hide()
        self._actions_box.hide()
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

    def set_fetching_state(self) -> None:
        self._empty_card.hide()
        self._info_card.show()
        self._options_card.hide()
        self._actions_box.hide()
        if hasattr(self, "_error_btns_row"):
            self._error_btns_row.hide()
        self._status_badge.setText("● Fetching info...")
        self._status_badge.setStyleSheet(f"color: {COLOR_WARNING};")
        self._video_title.setText("Retrieving details from server...")
        self._video_meta.setText("Extracting available qualities and formats...")
        self._dur_pill.setText("--:--")
        self._res_pill.setText("---")
        self._fetch_btn.setText("Fetching...")
        self._fetch_btn.setEnabled(False)

    def set_loaded_state(self, video_info: VideoInfo) -> None:
        self._video_info = video_info
        self._empty_card.hide()
        self._info_card.show()
        self._options_card.show()
        self._actions_box.show()
        if hasattr(self, "_error_btns_row"):
            self._error_btns_row.hide()
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

        if video_info.duration and video_info.duration > 0:
            dur_str = _format_time(video_info.duration)
        else:
            dur_str = "Media"
        self._status_badge.setText(f"● {self._type_singular} found")
        self._status_badge.setStyleSheet(f"color: {COLOR_SUCCESS};")
        self._dur_pill.setText(dur_str)
        best_res = video_info.video_formats[0].resolution if video_info.video_formats else "Best"
        self._res_pill.setText(best_res)

        title = video_info.title
        if not title or title.strip() in ("NA", "None", ""):
            desc = getattr(video_info, "description", "")
            title = desc.split("\n")[0][:80] if desc else f"{self._platform_name} Media"

        self._full_video_title = title
        self._video_title.setText(title)
        self._video_title.setToolTip(title)

        meta_parts = []
        if getattr(video_info, "uploader", None):
            meta_parts.append(f"@{video_info.uploader}" if not video_info.uploader.startswith("@") else video_info.uploader)
        if getattr(video_info, "upload_date", None):
            meta_parts.append(video_info.upload_date)
        self._video_meta.setText(" · ".join(meta_parts) if meta_parts else f"{self._platform_name} media")

        self._filename_input.setText(suggest_filename(title))
        self._rebuild_quality_combo_for_video()
        self._update_summary_label()
        self.info_fetched.emit(video_info)

    def set_error_state(self, error_msg: str) -> None:
        self._empty_card.hide()
        self._info_card.show()
        self._options_card.hide()
        self._actions_box.hide()
        if hasattr(self, "_error_btns_row"):
            self._error_btns_row.show()
            self._retry_btn.show()
        self._status_badge.setText("● Fetch failed")
        self._status_badge.setStyleSheet(f"color: {COLOR_ERROR};")
        self._video_title.setText("Could not fetch media info")
        self._video_meta.setText(error_msg[:300])
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FETCH WORKER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _on_fetch_clicked(self) -> None:
        url = self._url_input.text().strip()
        if not url:
            return

        url = clean_media_url(url)
        self._url_input.setText(url)

        plat = detect_platform(url)
        plat_id = plat.id.lower() if plat else "unsupported"
        if plat.engine != DownloadEngine.UNSUPPORTED and plat_id != self._platform_id:
            self._on_url_text_changed(url)
            return

        self._shutdown_fetch_worker()
        self.set_fetching_state()
        self.status_message.emit(f"Fetching {self._platform_name} info...")

        self._fetch_thread = QThread(self)
        self._fetch_worker = DownloadWorker()
        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_worker.fetch_url = url
        self._fetch_worker.fetch_platform_id = self._platform_id

        self._fetch_worker.info_fetched.connect(self._on_worker_info_fetched)
        self._fetch_worker.info_fetch_failed.connect(self._on_worker_fetch_failed)

        self._fetch_thread.started.connect(self._fetch_worker.execute_fetch, Qt.ConnectionType.DirectConnection)
        self._fetch_thread.finished.connect(self._on_fetch_thread_finished)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)

        self._watchdog_timer.start()
        self._fetch_thread.start()
        logger.info("[%s] Fetch worker thread started for url: %s", self._platform_name.upper(), url)

    def _on_fetch_timeout(self) -> None:
        logger.error("[%s] Fetch timed out after 90 seconds", self._platform_name.upper())
        self._shutdown_fetch_worker()
        self.set_error_state("Fetch request timed out. Please check your network connection and click Retry Fetch.")
        self.status_message.emit("Fetch timed out.")

    @Slot(object)
    def _on_worker_info_fetched(self, video_info: VideoInfo) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
        logger.info("[%s] Info fetched successfully: '%s'", self._platform_name.upper(), video_info.title)
        self.set_loaded_state(video_info)
        self.status_message.emit(f"Ready: {video_info.title[:40]}")

    @Slot(str, bool)
    def _on_worker_fetch_failed(self, error_msg: str, is_extraction: bool = False) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
        logger.error("[%s] Fetch failed: %s", self._platform_name.upper(), error_msg)
        self.set_error_state(error_msg)
        self.status_message.emit("Fetch failed.")

    def _on_fetch_thread_finished(self) -> None:
        self._fetch_thread = None
        self._fetch_worker = None

    def _shutdown_fetch_worker(self) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()
        if self._fetch_worker:
            try:
                self._fetch_worker.request_cancel()
            except Exception as e:
                logger.warning("[%s] Error cancelling fetch worker: %s", self._platform_name.upper(), e)
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            if not self._fetch_thread.wait(1500):
                self._fetch_thread.terminate()
                self._fetch_thread.wait(500)
        self._fetch_thread = None
        self._fetch_worker = None

    def shutdown(self) -> None:
        self._shutdown_fetch_worker()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DOWNLOAD ACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _on_download_clicked(self) -> None:
        if not self._video_info:
            return

        url = self._url_input.text().strip()
        output_dir = self._full_folder_path or os.path.join(os.path.expanduser("~"), "Downloads")
        filename = self._filename_input.text().strip() or suggest_filename(self._video_info.title)

        selected_vf = self._quality_combo.currentData() if not self._is_audio_only else None
        selected_af = self._quality_combo.currentData() if self._is_audio_only else None

        req = DownloadRequest(
            url=url,
            output_dir=output_dir,
            filename=filename,
            audio_only=self._is_audio_only,
            merge_format=self._selected_format.lower(),
            convert_to_gif=(self._selected_format == "GIF"),
            video_format=selected_vf if isinstance(selected_vf, VideoFormat) else None,
            audio_format=selected_af if isinstance(selected_af, AudioFormat) else None,
            platform_id=self._platform_id,
            concurrent_fragments=getattr(self._settings, "concurrent_fragments", 16) if self._settings else 16,
        )

        logger.info("[%s] Download requested: '%s' -> %s", self._platform_name.upper(), filename, output_dir)
        self.download_requested.emit(req, self._video_info)

    def set_url(self, url: str) -> None:
        """Prefill URL into input and trigger fetch."""
        self._url_input.setText(url)
        self._on_fetch_clicked()

    def update_theme(self, theme_name: str) -> None:
        """Update social page elements for dark/light theme."""
        is_dark = (theme_name == "dark")
        card_bg = BG_CARD if is_dark else "#FFFFFF"
        card_border = BORDER_CARD if is_dark else "#DCE0E8"
        card_alt_bg = BG_CARD_ALT if is_dark else "#F5F7FA"
        card_alt_border = BORDER_CARD_SUBTLE if is_dark else "#E2E5EC"
        input_bg = BG_INPUT if is_dark else "#FFFFFF"
        input_border = BORDER_INPUT if is_dark else "#CCD1DC"
        text_primary = TEXT_PRIMARY if is_dark else "#111318"
        text_secondary = TEXT_SECONDARY if is_dark else "#5A6170"
        text_muted = TEXT_MUTED if is_dark else "#8A92A2"
        pill_bg = BG_PILL if is_dark else "#EEF1F6"
        pill_border = BORDER_PILL if is_dark else "#CCD1DC"
        input_fg = "#FFFFFF" if is_dark else "#111318"

        if hasattr(self, "_title_lbl"):
            self._title_lbl.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_sub_lbl"):
            self._sub_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_url_input_box"):
            self._url_input_box.setStyleSheet(
                f"QFrame#urlInputBox {{ background-color: {input_bg}; border: 1px solid {input_border}; border-radius: 12px; }}"
            )
        if hasattr(self, "_url_input"):
            self._url_input.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {input_fg}; padding: 0; }}")
        if hasattr(self, "_paste_btn"):
            paste_hover = "#1C1F2B" if is_dark else "#F5F7FA"
            self._paste_btn.setStyleSheet(
                f"QPushButton {{ background-color: {card_bg}; color: {text_primary}; border: 1px solid {card_border}; border-radius: 10px; padding: 0 16px; }}"
                f"QPushButton:hover {{ background-color: {paste_hover}; }}"
            )
        if hasattr(self, "_empty_card"):
            self._empty_card.setStyleSheet(
                f"QFrame#emptyCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }}"
            )
        if hasattr(self, "_badge_circle"):
            self._badge_circle.setStyleSheet(
                f"QFrame#badgeCircle {{ background-color: {card_alt_bg}; border: 1px solid {card_alt_border}; border-radius: 28px; }}"
            )
        if hasattr(self, "_empty_heading"):
            self._empty_heading.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_empty_desc"):
            self._empty_desc.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_steps_box"):
            self._steps_box.setStyleSheet(
                f"QFrame#stepsBox {{ background-color: {card_alt_bg}; border: 1px solid {card_alt_border}; border-radius: 12px; }}"
            )
        if hasattr(self, "_info_card"):
            self._info_card.setStyleSheet(
                f"QFrame#socialInfoCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }}"
            )
        if hasattr(self, "_video_title"):
            self._video_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_video_meta"):
            self._video_meta.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_dur_pill"):
            self._dur_pill.setStyleSheet(
                f"background-color: {pill_bg}; color: {text_secondary}; border-radius: 6px; padding: 3px 8px; border: 1px solid {pill_border};"
            )
        if hasattr(self, "_res_pill"):
            self._res_pill.setStyleSheet(
                f"background-color: {pill_bg}; color: {text_secondary}; border-radius: 6px; padding: 3px 8px; border: 1px solid {pill_border};"
            )
        if hasattr(self, "_options_card"):
            self._options_card.setStyleSheet(
                f"QFrame#socialOptionsCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 16px; }}"
            )
        if hasattr(self, "_opt_title"):
            self._opt_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_quality_lbl"):
            self._quality_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_fmt_lbl"):
            self._fmt_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_save_lbl"):
            self._save_lbl.setStyleSheet(f"color: {text_primary}; margin-top: 6px;")
        if hasattr(self, "_fn_lbl"):
            self._fn_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_fd_lbl"):
            self._fd_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_fn_box"):
            self._fn_box.setStyleSheet(f"QFrame {{ background-color: {input_bg}; border: 1px solid {input_border}; border-radius: 10px; }}")
        if hasattr(self, "_filename_input"):
            self._filename_input.setStyleSheet(f"background: transparent; border: none; color: {input_fg};")
        if hasattr(self, "_ext_lbl"):
            self._ext_lbl.setStyleSheet(f"color: {text_muted};")
        if hasattr(self, "_folder_input"):
            self._folder_input.setStyleSheet(
                f"QLineEdit {{ background-color: {input_bg}; color: {text_secondary}; border: 1px solid {input_border}; border-radius: 10px; padding: 0 10px; }}"
            )
        if hasattr(self, "_browse_btn"):
            browse_hover = "#262A37" if is_dark else "#E2E6EE"
            self._browse_btn.setStyleSheet(
                f"QPushButton {{ background-color: {pill_bg}; color: {text_primary}; border: 1px solid {pill_border}; border-radius: 10px; padding: 0 14px; }}"
                f"QPushButton:hover {{ background-color: {browse_hover}; }}"
            )
        if hasattr(self, "_summary_lbl"):
            self._summary_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_mode_toggle"):
            self._mode_toggle._update_appearance()
        if hasattr(self, "_domain_pills"):
            for dpill in self._domain_pills:
                dpill.setStyleSheet(f"background-color: {pill_bg}; color: {text_muted}; border: 1px solid {pill_border}; border-radius: 6px; padding: 4px 10px;")
        if hasattr(self, "_step_badges"):
            for sbadge in self._step_badges:
                sbadge.setStyleSheet(f"background-color: {pill_bg}; color: {COLOR_ACCENT}; border: 1px solid {pill_border}; border-radius: 13px;")
        if hasattr(self, "_step_titles"):
            for st in self._step_titles:
                st.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_step_descs"):
            for sd in self._step_descs:
                sd.setStyleSheet(f"color: {text_muted};")
        self._update_type_pill_styles()
        for chip in getattr(self, "_format_chips", {}).values():
            chip._update_style()
