"""
YouCut — Centralized Thumbnail Service and Asynchronous Backfill Engine.

Handles:
- Storage in ~/.youcut/thumbnails/
- Safe frame extraction via bundled FFmpeg
- Remote thumbnail download via urllib (timeouts, no extra dependencies)
- YouTube video ID parsing and mqdefault.jpg resolution
- Thread-safe background workers (creating QImage only, QPixmap on GUI thread)
- Antialiased 16:9 rounded (8px) cropping
- Themed platform and audio placeholders
- In-memory LRU caching for smooth 60fps scrolling
- Attempted tracking to eliminate redundant backfill cycles
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap

from clipgrab.core.ffmpeg_utils import get_ffmpeg_path
from clipgrab.core.logger import get_logger
from clipgrab.core.platforms import detect_platform
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.icons import get_svg_pixmap

logger = get_logger("thumbnail_service")


def get_thumbnail_dir() -> Path:
    """Get the local directory path for storing cached thumbnails."""
    env_home = os.environ.get("YOUCUT_HOME")
    if env_home:
        base = Path(env_home).resolve()
    else:
        base = Path.home() / ".youcut"
    thumb_dir = base / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    return thumb_dir


def extract_youtube_id(url: str) -> Optional[str]:
    """Extract an 11-character YouTube video ID from various URL formats."""
    if not url:
        return None
    patterns = [
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([a-zA-Z0-9_-]{11})",
        r"(?:[?&]v=)([a-zA-Z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def is_audio_entry(entry: HistoryEntry) -> bool:
    """Check if a history entry is an audio-only file."""
    out_lower = (entry.output_path or "").lower()
    vq_lower = (entry.video_quality or "").lower()

    # Explicit audio file extensions
    audio_exts = (".mp3", ".m4a", ".wav", ".aac", ".opus", ".flac", ".ogg", ".wma")
    if any(out_lower.endswith(ext) for ext in audio_exts):
        return True

    # Explicit video or gif extensions
    video_exts = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".wmv", ".m4v", ".gif")
    if any(out_lower.endswith(ext) for ext in video_exts):
        return False

    if "audio only" in vq_lower:
        return True

    if entry.audio_quality and not entry.video_quality:
        return True

    return False


def extract_frame_ffmpeg(video_path: str, output_jpg: str, start_sec: float = 1.0) -> bool:
    """
    Extract a single frame from a video file using bundled FFmpeg, scaled to max 480px width.
    Uses CREATE_NO_WINDOW on Windows to prevent console flashing.
    """
    if not video_path or not os.path.isfile(video_path):
        return False

    ffmpeg_bin = get_ffmpeg_path()
    if not ffmpeg_bin:
        logger.warning("FFmpeg binary not resolved for frame extraction")
        return False

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    cmd = [
        ffmpeg_bin,
        "-ss", str(start_sec),
        "-i", video_path,
        "-vframes", "1",
        "-vf", "scale='min(480,iw)':-1",
        "-q:v", "3",
        output_jpg,
        "-y",
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            creationflags=creationflags,
        )
        if res.returncode == 0 and os.path.isfile(output_jpg) and os.path.getsize(output_jpg) > 0:
            return True
    except Exception as e:
        logger.warning("FFmpeg frame extraction failed for %s: %s", video_path, e)

    return False


def download_image_urllib(url: str, output_jpg: str) -> bool:
    """
    Download an image URL using urllib, scale to max 480px wide, and save as JPEG.
    """
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()

        img = QImage()
        if not img.loadFromData(data):
            return False

        if img.width() > 480:
            img = img.scaledToWidth(480, Qt.TransformationMode.SmoothTransformation)

        img.save(output_jpg, "JPEG", quality=85)
        return os.path.isfile(output_jpg) and os.path.getsize(output_jpg) > 0
    except Exception as e:
        logger.debug("Failed to download image from %s: %s", url, e)
        return False


def make_rounded_pixmap(
    source: QImage | QPixmap,
    target_w: int,
    target_h: int,
    radius: int = 8,
) -> QPixmap:
    """
    Crop to 16:9 aspect ratio (KeepAspectRatioByExpanding) centered,
    and clip with antialiased rounded corners.
    """
    if isinstance(source, QImage):
        src_pix = QPixmap.fromImage(source)
    else:
        src_pix = source

    if src_pix.isNull():
        empty = QPixmap(target_w, target_h)
        empty.fill(Qt.GlobalColor.transparent)
        return empty

    scaled = src_pix.scaled(
        target_w,
        target_h,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )

    crop_x = max(0, (scaled.width() - target_w) // 2)
    crop_y = max(0, (scaled.height() - target_h) // 2)
    cropped = scaled.copy(crop_x, crop_y, target_w, target_h)

    out_pix = QPixmap(target_w, target_h)
    out_pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(out_pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    path = QPainterPath()
    path.addRoundedRect(0, 0, target_w, target_h, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, cropped)
    painter.end()

    return out_pix


def generate_placeholder_pixmap(
    entry: HistoryEntry,
    target_w: int,
    target_h: int,
    is_dark: bool = True,
    radius: int = 8,
) -> QPixmap:
    """
    Generate a clean themed placeholder with 8px rounded corners.
    Shows a music icon for audio entries, and platform icon for video entries.
    """
    pix = QPixmap(target_w, target_h)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    bg_color = QColor("#1E212D") if is_dark else QColor("#E4E7EC")
    border_color = QColor(255, 255, 255, 18) if is_dark else QColor(0, 0, 0, 15)

    path = QPainterPath()
    path.addRoundedRect(0, 0, target_w, target_h, radius, radius)
    painter.fillPath(path, bg_color)
    painter.setPen(border_color)
    painter.drawPath(path)

    # Determine icon
    if is_audio_entry(entry):
        icon_pix = get_svg_pixmap("music", color="#8B949E" if is_dark else "#6E7781", size=min(target_h // 2, 28))
    else:
        plat = detect_platform(entry.url)
        p_id = plat.id.lower() if plat else "youtube"
        icon_size = min(target_h // 2, 28)
        if p_id == "youtube":
            icon_pix = get_svg_pixmap("youtube", color="#EF4444", size=icon_size)
        elif p_id == "instagram":
            icon_pix = get_svg_pixmap("instagram", color="#EC4899", size=icon_size)
        elif p_id == "facebook":
            icon_pix = get_svg_pixmap("facebook", color="#3B82F6", size=icon_size)
        else:
            icon_pix = get_svg_pixmap("film", color="#8B949E" if is_dark else "#6E7781", size=icon_size)

    if icon_pix and not icon_pix.isNull():
        ix = (target_w - icon_pix.width()) // 2
        iy = (target_h - icon_pix.height()) // 2
        painter.drawPixmap(ix, iy, icon_pix)

    painter.end()
    return pix


class _WorkerSignals(QObject):
    """Signals emitted from background thumbnail workers to the GUI thread."""
    result_ready = Signal(int, object, str)  # entry_id, QImage (or None), cached_path (or "")


class _ThumbnailBackfillWorker(QRunnable):
    """
    Background worker that resolves thumbnail using the strict priority order:
      a. cached thumbnail_path on disk
      b. else if downloaded file exists -> extract frame via FFmpeg
      c. else if YouTube URL -> derive https://i.ytimg.com/vi/<id>/mqdefault.jpg
      d. else if thumbnail_url is present -> download via urllib
      e. else -> return None (placeholder)
    Worker creates QImage ONLY, never touching QPixmap or GUI objects.
    """

    def __init__(self, entry: HistoryEntry) -> None:
        super().__init__()
        self.entry = entry
        self.signals = _WorkerSignals(parent=None)

    def run(self) -> None:
        thumb_dir = get_thumbnail_dir()
        entry = self.entry
        cached_path = ""
        resolved_image: Optional[QImage] = None

        # a. Check if existing thumbnail_path exists on disk
        if entry.thumbnail_path and os.path.isfile(entry.thumbnail_path):
            img = QImage()
            if img.load(entry.thumbnail_path):
                resolved_image = img
                cached_path = entry.thumbnail_path

        # b. Else if downloaded file still exists -> extract frame with FFmpeg
        if resolved_image is None and entry.output_path and os.path.isfile(entry.output_path):
            # Do not extract video frames for known audio files
            if not is_audio_entry(entry):
                file_hash = hashlib.md5(entry.output_path.encode("utf-8")).hexdigest()
                cand_path = str(thumb_dir / f"frame_{file_hash}.jpg")
                if extract_frame_ffmpeg(entry.output_path, cand_path):
                    img = QImage()
                    if img.load(cand_path):
                        resolved_image = img
                        cached_path = cand_path

        # c. Else if YouTube URL -> derive thumbnail from video ID
        if resolved_image is None:
            yt_id = extract_youtube_id(entry.url)
            if yt_id:
                cand_path = str(thumb_dir / f"yt_{yt_id}.jpg")
                if os.path.isfile(cand_path):
                    img = QImage()
                    if img.load(cand_path):
                        resolved_image = img
                        cached_path = cand_path
                else:
                    yt_url = f"https://i.ytimg.com/vi/{yt_id}/mqdefault.jpg"
                    if download_image_urllib(yt_url, cand_path):
                        img = QImage()
                        if img.load(cand_path):
                            resolved_image = img
                            cached_path = cand_path

        # d. Else if thumbnail_url exists in database
        if resolved_image is None and entry.thumbnail_url:
            url_hash = hashlib.md5(entry.thumbnail_url.encode("utf-8")).hexdigest()
            cand_path = str(thumb_dir / f"net_{url_hash}.jpg")
            if os.path.isfile(cand_path):
                img = QImage()
                if img.load(cand_path):
                    resolved_image = img
                    cached_path = cand_path
            else:
                if download_image_urllib(entry.thumbnail_url, cand_path):
                    img = QImage()
                    if img.load(cand_path):
                        resolved_image = img
                        cached_path = cand_path

        # Emit back to GUI thread
        self.signals.result_ready.emit(entry.id, resolved_image, cached_path)


class ThumbnailService(QObject):
    """
    Singleton service managing asynchronous thumbnail loading, backfills,
    and in-memory LRU pixmap caching.
    """

    _instance: Optional[ThumbnailService] = None

    @classmethod
    def get_instance(cls) -> ThumbnailService:
        if cls._instance is None:
            cls._instance = ThumbnailService()
        return cls._instance

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._lru_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._max_cache_size: int = 250
        self._attempted_entry_ids: set[int] = set()
        self._pending_callbacks: dict[int, list[Callable[[int, QPixmap], None]]] = {}

        self._thread_pool = QThreadPool()
        # Limit worker threads to 2 so UI and disk I/O remain lightweight
        self._thread_pool.setMaxThreadCount(2)

    def cache_thumbnail_for_completed_item(
        self,
        item_id: str,
        thumbnail_url: str = "",
        output_path: str = "",
    ) -> Optional[str]:
        """
        Cache thumbnail as a JPEG (max 480px wide) at download completion time.
        Critical for Instagram and Facebook where URLs expire rapidly.
        """
        thumb_dir = get_thumbnail_dir()
        seed = thumbnail_url or output_path or item_id
        file_hash = hashlib.md5(seed.encode("utf-8")).hexdigest()
        target_jpg = str(thumb_dir / f"thumb_{file_hash}.jpg")

        if os.path.isfile(target_jpg) and os.path.getsize(target_jpg) > 0:
            return target_jpg

        # Try downloading thumbnail_url
        if thumbnail_url:
            if download_image_urllib(thumbnail_url, target_jpg):
                return target_jpg

        # Fallback to frame extraction from downloaded video file
        if output_path and os.path.isfile(output_path):
            if extract_frame_ffmpeg(output_path, target_jpg):
                return target_jpg

        return None

    def load_thumbnail(
        self,
        entry: HistoryEntry,
        target_size: QSize,
        callback: Callable[[int, QPixmap], None],
        is_dark: bool = True,
        db: Optional[HistoryDB] = None,
    ) -> QPixmap:
        """
        Load thumbnail for a history entry:
        1. Returns immediately with cached QPixmap if present in LRU cache.
        2. Else returns a clean themed placeholder immediately, and queues a background worker
           to backfill/load the real thumbnail asynchronously if not already attempted.
        """
        w, h = target_size.width(), target_size.height()
        cache_key = f"{entry.id}_{w}x{h}_{'dark' if is_dark else 'light'}"

        # 1. In-memory LRU cache hit
        if cache_key in self._lru_cache:
            self._lru_cache.move_to_end(cache_key)
            return self._lru_cache[cache_key]

        # 2. Check if thumbnail_path exists on disk right now (instant synchronous conversion)
        if entry.thumbnail_path and os.path.isfile(entry.thumbnail_path):
            pix = make_rounded_pixmap(QPixmap(entry.thumbnail_path), w, h, radius=8)
            if not pix.isNull():
                self._put_in_cache(cache_key, pix)
                return pix

        # 3. Generate immediate themed placeholder
        placeholder = generate_placeholder_pixmap(entry, w, h, is_dark=is_dark)

        # 4. If already attempted, don't re-queue background tasks
        if entry.id in self._attempted_entry_ids:
            return placeholder

        # 5. Mark as attempted and queue background worker
        self._attempted_entry_ids.add(entry.id)

        if entry.id not in self._pending_callbacks:
            self._pending_callbacks[entry.id] = []
        self._pending_callbacks[entry.id].append(callback)

        worker = _ThumbnailBackfillWorker(entry)
        worker.signals.result_ready.connect(
            lambda eid, img, path: self._on_worker_result(eid, img, path, target_size, is_dark, db)
        )
        self._thread_pool.start(worker)

        return placeholder

    def _on_worker_result(
        self,
        entry_id: int,
        qimage: Optional[QImage],
        cached_path: str,
        target_size: QSize,
        is_dark: bool,
        db: Optional[HistoryDB],
    ) -> None:
        """Runs strictly on the GUI thread via Qt Signal connection."""
        w, h = target_size.width(), target_size.height()
        cache_key = f"{entry_id}_{w}x{h}_{'dark' if is_dark else 'light'}"

        # If a real thumbnail was found or extracted
        if qimage and not qimage.isNull():
            pix = make_rounded_pixmap(qimage, w, h, radius=8)
            self._put_in_cache(cache_key, pix)

            # Update database if new thumbnail_path was established
            if cached_path and db:
                try:
                    db.update_thumbnail_path(entry_id, cached_path)
                except Exception as e:
                    logger.debug("Failed to update thumbnail_path in DB: %s", e)
        else:
            # Fallback to placeholder in cache
            dummy_entry = HistoryEntry(id=entry_id)
            pix = generate_placeholder_pixmap(dummy_entry, w, h, is_dark=is_dark)
            self._put_in_cache(cache_key, pix)

        # Notify all pending callbacks
        callbacks = self._pending_callbacks.pop(entry_id, [])
        for cb in callbacks:
            try:
                cb(entry_id, pix)
            except Exception as e:
                logger.debug("Error in thumbnail callback: %s", e)

    def _put_in_cache(self, key: str, pix: QPixmap) -> None:
        """Store item in LRU cache with eviction."""
        if key in self._lru_cache:
            self._lru_cache.move_to_end(key)
        self._lru_cache[key] = pix
        if len(self._lru_cache) > self._max_cache_size:
            self._lru_cache.popitem(last=False)

    def invalidate_entry(self, entry_id: int) -> None:
        """Remove entry from LRU cache and attempted registry."""
        self._attempted_entry_ids.discard(entry_id)
        keys_to_remove = [k for k in self._lru_cache.keys() if k.startswith(f"{entry_id}_")]
        for k in keys_to_remove:
            self._lru_cache.pop(k, None)

    def clear_cache(self) -> None:
        """Clear all LRU cached pixmaps and reset attempted tracking."""
        self._lru_cache.clear()
        self._attempted_entry_ids.clear()
        self._pending_callbacks.clear()
