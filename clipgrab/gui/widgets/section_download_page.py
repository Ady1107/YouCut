"""
YouCut — Configurable Shared Section Download Page.

Reuses existing UrlInputWidget, FormatSelectorWidget, TimeRangeWidget,
and DownloadControlsWidget driven by a SectionConfig. Panels that don't apply
are hidden (not disabled), ensuring zero code duplication across sub-sections.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clipgrab.config.settings import AppSettings
from clipgrab.core.formats import VideoInfo
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import DownloadEngine, detect_platform
from clipgrab.gui.sections import (
    SECTION_YOUTUBE_VIDEO,
    SectionConfig,
    get_section_by_id,
    route_url_to_section,
)
from clipgrab.gui.widgets.download_controls import DownloadControlsWidget
from clipgrab.gui.widgets.format_selector import FormatSelectorWidget
from clipgrab.gui.widgets.time_range_input import TimeRangeWidget
from clipgrab.gui.widgets.url_input import UrlInputWidget

logger = get_logger("section_download_page")


class SectionDownloadPage(QWidget):
    """
    Unified, configurable download and clipping page.

    Signals:
        switch_section_requested: (target_section_id, url, auto_fetch)
        status_message: (str)
    """

    switch_section_requested = Signal(str, str, bool)
    status_message = Signal(str)

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._current_config: SectionConfig = SECTION_YOUTUBE_VIDEO
        self._current_video_info: Optional[VideoInfo] = None

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._container = QWidget()
        self._content_layout = QVBoxLayout(self._container)
        self._content_layout.setContentsMargins(18, 12, 18, 16)
        self._content_layout.setSpacing(10)

        # ─── 1. Section Header & Empty State Hint ─────────────────────────────
        self._header_card = QFrame()
        self._header_card.setStyleSheet(
            "QFrame {"
            "  background: rgba(28, 28, 36, 0.7);"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 10px;"
            "}"
        )
        h_layout = QVBoxLayout(self._header_card)
        h_layout.setContentsMargins(14, 10, 14, 10)
        h_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._title_icon_lbl = QLabel("🎬")
        self._title_icon_lbl.setFont(QFont("Segoe UI", 14))
        top_row.addWidget(self._title_icon_lbl)

        self._title_lbl = QLabel("YouTube — Video Download")
        self._title_lbl.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet("color: #ffffff;")
        top_row.addWidget(self._title_lbl)

        self._experimental_badge = QLabel("Experimental")
        self._experimental_badge.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        self._experimental_badge.setStyleSheet(
            "background: rgba(247, 127, 0, 0.2); color: #F77F00; "
            "border: 1px solid rgba(247, 127, 0, 0.4); border-radius: 4px; padding: 2px 6px;"
        )
        self._experimental_badge.hide()
        top_row.addWidget(self._experimental_badge)

        top_row.addStretch()
        h_layout.addLayout(top_row)

        self._hint_lbl = QLabel("Paste any YouTube video or Shorts link")
        self._hint_lbl.setFont(QFont("Segoe UI", 9))
        self._hint_lbl.setStyleSheet("color: rgba(235, 235, 245, 0.65);")
        h_layout.addWidget(self._hint_lbl)

        self._content_layout.addWidget(self._header_card)

        # ─── 2. Wrong-Section Banner ──────────────────────────────────────────
        self._wrong_section_banner = QFrame()
        self._wrong_section_banner.setStyleSheet(
            "QFrame {"
            "  background: rgba(247, 127, 0, 0.12);"
            "  border: 1px solid rgba(247, 127, 0, 0.4);"
            "  border-radius: 8px;"
            "}"
        )
        ws_layout = QHBoxLayout(self._wrong_section_banner)
        ws_layout.setContentsMargins(12, 8, 12, 8)
        ws_layout.setSpacing(10)

        self._ws_text_lbl = QLabel("ℹ️ This looks like an Instagram Reel.")
        self._ws_text_lbl.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self._ws_text_lbl.setStyleSheet("color: #ffffff;")
        ws_layout.addWidget(self._ws_text_lbl, stretch=1)

        self._ws_switch_btn = QPushButton("Switch Section ➜")
        self._ws_switch_btn.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._ws_switch_btn.setMinimumHeight(28)
        self._ws_switch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ws_switch_btn.setStyleSheet(
            "QPushButton {"
            "  background: #F77F00; color: white; border: none; border-radius: 5px; padding: 2px 12px;"
            "}"
            "QPushButton:hover { background: #E06F00; }"
        )
        self._ws_switch_btn.clicked.connect(self._on_switch_banner_clicked)
        ws_layout.addWidget(self._ws_switch_btn)

        self._wrong_section_banner.hide()
        self._content_layout.addWidget(self._wrong_section_banner)

        # ─── 3. Existing URL Input Widget ─────────────────────────────────────
        self._url_input = UrlInputWidget()
        self._content_layout.addWidget(self._url_input)

        # ─── 4. Image Preview Card (for Pinterest / Photo posts) ──────────────
        self._preview_card = QFrame()
        self._preview_card.setStyleSheet(
            "QFrame {"
            "  background: rgba(28, 28, 36, 0.85);"
            "  border: 1px solid rgba(255, 255, 255, 0.08);"
            "  border-radius: 10px;"
            "}"
        )
        prev_layout = QVBoxLayout(self._preview_card)
        prev_layout.setContentsMargins(12, 10, 12, 10)
        prev_layout.setSpacing(6)

        prev_title = QLabel("📌 Pin / Image Preview")
        prev_title.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        prev_title.setStyleSheet("color: #ffffff;")
        prev_layout.addWidget(prev_title)

        self._preview_img_lbl = QLabel("Preview will appear after fetching")
        self._preview_img_lbl.setFont(QFont("Segoe UI", 9))
        self._preview_img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_img_lbl.setStyleSheet(
            "color: rgba(235, 235, 245, 0.5); background: rgba(12, 12, 18, 0.6);"
            "border: 1px dashed rgba(255, 255, 255, 0.1); border-radius: 6px; min-height: 120px;"
        )
        prev_layout.addWidget(self._preview_img_lbl)

        self._preview_card.hide()
        self._content_layout.addWidget(self._preview_card)

        # ─── 5. Existing Format Selector Widget ───────────────────────────────
        self._format_selector = FormatSelectorWidget()
        self._content_layout.addWidget(self._format_selector)

        # ─── 6. Existing Time Range / Clipping Widget (UNCHANGED) ─────────────
        self._time_range = TimeRangeWidget()
        self._content_layout.addWidget(self._time_range)

        # ─── 7. Existing Download Controls Widget ─────────────────────────────
        self._download_controls = DownloadControlsWidget(
            default_output_folder=self._settings.last_output_folder
        )
        self._content_layout.addWidget(self._download_controls)

        self._content_layout.addStretch()
        scroll.setWidget(self._container)
        outer_layout.addWidget(scroll)

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        self._url_input._url_input.textChanged.connect(self._check_wrong_section)
        self._url_input.reroute_requested.connect(self._on_reroute_requested)

    # ─── Section Configuration Engine ─────────────────────────────────────────

    def configure_section(
        self,
        config: SectionConfig,
        carry_url: Optional[str] = None,
        auto_fetch: bool = False,
    ) -> None:
        """
        Dynamically configures this shared page for a specific SectionConfig.
        Panels that don't apply are hidden. Form is reset if switching to an incompatible platform.
        """
        prev_config = self._current_config
        self._current_config = config

        # 1. Update Header Card
        self._title_icon_lbl.setText(config.icon)
        from clipgrab.gui.sections import PLATFORM_GROUPS
        group_name = next(
            (g.name for g in PLATFORM_GROUPS if g.id == config.platform_id),
            config.platform_id.capitalize(),
        )
        self._title_lbl.setText(f"{group_name} — {config.title}")
        self._hint_lbl.setText(config.example_hint)
        self._experimental_badge.setVisible(config.is_experimental)

        # 2. Update URL Input
        self._url_input.set_label_text(config.url_label)
        self._url_input.set_placeholder_text(config.placeholder)

        # 3. Quality / Format Panel
        self._format_selector.setVisible(config.show_quality)
        self._format_selector.set_audio_only_locked(config.audio_only)
        self._format_selector.set_gif_mode(config.id == "x_gif")

        # 4. Preview Panel (for Pinterest images)
        self._preview_card.setVisible(config.show_preview and not config.show_quality)

        # 5. Time Range Clipping Panel
        self._time_range.setVisible(config.show_clip)
        if config.clip_focus:
            # Emphasise clipping with highlighted card styling
            self._time_range.setStyleSheet(
                "QGroupBox {"
                "  border: 1px solid rgba(230, 57, 70, 0.45);"
                "  background-color: rgba(36, 24, 28, 0.85);"
                "}"
            )
        else:
            self._time_range.setStyleSheet("")

        # 6. Check URL compatibility & carry-over
        target_url = carry_url if carry_url is not None else self._url_input.get_url()

        if target_url:
            platform = detect_platform(target_url)
            # If the URL is incompatible with the new section's platform, clear form completely
            if platform.id != "unsupported" and platform.id != config.platform_id:
                self.clear_form()
                target_url = ""

        if target_url:
            self._url_input.set_url(target_url)
            if auto_fetch:
                self._url_input.trigger_fetch()
        elif prev_config.platform_id != config.platform_id:
            # Different platform with no carried URL: clear form completely
            self.clear_form()

        # 7. Restore section-specific preferences (e.g. save folder)
        sec_prefs = self._settings.section_preferences.get(config.id, {})
        saved_folder = sec_prefs.get("output_folder")
        if saved_folder and os.path.isdir(saved_folder):
            self._download_controls.set_output_folder(saved_folder)
        else:
            self._download_controls.set_output_folder(self._settings.last_output_folder)

        self._check_wrong_section(self._url_input.get_url())

    def clear_form(self) -> None:
        """Clear the form completely: no stale filename, thumbnail, qualities, or clip."""
        self._current_video_info = None
        self._url_input.set_url("")
        self._format_selector.clear_formats()
        self._time_range.reset()
        self._download_controls.set_actions_enabled(False)
        self._download_controls.set_filename("")
        self._preview_img_lbl.setText("Preview will appear after fetching")
        self._wrong_section_banner.hide()

    def set_thumbnail_preview(self, pixmap: QPixmap) -> None:
        """Display image preview in the preview card if active."""
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                320, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self._preview_img_lbl.setPixmap(scaled)

    # ─── Wrong-Section Banner Detection ───────────────────────────────────────

    def _check_wrong_section(self, text: str) -> None:
        """Check if entered URL belongs to a different platform/section."""
        text = text.strip()
        if not text:
            self._wrong_section_banner.hide()
            return

        platform = detect_platform(text)
        if platform.engine == DownloadEngine.UNSUPPORTED:
            self._wrong_section_banner.hide()
            return

        # Route URL to ideal section
        target_sec, desc = route_url_to_section(text)

        # If it doesn't match the current section, show banner
        if target_sec.id != self._current_config.id:
            self._pending_switch_section_id = target_sec.id
            self._ws_text_lbl.setText(
                f"ℹ️ This looks like a {desc}. Switch to {target_sec.title}?"
            )
            self._wrong_section_banner.show()
        else:
            self._wrong_section_banner.hide()

    def _on_switch_banner_clicked(self) -> None:
        """User clicked switch section button on the wrong-section banner."""
        target_id = getattr(self, "_pending_switch_section_id", None)
        if target_id:
            url = self._url_input.get_url()
            self._wrong_section_banner.hide()
            self.switch_section_requested.emit(target_id, url, True)

    def _on_reroute_requested(self, target_section_id: str, url: str) -> None:
        """Reroute requested from error dropdown."""
        self.switch_section_requested.emit(target_section_id, url, True)

    # ─── Section Preferences Persistence ──────────────────────────────────────

    def save_section_preference(self, key: str, value: Any) -> None:
        """Save a preference for the currently active section."""
        sec_id = self._current_config.id
        if sec_id not in self._settings.section_preferences:
            self._settings.section_preferences[sec_id] = {}
        self._settings.section_preferences[sec_id][key] = value
        self._settings.save()

    def get_current_config(self) -> SectionConfig:
        return self._current_config

    # Expose inner widgets for MainWindow signal connections
    @property
    def url_input(self) -> UrlInputWidget:
        return self._url_input

    @property
    def format_selector(self) -> FormatSelectorWidget:
        return self._format_selector

    @property
    def time_range(self) -> TimeRangeWidget:
        return self._time_range

    @property
    def download_controls(self) -> DownloadControlsWidget:
        return self._download_controls

    def update_theme(self, theme_name: str) -> None:
        """Update Section page elements for dark/light theme."""
        is_dark = (theme_name == "dark")
        card_bg = "rgba(28, 28, 36, 0.7)" if is_dark else "rgba(255, 255, 255, 0.95)"
        border_col = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.08)"
        text_col = "#ffffff" if is_dark else "#181820"
        hint_col = "rgba(235, 235, 245, 0.65)" if is_dark else "rgba(24, 24, 32, 0.65)"

        self._header_card.setStyleSheet(
            f"QFrame {{ background: {card_bg}; border: 1px solid {border_col}; border-radius: 10px; }}"
        )
        self._title_lbl.setStyleSheet(f"color: {text_col};")
        self._hint_lbl.setStyleSheet(f"color: {hint_col};")
