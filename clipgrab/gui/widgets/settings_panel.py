"""
YouCut — Settings panel widget.

Provides controls for theme toggle, yt-dlp auto-update preferences,
default output folder, version info display, and manual update check.
Includes a prominent app-update banner when a new YouCut version is found.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from clipgrab.config.settings import AppSettings
from clipgrab.core.ffmpeg_utils import verify_ffmpeg, verify_ytdlp
from clipgrab.core.updater import UpdateManager, get_installed_ytdlp_version
from clipgrab.core.logger import get_logger
from clipgrab.version import APP_VERSION

logger = get_logger("settings_panel")


class SettingsPanel(QWidget):
    """
    Settings panel for application preferences.

    Signals:
        theme_toggle_requested: () — user wants to toggle the theme
        settings_changed: () — a setting was modified
    """

    theme_toggle_requested = Signal()
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
        self._app_download_url: str = ""
        self._setup_ui()
        self._load_from_settings()

    def _setup_ui(self) -> None:
        """Set up the settings panel UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(16)

        title = QLabel("⚙️ Settings")
        title.setFont(QFont("Segoe UI", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #E63946; margin-bottom: 4px;")
        layout.addWidget(title)

        # ── App Update Banner (hidden until update is found) ──────────────
        self._update_banner = QFrame()
        self._update_banner.setFrameStyle(QFrame.Shape.StyledPanel)
        self._update_banner.setStyleSheet(
            "QFrame { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 rgba(230, 57, 70, 0.15), stop:1 rgba(247, 127, 0, 0.15));"
            "  border: 1px solid rgba(230, 57, 70, 0.4);"
            "  border-radius: 10px; padding: 4px; }"
        )
        banner_layout = QVBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(14, 10, 14, 10)
        banner_layout.setSpacing(6)

        banner_title_row = QHBoxLayout()
        self._banner_title = QLabel("🚀  YouCut Update Available!")
        self._banner_title.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self._banner_title.setStyleSheet("color: #E63946;")
        banner_title_row.addWidget(self._banner_title)
        banner_title_row.addStretch()

        self._banner_version_label = QLabel("")
        self._banner_version_label.setFont(QFont("Segoe UI", 9))
        self._banner_version_label.setStyleSheet("color: rgba(235, 235, 245, 0.6);")
        banner_title_row.addWidget(self._banner_version_label)
        banner_layout.addLayout(banner_title_row)

        self._banner_notes = QLabel("")
        self._banner_notes.setFont(QFont("Segoe UI", 9))
        self._banner_notes.setStyleSheet("color: rgba(235, 235, 245, 0.7);")
        self._banner_notes.setWordWrap(True)
        banner_layout.addWidget(self._banner_notes)

        self._banner_download_btn = QPushButton("⬇️  Download YouCut Update")
        self._banner_download_btn.setMinimumHeight(34)
        self._banner_download_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._banner_download_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #E63946, stop:1 #F77F00); color: white;"
            "  border: none; border-radius: 8px; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #D12D3A, stop:1 #E06F00); }"
        )
        self._banner_download_btn.clicked.connect(self._on_banner_download)
        banner_layout.addWidget(self._banner_download_btn)

        self._update_banner.hide()
        layout.addWidget(self._update_banner)

        # ── Appearance ────────────────────────────────────────────────────
        appearance_group = QGroupBox("Appearance")
        appearance_layout = QVBoxLayout(appearance_group)

        theme_row = QHBoxLayout()
        theme_label = QLabel("Theme:")
        theme_label.setFont(QFont("Segoe UI", 10))
        theme_row.addWidget(theme_label)

        self._theme_btn = QPushButton("🌙 Switch to Light Theme")
        self._theme_btn.setMinimumHeight(34)
        self._theme_btn.setFont(QFont("Segoe UI", 10))
        self._theme_btn.clicked.connect(self._on_theme_toggle)
        theme_row.addWidget(self._theme_btn)
        theme_row.addStretch()

        appearance_layout.addLayout(theme_row)
        layout.addWidget(appearance_group)

        # ── Downloads ─────────────────────────────────────────────────────
        downloads_group = QGroupBox("Downloads")
        downloads_layout = QVBoxLayout(downloads_group)

        folder_row = QHBoxLayout()
        folder_label = QLabel("Default output folder:")
        folder_label.setFont(QFont("Segoe UI", 10))
        folder_row.addWidget(folder_label)

        self._folder_input = QLineEdit()
        self._folder_input.setReadOnly(True)
        self._folder_input.setMinimumHeight(32)
        self._folder_input.setFont(QFont("Segoe UI", 9))
        folder_row.addWidget(self._folder_input, stretch=1)

        browse_btn = QPushButton("📁 Browse")
        browse_btn.setMinimumHeight(32)
        browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(browse_btn)

        downloads_layout.addLayout(folder_row)

        # Concurrent fragments setting
        frag_row = QHBoxLayout()
        frag_label = QLabel("Concurrent download fragments:")
        frag_label.setFont(QFont("Segoe UI", 10))
        frag_label.setToolTip("Number of simultaneous connections per download (1–32). Default: 16.")
        frag_row.addWidget(frag_label)

        self._frag_spin = QSpinBox()
        self._frag_spin.setRange(1, 32)
        self._frag_spin.setValue(getattr(self._settings, "concurrent_fragments", 16))
        self._frag_spin.setMinimumHeight(32)
        self._frag_spin.setFont(QFont("Segoe UI", 9))
        self._frag_spin.setToolTip("Higher values increase download speeds on fast connections (yt-dlp --concurrent-fragments).")
        self._frag_spin.valueChanged.connect(self._on_concurrent_fragments_changed)
        frag_row.addWidget(self._frag_spin)
        downloads_layout.addLayout(frag_row)

        # Default video container format
        format_row = QHBoxLayout()
        format_label = QLabel("Default video format:")
        format_label.setFont(QFont("Segoe UI", 10))
        format_label.setToolTip("Container format for merged video downloads (default: MP4).")
        format_row.addWidget(format_label)

        self._format_combo = QComboBox()
        self._format_combo.addItems(["MP4", "MKV", "WEBM"])
        self._format_combo.setMinimumHeight(32)
        self._format_combo.setFont(QFont("Segoe UI", 9))
        self._format_combo.currentTextChanged.connect(self._on_default_format_changed)
        format_row.addWidget(self._format_combo)
        format_row.addStretch()

        downloads_layout.addLayout(format_row)
        layout.addWidget(downloads_group)

        # ── Updates ───────────────────────────────────────────────────────
        updates_group = QGroupBox("Software & Component Updates")
        updates_layout = QVBoxLayout(updates_group)

        self._auto_update_check = QCheckBox("Auto-update yt-dlp when a new version is available")
        self._auto_update_check.setFont(QFont("Segoe UI", 10))
        self._auto_update_check.toggled.connect(self._on_auto_update_toggled)
        updates_layout.addWidget(self._auto_update_check)

        self._notify_label = QLabel(
            "When disabled, you'll receive a notification but the update won't be installed automatically."
        )
        self._notify_label.setFont(QFont("Segoe UI", 9))
        self._notify_label.setStyleSheet("color: rgba(235, 235, 245, 0.4);")
        self._notify_label.setWordWrap(True)
        updates_layout.addWidget(self._notify_label)

        check_row = QHBoxLayout()
        self._check_update_btn = QPushButton("🔍 Check for Updates Now")
        self._check_update_btn.setMinimumHeight(34)
        self._check_update_btn.setFont(QFont("Segoe UI", 10))
        self._check_update_btn.clicked.connect(self._on_check_update)
        check_row.addWidget(self._check_update_btn)
        check_row.addStretch()
        updates_layout.addLayout(check_row)

        layout.addWidget(updates_group)

        # ── Version Info ──────────────────────────────────────────────────
        versions_group = QGroupBox("Version Information")
        versions_layout = QVBoxLayout(versions_group)

        self._app_version_label = QLabel(f"YouCut v{APP_VERSION}")
        self._app_version_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        versions_layout.addWidget(self._app_version_label)

        self._ytdlp_version_label = QLabel("yt-dlp: checking...")
        self._ytdlp_version_label.setFont(QFont("Segoe UI", 10))
        versions_layout.addWidget(self._ytdlp_version_label)

        self._ffmpeg_version_label = QLabel("FFmpeg: checking...")
        self._ffmpeg_version_label.setFont(QFont("Segoe UI", 10))
        versions_layout.addWidget(self._ffmpeg_version_label)

        layout.addWidget(versions_group)
        layout.addStretch()

        # ── Credits ───────────────────────────────────────────────────────
        credit_label = QLabel("Made with ❤️ by sHUBH")
        credit_label.setFont(QFont("Segoe UI", 9, QFont.Weight.Medium))
        credit_label.setStyleSheet("color: rgba(235, 235, 245, 0.35);")
        credit_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(credit_label)

    def _load_from_settings(self) -> None:
        """Load current values from AppSettings."""
        self._folder_input.setText(self._settings.last_output_folder)
        self._auto_update_check.setChecked(self._settings.auto_update_ytdlp)

        if self._settings.theme == "dark":
            self._theme_btn.setText("☀️ Switch to Light Theme")
        else:
            self._theme_btn.setText("🌙 Switch to Dark Theme")

        # Video container format
        fmt = getattr(self._settings, "default_merge_format", "mp4").upper()
        idx = self._format_combo.findText(fmt)
        if idx >= 0:
            self._format_combo.setCurrentIndex(idx)
        else:
            self._format_combo.setCurrentText("MP4")

    def _on_default_format_changed(self, text: str) -> None:
        """Handle default video container format change."""
        new_fmt = text.lower()
        self._settings.update(default_merge_format=new_fmt)
        self.settings_changed.emit()

    def show_app_update_banner(self, version: str, notes: str, url: str) -> None:
        """
        Display the app-update banner with version info and a download button.

        Called by MainWindow when UpdateManager emits app_update_available.

        Args:
            version: New version string e.g. '1.1.0'
            notes: Short release notes text.
            url: Direct download URL for the new YouCut.exe.
        """
        self._app_download_url = url
        self._banner_version_label.setText(f"v{APP_VERSION} → v{version}")
        self._banner_notes.setText(notes or "A new version of YouCut is available.")
        self._update_banner.show()
        logger.info("App update banner shown for v%s", version)

    def refresh_versions(self) -> None:
        """Refresh version information labels."""
        # App version
        self._app_version_label.setText(f"YouCut v{APP_VERSION}")

        # yt-dlp version
        ytdlp_version = get_installed_ytdlp_version()
        self._ytdlp_version_label.setText(f"yt-dlp: v{ytdlp_version}")

        # FFmpeg version
        ffmpeg_ok, ffmpeg_info = verify_ffmpeg()
        if ffmpeg_ok:
            version_str = ffmpeg_info.split(" ")[2] if len(ffmpeg_info.split(" ")) > 2 else ffmpeg_info
            self._ffmpeg_version_label.setText(f"FFmpeg: {version_str}")
        else:
            self._ffmpeg_version_label.setText("FFmpeg: ⚠️ Not found")
            self._ffmpeg_version_label.setStyleSheet("color: #e74c3c;")

    def update_theme_button(self, current_theme: str) -> None:
        """Update the theme toggle button text after a theme change."""
        if current_theme == "dark":
            self._theme_btn.setText("☀️ Switch to Light Theme")
        else:
            self._theme_btn.setText("🌙 Switch to Dark Theme")

    def _on_banner_download(self) -> None:
        """Open the download URL in the system browser."""
        if self._app_download_url:
            QDesktopServices.openUrl(QUrl(self._app_download_url))
            logger.info("Opened download URL: %s", self._app_download_url)

    def _on_theme_toggle(self) -> None:
        self.theme_toggle_requested.emit()

    def _on_browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Default Output Folder",
            self._settings.last_output_folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self._folder_input.setText(folder)
            self._settings.update(last_output_folder=folder)
            self.settings_changed.emit()

    def _on_concurrent_fragments_changed(self, value: int) -> None:
        self._settings.update(concurrent_fragments=value)
        self.settings_changed.emit()

    def _on_auto_update_toggled(self, checked: bool) -> None:
        self._settings.update(auto_update_ytdlp=checked)
        self._update_manager.auto_update = checked
        self.settings_changed.emit()

    def _on_check_update(self) -> None:
        """Trigger a manual update check (both app and yt-dlp)."""
        self._check_update_btn.setText("⏳ Checking...")
        self._check_update_btn.setEnabled(False)
        self._update_manager.manual_check()

        if not hasattr(self, "_btn_reset_timer") or self._btn_reset_timer is None:
            from PySide6.QtCore import QTimer
            self._btn_reset_timer = QTimer(self)
            self._btn_reset_timer.setSingleShot(True)
            self._btn_reset_timer.setInterval(5000)
            self._btn_reset_timer.timeout.connect(self._reset_check_update_btn)
        self._btn_reset_timer.start()

    def _reset_check_update_btn(self) -> None:
        self._check_update_btn.setText("🔍 Check for Updates Now")
        self._check_update_btn.setEnabled(True)

    def shutdown(self) -> None:
        """Stop any active UI reset timers on close."""
        if hasattr(self, "_btn_reset_timer") and self._btn_reset_timer and self._btn_reset_timer.isActive():
            self._btn_reset_timer.stop()
