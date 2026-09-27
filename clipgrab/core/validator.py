"""
YouCut — URL and time-range validation utilities.

Provides regex-based YouTube URL validation, HH:MM:SS time parsing,
filename sanitization for Windows, and video-type detection helpers.
"""

import re
from enum import Enum
from typing import Optional

from clipgrab.core.logger import get_logger

logger = get_logger("validator")


class VideoType(Enum):
    """Classification of YouTube content types."""
    SINGLE_VIDEO = "single_video"
    PLAYLIST = "playlist"
    LIVE_STREAM = "live_stream"
    SHORT = "short"
    UNKNOWN = "unknown"


# Regex patterns for YouTube URL validation
_YOUTUBE_PATTERNS: list[re.Pattern[str]] = [
    # Standard watch URLs
    re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=[\w-]{11}",
        re.IGNORECASE,
    ),
    # Short URLs
    re.compile(
        r"^(?:https?://)?youtu\.be/[\w-]{11}",
        re.IGNORECASE,
    ),
    # Embed URLs
    re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/embed/[\w-]{11}",
        re.IGNORECASE,
    ),
    # Shorts URLs
    re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]{11}",
        re.IGNORECASE,
    ),
    # Live URLs
    re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/live/[\w-]{11}",
        re.IGNORECASE,
    ),
    # Playlist URLs
    re.compile(
        r"^(?:https?://)?(?:www\.)?youtube\.com/playlist\?.*list=[\w-]+",
        re.IGNORECASE,
    ),
    # Music URLs
    re.compile(
        r"^(?:https?://)?music\.youtube\.com/watch\?.*v=[\w-]{11}",
        re.IGNORECASE,
    ),
]

# Characters illegal in Windows filenames
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def validate_youtube_url(url: str) -> tuple[bool, str]:
    """
    Validate whether a string is a recognized YouTube URL.

    Args:
        url: The URL string to validate.

    Returns:
        A tuple of (is_valid, message). If invalid, message explains why.
    """
    if not url or not url.strip():
        return False, "URL cannot be empty."

    url = url.strip()

    for pattern in _YOUTUBE_PATTERNS:
        if pattern.match(url):
            logger.debug("URL validated successfully: %s", url[:60])
            return True, "Valid YouTube URL."

    logger.warning("URL validation failed: %s", url[:60])
    return False, (
        "Not a recognized YouTube URL. Supported formats:\n"
        "• youtube.com/watch?v=...\n"
        "• youtu.be/...\n"
        "• youtube.com/shorts/...\n"
        "• youtube.com/live/...\n"
        "• youtube.com/playlist?list=..."
    )


def detect_url_type(url: str) -> VideoType:
    """
    Detect the type of YouTube content from the URL pattern.

    Args:
        url: A validated YouTube URL.

    Returns:
        The detected VideoType enum value.
    """
    url_lower = url.lower()
    if "playlist" in url_lower and "list=" in url_lower:
        return VideoType.PLAYLIST
    if "/shorts/" in url_lower:
        return VideoType.SHORT
    if "/live/" in url_lower:
        return VideoType.LIVE_STREAM
    return VideoType.SINGLE_VIDEO


def detect_video_type_from_info(info_dict: dict) -> VideoType:
    """
    Detect the video type from yt-dlp's extracted info dictionary.

    Args:
        info_dict: The dictionary returned by yt_dlp extract_info().

    Returns:
        The detected VideoType enum value.
    """
    if info_dict.get("_type") == "playlist":
        return VideoType.PLAYLIST
    if info_dict.get("is_live"):
        return VideoType.LIVE_STREAM
    return VideoType.SINGLE_VIDEO


def parse_time_to_seconds(time_str: str) -> Optional[float]:
    """
    Convert a time string in HH:MM:SS or MM:SS format to seconds.

    Args:
        time_str: Time string like "01:30:00", "5:30", or "330".

    Returns:
        The time in seconds, or None if the format is invalid.
    """
    if not time_str or not time_str.strip():
        return None

    time_str = time_str.strip()

    # Try HH:MM:SS
    match = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?$", time_str)
    if match:
        h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3))
        frac = float(f"0.{match.group(4)}") if match.group(4) else 0.0
        if m >= 60 or s >= 60:
            return None
        return h * 3600 + m * 60 + s + frac

    # Try MM:SS
    match = re.match(r"^(\d{1,3}):(\d{2})(?:\.(\d+))?$", time_str)
    if match:
        m, s = int(match.group(1)), int(match.group(2))
        frac = float(f"0.{match.group(3)}") if match.group(3) else 0.0
        if s >= 60:
            return None
        return m * 60 + s + frac

    # Try plain seconds
    match = re.match(r"^(\d+)(?:\.(\d+))?$", time_str)
    if match:
        return float(time_str)

    return None


def seconds_to_hms(seconds: float) -> str:
    """
    Convert seconds to HH:MM:SS format string.

    Args:
        seconds: Time in seconds (non-negative).

    Returns:
        Formatted string like "01:30:45".
    """
    if seconds < 0:
        seconds = 0.0
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def validate_time_range(
    start_str: str,
    end_str: str,
    duration: float,
) -> tuple[bool, str]:
    """
    Validate a start/end time range against the video duration.

    Args:
        start_str: Start time in HH:MM:SS format.
        end_str: End time in HH:MM:SS format.
        duration: Total video duration in seconds.

    Returns:
        A tuple of (is_valid, message).
    """
    start = parse_time_to_seconds(start_str)
    end = parse_time_to_seconds(end_str)

    if start is None:
        return False, "Invalid start time format. Use HH:MM:SS."
    if end is None:
        return False, "Invalid end time format. Use HH:MM:SS."
    if start >= end:
        return False, f"Start time ({start_str}) must be before end time ({end_str})."
    if end > duration:
        return False, (
            f"End time ({end_str}) exceeds video duration "
            f"({seconds_to_hms(duration)})."
        )
    if start < 0:
        return False, "Start time cannot be negative."

    logger.debug(
        "Time range validated: %s - %s (duration: %.1fs)", start_str, end_str, duration
    )
    return True, "Valid time range."


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    Sanitize a string for use as a Windows filename.

    Strips illegal characters, collapses whitespace, and truncates to max_length.

    Args:
        name: The raw filename string.
        max_length: Maximum allowed filename length (excluding extension).

    Returns:
        A sanitized filename safe for Windows filesystems.
    """
    if not name:
        return "untitled"

    # Replace illegal characters with underscores
    sanitized = _ILLEGAL_CHARS.sub("_", name)

    # Collapse multiple spaces/underscores
    sanitized = re.sub(r"[_\s]+", " ", sanitized).strip()

    # Remove leading/trailing dots and spaces (Windows restrictions)
    sanitized = sanitized.strip(". ")

    # Truncate
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(". ")

    # Fallback if empty after sanitization
    if not sanitized:
        sanitized = "untitled"

    return sanitized


def suggest_filename(
    title: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
) -> str:
    """
    Generate a suggested output filename from video title and optional timecodes.

    Args:
        title: The video title.
        start_time: Optional start time string (HH:MM:SS).
        end_time: Optional end time string (HH:MM:SS).

    Returns:
        A sanitized suggested filename (without extension).
    """
    parts = [sanitize_filename(title)]

    if start_time and end_time:
        # Replace colons with dots for filename-safe timecodes
        safe_start = start_time.replace(":", ".")
        safe_end = end_time.replace(":", ".")
        parts.append(f"[{safe_start}-{safe_end}]")

    return " ".join(parts)
