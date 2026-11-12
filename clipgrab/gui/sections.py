"""
YouCut — Platform Sections and Navigation Architecture.

Defines the structure for all platform groups, sub-sections, panels shown,
and the universal auto-routing logic mapping detected URLs to their corresponding section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from clipgrab.core.platforms import (
    ContentKind,
    DownloadEngine,
    PlatformConfig,
    detect_platform,
    guess_content_kind,
)


@dataclass
class SectionConfig:
    """Configuration for a specific sub-section in YouCut."""

    id: str  # Unique section ID, e.g. "youtube_video"
    platform_id: str  # Matches PlatformConfig.id, e.g. "youtube"
    title: str  # Display title, e.g. "Video Download"
    icon: str  # Emoji or symbol, e.g. "🎬"
    url_label: str  # Input label, e.g. "Video URL"
    placeholder: str  # Input placeholder text
    example_hint: str  # Empty state guidance hint
    show_quality: bool = True  # Quality/Format selector shown
    show_clip: bool = False  # Clip Range panel shown
    audio_only: bool = False  # Audio-only mode locked on
    clip_focus: bool = False  # Clip Range emphasised
    show_preview: bool = False  # Static image preview shown
    is_experimental: bool = False  # Shows experimental tag
    allow_carousel: bool = False  # Enables carousel picker if multiple items


@dataclass
class PlatformGroup:
    """A collapsible platform group containing multiple sub-sections."""

    id: str
    name: str
    icon: str
    accent_color: str
    shortcut: str
    sections: list[SectionConfig] = field(default_factory=list)
    hidden: bool = False


# ─── YouTube Sections ────────────────────────────────────────────────────────
SECTION_YOUTUBE_VIDEO = SectionConfig(
    id="youtube_video",
    platform_id="youtube",
    title="Video Download",
    icon="🎬",
    url_label="Video URL",
    placeholder="https://www.youtube.com/watch?v=... or youtu.be/...",
    example_hint="Paste any YouTube video, Shorts, or playlist link to download in high quality.",
    show_quality=True,
    show_clip=True,
    audio_only=False,
)

SECTION_YOUTUBE_AUDIO = SectionConfig(
    id="youtube_audio",
    platform_id="youtube",
    title="Audio Download",
    icon="🎵",
    url_label="Audio URL",
    placeholder="https://www.youtube.com/watch?v=...",
    example_hint="Extract pure audio (MP3, M4A, or WAV) with original bitrate.",
    show_quality=True,
    show_clip=False,
    audio_only=True,
)

SECTION_YOUTUBE_CLIP = SectionConfig(
    id="youtube_clip",
    platform_id="youtube",
    title="Clip / Trim",
    icon="✂️",
    url_label="Clip URL",
    placeholder="https://www.youtube.com/watch?v=...",
    example_hint="Set precise Start and End timestamps with interactive dual sliders.",
    show_quality=True,
    show_clip=True,
    audio_only=False,
    clip_focus=True,
)


# ─── Instagram Sections ──────────────────────────────────────────────────────
SECTION_INSTAGRAM_REEL = SectionConfig(
    id="instagram_reel",
    platform_id="instagram",
    title="Reel Download",
    icon="📱",
    url_label="Reel URL",
    placeholder="https://www.instagram.com/reel/...",
    example_hint="Paste an Instagram Reel link. Clip range is supported for trimming clips.",
    show_quality=True,
    show_clip=True,
    audio_only=False,
)

SECTION_INSTAGRAM_POST = SectionConfig(
    id="instagram_post",
    platform_id="instagram",
    title="Post Download",
    icon="🖼️",
    url_label="Post URL",
    placeholder="https://www.instagram.com/p/...",
    example_hint="Paste an Instagram Post or Carousel link to download videos or photos.",
    show_quality=True,
    show_clip=False,
    audio_only=False,
    allow_carousel=True,
)

SECTION_INSTAGRAM_STORY = SectionConfig(
    id="instagram_story",
    platform_id="instagram",
    title="Story Download",
    icon="⏱️",
    url_label="Story URL",
    placeholder="https://www.instagram.com/stories/.../...",
    example_hint="Paste an Instagram Story link (public accounts supported).",
    show_quality=True,
    show_clip=False,
    audio_only=False,
)

SECTION_INSTAGRAM_AUDIO = SectionConfig(
    id="instagram_audio",
    platform_id="instagram",
    title="Audio Download",
    icon="🎵",
    url_label="Audio URL",
    placeholder="https://www.instagram.com/reel/...",
    example_hint="Extract audio track from any Instagram Reel or video.",
    show_quality=True,
    show_clip=False,
    audio_only=True,
)


# ─── Facebook Sections ───────────────────────────────────────────────────────
SECTION_FACEBOOK_VIDEO = SectionConfig(
    id="facebook_video",
    platform_id="facebook",
    title="Video Download",
    icon="📹",
    url_label="Video URL",
    placeholder="https://www.facebook.com/watch/?v=... or fb.watch/...",
    example_hint="Download public Facebook Watch or page videos in top quality.",
    show_quality=True,
    show_clip=True,
    audio_only=False,
)

SECTION_FACEBOOK_REEL = SectionConfig(
    id="facebook_reel",
    platform_id="facebook",
    title="Reel Download",
    icon="📱",
    url_label="Reel URL",
    placeholder="https://www.facebook.com/reel/...",
    example_hint="Paste a Facebook Reel link with trimming support.",
    show_quality=True,
    show_clip=True,
    audio_only=False,
)

SECTION_FACEBOOK_POST = SectionConfig(
    id="facebook_post",
    platform_id="facebook",
    title="Post Download",
    icon="📝",
    url_label="Post URL",
    placeholder="https://www.facebook.com/.../posts/...",
    example_hint="Download media attached to Facebook posts (experimental).",
    show_quality=True,
    show_clip=False,
    audio_only=False,
    is_experimental=True,
)


# ─── Pinterest Sections ──────────────────────────────────────────────────────
SECTION_PINTEREST_IMAGE = SectionConfig(
    id="pinterest_image",
    platform_id="pinterest",
    title="Image Download",
    icon="📌",
    url_label="Pin URL",
    placeholder="https://www.pinterest.com/pin/... or pin.it/...",
    example_hint="Download high-resolution image pins from Pinterest.",
    show_quality=False,
    show_clip=False,
    show_preview=True,
)

SECTION_PINTEREST_VIDEO = SectionConfig(
    id="pinterest_video",
    platform_id="pinterest",
    title="Video Download",
    icon="🎥",
    url_label="Pin URL",
    placeholder="https://www.pinterest.com/pin/...",
    example_hint="Download video pins from Pinterest with audio.",
    show_quality=True,
    show_clip=False,
    show_preview=True,
)


# ─── X / Twitter Sections ────────────────────────────────────────────────────
SECTION_X_VIDEO = SectionConfig(
    id="x_video",
    platform_id="twitter_x",
    title="Video Download",
    icon="🎬",
    url_label="Tweet / Post URL",
    placeholder="https://x.com/.../status/... or twitter.com/...",
    example_hint="Download video clips from X (Twitter) posts in MP4.",
    show_quality=True,
    show_clip=False,
)

SECTION_X_GIF = SectionConfig(
    id="x_gif",
    platform_id="twitter_x",
    title="GIF Download",
    icon="🎞️",
    url_label="GIF Tweet URL",
    placeholder="https://x.com/.../status/...",
    example_hint="Download animated GIFs from X (Twitter) as loopable video files.",
    show_quality=True,
    show_clip=False,
)

SECTION_X_POST = SectionConfig(
    id="x_post",
    platform_id="twitter_x",
    title="Post Download",
    icon="💬",
    url_label="Post URL",
    placeholder="https://x.com/.../status/...",
    example_hint="Download media from standard X posts.",
    show_quality=True,
    show_clip=False,
)


# ─── Registry of All Platform Groups ─────────────────────────────────────────
PLATFORM_GROUPS: list[PlatformGroup] = [
    PlatformGroup(
        id="youtube",
        name="YouTube",
        icon="▶️",
        accent_color="#E63946",
        shortcut="Ctrl+1",
        sections=[SECTION_YOUTUBE_VIDEO, SECTION_YOUTUBE_AUDIO, SECTION_YOUTUBE_CLIP],
    ),
    PlatformGroup(
        id="instagram",
        name="Instagram",
        icon="📸",
        accent_color="#E1306C",
        shortcut="Ctrl+2",
        sections=[
            SECTION_INSTAGRAM_REEL,
            SECTION_INSTAGRAM_POST,
            SECTION_INSTAGRAM_STORY,
            SECTION_INSTAGRAM_AUDIO,
        ],
    ),
    PlatformGroup(
        id="facebook",
        name="Facebook",
        icon="📘",
        accent_color="#1877F2",
        shortcut="Ctrl+3",
        sections=[SECTION_FACEBOOK_VIDEO, SECTION_FACEBOOK_REEL, SECTION_FACEBOOK_POST],
    ),
    PlatformGroup(
        id="pinterest",
        name="Pinterest",
        icon="📌",
        accent_color="#E60023",
        shortcut="Ctrl+4",
        sections=[SECTION_PINTEREST_IMAGE, SECTION_PINTEREST_VIDEO],
        hidden=True,
    ),
    PlatformGroup(
        id="twitter_x",
        name="X / Twitter",
        icon="𝕏",
        accent_color="#1DA1F2",
        shortcut="Ctrl+5",
        sections=[SECTION_X_VIDEO, SECTION_X_GIF, SECTION_X_POST],
        hidden=True,
    ),
]

VISIBLE_PLATFORM_GROUPS: list[PlatformGroup] = [
    g for g in PLATFORM_GROUPS if not g.hidden
]

ALL_SECTIONS: dict[str, SectionConfig] = {
    s.id: s for group in PLATFORM_GROUPS for s in group.sections
}


def get_section_by_id(section_id: str) -> SectionConfig:
    """Retrieve section configuration by ID, fallback to YouTube Video."""
    return ALL_SECTIONS.get(section_id, SECTION_YOUTUBE_VIDEO)


def route_url_to_section(url: str) -> tuple[SectionConfig, str]:
    """
    Examines a URL and determines the best target sub-section and detection label.

    Returns:
        (matching_section_config, detection_description)
    """
    if not url:
        return SECTION_YOUTUBE_VIDEO, "YouTube Video"

    url_str = url.strip()
    platform = detect_platform(url_str)
    url_lower = url_str.lower()

    if platform.id == "youtube":
        if "/shorts/" in url_lower:
            return SECTION_YOUTUBE_VIDEO, "YouTube Shorts"
        return SECTION_YOUTUBE_VIDEO, "YouTube Video"

    if platform.id == "instagram":
        if "/reel/" in url_lower or "/reels/" in url_lower:
            return SECTION_INSTAGRAM_REEL, "Instagram Reel"
        if "/stories/" in url_lower:
            return SECTION_INSTAGRAM_STORY, "Instagram Story"
        if "/p/" in url_lower or "/tv/" in url_lower:
            return SECTION_INSTAGRAM_POST, "Instagram Post"
        return SECTION_INSTAGRAM_REEL, "Instagram Content"

    if platform.id == "facebook":
        if "/reel/" in url_lower or "/reels/" in url_lower or "/share/r/" in url_lower:
            return SECTION_FACEBOOK_REEL, "Facebook Reel"
        if "/share/p/" in url_lower or "/posts/" in url_lower or "permalink.php" in url_lower:
            return SECTION_FACEBOOK_POST, "Facebook Post"
        if "/watch" in url_lower or "/videos/" in url_lower or "fb.watch" in url_lower or "/share/v/" in url_lower:
            return SECTION_FACEBOOK_VIDEO, "Facebook Video"
        return SECTION_FACEBOOK_VIDEO, "Facebook Video"

    if platform.id == "pinterest":
        # By default route to image download; if fetched metadata indicates video,
        # the section page switches to Video Download automatically.
        return SECTION_PINTEREST_IMAGE, "Pinterest Pin"

    if platform.id == "twitter_x":
        return SECTION_X_VIDEO, "X / Twitter Post"

    # Default fallback for unrecognized
    return SECTION_YOUTUBE_VIDEO, "Unrecognized Link"
