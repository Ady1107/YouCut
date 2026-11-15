"""
YouCut — Update management system.

Two independent update channels:
  1. YouCut app updates — queries GitHub Releases API directly for the latest release.
     When a new app version is found, it notifies the user in the
     Settings panel with a prominent banner and direct installer download link.
  2. yt-dlp binary updates — checks PyPI and self-updates the bundled
     yt-dlp.exe in place using 'yt-dlp -U'.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import requests
from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from clipgrab.core.ffmpeg_utils import (
    get_ffmpeg_path,
    get_ytdlp_path,
    invalidate_ffmpeg_cache,
    invalidate_ytdlp_cache,
    verify_ffmpeg,
    verify_ytdlp,
)
from clipgrab.core.logger import get_logger
from clipgrab.version import APP_VERSION, VERSION_CHECK_URL

if TYPE_CHECKING:
    from clipgrab.config.settings import AppSettings

logger = get_logger("updater")

# Cooldown intervals in seconds (per spec)
COOLDOWN_YOUCUT_SECONDS = 24 * 3600         # 24 hours
COOLDOWN_YTDLP_SECONDS = 12 * 3600          # 12 hours
COOLDOWN_FFMPEG_SECONDS = 7 * 86400         # 7 days
RETRY_FAILED_NETWORK_SECONDS = 3600         # 1 hour
PERIODIC_CHECK_INTERVAL_MS = 6 * 3600 * 1000  # 6 hours

# Subprocess flags — hide console window on Windows
_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


@dataclass
class ComponentState:
    """State of an updatable component (YouCut, yt-dlp, FFmpeg)."""

    component: str                          # "youcut", "ytdlp", "ffmpeg"
    status: str = "up_to_date"              # "up_to_date", "checking", "available", "downloading", "installing", "error"
    current_version: str = ""
    latest_version: str = ""
    size_str: str = ""
    release_notes_url: str = ""
    download_url: str = ""
    error_message: str = ""
    progress_percent: int = 0
    progress_speed: str = ""



# ── Version helpers ──────────────────────────────────────────────────────────

def _parse_version(version_str: str) -> tuple[int, ...]:
    """Fallback version parser converting 'v1.2.3' or '1.2.3' into a comparable tuple."""
    try:
        clean = str(version_str).strip().lstrip("vV")
        parts = clean.split("-")[0].split("+")[0].split(".")
        return tuple(int(x) for x in parts if x.isdigit())
    except (ValueError, AttributeError):
        return (0,)


def _is_newer(remote: str, local: str) -> bool:
    """Return True if remote version is strictly newer than local version using semver comparison."""
    try:
        from packaging import version
        remote_clean = str(remote).strip().lstrip("vV")
        local_clean = str(local).strip().lstrip("vV")
        return version.parse(remote_clean) > version.parse(local_clean)
    except Exception:
        return _parse_version(remote) > _parse_version(local)


def _is_installed_ge_latest(installed: str, latest: str) -> bool:
    """Return True if installed version is greater than or equal to latest version (packaging.version.parse)."""
    if not installed or not latest or installed == "unknown":
        return False
    try:
        from packaging import version
        inst_clean = str(installed).strip().lstrip("vV")
        lat_clean = str(latest).strip().lstrip("vV")
        return version.parse(inst_clean) >= version.parse(lat_clean)
    except Exception:
        inst_clean = str(installed).strip().lstrip("vV")
        lat_clean = str(latest).strip().lstrip("vV")
        return (inst_clean == lat_clean) or (lat_clean in inst_clean)


def format_last_checked(dt_or_iso: Any) -> str:
    """Format check time as 'Last checked today, HH:MM', 'Last checked yesterday, HH:MM', or 'Last checked Mon DD, YYYY'."""
    if not dt_or_iso:
        return "Not checked yet"
    if isinstance(dt_or_iso, str):
        try:
            dt = datetime.fromisoformat(dt_or_iso)
        except Exception:
            return dt_or_iso if dt_or_iso.startswith("Last checked") else f"Last checked {dt_or_iso}"
    elif isinstance(dt_or_iso, datetime):
        dt = dt_or_iso
    else:
        return "Not checked yet"

    now = datetime.now()
    if dt.tzinfo is not None:
        dt = dt.astimezone()
        now = now.astimezone()

    today = now.date()
    check_date = dt.date()
    time_str = dt.strftime("%H:%M")
    if check_date == today:
        return f"Last checked today, {time_str}"
    elif check_date == today - timedelta(days=1):
        return f"Last checked yesterday, {time_str}"
    else:
        return f"Last checked {dt.strftime('%b %d, %Y')}"


# ── App Update Checker ───────────────────────────────────────────────────────

def check_app_update(check_url: str = "", current_version: str = "") -> Optional[dict]:
    """
    Query GitHub Releases API directly for the latest release and compare against APP_VERSION.

    Returns:
        A dict with keys {status, version, release_notes, download_url} or error info.
    """
    url = check_url.strip() if check_url else VERSION_CHECK_URL
    local_ver = current_version.strip() if current_version else APP_VERSION
    if not url or "YOUR_USERNAME" in url:
        logger.debug("App update URL not configured — skipping check")
        return None

    try:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "YouCut-App",
        }
        resp = requests.get(url, timeout=20, headers=headers)
        if resp.status_code == 404:
            logger.debug("No release found at endpoint %s", url)
            return {"status": "up_to_date", "version": local_ver}
        resp.raise_for_status()
        data = resp.json()

        # Extract tag and clean version string
        raw_tag = data.get("tag_name", "").strip()
        remote_version = raw_tag.lstrip("vV") if raw_tag else data.get("version", "").strip()
        if not remote_version:
            logger.debug("No valid version tag found in release response")
            return {"status": "up_to_date", "version": local_ver}

        if _is_newer(remote_version, local_ver):
            logger.info(
                "YouCut app update available: %s → %s", local_ver, remote_version
            )

            # Locate YouCut-Setup-*.exe asset download URL
            download_url = data.get("html_url", "")  # fallback to GitHub Release page
            assets = data.get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                if name.startswith("YouCut-Setup-") and name.endswith(".exe"):
                    download_url = asset.get("browser_download_url", download_url)
                    break

            if not download_url and "download_url" in data:
                download_url = data["download_url"]

            notes = data.get("body", "").strip() or data.get("release_notes", "").strip() or f"YouCut {raw_tag} is now available."

            return {
                "status": "available",
                "version": remote_version,
                "release_notes": notes,
                "download_url": download_url,
            }

        logger.info("YouCut is up to date (current: v%s, latest: v%s)", APP_VERSION, remote_version)
        return {"status": "up_to_date", "version": remote_version}
    except requests.ConnectionError as e:
        logger.debug("App update check failed: no internet: %s", e)
        return {"status": "error", "error": "No internet connection"}
    except requests.Timeout as e:
        logger.debug("App update check timed out: %s", e)
        return {"status": "error", "error": "Connection timed out"}
    except Exception as e:
        logger.debug("App update check error: %s", e)
        return {"status": "error", "error": str(e)}


# ── yt-dlp helpers ───────────────────────────────────────────────────────────

def get_installed_ytdlp_version() -> str:
    """
    Get the currently installed yt-dlp version by running yt-dlp.exe --version.

    Returns:
        Version string like '2024.12.23', or 'unknown' if the binary can't run.
    """
    try:
        ytdlp_path = get_ytdlp_path()
        result = subprocess.run(
            [ytdlp_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_CREATION_FLAGS,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"


def get_latest_pypi_version() -> Optional[str]:
    """
    Fetch the latest yt-dlp version from PyPI JSON API.

    Returns:
        The latest version string, or None if the check failed.
    """
    try:
        resp = requests.get(
            "https://pypi.org/pypi/yt-dlp/json",
            timeout=10,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("info", {}).get("version")
    except requests.ConnectionError:
        logger.debug("PyPI check failed: no internet connection")
        return None
    except requests.Timeout:
        logger.debug("PyPI check timed out")
        return None
    except Exception as e:
        logger.debug("PyPI check failed: %s", e)
        return None


# ── FFmpeg helpers ───────────────────────────────────────────────────────────

def parse_ffmpeg_installed_version(v_str: str) -> tuple[Optional[int], Optional[str]]:
    """
    Parse commit number and date from installed FFmpeg version string.
    Example: 'N-127579-g5cb9fcd211-20261016' -> (127579, '2026-10-16')
    """
    if not v_str or v_str == "unknown":
        return None, None
    commit_match = re.search(r"N-(\d+)", v_str, re.IGNORECASE)
    commit_num = int(commit_match.group(1)) if commit_match else None

    # Date match: 8 digits like 20261016 or YYYY-MM-DD
    date_match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", v_str)
    date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}" if date_match else None
    return commit_num, date_str


def parse_ffmpeg_remote_version(release_data: dict, asset_name: str) -> tuple[Optional[int], Optional[str], str]:
    """
    Extract commit number, build date, and human-readable version string from release metadata.
    Never returns the word 'latest'.
    """
    name = release_data.get("name", "")
    body = release_data.get("body", "")
    tag = release_data.get("tag_name", "")
    published_at = release_data.get("published_at", "")

    # 1. Look for commit number across name, asset_name, body, tag
    commit_num: Optional[int] = None
    for text in (asset_name, name, tag, body):
        m = re.search(r"N-(\d+)", text, re.IGNORECASE)
        if m:
            commit_num = int(m.group(1))
            break

    # 2. Look for date in published_at, name, asset_name, tag
    date_str: Optional[str] = None
    for text in (published_at, name, asset_name, tag):
        m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", text)
        if m:
            date_str = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            break

    # 3. Construct display version (never "latest")
    if commit_num and date_str:
        display = f"N-{commit_num} · {date_str}"
    elif commit_num:
        display = f"N-{commit_num}"
    elif date_str:
        display = date_str
    else:
        # Fallback without the word latest
        clean_tag = tag.replace("latest", "").replace("autobuild-", "").strip(" -_")
        display = clean_tag if clean_tag else "New build"

    return commit_num, date_str, display


def is_ffmpeg_update_newer(
    remote_commit: Optional[int],
    remote_date: Optional[str],
    installed_commit: Optional[int],
    installed_date: Optional[str],
) -> bool:
    """Compare remote FFmpeg build against installed build."""
    if remote_commit is not None and installed_commit is not None:
        return remote_commit > installed_commit
    if remote_date is not None and installed_date is not None:
        return remote_date > installed_date
    return False


def get_installed_ffmpeg_version() -> str:
    """
    Get the installed FFmpeg version string by running ffmpeg.exe -version.
    Returns build string like 'N-127579-g5cb9fcd211-20261016' or 'unknown'.
    """
    try:
        ok, info = verify_ffmpeg()
        if ok:
            parts = info.split(" ")
            if len(parts) > 2:
                return parts[2]
            return info.strip()
        return "unknown"
    except Exception:
        return "unknown"


def check_ffmpeg_update() -> Optional[dict]:
    """
    Query GitHub Releases API for the latest FFmpeg Windows build.
    Selects the static GPL build (excluding shared builds).
    Parses real build commit and date, never the word 'latest'.

    Returns:
        dict with {current_version, latest_version, update_available, download_url, size_str, release_url}
        or None if failed.
    """
    try:
        url = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "YouCut-App",
        }
        resp = requests.get(url, timeout=15, headers=headers)
        if resp.status_code != 200:
            logger.debug("FFmpeg update check status code: %s", resp.status_code)
            return None
        data = resp.json()
        release_url = data.get("html_url", "https://github.com/BtbN/FFmpeg-Builds/releases")

        # Select static win64 build (exclude -shared builds!)
        target_asset = None
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if "win64-gpl" in name and "-shared" not in name and name.endswith(".zip"):
                target_asset = asset
                break

        if not target_asset:
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if "win64" in name and "-shared" not in name and name.endswith(".zip"):
                    target_asset = asset
                    break

        if not target_asset and data.get("assets"):
            target_asset = data["assets"][0]

        if not target_asset:
            return None

        asset_name = target_asset.get("name", "")
        size_bytes = target_asset.get("size", 0)
        size_mb = size_bytes / (1024 * 1024)
        size_str = f"≈ {size_mb:.0f} MB" if size_mb > 0 else "≈ 201 MB"
        dl_url = target_asset.get("browser_download_url", "")

        remote_commit, remote_date, display_ver = parse_ffmpeg_remote_version(data, asset_name)
        current_ver = get_installed_ffmpeg_version()
        inst_commit, inst_date = parse_ffmpeg_installed_version(current_ver)

        update_available = False
        if current_ver and current_ver != "unknown":
            update_available = is_ffmpeg_update_newer(remote_commit, remote_date, inst_commit, inst_date)

        logger.info(
            "FFmpeg check result — installed: %s (commit=%s, date=%s), remote: %s (commit=%s, date=%s), newer: %s, asset: %s",
            current_ver, inst_commit, inst_date, display_ver, remote_commit, remote_date, update_available, asset_name,
        )

        return {
            "current_version": current_ver,
            "latest_version": display_ver,
            "update_available": update_available,
            "download_url": dl_url,
            "size_str": size_str,
            "release_url": release_url,
        }
    except Exception as e:
        logger.debug("FFmpeg update check error: %s", e)
        return None


# ── Background Workers ───────────────────────────────────────────────────────

class AppUpdateChecker(QObject):
    """
    Background worker that checks for a new YouCut app version.

    Signals:
        update_found: (version, release_notes, download_url)
        no_update: ()
        check_failed: (error_message,)
    """

    update_found = Signal(str, str, str)
    no_update = Signal()
    check_failed = Signal(str)

    def __init__(self, check_url: str = "", parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._check_url = check_url
        self._cancel_requested: bool = False

    def cancel(self) -> None:
        """Cancel app update check."""
        self._cancel_requested = True

    def run(self) -> None:
        """Perform the check and emit the appropriate signal."""
        if self._cancel_requested:
            return
        result = check_app_update(self._check_url)
        if self._cancel_requested:
            return
        if not result:
            self.no_update.emit()
        elif result.get("status") == "available":
            self.update_found.emit(
                result.get("version", ""),
                result.get("release_notes", ""),
                result.get("download_url", ""),
            )
        elif result.get("status") == "error":
            self.check_failed.emit(result.get("error", "Check failed"))
        else:
            self.no_update.emit()


class AppInstallerDownloader(QObject):
    """
    Background worker that downloads the YouCut installer for silent update in frozen mode.
    """
    progress = Signal(int, str)
    completed = Signal(str)  # path to installer .exe
    failed = Signal(str)

    def __init__(self, download_url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._download_url = download_url
        self._cancel_requested: bool = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        if not self._download_url or self._cancel_requested:
            self.failed.emit("No valid download URL provided.")
            return

        import tempfile
        try:
            target_path = Path(tempfile.gettempdir()) / "YouCut-Setup.exe"
            resp = requests.get(self._download_url, stream=True, timeout=45)
            resp.raise_for_status()

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(target_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=128 * 1024):
                    if self._cancel_requested:
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = int((downloaded / total_size) * 100) if total_size > 0 else 0
                        self.progress.emit(pct, "")

            if not self._cancel_requested:
                self.completed.emit(str(target_path))
        except Exception as e:
            if not self._cancel_requested:
                self.failed.emit(str(e))


class UpdateChecker(QObject):
    """
    Background worker that checks for yt-dlp updates and performs self-update.

    Signals:
        check_completed: (current_version, latest_version, update_available, success)
        update_completed: (new_version,)
        update_failed: (error_message,)
        status_message: (message,)
    """

    check_completed = Signal(str, str, bool, bool)
    update_completed = Signal(str)
    update_failed = Signal(str)
    status_message = Signal(str)
    progress = Signal(int, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_process: Optional[Any] = None
        self._cancel_requested: bool = False

    def cancel(self) -> None:
        """Cancel yt-dlp update and kill any running subprocess tree."""
        self._cancel_requested = True
        if self._active_process and self._active_process.poll() is None:
            pid = self._active_process.pid
            logger.info("Cancelling UpdateChecker process tree for PID: %d", pid)
            try:
                from clipgrab.core.downloader import kill_process_tree
                kill_process_tree(pid)
            except Exception as e:
                logger.warning("Failed to kill UpdateChecker process tree: %s", e)
            try:
                self._active_process.kill()
            except Exception:
                pass

    def check_for_update(self) -> None:
        """Compare installed yt-dlp.exe version against PyPI latest."""
        if self._cancel_requested:
            return
        current = get_installed_ytdlp_version()
        logger.info("yt-dlp update check — installed: %s", current)

        if self._cancel_requested:
            return
        latest = get_latest_pypi_version()
        if self._cancel_requested:
            return
        if latest is None:
            logger.info("Could not fetch latest version from PyPI")
            self.check_completed.emit(current, "", False, False)
            return

        update_available = False
        if current and current != "unknown" and latest:
            try:
                from packaging import version
                # packaging.version correctly handles 2026.08.19 == 2026.8.19
                update_available = version.parse(latest) > version.parse(current)
            except Exception:
                update_available = (latest.strip() != current.strip())

        logger.info(
            "yt-dlp update check — installed: %s, latest: %s, update_available: %s",
            current, latest, update_available,
        )
        self.check_completed.emit(current, latest, update_available, True)

    def perform_update(self) -> None:
        """
        Update yt-dlp.exe using its built-in self-update mechanism (yt-dlp -U).

        Downloads the latest binary and replaces the existing one in place.
        In PyInstaller --onefile mode, targets ~/.youcut/bin/yt-dlp.exe so updates
        persist across runs.
        Emits update_completed on success, update_failed on error.
        """
        # Ensure user bin directory exists
        env_home = os.environ.get("YOUCUT_HOME")
        base_dir = Path(env_home).resolve() if env_home else Path.home() / ".youcut"
        user_bin_dir = base_dir / "bin"
        user_bin_dir.mkdir(parents=True, exist_ok=True)
        user_ytdlp = user_bin_dir / "yt-dlp.exe"

        # If user copy doesn't exist yet, seed it from bundled yt-dlp
        if not user_ytdlp.is_file():
            bundled = get_ytdlp_path()
            if os.path.isfile(bundled):
                try:
                    shutil.copy2(bundled, str(user_ytdlp))
                    logger.info("Copied bundled yt-dlp to %s for persistent updates", user_ytdlp)
                except Exception as e:
                    logger.warning("Could not copy bundled yt-dlp to user dir: %s", e)

        ytdlp_path = str(user_ytdlp) if user_ytdlp.is_file() else get_ytdlp_path()

        # Guard 1: Verify version comparison (never run yt-dlp -U if versions are equal or local is newer)
        current = get_installed_ytdlp_version()
        latest = get_latest_pypi_version()
        if current and latest and current != "unknown":
            try:
                from packaging import version
                if version.parse(latest) <= version.parse(current):
                    logger.info("yt-dlp is already up to date (%s == %s), skipping yt-dlp -U.", current, latest)
                    self.update_completed.emit(current)
                    return
            except Exception as e:
                logger.debug("Version parse check exception: %s", e)

        # Guard 2: Never run yt-dlp -U while another YouCut instance is running
        lock_path = base_dir / "youcut.lock"
        if lock_path.is_file():
            try:
                from PySide6.QtCore import QLockFile
                lock_test = QLockFile(str(lock_path))
                if not lock_test.tryLock(50):
                    logger.warning("Another YouCut instance is running; skipping yt-dlp -U.")
                    self.update_failed.emit("Another YouCut instance is currently running.")
                    return
                lock_test.unlock()
            except Exception as e:
                logger.debug("Lockfile check exception: %s", e)

        logger.info("Starting yt-dlp self-update: %s -U", ytdlp_path)
        self.status_message.emit("Updating yt-dlp...")
        bak_ytdlp = user_bin_dir / "yt-dlp.exe.bak"
        if user_ytdlp.is_file():
            try:
                if bak_ytdlp.is_file():
                    bak_ytdlp.unlink()
                shutil.copy2(str(user_ytdlp), str(bak_ytdlp))
            except Exception as be:
                logger.warning("Could not backup yt-dlp before update: %s", be)

        try:
            import subprocess
            self._active_process = subprocess.Popen(
                [ytdlp_path, "-U"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_CREATION_FLAGS,
            )
            stdout, stderr = self._active_process.communicate(timeout=120)
            returncode = self._active_process.returncode

            if self._cancel_requested:
                logger.info("yt-dlp self-update was cancelled, aborting.")
                return

            output = (stdout + "\n" + stderr).strip()
            logger.debug("yt-dlp -U output: %s", output)

            if returncode == 0:
                invalidate_ytdlp_cache()
                ok, verified_str = verify_ytdlp(force=True)
                new_version = verified_str.strip() if ok else get_installed_ytdlp_version()
                logger.info("yt-dlp updated successfully to %s", new_version)
                self.update_completed.emit(new_version)
                self.status_message.emit(f"yt-dlp updated to v{new_version}")
            else:
                error_detail = stderr.strip() or stdout.strip() or "Unknown error"
                logger.error(
                    "yt-dlp self-update failed (rc=%d): %s",
                    returncode, error_detail
                )
                if bak_exe.is_file() if 'bak_exe' in locals() else False:
                    pass
                if bak_ytdlp.is_file() and not user_ytdlp.is_file():
                    try:
                        shutil.copy2(str(bak_ytdlp), str(user_ytdlp))
                        invalidate_ytdlp_cache()
                    except Exception:
                        pass
                self.update_failed.emit(error_detail)

        except subprocess.TimeoutExpired:
            logger.error("yt-dlp self-update timed out")
            self.update_failed.emit("Update timed out after 120 seconds")
        except PermissionError:
            logger.error("yt-dlp self-update permission denied")
            self.update_failed.emit(
                "Permission denied. The yt-dlp binary may be locked."
            )
        except FileNotFoundError:
            logger.error("yt-dlp.exe not found for update: %s", ytdlp_path)
            self.update_failed.emit(f"yt-dlp.exe not found at: {ytdlp_path}")
        except Exception as e:
            if self._cancel_requested:
                return
            logger.error("yt-dlp self-update error: %s\n%s", e, traceback.format_exc())
            if bak_ytdlp.is_file() and not user_ytdlp.is_file():
                try:
                    shutil.copy2(str(bak_ytdlp), str(user_ytdlp))
                    invalidate_ytdlp_cache()
                except Exception:
                    pass
            self.update_failed.emit(str(e))
        finally:
            self._active_process = None


# ── FFmpeg Workers ───────────────────────────────────────────────────────────

class FFmpegUpdateChecker(QObject):
    """
    Background worker that checks for FFmpeg updates.

    Signals:
        check_completed: (current_version, latest_version, update_available, download_url, size_str, release_url)
        check_failed: (error_message,)
    """

    check_completed = Signal(str, str, bool, str, str, str)
    check_failed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    def check(self) -> None:
        if self._cancel_requested:
            return
        result = check_ffmpeg_update()
        if self._cancel_requested:
            return
        if result:
            self.check_completed.emit(
                result["current_version"],
                result["latest_version"],
                result["update_available"],
                result["download_url"],
                result["size_str"],
                result["release_url"],
            )
        else:
            self.check_failed.emit("Could not check for FFmpeg updates.")


class FFmpegUpdater(QObject):
    """
    Background worker that downloads, verifies, and updates FFmpeg.

    Signals:
        progress: (percent, speed_str, downloaded_bytes, total_bytes)
        update_completed: (new_version,)
        update_failed: (error_message,)
        status_message: (message,)
    """

    progress = Signal(int, str, int, int)
    update_completed = Signal(str)
    update_failed = Signal(str)
    status_message = Signal(str)

    def __init__(self, download_url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._download_url = download_url
        self._cancel_requested = False
        self._resp: Optional[requests.Response] = None

    def cancel(self) -> None:
        self._cancel_requested = True
        if self._resp:
            try:
                self._resp.close()
            except Exception:
                pass

    def perform_update(self) -> None:
        if self._cancel_requested or not self._download_url:
            return

        import time
        import zipfile

        env_home = os.environ.get("YOUCUT_HOME")
        base_dir = Path(env_home).resolve() if env_home else Path.home() / ".youcut"
        user_bin_dir = base_dir / "bin"
        user_bin_dir.mkdir(parents=True, exist_ok=True)

        user_ffmpeg = user_bin_dir / "ffmpeg.exe"
        tmp_archive = user_bin_dir / "ffmpeg_download.tmp"
        extract_dir = user_bin_dir / "tmp_ffmpeg_extract"
        bak_exe = user_bin_dir / "ffmpeg.exe.bak"

        logger.info("[FFmpeg Update] Step 1/7: Initializing download from %s", self._download_url)
        try:
            self.status_message.emit("Downloading FFmpeg build...")
            try:
                resp = requests.get(self._download_url, stream=True, timeout=30)
                self._resp = resp
                resp.raise_for_status()
            except requests.RequestException as re:
                logger.error("[FFmpeg Update] Download network request failed: %s", re)
                self.update_failed.emit("Download failed (no internet)")
                return

            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            last_emit = start_time

            with open(tmp_archive, "wb") as f:
                for chunk in resp.iter_content(chunk_size=128 * 1024):
                    if self._cancel_requested:
                        logger.info("[FFmpeg Update] Download cancelled by user.")
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_emit >= 0.2:
                            dur = max(now - start_time, 0.001)
                            speed_mb = (downloaded / (1024 * 1024)) / dur
                            pct = int((downloaded / total_size) * 100) if total_size > 0 else 0
                            self.progress.emit(pct, f"{speed_mb:.1f} MB/s", downloaded, total_size)
                            last_emit = now

            if self._cancel_requested:
                logger.info("[FFmpeg Update] Update cancelled by user.")
                return

            # Step 2: Check size
            logger.info("[FFmpeg Update] Step 2/7: Checking downloaded file size (%d bytes)", downloaded)
            if not tmp_archive.exists() or tmp_archive.stat().st_size < 1024:
                logger.error("[FFmpeg Update] Downloaded archive is empty or invalid (<1KB)")
                self.update_failed.emit("Download failed (no internet)")
                return

            # Step 3: Extract archive to temp folder
            logger.info("[FFmpeg Update] Step 3/7: Extracting ffmpeg binary to temp directory %s", extract_dir)
            self.status_message.emit("Extracting FFmpeg binary...")
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            extract_dir.mkdir(parents=True, exist_ok=True)

            tmp_exe = extract_dir / "ffmpeg.exe"
            try:
                if zipfile.is_zipfile(tmp_archive):
                    with zipfile.ZipFile(tmp_archive, "r") as zf:
                        ffmpeg_entry = None
                        for name in zf.namelist():
                            if name.endswith("ffmpeg.exe"):
                                ffmpeg_entry = name
                                break
                        if not ffmpeg_entry:
                            raise ValueError("Archive did not contain ffmpeg.exe")

                        # Extract ffmpeg.exe
                        with zf.open(ffmpeg_entry) as src, open(tmp_exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)

                        # Also extract any DLLs in the same archive directory if present
                        prefix = os.path.dirname(ffmpeg_entry)
                        for name in zf.namelist():
                            if name.endswith(".dll") and (not prefix or name.startswith(prefix)):
                                dll_name = os.path.basename(name)
                                with zf.open(name) as src, open(extract_dir / dll_name, "wb") as dst:
                                    shutil.copyfileobj(src, dst)
                else:
                    shutil.move(str(tmp_archive), str(tmp_exe))
            except Exception as ee:
                logger.error("[FFmpeg Update] Extraction failed: %s", ee)
                self.update_failed.emit("Couldn't extract ffmpeg.exe from the download")
                return

            if not tmp_exe.is_file():
                self.update_failed.emit("Couldn't extract ffmpeg.exe from the download")
                return

            # Step 4: Self-test [tmp_exe, "-version"]
            logger.info("[FFmpeg Update] Step 4/7: Self-testing extracted binary %s", tmp_exe)
            self.status_message.emit("Verifying FFmpeg binary...")
            try:
                test_res = subprocess.run(
                    [str(tmp_exe), "-version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=_CREATION_FLAGS,
                )
                if test_res.returncode != 0:
                    logger.error("[FFmpeg Update] Self-test failed (rc=%s): %s", test_res.returncode, test_res.stderr)
                    self.update_failed.emit("New FFmpeg failed its self-test")
                    return
            except Exception as se:
                logger.error("[FFmpeg Update] Self-test execution exception: %s", se)
                self.update_failed.emit("New FFmpeg failed its self-test")
                return

            first_line = test_res.stdout.split("\n")[0] if test_res.stdout else ""
            new_version = first_line.split(" ")[2] if len(first_line.split(" ")) > 2 else first_line.strip()
            logger.info("[FFmpeg Update] Self-test succeeded. Version: %s", new_version)

            # Step 5: Backup current binary to .bak
            logger.info("[FFmpeg Update] Step 5/7: Backing up current binary to %s", bak_exe)
            if user_ffmpeg.is_file():
                try:
                    if bak_exe.is_file():
                        bak_exe.unlink()
                    shutil.copy2(str(user_ffmpeg), str(bak_exe))
                except Exception as be:
                    logger.warning("[FFmpeg Update] Could not backup existing binary: %s", be)

            # Step 6: Atomic replace with 3x retry on lock
            logger.info("[FFmpeg Update] Step 6/7: Atomically replacing %s", user_ffmpeg)
            self.status_message.emit("Installing FFmpeg...")
            replaced = False
            for attempt in range(1, 4):
                try:
                    os.replace(str(tmp_exe), str(user_ffmpeg))
                    for dll in extract_dir.glob("*.dll"):
                        target_dll = user_bin_dir / dll.name
                        os.replace(str(dll), str(target_dll))
                    replaced = True
                    break
                except (PermissionError, OSError) as pe:
                    logger.warning("[FFmpeg Update] Atomic replace locked on attempt %d/3: %s", attempt, pe)
                    if attempt < 3:
                        time.sleep(1)

            if not replaced:
                logger.error("[FFmpeg Update] Failed to replace %s after 3 attempts — binary is locked", user_ffmpeg)
                if bak_exe.is_file() and not user_ffmpeg.is_file():
                    shutil.copy2(str(bak_exe), str(user_ffmpeg))
                self.update_failed.emit("ffmpeg.exe is in use — wait for downloads to finish and retry")
                return

            # Step 7: Post-install verification & re-read version
            logger.info("[FFmpeg Update] Step 7/7: Re-verifying installed binary at %s", user_ffmpeg)
            invalidate_ffmpeg_cache()
            ok, verified_info = verify_ffmpeg(force=True)
            if not ok:
                logger.error("[FFmpeg Update] Post-install verification failed! Restoring backup...")
                if bak_exe.is_file():
                    shutil.copy2(str(bak_exe), str(user_ffmpeg))
                    invalidate_ffmpeg_cache()
                self.update_failed.emit("New FFmpeg failed its self-test")
                return

            first_v_line = verified_info.split("\n")[0] if verified_info else ""
            parts = first_v_line.split(" ")
            installed_build = parts[2] if len(parts) > 2 else first_v_line.strip()

            logger.info("[FFmpeg Update] Pipeline completed successfully! New build: %s", installed_build)
            self.update_completed.emit(installed_build)
            self.status_message.emit(f"FFmpeg updated to {installed_build}")

        except Exception as e:
            if self._cancel_requested:
                logger.info("[FFmpeg Update] Cancelled during execution.")
                return
            logger.error("[FFmpeg Update] Unexpected exception in update pipeline: %s", e)
            if bak_exe.is_file() and not user_ffmpeg.is_file():
                try:
                    shutil.copy2(str(bak_exe), str(user_ffmpeg))
                    invalidate_ffmpeg_cache()
                except Exception:
                    pass
            self.update_failed.emit(str(e))
        finally:
            try:
                if tmp_archive.is_file():
                    tmp_archive.unlink()
            except Exception:
                pass
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)


# ── Update Manager ───────────────────────────────────────────────────────────

class UpdateManager(QObject):
    """
    Orchestrates YouCut app update checking, yt-dlp update checking,
    and FFmpeg binary update checking and installation.

    Signals:
        app_update_available: (version, release_notes, download_url)
        update_available: (current_ytdlp, latest_ytdlp)
        update_installed: (new_ytdlp_version,)
        toast_message: (message,)

        # Fine-grained state signals for the Settings UI cards:
        app_status_changed: (status, version, notes, download_url)
            status in {"checking", "up_to_date", "available", "updating", "error"}
        ytdlp_status_changed: (status, current_ver, latest_ver)
            status in {"checking", "up_to_date", "available", "updating", "error"}
        ytdlp_progress: (percent, speed_str)
        ffmpeg_status_changed: (status, current_ver, latest_ver, size_str, release_url)
            status in {"checking", "up_to_date", "available", "updating", "error"}
        ffmpeg_progress: (percent, speed_str)
        all_checks_completed: (last_checked_display_text,)
        has_update_available: (bool,)
    """

    app_update_available = Signal(str, str, str)  # version, notes, url
    update_available = Signal(str, str)           # current, latest yt-dlp
    update_installed = Signal(str)               # new yt-dlp version
    toast_message = Signal(str)

    app_status_changed = Signal(str, str, str, str)
    ytdlp_status_changed = Signal(str, str, str)
    ytdlp_progress = Signal(int, str)
    ffmpeg_status_changed = Signal(str, str, str, str, str)
    ffmpeg_progress = Signal(int, str)
    all_checks_completed = Signal(str)
    has_update_available = Signal(bool)
    component_state_changed = Signal(object)  # ComponentState

    def __init__(self, parent: Optional[QObject] = None, settings: Optional[AppSettings] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        if self._settings is None:
            try:
                from clipgrab.config.settings import AppSettings
                self._settings = AppSettings.load()
            except Exception:
                self._settings = None

        self._auto_update: bool = True
        self._auto_update_app: bool = True
        self._auto_update_ffmpeg: bool = False
        if self._settings:
            self._auto_update_app = getattr(self._settings, "auto_update_app", True)
            self._auto_update = getattr(self._settings, "auto_update_ytdlp", True)
            self._auto_update_ffmpeg = getattr(self._settings, "auto_update_ffmpeg", False)

        self._last_check_time: Optional[datetime] = None
        self._pending_update: bool = False
        self._pending_ffmpeg_update: bool = False
        self._latest_version: str = ""
        self._queue_busy: bool = False

        self._app_update_url: str = ""
        self._latest_ffmpeg_download_url: str = ""
        self._app_check_is_manual: bool = False
        self._ytdlp_check_is_manual: bool = False
        self._ffmpeg_check_is_manual: bool = False
        self._pending_app_update: bool = False

        # Unified component states
        self._states: dict[str, ComponentState] = {
            "youcut": ComponentState(component="youcut", status="up_to_date", current_version=APP_VERSION),
            "ytdlp": ComponentState(component="ytdlp", status="up_to_date", current_version=get_installed_ytdlp_version()),
            "ffmpeg": ComponentState(component="ffmpeg", status="up_to_date", current_version=get_installed_ffmpeg_version()),
        }

        # Component periodic timers (evaluates cooldown every 6 hours)
        self._youcut_timer = QTimer(self)
        self._youcut_timer.setInterval(PERIODIC_CHECK_INTERVAL_MS)
        self._youcut_timer.timeout.connect(self._on_youcut_periodic_timeout)

        self._ytdlp_timer = QTimer(self)
        self._ytdlp_timer.setInterval(PERIODIC_CHECK_INTERVAL_MS)
        self._ytdlp_timer.timeout.connect(self._on_ytdlp_periodic_timeout)

        self._ffmpeg_timer = QTimer(self)
        self._ffmpeg_timer.setInterval(PERIODIC_CHECK_INTERVAL_MS)
        self._ffmpeg_timer.timeout.connect(self._on_ffmpeg_periodic_timeout)

        # Alias for backwards compatibility
        self._periodic_timer = self._ytdlp_timer

        # Start timer ONLY for components with auto_update toggle ON
        if self._auto_update_app:
            self._youcut_timer.start()
        if self._auto_update:
            self._ytdlp_timer.start()
        if self._auto_update_ffmpeg:
            self._ffmpeg_timer.start()

        # Worker thread references (prevent GC)
        self._check_thread: Optional[QThread] = None
        self._update_thread: Optional[QThread] = None
        self._app_check_thread: Optional[QThread] = None
        self._ffmpeg_check_thread: Optional[QThread] = None
        self._ffmpeg_update_thread: Optional[QThread] = None
        self._app_installer_thread: Optional[QThread] = None

        self._check_worker: Optional[UpdateChecker] = None
        self._update_worker: Optional[UpdateChecker] = None
        self._app_checker: Optional[AppUpdateChecker] = None
        self._ffmpeg_checker: Optional[FFmpegUpdateChecker] = None
        self._ffmpeg_updater: Optional[FFmpegUpdater] = None
        self._app_installer_worker: Optional[AppInstallerDownloader] = None

    def set_settings(self, settings: AppSettings) -> None:
        """Update settings reference and configure component timers."""
        self._settings = settings
        if self._settings:
            self._auto_update_app = getattr(self._settings, "auto_update_app", True)
            self._auto_update = getattr(self._settings, "auto_update_ytdlp", True)
            self._auto_update_ffmpeg = getattr(self._settings, "auto_update_ffmpeg", False)
            if hasattr(self, "_youcut_timer") and self._youcut_timer:
                if self._auto_update_app:
                    if not self._youcut_timer.isActive():
                        self._youcut_timer.start()
                else:
                    self._youcut_timer.stop()
            if hasattr(self, "_ytdlp_timer") and self._ytdlp_timer:
                if self._auto_update:
                    if not self._ytdlp_timer.isActive():
                        self._ytdlp_timer.start()
                else:
                    self._ytdlp_timer.stop()
            if hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer:
                if self._auto_update_ffmpeg:
                    if not self._ffmpeg_timer.isActive():
                        self._ffmpeg_timer.start()
                else:
                    self._ffmpeg_timer.stop()

    def is_timer_active(self, component: str) -> bool:
        """Return True if periodic check timer is active for component."""
        if component == "youcut":
            return bool(hasattr(self, "_youcut_timer") and self._youcut_timer and self._youcut_timer.isActive())
        elif component == "ytdlp":
            return bool(hasattr(self, "_ytdlp_timer") and self._ytdlp_timer and self._ytdlp_timer.isActive())
        elif component == "ffmpeg":
            return bool(hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer and self._ffmpeg_timer.isActive())
        return False

    def _get_cache(self, component: str) -> dict[str, Any]:
        """Return or create update cache dict for component."""
        if not self._settings:
            return {}
        if not hasattr(self._settings, "update_cache") or not isinstance(self._settings.update_cache, dict):
            self._settings.update_cache = {}
        if component not in self._settings.update_cache:
            self._settings.update_cache[component] = {
                "last_check_time": "",
                "latest_version": "",
                "size": "",
                "release_notes_url": "",
                "download_url": "",
                "last_toast_version": "",
                "last_check_success": True,
            }
        return self._settings.update_cache[component]

    def _save_settings(self) -> None:
        """Persist settings to ~/.youcut/config.json."""
        if self._settings:
            try:
                self._settings.save()
            except Exception as e:
                logger.debug("Failed to save settings: %s", e)

    def _has_any_update_available(self) -> bool:
        """Check if any component currently has an update available."""
        if not self._settings:
            return False
        cache = getattr(self._settings, "update_cache", {})
        if not isinstance(cache, dict):
            return False
        for comp in ("youcut", "ytdlp", "ffmpeg"):
            c = cache.get(comp, {})
            if c.get("latest_version"):
                return True
        return False

    def get_component_state(self, component: str) -> ComponentState:
        """Get the current unified state of a component."""
        return self._states.get(component, ComponentState(component=component))

    def _set_component_state(self, state: ComponentState) -> None:
        """Update unified component state and emit both new and legacy signals."""
        self._states[state.component] = state
        self.component_state_changed.emit(state)

        if state.component == "youcut":
            self.app_status_changed.emit(
                state.status,
                state.latest_version or state.current_version,
                state.release_notes_url,
                state.download_url,
            )
        elif state.component == "ytdlp":
            self.ytdlp_status_changed.emit(
                state.status,
                state.current_version,
                state.latest_version,
            )
        elif state.component == "ffmpeg":
            self.ffmpeg_status_changed.emit(
                state.status,
                state.current_version,
                state.latest_version,
                state.size_str,
                state.release_notes_url,
            )

    def is_checking(self, component: str) -> bool:
        """Return True if component is currently checking for updates."""
        if component == "youcut":
            return bool(self._app_check_thread and self._app_check_thread.isRunning())
        elif component == "ytdlp":
            return bool(self._check_thread and self._check_thread.isRunning())
        elif component == "ffmpeg":
            return bool(self._ffmpeg_check_thread and self._ffmpeg_check_thread.isRunning())
        return False

    def _is_operation_in_progress(self, component: str) -> bool:
        """Check if an update or check operation is already in flight for component."""
        return self.is_mid_update(component) or self.is_checking(component)

    def load_cached_badges(self) -> None:
        """
        Show cached badges instantly on launch.
        Drop cache when installed >= latest (packaging.version.parse).
        """
        if not self._settings:
            return

        # 1. YouCut
        cur_youcut = APP_VERSION
        c_youcut = self._get_cache("youcut")
        latest_youcut = c_youcut.get("latest_version", "")
        if latest_youcut:
            if _is_installed_ge_latest(cur_youcut, latest_youcut):
                c_youcut["latest_version"] = ""
                c_youcut["download_url"] = ""
                self._save_settings()
                self._set_component_state(ComponentState("youcut", "up_to_date", cur_youcut, cur_youcut))
            else:
                self._set_component_state(ComponentState(
                    component="youcut",
                    status="available",
                    current_version=cur_youcut,
                    latest_version=latest_youcut,
                    release_notes_url=c_youcut.get("release_notes_url", ""),
                    download_url=c_youcut.get("download_url", ""),
                ))

        # 2. yt-dlp
        cur_ytdlp = get_installed_ytdlp_version()
        c_ytdlp = self._get_cache("ytdlp")
        latest_ytdlp = c_ytdlp.get("latest_version", "")
        if latest_ytdlp and cur_ytdlp != "unknown":
            if _is_installed_ge_latest(cur_ytdlp, latest_ytdlp):
                c_ytdlp["latest_version"] = ""
                self._save_settings()
                self._set_component_state(ComponentState("ytdlp", "up_to_date", cur_ytdlp, cur_ytdlp))
            else:
                self._set_component_state(ComponentState(
                    component="ytdlp",
                    status="available",
                    current_version=cur_ytdlp,
                    latest_version=latest_ytdlp,
                ))

        # 3. FFmpeg
        cur_ffmpeg = get_installed_ffmpeg_version()
        c_ffmpeg = self._get_cache("ffmpeg")
        latest_ffmpeg = c_ffmpeg.get("latest_version", "")
        if latest_ffmpeg and cur_ffmpeg != "unknown":
            inst_commit, inst_date = parse_ffmpeg_installed_version(cur_ffmpeg)
            rem_commit = None
            m = re.search(r"N-(\d+)", latest_ffmpeg)
            if m:
                rem_commit = int(m.group(1))
            rem_date = None
            m_date = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", latest_ffmpeg)
            if m_date:
                rem_date = f"{m_date.group(1)}-{m_date.group(2)}-{m_date.group(3)}"

            # If installed is already up-to-date or newer, drop cache
            if not is_ffmpeg_update_newer(rem_commit, rem_date, inst_commit, inst_date):
                c_ffmpeg["latest_version"] = ""
                self._save_settings()
                self._set_component_state(ComponentState("ffmpeg", "up_to_date", cur_ffmpeg, cur_ffmpeg))
            else:
                self._set_component_state(ComponentState(
                    component="ffmpeg",
                    status="available",
                    current_version=cur_ffmpeg,
                    latest_version=latest_ffmpeg,
                    size_str=c_ffmpeg.get("size", "≈ 201 MB"),
                    release_notes_url=c_ffmpeg.get("release_notes_url", ""),
                    download_url=c_ffmpeg.get("download_url", ""),
                ))

        self.has_update_available.emit(self._has_any_update_available())

    def _is_cooldown_active(self, component: str, cooldown_seconds: int) -> bool:
        """
        Return True if cooldown interval has not elapsed yet.
        On network failure do not count as checked; retry at most once per hour.
        """
        cache = self._get_cache(component)
        now = datetime.now(timezone.utc)

        # 1. Network failure retry cooldown (at most once per hour)
        fail_str = cache.get("last_failure_time", "")
        if fail_str:
            try:
                fail_dt = datetime.fromisoformat(fail_str)
                if fail_dt.tzinfo is None:
                    fail_dt = fail_dt.replace(tzinfo=timezone.utc)
                if (now - fail_dt).total_seconds() < RETRY_FAILED_NETWORK_SECONDS:
                    return True
            except Exception:
                pass

        # Support fallback legacy last_check_success=False
        if not cache.get("last_check_success", True) and not fail_str:
            last_check = cache.get("last_check_time", "")
            if last_check:
                try:
                    chk_dt = datetime.fromisoformat(last_check)
                    if chk_dt.tzinfo is None:
                        chk_dt = chk_dt.replace(tzinfo=timezone.utc)
                    return (now - chk_dt).total_seconds() < RETRY_FAILED_NETWORK_SECONDS
                except Exception:
                    pass

        # 2. Regular check cooldown from last successful check
        last_str = cache.get("last_check_time", "")
        if not last_str:
            return False
        try:
            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            return (now - last_dt).total_seconds() < cooldown_seconds
        except Exception:
            return False

    def _record_successful_check(self, component: str) -> None:
        """Record real check completion time in cache and config and notify UI."""
        c = self._get_cache(component)
        now = datetime.now(timezone.utc)
        iso_str = now.isoformat()
        c["last_check_time"] = iso_str
        c["last_failure_time"] = ""
        c["last_check_success"] = True
        self._last_check_time = now
        if self._settings:
            self._settings.update(last_checked_iso=iso_str)
            self._save_settings()
        self.all_checks_completed.emit(format_last_checked(now))

    def shutdown(self) -> None:
        """Stop all update timers, terminate all update workers/threads, and clean up."""
        logger.info("Shutdown: Stopping update timer and update threads...")
        if hasattr(self, "_youcut_timer") and self._youcut_timer:
            self._youcut_timer.stop()
        if hasattr(self, "_ytdlp_timer") and self._ytdlp_timer:
            self._ytdlp_timer.stop()
        if hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer:
            self._ffmpeg_timer.stop()

        # Stop check worker/thread
        if self._check_worker:
            self._check_worker.cancel()
        if self._check_thread and self._check_thread.isRunning():
            self._check_thread.quit()
            self._check_thread.wait(500)
        self._check_thread = None
        self._check_worker = None

        # Stop update worker/thread
        if self._update_worker:
            self._update_worker.cancel()
        if self._update_thread and self._update_thread.isRunning():
            self._update_thread.quit()
            self._update_thread.wait(500)
        self._update_thread = None
        self._update_worker = None

        # Stop app check worker/thread
        if self._app_checker:
            self._app_checker.cancel()
        if self._app_check_thread and self._app_check_thread.isRunning():
            self._app_check_thread.quit()
            self._app_check_thread.wait(500)
        self._app_check_thread = None
        self._app_checker = None

        # Stop ffmpeg check worker/thread
        if self._ffmpeg_checker:
            self._ffmpeg_checker.cancel()
        if self._ffmpeg_check_thread and self._ffmpeg_check_thread.isRunning():
            self._ffmpeg_check_thread.quit()
            self._ffmpeg_check_thread.wait(500)
        self._ffmpeg_check_thread = None
        self._ffmpeg_checker = None

        # Stop ffmpeg update worker/thread
        if self._ffmpeg_updater:
            self._ffmpeg_updater.cancel()
        if self._ffmpeg_update_thread and self._ffmpeg_update_thread.isRunning():
            self._ffmpeg_update_thread.quit()
            self._ffmpeg_update_thread.wait(500)
        self._ffmpeg_update_thread = None
        self._ffmpeg_updater = None

        # Stop app installer worker/thread
        if self._app_installer_worker:
            self._app_installer_worker.cancel()
        if self._app_installer_thread and self._app_installer_thread.isRunning():
            self._app_installer_thread.quit()
            self._app_installer_thread.wait(500)
        self._app_installer_thread = None
        self._app_installer_worker = None

        logger.info("Shutdown: Update manager shutdown complete.")

    @property
    def app_update_url(self) -> str:
        """Custom URL for checking app updates."""
        return self._app_update_url

    @app_update_url.setter
    def app_update_url(self, value: str) -> None:
        self._app_update_url = value

    @property
    def auto_update(self) -> bool:
        """Whether yt-dlp auto-update is enabled."""
        return self._auto_update

    @auto_update.setter
    def auto_update(self, value: bool) -> None:
        val = bool(value)
        if self._auto_update == val:
            return
        self._auto_update = val
        if self._settings:
            self._settings.update(auto_update_ytdlp=self._auto_update)
            self._save_settings()
        if not self._auto_update:
            if hasattr(self, "_ytdlp_timer") and self._ytdlp_timer:
                self._ytdlp_timer.stop()
            self._pending_update = False
            logger.info("yt-dlp auto-update turned OFF (timer stopped, pending auto-install cancelled)")
        else:
            if hasattr(self, "_ytdlp_timer") and self._ytdlp_timer and not self._ytdlp_timer.isActive():
                self._ytdlp_timer.start()
            logger.info("yt-dlp auto-update turned ON (running cooldown-guarded check)")
            if not self._is_cooldown_active("ytdlp", COOLDOWN_YTDLP_SECONDS):
                self.check_ytdlp(force=False)

    @property
    def auto_update_app(self) -> bool:
        """Whether YouCut app auto-update check is enabled."""
        return self._auto_update_app

    @auto_update_app.setter
    def auto_update_app(self, value: bool) -> None:
        val = bool(value)
        if self._auto_update_app == val:
            return
        self._auto_update_app = val
        if self._settings:
            self._settings.update(auto_update_app=self._auto_update_app)
            self._save_settings()
        if not self._auto_update_app:
            if hasattr(self, "_youcut_timer") and self._youcut_timer:
                self._youcut_timer.stop()
            self._pending_app_update = False
            logger.info("YouCut auto-update turned OFF (timer stopped, pending auto-install cancelled)")
        else:
            if hasattr(self, "_youcut_timer") and self._youcut_timer and not self._youcut_timer.isActive():
                self._youcut_timer.start()
            logger.info("YouCut auto-update turned ON (running cooldown-guarded check)")
            if not self._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS):
                self.check_app(force=False, is_manual=False)

    @property
    def auto_update_ffmpeg(self) -> bool:
        """Whether FFmpeg auto-update check is enabled."""
        return self._auto_update_ffmpeg

    @auto_update_ffmpeg.setter
    def auto_update_ffmpeg(self, value: bool) -> None:
        val = bool(value)
        if self._auto_update_ffmpeg == val:
            return
        self._auto_update_ffmpeg = val
        if self._settings:
            self._settings.update(auto_update_ffmpeg=self._auto_update_ffmpeg)
            self._save_settings()
        if not self._auto_update_ffmpeg:
            if hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer:
                self._ffmpeg_timer.stop()
            self._pending_ffmpeg_update = False
            logger.info("FFmpeg auto-update turned OFF (timer stopped, pending auto-install cancelled)")
        else:
            if hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer and not self._ffmpeg_timer.isActive():
                self._ffmpeg_timer.start()
            logger.info("FFmpeg auto-update turned ON (running cooldown-guarded check)")
            if not self._is_cooldown_active("ffmpeg", COOLDOWN_FFMPEG_SECONDS):
                self.check_ffmpeg(force=False)

    def set_queue_busy(self, busy: bool) -> None:
        """Inform whether the download queue is currently active."""
        was_busy = self._queue_busy
        self._queue_busy = busy
        if was_busy and not busy:
            if self._pending_update and self._auto_update:
                logger.info("Queue idle — applying pending yt-dlp update")
                self._pending_update = False
                self._perform_update()
            if self._pending_ffmpeg_update and self._auto_update_ffmpeg:
                logger.info("Queue idle — applying pending FFmpeg update")
                self._pending_ffmpeg_update = False
                self.update_ffmpeg()
            if self._pending_app_update and self._auto_update_app:
                logger.info("Queue idle — applying pending YouCut update")
                self._pending_app_update = False
                c = self._get_cache("youcut")
                dl_url = c.get("download_url", "")
                if getattr(sys, "frozen", False):
                    self.install_frozen_app_update(dl_url)

    def set_last_check_from_config(self, timestamp_str: str) -> None:
        """Restore last-check time from a saved ISO timestamp."""
        try:
            if timestamp_str:
                self._last_check_time = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError):
            self._last_check_time = None

    def get_last_check_iso(self) -> str:
        """Return the last-check time as ISO string for config persistence."""
        if self._last_check_time:
            return self._last_check_time.isoformat()
        return ""

    def startup_check(self) -> None:
        """
        Run update checks on app startup.
        Shows cached badges immediately without network calls.
        For each component:
          - If toggle is OFF: NO startup or background checks, NO timers.
          - If toggle is ON: checks only if cooldown has passed, runs periodic timer.
        """
        logger.info("Startup update checks triggered")
        self.load_cached_badges()

        # 1. YouCut check
        if self._auto_update_app:
            if not self._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS):
                self.check_app(force=False, is_manual=False)
            else:
                logger.info("YouCut startup check skipped (checked recently)")
            if not self._youcut_timer.isActive():
                self._youcut_timer.start()
        else:
            self._youcut_timer.stop()

        # 2. yt-dlp check
        if self._auto_update:
            if not self._is_cooldown_active("ytdlp", COOLDOWN_YTDLP_SECONDS):
                self.check_ytdlp(force=False)
            else:
                logger.info("yt-dlp startup check skipped (checked recently)")
            if not self._ytdlp_timer.isActive():
                self._ytdlp_timer.start()
        else:
            self._ytdlp_timer.stop()

        # 3. FFmpeg check
        if self._auto_update_ffmpeg:
            if not self._is_cooldown_active("ffmpeg", COOLDOWN_FFMPEG_SECONDS):
                self.check_ffmpeg(force=False)
            else:
                logger.info("FFmpeg startup check skipped (checked recently)")
            if not self._ffmpeg_timer.isActive():
                self._ffmpeg_timer.start()
        else:
            self._ffmpeg_timer.stop()

    def on_extraction_error(self) -> None:
        """Trigger an immediate yt-dlp update check (bypasses cooldown)."""
        logger.info("Extraction error triggered yt-dlp update check")
        self.check_ytdlp(force=True)

    def _restart_periodic_timer(self) -> None:
        if hasattr(self, "_periodic_timer") and self._periodic_timer:
            self._periodic_timer.stop()
            self._periodic_timer.start()

    def _on_youcut_periodic_timeout(self) -> None:
        if self._auto_update_app and not self._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS):
            self.check_app(force=False, is_manual=False)

    def _on_ytdlp_periodic_timeout(self) -> None:
        if self._auto_update and not self._is_cooldown_active("ytdlp", COOLDOWN_YTDLP_SECONDS):
            self.check_ytdlp(force=False)

    def _on_ffmpeg_periodic_timeout(self) -> None:
        if self._auto_update_ffmpeg and not self._is_cooldown_active("ffmpeg", COOLDOWN_FFMPEG_SECONDS):
            self.check_ffmpeg(force=False)

    def _on_periodic_timeout(self) -> None:
        self._on_youcut_periodic_timeout()
        self._on_ytdlp_periodic_timeout()
        self._on_ffmpeg_periodic_timeout()

    def reset_idle_timer(self) -> None:
        """Reset the periodic check timers."""
        if hasattr(self, "_youcut_timer") and self._youcut_timer and self._auto_update_app:
            self._youcut_timer.stop()
            self._youcut_timer.start()
        if hasattr(self, "_ytdlp_timer") and self._ytdlp_timer and self._auto_update:
            self._ytdlp_timer.stop()
            self._ytdlp_timer.start()
        if hasattr(self, "_ffmpeg_timer") and self._ffmpeg_timer and self._auto_update_ffmpeg:
            self._ffmpeg_timer.stop()
            self._ffmpeg_timer.start()

    def is_mid_update(self, component: str) -> bool:
        """Return True if component is currently downloading or applying an update."""
        if component == "youcut":
            return bool(self._app_installer_thread and self._app_installer_thread.isRunning())
        elif component == "ytdlp":
            return bool(self._update_thread and self._update_thread.isRunning())
        elif component == "ffmpeg":
            return bool(self._ffmpeg_update_thread and self._ffmpeg_update_thread.isRunning())
        return False

    def check_all(self, force: bool = True, is_manual: bool = True) -> None:
        """
        Trigger update checks for YouCut, yt-dlp, and FFmpeg (bypasses cooldown when force=True).
        Skips components that are mid-update.
        """
        logger.info("Check all updates triggered (force=%s, is_manual=%s)", force, is_manual)
        if not self.is_mid_update("youcut"):
            self.check_app(force=force, is_manual=is_manual)
        if not self.is_mid_update("ytdlp"):
            self.check_ytdlp(force=force, is_manual=is_manual)
        if not self.is_mid_update("ffmpeg"):
            self.check_ffmpeg(force=force, is_manual=is_manual)

    def manual_check(self) -> None:
        """Force update checks for all components (triggered by user)."""
        self.check_all(force=True, is_manual=True)

    # ── YouCut App Check ──────────────────────────────────────────────────────

    def check_app(self, force: bool = False, is_manual: bool = False) -> None:
        """Check for YouCut app updates."""
        if not force and self._is_cooldown_active("youcut", COOLDOWN_YOUCUT_SECONDS):
            return
        if self._is_operation_in_progress("youcut"):
            logger.info("YouCut check/update already in progress — skipping")
            return

        self._set_component_state(ComponentState("youcut", "checking", current_version=APP_VERSION))
        self._run_app_check(is_manual=is_manual)

    def _run_app_check(self, is_manual: bool = False) -> None:
        """Run the YouCut app version check in a background thread."""
        checker = AppUpdateChecker(check_url=self._app_update_url)
        self._app_checker = checker
        thread = QThread(self)
        self._app_check_thread = thread
        checker.moveToThread(thread)

        self._app_check_is_manual = is_manual
        checker.update_found.connect(self._on_app_update_found)
        checker.no_update.connect(self._on_app_no_update)
        checker.check_failed.connect(self._on_app_check_failed)

        thread.started.connect(checker.run)
        checker.update_found.connect(thread.quit)
        checker.no_update.connect(thread.quit)
        checker.check_failed.connect(thread.quit)

        thread.finished.connect(self._on_app_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(checker.deleteLater)
        thread.start()

    def _on_app_thread_cleanup(self) -> None:
        self._app_checker = None
        self._app_check_thread = None

    def _on_app_no_update(self) -> None:
        logger.info("YouCut is up to date (v%s)", APP_VERSION)
        c = self._get_cache("youcut")
        c["latest_version"] = ""
        c["download_url"] = ""
        self._record_successful_check("youcut")

        self._set_component_state(ComponentState("youcut", "up_to_date", current_version=APP_VERSION, latest_version=APP_VERSION))
        if self._app_check_is_manual:
            self.toast_message.emit(f"YouCut is up to date (v{APP_VERSION}).")
        self.has_update_available.emit(self._has_any_update_available())

    def _on_app_update_found(self, version: str, notes: str, url: str) -> None:
        logger.info("YouCut app update found: v%s", version)
        c = self._get_cache("youcut")
        c["latest_version"] = version
        c["release_notes_url"] = notes
        c["download_url"] = url
        self._record_successful_check("youcut")

        # One-time toast
        if c.get("last_toast_version") != version:
            self.toast_message.emit(f"YouCut v{version} is available! Check Settings to download.")
            c["last_toast_version"] = version

        self._save_settings()

        if not self._app_check_is_manual and self._auto_update_app:
            if self._queue_busy:
                self._pending_app_update = True
                logger.info("YouCut update deferred — queue is busy")
                self.toast_message.emit(
                    f"YouCut v{version} update available — will install when queue is idle"
                )
            else:
                if getattr(sys, "frozen", False):
                    self.install_frozen_app_update(url)

        self._set_component_state(ComponentState(
            component="youcut",
            status="available",
            current_version=APP_VERSION,
            latest_version=version,
            release_notes_url=notes,
            download_url=url,
        ))
        self.app_update_available.emit(version, notes, url)
        self.has_update_available.emit(True)

    def _on_app_check_failed(self, error_msg: str) -> None:
        logger.warning("YouCut update check failed: %s", error_msg)
        c = self._get_cache("youcut")
        c["last_failure_time"] = datetime.now(timezone.utc).isoformat()
        c["last_check_success"] = False
        self._save_settings()

        if c.get("latest_version"):
            self._set_component_state(ComponentState(
                component="youcut",
                status="available",
                current_version=APP_VERSION,
                latest_version=c["latest_version"],
                release_notes_url=c.get("release_notes_url", ""),
                download_url=c.get("download_url", ""),
            ))
        else:
            self._set_component_state(ComponentState(
                component="youcut",
                status="error",
                current_version=APP_VERSION,
                error_message="Couldn't reach GitHub Releases.",
            ))
        if self._app_check_is_manual:
            self.toast_message.emit("Could not check for YouCut updates.")

    def install_frozen_app_update(self, download_url: str) -> None:
        """Download and silently run installer when in frozen mode; otherwise open browser."""
        if not getattr(sys, "frozen", False):
            url = download_url or "https://github.com/shubh-creator/YouCut/releases/latest"
            QDesktopServices.openUrl(QUrl(url))
            return

        if not download_url:
            logger.error("No Windows installer URL available for YouCut update.")
            self._set_component_state(ComponentState(
                component="youcut",
                status="error",
                current_version=APP_VERSION,
                error_message="No Windows installer asset found in latest release",
            ))
            self.toast_message.emit("No Windows installer asset found in latest release")
            return

        self._set_component_state(ComponentState("youcut", "installing", current_version=APP_VERSION, download_url=download_url))
        self.toast_message.emit("Downloading YouCut installer...")
        downloader = AppInstallerDownloader(download_url)
        self._app_installer_worker = downloader
        thread = QThread(self)
        self._app_installer_thread = thread
        downloader.moveToThread(thread)

        downloader.completed.connect(self._on_installer_downloaded)
        downloader.failed.connect(self._on_installer_failed)
        thread.started.connect(downloader.run)
        downloader.completed.connect(thread.quit)
        downloader.failed.connect(thread.quit)
        thread.finished.connect(self._on_installer_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(downloader.deleteLater)
        thread.start()

    def _on_installer_thread_cleanup(self) -> None:
        self._app_installer_worker = None
        self._app_installer_thread = None

    def _on_installer_downloaded(self, installer_path: str) -> None:
        logger.info("Installer downloaded to %s, launching silent update...", installer_path)
        self.toast_message.emit("Restarting to update YouCut...")
        try:
            subprocess.Popen([
                installer_path,
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ])
            app = QApplication.instance()
            if app:
                app.quit()
        except Exception as e:
            logger.error("Failed to run installer: %s", e)
            self._set_component_state(ComponentState("youcut", "error", current_version=APP_VERSION, error_message=f"Failed to launch installer: {e}"))
            self.toast_message.emit(f"Failed to launch installer: {e}")

    def _on_installer_failed(self, error_msg: str) -> None:
        logger.warning("Installer download failed: %s", error_msg)
        self._set_component_state(ComponentState("youcut", "error", current_version=APP_VERSION, error_message=error_msg))
        self.toast_message.emit(f"Failed to download installer: {error_msg}")

    # ── yt-dlp Check & Update ─────────────────────────────────────────────────

    def check_ytdlp(self, force: bool = False, is_manual: bool = False) -> None:
        """Check for yt-dlp updates."""
        if not force and self._is_cooldown_active("ytdlp", COOLDOWN_YTDLP_SECONDS):
            return
        if self._is_operation_in_progress("ytdlp"):
            logger.info("yt-dlp check/update already in progress — skipping")
            return

        self._ytdlp_check_is_manual = is_manual
        self._set_component_state(ComponentState("ytdlp", "checking", current_version=get_installed_ytdlp_version()))
        self._run_check(force=force)

    def _run_check(self, force: bool = False) -> None:
        checker = UpdateChecker()
        self._check_worker = checker
        thread = QThread(self)
        self._check_thread = thread
        checker.moveToThread(thread)

        checker.check_completed.connect(self._on_ytdlp_check_completed)
        thread.started.connect(checker.check_for_update)
        checker.check_completed.connect(thread.quit)

        thread.finished.connect(self._on_check_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(checker.deleteLater)
        thread.start()

    def _on_check_thread_cleanup(self) -> None:
        self._check_worker = None
        self._check_thread = None

    def _on_ytdlp_check_completed(
        self, current: str, latest: str, update_available: bool, success: bool = True
    ) -> None:
        c = self._get_cache("ytdlp")

        if not success:
            logger.warning("yt-dlp update check failed")
            c["last_failure_time"] = datetime.now(timezone.utc).isoformat()
            c["last_check_success"] = False
            self._save_settings()
            if c.get("latest_version"):
                self._set_component_state(ComponentState("ytdlp", "available", current, c["latest_version"]))
            else:
                self._set_component_state(ComponentState("ytdlp", "error", current, error_message="Couldn't check PyPI."))
            return

        self._record_successful_check("ytdlp")

        if not update_available or not latest:
            logger.info("yt-dlp is up to date (v%s)", current)
            c["latest_version"] = ""
            self._save_settings()
            self._set_component_state(ComponentState("ytdlp", "up_to_date", current, current))
            self.has_update_available.emit(self._has_any_update_available())
            return

        self._latest_version = latest
        c["latest_version"] = latest
        logger.info("yt-dlp update available: %s -> %s", current, latest)

        # Check 24-hour failure backoff
        failed_ver = c.get("failed_version", "")
        failed_at = c.get("failed_at", "")
        in_failure_backoff = False
        if failed_ver and failed_ver == latest and failed_at:
            try:
                f_dt = datetime.fromisoformat(failed_at)
                if f_dt.tzinfo is None:
                    f_dt = f_dt.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - f_dt).total_seconds() < 24 * 3600:
                    in_failure_backoff = True
            except Exception:
                pass

        if not self._ytdlp_check_is_manual and self._auto_update and not in_failure_backoff:
            if self._queue_busy:
                self._pending_update = True
                logger.info("Update deferred — queue is busy")
                self.toast_message.emit(
                    f"yt-dlp update v{latest} available — will install when queue is idle"
                )
            else:
                self._perform_update()
        elif in_failure_backoff and not self._ytdlp_check_is_manual:
            logger.info("yt-dlp auto-update suppressed by 24h failure backoff for %s", latest)
            self._set_component_state(ComponentState(
                component="ytdlp",
                status="error",
                current_version=current,
                latest_version=latest,
                error_message=c.get("last_failure_reason", "Update failed recently. Retry available."),
            ))
            self.has_update_available.emit(True)
            return
        else:
            if c.get("last_toast_version") != latest:
                self.toast_message.emit(f"yt-dlp update available: v{current} → v{latest}")
                c["last_toast_version"] = latest

        self._save_settings()
        self._set_component_state(ComponentState("ytdlp", "available", current, latest))
        self.update_available.emit(current, latest)
        self.has_update_available.emit(True)

    def update_ytdlp(self) -> None:
        """Trigger yt-dlp update."""
        if self._is_operation_in_progress("ytdlp") and self._states["ytdlp"].status in ("downloading", "installing"):
            return
        self._set_component_state(ComponentState("ytdlp", "downloading", get_installed_ytdlp_version(), self._latest_version or ""))
        self._perform_update()

    def cancel_ytdlp_update(self) -> None:
        """Cancel running yt-dlp update and return card to available."""
        if self._update_worker:
            self._update_worker.cancel()
        if self._update_thread and self._update_thread.isRunning():
            self._update_thread.quit()
            self._update_thread.wait(1000)
        c = self._get_cache("ytdlp")
        latest = c.get("latest_version", "")
        cur = get_installed_ytdlp_version()
        if latest:
            self._set_component_state(ComponentState("ytdlp", "available", cur, latest))
        else:
            self._set_component_state(ComponentState("ytdlp", "up_to_date", cur, cur))
        self.toast_message.emit("yt-dlp update cancelled.")

    def _perform_update(self) -> None:
        self._pending_update = False
        self._set_component_state(ComponentState("ytdlp", "downloading", get_installed_ytdlp_version(), self._latest_version or ""))

        updater = UpdateChecker()
        self._update_worker = updater
        thread = QThread(self)
        self._update_thread = thread
        updater.moveToThread(thread)

        updater.progress.connect(self.ytdlp_progress.emit)
        updater.update_completed.connect(self._on_update_completed)
        updater.update_failed.connect(self._on_update_failed)
        updater.status_message.connect(self.toast_message.emit)
        thread.started.connect(updater.perform_update)

        updater.update_completed.connect(thread.quit)
        updater.update_failed.connect(thread.quit)

        thread.finished.connect(self._on_update_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(updater.deleteLater)
        thread.start()

    def _on_update_thread_cleanup(self) -> None:
        self._update_worker = None
        self._update_thread = None

    def _on_update_completed(self, new_version: str) -> None:
        invalidate_ytdlp_cache()
        logger.info("yt-dlp updated to %s", new_version)
        c = self._get_cache("ytdlp")
        c["latest_version"] = ""
        c["failed_version"] = ""
        c["failed_at"] = ""
        c["last_failure_reason"] = ""
        self._save_settings()
        self._set_component_state(ComponentState("ytdlp", "up_to_date", new_version, new_version))
        self.update_installed.emit(new_version)
        self.toast_message.emit(f"yt-dlp updated to v{new_version}")
        self.has_update_available.emit(self._has_any_update_available())

    def _on_update_failed(self, error_msg: str) -> None:
        logger.warning("yt-dlp update failed: %s (will retry at next trigger)", error_msg)
        c = self._get_cache("ytdlp")
        c["failed_version"] = c.get("latest_version", "")
        c["failed_at"] = datetime.now(timezone.utc).isoformat()
        c["last_failure_reason"] = error_msg
        c["last_check_success"] = False
        self._save_settings()
        self._set_component_state(ComponentState("ytdlp", "error", get_installed_ytdlp_version(), error_message=error_msg))

    # ── FFmpeg Check & Update ─────────────────────────────────────────────────

    def check_ffmpeg(self, force: bool = False, is_manual: bool = False) -> None:
        """Check for FFmpeg updates."""
        if not force and self._is_cooldown_active("ffmpeg", COOLDOWN_FFMPEG_SECONDS):
            return
        if self._is_operation_in_progress("ffmpeg"):
            logger.info("FFmpeg check/update already in progress — skipping")
            return

        self._ffmpeg_check_is_manual = is_manual
        cur_v = get_installed_ffmpeg_version()
        self._set_component_state(ComponentState("ffmpeg", "checking", current_version=cur_v))

        checker = FFmpegUpdateChecker()
        self._ffmpeg_checker = checker
        thread = QThread(self)
        self._ffmpeg_check_thread = thread
        checker.moveToThread(thread)

        checker.check_completed.connect(self._on_ffmpeg_check_completed)
        checker.check_failed.connect(self._on_ffmpeg_check_failed)
        thread.started.connect(checker.check)

        checker.check_completed.connect(thread.quit)
        checker.check_failed.connect(thread.quit)

        thread.finished.connect(self._on_ffmpeg_check_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(checker.deleteLater)
        thread.start()

    def _on_ffmpeg_check_thread_cleanup(self) -> None:
        self._ffmpeg_checker = None
        self._ffmpeg_check_thread = None

    def _on_ffmpeg_check_completed(
        self, cur: str, latest: str, available: bool, dl_url: str, size: str, rel_url: str
    ) -> None:
        self._latest_ffmpeg_download_url = dl_url
        c = self._get_cache("ffmpeg")
        self._record_successful_check("ffmpeg")
        c["size"] = size
        c["download_url"] = dl_url
        c["release_notes_url"] = rel_url

        if available:
            c["latest_version"] = latest

            # Check 24-hour failure backoff
            failed_ver = c.get("failed_version", "")
            failed_at = c.get("failed_at", "")
            in_failure_backoff = False
            if failed_ver and failed_ver == latest and failed_at:
                try:
                    f_dt = datetime.fromisoformat(failed_at)
                    if f_dt.tzinfo is None:
                        f_dt = f_dt.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - f_dt).total_seconds() < 24 * 3600:
                        in_failure_backoff = True
                except Exception:
                    pass

            # Manual check NEVER triggers auto-download or auto-install!
            if not self._ffmpeg_check_is_manual and self._auto_update_ffmpeg and not in_failure_backoff:
                if self._queue_busy:
                    self._pending_ffmpeg_update = True
                    logger.info("FFmpeg update deferred — queue is busy")
                    self.toast_message.emit("FFmpeg update available — will install when queue is idle")
                else:
                    self.update_ffmpeg(download_url=dl_url)
            elif in_failure_backoff and not self._ffmpeg_check_is_manual:
                logger.info("FFmpeg auto-update suppressed by 24h failure backoff for %s", latest)
                self._set_component_state(ComponentState(
                    component="ffmpeg",
                    status="error",
                    current_version=cur,
                    latest_version=latest,
                    size_str=size,
                    release_notes_url=rel_url,
                    download_url=dl_url,
                    error_message=c.get("last_failure_reason", "Update failed recently. Retry available."),
                ))
                self.has_update_available.emit(True)
                return
            else:
                if c.get("last_toast_version") != latest:
                    self.toast_message.emit(f"FFmpeg update available: {latest}")
                    c["last_toast_version"] = latest

            self._save_settings()
            self._set_component_state(ComponentState(
                component="ffmpeg",
                status="available",
                current_version=cur,
                latest_version=latest,
                size_str=size,
                release_notes_url=rel_url,
                download_url=dl_url,
            ))
            self.has_update_available.emit(True)
        else:
            c["latest_version"] = ""
            self._save_settings()
            self._set_component_state(ComponentState(
                component="ffmpeg",
                status="up_to_date",
                current_version=cur,
                latest_version=latest,
                size_str=size,
                release_notes_url=rel_url,
            ))
            self.has_update_available.emit(self._has_any_update_available())

    def _on_ffmpeg_check_failed(self, error_msg: str) -> None:
        logger.warning("FFmpeg update check failed: %s", error_msg)
        c = self._get_cache("ffmpeg")
        c["last_failure_time"] = datetime.now(timezone.utc).isoformat()
        c["last_check_success"] = False
        self._save_settings()

        cur_v = get_installed_ffmpeg_version()
        if c.get("latest_version"):
            self._set_component_state(ComponentState(
                component="ffmpeg",
                status="available",
                current_version=cur_v,
                latest_version=c["latest_version"],
                size_str=c.get("size", ""),
                release_notes_url=c.get("release_notes_url", ""),
                download_url=c.get("download_url", ""),
            ))
        else:
            self._set_component_state(ComponentState(
                component="ffmpeg",
                status="error",
                current_version=cur_v,
                error_message="Couldn't check GitHub.",
            ))

    def update_ffmpeg(self, download_url: Optional[str] = None) -> None:
        """Download and install the latest FFmpeg build in a background thread."""
        if self._is_operation_in_progress("ffmpeg") and self._states["ffmpeg"].status in ("downloading", "installing"):
            return

        url = download_url or self._latest_ffmpeg_download_url
        if not url:
            result = check_ffmpeg_update()
            if result and result.get("download_url"):
                url = result["download_url"]
                self._latest_ffmpeg_download_url = url
            else:
                self.toast_message.emit("No FFmpeg update URL found.")
                return

        self._pending_ffmpeg_update = False
        self._set_component_state(ComponentState("ffmpeg", "downloading", get_installed_ffmpeg_version(), download_url=url))
        updater = FFmpegUpdater(download_url=url)
        self._ffmpeg_updater = updater
        thread = QThread(self)
        self._ffmpeg_update_thread = thread
        updater.moveToThread(thread)

        updater.progress.connect(self._on_ffmpeg_progress_forward)
        updater.update_completed.connect(self._on_ffmpeg_completed)
        updater.update_failed.connect(self._on_ffmpeg_failed)
        thread.started.connect(updater.perform_update)

        updater.update_completed.connect(thread.quit)
        updater.update_failed.connect(thread.quit)

        thread.finished.connect(self._on_ffmpeg_update_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(updater.deleteLater)
        thread.start()

    def _on_ffmpeg_progress_forward(self, pct: int, spd: str, *args) -> None:
        self.ffmpeg_progress.emit(pct, spd)

    def _on_ffmpeg_update_thread_cleanup(self) -> None:
        self._ffmpeg_updater = None
        self._ffmpeg_update_thread = None

    def cancel_ffmpeg_update(self) -> None:
        """Cancel running FFmpeg update and return card to available."""
        if self._ffmpeg_updater:
            self._ffmpeg_updater.cancel()
        if self._ffmpeg_update_thread and self._ffmpeg_update_thread.isRunning():
            self._ffmpeg_update_thread.quit()
            self._ffmpeg_update_thread.wait(1000)
        c = self._get_cache("ffmpeg")
        latest = c.get("latest_version", "")
        cur = get_installed_ffmpeg_version()
        if latest:
            self._set_component_state(ComponentState(
                component="ffmpeg",
                status="available",
                current_version=cur,
                latest_version=latest,
                size_str=c.get("size", ""),
                release_notes_url=c.get("release_notes_url", ""),
                download_url=c.get("download_url", ""),
            ))
        else:
            self._set_component_state(ComponentState("ffmpeg", "up_to_date", cur, cur))
        self.toast_message.emit("FFmpeg update cancelled.")

    def _on_ffmpeg_completed(self, new_ver: str) -> None:
        invalidate_ffmpeg_cache()
        logger.info("FFmpeg updated to %s", new_ver)
        c = self._get_cache("ffmpeg")
        c["latest_version"] = ""
        c["failed_version"] = ""
        c["failed_at"] = ""
        c["last_failure_reason"] = ""
        self._save_settings()
        self._set_component_state(ComponentState("ffmpeg", "up_to_date", new_ver, new_ver))
        self.toast_message.emit(f"FFmpeg updated to {new_ver}")
        self.has_update_available.emit(self._has_any_update_available())

    def _on_ffmpeg_failed(self, error_msg: str) -> None:
        logger.warning("FFmpeg update failed: %s", error_msg)
        c = self._get_cache("ffmpeg")
        c["failed_version"] = c.get("latest_version", "")
        c["failed_at"] = datetime.now(timezone.utc).isoformat()
        c["last_failure_reason"] = error_msg
        c["last_check_success"] = False
        self._save_settings()
        self._set_component_state(ComponentState("ffmpeg", "error", get_installed_ffmpeg_version(), error_message=error_msg))
        self.toast_message.emit(f"FFmpeg update failed: {error_msg}")
