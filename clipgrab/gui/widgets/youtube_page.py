"""
YouCut — YouTube Clipper Page.

Implements all 8 states per specification:
1. Empty state (neutral, no hardcoded numbers, disabled controls)
2. Fetching state (skeleton, spinner/disabled button, status pill "Fetching info...")
3. Loaded video state (chips, title, metadata, format selector, trim controls)
4. Loaded audio-only state (audio qualities, MP3/M4A/WAV chips, inline extension)
5. Full video ON state (dimmed clipping, badge "Full video · duration", primary button "Download full video")
6. Error state (inline error card with Retry button, red styling only on error)
7. Wrong-platform banner (inline alert when Instagram/Facebook link pasted)
8. Playlist URL notice (playlists not supported yet; single video downloads with --no-playlist)

Includes full background fetch worker with 60-second watchdog timeout and step-by-step INFO logging.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from typing import Optional, cast

import requests
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clipgrab.config.settings import AppSettings
from clipgrab.core.downloader import DownloadRequest, DownloadWorker
from clipgrab.core.ffmpeg_utils import get_ffmpeg_directory, get_ytdlp_path
from clipgrab.core.formats import AudioFormat, AudioOutputFormat, VideoFormat, VideoInfo, parse_video_info
from clipgrab.core.playback_controller import PlaybackController, get_playback_controller
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import DownloadEngine, detect_platform
from clipgrab.core.validator import sanitize_filename, suggest_filename
from clipgrab.gui.icons import get_svg_icon, get_svg_pixmap
from clipgrab.gui.widgets.flow_layout import FlowLayout
from clipgrab.gui.theme import (
    BG_APP,
    BG_CARD,
    BG_CARD_ALT,
    BG_INPUT,
    BG_PILL,
    BG_PILL_SELECTED,
    BORDER_CARD,
    BORDER_CARD_SUBTLE,
    BORDER_DIVIDER,
    BORDER_INPUT,
    BORDER_PILL,
    BORDER_PILL_SELECTED,
    COLOR_ACCENT,
    COLOR_ERROR,
    COLOR_ERROR_BG,
    COLOR_SUCCESS,
    COLOR_WARNING,
    RIGHT_COL_WIDTH,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    get_font,
)

logger = get_logger("youtube_page")


def _format_time(seconds: int) -> str:
    """Format seconds into M:SS or H:MM:SS."""
    if seconds < 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_views(count: Optional[int]) -> str:
    """Format view count to compact notation (e.g. 1.2M views)."""
    if not count:
        return ""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M views"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K views"
    return f"{count} views"


class ChipButton(QPushButton):
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


class SegmentedToggle(QFrame):
    """Segmented Video | Audio only mode switch."""

    toggled = Signal(str)  # "video" or "audio"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("segmentedToggle")
        self.setFixedHeight(38)
        self._mode = "video"

        self.setStyleSheet(
            f"QFrame#segmentedToggle {{ background-color: {BG_INPUT}; border-radius: 12px; border: 1px solid {BORDER_INPUT}; }}"
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
            f"QFrame#segmentedToggle {{ background-color: {container_bg}; border-radius: 12px; border: 1px solid {container_border}; }}"
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


class TimelineRulerWidget(QWidget):
    """Adaptive timeline ticks ruler."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(18)
        self._duration = 0

    def set_duration(self, duration: int) -> None:
        self._duration = duration
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._duration <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(get_font(pixel_size=11))
        painter.setPen(QColor(TEXT_MUTED))

        w = self.width()
        # Compute 5-6 adaptive ticks
        num_ticks = 5
        for i in range(num_ticks + 1):
            sec = int(i * self._duration / num_ticks)
            label = _format_time(sec)
            x = int(i * (w - 40) / num_ticks)
            painter.drawText(x, 13, label)
        painter.end()


