"""
YouCut — Responsive & Adjustable Time Range Input Widget.

Provides:
- Interactive dual-thumb timeline scrub slider (RangeSlider)
- Precision Start & End time inputs with HH:MM:SS masking
- One-click step / nudge buttons (-5s, -1s, +1s, +5s)
- Quick clipping presets (First 30s, First 60s, Middle 50%, Last 30s, Reset)
- Full video mode toggle with responsive badges
- Frame-accurate trimming option with detailed tooltip
- Responsive card layout that adapts seamlessly to window resizing without clipping
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from clipgrab.core.logger import get_logger
from clipgrab.core.validator import parse_time_to_seconds, seconds_to_hms, validate_time_range

logger = get_logger("time_range")


class RangeSlider(QWidget):
    """
    Interactive dual-thumb range slider for precision video timeline clipping.

    Supports:
    - Dragging Start thumb (red) and End thumb (orange)
    - Clicking on track to jump nearest thumb
    - Illuminated gradient active span
    - Live tooltips with formatted timestamps
    - Dynamic light / dark theme adaptation
    """

    range_changed = Signal(float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._min_val: float = 0.0
        self._max_val: float = 100.0
        self._start_val: float = 0.0
        self._end_val: float = 100.0
        self._active_thumb: Optional[str] = None
        self._hover_thumb: Optional[str] = None
        self._thumb_radius: float = 7.5
        self._margin: float = 10.0

        self.setMouseTracking(True)
        self.setMinimumHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_duration(self, duration: float) -> None:
        """Set total duration and clamp values."""
        self._max_val = max(duration, 0.1)
        self._start_val = max(self._min_val, min(self._start_val, self._max_val))
        self._end_val = max(self._start_val, min(self._end_val, self._max_val))
        self.update()

    def set_range(self, start: float, end: float, block_signals: bool = False) -> None:
        """Set active start and end seconds."""
        start = max(self._min_val, min(start, self._max_val))
        end = max(start, min(end, self._max_val))
        if abs(self._start_val - start) > 0.01 or abs(self._end_val - end) > 0.01:
            self._start_val = start
            self._end_val = end
            self.update()
            if not block_signals:
                self.range_changed.emit(self._start_val, self._end_val)

    def get_range(self) -> tuple[float, float]:
        """Get (start_seconds, end_seconds)."""
        return self._start_val, self._end_val

    def _val_to_x(self, val: float) -> float:
        w = max(1.0, self.width() - 2 * self._margin)
        span = max(0.1, self._max_val - self._min_val)
        ratio = (val - self._min_val) / span
        return self._margin + ratio * w

    def _x_to_val(self, x: float) -> float:
        w = max(1.0, self.width() - 2 * self._margin)
        clamped_x = max(self._margin, min(x, self.width() - self._margin))
        ratio = (clamped_x - self._margin) / w
        return self._min_val + ratio * (self._max_val - self._min_val)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width() - 2 * self._margin
        cy = self.height() / 2.0
        track_h = 5.0

        is_dark = self.palette().color(QPalette.ColorRole.Window).value() < 128
        is_on = self.isEnabled()

        # Background track
        if is_dark:
            bg_color = QColor(255, 255, 255, 26) if is_on else QColor(255, 255, 255, 10)
        else:
            bg_color = QColor(0, 0, 0, 32) if is_on else QColor(0, 0, 0, 12)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(QRectF(self._margin, cy - track_h / 2, w, track_h), 2.5, 2.5)

        x1 = self._val_to_x(self._start_val)
        x2 = self._val_to_x(self._end_val)

        # Active range bar
        if is_on and x2 > x1:
            grad = QLinearGradient(x1, 0, x2, 0)
            grad.setColorAt(0, QColor("#E63946"))
            grad.setColorAt(1, QColor("#F77F00"))
            painter.setBrush(grad)
            painter.drawRoundedRect(QRectF(x1, cy - track_h / 2, x2 - x1, track_h), 2.5, 2.5)
        elif not is_on and x2 > x1:
            dim_color = QColor(255, 255, 255, 18) if is_dark else QColor(0, 0, 0, 18)
            painter.setBrush(dim_color)
            painter.drawRoundedRect(QRectF(x1, cy - track_h / 2, x2 - x1, track_h), 2.5, 2.5)

        # Start thumb (Red accent)
        start_color = QColor("#E63946") if is_on else QColor(90, 90, 100)
        start_border = QColor("#FFFFFF") if (is_on and self._hover_thumb == "start") else QColor(210, 210, 220)
        painter.setPen(QPen(start_border, 2))
        painter.setBrush(start_color)
        r_start = self._thumb_radius + (1.0 if (self._hover_thumb == "start" or self._active_thumb == "start") else 0.0)
        painter.drawEllipse(QPointF(x1, cy), r_start, r_start)

        # End thumb (Orange accent)
        end_color = QColor("#F77F00") if is_on else QColor(90, 90, 100)
        end_border = QColor("#FFFFFF") if (is_on and self._hover_thumb == "end") else QColor(210, 210, 220)
        painter.setPen(QPen(end_border, 2))
        painter.setBrush(end_color)
        r_end = self._thumb_radius + (1.0 if (self._hover_thumb == "end" or self._active_thumb == "end") else 0.0)
        painter.drawEllipse(QPointF(x2, cy), r_end, r_end)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if not self.isEnabled():
            return
        x = event.position().x()
        x1 = self._val_to_x(self._start_val)
        x2 = self._val_to_x(self._end_val)

        d1 = abs(x - x1)
        d2 = abs(x - x2)

        if d1 <= 15 and d1 <= d2:
            self._active_thumb = "start"
        elif d2 <= 15:
            self._active_thumb = "end"
        else:
            val = self._x_to_val(x)
            if d1 <= d2:
                self._active_thumb = "start"
                self.set_range(val, self._end_val)
            else:
                self._active_thumb = "end"
                self.set_range(self._start_val, val)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        if not self.isEnabled():
            return
        x = event.position().x()
        if self._active_thumb:
            val = self._x_to_val(x)
            if self._active_thumb == "start":
                self.set_range(min(val, self._end_val), self._end_val)
            elif self._active_thumb == "end":
                self.set_range(self._start_val, max(val, self._start_val))
            self.setToolTip(f"{seconds_to_hms(val)}")
        else:
            x1 = self._val_to_x(self._start_val)
            x2 = self._val_to_x(self._end_val)
            d1 = abs(x - x1)
            d2 = abs(x - x2)
            prev_hover = self._hover_thumb
            if d1 <= 13:
                self._hover_thumb = "start"
                self.setToolTip(f"Start: {seconds_to_hms(self._start_val)}")
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif d2 <= 13:
                self._hover_thumb = "end"
                self.setToolTip(f"End: {seconds_to_hms(self._end_val)}")
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self._hover_thumb = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
            if prev_hover != self._hover_thumb:
                self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._active_thumb = None
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover_thumb = None
        self.update()


class TimeRangeWidget(QWidget):
    """
    Modern, responsive widget for specifying clip start/end times with validation.

    Signals:
        range_changed: () — emitted when the time range changes
    """

    range_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._duration: float = 0.0
        self._syncing: bool = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Set up the time range input UI with anti-collapsing responsive layout."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("Clip Range")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)
        group_layout.setContentsMargins(12, 14, 12, 12)

        # ── 1. Top Bar: Full Video Checkbox + Duration / Mode Badges ───────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self._full_video_check = QCheckBox("Download full video (no clipping)")
        self._full_video_check.setChecked(True)
        self._full_video_check.setFont(QFont("Segoe UI", 10))
        self._full_video_check.toggled.connect(self._on_full_video_toggled)
        top_bar.addWidget(self._full_video_check)

        top_bar.addStretch()

        self._duration_label = QLabel("Video duration: --:--:--")
        self._duration_label.setFont(QFont("Segoe UI", 9))
        self._duration_label.setStyleSheet("color: rgba(235, 235, 245, 0.5);")
        top_bar.addWidget(self._duration_label)

        self._clip_badge = QLabel("Clip: Full Video")
        self._clip_badge.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        self._clip_badge.setStyleSheet(
            "QLabel { background: rgba(255, 255, 255, 0.07); color: rgba(235, 235, 245, 0.7);"
            "  border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 9px; padding: 2px 10px; }"
        )
        top_bar.addWidget(self._clip_badge)
        group_layout.addLayout(top_bar)

        # ── 2. Interactive Clipping Container ─────────────────────────────────
        self._clipping_container = QWidget()
        clip_layout = QVBoxLayout(self._clipping_container)
        clip_layout.setContentsMargins(0, 2, 0, 2)
        clip_layout.setSpacing(8)

        # Timeline Slider Row with boundary labels
        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)

        self._slider_start_label = QLabel("00:00:00")
        self._slider_start_label.setFont(QFont("Consolas", 9))
        self._slider_start_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        slider_row.addWidget(self._slider_start_label)

        self._range_slider = RangeSlider()
        self._range_slider.range_changed.connect(self._on_slider_range_changed)
        slider_row.addWidget(self._range_slider, stretch=1)

        self._slider_end_label = QLabel("00:00:00")
        self._slider_end_label.setFont(QFont("Consolas", 9))
        self._slider_end_label.setStyleSheet("color: rgba(235, 235, 245, 0.45);")
        slider_row.addWidget(self._slider_end_label)
        clip_layout.addLayout(slider_row)

        # Cards Row (Start & End Precision Trimming Cards)
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)

        # --- Start Time Card ---
        start_card = QFrame()
        start_card.setObjectName("timeRangeCard")
        sc_layout = QVBoxLayout(start_card)
        sc_layout.setContentsMargins(10, 6, 10, 6)
        sc_layout.setSpacing(5)

        s_top = QHBoxLayout()
        s_title = QLabel("START TIME")
        s_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        s_title.setStyleSheet("color: #E63946;")
        s_top.addWidget(s_title)
        s_top.addStretch()

        self._start_reset_btn = QPushButton("⏮ 0:00")
        self._start_reset_btn.setProperty("class", "timeNudgeBtn")
        self._start_reset_btn.setFont(QFont("Segoe UI", 8))
        self._start_reset_btn.setFixedHeight(20)
        self._start_reset_btn.setToolTip("Reset Start to 00:00:00")
        self._start_reset_btn.clicked.connect(lambda: self._set_start_seconds(0.0))
        s_top.addWidget(self._start_reset_btn)
        sc_layout.addLayout(s_top)

        s_mid = QHBoxLayout()
        s_mid.setSpacing(6)

        for d in [-5, -1]:
            b = QPushButton(f"{d}s")
            b.setProperty("class", "timeNudgeBtn")
            b.setFont(QFont("Segoe UI", 8))
            b.setFixedSize(30, 26)
            b.setToolTip(f"Nudge start {d} seconds")
            b.clicked.connect(lambda _, delta=d: self._nudge_start(delta))
            s_mid.addWidget(b)

        self._start_input = QLineEdit("00:00:00")
        self._start_input.setInputMask("99:99:99")
        self._start_input.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self._start_input.setMinimumHeight(28)
        self._start_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._start_input.textChanged.connect(self._on_time_changed)
        s_mid.addWidget(self._start_input, stretch=1)

        for d in [1, 5]:
            b = QPushButton(f"+{d}s")
            b.setProperty("class", "timeNudgeBtn")
            b.setFont(QFont("Segoe UI", 8))
            b.setFixedSize(30, 26)
            b.setToolTip(f"Nudge start +{d} seconds")
            b.clicked.connect(lambda _, delta=d: self._nudge_start(delta))
            s_mid.addWidget(b)

        sc_layout.addLayout(s_mid)
        cards_row.addWidget(start_card, stretch=1)

        # Center Arrow
        center_arrow = QLabel("➔")
        center_arrow.setFont(QFont("Segoe UI", 14))
        center_arrow.setStyleSheet("color: rgba(235, 235, 245, 0.35);")
        center_arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cards_row.addWidget(center_arrow)

        # --- End Time Card ---
        end_card = QFrame()
        end_card.setObjectName("timeRangeCard")
        ec_layout = QVBoxLayout(end_card)
        ec_layout.setContentsMargins(10, 6, 10, 6)
        ec_layout.setSpacing(5)

        e_top = QHBoxLayout()
        e_title = QLabel("END TIME")
        e_title.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        e_title.setStyleSheet("color: #F77F00;")
        e_top.addWidget(e_title)
        e_top.addStretch()

        self._end_max_btn = QPushButton("End ⏭")
        self._end_max_btn.setProperty("class", "timeNudgeBtn")
        self._end_max_btn.setFont(QFont("Segoe UI", 8))
        self._end_max_btn.setFixedHeight(20)
        self._end_max_btn.setToolTip("Set End to full video duration")
        self._end_max_btn.clicked.connect(lambda: self._set_end_seconds(self._duration))
        e_top.addWidget(self._end_max_btn)
        ec_layout.addLayout(e_top)

        e_mid = QHBoxLayout()
        e_mid.setSpacing(6)

        for d in [-5, -1]:
            b = QPushButton(f"{d}s")
            b.setProperty("class", "timeNudgeBtn")
            b.setFont(QFont("Segoe UI", 8))
            b.setFixedSize(30, 26)
            b.setToolTip(f"Nudge end {d} seconds")
            b.clicked.connect(lambda _, delta=d: self._nudge_end(delta))
            e_mid.addWidget(b)

        self._end_input = QLineEdit("00:00:00")
        self._end_input.setInputMask("99:99:99")
        self._end_input.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        self._end_input.setMinimumHeight(28)
        self._end_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._end_input.textChanged.connect(self._on_time_changed)
        e_mid.addWidget(self._end_input, stretch=1)

        for d in [1, 5]:
            b = QPushButton(f"+{d}s")
            b.setProperty("class", "timeNudgeBtn")
            b.setFont(QFont("Segoe UI", 8))
            b.setFixedSize(30, 26)
            b.setToolTip(f"Nudge end +{d} seconds")
            b.clicked.connect(lambda _, delta=d: self._nudge_end(delta))
            e_mid.addWidget(b)

        ec_layout.addLayout(e_mid)
        cards_row.addWidget(end_card, stretch=1)

        clip_layout.addLayout(cards_row)
        group_layout.addWidget(self._clipping_container)

        # ── 3. Bottom Row: Frame-Accurate Trimming + Presets Chips ────────────
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(10)

        self._frame_accurate_check = QCheckBox("Frame-accurate trimming (slower, but exact)")
        self._frame_accurate_check.setChecked(False)
        self._frame_accurate_check.setFont(QFont("Segoe UI", 9))
        self._frame_accurate_check.setToolTip(
            "When off (default), uses stream-copy for blazing fast speeds.\n"
            "When on, does a full re-encode to ensure cuts are exactly at the requested frames."
        )
        bottom_row.addWidget(self._frame_accurate_check)

        bottom_row.addStretch()

        preset_lbl = QLabel("Presets:")
        preset_lbl.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        preset_lbl.setStyleSheet("color: rgba(235, 235, 245, 0.4);")
        bottom_row.addWidget(preset_lbl)

        presets = [
            ("First 30s", self._preset_first_30s),
            ("First 60s", self._preset_first_60s),
            ("Middle 50%", self._preset_middle_half),
            ("Last 30s", self._preset_last_30s),
            ("Full", self._preset_full),
        ]
        for name, callback in presets:
            pb = QPushButton(name)
            pb.setProperty("class", "presetChip")
            pb.setFont(QFont("Segoe UI", 8))
            pb.setFixedHeight(22)
            pb.clicked.connect(callback)
            bottom_row.addWidget(pb)

        group_layout.addLayout(bottom_row)

        # ── 4. Validation Label (only visible when error occurs) ───────────────
        self._validation_label = QLabel("")
        self._validation_label.setFont(QFont("Segoe UI", 9))
        self._validation_label.setWordWrap(True)
        self._validation_label.hide()
        group_layout.addWidget(self._validation_label)

        layout.addWidget(group)

        # Initially disabled until video duration is known
        self._start_input.setEnabled(False)
        self._end_input.setEnabled(False)
        self._range_slider.setEnabled(False)
        self._clipping_container.setEnabled(False)
        self._frame_accurate_check.setEnabled(False)

    def set_duration(self, duration: float) -> None:
        """
        Set the video duration and update the UI.

        Args:
            duration: Video duration in seconds.
        """
        self._duration = duration
        hms = seconds_to_hms(duration)
        self._duration_label.setText(f"Video duration: {hms}")
        self._slider_end_label.setText(hms)
        self._range_slider.set_duration(duration)

        self._syncing = True
        self._start_input.setText("00:00:00")
        self._end_input.setText(hms)
        self._range_slider.set_range(0.0, duration, block_signals=True)
        self._syncing = False

        self._on_full_video_toggled(self._full_video_check.isChecked())

    def reset(self) -> None:
        """Reset the widget to its initial state."""
        self._duration = 0.0
        self._syncing = True
        self._start_input.setText("00:00:00")
        self._end_input.setText("00:00:00")
        self._slider_end_label.setText("00:00:00")
        self._duration_label.setText("Video duration: --:--:--")
        self._clip_badge.setText("Clip: Full Video")
        self._clip_badge.setStyleSheet(
            "QLabel { background: rgba(255, 255, 255, 0.07); color: rgba(235, 235, 245, 0.7);"
            "  border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 9px; padding: 2px 10px; }"
        )
        self._validation_label.setText("")
        self._validation_label.hide()
        self._full_video_check.setChecked(True)
        self._frame_accurate_check.setChecked(False)
        self._range_slider.set_duration(100.0)
        self._range_slider.set_range(0.0, 100.0, block_signals=True)
        self._syncing = False

        self._start_input.setEnabled(False)
        self._end_input.setEnabled(False)
        self._range_slider.setEnabled(False)
        self._clipping_container.setEnabled(False)
        self._frame_accurate_check.setEnabled(False)

    @property
    def is_full_video(self) -> bool:
        """Whether the full video option is selected."""
        return self._full_video_check.isChecked()

    @property
    def is_frame_accurate(self) -> bool:
        """Whether frame accurate clipping (slower) is selected."""
        return self._frame_accurate_check.isChecked()

    def get_start_time(self) -> Optional[str]:
        """Get the start time string, or None if full video mode."""
        if self._full_video_check.isChecked():
            return None
        return self._start_input.text()

    def get_end_time(self) -> Optional[str]:
        """Get the end time string, or None if full video mode."""
        if self._full_video_check.isChecked():
            return None
        return self._end_input.text()

    def get_start_seconds(self) -> Optional[float]:
        """Get the start time in seconds, or None if full video mode."""
        t = self.get_start_time()
        return parse_time_to_seconds(t) if t else None

    def get_end_seconds(self) -> Optional[float]:
        """Get the end time in seconds, or None if full video mode."""
        t = self.get_end_time()
        return parse_time_to_seconds(t) if t else None

    def validate(self) -> tuple[bool, str]:
        """
        Validate the current time range settings.

        Returns:
            A tuple of (is_valid, message).
        """
        if self._full_video_check.isChecked():
            return True, "Full video selected."

        start_str = self._start_input.text()
        end_str = self._end_input.text()

        if self._duration <= 0:
            return False, "Video duration not set."

        return validate_time_range(start_str, end_str, self._duration)

    def _on_full_video_toggled(self, checked: bool) -> None:
        """Toggle time inputs based on full video checkbox."""
        clipping_enabled = not checked and self._duration > 0
        self._start_input.setEnabled(clipping_enabled)
        self._end_input.setEnabled(clipping_enabled)
        self._range_slider.setEnabled(clipping_enabled)
        self._clipping_container.setEnabled(clipping_enabled)
        self._frame_accurate_check.setEnabled(clipping_enabled)

        if checked:
            self._clip_badge.setText("Clip: Full Video")
            self._clip_badge.setStyleSheet(
                "QLabel { background: rgba(255, 255, 255, 0.07); color: rgba(235, 235, 245, 0.7);"
                "  border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 9px; padding: 2px 10px; }"
            )
            self._validation_label.setText("")
            self._validation_label.hide()
        else:
            self._on_time_changed()

        self.range_changed.emit()

    def _on_slider_range_changed(self, start_s: float, end_s: float) -> None:
        """Synchronize inputs when slider is dragged."""
        if self._syncing:
            return
        self._syncing = True
        self._start_input.setText(seconds_to_hms(start_s))
        self._end_input.setText(seconds_to_hms(end_s))
        self._syncing = False
        self._on_time_changed()

    def _on_time_changed(self) -> None:
        """Validate time range when inputs change and sync slider."""
        if self._syncing:
            return

        start_s = parse_time_to_seconds(self._start_input.text())
        end_s = parse_time_to_seconds(self._end_input.text())

        if start_s is not None and end_s is not None and self._duration > 0:
            self._syncing = True
            self._range_slider.set_range(start_s, end_s, block_signals=True)
            self._syncing = False

        if self._full_video_check.isChecked() or self._duration <= 0:
            return

        is_valid, msg = self.validate()
        if is_valid:
            if start_s is not None and end_s is not None and end_s >= start_s:
                clip_dur = end_s - start_s
                clip_str = seconds_to_hms(clip_dur)
                self._clip_badge.setText(f"✓ Clip: {clip_str}")
                self._clip_badge.setStyleSheet(
                    "QLabel { background: rgba(46, 204, 113, 0.15); color: #2ecc71; "
                    "  border: 1px solid rgba(46, 204, 113, 0.35); border-radius: 9px; padding: 2px 10px; }"
                )
            self._validation_label.setText("")
            self._validation_label.hide()
        else:
            self._clip_badge.setText("Clip: Error")
            self._clip_badge.setStyleSheet(
                "QLabel { background: rgba(231, 76, 60, 0.15); color: #e74c3c; "
                "  border: 1px solid rgba(231, 76, 60, 0.35); border-radius: 9px; padding: 2px 10px; }"
            )
            self._validation_label.setText(f"✗ {msg}")
            self._validation_label.setStyleSheet("color: #e74c3c; margin-top: 2px;")
            self._validation_label.show()

        self.range_changed.emit()

    def _set_start_seconds(self, s: float) -> None:
        """Set start time directly in seconds."""
        end_s = parse_time_to_seconds(self._end_input.text()) or self._duration
        s = max(0.0, min(s, end_s))
        self._start_input.setText(seconds_to_hms(s))

    def _set_end_seconds(self, s: float) -> None:
        """Set end time directly in seconds."""
        start_s = parse_time_to_seconds(self._start_input.text()) or 0.0
        s = max(start_s, min(s, self._duration))
        self._end_input.setText(seconds_to_hms(s))

    def _nudge_start(self, delta: float) -> None:
        """Nudge start time by delta seconds."""
        cur = parse_time_to_seconds(self._start_input.text()) or 0.0
        self._set_start_seconds(cur + delta)

    def _nudge_end(self, delta: float) -> None:
        """Nudge end time by delta seconds."""
        cur = parse_time_to_seconds(self._end_input.text()) or self._duration
        self._set_end_seconds(cur + delta)

    def _preset_first_30s(self) -> None:
        """Preset: First 30 seconds of video."""
        self._set_start_seconds(0.0)
        self._set_end_seconds(min(30.0, self._duration))

    def _preset_first_60s(self) -> None:
        """Preset: First 60 seconds of video."""
        self._set_start_seconds(0.0)
        self._set_end_seconds(min(60.0, self._duration))

    def _preset_middle_half(self) -> None:
        """Preset: Middle 50% of video."""
        if self._duration > 0:
            quarter = self._duration / 4.0
            self._set_start_seconds(quarter)
            self._set_end_seconds(self._duration - quarter)

    def _preset_last_30s(self) -> None:
        """Preset: Last 30 seconds of video."""
        if self._duration > 0:
            self._set_start_seconds(max(0.0, self._duration - 30.0))
            self._set_end_seconds(self._duration)

    def _preset_full(self) -> None:
        """Preset: Full video duration."""
        self._set_start_seconds(0.0)
        self._set_end_seconds(self._duration)
