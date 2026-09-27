"""
YouCut — Format parsing and data structures.

Parses the raw format list from yt-dlp's extract_info() into typed dataclasses
that can populate the GUI's quality dropdowns with real data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from clipgrab.core.logger import get_logger

logger = get_logger("formats")


class AudioOutputFormat(Enum):
    """Supported audio output container formats."""
    MP3 = "mp3"
    M4A = "m4a"
    WAV = "wav"


@dataclass
class VideoFormat:
    """Represents a single video stream format available for a YouTube video."""

    format_id: str
    resolution: str  # e.g., "1920x1080"
    height: int
    width: int
    fps: Optional[int] = None
    vcodec: str = "unknown"
    ext: str = "mp4"
    filesize_approx: Optional[int] = None
    format_note: str = ""
    dynamic_range: Optional[str] = None

    def display_label(self) -> str:
        """
        Generate a human-readable label for the format dropdown.

        Returns:
            A string like "1080p60 (h264) — ~45MB" or "720p (vp9) — ~20MB".
        """
        parts: list[str] = []

        # Resolution with fps
        res_label = f"{self.height}p"
        if self.fps and self.fps > 30:
            res_label += str(self.fps)
        parts.append(res_label)

        # HDR indicator
        if self.dynamic_range and self.dynamic_range.lower() != "sdr":
            parts.append(self.dynamic_range.upper())

        # Codec
        codec_short = _shorten_codec(self.vcodec)
        parts.append(f"({codec_short})")

        # Approx file size
        if self.filesize_approx and self.filesize_approx > 0:
            size_str = _format_filesize(self.filesize_approx)
            parts.append(f"— ~{size_str}")

        return " ".join(parts)


@dataclass
class AudioFormat:
    """Represents a single audio stream format available for a YouTube video."""

    format_id: str
    abr: Optional[float] = None  # audio bitrate in kbps
    acodec: str = "unknown"
    ext: str = "m4a"
    filesize_approx: Optional[int] = None
    format_note: str = ""
    asr: Optional[int] = None  # audio sample rate

    def display_label(self) -> str:
        """
        Generate a human-readable label for the audio format dropdown.

        Returns:
            A string like "160kbps (opus)" or "128kbps (aac) — ~5MB".
        """
        parts: list[str] = []

        # Bitrate
        if self.abr:
            parts.append(f"{int(self.abr)}kbps")
        else:
            parts.append("unknown bitrate")

        # Codec
        codec_short = _shorten_codec(self.acodec)
        parts.append(f"({codec_short})")

        # Sample rate
        if self.asr:
            parts.append(f"{self.asr}Hz")

        # Approx file size
        if self.filesize_approx and self.filesize_approx > 0:
            size_str = _format_filesize(self.filesize_approx)
            parts.append(f"— ~{size_str}")

        return " ".join(parts)


@dataclass
class VideoInfo:
    """Parsed metadata for a YouTube video."""

    title: str = ""
    url: str = ""
    duration: float = 0.0
    thumbnail_url: str = ""
    uploader: str = ""
    upload_date: str = ""
    description: str = ""
    video_formats: list[VideoFormat] = field(default_factory=list)
    audio_formats: list[AudioFormat] = field(default_factory=list)
    is_live: bool = False
    is_age_restricted: bool = False
    is_playlist: bool = False
    playlist_count: int = 0
    raw_info: dict = field(default_factory=dict, repr=False)


def parse_video_formats(info_dict: dict) -> list[VideoFormat]:
    """
    Extract and sort video-only formats from yt-dlp's info dictionary.

    Filters to formats that have a video codec and no audio codec (video-only streams),
    deduplicates by resolution+codec, and sorts by quality (highest first).

    Args:
        info_dict: The dictionary returned by yt_dlp extract_info().

    Returns:
        A sorted list of VideoFormat objects, highest quality first.
    """
    formats = info_dict.get("formats", [])
    video_formats: list[VideoFormat] = []
    seen: set[str] = set()

    for fmt in formats:
        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")

        # Skip audio-only and formats without video
        if vcodec in ("none", None) or vcodec == "":
            continue

        height = fmt.get("height")
        if not height or height <= 0:
            continue

        # Deduplicate by resolution + codec combination
        width = fmt.get("width", 0) or 0
        fps = fmt.get("fps")
        dedup_key = f"{height}_{fps}_{vcodec}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        vf = VideoFormat(
            format_id=str(fmt.get("format_id", "")),
            resolution=fmt.get("resolution", f"{width}x{height}"),
            height=height,
            width=width,
            fps=int(fps) if fps else None,
            vcodec=vcodec,
            ext=fmt.get("ext", "mp4"),
            filesize_approx=fmt.get("filesize_approx") or fmt.get("filesize"),
            format_note=fmt.get("format_note", ""),
            dynamic_range=fmt.get("dynamic_range"),
        )
        video_formats.append(vf)

    # Sort: highest resolution first, then highest fps, then prefer h264 > vp9 > av01
    video_formats.sort(
        key=lambda f: (f.height, f.fps or 0, _codec_priority(f.vcodec)),
        reverse=True,
    )

    logger.debug("Parsed %d video formats from info dict", len(video_formats))
    return video_formats


def parse_audio_formats(info_dict: dict) -> list[AudioFormat]:
    """
    Extract and sort audio-only formats from yt-dlp's info dictionary.

    Filters to formats that have an audio codec but no video codec,
    deduplicates by bitrate+codec, and sorts by quality (highest first).

    Args:
        info_dict: The dictionary returned by yt_dlp extract_info().

    Returns:
        A sorted list of AudioFormat objects, highest quality first.
    """
    formats = info_dict.get("formats", [])
    audio_formats: list[AudioFormat] = []
    seen: set[str] = set()

    for fmt in formats:
        vcodec = fmt.get("vcodec", "none")
        acodec = fmt.get("acodec", "none")

        # Only audio-only streams
        if acodec in ("none", None) or acodec == "":
            continue
        if vcodec not in ("none", None, ""):
            continue

        abr = fmt.get("abr") or fmt.get("tbr")
        dedup_key = f"{abr}_{acodec}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        af = AudioFormat(
            format_id=str(fmt.get("format_id", "")),
            abr=float(abr) if abr else None,
            acodec=acodec,
            ext=fmt.get("ext", "m4a"),
            filesize_approx=fmt.get("filesize_approx") or fmt.get("filesize"),
            format_note=fmt.get("format_note", ""),
            asr=fmt.get("asr"),
        )
        audio_formats.append(af)

    # Sort: highest bitrate first
    audio_formats.sort(key=lambda f: f.abr or 0, reverse=True)

    logger.debug("Parsed %d audio formats from info dict", len(audio_formats))
    return audio_formats


def parse_video_info(info_dict: dict) -> VideoInfo:
    """
    Parse the full yt-dlp info dictionary into a structured VideoInfo object.

    Args:
        info_dict: The dictionary returned by yt_dlp extract_info().

    Returns:
        A VideoInfo object with all parsed metadata and format lists.
    """
    return VideoInfo(
        title=info_dict.get("title", "Unknown Title"),
        url=info_dict.get("webpage_url", info_dict.get("url", "")),
        duration=float(info_dict.get("duration", 0) or 0),
        thumbnail_url=info_dict.get("thumbnail", ""),
        uploader=info_dict.get("uploader", ""),
        upload_date=info_dict.get("upload_date", ""),
        description=info_dict.get("description", ""),
        video_formats=parse_video_formats(info_dict),
        audio_formats=parse_audio_formats(info_dict),
        is_live=bool(info_dict.get("is_live")),
        is_age_restricted=bool(info_dict.get("age_limit", 0)),
        is_playlist=info_dict.get("_type") == "playlist",
        playlist_count=int(info_dict.get("playlist_count", 0) or 0),
        raw_info=info_dict,
    )


def _shorten_codec(codec: str) -> str:
    """Shorten a codec string to a human-friendly abbreviation."""
    if not codec:
        return "unknown"
    codec_lower = codec.lower()
    if "avc1" in codec_lower or "h264" in codec_lower:
        return "h264"
    if "vp9" in codec_lower or "vp09" in codec_lower:
        return "vp9"
    if "av01" in codec_lower or "av1" in codec_lower:
        return "av1"
    if "opus" in codec_lower:
        return "opus"
    if "mp4a" in codec_lower or "aac" in codec_lower:
        return "aac"
    if "vorbis" in codec_lower:
        return "vorbis"
    # Return first part before dot
    return codec.split(".")[0]


def _codec_priority(codec: str) -> int:
    """Return a priority value for codec preference (higher = more preferred)."""
    codec_lower = codec.lower() if codec else ""
    if "avc1" in codec_lower or "h264" in codec_lower:
        return 3  # Most compatible
    if "vp9" in codec_lower or "vp09" in codec_lower:
        return 2
    if "av01" in codec_lower or "av1" in codec_lower:
        return 1  # Best quality but less compatible
    return 0


def _format_filesize(size_bytes: int) -> str:
    """Format a file size in bytes to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes}B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f}KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.0f}MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
