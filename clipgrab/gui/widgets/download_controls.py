"""
YouCut — Download controls widget.

Provides filename input, output folder selection, progress bar with speed/ETA,
and Add to Queue / Download Now buttons.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.logger import get_logger

logger = get_logger("download_controls")


class DownloadControlsWidget(QWidget):
    """
    Widget for download configuration and execution controls.

    Signals:
        add_to_queue_clicked: () — user wants to add current settings to queue
        download_now_clicked: () — user wants immediate download
        cancel_clicked: () — user wants to cancel current download
        output_folder_changed: (path,) — output folder was changed
    """

    add_to_queue_clicked = Signal()
    download_now_clicked = Signal()
    cancel_clicked = Signal()
    output_folder_changed = Signal(str)

    def __init__(
        self,
        default_output_folder: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._output_folder = default_output_folder or str(
            os.path.join(os.path.expanduser("~"), "Downloads")
        )
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the download controls UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Output")
        group_layout = QVBoxLayout(group)

        # Filename
        name_row = QHBoxLayout()
        name_label = QLabel("Filename:")
        name_label.setFont(QFont("Segoe UI", 10))
        name_label.setFixedWidth(75)
        name_row.addWidget(name_label)

        self._filename_input = QLineEdit()
        self._filename_input.setPlaceholderText("Auto-generated from video title")
        self._filename_input.setMinimumHeight(32)
        self._filename_input.setFont(QFont("Segoe UI", 10))
        name_row.addWidget(self._filename_input)
        group_layout.addLayout(name_row)

        # Output folder
        folder_row = QHBoxLayout()
        folder_label = QLabel("Save to:")
        folder_label.setFont(QFont("Segoe UI", 10))
        folder_label.setFixedWidth(75)
        folder_row.addWidget(folder_label)

        self._folder_input = QLineEdit(self._output_folder)
        self._folder_input.setMinimumHeight(32)
        self._folder_input.setFont(QFont("Segoe UI", 9))
        self._folder_input.setReadOnly(True)
        folder_row.addWidget(self._folder_input)

        self._browse_btn = QPushButton("📁 Browse")
        self._browse_btn.setMinimumHeight(32)
        self._browse_btn.setFixedWidth(100)
        self._browse_btn.clicked.connect(self._on_browse_clicked)
        folder_row.addWidget(self._browse_btn)
        group_layout.addLayout(folder_row)

        layout.addWidget(group)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._queue_btn = QPushButton("➕ Add to Queue")
        self._queue_btn.setMinimumHeight(40)
        self._queue_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._queue_btn.setEnabled(False)
        self._queue_btn.clicked.connect(self.add_to_queue_clicked.emit)
        btn_row.addWidget(self._queue_btn)

        self._download_btn = QPushButton("⬇️ Download Now")
        self._download_btn.setMinimumHeight(40)
        self._download_btn.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self._download_btn.setEnabled(False)
        self._download_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #E63946, stop:1 #F77F00); color: white;"
            "  border: none; border-radius: 8px; }"
            "QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #D12D3A, stop:1 #E06F00); }"
            "QPushButton:pressed { background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "  stop:0 #B8232E, stop:1 #CC6200); }"
            "QPushButton:disabled { background: rgba(28, 28, 36, 0.5);"
            "  color: rgba(235, 235, 245, 0.2); border: 1px solid rgba(255, 255, 255, 0.03); }"
        )
        self._download_btn.clicked.connect(self.download_now_clicked.emit)
        btn_row.addWidget(self._download_btn)

        layout.addLayout(btn_row)

        # Progress section (hidden by default)
        self._progress_group = QGroupBox("Download Progress")
        progress_layout = QVBoxLayout(self._progress_group)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        self._progress_bar.setMinimumHeight(24)
        progress_layout.addWidget(self._progress_bar)

        # Speed / ETA row
        stats_row = QHBoxLayout()
        self._speed_label = QLabel("Speed: --")
        self._speed_label.setFont(QFont("Segoe UI", 9))
        stats_row.addWidget(self._speed_label)
        stats_row.addStretch()
        self._eta_label = QLabel("ETA: --")
        self._eta_label.setFont(QFont("Segoe UI", 9))
        stats_row.addWidget(self._eta_label)
        progress_layout.addLayout(stats_row)

        self._status_label = QLabel("")
        self._status_label.setFont(QFont("Segoe UI", 9))
        progress_layout.addWidget(self._status_label)

        self._cancel_btn = QPushButton("✗ Cancel")
        self._cancel_btn.setMinimumHeight(34)
        self._cancel_btn.setStyleSheet(
            "QPushButton { background: rgba(231, 76, 60, 0.85); color: white;"
            "  border: none; border-radius: 8px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(231, 76, 60, 1.0); }"
        )
        self._cancel_btn.clicked.connect(self.cancel_clicked.emit)
        progress_layout.addWidget(self._cancel_btn)

        self._progress_group.hide()
        layout.addWidget(self._progress_group)

    def get_filename(self) -> str:
        """Get the user-specified filename."""
        return self._filename_input.text().strip()

    def set_filename(self, filename: str) -> None:
        """Set the filename field."""
        self._filename_input.setText(filename)

    def get_output_folder(self) -> str:
        """Get the current output folder path."""
        return self._output_folder

    def set_output_folder(self, folder: str) -> None:
        """Set the output folder path."""
        self._output_folder = folder
        self._folder_input.setText(folder)

    def set_actions_enabled(self, enabled: bool) -> None:
        """Enable/disable the action buttons."""
        self._queue_btn.setEnabled(enabled)
        self._download_btn.setEnabled(enabled)

    def show_progress(self, show: bool = True) -> None:
        """Show or hide the progress section."""
        self._progress_group.setVisible(show)
        if show:
            self._progress_bar.setValue(0)
            self._speed_label.setText("Speed: --")
            self._eta_label.setText("ETA: --")
            self._status_label.setText("Starting...")

    def update_progress(
        self, percent: float, speed: str, eta: str
    ) -> None:
        """Update the progress bar and stats."""
        self._progress_bar.setValue(int(percent))
        self._speed_label.setText(f"Speed: {speed}" if speed else "Speed: --")
        self._eta_label.setText(f"ETA: {eta}" if eta else "ETA: --")

    def set_status(self, text: str) -> None:
        """Set the status label text."""
        self._status_label.setText(text)

    def _on_browse_clicked(self) -> None:
        """Open a folder picker dialog."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder",
            self._output_folder,
            QFileDialog.Option.ShowDirsOnly,
        )
        if folder:
            self._output_folder = folder
            self._folder_input.setText(folder)
            self.output_folder_changed.emit(folder)
            logger.info("Output folder changed to: %s", folder)
