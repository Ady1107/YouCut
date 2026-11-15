"""
YouCut — Application settings (JSON config).

Manages user preferences persisted as a JSON file at ~/.youcut/config.json
(or $YOUCUT_HOME/config.json when isolated).
Handles load/save with defaults, type safety, and graceful handling of
corrupted or missing config files. Automatically migrates from legacy ~/.clipgrab if present.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from clipgrab.core.logger import get_logger

logger = get_logger("settings")


def get_youcut_dir() -> Path:
    """
    Get the base directory for YouCut data (~/.youcut by default).

    If the YOUCUT_HOME environment variable is set, that directory is used instead.
    This guarantees that tests and harnesses run in an isolated environment without
    touching the user's real configuration or history.
    """
    env_home = os.environ.get("YOUCUT_HOME")
    if env_home:
        d = Path(env_home).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    return Path.home() / ".youcut"


def _get_config_dir() -> Path:
    """Get the config directory path, migrating legacy ~/.clipgrab if needed."""
    env_home = os.environ.get("YOUCUT_HOME")
    if env_home:
        config_dir = Path(env_home).resolve()
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    config_dir = Path.home() / ".youcut"
    legacy_dir = Path.home() / ".clipgrab"

    # Migrate from legacy ~/.clipgrab to ~/.youcut if user had existing data
    if not config_dir.is_dir() and legacy_dir.is_dir():
        try:
            shutil.copytree(str(legacy_dir), str(config_dir))
            logger.info("Migrated user data from %s to %s", legacy_dir, config_dir)
        except Exception as e:
            logger.warning("Could not auto-migrate legacy data: %s", e)

    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _get_config_path() -> Path:
    """Get the full path to the config JSON file."""
    return _get_config_dir() / "config.json"


def _get_default_downloads_dir() -> str:
    """Get the Windows/OS Downloads directory as default save path."""
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return str(downloads)
    return str(Path.home())


@dataclass
class AppSettings:
    """
    Application settings with typed fields and defaults.

    Persisted as JSON at ~/.youcut/config.json (or $YOUCUT_HOME/config.json).
    """

    # Output preferences
    last_output_folder: str = ""
    default_merge_format: str = "mp4"
    default_audio_format: str = "mp3"

    # Theme ("dark", "light", or "system")
    theme: str = "dark"

    # Download performance
    concurrent_fragments: int = 16
    default_video_format: str = "mp4"   # "mp4", "mkv", "webm", etc.
    default_trim_mode: str = "auto"     # "auto", "fast", "exact"

    # Update settings
    auto_update_app: bool = True
    auto_update_ytdlp: bool = True
    auto_update_ffmpeg: bool = False
    last_update_check: str = ""  # ISO timestamp
    last_checked_all: str = ""   # Display string or ISO timestamp
    last_checked_iso: str = ""   # ISO timestamp of last successful real check
    last_ytdlp_version: str = ""
    last_ffmpeg_version: str = ""

    # Component update cache (last_check_time, latest_version, size, release_notes_url, download_url, last_toast_version)
    update_cache: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "youcut": {"last_check_time": "", "latest_version": "", "size": "", "release_notes_url": "", "download_url": "", "last_toast_version": ""},
        "ytdlp": {"last_check_time": "", "latest_version": "", "size": "", "release_notes_url": "", "download_url": "", "last_toast_version": ""},
        "ffmpeg": {"last_check_time": "", "latest_version": "", "size": "", "release_notes_url": "", "download_url": "", "last_toast_version": ""},
    })

    # App update settings (empty = use default from clipgrab.version)
    app_update_url: str = ""

    # Window geometry
    window_x: int = 100
    window_y: int = 100
    window_width: int = 1100
    window_height: int = 750
    window_maximized: bool = False

    # Thumbnail cache directory
    thumbnail_cache_dir: str = ""

    # UI navigation & section state
    sidebar_collapsed: bool = False
    expanded_groups: list[str] = field(default_factory=lambda: ["youtube", "instagram", "facebook"])
    last_section: str = "home"
    section_preferences: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Set dynamic defaults after initialization."""
        default_dl = _get_default_downloads_dir()
        if (
            not self.last_output_folder
            or "test_out" in str(self.last_output_folder)
            or "scratch" in str(self.last_output_folder)
            or not os.path.isdir(str(self.last_output_folder))
        ):
            self.last_output_folder = default_dl

        if not self.thumbnail_cache_dir:
            cache_dir = _get_config_dir() / "thumbnails"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.thumbnail_cache_dir = str(cache_dir)

    @classmethod
    def load(cls) -> AppSettings:
        """
        Load settings from the config file.

        If the file doesn't exist or is corrupted, returns default settings.

        Returns:
            An AppSettings instance with loaded or default values.
        """
        config_path = _get_config_path()

        if not config_path.is_file():
            logger.info("Config file not found, using defaults: %s", config_path)
            settings = cls()
            settings.save()
            return settings

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not isinstance(data, dict):
                logger.warning("Config file contains non-dict data, using defaults")
                return cls()

            # One-time migration: drop leftover legacy authentication keys if present
            legacy_auth_keys = (
                "browser_for_cookies",
                "cookies_file_path",
                "cookie_browser",
                "cookies_path",
            )
            has_legacy = any(k in data for k in legacy_auth_keys)
            if has_legacy:
                for k in legacy_auth_keys:
                    data.pop(k, None)
                try:
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                    logger.info("Migrated config.json: removed legacy authentication keys")
                except Exception as e:
                    logger.warning("Could not persist cleaned config.json: %s", e)

            # Create settings with loaded values, falling back to defaults
            settings = cls()
            for key, value in data.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

            # Ensure save folder is not pointing to scratch/test_out or invalid dir
            default_dl = _get_default_downloads_dir()
            if (
                not settings.last_output_folder
                or "test_out" in settings.last_output_folder
                or "scratch" in settings.last_output_folder
                or not os.path.isdir(settings.last_output_folder)
            ):
                logger.info("Resetting invalid/scratch save folder to %s", default_dl)
                settings.last_output_folder = default_dl
                settings.save()

            logger.info("Settings loaded from %s", config_path)
            return settings

        except json.JSONDecodeError as e:
            logger.error("Config file corrupted (JSON parse error): %s", e)
            return cls()
        except PermissionError as e:
            logger.error("Cannot read config file (permission denied): %s", e)
            return cls()
        except OSError as e:
            logger.error("Cannot read config file: %s", e)
            return cls()

    def save(self) -> bool:
        """
        Save current settings to the config file.

        Returns:
            True if saved successfully, False otherwise.
        """
        config_path = _get_config_path()

        try:
            data = asdict(self)

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.debug("Settings saved to %s", config_path)
            return True

        except PermissionError as e:
            logger.error("Cannot write config file (permission denied): %s", e)
            return False
        except OSError as e:
            logger.error("Cannot write config file: %s", e)
            return False

    def update(self, **kwargs: Any) -> None:
        """
        Update one or more settings and save immediately.

        Args:
            **kwargs: Setting name=value pairs to update.
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                logger.warning("Unknown setting: %s", key)

        self.save()
