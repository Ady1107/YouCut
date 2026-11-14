"""
YouCut — Central Playback Controller.

Acts as the single source of truth for all playback state:
- Play / Pause state
- Playhead position and duration
- Volume level (0-100) and Mute / Unmute state
- Playback speed (0.25x - 2.0x)
- Video playback quality (144p - 2160p / Auto)
- Captions status (strictly OFF by default)
- In Point and Out Point markers for clipping

All playback windows, widgets, and adapters synchronize through this controller,
preventing playback conflicts, state desynchronization, and duplicate commands.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("youcut.playback_controller")


class PlaybackController(QObject):
    """
    Central playback controller serving as the single source of truth.
    """

    # Playback signals
    play_state_changed = Signal(bool)          # is_playing
    playhead_changed = Signal(float)           # current seconds
    duration_changed = Signal(float)           # total seconds
    volume_changed = Signal(int, bool)         # volume (0-100), is_muted
    speed_changed = Signal(float)              # speed multiplier (e.g. 1.0, 1.5)
    quality_changed = Signal(str)              # quality label (e.g. "1080p")
    captions_changed = Signal(bool)            # False = OFF, True = ON (default OFF)
    in_point_changed = Signal(float)           # in-point seconds
    out_point_changed = Signal(float)          # out-point seconds
    video_loaded = Signal(str, float)          # video_id, duration

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._is_playing: bool = False
        self._playhead_seconds: float = 0.0
        self._duration: float = 0.0
        self._volume: int = 70                # Default 70% matching UI concept
        self._is_muted: bool = False
        self._speed: float = 1.0               # Default normal 1x speed
        self._quality: str = "1080p"           # Default 1080p
        self._captions_enabled: bool = False   # Always OFF by default
        self._in_point: float = 0.0
        self._out_point: float = 0.0
        self._video_id: str = ""
        self._url: str = ""
        self._title: str = ""

        # Guard against recursive update loops from adapters
        self._is_updating: bool = False
        self._adapters: List[Any] = []

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Properties (Read-Only Accessors)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def playhead_seconds(self) -> float:
        return self._playhead_seconds

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def is_muted(self) -> bool:
        return self._is_muted

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def quality(self) -> str:
        return self._quality

    @property
    def captions_enabled(self) -> bool:
        return self._captions_enabled

    @property
    def in_point(self) -> float:
        return self._in_point

    @property
    def out_point(self) -> float:
        return self._out_point

    @property
    def video_id(self) -> str:
        return self._video_id

    @property
    def url(self) -> str:
        return self._url

    @property
    def title(self) -> str:
        return self._title

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Adapter Registration
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def register_adapter(self, adapter: Any) -> None:
        """Register a player adapter (e.g. WebEngine, QtMultimedia, Preview)."""
        if adapter not in self._adapters:
            self._adapters.append(adapter)
            logger.debug("Registered playback adapter: %r", adapter)

    def unregister_adapter(self, adapter: Any) -> None:
        """Unregister a player adapter."""
        if adapter in self._adapters:
            self._adapters.remove(adapter)
            logger.debug("Unregistered playback adapter: %r", adapter)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Playback Actions
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def load_video(self, video_id: str, duration: float = 0.0, url: str = "", title: str = "") -> None:
        """Load a new video into the central controller."""
        self._video_id = video_id
        self._duration = max(0.0, float(duration))
        self._url = url
        self._title = title
        self._playhead_seconds = 0.0
        self._in_point = 0.0
        self._out_point = self._duration
        self._is_playing = False
        # Captions MUST always be OFF by default on new video load
        self._captions_enabled = False

        logger.info("PlaybackController: Loaded video '%s' (id=%s, dur=%.1fs)", title, video_id, self._duration)

        # Notify adapters
        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "load_video"):
                    adapter.load_video(video_id)
                if hasattr(adapter, "set_captions"):
                    adapter.set_captions(False)
            except Exception as e:
                logger.warning("Adapter load_video error: %s", e)

        self.duration_changed.emit(self._duration)
        self.playhead_changed.emit(0.0)
        self.in_point_changed.emit(0.0)
        self.out_point_changed.emit(self._out_point)
        self.captions_changed.emit(False)
        self.play_state_changed.emit(False)
        self.video_loaded.emit(video_id, self._duration)

    def play(self) -> None:
        """Start or resume playback across all players."""
        if self._is_playing:
            return
        self._is_playing = True
        logger.debug("PlaybackController: play()")

        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "play"):
                    adapter.play()
            except Exception as e:
                logger.warning("Adapter play error: %s", e)

        self.play_state_changed.emit(True)

    def pause(self) -> None:
        """Pause playback across all players."""
        if not self._is_playing:
            return
        self._is_playing = False
        logger.debug("PlaybackController: pause()")

        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "pause"):
                    adapter.pause()
            except Exception as e:
                logger.warning("Adapter pause error: %s", e)

        self.play_state_changed.emit(False)

    def toggle_play(self) -> None:
        """Toggle between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float, notify_adapters: bool = True) -> None:
        """Seek playhead to specific second timestamp."""
        target = max(0.0, min(float(seconds), self._duration if self._duration > 0 else 999999.0))
        self._playhead_seconds = target

        if notify_adapters:
            for adapter in list(self._adapters):
                try:
                    if hasattr(adapter, "seek"):
                        adapter.seek(target)
                except Exception as e:
                    logger.warning("Adapter seek error: %s", e)

        self.playhead_changed.emit(target)

    def step_backward(self, delta: float = 10.0) -> None:
        """Step playhead backward by delta seconds (default 10s)."""
        self.seek(self._playhead_seconds - delta)

    def step_forward(self, delta: float = 10.0) -> None:
        """Step playhead forward by delta seconds (default 10s)."""
        self.seek(self._playhead_seconds + delta)

    def update_playhead_from_adapter(self, current_seconds: float) -> None:
        """Called by a live adapter when its internal playhead advances."""
        if self._is_updating:
            return
        self._is_updating = True
        try:
            self._playhead_seconds = max(0.0, float(current_seconds))
            self.playhead_changed.emit(self._playhead_seconds)
        finally:
            self._is_updating = False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Volume & Mute Controls
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_volume(self, volume: int) -> None:
        """Set volume percentage (0 - 100)."""
        clamped = max(0, min(100, int(volume)))
        self._volume = clamped
        if clamped > 0 and self._is_muted:
            self._is_muted = False

        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "set_volume"):
                    adapter.set_volume(clamped)
                if hasattr(adapter, "set_muted"):
                    adapter.set_muted(self._is_muted)
            except Exception as e:
                logger.warning("Adapter volume error: %s", e)

        self.volume_changed.emit(self._volume, self._is_muted)

    def set_muted(self, muted: bool) -> None:
        """Set mute state."""
        self._is_muted = bool(muted)
        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "set_muted"):
                    adapter.set_muted(self._is_muted)
            except Exception as e:
                logger.warning("Adapter mute error: %s", e)

        self.volume_changed.emit(self._volume, self._is_muted)

    def toggle_mute(self) -> None:
        """Toggle mute on/off."""
        self.set_muted(not self._is_muted)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Quality & Speed Controls
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_speed(self, speed: float) -> None:
        """Set playback speed rate (e.g. 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)."""
        self._speed = float(speed)
        logger.info("PlaybackController: Speed set to %.2fx", self._speed)

        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "set_speed"):
                    adapter.set_speed(self._speed)
                # Ensure captions stay disabled when speed changes
                if hasattr(adapter, "set_captions"):
                    adapter.set_captions(self._captions_enabled)
            except Exception as e:
                logger.warning("Adapter speed error: %s", e)

        self.speed_changed.emit(self._speed)

    def set_quality(self, quality: str) -> None:
        """Set playback quality (e.g. '1080p', '720p', '480p', '360p', 'Auto')."""
        self._quality = str(quality).strip()
        logger.info("PlaybackController: Quality set to %s", self._quality)

        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "set_quality"):
                    adapter.set_quality(self._quality)
                # Ensure captions stay disabled when quality changes
                if hasattr(adapter, "set_captions"):
                    adapter.set_captions(self._captions_enabled)
            except Exception as e:
                logger.warning("Adapter quality error: %s", e)

        self.quality_changed.emit(self._quality)

    def set_captions(self, enabled: bool) -> None:
        """Set captions state (strictly OFF by default)."""
        self._captions_enabled = bool(enabled)
        for adapter in list(self._adapters):
            try:
                if hasattr(adapter, "set_captions"):
                    adapter.set_captions(self._captions_enabled)
            except Exception as e:
                logger.warning("Adapter captions error: %s", e)

        self.captions_changed.emit(self._captions_enabled)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # In / Out Markers (Clipping Shortcuts)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def set_in_point(self, seconds: Optional[float] = None) -> float:
        """Set In Point (Start Time) at current playhead or specified second."""
        pos = self._playhead_seconds if seconds is None else float(seconds)
        pos = max(0.0, round(pos, 2))
        if self._duration > 0:
            pos = min(pos, max(0.0, self._duration - 1.0))
        # Ensure In Point < Out Point
        if self._out_point > 0 and pos >= self._out_point:
            self._out_point = min(self._duration if self._duration > 0 else pos + 1.0, pos + 1.0)
            self.out_point_changed.emit(self._out_point)

        self._in_point = pos
        logger.info("PlaybackController: In Point set to %.2fs", self._in_point)
        self.in_point_changed.emit(self._in_point)
        return self._in_point

    def set_out_point(self, seconds: Optional[float] = None) -> float:
        """Set Out Point (End Time) at current playhead or specified second."""
        pos = self._playhead_seconds if seconds is None else float(seconds)
        pos = round(pos, 2)
        if self._duration > 0:
            pos = min(float(self._duration), pos)
        # Ensure Out Point > In Point
        if pos <= self._in_point:
            pos = self._in_point + 1.0

        self._out_point = pos
        logger.info("PlaybackController: Out Point set to %.2fs", self._out_point)
        self.out_point_changed.emit(self._out_point)
        return self._out_point


# Global Singleton Instance
_global_controller: Optional[PlaybackController] = None


def get_playback_controller() -> PlaybackController:
    """Get or create the global PlaybackController singleton."""
    global _global_controller
    if _global_controller is None:
        _global_controller = PlaybackController()
    return _global_controller

