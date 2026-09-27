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
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PySide6.QtCore import QObject, QThread, QTimer, Signal

from clipgrab.core.ffmpeg_utils import get_ytdlp_path
from clipgrab.core.logger import get_logger
from clipgrab.version import APP_VERSION, VERSION_CHECK_URL

logger = get_logger("updater")

# Minimum interval between update checks (seconds)
_CHECK_COOLDOWN_SECONDS = 3600  # 1 hour

# Idle timeout before re-checking yt-dlp (milliseconds)
_IDLE_CHECK_MS = 30 * 60 * 1000  # 30 minutes

# Subprocess flags — hide console window on Windows
_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


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


# ── App Update Checker ───────────────────────────────────────────────────────

def check_app_update(check_url: str = "", current_version: str = "") -> Optional[dict]:
    """
    Query GitHub Releases API directly for the latest release and compare against APP_VERSION.

    Returns:
        A dict with keys {version, release_notes, download_url} if an update
        is available, or None if up-to-date or the check failed.
    """
    from clipgrab import version as version_mod
    url = check_url.strip() if check_url else getattr(version_mod, "VERSION_CHECK_URL", "")
    local_ver = current_version.strip() if current_version else getattr(version_mod, "APP_VERSION", "1.0.0")
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
            return None
        resp.raise_for_status()
        data = resp.json()

        # Extract tag and clean version string
        raw_tag = data.get("tag_name", "").strip()
        remote_version = raw_tag.lstrip("vV") if raw_tag else data.get("version", "").strip()
        if not remote_version:
            logger.debug("No valid version tag found in release response")
            return None

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
                "version": remote_version,
                "release_notes": notes,
                "download_url": download_url,
            }

        logger.info("YouCut is up to date (current: v%s, latest: v%s)", APP_VERSION, remote_version)
        return None
    except requests.ConnectionError:
        logger.debug("App update check failed: no internet")
        return None
    except requests.Timeout:
        logger.debug("App update check timed out")
        return None
    except Exception as e:
        logger.debug("App update check error: %s", e)
        return None


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


# ── Background Workers ───────────────────────────────────────────────────────

class AppUpdateChecker(QObject):
    """
    Background worker that checks for a new YouCut app version.

    Signals:
        update_found: (version, release_notes, download_url)
        no_update: ()
    """

    update_found = Signal(str, str, str)
    no_update = Signal()

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
        if result:
            self.update_found.emit(
                result.get("version", ""),
                result.get("release_notes", ""),
                result.get("download_url", ""),
            )
        else:
            self.no_update.emit()


class UpdateChecker(QObject):
    """
    Background worker that checks for yt-dlp updates and performs self-update.

    Signals:
        check_completed: (current_version, latest_version, update_available)
        update_completed: (new_version,)
        update_failed: (error_message,)
        status_message: (message,)
    """

    check_completed = Signal(str, str, bool)
    update_completed = Signal(str)
    update_failed = Signal(str)
    status_message = Signal(str)

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
            self.check_completed.emit(current, "", False)
            return

        update_available = latest != current and current != "unknown"
        logger.info(
            "yt-dlp update check — installed: %s, latest: %s, update_available: %s",
            current, latest, update_available,
        )
        self.check_completed.emit(current, latest, update_available)

    def perform_update(self) -> None:
        """
        Update yt-dlp.exe using its built-in self-update mechanism (yt-dlp -U).

        Downloads the latest binary and replaces the existing one in place.
        In PyInstaller --onefile mode, targets ~/.youcut/bin/yt-dlp.exe so updates
        persist across runs.
        Emits update_completed on success, update_failed on error.
        """
        # Ensure user bin directory exists
        user_bin_dir = Path.home() / ".youcut" / "bin"
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
        logger.info("Starting yt-dlp self-update: %s -U", ytdlp_path)
        self.status_message.emit("Updating yt-dlp...")

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
                new_version = get_installed_ytdlp_version()
                logger.info("yt-dlp updated successfully to %s", new_version)
                self.update_completed.emit(new_version)
                self.status_message.emit(f"yt-dlp updated to v{new_version}")
            else:
                error_detail = stderr.strip() or stdout.strip() or "Unknown error"
                logger.error(
                    "yt-dlp self-update failed (rc=%d): %s",
                    returncode, error_detail
                )
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
            self.update_failed.emit(str(e))
        finally:
            self._active_process = None


