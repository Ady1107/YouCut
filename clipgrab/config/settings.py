"""
YouCut — Application settings (JSON config).

Manages user preferences persisted as a JSON file at ~/.youcut/config.json.
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


def _get_config_dir() -> Path:
    """Get the config directory path, migrating legacy ~/.clipgrab if needed."""
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


@dataclass
class AppSettings:
    """
    Application settings with typed fields and defaults.

    Persisted as JSON at ~/.youcut/config.json.
    """

    # Output preferences
    last_output_folder: str = ""
    default_merge_format: str = "mp4"
    default_audio_format: str = "mp3"

    # Theme
    theme: str = "dark"  # "dark" or "light"

    # Download performance
    concurrent_fragments: int = 16

    # yt-dlp update settings
    auto_update_ytdlp: bool = True
    last_update_check: str = ""  # ISO timestamp
    last_ytdlp_version: str = ""

    # App update settings (empty = use default from clipgrab.version)
    app_update_url: str = ""

    # Window geometry
    window_x: int = 100
    window_y: int = 100
    window_width: int = 1100
    window_height: int = 750

    # Thumbnail cache directory
    thumbnail_cache_dir: str = ""

    def __post_init__(self) -> None:
        """Set dynamic defaults after initialization."""
        if not self.last_output_folder:
            # Default to user's Downloads folder
            downloads = Path.home() / "Downloads"
            if downloads.is_dir():
                self.last_output_folder = str(downloads)
            else:
                self.last_output_folder = str(Path.home())

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

            # Create settings with loaded values, falling back to defaults
            settings = cls()
            for key, value in data.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)

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