class VideoCanvasWidget(QFrame):
    """
    Inner canvas for rendering video thumbnail or empty placeholder with
    clean 16:9 aspect ratio and letterboxing, with overlay scrim.
    """

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoCanvas")
        self.setStyleSheet("background-color: #0E1017; border-radius: 12px;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap: Optional[QPixmap] = None

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self._pixmap = pixmap
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._pixmap and not self._pixmap.isNull():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

            w = self.width()
            h = self.height()

            # Clip rounded border
            clip_path = QPainterPath()
            clip_path.addRoundedRect(0, 0, w, h, 12, 12)
            painter.setClipPath(clip_path)

            # Draw background
            painter.fillRect(0, 0, w, h, QColor("#0A0C12"))

            # Calculate 16:9 letterboxed/pillarboxed rect
            target_aspect = 16.0 / 9.0
            cur_aspect = w / max(1, h)
            if abs(cur_aspect - target_aspect) < 0.03:
                draw_w = w
                draw_h = h
            elif cur_aspect > target_aspect:
                draw_h = h
                draw_w = int(round(h * target_aspect))
            else:
                draw_w = w
                draw_h = int(round(w / target_aspect))

            draw_x = (w - draw_w) // 2
            draw_y = (h - draw_h) // 2

            scaled_pix = self._pixmap.scaled(
                draw_w, draw_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            crop_x = max(0, (scaled_pix.width() - draw_w) // 2)
            crop_y = max(0, (scaled_pix.height() - draw_h) // 2)
            painter.drawPixmap(draw_x, draw_y, scaled_pix, crop_x, crop_y, draw_w, draw_h)

            # Dark scrim for readability
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 75))

            # Border
            painter.setClipping(False)
            painter.setPen(QColor(BORDER_CARD_SUBTLE))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(0, 0, w - 1, h - 1, 12, 12)
            painter.end()


class VideoSurfaceWidget(QFrame):
    """
    Responsive video preview surface supporting dynamic screen resize (16:9 ratio),
    high-resolution static thumbnail/scrim rendering, and live embedded video/audio playback.
    """

    clicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoSurface")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(200)
        self.setMaximumHeight(540)
        self.setStyleSheet(
            f"QFrame#videoSurface {{ background-color: #0E1017; border-radius: 12px; border: 1px solid {BORDER_CARD_SUBTLE}; }}"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._stack = QStackedLayout(self)
        self._stack.setStackingMode(QStackedLayout.StackingMode.StackOne)

        # Page 0: Thumbnail canvas with layout for big play button and hint
        self._canvas = VideoCanvasWidget()
        self._canvas.clicked.connect(self.clicked.emit)
        self._canvas_layout = QVBoxLayout(self._canvas)
        self._canvas_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._canvas_layout.setSpacing(8)
        self._stack.addWidget(self._canvas)

        # Page 1: Live WebEngine video & audio player
        self._web_view = None
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView
            from PySide6.QtWebEngineCore import QWebEngineSettings
            self._web_view = QWebEngineView()
            self._web_view.setStyleSheet("background: #000000; border-radius: 12px;")
            s = self._web_view.settings()
            s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
            s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            self._stack.addWidget(self._web_view)
        except Exception as e:
            logger.warning("Could not initialize QWebEngineView: %s", e)
            self._web_view = None

        self._current_video_id: str = ""
        self._is_web_ready: bool = False

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        h = int(round(width * 9.0 / 16.0))
        return min(max(h, 200), 540)

    def sizeHint(self) -> QSize:
        return QSize(640, 360)

    @property
    def canvas_layout(self) -> QVBoxLayout:
        return self._canvas_layout

    def set_pixmap(self, pixmap: Optional[QPixmap]) -> None:
        self._canvas.set_pixmap(pixmap)
        self._stack.setCurrentWidget(self._canvas)

    def load_video(self, video_id: str) -> None:
        if not video_id or not self._web_view:
            return
        if self._current_video_id == video_id and self._is_web_ready:
            return
        self._current_video_id = video_id
        self._is_web_ready = False

        html = f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 100%; height: 100%; overflow: hidden; background: #000; }}
#player-container {{ width: 100%; height: 100%; position: absolute; top: 0; left: 0; }}
iframe {{ width: 100%; height: 100%; border: none; }}
</style>
</head>
<body>
<div id="player-container"><div id="player"></div></div>
<script>
var tag = document.createElement('script');
tag.src = 'https://www.youtube.com/iframe_api';
var firstScriptTag = document.getElementsByTagName('script')[0];
firstScriptTag.parentNode.insertBefore(tag, firstScriptTag);
var player;
window.isPlayerReady = false;
function apiDisableCaptions() {{
    try {{
        if (player) {{
            if (player.unloadModule) {{
                player.unloadModule("captions");
                player.unloadModule("cc");
            }}
            if (player.setOption) {{
                player.setOption("captions", "track", {{}});
            }}
        }}
    }} catch(e) {{}}
}}
function onYouTubeIframeAPIReady() {{
    player = new YT.Player('player', {{
        height: '100%',
        width: '100%',
        videoId: '{video_id}',
        playerVars: {{
            'playsinline': 1,
            'controls': 0,
            'rel': 0,
            'modestbranding': 1,
            'disablekb': 1,
            'fs': 0,
            'iv_load_policy': 3,
            'cc_load_policy': 0,
            'origin': 'https://www.youtube-nocookie.com'
        }},
        events: {{
            'onReady': function(event) {{
                window.isPlayerReady = true;
                apiDisableCaptions();
            }}
        }}
    }});
}}
function apiPlay() {{ if (player && player.playVideo) {{ player.playVideo(); apiDisableCaptions(); }} }}
function apiPause() {{ if (player && player.pauseVideo) player.pauseVideo(); }}
function apiSeek(s) {{ if (player && player.seekTo) player.seekTo(s, true); }}
function apiMute() {{ if (player && player.mute) player.mute(); }}
function apiUnMute() {{ if (player && player.unMute) player.unMute(); }}
function apiSetVolume(vol) {{ if (player && player.setVolume) player.setVolume(vol); }}
function apiSetSpeed(rate) {{
    if (player && player.setPlaybackRate) player.setPlaybackRate(rate);
    apiDisableCaptions();
}}
function apiSetQuality(q) {{
    if (player && player.setPlaybackQuality) {{
        var ytQ = 'default';
        var str = String(q).toLowerCase();
        if (str.indexOf('2160') !== -1 || str.indexOf('4k') !== -1) ytQ = 'hd2160';
        else if (str.indexOf('1440') !== -1) ytQ = 'hd1440';
        else if (str.indexOf('1080') !== -1) ytQ = 'hd1080';
        else if (str.indexOf('720') !== -1) ytQ = 'hd720';
        else if (str.indexOf('480') !== -1) ytQ = 'large';
        else if (str.indexOf('360') !== -1) ytQ = 'medium';
        else if (str.indexOf('240') !== -1) ytQ = 'small';
        else if (str.indexOf('144') !== -1) ytQ = 'tiny';
        else ytQ = 'auto';
        player.setPlaybackQuality(ytQ);
    }}
    apiDisableCaptions();
}}
function apiSetCaptions(enabled) {{
    if (!enabled) {{
        apiDisableCaptions();
    }} else {{
        try {{
            if (player && player.loadModule) {{
                player.loadModule("captions");
            }}
        }} catch(e) {{}}
    }}
}}
function apiGetTime() {{ return (player && player.getCurrentTime) ? player.getCurrentTime() : 0; }}
</script>
</body>
</html>
"""
        self._web_view.setHtml(html, QUrl("https://www.youtube-nocookie.com/"))
        self._is_web_ready = True

    def play(self) -> None:
        if self._web_view and self._current_video_id:
            self._stack.setCurrentWidget(self._web_view)
            self._web_view.page().runJavaScript("apiPlay();")

    def pause(self) -> None:
        if self._web_view:
            self._web_view.page().runJavaScript("apiPause();")

    def seek(self, seconds: float) -> None:
        if self._web_view and self._current_video_id:
            self._web_view.page().runJavaScript(f"apiSeek({seconds});")

    def set_muted(self, muted: bool) -> None:
        if self._web_view:
            if muted:
                self._web_view.page().runJavaScript("apiMute();")
            else:
                self._web_view.page().runJavaScript("apiUnMute();")

    def set_volume(self, volume: int) -> None:
        if self._web_view:
            self._web_view.page().runJavaScript(f"apiSetVolume({int(volume)});")

    def set_speed(self, speed: float) -> None:
        if self._web_view:
            self._web_view.page().runJavaScript(f"apiSetSpeed({float(speed)});")

    def set_quality(self, quality: str) -> None:
        if self._web_view:
            safe_q = str(quality).replace('"', '')
            self._web_view.page().runJavaScript(f'apiSetQuality("{safe_q}");')

    def set_captions(self, enabled: bool) -> None:
        if self._web_view:
            arg = "true" if enabled else "false"
            self._web_view.page().runJavaScript(f"apiSetCaptions({arg});")

    def get_time(self, callback) -> None:
        if self._web_view and self._current_video_id:
            self._web_view.page().runJavaScript("apiGetTime();", callback)

    def clear(self) -> None:
        self._current_video_id = ""
        self._is_web_ready = False
        if self._web_view:
            self._web_view.setHtml("<html><body style='background:#000;'></body></html>")
        self._canvas.set_pixmap(None)
        self._stack.setCurrentWidget(self._canvas)


class TimelineTrack(QFrame):
    """
    Timeline track widget with interactive draggable selection range handles and playhead.
    Supports empty state (disabled neutral), active state, and dimmed state.
    """

    range_changed = Signal(int, int)  # (start_seconds, end_seconds)
    playhead_changed = Signal(int)    # (playhead_seconds)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("timelineTrack")
        self.setFixedHeight(54)
        self.setStyleSheet(
            f"QFrame#timelineTrack {{ background-color: #0E1017; border-radius: 10px; border: 1px solid {BORDER_CARD_SUBTLE}; }}"
        )
        self.setMouseTracking(True)
        self._duration = 0
        self._start_sec = 0
        self._end_sec = 0
        self._playhead_sec = 0
        self._is_enabled = False
        self._dimmed = False

        # Drag state
        self._drag_target: Optional[str] = None  # "start", "end", "range", "playhead"
        self._drag_start_x: int = 0
        self._drag_orig_start: int = 0
        self._drag_orig_end: int = 0

    def sec_to_x(self, sec: float) -> int:
        if self._duration <= 0 or self.width() <= 1:
            return 0
        ratio = max(0.0, min(1.0, float(sec) / float(self._duration)))
        return int(ratio * (self.width() - 1))

    def x_to_sec(self, x: int) -> int:
        if self._duration <= 0 or self.width() <= 1:
            return 0
        ratio = max(0.0, min(1.0, float(x) / float(self.width() - 1)))
        return int(round(ratio * float(self._duration)))

    def set_duration(self, duration: int) -> None:
        self._duration = max(0, duration)
        self._start_sec = 0
        self._end_sec = self._duration
        self._playhead_sec = 0
        self._is_enabled = (self._duration > 0)
        self._drag_target = None
        self.update()

    def set_range(self, start: int, end: int) -> None:
        self._start_sec = max(0, min(start, self._duration))
        self._end_sec = max(self._start_sec, min(end, self._duration))
        self.update()

    def set_playhead(self, sec: int) -> None:
        self._playhead_sec = max(0, min(sec, self._duration))
        self.update()

    def set_dimmed(self, dimmed: bool) -> None:
        self._dimmed = dimmed
        self._drag_target = None
        self.update()

    def _hit_test(self, x: int, y: int) -> Optional[str]:
        if not self._is_enabled or self._dimmed or self._duration <= 0:
            return None
        start_x = self.sec_to_x(self._start_sec)
        end_x = self.sec_to_x(self._end_sec)
        playhead_x = self.sec_to_x(self._playhead_sec)

        # 1. Start handle & tag
        if abs(x - start_x) <= 12:
            return "start"

        # 2. End handle & tag
        if abs(x - end_x) <= 12:
            return "end"

        # 3. Playhead knob or vertical line
        if abs(x - playhead_x) <= 8:
            return "playhead"

        # 4. Top bar range slide (y <= 22 inside selection)
        if y <= 22 and (start_x + 12 < x < end_x - 12):
            return "range"

        return "track"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_enabled and not self._dimmed:
            x = event.pos().x()
            y = event.pos().y()
            hit = self._hit_test(x, y)
            if hit == "start":
                self._drag_target = "start"
            elif hit == "end":
                self._drag_target = "end"
            elif hit == "playhead":
                self._drag_target = "playhead"
                new_sec = self.x_to_sec(x)
                self._playhead_sec = new_sec
                self.playhead_changed.emit(new_sec)
                self.update()
            elif hit == "range":
                self._drag_target = "range"
                self._drag_start_x = x
                self._drag_orig_start = self._start_sec
                self._drag_orig_end = self._end_sec
            elif hit == "track":
                # Seek playhead to clicked spot and allow continuous dragging
                new_sec = self.x_to_sec(x)
                self._playhead_sec = new_sec
                self._drag_target = "playhead"
                self.playhead_changed.emit(new_sec)
                self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.pos().x()
        y = event.pos().y()

        if not self._drag_target:
            hit = self._hit_test(x, y)
            if hit in ("start", "end"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit == "range":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            elif hit in ("playhead", "track"):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            super().mouseMoveEvent(event)
            return

        if self._drag_target == "start":
            new_sec = max(0, min(self.x_to_sec(x), self._end_sec - 1))
            if new_sec != self._start_sec:
                self._start_sec = new_sec
                self.range_changed.emit(self._start_sec, self._end_sec)
                self.update()
        elif self._drag_target == "end":
            new_sec = max(self._start_sec + 1, min(self.x_to_sec(x), self._duration))
            if new_sec != self._end_sec:
                self._end_sec = new_sec
                self.range_changed.emit(self._start_sec, self._end_sec)
                self.update()
        elif self._drag_target == "range":
            delta_sec = self.x_to_sec(x) - self.x_to_sec(self._drag_start_x)
            range_dur = self._drag_orig_end - self._drag_orig_start
            new_start = max(0, min(self._drag_orig_start + delta_sec, self._duration - range_dur))
            new_end = new_start + range_dur
            if new_start != self._start_sec or new_end != self._end_sec:
                self._start_sec = new_start
                self._end_sec = new_end
                self.range_changed.emit(self._start_sec, self._end_sec)
                self.update()
        elif self._drag_target == "playhead":
            new_sec = max(0, min(self.x_to_sec(x), self._duration))
            if new_sec != self._playhead_sec:
                self._playhead_sec = new_sec
                self.playhead_changed.emit(new_sec)
                self.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_target = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        if not self._is_enabled or self._duration <= 0:
            # Empty / disabled neutral track (Defect 5: no demo data)
            painter.fillRect(0, 0, w, h, QColor(255, 255, 255, 6))
            painter.end()
            return

        # 1. Background simulated filmstrip blocks (matching reference image)
        num_blocks = 9
        block_w = w / num_blocks
        palette_blocks = [
            QColor(28, 38, 50), QColor(44, 40, 34), QColor(32, 46, 44), QColor(46, 34, 48),
            QColor(30, 42, 54), QColor(38, 34, 44), QColor(26, 36, 42), QColor(40, 38, 32),
            QColor(32, 44, 52)
        ]
        for b in range(num_blocks):
            bx = int(b * block_w)
            bw = int((b + 1) * block_w) - bx
            painter.fillRect(bx, 0, bw, h, palette_blocks[b % len(palette_blocks)])

        if self._dimmed:
            # Full video mode: track is uniformly dimmed
            painter.fillRect(0, 0, w, h, QColor(0, 0, 0, 160))
            painter.setPen(QColor(BORDER_CARD_SUBTLE))
            painter.drawRect(0, 0, w - 1, h - 1)
            painter.end()
            return

        # Loaded timeline range
        start_x = self.sec_to_x(self._start_sec)
        end_x = self.sec_to_x(self._end_sec)
        sel_w = max(6, end_x - start_x)

        # 2. Dimmed regions outside selection
        painter.fillRect(0, 0, start_x, h, QColor(0, 0, 0, 195))
        painter.fillRect(end_x, 0, w - end_x, h, QColor(0, 0, 0, 195))

        # 3. Selected range inner tint & border
        painter.fillRect(start_x, 0, sel_w, h, QColor(255, 92, 77, 18))
        painter.setPen(QPen(QColor(COLOR_ACCENT), 2))
        painter.drawRect(start_x, 1, sel_w, h - 2)

        # 4. Drag handles
        handle_w = 6
        painter.setBrush(QColor(COLOR_ACCENT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(start_x - handle_w // 2, 0, handle_w, h, 2, 2)
        painter.drawRoundedRect(end_x - handle_w // 2, 0, handle_w, h, 2, 2)

        # 5. Handle bubble tags
        painter.setFont(get_font(pixel_size=10, weight=QFont.Weight.Bold))
        tag_w, tag_h = 38, 16
        # Start tag
        st_x = max(2, min(start_x - tag_w // 2, w - tag_w - 2))
        painter.setBrush(QColor(COLOR_ACCENT))
        painter.drawRoundedRect(st_x, 4, tag_w, tag_h, 4, 4)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(st_x, 4, tag_w, tag_h, Qt.AlignmentFlag.AlignCenter, _format_time(self._start_sec))

        # End tag
        et_x = max(2, min(end_x - tag_w // 2, w - tag_w - 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLOR_ACCENT))
        painter.drawRoundedRect(et_x, 4, tag_w, tag_h, 4, 4)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(et_x, 4, tag_w, tag_h, Qt.AlignmentFlag.AlignCenter, _format_time(self._end_sec))

        # 6. Playhead indicator (white line + circular top knob)
        playhead_x = self.sec_to_x(self._playhead_sec)
        painter.setPen(QPen(QColor("#FFFFFF"), 2))
        painter.drawLine(playhead_x, 2, playhead_x, h - 2)
        painter.setBrush(QColor("#FFFFFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(playhead_x - 4, 6, 8, 8)

        painter.end()


class YouTubePage(QWidget):
    """
    Modern YouTube page layout and fetch engine.
    Matches reference design and handles all 8 required states.
    """

    status_message = Signal(str)
    status_pill_update = Signal(str, str)  # (text, state)
    add_to_queue_requested = Signal(dict)
    download_requested = Signal(dict)
    switch_platform_requested = Signal(str)  # platform_id
    info_fetched = Signal(object)  # (VideoInfo)

    def __init__(self, settings: Optional[AppSettings] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._video_info: Optional[VideoInfo] = None
        self._is_audio_only = False
        self._is_full_video = False
        self._selected_format = "MP4"
        self._start_seconds = 0
        self._end_seconds = 0
        self._saved_range = (0, 0)
        self._is_frame_accurate = False

        # Playback engine state
        self._is_playing = False
        self._is_muted = False
        self._playhead_seconds: float = 0.0
        self._playhead_mode = "full"
        self._playback_timer = QTimer(self)
        self._playback_timer.setInterval(250)
        self._playback_timer.timeout.connect(self._on_playback_tick)

        # Wire up central playback controller
        self._controller = get_playback_controller()
        self._controller.play_state_changed.connect(self._on_controller_play_state_changed)
        self._controller.playhead_changed.connect(self._on_controller_playhead_changed)
        self._controller.volume_changed.connect(self._on_controller_volume_changed)
        self._controller.speed_changed.connect(self._on_controller_speed_changed)
        self._controller.quality_changed.connect(self._on_controller_quality_changed)
        self._controller.captions_changed.connect(self._on_controller_captions_changed)
        self._controller.in_point_changed.connect(self._on_controller_in_point_changed)
        self._controller.out_point_changed.connect(self._on_controller_out_point_changed)

        # Background worker thread & timeout timer
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[DownloadWorker] = None
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.setInterval(60000)  # 60s timeout
        self._watchdog_timer.timeout.connect(self._on_fetch_timeout)

        # Responsive state tracking
        self._current_bp: str = "wide"
        self._full_video_title: str = ""
        self._full_folder_path: str = os.path.join(os.path.expanduser("~"), "Downloads")
        if self._settings and getattr(self._settings, "last_output_folder", None):
            self._full_folder_path = self._settings.last_output_folder
        self._tc_is_vertical: bool = False

        self._setup_ui()
        self._controller.register_adapter(self._video_surface)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.installEventFilter(self)
        self.set_empty_state()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UI SETUP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _setup_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for smaller viewports
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Centered container constrained to max 1480px (Defect 7)
        container = QWidget()
        center_row = QHBoxLayout(container)
        center_row.setContentsMargins(0, 0, 0, 0)
        center_row.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        self._content_box = QWidget()
        self._content_box.setMaximumWidth(1680)
        self._content_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        page_layout = QVBoxLayout(self._content_box)
        page_layout.setContentsMargins(14, 12, 14, 14)
        page_layout.setSpacing(12)

        # ── 1. Top URL Bar ───────────────────────────────────────────────────
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
        self._url_input.setPlaceholderText("Paste YouTube video, Shorts, or clip URL...")
        self._url_input.setFont(get_font(pixel_size=14))
        self._url_input.setStyleSheet(
            "QLineEdit { background: transparent; border: none; color: #FFFFFF; padding: 0; }"
        )
        self._url_input.returnPressed.connect(self._on_fetch_clicked)
        self._url_input.textChanged.connect(self._on_url_text_changed)
        url_box_layout.addWidget(self._url_input, stretch=1)

        self._platform_chip = QLabel("YouTube")
        self._platform_chip.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._platform_chip.setStyleSheet(
            "background-color: #17212F; color: #58A6FF; border: 1px solid #1F385C; border-radius: 6px; padding: 2px 8px;"
        )
        self._platform_chip.hide()
        url_box_layout.addWidget(self._platform_chip)

        self._valid_check = QLabel()
        self._valid_check.setPixmap(get_svg_pixmap("check", color=COLOR_SUCCESS, size=14))
        self._valid_check.hide()
        url_box_layout.addWidget(self._valid_check)

        url_bar_row.addWidget(self._url_input_box, stretch=1)

        # Paste Button
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

        # Fetch Info Button (Coral accent)
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

        # ── 2. Wrong-Platform Alert Banner (Dismissible) ──────────────────────
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
        self._wp_switch_btn.clicked.connect(self._on_switch_platform_clicked)
        wp_layout.addWidget(self._wp_switch_btn)

        wp_dismiss_btn = QPushButton()
        wp_dismiss_btn.setFixedSize(24, 24)
        wp_dismiss_btn.setIcon(get_svg_icon("x", color=TEXT_SECONDARY, size=14))
        wp_dismiss_btn.setStyleSheet("background: transparent; border: none;")
        wp_dismiss_btn.clicked.connect(self._wrong_platform_banner.hide)
        wp_layout.addWidget(wp_dismiss_btn)

        self._wrong_platform_banner.hide()
        page_layout.addWidget(self._wrong_platform_banner)

        # ── 3. Responsive Workspace Container ─────────────────────────────────
        self._workspace_container = QWidget()
        self._workspace_stack = QStackedLayout(self._workspace_container)
        self._workspace_stack.setContentsMargins(0, 0, 0, 0)

        # Page 0: Two-Column (Wide & Medium)
        self._two_col_widget = QWidget()
        self._two_col_layout = QHBoxLayout(self._two_col_widget)
        self._two_col_layout.setContentsMargins(0, 0, 0, 0)
        self._two_col_layout.setSpacing(16)

        self._left_col_widget = QWidget()
        self._left_col_layout = QVBoxLayout(self._left_col_widget)
        self._left_col_layout.setContentsMargins(0, 0, 0, 0)
        self._left_col_layout.setSpacing(16)

        self._right_col_widget = QWidget()
        self._right_col_layout = QVBoxLayout(self._right_col_widget)
        self._right_col_layout.setContentsMargins(0, 0, 0, 0)
        self._right_col_layout.setSpacing(10)

        self._two_col_layout.addWidget(self._left_col_widget, stretch=1)
        self._two_col_layout.addWidget(self._right_col_widget)
        self._workspace_stack.addWidget(self._two_col_widget)

        # Page 1: One-Column (Narrow)
        self._one_col_widget = QWidget()
        self._one_col_layout = QVBoxLayout(self._one_col_widget)
        self._one_col_layout.setContentsMargins(0, 0, 0, 0)
        self._one_col_layout.setSpacing(12)
        self._workspace_stack.addWidget(self._one_col_widget)

        page_layout.addWidget(self._workspace_container)

        left_col = self._left_col_layout
        right_col = self._right_col_layout

        # ── A. Preview Card ──────────────────────────────────────────────────
        self._preview_card = QFrame()
        self._preview_card.setObjectName("previewCard")
        self._preview_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        preview_layout = QVBoxLayout(self._preview_card)
        preview_layout.setContentsMargins(18, 14, 18, 14)
        preview_layout.setSpacing(10)

        # Chips row
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)

        self._live_chip = QLabel("Live preview")
        self._live_chip.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._live_chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._live_chip.setFixedHeight(28)
        self._live_chip.setStyleSheet(
            f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border-radius: 14px; padding: 4px 12px;"
        )
        chips_row.addWidget(self._live_chip)

        self._res_chip = QLabel("1080p")
        self._res_chip.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._res_chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._res_chip.setFixedHeight(28)
        self._res_chip.setStyleSheet(
            f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border-radius: 14px; padding: 4px 12px;"
        )
        self._res_chip.hide()  # Hidden in empty state (Defect 5)
        chips_row.addWidget(self._res_chip)
        chips_row.addStretch()
        preview_layout.addLayout(chips_row)

        # Video canvas (16:9 aspect, responsive resize)
        self._video_surface = VideoSurfaceWidget()
        surf_layout = self._video_surface.canvas_layout

        self._big_play_btn = QPushButton()
        self._big_play_btn.setFixedSize(54, 54)
        self._big_play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._big_play_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: rgba(255, 255, 255, 0.12);"
            "  border: 1px solid rgba(255, 255, 255, 0.2);"
            "  border-radius: 27px;"
            "}"
            "QPushButton:hover {"
            "  background-color: rgba(255, 85, 66, 0.8);"
            "}"
        )
        self._big_play_btn.setIcon(get_svg_icon("play", color="#FFFFFF", size=22))
        self._big_play_btn.clicked.connect(self._toggle_play_pause)
        surf_layout.addWidget(self._big_play_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._preview_hint = QLabel("Paste a YouTube link above to start preview and clipping")
        self._preview_hint.setFont(get_font(pixel_size=13))
        self._preview_hint.setStyleSheet(f"color: {TEXT_MUTED};")
        surf_layout.addWidget(self._preview_hint, alignment=Qt.AlignmentFlag.AlignCenter)

        self._video_surface.clicked.connect(self._toggle_play_pause)
        preview_layout.addWidget(self._video_surface)

        # Player control bar matching Concept UI tightly grouped
        controls_strip = QWidget()
        controls_strip.setObjectName("playerControlsStrip")
        controls_strip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip_layout = QVBoxLayout(controls_strip)
        strip_layout.setContentsMargins(0, 4, 0, 0)
        strip_layout.setSpacing(4)

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(6)

        # Left controls
        self._rewind_btn = QPushButton()
        self._rewind_btn.setFixedSize(30, 30)
        self._rewind_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._rewind_btn.setIcon(get_svg_icon("skip-back", color="#FFFFFF", size=16))
        self._rewind_btn.setStyleSheet("background: transparent; border: none;")
        self._rewind_btn.setToolTip("Step backward 10s")
        self._rewind_btn.clicked.connect(self._on_rewind_clicked)
        controls_row.addWidget(self._rewind_btn)

        self._play_btn = QPushButton()
        self._play_btn.setFixedSize(30, 30)
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setIcon(get_svg_icon("play", color="#FFFFFF", size=16))
        self._play_btn.setStyleSheet("background: transparent; border: none;")
        self._play_btn.setToolTip("Play / Pause (Space)")
        self._play_btn.clicked.connect(self._toggle_play_pause)
        controls_row.addWidget(self._play_btn)

        self._forward_btn = QPushButton()
        self._forward_btn.setFixedSize(30, 30)
        self._forward_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._forward_btn.setIcon(get_svg_icon("skip-forward", color="#FFFFFF", size=16))
        self._forward_btn.setStyleSheet("background: transparent; border: none;")
        self._forward_btn.setToolTip("Step forward 10s")
        self._forward_btn.clicked.connect(self._on_forward_clicked)
        controls_row.addWidget(self._forward_btn)

        self._volume_btn = QPushButton()
        self._volume_btn.setFixedSize(30, 30)
        self._volume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._volume_btn.setIcon(get_svg_icon("volume", color=TEXT_SECONDARY, size=16))
        self._volume_btn.setStyleSheet("background: transparent; border: none;")
        self._volume_btn.setToolTip("Mute / Unmute")
        self._volume_btn.clicked.connect(self._toggle_volume)
        controls_row.addWidget(self._volume_btn)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(70)
        self._volume_slider.setFixedWidth(54)
        self._volume_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._volume_slider.setToolTip("Volume (0–100%)")
        self._volume_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: rgba(255, 255, 255, 0.2); border-radius: 2px; }"
            "QSlider::sub-page:horizontal { background: #FFFFFF; border-radius: 2px; }"
            "QSlider::handle:horizontal { background: #FFFFFF; border: none; width: 10px; height: 10px; margin: -3px 0; border-radius: 5px; }"
        )
        self._volume_slider.valueChanged.connect(self._on_volume_slider_changed)
        controls_row.addWidget(self._volume_slider)

        self._volume_lbl = QLabel("70%")
        self._volume_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._volume_lbl.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self._volume_lbl.setMinimumWidth(28)
        controls_row.addWidget(self._volume_lbl)

        self._timecode_lbl = QLabel("00:00 / 00:00")
        self._timecode_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._timecode_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        controls_row.addWidget(self._timecode_lbl)

        controls_row.addStretch()

        # Right controls
        self._play_selection_btn = QPushButton("Play selection")
        self._play_selection_btn.setFixedHeight(28)
        self._play_selection_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._play_selection_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_selection_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgba(255, 92, 77, 0.08); color: {COLOR_ACCENT}; border: 1px solid {COLOR_ACCENT}; border-radius: 14px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: rgba(255, 92, 77, 0.16); }}"
        )
        self._play_selection_btn.clicked.connect(self._play_selection_clicked)
        controls_row.addWidget(self._play_selection_btn)

        # Quality dropdown button (YouTube-style)
        self._quality_btn = QPushButton("1080p")
        self._quality_btn.setFixedHeight(28)
        self._quality_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._quality_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgba(255, 255, 255, 0.08); color: {TEXT_PRIMARY}; border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 14px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.16); }}"
        )
        self._quality_btn.clicked.connect(self._show_quality_menu)
        controls_row.addWidget(self._quality_btn)

        # Speed dropdown button
        self._speed_btn = QPushButton("1x")
        self._speed_btn.setFixedHeight(28)
        self._speed_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speed_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgba(255, 255, 255, 0.08); color: {TEXT_PRIMARY}; border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 14px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.16); }}"
        )
        self._speed_btn.clicked.connect(self._show_speed_menu)
        controls_row.addWidget(self._speed_btn)

        # YouTube Captions — Strictly OFF by default
        self._cc_btn = QPushButton("CC OFF")
        self._cc_btn.setFixedHeight(28)
        self._cc_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._cc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cc_btn.setToolTip("YouTube Subtitles (Always disabled by default)")
        self._cc_btn.setStyleSheet(
            f"QPushButton {{ background-color: rgba(255, 255, 255, 0.08); color: rgba(255, 255, 255, 0.7); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 14px; padding: 0 8px; }}"
            f"QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.16); }}"
        )
        self._cc_btn.clicked.connect(self._toggle_captions)
        controls_row.addWidget(self._cc_btn)

        # Playback settings button
        self._playback_settings_btn = QPushButton()
        self._playback_settings_btn.setFixedSize(28, 28)
        self._playback_settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._playback_settings_btn.setIcon(get_svg_icon("settings", color=TEXT_SECONDARY, size=16))
        self._playback_settings_btn.setStyleSheet("background: transparent; border: none;")
        self._playback_settings_btn.clicked.connect(self._show_playback_settings)
        controls_row.addWidget(self._playback_settings_btn)

        # More button for narrow viewports
        self._more_btn = QPushButton()
        self._more_btn.setFixedSize(28, 28)
        self._more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more_btn.setIcon(get_svg_icon("more-horizontal", color=TEXT_SECONDARY, size=16))
        self._more_btn.setStyleSheet("background: transparent; border: none;")
        self._more_btn.setToolTip("More playback options")
        self._more_btn.clicked.connect(self._show_more_menu)
        self._more_btn.hide()
        controls_row.addWidget(self._more_btn)

        strip_layout.addLayout(controls_row)

        # Scrubber bar spanning full preview width
        self._scrubber_slider = QSlider(Qt.Orientation.Horizontal)
        self._scrubber_slider.setObjectName("playerScrubber")
        self._scrubber_slider.setRange(0, 1000)
        self._scrubber_slider.setValue(0)
        self._scrubber_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scrubber_slider.setStyleSheet(
            "QSlider#playerScrubber { height: 16px; }"
            "QSlider#playerScrubber::groove:horizontal { height: 3px; background: rgba(255, 255, 255, 0.2); border-radius: 1.5px; }"
            "QSlider#playerScrubber::sub-page:horizontal { background: #FFFFFF; border-radius: 1.5px; }"
            "QSlider#playerScrubber::handle:horizontal { background: #FFFFFF; border: none; width: 8px; height: 8px; margin: -2.5px 0; border-radius: 4px; }"
        )
        self._scrubber_slider.sliderMoved.connect(self._on_scrubber_moved)
        strip_layout.addWidget(self._scrubber_slider)

        # In/Out shortcut indicator row
        in_out_row = QHBoxLayout()
        in_out_row.setContentsMargins(0, 0, 0, 0)
        self._in_point_shortcut_lbl = QLabel("I — In Point")
        self._in_point_shortcut_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._in_point_shortcut_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        self._in_point_shortcut_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._in_point_shortcut_lbl.setToolTip("Set In Point at current playhead (Shortcut: I)")
        self._in_point_shortcut_lbl.mousePressEvent = lambda e: self._on_shortcut_in_point()
        in_out_row.addWidget(self._in_point_shortcut_lbl)

        in_out_row.addStretch()

        self._out_point_shortcut_lbl = QLabel("O — Out Point")
        self._out_point_shortcut_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._out_point_shortcut_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        self._out_point_shortcut_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._out_point_shortcut_lbl.setToolTip("Set Out Point at current playhead (Shortcut: O)")
        self._out_point_shortcut_lbl.mousePressEvent = lambda e: self._on_shortcut_out_point()
        in_out_row.addWidget(self._out_point_shortcut_lbl)

        strip_layout.addLayout(in_out_row)
        preview_layout.addWidget(controls_strip)
        left_col.addWidget(self._preview_card)

        # ── B. Trim Card ─────────────────────────────────────────────────────
        self._trim_card = QFrame()
        self._trim_card.setObjectName("trimCard")
        self._trim_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        trim_layout = QVBoxLayout(self._trim_card)
        trim_layout.setContentsMargins(18, 14, 18, 14)
        trim_layout.setSpacing(10)

        # Header Row
        trim_header_row = QHBoxLayout()
        trim_header_row.setSpacing(8)

        trim_title_col = QVBoxLayout()
        trim_title_col.setSpacing(2)

        self._trim_title = QLabel("Trim your clip")
        self._trim_title.setFont(get_font(pixel_size=18, weight=QFont.Weight.DemiBold))
        self._trim_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        trim_title_col.addWidget(self._trim_title)

        self._trim_sub = QLabel("Drag the handles as you watch. Only the selected part is downloaded.")
        self._trim_sub.setFont(get_font(pixel_size=12))
        self._trim_sub.setStyleSheet(f"color: {TEXT_SECONDARY};")
        trim_title_col.addWidget(self._trim_sub)
        trim_header_row.addLayout(trim_title_col, stretch=1)

        # Full video toggle pill (Defect 2: fixed height 28px, fully rounded)
        self._full_video_btn = QPushButton("  Full video")
        self._full_video_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._full_video_btn.setFixedHeight(28)
        self._full_video_btn.setIcon(get_svg_icon("check", color=TEXT_MUTED, size=12))
        self._full_video_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._full_video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._full_video_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 14px; padding: 0 12px; }}"
        )
        self._full_video_btn.clicked.connect(self._toggle_full_video)
        trim_header_row.addWidget(self._full_video_btn)

        # Duration badge (Defect 2: fixed height 28px, fully rounded)
        self._duration_badge = QLabel("Full video · 00:00")
        self._duration_badge.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._duration_badge.setFixedHeight(28)
        self._duration_badge.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._duration_badge.setStyleSheet(
            f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border-radius: 14px; padding: 4px 12px;"
        )
        self._duration_badge.hide()  # Hidden in empty state
        trim_header_row.addWidget(self._duration_badge)

        trim_layout.addLayout(trim_header_row)

        # Timeline track & ruler
        self._timeline_track = TimelineTrack()
        self._timeline_track.range_changed.connect(self._on_timeline_range_changed)
        self._timeline_track.playhead_changed.connect(self._on_timeline_playhead_changed)
        trim_layout.addWidget(self._timeline_track)

        self._ruler = TimelineRulerWidget()
        trim_layout.addWidget(self._ruler)

        # START / END Cards Container (responsive side-by-side or stacked)
        self._tc_container = QWidget()
        self._tc_layout = QHBoxLayout(self._tc_container)
        self._tc_layout.setContentsMargins(0, 0, 0, 0)
        self._tc_layout.setSpacing(10)

        # START Card
        self._start_card = QFrame()
        self._start_card.setObjectName("startCard")
        self._start_card.setFixedHeight(80)
        self._start_card.setStyleSheet(
            f"QFrame#startCard {{ background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 12px; }}"
        )
        start_layout = QHBoxLayout(self._start_card)
        start_layout.setContentsMargins(12, 8, 12, 8)
        start_layout.setSpacing(6)

        s_col = QVBoxLayout()
        s_col.setSpacing(2)
        self._s_lbl = QLabel("START")
        self._s_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._s_lbl.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 0.5px;")
        s_col.addWidget(self._s_lbl)

        self._start_tc = QLabel("00:00")
        self._start_tc.setFont(get_font(pixel_size=26, weight=QFont.Weight.DemiBold))
        self._start_tc.setStyleSheet(f"color: {TEXT_PRIMARY};")
        s_col.addWidget(self._start_tc)
        start_layout.addLayout(s_col, stretch=1)

        s_btns_col = QHBoxLayout()
        s_btns_col.setSpacing(4)

        self._s_m1 = QPushButton("-1s")
        self._s_m1.setFixedSize(34, 26)
        self._s_m1.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._s_m1.setCursor(Qt.CursorShape.PointingHandCursor)
        self._s_m1.setStyleSheet(f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0;")
        self._s_m1.clicked.connect(lambda: self._nudge_start(-1))
        s_btns_col.addWidget(self._s_m1)

        self._s_p1 = QPushButton("+1s")
        self._s_p1.setFixedSize(34, 26)
        self._s_p1.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._s_p1.setCursor(Qt.CursorShape.PointingHandCursor)
        self._s_p1.setStyleSheet(f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0;")
        self._s_p1.clicked.connect(lambda: self._nudge_start(1))
        s_btns_col.addWidget(self._s_p1)

        self._s_use = QPushButton("Use playhead")
        self._s_use.setFixedHeight(26)
        self._s_use.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._s_use.setCursor(Qt.CursorShape.PointingHandCursor)
        self._s_use.setStyleSheet(f"background-color: {BG_PILL}; color: {COLOR_ACCENT}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0 6px;")
        self._s_use.clicked.connect(self._use_playhead_as_start)
        s_btns_col.addWidget(self._s_use)

        start_layout.addLayout(s_btns_col)
        self._tc_layout.addWidget(self._start_card, stretch=1)

        # Arrow indicator
        self._arrow_lbl = QLabel("→")
        self._arrow_lbl.setFont(get_font(pixel_size=15, weight=QFont.Weight.Bold))
        self._arrow_lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        self._arrow_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tc_layout.addWidget(self._arrow_lbl)

        # END Card
        self._end_card = QFrame()
        self._end_card.setObjectName("endCard")
        self._end_card.setFixedHeight(80)
        self._end_card.setStyleSheet(
            f"QFrame#endCard {{ background-color: {BG_CARD_ALT}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 12px; }}"
        )
        end_layout = QHBoxLayout(self._end_card)
        end_layout.setContentsMargins(12, 8, 12, 8)
        end_layout.setSpacing(6)

        e_col = QVBoxLayout()
        e_col.setSpacing(2)
        self._e_lbl = QLabel("END")
        self._e_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.DemiBold))
        self._e_lbl.setStyleSheet(f"color: {TEXT_MUTED}; letter-spacing: 0.5px;")
        e_col.addWidget(self._e_lbl)

        self._end_tc = QLabel("00:00")
        self._end_tc.setFont(get_font(pixel_size=26, weight=QFont.Weight.DemiBold))
        self._end_tc.setStyleSheet(f"color: {TEXT_PRIMARY};")
        e_col.addWidget(self._end_tc)
        end_layout.addLayout(e_col, stretch=1)

        e_btns_col = QHBoxLayout()
        e_btns_col.setSpacing(4)

        self._e_m1 = QPushButton("-1s")
        self._e_m1.setFixedSize(34, 26)
        self._e_m1.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._e_m1.setCursor(Qt.CursorShape.PointingHandCursor)
        self._e_m1.setStyleSheet(f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0;")
        self._e_m1.clicked.connect(lambda: self._nudge_end(-1))
        e_btns_col.addWidget(self._e_m1)

        self._e_p1 = QPushButton("+1s")
        self._e_p1.setFixedSize(34, 26)
        self._e_p1.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._e_p1.setCursor(Qt.CursorShape.PointingHandCursor)
        self._e_p1.setStyleSheet(f"background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0;")
        self._e_p1.clicked.connect(lambda: self._nudge_end(1))
        e_btns_col.addWidget(self._e_p1)

        self._e_use = QPushButton("Use playhead")
        self._e_use.setFixedHeight(26)
        self._e_use.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._e_use.setCursor(Qt.CursorShape.PointingHandCursor)
        self._e_use.setStyleSheet(f"background-color: {BG_PILL}; color: {COLOR_ACCENT}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0 6px;")
        self._e_use.clicked.connect(self._use_playhead_as_end)
        e_btns_col.addWidget(self._e_use)

        end_layout.addLayout(e_btns_col)
        self._tc_layout.addWidget(self._end_card, stretch=1)
        trim_layout.addWidget(self._tc_container)

        # Presets Flow Row (wraps cleanly without horizontal scroll)
        presets_container = QWidget()
        presets_flow = FlowLayout(presets_container, margin=0, h_spacing=6, v_spacing=6)

        self._preset_buttons = []
        for p_label in ("First 30s", "First 60s", "Middle 50%", "Last 30s", "Reset"):
            p_btn = QPushButton(p_label)
            p_btn.setFixedHeight(28)
            p_btn.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
            p_btn.setEnabled(False)
            p_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 6px; padding: 0 8px; }}"
                f"QPushButton:hover {{ background-color: #262A37; color: {TEXT_PRIMARY}; }}"
                f"QPushButton:disabled {{ background-color: transparent; color: {TEXT_MUTED}; border-color: {BORDER_DIVIDER}; }}"
            )
            p_btn.clicked.connect(lambda checked=False, l=p_label: self._apply_preset(l))
            presets_flow.addWidget(p_btn)
            self._preset_buttons.append(p_btn)

        self._exact_lbl = QLabel("Frame-accurate · slower")
        self._exact_lbl.setFont(get_font(pixel_size=11, weight=QFont.Weight.Medium))
        self._exact_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        presets_flow.addWidget(self._exact_lbl)

        trim_layout.addWidget(presets_container)
        left_col.addWidget(self._trim_card)
        left_col.addStretch(1)

        # ─────────────────────────────────────────────────────────────────────
        # RIGHT COLUMN (Quality, Save, Actions)
        # ─────────────────────────────────────────────────────────────────────

        # ── 1. Info Card ─────────────────────────────────────────────────────
        self._info_card = QFrame()
        self._info_card.setObjectName("infoCard")
        self._info_card.setFixedWidth(RIGHT_COL_WIDTH)
        self._info_layout = QVBoxLayout(self._info_card)
        self._info_layout.setContentsMargins(16, 12, 16, 12)
        self._info_layout.setSpacing(6)

        self._status_badge = QLabel("● Ready to clip")
        self._status_badge.setFont(get_font(pixel_size=13, weight=QFont.Weight.DemiBold))
        self._status_badge.setStyleSheet(f"color: {TEXT_MUTED};")
        self._info_layout.addWidget(self._status_badge)

        self._video_title = QLabel("Paste a YouTube link above")
        self._video_title.setFont(get_font(pixel_size=16, weight=QFont.Weight.DemiBold))
        self._video_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        self._video_title.setWordWrap(True)
        self._info_layout.addWidget(self._video_title)

        self._video_meta = QLabel("Preview, metadata, and clipping options will appear here")
        self._video_meta.setFont(get_font(pixel_size=12))
        self._video_meta.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self._video_meta.setWordWrap(True)
        self._info_layout.addWidget(self._video_meta)

        # Inline Skeleton Container (for Fetching state)
        self._skeleton_box = QWidget()
        sk_layout = QVBoxLayout(self._skeleton_box)
        sk_layout.setContentsMargins(0, 4, 0, 4)
        sk_layout.setSpacing(8)

        sk_bar1 = QFrame()
        sk_bar1.setFixedHeight(18)
        sk_bar1.setStyleSheet("background-color: #222634; border-radius: 4px;")
        sk_layout.addWidget(sk_bar1)

        sk_bar2 = QFrame()
        sk_bar2.setFixedHeight(14)
        sk_bar2.setStyleSheet("background-color: #1C202C; border-radius: 4px;")
        sk_layout.addWidget(sk_bar2)

        self._skeleton_box.hide()
        self._info_layout.addWidget(self._skeleton_box)

        # Inline Retry Button (for Error state)
        self._retry_btn = QPushButton("Retry Fetch")
        self._retry_btn.setFixedHeight(32)
        self._retry_btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.DemiBold))
        self._retry_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 8px; padding: 0 14px; }}"
        )
        self._retry_btn.clicked.connect(self._on_fetch_clicked)
        self._retry_btn.hide()
        self._info_layout.addWidget(self._retry_btn)

        right_col.addWidget(self._info_card)

        # ── 2. Quality and Format Card ───────────────────────────────────────
        self._quality_card = QFrame()
        self._quality_card.setObjectName("qualityCard")
        self._quality_card.setFixedWidth(RIGHT_COL_WIDTH)
        q_layout = QVBoxLayout(self._quality_card)
        q_layout.setContentsMargins(16, 12, 16, 12)
        q_layout.setSpacing(10)

        self._q_title = QLabel("Quality and format")
        self._q_title.setFont(get_font(pixel_size=16, weight=QFont.Weight.DemiBold))
        self._q_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        q_layout.addWidget(self._q_title)

        self._mode_toggle = SegmentedToggle()
        self._mode_toggle.toggled.connect(self._on_mode_toggled)
        q_layout.addWidget(self._mode_toggle)

        self._quality_lbl = QLabel("Video quality")
        self._quality_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._quality_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        q_layout.addWidget(self._quality_lbl)

        self._quality_combo = QComboBox()
        self._quality_combo.setFixedHeight(36)
        self._quality_combo.setFont(get_font(pixel_size=13))
        self._quality_combo.addItem("Best available · 1080p")
        self._quality_combo.setEnabled(False)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        q_layout.addWidget(self._quality_combo)

        self._fmt_lbl = QLabel("Output format")
        self._fmt_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fmt_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        q_layout.addWidget(self._fmt_lbl)

        self._chips_row = QHBoxLayout()
        self._chips_row.setSpacing(8)
        self._format_chips: dict[str, ChipButton] = {}
        self._build_format_chips(["MP4", "MOV", "GIF"])
        q_layout.addLayout(self._chips_row)

        right_col.addWidget(self._quality_card)

        # ── 3. Save Card ─────────────────────────────────────────────────────
        self._save_card = QFrame()
        self._save_card.setObjectName("saveCard")
        self._save_card.setFixedWidth(RIGHT_COL_WIDTH)
        save_layout = QVBoxLayout(self._save_card)
        save_layout.setContentsMargins(16, 12, 16, 12)
        save_layout.setSpacing(8)

        self._save_title = QLabel("Save")
        self._save_title.setFont(get_font(pixel_size=16, weight=QFont.Weight.DemiBold))
        self._save_title.setStyleSheet(f"color: {TEXT_PRIMARY};")
        save_layout.addWidget(self._save_title)

        self._fn_lbl = QLabel("File name")
        self._fn_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fn_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        save_layout.addWidget(self._fn_lbl)

        self._fn_box = QFrame()
        self._fn_box.setObjectName("fnBox")
        self._fn_box.setFixedHeight(36)
        self._fn_box.setStyleSheet(
            f"QFrame#fnBox {{ background-color: {BG_INPUT}; border: 1px solid {BORDER_INPUT}; border-radius: 10px; }}"
        )
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
        save_layout.addWidget(self._fn_box)

        self._fd_lbl = QLabel("Folder")
        self._fd_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._fd_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        opt_layout = save_layout
        save_layout.addWidget(self._fd_lbl)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(6)

        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        if self._settings and getattr(self._settings, "last_output_folder", None):
            downloads_path = self._settings.last_output_folder

        self._folder_input = QLineEdit(downloads_path)
        self._folder_input.setFixedHeight(36)
        self._folder_input.setReadOnly(True)
        self._folder_input.setFont(get_font(pixel_size=12))
        self._folder_input.setToolTip(downloads_path)
        self._folder_input.setStyleSheet(
            f"QLineEdit {{ background-color: {BG_INPUT}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_INPUT}; border-radius: 10px; padding: 0 10px; }}"
        )
        folder_row.addWidget(self._folder_input, stretch=1)

        self._browse_btn = QPushButton("Browse")
        self._browse_btn.setFixedHeight(36)
        self._browse_btn.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._browse_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_PILL}; border-radius: 10px; padding: 0 12px; }}"
            f"QPushButton:hover {{ background-color: #262A37; }}"
        )
        self._browse_btn.clicked.connect(self._on_browse_folder)
        folder_row.addWidget(self._browse_btn)
        save_layout.addLayout(folder_row)

        right_col.addWidget(self._save_card)

        # ── 4. Bottom Pinned Action Area (Defect 10) ─────────────────────────
        right_col.addStretch(1)

        actions_box = QWidget()
        actions_box.setFixedWidth(RIGHT_COL_WIDTH)
        actions_box_layout = QVBoxLayout(actions_box)
        actions_box_layout.setContentsMargins(0, 0, 0, 0)
        actions_box_layout.setSpacing(8)

        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        self._summary_lbl = QLabel("")
        self._summary_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._summary_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        summary_row.addWidget(self._summary_lbl)
        summary_row.addStretch()

        self._size_lbl = QLabel("")
        self._size_lbl.setFont(get_font(pixel_size=12, weight=QFont.Weight.Medium))
        self._size_lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
        summary_row.addWidget(self._size_lbl)
        actions_box_layout.addLayout(summary_row)

        actions_row = QHBoxLayout()
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(10)

        self._queue_btn = QPushButton("Add to queue")
        self._queue_btn.setFixedHeight(44)
        self._queue_btn.setFont(get_font(pixel_size=14, weight=QFont.Weight.DemiBold))
        self._queue_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._queue_btn.setEnabled(False)
        self._queue_btn.setStyleSheet(
            f"QPushButton {{ background-color: {BG_CARD}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_CARD}; border-radius: 12px; }}"
            f"QPushButton:hover {{ background-color: #1F2330; }}"
            f"QPushButton:disabled {{ background-color: {BG_CARD}; color: {TEXT_MUTED}; border-color: {BORDER_DIVIDER}; }}"
        )
        self._queue_btn.clicked.connect(self._on_add_to_queue_clicked)
        actions_row.addWidget(self._queue_btn)

        self._download_btn = QPushButton("  Download clip")
        self._download_btn.setIcon(get_svg_icon("download", color="#FFFFFF", size=16))
        self._download_btn.setFixedHeight(44)
        self._download_btn.setFont(get_font(pixel_size=14, weight=QFont.Weight.DemiBold))
        self._download_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._download_btn.setEnabled(False)
        self._download_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: #FF6957; }}"
            f"QPushButton:disabled {{ background-color: #351C21; color: {TEXT_MUTED}; }}"
        )
        self._download_btn.clicked.connect(self._on_download_clicked)
        actions_row.addWidget(self._download_btn)

        actions_box_layout.addLayout(actions_row)
        self._actions_box = actions_box
        right_col.addWidget(self._actions_box)

        page_layout.addStretch(1)

        center_row.addWidget(self._content_box)
        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adjust_video_surface_height()
        self._update_text_elisions()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._adjust_video_surface_height()
        self._update_text_elisions()

    def _show_more_menu(self) -> None:
        """Show more options popup menu for narrow viewports."""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #14161D; color: #F3F4F7; border: 1px solid #232733; border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; }"
            "QMenu::item:selected { background-color: #2A1B19; color: #FF5B4A; }"
            "QMenu::separator { height: 1px; background-color: #232733; margin: 4px 0; }"
        )

        quality_menu = menu.addMenu(f"Quality ({self._quality_btn.text()})")
        for q_label in ("2160p (4K)", "1440p (2K)", "1080p", "720p", "480p", "360p", "240p", "144p"):
            act = quality_menu.addAction(q_label)
            act.setCheckable(True)
            if self._quality_btn.text() in q_label or q_label.startswith(self._quality_btn.text()):
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, q=q_label: self._controller.set_quality(q.split()[0]))

        speed_menu = menu.addMenu(f"Playback Speed ({self._speed_btn.text()})")
        for s_label, s_val in (
            ("0.25x", 0.25),
            ("0.5x", 0.5),
            ("0.75x", 0.75),
            ("1x (Normal)", 1.0),
            ("1.25x", 1.25),
            ("1.5x", 1.5),
            ("1.75x", 1.75),
            ("2x", 2.0),
        ):
            act = speed_menu.addAction(s_label)
            act.setCheckable(True)
            if self._speed_btn.text() == s_label or (self._speed_btn.text() == "1x" and "Normal" in s_label):
                act.setChecked(True)
            act.triggered.connect(lambda checked=False, val=s_val: self._controller.set_speed(val))

        cc_text = "Captions: Turn ON" if not self._controller.captions_enabled else "Captions: Turn OFF"
        cc_act = menu.addAction(cc_text)
        cc_act.triggered.connect(self._toggle_captions)

        menu.addSeparator()

        settings_act = menu.addAction("Playback Settings...")
        settings_act.triggered.connect(self._show_playback_settings)

        menu.exec(self._more_btn.mapToGlobal(QPoint(0, self._more_btn.height())))

    def set_breakpoint(self, bp: str, window_width: int) -> None:
        """Adapt YouTube clipper page layouts based on window width breakpoint."""
        if bp == getattr(self, "_current_bp", None):
            self._update_text_elisions()
            return
        self._current_bp = bp

        if bp == "narrow":
            # Remove from 2-col layouts
            self._left_col_layout.removeWidget(self._preview_card)
            self._left_col_layout.removeWidget(self._trim_card)
            self._right_col_layout.removeWidget(self._info_card)
            self._right_col_layout.removeWidget(self._quality_card)
            self._right_col_layout.removeWidget(self._save_card)
            self._right_col_layout.removeWidget(self._actions_box)

            # Add to 1-col in exact requested order:
            # Info -> Preview -> Trim -> Quality -> Save -> Actions
            self._one_col_layout.addWidget(self._info_card)
            self._one_col_layout.addWidget(self._preview_card)
            self._one_col_layout.addWidget(self._trim_card)
            self._one_col_layout.addWidget(self._quality_card)
            self._one_col_layout.addWidget(self._save_card)
            self._one_col_layout.addWidget(self._actions_box)

            for card in (self._info_card, self._quality_card, self._save_card, self._actions_box):
                card.setMinimumWidth(0)
                card.setMaximumWidth(16777215)

            # Adapt player controls
            self._more_btn.show()
            self._quality_btn.hide()
            self._speed_btn.hide()
            self._cc_btn.hide()
            self._playback_settings_btn.hide()
            self._volume_slider.hide()
            self._volume_lbl.hide()
            self._rewind_btn.hide()
            self._forward_btn.hide()

            # Stack start / end cards vertically
            self._set_tc_cards_vertical(True)

            self._workspace_stack.setCurrentIndex(1)
        else:
            # Remove from 1-col layout
            self._one_col_layout.removeWidget(self._info_card)
            self._one_col_layout.removeWidget(self._preview_card)
            self._one_col_layout.removeWidget(self._trim_card)
            self._one_col_layout.removeWidget(self._quality_card)
            self._one_col_layout.removeWidget(self._save_card)
            self._one_col_layout.removeWidget(self._actions_box)

            self._left_col_layout.insertWidget(0, self._preview_card)
            self._left_col_layout.insertWidget(1, self._trim_card)

            self._right_col_layout.insertWidget(0, self._info_card)
            self._right_col_layout.insertWidget(1, self._quality_card)
            self._right_col_layout.insertWidget(2, self._save_card)
            self._right_col_layout.addWidget(self._actions_box)

            # Set widths for right column
            for card in (self._info_card, self._quality_card, self._save_card, self._actions_box):
                if bp == "wide":
                    card.setMinimumWidth(320)
                    card.setMaximumWidth(400)
                else:
                    card.setFixedWidth(320)
            if bp == "wide":
                self._right_col_widget.setMinimumWidth(320)
                self._right_col_widget.setMaximumWidth(400)
            else:
                self._right_col_widget.setFixedWidth(320)

            # Adapt player controls
            self._more_btn.hide()
            self._quality_btn.show()
            self._speed_btn.show()
            self._cc_btn.show()
            self._playback_settings_btn.show()
            self._volume_slider.show()
            self._volume_lbl.show()
            self._rewind_btn.show()
            self._forward_btn.show()

            # Side-by-side start / end cards
            self._set_tc_cards_vertical(False)

            self._workspace_stack.setCurrentIndex(0)

        self._update_text_elisions()
        self._adjust_video_surface_height()

    def _set_tc_cards_vertical(self, vertical: bool) -> None:
        """Switch START / END time cards between horizontal and vertical stacking."""
        if getattr(self, "_tc_is_vertical", None) == vertical:
            return
        self._tc_is_vertical = vertical

        self._tc_layout.removeWidget(self._start_card)
        self._tc_layout.removeWidget(self._arrow_lbl)
        self._tc_layout.removeWidget(self._end_card)
        QWidget().setLayout(self._tc_layout)

        if vertical:
            new_layout = QVBoxLayout(self._tc_container)
            new_layout.setContentsMargins(0, 0, 0, 0)
            new_layout.setSpacing(8)
            new_layout.addWidget(self._start_card)
            self._arrow_lbl.hide()
            new_layout.addWidget(self._end_card)
        else:
            new_layout = QHBoxLayout(self._tc_container)
            new_layout.setContentsMargins(0, 0, 0, 0)
            new_layout.setSpacing(10)
            new_layout.addWidget(self._start_card, stretch=1)
            self._arrow_lbl.show()
            new_layout.addWidget(self._arrow_lbl)
            new_layout.addWidget(self._end_card, stretch=1)
        self._tc_layout = new_layout

    def _update_text_elisions(self) -> None:
        """Update video title and folder path with proper ellipsis truncation and tooltips."""
        if hasattr(self, "_video_title") and hasattr(self, "_full_video_title") and self._full_video_title:
            self._video_title.setToolTip(self._full_video_title)
            fm = self._video_title.fontMetrics()
            w = max(180, self._video_title.width())
            max_two_lines_w = (w - 10) * 2
            if fm.horizontalAdvance(self._full_video_title) <= max_two_lines_w:
                self._video_title.setText(self._full_video_title)
            else:
                elided = fm.elidedText(self._full_video_title, Qt.TextElideMode.ElideRight, max_two_lines_w)
                self._video_title.setText(elided)

        if hasattr(self, "_folder_input") and hasattr(self, "_full_folder_path") and self._full_folder_path:
            self._folder_input.setToolTip(self._full_folder_path)
            fm = self._folder_input.fontMetrics()
            w = max(120, self._folder_input.width() - 20)
            elided = fm.elidedText(self._full_folder_path, Qt.TextElideMode.ElideMiddle, w)
            self._folder_input.setText(elided)

    def _adjust_video_surface_height(self) -> None:
        """Dynamically maintain exact 16:9 aspect ratio without vertical stretching or pillarboxing."""
        if hasattr(self, "_preview_card") and hasattr(self, "_video_surface"):
            card_w = self._preview_card.width()
            if card_w > 100:
                # 18px padding on each side of preview card = 36px
                surf_w = max(100, card_w - 36)
                target_h = int(round(surf_w * 9.0 / 16.0))
                clamped_h = min(max(target_h, 200), 540)
                if self._video_surface.height() != clamped_h:
                    self._video_surface.setFixedHeight(clamped_h)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FORMAT & CHIP HELPERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _build_format_chips(self, formats: list[str]) -> None:
        while self._chips_row.count():
            item = self._chips_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._format_chips.clear()

        for fmt in formats:
            chip = ChipButton(fmt)
            chip.clicked.connect(lambda checked=False, f=fmt: self._select_format(f))
            self._chips_row.addWidget(chip)
            self._format_chips[fmt] = chip

        if formats:
            self._select_format(formats[0])

    def _select_format(self, fmt: str) -> None:
        self._selected_format = fmt
        if hasattr(self, "_ext_lbl"):
            self._ext_lbl.setText(f".{fmt.lower()}")
        for name, chip in self._format_chips.items():
            chip.set_active(name == fmt)
        self._update_summary()

    def _on_mode_toggled(self, mode: str) -> None:
        self._is_audio_only = (mode == "audio")
        if self._is_audio_only:
            self._quality_lbl.setText("Audio quality")
            self._quality_combo.clear()
            if self._video_info and self._video_info.audio_formats:
                for af in self._video_info.audio_formats:
                    self._quality_combo.addItem(af.display_label(), af)
            else:
                self._quality_combo.addItems(["Best available · 320 kbps", "256 kbps", "192 kbps", "128 kbps"])
            self._build_format_chips(["MP3", "M4A", "WAV"])
        else:
            self._quality_lbl.setText("Video quality")
            self._quality_combo.clear()
            if self._video_info and self._video_info.video_formats:
                for vf in self._video_info.video_formats:
                    self._quality_combo.addItem(vf.display_label(), vf)
            else:
                self._quality_combo.addItems(["Best available · 1080p", "1080p · 60fps", "720p · 30fps", "480p", "360p"])
            self._build_format_chips(["MP4", "MOV", "GIF"])

    def _on_quality_changed(self, idx: int) -> None:
        self._update_summary()

    def _update_summary(self) -> None:
        if not hasattr(self, "_summary_lbl") or not hasattr(self, "_size_lbl"):
            return
        if not self._video_info or self._video_info.duration <= 0:
            self._summary_lbl.setText("")
            self._size_lbl.setText("")
            return

        dur = self._end_seconds - self._start_seconds if not self._is_full_video else self._video_info.duration
        dur_str = _format_time(dur)
        fmt = self._selected_format
        prefix = "Full video" if self._is_full_video else "Fetching"
        self._summary_lbl.setText(f"{prefix} {dur_str} · {fmt}")

        if hasattr(self, "_download_btn"):
            if self._is_full_video:
                self._download_btn.setText("  Download full video")
            else:
                self._download_btn.setText("  Download clip")

        # Size estimation
        if fmt == "WAV":
            # WAV 16-bit 44.1kHz stereo is ~176 KB/s
            approx_mb = int((dur * 176) / 1024)
            self._size_lbl.setText(f"≈ {approx_mb} MB")
        elif self._video_info.duration > 0:
            fraction = dur / self._video_info.duration
            approx_size = 0
            data = self._quality_combo.currentData()
            filesize = getattr(data, "filesize_approx", None) or getattr(data, "filesize", None)
            if filesize:
                approx_size = int(filesize * fraction / (1024 * 1024))
            if approx_size > 0:
                self._size_lbl.setText(f"≈ {approx_size} MB")
            else:
                self._size_lbl.setText("")
        else:
            self._size_lbl.setText("")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FULL VIDEO TOGGLE & NUDGES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _toggle_full_video(self) -> None:
        self.set_full_video_mode(not self._is_full_video)

    def set_full_video_mode(self, enabled: bool) -> None:
        self._is_full_video = enabled
        if self._video_info and self._video_info.duration > 0:
            total_dur = _format_time(self._video_info.duration)
        else:
            total_dur = "00:00"

        if self._is_full_video:
            self._saved_range = (self._start_seconds, self._end_seconds)
            self._full_video_btn.show()
            self._full_video_btn.setStyleSheet(
                f"QPushButton {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; border: none; border-radius: 14px; padding: 0 12px; }}"
            )
            self._full_video_btn.setIcon(get_svg_icon("check", color="#FFFFFF", size=12))
            self._full_video_btn.setText("  Full video")
            self._duration_badge.setText(f"Full video · {total_dur}")
            self._duration_badge.setStyleSheet("background-color: rgba(255, 92, 77, 0.18); color: #FF8577; border-radius: 14px; padding: 4px 12px;")
            self._download_btn.setText("  Download full video")
            self._trim_sub.setText("Clipping is off. The whole video will be downloaded.")
            self._trim_sub.setStyleSheet(f"color: {COLOR_ACCENT}; font-weight: 500;")

            # Dim timeline & controls
            self._timeline_track.set_dimmed(True)
            self._start_card.setEnabled(False)
            self._end_card.setEnabled(False)
            for btn in self._preset_buttons:
                btn.setEnabled(False)
        else:
            self._full_video_btn.show()
            self._full_video_btn.setStyleSheet(
                f"QPushButton {{ background-color: {BG_PILL}; color: {TEXT_SECONDARY}; border: 1px solid {BORDER_PILL}; border-radius: 14px; padding: 0 12px; }}"
                f"QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.1); color: #FFFFFF; }}"
            )
            self._full_video_btn.setIcon(get_svg_icon("check", color=TEXT_MUTED, size=12))
            self._full_video_btn.setText("  Full video")
            self._duration_badge.setStyleSheet(f"background-color: rgba(255, 92, 77, 0.12); color: {COLOR_ACCENT}; border-radius: 14px; padding: 4px 12px;")
            self._trim_sub.setText("Drag the handles as you watch. Only the selected part is downloaded.")
            self._trim_sub.setStyleSheet(f"color: {TEXT_SECONDARY};")
            self._download_btn.setText("  Download clip")

            self._timeline_track.set_dimmed(False)
            self._start_card.setEnabled(True)
            self._end_card.setEnabled(True)
            for btn in self._preset_buttons:
                btn.setEnabled(True)

            # Restore previous range
            if self._saved_range != (0, 0):
                self._start_seconds, self._end_seconds = self._saved_range
            self._start_tc.setText(_format_time(self._start_seconds))
            self._end_tc.setText(_format_time(self._end_seconds))
            clip_dur = _format_time(self._end_seconds - self._start_seconds)
            self._duration_badge.setText(f"Clip {clip_dur} of {total_dur}")

        self._update_summary()

    def _nudge_start(self, delta: int) -> None:
        if not self._video_info:
            return
        new_start = max(0, min(self._start_seconds + delta, self._end_seconds - 1))
        self._start_seconds = new_start
        self._start_tc.setText(_format_time(new_start))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_summary()

    def _nudge_end(self, delta: int) -> None:
        if not self._video_info:
            return
        new_end = max(self._start_seconds + 1, min(self._end_seconds + delta, self._video_info.duration))
        self._end_seconds = new_end
        self._end_tc.setText(_format_time(new_end))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_summary()

    def _apply_preset(self, preset_name: str) -> None:
        if not self._video_info or self._video_info.duration <= 0:
            return
        dur = self._video_info.duration

        if preset_name == "First 30s":
            self._start_seconds = 0
            self._end_seconds = min(30, dur)
        elif preset_name == "First 60s":
            self._start_seconds = 0
            self._end_seconds = min(60, dur)
        elif preset_name == "Middle 50%":
            q1 = int(dur * 0.25)
            q3 = int(dur * 0.75)
            self._start_seconds = q1
            self._end_seconds = q3
        elif preset_name == "Last 30s":
            self._start_seconds = max(0, dur - 30)
            self._end_seconds = dur
        elif preset_name == "Reset":
            self._start_seconds = 0
            self._end_seconds = dur

        self._start_tc.setText(_format_time(self._start_seconds))
        self._end_tc.setText(_format_time(self._end_seconds))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        clip_dur = _format_time(self._end_seconds - self._start_seconds)
        self._duration_badge.setText(f"Clip {clip_dur} of {_format_time(dur)}")
        self._update_summary()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBACK CONTROLLER & SYNCHRONISATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _on_controller_play_state_changed(self, is_playing: bool) -> None:
        self._is_playing = is_playing
        icon_name = "pause" if is_playing else "play"
        self._play_btn.setIcon(get_svg_icon(icon_name, color="#FFFFFF", size=16))
        self._big_play_btn.setIcon(get_svg_icon(icon_name, color="#FFFFFF", size=22))
        if is_playing:
            self._big_play_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: rgba(255, 92, 77, 0.4);"
                "  border: 1px solid rgba(255, 92, 77, 0.6);"
                "  border-radius: 27px;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(255, 92, 77, 0.7);"
                "}"
            )
            self._playback_timer.start(250)
        else:
            self._big_play_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: rgba(255, 255, 255, 0.12);"
                "  border: 1px solid rgba(255, 255, 255, 0.2);"
                "  border-radius: 27px;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(255, 85, 66, 0.8);"
                "}"
            )
            self._playback_timer.stop()

    def _on_controller_playhead_changed(self, seconds: float) -> None:
        self._playhead_seconds = seconds
        sec_int = int(seconds)
        self._timeline_track.set_playhead(sec_int)
        dur = float(self._video_info.duration) if self._video_info else 0.0
        if dur > 0:
            pct = int((seconds / dur) * 1000)
            self._scrubber_slider.blockSignals(True)
            self._scrubber_slider.setValue(pct)
            self._scrubber_slider.blockSignals(False)
            self._timecode_lbl.setText(f"{_format_time(sec_int)} / {_format_time(int(dur))}")
        else:
            self._scrubber_slider.setValue(0)
            self._timecode_lbl.setText("00:00 / 00:00")

    def _on_controller_volume_changed(self, volume: int, is_muted: bool) -> None:
        self._is_muted = is_muted
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(volume)
        self._volume_slider.blockSignals(False)
        self._volume_lbl.setText(f"{volume}%")
        icon_name = "volume-x" if (is_muted or volume == 0) else "volume"
        self._volume_btn.setIcon(get_svg_icon(icon_name, color=TEXT_SECONDARY, size=16))

    def _on_controller_speed_changed(self, speed: float) -> None:
        self._speed_btn.setText(f"{speed:g}x")

    def _on_controller_quality_changed(self, quality: str) -> None:
        self._quality_btn.setText(quality)

    def _on_controller_captions_changed(self, enabled: bool) -> None:
        self._cc_btn.setText("CC ON" if enabled else "CC OFF")
        if enabled:
            self._cc_btn.setStyleSheet(
                f"QPushButton {{ background-color: rgba(255, 92, 77, 0.2); color: {COLOR_ACCENT}; border: 1px solid {COLOR_ACCENT}; border-radius: 14px; padding: 0 10px; }}"
            )
        else:
            self._cc_btn.setStyleSheet(
                "QPushButton { background-color: rgba(255, 255, 255, 0.08); color: rgba(255, 255, 255, 0.7); border: 1px solid rgba(255, 255, 255, 0.15); border-radius: 14px; padding: 0 10px; }"
                "QPushButton:hover { background-color: rgba(255, 255, 255, 0.16); }"
            )

    def _on_controller_in_point_changed(self, in_sec: float) -> None:
        self._start_seconds = int(in_sec)
        self._start_tc.setText(_format_time(self._start_seconds))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_duration_badge()
        self._update_summary()

    def _on_controller_out_point_changed(self, out_sec: float) -> None:
        self._end_seconds = int(out_sec)
        self._end_tc.setText(_format_time(self._end_seconds))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_duration_badge()
        self._update_summary()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBACK ENGINE & INTERACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _toggle_play_pause(self) -> None:
        if not self._video_info:
            return
        self._controller.toggle_play()

    def _start_playback(self) -> None:
        if not self._video_info or self._video_info.duration <= 0:
            return
        self._controller.play()

    def _pause_playback(self) -> None:
        self._controller.pause()

    def _on_playback_tick(self) -> None:
        if not self._video_info or not self._is_playing:
            self._pause_playback()
            return

        dur = float(self._video_info.duration)
        step = 0.25 * getattr(self._controller, "speed", 1.0)
        self._playhead_seconds += step

        if self._playhead_mode == "selection":
            if self._playhead_seconds >= float(self._end_seconds):
                self._playhead_seconds = float(self._start_seconds)
                self._pause_playback()
                self._controller.seek(self._playhead_seconds)
                self._playhead_mode = "full"
        else:
            if dur > 0 and self._playhead_seconds >= dur:
                self._playhead_seconds = 0.0
                self._pause_playback()
                self._controller.seek(0.0)

        sec_int = int(self._playhead_seconds)
        self._timeline_track.set_playhead(sec_int)
        if dur > 0:
            pct = int((self._playhead_seconds / dur) * 1000)
            self._scrubber_slider.blockSignals(True)
            self._scrubber_slider.setValue(pct)
            self._scrubber_slider.blockSignals(False)
        self._timecode_lbl.setText(f"{_format_time(sec_int)} / {_format_time(int(dur))}")
        self._controller.update_playhead_from_adapter(self._playhead_seconds)

        def _sync_time(cur_time):
            if cur_time is not None and cur_time > 0 and self._is_playing and self._video_info:
                self._playhead_seconds = float(cur_time)
                s_int = int(self._playhead_seconds)
                self._timeline_track.set_playhead(s_int)
                if dur > 0:
                    pct = int((self._playhead_seconds / dur) * 1000)
                    self._scrubber_slider.blockSignals(True)
                    self._scrubber_slider.setValue(pct)
                    self._scrubber_slider.blockSignals(False)
                self._timecode_lbl.setText(f"{_format_time(s_int)} / {_format_time(int(dur))}")
                self._controller.update_playhead_from_adapter(self._playhead_seconds)

        if hasattr(self._video_surface, "_is_web_ready") and self._video_surface._is_web_ready:
            self._video_surface.get_time(_sync_time)

    def _on_rewind_clicked(self) -> None:
        if not self._video_info:
            return
        self._controller.step_backward(10.0)

    def _on_forward_clicked(self) -> None:
        if not self._video_info:
            return
        self._controller.step_forward(10.0)

    def _play_selection_clicked(self) -> None:
        if not self._video_info:
            return
        self._playhead_mode = "selection"
        self._playhead_seconds = float(self._start_seconds)
        self._controller.seek(self._playhead_seconds)
        self._controller.play()

    def _toggle_volume(self) -> None:
        self._controller.toggle_mute()

    def _on_volume_slider_changed(self, value: int) -> None:
        self._controller.set_volume(value)

    def _on_scrubber_moved(self, value: int) -> None:
        if self._video_info and self._video_info.duration > 0:
            target_sec = (value / 1000.0) * float(self._video_info.duration)
            self._controller.seek(target_sec)

    def _show_quality_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 8px; padding: 4px; }}"
            f"QMenu::item {{ color: {TEXT_PRIMARY}; padding: 6px 16px; border-radius: 4px; font-family: Inter, Segoe UI, sans-serif; }}"
            f"QMenu::item:selected {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; }}"
        )
        qualities = ["2160p (4K)", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p", "Auto"]
        for q in qualities:
            action = menu.addAction(q)
            action.triggered.connect(lambda checked=False, val=q: self._controller.set_quality(val))
        menu.exec(self._quality_btn.mapToGlobal(self._quality_btn.rect().bottomLeft()))

    def _show_speed_menu(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 8px; padding: 4px; }}"
            f"QMenu::item {{ color: {TEXT_PRIMARY}; padding: 6px 16px; border-radius: 4px; font-family: Inter, Segoe UI, sans-serif; }}"
            f"QMenu::item:selected {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; }}"
        )
        speeds = [
            ("0.25x", 0.25),
            ("0.5x", 0.5),
            ("0.75x", 0.75),
            ("1x (Normal)", 1.0),
            ("1.25x", 1.25),
            ("1.5x", 1.5),
            ("1.75x", 1.75),
            ("2x", 2.0),
        ]
        for label, val in speeds:
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False, rate=val: self._controller.set_speed(rate))
        menu.exec(self._speed_btn.mapToGlobal(self._speed_btn.rect().bottomLeft()))

    def _toggle_captions(self) -> None:
        # Strictly OFF by default. User explicitly clicks to toggle.
        new_state = not self._controller.captions_enabled
        self._controller.set_captions(new_state)

    def _show_playback_settings(self) -> None:
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background-color: {BG_CARD}; border: 1px solid {BORDER_CARD_SUBTLE}; border-radius: 8px; padding: 4px; }}"
            f"QMenu::item {{ color: {TEXT_PRIMARY}; padding: 6px 16px; border-radius: 4px; font-family: Inter, Segoe UI, sans-serif; }}"
            f"QMenu::item:selected {{ background-color: {COLOR_ACCENT}; color: #FFFFFF; }}"
        )
        a_reset = menu.addAction("Reset to Start (00:00)")
        a_reset.triggered.connect(lambda: self._controller.seek(0.0))
        a_in = menu.addAction("Set In Point (I)")
        a_in.triggered.connect(self._on_shortcut_in_point)
        a_out = menu.addAction("Set Out Point (O)")
        a_out.triggered.connect(self._on_shortcut_out_point)
        menu.exec(self._playback_settings_btn.mapToGlobal(self._playback_settings_btn.rect().bottomLeft()))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # IN / OUT SHORTCUTS & CLIPPING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _on_shortcut_in_point(self) -> None:
        if not self._video_info:
            return
        cur_ph = self._playhead_seconds
        new_in = self._controller.set_in_point(cur_ph)
        self._start_seconds = int(new_in)
        self._start_tc.setText(_format_time(self._start_seconds))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_duration_badge()
        self._update_summary()
        logger.info("Shortcut 'I': In Point set to %s (%.1fs)", _format_time(self._start_seconds), new_in)

    def _on_shortcut_out_point(self) -> None:
        if not self._video_info:
            return
        cur_ph = self._playhead_seconds
        new_out = self._controller.set_out_point(cur_ph)
        self._end_seconds = int(new_out)
        self._end_tc.setText(_format_time(self._end_seconds))
        self._timeline_track.set_range(self._start_seconds, self._end_seconds)
        self._update_duration_badge()
        self._update_summary()
        logger.info("Shortcut 'O': Out Point set to %s (%.1fs)", _format_time(self._end_seconds), new_out)

    def _use_playhead_as_start(self) -> None:
        self._on_shortcut_in_point()

    def _use_playhead_as_end(self) -> None:
        self._on_shortcut_out_point()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_I:
            self._on_shortcut_in_point()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_O:
            self._on_shortcut_out_point()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Space:
            self._toggle_play_pause()
            event.accept()
            return

        super().keyPressEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress:
            key_event = cast(QKeyEvent, event)
            focus = QApplication.focusWidget()
            if not isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                if key_event.key() == Qt.Key.Key_I:
                    self._on_shortcut_in_point()
                    return True
                elif key_event.key() == Qt.Key.Key_O:
                    self._on_shortcut_out_point()
                    return True
                elif key_event.key() == Qt.Key.Key_Space:
                    self._toggle_play_pause()
                    return True
        return super().eventFilter(watched, event)

    def _on_timeline_range_changed(self, start_sec: int, end_sec: int) -> None:
        if self._is_full_video:
            self.set_full_video_mode(False)
        self._start_seconds = start_sec
        self._end_seconds = end_sec
        self._start_tc.setText(_format_time(start_sec))
        self._end_tc.setText(_format_time(end_sec))
        self._update_duration_badge()
        self._update_summary()

    def _on_timeline_playhead_changed(self, playhead_sec: int) -> None:
        self._playhead_seconds = float(playhead_sec)
        self._video_surface.seek(self._playhead_seconds)
        total = self._video_info.duration if self._video_info else 0
        self._timecode_lbl.setText(f"{_format_time(playhead_sec)} / {_format_time(total)}")

    def _update_duration_badge(self) -> None:
        if not self._video_info:
            return
        total_dur = _format_time(self._video_info.duration)
        if self._is_full_video:
            self._duration_badge.setText(f"Full video · {total_dur}")
        else:
            clip_dur = _format_time(self._end_seconds - self._start_seconds)
            self._duration_badge.setText(f"Clip {clip_dur} of {total_dur}")

    def set_thumbnail_preview(self, pixmap: QPixmap) -> None:
        """Apply video thumbnail to video surface canvas."""
        self._video_surface.set_pixmap(pixmap)

    def _load_thumbnail(self, video_info: VideoInfo) -> None:
        if not video_info.thumbnail_url:
            return
        cache_dir = getattr(self._settings, "thumbnail_cache_dir", None) if self._settings else None
        if not cache_dir:
            cache_dir = os.path.join(os.path.expanduser("~"), ".youcut", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        url_hash = hashlib.md5(video_info.url.encode()).hexdigest()[:12]
        cache_path = os.path.join(cache_dir, f"{url_hash}.jpg")

        if os.path.isfile(cache_path):
            pix = QPixmap(cache_path)
            if not pix.isNull():
                self.set_thumbnail_preview(pix)
                return

        def _fetch():
            try:
                resp = requests.get(video_info.thumbnail_url, timeout=10)
                if resp.status_code == 200:
                    with open(cache_path, "wb") as f:
                        f.write(resp.content)
                    pix = QPixmap(cache_path)
                    if not pix.isNull():
                        QTimer.singleShot(0, lambda: self.set_thumbnail_preview(pix))
            except Exception as e:
                logger.debug("Failed to fetch thumbnail: %s", e)

        import threading
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATES MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _update_info_card_style(self) -> None:
        app = QApplication.instance()
        is_dark = (app.property("current_theme") != "light") if app else True
        card_bg = BG_CARD if is_dark else "#FFFFFF"
        card_border = BORDER_CARD if is_dark else "#DCE0E8"
        self._info_card.setStyleSheet(
            f"QFrame#infoCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 20px; }}"
        )

    def set_empty_state(self) -> None:
        """Render YouTube page in the clean Empty state (Defect 5)."""
        self._pause_playback()
        self._playhead_seconds = 0.0
        self._video_info = None
        self._url_input.clear()
        self._platform_chip.hide()
        self._valid_check.hide()
        self._wrong_platform_banner.hide()
        self._skeleton_box.hide()
        self._retry_btn.hide()

        app = QApplication.instance()
        is_dark = (app.property("current_theme") != "light") if app else True
        title_col = TEXT_PRIMARY if is_dark else "#111318"

        # Info card
        self._update_info_card_style()
        self._status_badge.setText("● Ready to clip")
        self._status_badge.setStyleSheet(f"color: {TEXT_MUTED};")
        self._video_title.setText("Paste a YouTube link above")
        self._video_title.setStyleSheet(f"color: {title_col};")
        self._full_video_title = "Paste a YouTube link above"
        self._update_text_elisions()
        self._video_meta.setText("Preview, metadata, and clipping options will appear here")

        # Preview card
        self._res_chip.hide()
        self._big_play_btn.hide()
        self._video_surface.clear()
        self._preview_hint.show()
        self._preview_hint.setText("Paste a YouTube link above")
        self._timecode_lbl.setText("00:00 / 00:00")
        if hasattr(self, "_scrubber_slider"):
            self._scrubber_slider.setValue(0)
        if hasattr(self, "_cc_btn"):
            self._cc_btn.setText("CC OFF")
        if hasattr(self, "_speed_btn"):
            self._speed_btn.setText("1x")
        if hasattr(self, "_quality_btn"):
            self._quality_btn.setText("1080p")
        if hasattr(self, "_volume_slider"):
            self._volume_slider.setValue(70)
        if hasattr(self, "_volume_lbl"):
            self._volume_lbl.setText("70%")

        # Trim card
        self._duration_badge.hide()
        self._full_video_btn.setEnabled(False)
        self._timeline_track.set_duration(0)
        self._ruler.set_duration(0)
        self._start_tc.setText("00:00")
        self._end_tc.setText("00:00")
        self._start_card.setEnabled(False)
        self._end_card.setEnabled(False)
        for btn in self._preset_buttons:
            btn.setEnabled(False)

        # Quality & format
        self._quality_combo.clear()
        self._quality_combo.addItem("Best available · 1080p")
        self._quality_combo.setEnabled(False)

        # Save & Actions
        self._filename_input.clear()
        self._filename_input.setPlaceholderText("Auto-generated from title")
        self._summary_lbl.setText("")
        self._size_lbl.setText("")
        self._queue_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

    def set_fetching_state(self) -> None:
        """Render YouTube page in Fetching state with skeleton and spinner text."""
        self._pause_playback()
        self._playhead_seconds = 0.0
        self._video_info = None
        self._video_surface.clear()
        self._fetch_btn.setText("Fetching...")
        self._fetch_btn.setEnabled(False)
        self._status_badge.setText("● Fetching info...")
        self._status_badge.setStyleSheet(f"color: {COLOR_WARNING};")
        self._video_title.setText("Retrieving video details...")
        self._full_video_title = "Retrieving video details..."
        self._update_text_elisions()
        self._video_meta.setText("Extracting available qualities, formats, and chapters...")
        self._skeleton_box.show()
        self._retry_btn.hide()
        self._wrong_platform_banner.hide()
        self._res_chip.hide()
        self._duration_badge.hide()
        self._queue_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._full_video_btn.setEnabled(False)
        self.status_pill_update.emit("Fetching info...", "fetching")
        self.status_message.emit("Fetching YouTube video info...")

    def set_loaded_state(self, video_info: VideoInfo) -> None:
        """Render YouTube page in Loaded state with real video metadata."""
        self._pause_playback()
        self._playhead_seconds = 0.0
        self._video_info = video_info
        self._skeleton_box.hide()
        self._retry_btn.hide()
        self._wrong_platform_banner.hide()
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

        dur_str = _format_time(video_info.duration)
        logger.info("[FETCH] UI populated into Loaded state successfully: '%s' (%s)", video_info.title, dur_str)

        # Info card
        self._update_info_card_style()
        self._status_badge.setText(f"● Video found · {dur_str}")
        self._status_badge.setStyleSheet(f"color: {COLOR_SUCCESS};")
        self._video_title.setText(video_info.title)
        app = QApplication.instance()
        is_dark = (app.property("current_theme") != "light") if app else True
        title_col = TEXT_PRIMARY if is_dark else "#111318"
        self._video_title.setStyleSheet(f"color: {title_col};")
        self._full_video_title = video_info.title
        self._update_text_elisions()

        meta_parts = []
        if getattr(video_info, "uploader", None):
            meta_parts.append(video_info.uploader)
        views = getattr(video_info, "view_count", None) or (
            video_info.raw_info.get("view_count")
            if hasattr(video_info, "raw_info") and isinstance(video_info.raw_info, dict)
            else None
        )
        if views:
            meta_parts.append(_format_views(views))
        if getattr(video_info, "upload_date", None):
            meta_parts.append(video_info.upload_date)
        self._video_meta.setText(" · ".join(meta_parts) if meta_parts else "YouTube video")

        # Preview card
        self._res_chip.setText("1080p60" if video_info.video_formats else "Audio")
        self._res_chip.show()
        self._big_play_btn.show()
        self._preview_hint.hide()
        self._timecode_lbl.setText(f"00:00 / {dur_str}")

        # Extract YouTube video id and preload into video surface and central controller
        vid_id = ""
        m = re.search(r"(?:v=|\/|embed\/|shorts\/)([0-9A-Za-z_-]{11})", video_info.url)
        if m:
            vid_id = m.group(1)
        elif hasattr(video_info, "raw_info") and isinstance(video_info.raw_info, dict):
            vid_id = video_info.raw_info.get("id", "")
        if vid_id:
            self._video_surface.load_video(vid_id)
            self._controller.load_video(
                video_id=vid_id,
                duration=float(video_info.duration),
                url=video_info.url,
                title=video_info.title,
            )
            if hasattr(self, "_cc_btn"):
                self._cc_btn.setText("CC OFF")
            if hasattr(self, "_speed_btn"):
                self._speed_btn.setText("1x")
            if hasattr(self, "_quality_btn"):
                self._quality_btn.setText("1080p")
            if hasattr(self, "_scrubber_slider"):
                self._scrubber_slider.setValue(0)

        if video_info.thumbnail_url:
            self._load_thumbnail(video_info)

        # Trim card
        self._start_seconds = 0
        self._end_seconds = video_info.duration
        self._timeline_track.set_duration(video_info.duration)
        self._timeline_track.set_playhead(0)
        self._ruler.set_duration(video_info.duration)
        self._start_tc.setText("00:00")
        self._end_tc.setText(dur_str)
        self._start_card.setEnabled(True)
        self._end_card.setEnabled(True)
        self._full_video_btn.setEnabled(True)
        self.set_full_video_mode(False)
        for btn in self._preset_buttons:
            btn.setEnabled(True)

        # Format combo
        self._quality_combo.setEnabled(True)
        self._on_mode_toggled("audio" if self._is_audio_only else "video")

        # Save card
        suggested = suggest_filename(video_info.title)
        self._filename_input.setText(suggested)

        # Actions
        self._queue_btn.setEnabled(True)
        self._download_btn.setEnabled(True)
        self._update_summary()

        self.status_pill_update.emit("Ready to clip", "ready")
        self.status_message.emit(f"Fetched: {video_info.title}")

    def set_error_state(self, error_msg: str) -> None:
        """Render YouTube page in Error state (red styling ONLY on error)."""
        self._pause_playback()
        self._playhead_seconds = 0.0
        self._video_info = None
        self._video_surface.clear()
        self._skeleton_box.hide()
        self._fetch_btn.setText("Fetch info")
        self._fetch_btn.setEnabled(True)

        self._info_card.setStyleSheet(
            f"QFrame#infoCard {{ background-color: {COLOR_ERROR_BG}; border: 1px solid {COLOR_ERROR}; border-radius: 20px; }}"
        )
        self._status_badge.setText("● Fetch failed")
        self._status_badge.setStyleSheet(f"color: {COLOR_ERROR};")
        self._video_title.setText("Could not fetch video")
        self._video_title.setStyleSheet(f"color: {COLOR_ERROR};")
        self._full_video_title = "Could not fetch video"
        self._update_text_elisions()
        self._video_meta.setText(error_msg[:180])
        self._retry_btn.show()

        self._res_chip.hide()
        self._duration_badge.hide()
        self._preview_hint.show()
        self._preview_hint.setText("Paste a YouTube link above")
        self._timecode_lbl.setText("00:00 / 00:00")
        self._start_tc.setText("00:00")
        self._end_tc.setText("00:00")
        self._start_card.setEnabled(False)
        self._end_card.setEnabled(False)
        for btn in self._preset_buttons:
            btn.setEnabled(False)
        self._queue_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._full_video_btn.setEnabled(False)
        self._update_summary()

        self.status_pill_update.emit("Fetch failed", "error")
        self.status_message.emit("Error fetching video info")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FETCH WORKER PIPELINE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_url(self, url: str, auto_fetch: bool = True) -> None:
        self._url_input.setText(url)
        if auto_fetch:
            self._on_fetch_clicked()

    def _on_url_text_changed(self, text: str) -> None:
        url = text.strip()
        if not url:
            self._platform_chip.hide()
            self._valid_check.hide()
            self._wrong_platform_banner.hide()
            return

        platform = detect_platform(url)
        if platform.id == "youtube":
            self._platform_chip.setText("YouTube")
            self._platform_chip.show()
            self._valid_check.show()
            self._wrong_platform_banner.hide()
        elif platform.engine != DownloadEngine.UNSUPPORTED:
            # Another recognized platform link pasted here
            self._platform_chip.setText(platform.display_name)
            self._platform_chip.show()
            self._valid_check.show()
            self._show_wrong_platform_banner(platform.id, platform.display_name)
        else:
            self._platform_chip.hide()
            self._valid_check.hide()
            self._wrong_platform_banner.hide()

    def _show_wrong_platform_banner(self, platform_id: str, platform_name: str) -> None:
        self._wp_target_id = platform_id
        self._wp_label.setText(f"This is an {platform_name} link. Open in {platform_name} page?")
        self._wp_switch_btn.setText(f"Open {platform_name}")
        self._wrong_platform_banner.show()

    def _on_switch_platform_clicked(self) -> None:
        url = self._url_input.text().strip()
        self._wrong_platform_banner.hide()
        self.switch_platform_requested.emit(self._wp_target_id)

    def _on_paste_clicked(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard:
            text = clipboard.text().strip()
            if text:
                self._url_input.setText(text)
                self._on_url_text_changed(text)

    def _on_fetch_clicked(self) -> None:
        """Trigger background fetch with logging and watchdog."""
        url = self._url_input.text().strip()
        logger.info("[FETCH] Mouse click received on Fetch button for URL: '%s'", url)

        if not url:
            logger.info("[FETCH] Validation failed: URL is empty")
            return

        platform = detect_platform(url)
        logger.info("[FETCH] Validation result: platform=%s, engine=%s", platform.id, platform.engine.name)

        if platform.engine == DownloadEngine.UNSUPPORTED:
            logger.warning("[FETCH] Validation failed: unsupported URL: %s", url)
            self.set_error_state("This link is not recognized as a supported media link.")
            return

        if platform.id != "youtube":
            logger.info("[FETCH] Non-YouTube URL detected on YouTube page: %s", url)
            self._show_wrong_platform_banner(platform.id, platform.display_name)
            return

        # Start fetching UI state
        self.set_fetching_state()

        # Start 60-second timeout watchdog
        self._watchdog_timer.start()

        # Launch QThread + DownloadWorker
        self._shutdown_fetch_worker()

        self._fetch_thread = QThread(self)
        self._fetch_worker = DownloadWorker()
        self._fetch_worker.moveToThread(self._fetch_thread)

        self._fetch_worker.fetch_url = url
        self._fetch_worker.fetch_platform_id = "youtube"

        self._fetch_worker.info_fetched.connect(self._on_worker_info_fetched)
        self._fetch_worker.info_fetch_failed.connect(self._on_worker_fetch_failed)

        self._fetch_thread.started.connect(self._fetch_worker.execute_fetch, Qt.ConnectionType.DirectConnection)
        self._fetch_thread.finished.connect(self._on_fetch_thread_finished)
        self._fetch_thread.finished.connect(self._fetch_thread.deleteLater)
        self._fetch_thread.finished.connect(self._fetch_worker.deleteLater)

        logger.info("[FETCH] Background DownloadWorker thread started")
        self._fetch_thread.start()

    def _on_fetch_timeout(self) -> None:
        logger.error("[FETCH] Fetch timed out after 60 seconds")
        self._shutdown_fetch_worker()
        self.set_error_state("Fetch timed out after 60 seconds. Please check your network connection and retry.")

    def is_fetching(self) -> bool:
        return self._fetch_thread is not None and self._fetch_thread.isRunning()

    def _shutdown_fetch_worker(self) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()

        if self._fetch_worker:
            try:
                self._fetch_worker.request_cancel()
            except Exception as e:
                logger.warning("Error cancelling fetch worker: %s", e)

        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
            if not self._fetch_thread.wait(1500):
                self._fetch_thread.terminate()
                self._fetch_thread.wait(500)

        self._fetch_thread = None
        self._fetch_worker = None

    def _on_fetch_thread_finished(self) -> None:
        self._fetch_thread = None
        self._fetch_worker = None

    @Slot(object)
    def _on_worker_info_fetched(self, video_info: VideoInfo) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
        logger.info("[FETCH] yt-dlp exit code: 0, info parsed: '%s' (duration=%ds, formats=%d)",
                    video_info.title, video_info.duration, len(video_info.video_formats))
        self.set_loaded_state(video_info)
        self.info_fetched.emit(video_info)

    @Slot(str, bool)
    def _on_worker_fetch_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        if self._watchdog_timer.isActive():
            self._watchdog_timer.stop()
        if self._fetch_thread and self._fetch_thread.isRunning():
            self._fetch_thread.quit()
        logger.error("[FETCH] yt-dlp fetch failed: %s (extraction=%s)", error_msg, is_extraction_error)
        self.set_error_state(error_msg)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ACTIONS & DOWNLOAD
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _on_browse_folder(self) -> None:
        init_dir = getattr(self, "_full_folder_path", "") or self._folder_input.text()
        folder = QFileDialog.getExistingDirectory(self, "Select Download Folder", init_dir)
        if folder:
            self._full_folder_path = folder
            if self._settings:
                self._settings.update(last_output_folder=folder)
            self._update_text_elisions()

    def _build_download_request(self) -> Optional[DownloadRequest]:
        if not self._video_info:
            return None

        url = self._url_input.text().strip()
        output_dir = getattr(self, "_full_folder_path", "") or self._folder_input.text().strip()
        if not output_dir:
            output_dir = getattr(self._settings, "last_output_folder", None) if self._settings else None
            if not output_dir:
                output_dir = os.path.join(os.path.expanduser("~"), "Downloads")

        filename = self._filename_input.text().strip() or suggest_filename(self._video_info.title)

        start_sec = None if self._is_full_video else self._start_seconds
        end_sec = None if self._is_full_video else self._end_seconds

        # Unique naming for clips to prevent filename collision
        if not self._is_full_video and start_sec is not None and end_sec is not None:
            clip_suffix = f"_{start_sec}s-{end_sec}s"
            if clip_suffix not in filename:
                filename = f"{filename}{clip_suffix}"

        video_fmt = None
        audio_fmt = None
        if not self._is_audio_only:
            data = self._quality_combo.currentData()
            if isinstance(data, VideoFormat):
                video_fmt = data
            elif self._video_info and self._video_info.video_formats:
                video_fmt = self._video_info.video_formats[0]
        else:
            data = self._quality_combo.currentData()
            if isinstance(data, AudioFormat):
                audio_fmt = data
            elif self._video_info and self._video_info.audio_formats:
                audio_fmt = self._video_info.audio_formats[0]

        audio_output_fmt = None
        if self._is_audio_only:
            fmt_str = self._selected_format.lower()
            try:
                audio_output_fmt = AudioOutputFormat(fmt_str)
            except Exception:
                pass

        req = DownloadRequest(
            url=url,
            output_dir=output_dir,
            filename=sanitize_filename(filename),
            video_format=video_fmt,
            audio_format=audio_fmt,
            audio_only=self._is_audio_only,
            audio_output_format=audio_output_fmt,
            start_time=start_sec,
            end_time=end_sec,
            merge_format=self._selected_format.lower(),
            frame_accurate_clipping=self._is_frame_accurate,
            convert_to_gif=(self._selected_format == "GIF"),
            concurrent_fragments=getattr(self._settings, "concurrent_fragments", 16) if self._settings else 16,
        )
        return req

    def _on_add_to_queue_clicked(self) -> None:
        req = self._build_download_request()
        if req and self._video_info:
            payload = {
                "request": req,
                "video_info": self._video_info,
                "title": self._video_info.title,
                "start_seconds": None if self._is_full_video else self._start_seconds,
                "end_seconds": None if self._is_full_video else self._end_seconds,
            }
            self.add_to_queue_requested.emit(payload)

    def _on_download_clicked(self) -> None:
        req = self._build_download_request()
        if req and self._video_info:
            payload = {
                "request": req,
                "video_info": self._video_info,
                "title": self._video_info.title,
                "start_seconds": None if self._is_full_video else self._start_seconds,
                "end_seconds": None if self._is_full_video else self._end_seconds,
            }
            self.download_requested.emit(payload)

    def update_theme(self, theme_name: str) -> None:
        """Update YouTube page elements for dark/light theme."""
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
        btn_hover = "#1C1F2B" if is_dark else "#F5F7FA"

        if hasattr(self, "_url_input_box"):
            self._url_input_box.setStyleSheet(
                f"QFrame#urlInputBox {{ background-color: {input_bg}; border: 1px solid {input_border}; border-radius: 12px; }}"
            )
        if hasattr(self, "_url_input"):
            self._url_input.setStyleSheet(f"QLineEdit {{ background: transparent; border: none; color: {input_fg}; padding: 0; }}")
        if hasattr(self, "_paste_btn"):
            self._paste_btn.setStyleSheet(
                f"QPushButton {{ background-color: {card_bg}; color: {text_primary}; border: 1px solid {card_border}; border-radius: 10px; padding: 0 16px; }}"
                f"QPushButton:hover {{ background-color: {btn_hover}; }}"
            )
        if hasattr(self, "_preview_card"):
            self._preview_card.setStyleSheet(
                f"QFrame#previewCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 20px; }}"
            )
        if hasattr(self, "_trim_card"):
            self._trim_card.setStyleSheet(
                f"QFrame#trimCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 20px; }}"
            )
        if hasattr(self, "_trim_title"):
            self._trim_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_trim_sub"):
            self._trim_sub.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_live_chip"):
            self._live_chip.setStyleSheet(f"background-color: {pill_bg}; color: {text_secondary}; border-radius: 14px; padding: 4px 12px;")
        if hasattr(self, "_res_chip"):
            self._res_chip.setStyleSheet(f"background-color: {pill_bg}; color: {text_secondary}; border-radius: 14px; padding: 4px 12px;")
        if hasattr(self, "_duration_badge"):
            self._duration_badge.setStyleSheet(f"background-color: {pill_bg}; color: {text_secondary}; border-radius: 14px; padding: 4px 12px;")
        if hasattr(self, "_full_video_btn"):
            self._full_video_btn.setStyleSheet(
                f"QPushButton {{ background-color: {pill_bg}; color: {text_secondary}; border: 1px solid {pill_border}; border-radius: 14px; padding: 0 12px; }}"
            )
        if hasattr(self, "_start_card"):
            self._start_card.setStyleSheet(
                f"QFrame#startCard {{ background-color: {card_alt_bg}; border: 1px solid {card_alt_border}; border-radius: 12px; }}"
            )
        if hasattr(self, "_end_card"):
            self._end_card.setStyleSheet(
                f"QFrame#endCard {{ background-color: {card_alt_bg}; border: 1px solid {card_alt_border}; border-radius: 12px; }}"
            )
        if hasattr(self, "_start_tc"):
            self._start_tc.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_end_tc"):
            self._end_tc.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_s_lbl"):
            self._s_lbl.setStyleSheet(f"color: {text_muted}; letter-spacing: 0.5px;")
        if hasattr(self, "_e_lbl"):
            self._e_lbl.setStyleSheet(f"color: {text_muted}; letter-spacing: 0.5px;")
        if hasattr(self, "_arrow_lbl"):
            self._arrow_lbl.setStyleSheet(f"color: {text_muted};")

        # Nudge & Use playhead buttons
        for btn in (getattr(self, "_s_m1", None), getattr(self, "_s_p1", None), getattr(self, "_e_m1", None), getattr(self, "_e_p1", None)):
            if btn:
                btn.setStyleSheet(f"background-color: {pill_bg}; color: {text_secondary}; border: 1px solid {pill_border}; border-radius: 6px; padding: 0;")
        for btn in (getattr(self, "_s_use", None), getattr(self, "_e_use", None)):
            if btn:
                btn.setStyleSheet(f"background-color: {pill_bg}; color: {COLOR_ACCENT}; border: 1px solid {pill_border}; border-radius: 6px; padding: 0 6px;")

        # Preset buttons
        if hasattr(self, "_preset_buttons"):
            for p_btn in self._preset_buttons:
                p_btn.setStyleSheet(
                    f"QPushButton {{ background-color: {pill_bg}; color: {text_secondary}; border: 1px solid {pill_border}; border-radius: 6px; padding: 0 8px; }}"
                    f"QPushButton:hover {{ background-color: {btn_hover}; color: {text_primary}; }}"
                    f"QPushButton:disabled {{ background-color: transparent; color: {text_muted}; border-color: {card_alt_border}; }}"
                )
        if hasattr(self, "_exact_lbl"):
            self._exact_lbl.setStyleSheet(f"color: {text_secondary};")

        # Player Controls Bar
        icon_color = "#FFFFFF" if is_dark else "#111318"
        ctrl_bg = "rgba(255, 255, 255, 0.08)" if is_dark else "#EEF1F6"
        ctrl_border = "rgba(255, 255, 255, 0.15)" if is_dark else "#D8DCE5"
        ctrl_hover = "rgba(255, 255, 255, 0.16)" if is_dark else "#E2E6EE"
        ctrl_fg = text_primary

        if hasattr(self, "_rewind_btn"):
            self._rewind_btn.setIcon(get_svg_icon("skip-back", color=icon_color, size=16))
        if hasattr(self, "_play_btn"):
            icon_name = "pause" if getattr(self, "_is_playing", False) else "play"
            self._play_btn.setIcon(get_svg_icon(icon_name, color=icon_color, size=16))
        if hasattr(self, "_forward_btn"):
            self._forward_btn.setIcon(get_svg_icon("skip-forward", color=icon_color, size=16))
        if hasattr(self, "_volume_btn"):
            self._volume_btn.setIcon(get_svg_icon("volume", color=text_secondary, size=16))
        if hasattr(self, "_volume_lbl"):
            self._volume_lbl.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_timecode_lbl"):
            self._timecode_lbl.setStyleSheet(f"color: {text_muted};")

        # Quality, Speed, CC buttons
        for btn in (getattr(self, "_quality_btn", None), getattr(self, "_speed_btn", None), getattr(self, "_cc_btn", None)):
            if btn:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {ctrl_bg}; color: {ctrl_fg}; border: 1px solid {ctrl_border}; border-radius: 14px; padding: 0 8px; }}"
                    f"QPushButton:hover {{ background-color: {ctrl_hover}; }}"
                )
        if hasattr(self, "_playback_settings_btn"):
            self._playback_settings_btn.setIcon(get_svg_icon("settings", color=text_secondary, size=16))
        if hasattr(self, "_more_btn"):
            self._more_btn.setIcon(get_svg_icon("more-horizontal", color=text_secondary, size=16))

        # Sliders
        slider_groove = "rgba(255, 255, 255, 0.2)" if is_dark else "#D8DCE5"
        slider_sub = "#FFFFFF" if is_dark else COLOR_ACCENT
        slider_handle = "#FFFFFF" if is_dark else COLOR_ACCENT
        if hasattr(self, "_volume_slider"):
            self._volume_slider.setStyleSheet(
                f"QSlider::groove:horizontal {{ height: 4px; background: {slider_groove}; border-radius: 2px; }}"
                f"QSlider::sub-page:horizontal {{ background: {slider_sub}; border-radius: 2px; }}"
                f"QSlider::handle:horizontal {{ background: {slider_handle}; border: none; width: 10px; height: 10px; margin: -3px 0; border-radius: 5px; }}"
            )
        if hasattr(self, "_scrubber_slider"):
            self._scrubber_slider.setStyleSheet(
                "QSlider#playerScrubber { height: 16px; }"
                f"QSlider#playerScrubber::groove:horizontal {{ height: 3px; background: {slider_groove}; border-radius: 1.5px; }}"
                f"QSlider#playerScrubber::sub-page:horizontal {{ background: {slider_sub}; border-radius: 1.5px; }}"
                f"QSlider#playerScrubber::handle:horizontal {{ background: {slider_handle}; border: none; width: 8px; height: 8px; margin: -2.5px 0; border-radius: 4px; }}"
            )
        if hasattr(self, "_in_point_shortcut_lbl"):
            self._in_point_shortcut_lbl.setStyleSheet(f"color: {text_muted};")
        if hasattr(self, "_out_point_shortcut_lbl"):
            self._out_point_shortcut_lbl.setStyleSheet(f"color: {text_muted};")

        # Info Card
        self._update_info_card_style()
        if hasattr(self, "_video_title") and not (self._video_info is None and hasattr(self, "_is_error_state") and self._is_error_state):
            self._video_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_video_meta"):
            self._video_meta.setStyleSheet(f"color: {text_secondary};")

        # Quality Card
        if hasattr(self, "_quality_card"):
            self._quality_card.setStyleSheet(
                f"QFrame#qualityCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 20px; }}"
            )
        if hasattr(self, "_q_title"):
            self._q_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_quality_lbl"):
            self._quality_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_fmt_lbl"):
            self._fmt_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_mode_toggle"):
            self._mode_toggle._update_appearance()
        if hasattr(self, "_format_chips"):
            for chip in self._format_chips.values():
                chip._update_style()

        # Save Card
        if hasattr(self, "_save_card"):
            self._save_card.setStyleSheet(
                f"QFrame#saveCard {{ background-color: {card_bg}; border: 1px solid {card_border}; border-radius: 20px; }}"
            )
        if hasattr(self, "_save_title"):
            self._save_title.setStyleSheet(f"color: {text_primary};")
        if hasattr(self, "_fn_lbl"):
            self._fn_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_fn_box"):
            self._fn_box.setStyleSheet(
                f"QFrame#fnBox {{ background-color: {input_bg}; border: 1px solid {input_border}; border-radius: 10px; }}"
            )
        if hasattr(self, "_filename_input"):
            self._filename_input.setStyleSheet(f"background: transparent; border: none; color: {input_fg};")
        if hasattr(self, "_ext_lbl"):
            self._ext_lbl.setStyleSheet(f"color: {text_muted};")
        if hasattr(self, "_fd_lbl"):
            self._fd_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_folder_input"):
            self._folder_input.setStyleSheet(
                f"QLineEdit {{ background-color: {input_bg}; color: {text_secondary}; border: 1px solid {input_border}; border-radius: 10px; padding: 0 10px; }}"
            )
        if hasattr(self, "_browse_btn"):
            self._browse_btn.setStyleSheet(
                f"QPushButton {{ background-color: {pill_bg}; color: {text_primary}; border: 1px solid {pill_border}; border-radius: 10px; padding: 0 12px; }}"
                f"QPushButton:hover {{ background-color: {btn_hover}; }}"
            )

        # Action Buttons
        if hasattr(self, "_summary_lbl"):
            self._summary_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_size_lbl"):
            self._size_lbl.setStyleSheet(f"color: {text_secondary};")
        if hasattr(self, "_queue_btn"):
            self._queue_btn.setStyleSheet(
                f"QPushButton {{ background-color: {card_bg}; color: {text_primary}; border: 1px solid {card_border}; border-radius: 12px; }}"
                f"QPushButton:hover {{ background-color: {btn_hover}; }}"
                f"QPushButton:disabled {{ background-color: {card_bg}; color: {text_muted}; border-color: {card_alt_border}; }}"
            )