# ── Update Manager ───────────────────────────────────────────────────────────

class UpdateManager(QObject):
    """
    Orchestrates both YouCut app update checking and yt-dlp update checking.

    App update channel:
      - Queries GitHub Releases API on startup and on manual check.
      - Emits app_update_available when a newer version exists.
      - Does NOT auto-install the app (user must download the new .exe manually).

    yt-dlp update channel:
      - Checks PyPI on startup (if cooldown elapsed), on idle timeout, and on
        extraction errors.
      - Auto-installs yt-dlp if auto_update is enabled and queue is idle.

    Signals:
        app_update_available: (version, release_notes, download_url)
        update_available: (current_ytdlp, latest_ytdlp)
        update_installed: (new_ytdlp_version,)
        toast_message: (message,)
    """

    app_update_available = Signal(str, str, str)  # version, notes, url
    update_available = Signal(str, str)           # current, latest yt-dlp
    update_installed = Signal(str)               # new yt-dlp version
    toast_message = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._auto_update: bool = True
        self._last_check_time: Optional[datetime] = None
        self._pending_update: bool = False
        self._latest_version: str = ""
        self._queue_busy: bool = False

        self._app_update_url: str = ""

        # Idle timer for yt-dlp re-checks
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(_IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        self._idle_timer.start()

        # Worker thread references (prevent GC)
        self._check_thread: Optional[QThread] = None
        self._update_thread: Optional[QThread] = None
        self._app_check_thread: Optional[QThread] = None
        self._check_worker: Optional[UpdateChecker] = None
        self._update_worker: Optional[UpdateChecker] = None
        self._app_checker: Optional[AppUpdateChecker] = None

    def shutdown(self) -> None:
        """Stop all update timers, terminate all update workers/threads, and clean up."""
        logger.info("Shutdown: Stopping update timer and update threads...")
        if self._idle_timer:
            self._idle_timer.stop()

        # Stop check worker/thread
        if self._check_worker:
            self._check_worker.cancel()
        if self._check_thread and self._check_thread.isRunning():
            self._check_thread.quit()
            if not self._check_thread.wait(2000):
                logger.warning("Shutdown: Update check thread did not stop within 2s, terminating...")
                self._check_thread.terminate()
                self._check_thread.wait(1000)
        self._check_thread = None
        self._check_worker = None

        # Stop update worker/thread
        if self._update_worker:
            self._update_worker.cancel()
        if self._update_thread and self._update_thread.isRunning():
            self._update_thread.quit()
            if not self._update_thread.wait(2000):
                logger.warning("Shutdown: Update thread did not stop within 2s, terminating...")
                self._update_thread.terminate()
                self._update_thread.wait(1000)
        self._update_thread = None
        self._update_worker = None

        # Stop app check worker/thread
        if self._app_checker:
            self._app_checker.cancel()
        if self._app_check_thread and self._app_check_thread.isRunning():
            self._app_check_thread.quit()
            if not self._app_check_thread.wait(2000):
                logger.warning("Shutdown: App check thread did not stop within 2s, terminating...")
                self._app_check_thread.terminate()
                self._app_check_thread.wait(1000)
        self._app_check_thread = None
        self._app_checker = None
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
        self._auto_update = value
        logger.info("Auto-update set to %s", value)

    def set_queue_busy(self, busy: bool) -> None:
        """Inform whether the download queue is currently active."""
        was_busy = self._queue_busy
        self._queue_busy = busy
        if was_busy and not busy and self._pending_update:
            logger.info("Queue idle — applying pending yt-dlp update")
            self._perform_update()

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
        Run all update checks on app startup (non-blocking).

        - Always checks for a new YouCut app version.
        - Checks yt-dlp if cooldown has elapsed.
        """
        logger.info("Startup update checks triggered")

        # App update check — always runs on startup
        self._run_app_check()

        # yt-dlp update check — respects cooldown
        if self._should_check():
            self._run_check()
        else:
            logger.info("yt-dlp startup check skipped (checked recently)")

        self._restart_idle_timer()

    def on_extraction_error(self) -> None:
        """Trigger an immediate yt-dlp update check (bypasses cooldown)."""
        logger.info("Extraction error triggered yt-dlp update check")
        self._run_check(force=True)

    def manual_check(self) -> None:
        """Force both update checks (triggered by user in Settings)."""
        logger.info("Manual update check triggered")
        self._run_app_check(is_manual=True)
        self._run_check(force=True)

    def reset_idle_timer(self) -> None:
        """Reset the idle timer (call on user interaction)."""
        self._restart_idle_timer()

    # ── Private: App Update ───────────────────────────────────────────────

    def _run_app_check(self, is_manual: bool = False) -> None:
        """Run the YouCut app version check in a background thread."""
        checker = AppUpdateChecker(check_url=self._app_update_url)
        self._app_checker = checker
        thread = QThread(self)
        checker.moveToThread(thread)

        checker.update_found.connect(self._on_app_update_found)
        if is_manual:
            checker.no_update.connect(
                lambda: self.toast_message.emit(f"YouCut is up to date (v{APP_VERSION}).")
            )
        checker.no_update.connect(lambda: thread.quit())
        checker.update_found.connect(lambda *_: thread.quit())
        thread.started.connect(checker.run)

        def _on_app_done():
            self._app_checker = None
            self._app_check_thread = None

        thread.finished.connect(_on_app_done)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(checker.deleteLater)

        self._app_check_thread = thread
        thread.start()

    def _on_app_update_found(self, version: str, notes: str, url: str) -> None:
        """Handle detection of a new YouCut app version."""
        logger.info("YouCut app update found: v%s", version)
        self.app_update_available.emit(version, notes, url)
        self.toast_message.emit(
            f"YouCut v{version} is available! Check Settings to download."
        )

    # ── Private: yt-dlp Update ────────────────────────────────────────────

    def _should_check(self) -> bool:
        if self._last_check_time is None:
            return True
        now = datetime.now(timezone.utc)
        last = self._last_check_time
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() >= _CHECK_COOLDOWN_SECONDS

    def _restart_idle_timer(self) -> None:
        self._idle_timer.stop()
        self._idle_timer.start()

    def _on_idle_timeout(self) -> None:
        logger.info("Idle timeout — checking for yt-dlp updates")
        if self._should_check():
            self._run_check()
        self._restart_idle_timer()

    def _run_check(self, force: bool = False) -> None:
        if not force and not self._should_check():
            return

        checker = UpdateChecker()
        self._check_worker = checker
        thread = QThread(self)
        checker.moveToThread(thread)

        checker.check_completed.connect(self._on_check_completed)
        thread.started.connect(checker.check_for_update)

        def _on_check_done():
            self._check_worker = None
            self._check_thread = None

        thread.finished.connect(_on_check_done)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(checker.deleteLater)

        self._check_thread = thread
        checker.check_completed.connect(lambda *_: thread.quit())
        thread.start()

    def _on_check_completed(
        self, current: str, latest: str, update_available: bool
    ) -> None:
        self._last_check_time = datetime.now(timezone.utc)

        if not update_available or not latest:
            logger.info("yt-dlp is up to date (v%s)", current)
            return

        self._latest_version = latest
        logger.info("yt-dlp update available: %s -> %s", current, latest)
        self.update_available.emit(current, latest)

        if self._auto_update:
            if self._queue_busy:
                self._pending_update = True
                logger.info("Update deferred — queue is busy")
                self.toast_message.emit(
                    f"yt-dlp update v{latest} available — will install when queue is idle"
                )
            else:
                self._perform_update()
        else:
            self.toast_message.emit(
                f"yt-dlp update available: v{current} → v{latest}"
            )

    def _perform_update(self) -> None:
        self._pending_update = False

        updater = UpdateChecker()
        self._update_worker = updater
        thread = QThread(self)
        updater.moveToThread(thread)

        updater.update_completed.connect(self._on_update_completed)
        updater.update_failed.connect(self._on_update_failed)
        updater.status_message.connect(self.toast_message.emit)
        thread.started.connect(updater.perform_update)

        def _on_update_done():
            self._update_worker = None
            self._update_thread = None

        thread.finished.connect(_on_update_done)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(updater.deleteLater)

        updater.update_completed.connect(lambda _: thread.quit())
        updater.update_failed.connect(lambda _: thread.quit())

        self._update_thread = thread
        thread.start()

    def _on_update_completed(self, new_version: str) -> None:
        logger.info("yt-dlp updated to %s", new_version)
        self.update_installed.emit(new_version)
        self.toast_message.emit(f"yt-dlp updated to v{new_version}")

    def _on_update_failed(self, error_msg: str) -> None:
        logger.warning("yt-dlp update failed: %s (will retry at next trigger)", error_msg)
