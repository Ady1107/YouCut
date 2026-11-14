"""
YouCut — Multi-platform configuration and URL detection.

Defines PlatformConfig — a descriptor that maps a social/video platform to:
  - A display name and emoji icon
  - URL regex patterns for detection
  - Supported content kinds (VIDEO, REEL, IMAGE, etc.)
  - Which download engine to use (YTDLP or PINTEREST_HTTP)

URL detection works by testing each PlatformConfig's patterns in order.
The first match wins. If nothing matches, UNSUPPORTED is returned.

NOTE: The existing YouTube validator.validate_youtube_url() is kept unchanged
and still used in url_input.py; this module augments rather than replaces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from clipgrab.core.logger import get_logger

logger = get_logger("platforms")


# ── Enums ────────────────────────────────────────────────────────────────────

class ContentKind(Enum):
    """Types of downloadable content a platform can offer."""
    VIDEO    = auto()   # Standard video
    REEL     = auto()   # Short-form vertical video (Instagram Reel, FB Reel)
    STORY    = auto()   # Ephemeral short-form (Instagram Story)
    POST     = auto()   # Generic post (may contain image or video)
    IMAGE    = auto()   # Static image
    GIF      = auto()   # Animated GIF (Twitter)
    PLAYLIST = auto()   # Playlist / album
    UNKNOWN  = auto()   # Could not be determined from URL alone


class DownloadEngine(Enum):
    """Which download backend handles this platform."""
    YTDLP          = "ytdlp"          # Shared yt-dlp QProcess worker (YouTube, IG, FB, X)
    PINTEREST_HTTP = "pinterest_http" # Lightweight HTTP scraper (Pinterest)
    UNSUPPORTED    = "unsupported"    # Not a recognised platform


# ── PlatformConfig ────────────────────────────────────────────────────────────

@dataclass
class PlatformConfig:
    """
    Descriptor for one supported (or partially supported) social platform.

    Fields
    ------
    id              : Short machine-readable key, e.g. "youtube"
    display_name    : Human-readable name shown in the UI, e.g. "YouTube"
    icon            : Single emoji used as a compact badge
    url_patterns    : Compiled regex list. A URL is accepted if ANY pattern matches.
    content_kinds   : Set of ContentKind values this platform supports.
    engine          : Which download engine to use.
    notes           : Optional developer notes (e.g. fragility warnings).
    """
    id: str
    display_name: str
    icon: str
    url_patterns: list[re.Pattern[str]]
    content_kinds: set[ContentKind]
    engine: DownloadEngine
    notes: str = ""


# ── Platform definitions ──────────────────────────────────────────────────────

def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    """Compile a list of regex strings with IGNORECASE."""
    return [re.compile(p, re.IGNORECASE) for p in patterns]


PLATFORM_YOUTUBE = PlatformConfig(
    id="youtube",
    display_name="YouTube",
    icon="▶️",
    url_patterns=_compile([
        # Standard watch
        r"^(?:https?://)?(?:www\.)?youtube\.com/watch\?.*v=[\w-]{11}",
        # Short URLs
        r"^(?:https?://)?youtu\.be/[\w-]{11}",
        # Embed
        r"^(?:https?://)?(?:www\.)?youtube\.com/embed/[\w-]{11}",
        # Shorts
        r"^(?:https?://)?(?:www\.)?youtube\.com/shorts/[\w-]{11}",
        # Live
        r"^(?:https?://)?(?:www\.)?youtube\.com/live/[\w-]{11}",
        # Playlist
        r"^(?:https?://)?(?:www\.)?youtube\.com/playlist\?.*list=[\w-]+",
        # YouTube Music
        r"^(?:https?://)?music\.youtube\.com/watch\?.*v=[\w-]{11}",
    ]),
    content_kinds={ContentKind.VIDEO, ContentKind.PLAYLIST, ContentKind.REEL},
    engine=DownloadEngine.YTDLP,
)

PLATFORM_INSTAGRAM = PlatformConfig(
    id="instagram",
    display_name="Instagram",
    icon="📸",
    url_patterns=_compile([
        # Reels
        r"^(?:https?://)?(?:www\.)?instagram\.com/reel/[\w-]+",
        # Static posts (single image or carousel)
        r"^(?:https?://)?(?:www\.)?instagram\.com/p/[\w-]+",
        # IGTV
        r"^(?:https?://)?(?:www\.)?instagram\.com/tv/[\w-]+",
        # Stories
        r"^(?:https?://)?(?:www\.)?instagram\.com/stories/[\w.]+/\d+",
        # Profile page (not downloadable but detectable)
        r"^(?:https?://)?(?:www\.)?instagram\.com/[\w.]+/?$",
    ]),
    content_kinds={ContentKind.REEL, ContentKind.POST, ContentKind.STORY, ContentKind.VIDEO, ContentKind.IMAGE},
    engine=DownloadEngine.YTDLP,
    notes=(
        "Private/login-gated content returns a login-required error from yt-dlp. "
        "Carousel posts appear as an entries list in the info dict. "
        "Stories have no duration worth clipping."
    ),
)

PLATFORM_FACEBOOK = PlatformConfig(
    id="facebook",
    display_name="Facebook",
    icon="👥",
    url_patterns=_compile([
        # facebook.com/watch
        r"^(?:https?://)?(?:www\.)?facebook\.com/watch(?:/\?v=\d+|/\?.*)?",
        # facebook.com/reel
        r"^(?:https?://)?(?:www\.)?facebook\.com/reel/\d+",
        # facebook.com/<page>/videos/<id>
        r"^(?:https?://)?(?:www\.)?facebook\.com/[\w.]+/videos/\d+",
        # facebook.com/video.php
        r"^(?:https?://)?(?:www\.)?facebook\.com/video\.php\?",
        # m.facebook.com (mobile)
        r"^(?:https?://)?m\.facebook\.com/[\w.]+/videos/\d+",
        # fb.watch short links
        r"^(?:https?://)?fb\.watch/[\w-]+",
        # facebook.com/share/v/<hash> (newer share links)
        r"^(?:https?://)?(?:www\.)?facebook\.com/share/[rv]/[\w-]+",
        # Reels in /stories/
        r"^(?:https?://)?(?:www\.)?facebook\.com/stories/\d+/\w+",
    ]),
    content_kinds={ContentKind.VIDEO, ContentKind.REEL},
    engine=DownloadEngine.YTDLP,
    notes=(
        "Facebook video pages are inconsistent between mobile/desktop URL formats. "
        "Public videos on public pages work; login-gated or geo-restricted content "
        "may return 403/401. yt-dlp error is surfaced verbatim — no silent fallthrough."
    ),
)

PLATFORM_TWITTER_X = PlatformConfig(
    id="twitter_x",
    display_name="X / Twitter",
    icon="🐦",
    url_patterns=_compile([
        # twitter.com status URLs
        r"^(?:https?://)?(?:www\.)?twitter\.com/\w+/status/\d+",
        # x.com status URLs
        r"^(?:https?://)?(?:www\.)?x\.com/\w+/status/\d+",
        # t.co short links (common in shares)
        r"^(?:https?://)?t\.co/[\w-]+",
    ]),
    content_kinds={ContentKind.VIDEO, ContentKind.GIF},
    engine=DownloadEngine.YTDLP,
    notes="Video tweets and GIFs only. Spaces/audio-only tweets are not supported.",
)

PLATFORM_PINTEREST = PlatformConfig(
    id="pinterest",
    display_name="Pinterest",
    icon="📌",
    url_patterns=_compile([
        # Full pin URLs
        r"^(?:https?://)?(?:www\.)?pinterest\.com/pin/\d+",
        r"^(?:https?://)?(?:[\w-]+\.)?pinterest\.com/pin/\d+",
        # Short pin.it links
        r"^(?:https?://)?pin\.it/[\w-]+",
        # Country-specific domains (e.g. pinterest.co.uk, pinterest.de)
        r"^(?:https?://)?(?:www\.)?pinterest\.\w{2,3}/pin/\d+",
    ]),
    content_kinds={ContentKind.IMAGE, ContentKind.VIDEO},
    engine=DownloadEngine.PINTEREST_HTTP,
    notes=(
        "⚠️  Pinterest uses a dedicated HTTP scraper (NOT yt-dlp). "
        "This extractor is the MOST LIKELY feature to break if Pinterest "
        "changes its page structure or embedded JSON schema. "
        "If extraction fails, a clear 'Pinterest extraction is currently broken' "
        "message is shown rather than crashing."
    ),
)

# Ordered list — detection tests them in this sequence.
# More specific platforms first; YouTube last (broad catch).
ALL_PLATFORMS: list[PlatformConfig] = [
    PLATFORM_INSTAGRAM,
    PLATFORM_FACEBOOK,
    PLATFORM_TWITTER_X,
    PLATFORM_PINTEREST,
    PLATFORM_YOUTUBE,   # Intentionally last: YouTube patterns are broad
]

# Sentinel returned when no platform matches
PLATFORM_UNSUPPORTED = PlatformConfig(
    id="unsupported",
    display_name="Unsupported",
    icon="❓",
    url_patterns=[],
    content_kinds=set(),
    engine=DownloadEngine.UNSUPPORTED,
)


# ── Detection API ─────────────────────────────────────────────────────────────

def detect_platform(url: str) -> PlatformConfig:
    """
    Identify which platform a URL belongs to.

    Iterates ALL_PLATFORMS in order and returns the first PlatformConfig whose
    url_patterns list contains at least one match.  Returns PLATFORM_UNSUPPORTED
    if nothing matches.

    Args:
        url: The raw URL string (will be stripped of whitespace).

    Returns:
        The matching PlatformConfig, or PLATFORM_UNSUPPORTED.
    """
    if not url:
        return PLATFORM_UNSUPPORTED

    url = url.strip()

    for platform in ALL_PLATFORMS:
        for pattern in platform.url_patterns:
            if pattern.match(url):
                logger.debug(
                    "Platform detected: %s  (url=%s)",
                    platform.display_name,
                    url[:80],
                )
                return platform

    logger.debug("No platform matched for url: %s", url[:80])
    return PLATFORM_UNSUPPORTED


def is_supported(url: str) -> bool:
    """Return True if the URL matches any known platform."""
    return detect_platform(url).engine != DownloadEngine.UNSUPPORTED


def is_ytdlp_platform(platform: PlatformConfig) -> bool:
    """Return True if this platform uses the shared yt-dlp engine."""
    return platform.engine == DownloadEngine.YTDLP


def guess_content_kind(url: str, platform: Optional[PlatformConfig] = None) -> ContentKind:
    """
    Make a best-effort guess at the content kind from URL structure alone.

    This is heuristic only; the authoritative kind comes from the yt-dlp info dict
    after a real fetch. Used for early UI decisions (e.g. whether to show clip range).

    Args:
        url: The URL string.
        platform: Optionally the already-detected PlatformConfig.

    Returns:
        A ContentKind enum value.
    """
    if platform is None:
        platform = detect_platform(url)

    url_lower = url.lower()

    if platform.id == "youtube":
        if "/shorts/" in url_lower:
            return ContentKind.REEL
        if "playlist" in url_lower and "list=" in url_lower:
            return ContentKind.PLAYLIST
        return ContentKind.VIDEO

    if platform.id == "instagram":
        if "/reel/" in url_lower:
            return ContentKind.REEL
        if "/stories/" in url_lower:
            return ContentKind.STORY
        if "/p/" in url_lower:
            return ContentKind.POST  # May be image or video — needs fetch to confirm
        if "/tv/" in url_lower:
            return ContentKind.VIDEO

    if platform.id == "facebook":
        if "/reel/" in url_lower:
            return ContentKind.REEL
        return ContentKind.VIDEO

    if platform.id == "twitter_x":
        return ContentKind.VIDEO  # Could be GIF, but GIF = silent video in yt-dlp

    if platform.id == "pinterest":
        return ContentKind.IMAGE  # Assume image; video is confirmed after fetch

    return ContentKind.UNKNOWN
