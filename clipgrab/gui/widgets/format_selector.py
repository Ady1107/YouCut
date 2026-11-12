"""
YouCut — Format selector widget.

Provides video quality and audio quality dropdowns populated with real format
data from yt-dlp, plus an audio-only mode toggle with output format selection.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.formats import AudioFormat, AudioOutputFormat, VideoFormat, VideoInfo
from clipgrab.core.logger import get_logger

logger = get_logger("format_selector")


class FormatSelectorWidget(QWidget):
    """
    Widget for selecting video and audio quality from available formats.

    Signals:
        selection_changed: () — emitted when format selection changes
    """

    selection_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._video_formats: list[VideoFormat] = []
        self._audio_formats: list[AudioFormat] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the format selector UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Group box
        group = QGroupBox("Quality && Format")
        group_layout = QVBoxLayout(group)

        # Audio only toggle
        self._audio_only_check = QCheckBox("Audio only mode")
        self._audio_only_check.setFont(QFont("Segoe UI", 10))
        self._audio_only_check.toggled.connect(self._on_audio_only_toggled)
        group_layout.addWidget(self._audio_only_check)

        # Form layout for dropdowns
        form = QFormLayout()
        form.setSpacing(8)

        # Media output choice (e.g. for GIFs / silent clips: MP4 or GIF)
        self._media_type_label = QLabel("Output choice:")
        self._media_type_label.setFont(QFont("Segoe UI", 10))
        self._media_type_combo = QComboBox()
        self._media_type_combo.setMinimumHeight(32)
        self._media_type_combo.setFont(QFont("Segoe UI", 9))
        self._media_type_combo.addItem("MP4 (original)", "mp4")
        self._media_type_combo.addItem("GIF", "gif")
        self._media_type_combo.currentIndexChanged.connect(
            lambda: self.selection_changed.emit()
        )
        form.addRow(self._media_type_label, self._media_type_combo)
        self._media_type_label.hide()
        self._media_type_combo.hide()

        # Video quality
        self._video_label = QLabel("Video quality:")
        self._video_label.setFont(QFont("Segoe UI", 10))
        self._video_combo = QComboBox()
        self._video_combo.setMinimumHeight(32)
        self._video_combo.setFont(QFont("Segoe UI", 9))
        self._video_combo.currentIndexChanged.connect(
            lambda: self.selection_changed.emit()
        )
        form.addRow(self._video_label, self._video_combo)

        # Audio quality
        self._audio_label = QLabel("Audio quality:")
        self._audio_label.setFont(QFont("Segoe UI", 10))
        self._audio_combo = QComboBox()
        self._audio_combo.setMinimumHeight(32)
        self._audio_combo.setFont(QFont("Segoe UI", 9))
        self._audio_combo.currentIndexChanged.connect(
            lambda: self.selection_changed.emit()
        )
        form.addRow(self._audio_label, self._audio_combo)

        # Audio output format (only visible in audio-only mode)
        self._output_format_label = QLabel("Output format:")
        self._output_format_label.setFont(QFont("Segoe UI", 10))
        self._output_format_combo = QComboBox()
        self._output_format_combo.setMinimumHeight(32)
        self._output_format_combo.setFont(QFont("Segoe UI", 9))
        self._output_format_combo.addItems(["MP3", "M4A", "WAV"])
        self._output_format_combo.setCurrentIndex(0)  # Default to MP3
        form.addRow(self._output_format_label, self._output_format_combo)

        # Initially hide audio output format
        self._output_format_label.hide()
        self._output_format_combo.hide()

        group_layout.addLayout(form)
        layout.addWidget(group)

        # Initially populate with placeholder and disable until formats are loaded
        self._video_combo.addItem("Fetch a link first")
        self._audio_combo.addItem("Fetch a link first")
        self.setEnabled(False)

    def populate_formats(self, video_info: VideoInfo) -> None:
        """
        Populate dropdowns with format data from a fetched VideoInfo.

        Args:
            video_info: The parsed VideoInfo object.
        """
        self._video_formats = video_info.video_formats
        self._audio_formats = video_info.audio_formats
        is_silent = len(self._audio_formats) == 0

        # Video dropdown
        self._video_combo.clear()
        self._video_combo.addItem("⭐ Best available", None)
        for vf in self._video_formats:
            self._video_combo.addItem(vf.display_label(), vf)

        # Audio dropdown
        self._audio_combo.clear()
        if is_silent:
            # Hide audio dropdown for silent media (e.g. X GIFs)
            self._audio_label.hide()
            self._audio_combo.hide()
            self._audio_only_check.hide()
            # Surface MP4 (original) / GIF output choice
            self._media_type_label.show()
            self._media_type_combo.show()
        else:
            self._audio_label.show()
            self._audio_combo.show()
            self._audio_only_check.show()
            self._audio_combo.addItem("⭐ Best available", None)
            for af in self._audio_formats:
                self._audio_combo.addItem(af.display_label(), af)

        self.setEnabled(True)
        logger.debug(
            "Formats populated: %d video, %d audio (silent=%s)",
            len(self._video_formats),
            len(self._audio_formats),
            is_silent,
        )

    def clear_formats(self) -> None:
        """Clear all format data and reset the widget."""
        self._video_formats = []
        self._audio_formats = []
        self._video_combo.clear()
        self._video_combo.addItem("Fetch a link first")
        self._audio_combo.clear()
        self._audio_combo.addItem("Fetch a link first")
        self._audio_label.show()
        self._audio_combo.show()
        self._audio_only_check.show()
        self._media_type_label.hide()
        self._media_type_combo.hide()
        self.setEnabled(False)

    def is_gif_selected(self) -> bool:
        """Whether GIF output format is chosen."""
        return (
            self._media_type_combo.isVisible()
            and self._media_type_combo.currentData() == "gif"
        )

    def set_gif_mode(self, enabled: bool) -> None:
        """Force or unforce GIF mode (used by GIF sections)."""
        if enabled:
            self._media_type_label.show()
            self._media_type_combo.show()
            idx = self._media_type_combo.findData("gif")
            if idx >= 0:
                self._media_type_combo.setCurrentIndex(idx)
        else:
            self._media_type_label.hide()
            self._media_type_combo.hide()

    @property
    def is_audio_only(self) -> bool:
        """Whether audio-only mode is selected."""
        return self._audio_only_check.isChecked()

    def get_selected_video_format(self) -> Optional[VideoFormat]:
        """Get the currently selected video format, or None for 'Best available'."""
        if self._audio_only_check.isChecked():
            return None
        return self._video_combo.currentData()

    def get_selected_audio_format(self) -> Optional[AudioFormat]:
        """Get the currently selected audio format, or None for 'Best available'."""
        return self._audio_combo.currentData()

    def get_audio_output_format(self) -> AudioOutputFormat:
        """Get the selected audio output format (for audio-only mode)."""
        text = self._output_format_combo.currentText().lower()
        try:
            return AudioOutputFormat(text)
        except ValueError:
            return AudioOutputFormat.MP3

    def get_selected_video_label(self) -> str:
        """Get the display text of the selected video format."""
        return self._video_combo.currentText() or "Best available"

    def get_selected_audio_label(self) -> str:
        """Get the display text of the selected audio format."""
        return self._audio_combo.currentText() or "Best available"

    def _on_audio_only_toggled(self, checked: bool) -> None:
        """Toggle visibility of video dropdown vs audio output format."""
        self._video_label.setVisible(not checked)
        self._video_combo.setVisible(not checked)
        self._output_format_label.setVisible(checked)
        self._output_format_combo.setVisible(checked)
        self.selection_changed.emit()

    def set_audio_only_locked(self, locked: bool) -> None:
        """Lock or unlock audio-only mode (used by Audio Download sections)."""
        self._audio_only_check.blockSignals(True)
        if locked:
            self._audio_only_check.setChecked(True)
            self._audio_only_check.setEnabled(False)
            self._video_label.setVisible(False)
            self._video_combo.setVisible(False)
            self._output_format_label.setVisible(True)
            self._output_format_combo.setVisible(True)
        else:
            self._audio_only_check.setEnabled(True)
            self._audio_only_check.setChecked(False)
            self._video_label.setVisible(True)
            self._video_combo.setVisible(True)
            self._output_format_label.setVisible(False)
            self._output_format_combo.setVisible(False)
        self._audio_only_check.blockSignals(False)
