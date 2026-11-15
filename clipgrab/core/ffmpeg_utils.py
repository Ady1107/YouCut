"""
YouCut — FFmpeg and yt-dlp binary utility functions.

Resolves the bundled ffmpeg.exe and yt-dlp.exe paths for both development and
PyInstaller-frozen environments, and verifies the binaries are present and executable.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from clipgrab.core.logger import get_logger

logger = get_logger("ffmpeg")


_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

_CACHED_FFMPEG_PATH: Optional[str] = None
_CACHED_YTDLP_PATH: Optional[str] = None
_CACHED_FFMPEG_VERIFICATION: Optional[tuple[bool, str]] = None
_CACHED_YTDLP_VERIFICATION: Optional[tuple[bool, str]] = None


def _can_run(binary_path: str, version_arg: str = "-version") -> tuple[bool, str]:
    """Test run binary with version argument and return (is_ok, output)."""
    try:
        r = subprocess.run(
            [binary_path, version_arg],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATION_FLAGS,
        )
        if r.returncode == 0:
            return True, (r.stdout or "").strip()
        return False, ""
    except Exception:
        return False, ""


def _get_assets_dir() -> str:
    """
    Get the directory containing bundled binaries (ffmpeg.exe, yt-dlp.exe).

    In a PyInstaller frozen build (onedir mode), binaries live next to the .exe.
    In development, they're in clipgrab/assets/.
    """
    if getattr(sys, "frozen", False):
        # PyInstaller onedir: binaries are in the same folder as the .exe
        base_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # Dev mode: relative to this file -> clipgrab/core/ -> clipgrab/assets/
        base_path = str(Path(__file__).resolve().parent.parent / "assets")
    return base_path


def get_ffmpeg_path() -> str:
    """
    Resolve the path to ffmpeg.exe.

    Searches in:
      1. Persistent user-updated copy in ~/.youcut/bin/ffmpeg.exe (if it exists and passes verification)
      2. sys._MEIPASS (PyInstaller temp folder in --onefile mode)
      3. Folder next to the executable
      4. Development assets folder
      5. System PATH

    Returns:
        Absolute path to ffmpeg.exe.
    """
    global _CACHED_FFMPEG_PATH
    if _CACHED_FFMPEG_PATH and os.path.isfile(_CACHED_FFMPEG_PATH):
        return _CACHED_FFMPEG_PATH

    # 1. User updated copy in ~/.youcut/bin/ (persists across runs/updates)
    env_home = os.environ.get("YOUCUT_HOME")
    base_dir = Path(env_home).resolve() if env_home else Path.home() / ".youcut"
    user_ffmpeg = base_dir / "bin" / "ffmpeg.exe"
    if user_ffmpeg.is_file():
        ok, _ = _can_run(str(user_ffmpeg), "-version")
        if ok:
            logger.debug("Resolved user ffmpeg path: %s", user_ffmpeg)
            _CACHED_FFMPEG_PATH = str(user_ffmpeg)
            return str(user_ffmpeg)
        else:
            logger.warning("User ffmpeg at %s failed verification — falling back to bundled assets", user_ffmpeg)

    candidates = [
        os.path.join(_get_assets_dir(), "ffmpeg.exe"),
        os.path.join(_get_assets_dir(), "assets", "ffmpeg.exe"),
    ]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.extend([
            os.path.join(exe_dir, "ffmpeg.exe"),
            os.path.join(exe_dir, "assets", "ffmpeg.exe"),
        ])

    for path in candidates:
        if os.path.isfile(path):
            logger.debug("Resolved ffmpeg path: %s", path)
            _CACHED_FFMPEG_PATH = path
            return path

    import shutil
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        logger.debug("Resolved ffmpeg from PATH: %s", system_ffmpeg)
        _CACHED_FFMPEG_PATH = system_ffmpeg
        return system_ffmpeg

    # Fallback to default
    fallback = os.path.join(_get_assets_dir(), "ffmpeg.exe")
    logger.debug("Falling back to ffmpeg path: %s", fallback)
    _CACHED_FFMPEG_PATH = fallback
    return fallback


def get_ffmpeg_directory() -> str:
    """
    Get the directory containing ffmpeg.exe (for yt-dlp's --ffmpeg-location flag).

    Returns:
        The directory path containing the ffmpeg binary.
    """
    return os.path.dirname(get_ffmpeg_path())


def get_ytdlp_path() -> str:
    """
    Resolve the path to yt-dlp.exe.

    Search priority:
      1. Resolves ~/.youcut/bin/yt-dlp.exe vs bundled and picks the newer one.
      2. System PATH
      3. Fallback

    Returns:
        Absolute path to yt-dlp.exe.
    """
    global _CACHED_YTDLP_PATH
    if _CACHED_YTDLP_PATH and os.path.isfile(_CACHED_YTDLP_PATH):
        return _CACHED_YTDLP_PATH

    env_home = os.environ.get("YOUCUT_HOME")
    base_dir = Path(env_home).resolve() if env_home else Path.home() / ".youcut"
    user_ytdlp = base_dir / "bin" / "yt-dlp.exe"
    if not user_ytdlp.is_file():
        legacy = Path.home() / ".clipgrab" / "bin" / "yt-dlp.exe"
        if legacy.is_file():
            user_ytdlp = legacy

    user_candidate: Optional[str] = None
    user_ver_str: Optional[str] = None
    if user_ytdlp.is_file():
        ok, out = _can_run(str(user_ytdlp), "--version")
        if ok and out:
            user_candidate = str(user_ytdlp)
            user_ver_str = out

    bundled_candidates = [
        os.path.join(_get_assets_dir(), "yt-dlp.exe"),
        os.path.join(_get_assets_dir(), "assets", "yt-dlp.exe"),
    ]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        bundled_candidates.extend([
            os.path.join(exe_dir, "yt-dlp.exe"),
            os.path.join(exe_dir, "assets", "yt-dlp.exe"),
        ])

    bundled_candidate: Optional[str] = None
    bundled_ver_str: Optional[str] = None
    for bp in bundled_candidates:
        if os.path.isfile(bp):
            ok, out = _can_run(bp, "--version")
            if ok and out:
                bundled_candidate = bp
                bundled_ver_str = out
                break

    resolved: Optional[str] = None
    if user_candidate and bundled_candidate and user_ver_str and bundled_ver_str:
        try:
            from packaging import version
            u_v = version.parse(user_ver_str.strip().lstrip("vV"))
            b_v = version.parse(bundled_ver_str.strip().lstrip("vV"))
            if u_v >= b_v:
                logger.debug("Selected user yt-dlp (v%s >= bundled v%s)", user_ver_str, bundled_ver_str)
                resolved = user_candidate
            else:
                logger.debug("Selected bundled yt-dlp (bundled v%s > user v%s)", bundled_ver_str, user_ver_str)
                resolved = bundled_candidate
        except Exception:
            resolved = user_candidate
    elif user_candidate:
        resolved = user_candidate
    elif bundled_candidate:
        resolved = bundled_candidate

    if resolved:
        _CACHED_YTDLP_PATH = resolved
        return resolved

    # 3. System PATH
    import shutil
    system_ytdlp = shutil.which("yt-dlp")
    if system_ytdlp:
        logger.debug("Resolved yt-dlp from PATH: %s", system_ytdlp)
        _CACHED_YTDLP_PATH = system_ytdlp
        return system_ytdlp

    # Fallback
    fallback = os.path.join(_get_assets_dir(), "yt-dlp.exe")
    logger.debug("Falling back to yt-dlp path: %s", fallback)
    _CACHED_YTDLP_PATH = fallback
    return fallback


# ── In-Memory Verification Caches ───────────────────────────────────────────

def invalidate_ffmpeg_cache() -> None:
    """Invalidate cached FFmpeg verification result and path (call after update)."""
    global _CACHED_FFMPEG_VERIFICATION, _CACHED_FFMPEG_PATH
    _CACHED_FFMPEG_VERIFICATION = None
    _CACHED_FFMPEG_PATH = None


def invalidate_ytdlp_cache() -> None:
    """Invalidate cached yt-dlp verification result and path (call after update)."""
    global _CACHED_YTDLP_VERIFICATION, _CACHED_YTDLP_PATH
    _CACHED_YTDLP_VERIFICATION = None
    _CACHED_YTDLP_PATH = None


def verify_ffmpeg(force: bool = False) -> tuple[bool, str]:
    """
    Verify that the bundled ffmpeg.exe exists and can run.

    Checks that the file exists, then runs 'ffmpeg -version' to confirm
    it's a valid, executable binary. Caches result in memory to avoid
    spawning redundant subprocesses at startup.

    Args:
        force: If True, re-executes ffmpeg -version ignoring cache.

    Returns:
        A tuple of (is_ok, version_string_or_error_message).
    """
    global _CACHED_FFMPEG_VERIFICATION
    if force:
        invalidate_ffmpeg_cache()
    elif _CACHED_FFMPEG_VERIFICATION is not None:
        return _CACHED_FFMPEG_VERIFICATION

    ffmpeg_path = get_ffmpeg_path()

    if not os.path.isfile(ffmpeg_path):
        error_msg = (
            f"FFmpeg binary not found at:\n{ffmpeg_path}\n\n"
            "YouCut requires ffmpeg.exe to be bundled in the assets folder.\n"
            "Please download from https://github.com/BtbN/FFmpeg-Builds/releases\n"
            "and place ffmpeg.exe in the assets/ directory."
        )
        logger.error("FFmpeg not found: %s", ffmpeg_path)
        _CACHED_FFMPEG_VERIFICATION = (False, error_msg)
        return False, error_msg

    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            error_msg = f"FFmpeg returned non-zero exit code: {result.returncode}"
            logger.error(error_msg)
            _CACHED_FFMPEG_VERIFICATION = (False, error_msg)
            return False, error_msg

        first_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        version_info = first_line.strip()
        logger.info("FFmpeg verified: %s", version_info)
        _CACHED_FFMPEG_VERIFICATION = (True, version_info)
        return True, version_info

    except FileNotFoundError:
        error_msg = f"FFmpeg binary is not executable: {ffmpeg_path}"
        logger.error(error_msg)
        _CACHED_FFMPEG_VERIFICATION = (False, error_msg)
        return False, error_msg
    except subprocess.TimeoutExpired:
        error_msg = "FFmpeg verification timed out (10s)"
        logger.error(error_msg)
        _CACHED_FFMPEG_VERIFICATION = (False, error_msg)
        return False, error_msg
    except OSError as e:
        error_msg = f"FFmpeg execution failed: {e}"
        logger.error(error_msg)
        _CACHED_FFMPEG_VERIFICATION = (False, error_msg)
        return False, error_msg


def verify_ytdlp(force: bool = False) -> tuple[bool, str]:
    """
    Verify that the bundled yt-dlp.exe exists and can run. Caches in memory.

    Args:
        force: If True, re-executes yt-dlp --version ignoring cache.

    Returns:
        A tuple of (is_ok, version_string_or_error_message).
    """
    global _CACHED_YTDLP_VERIFICATION
    if force:
        invalidate_ytdlp_cache()
    elif _CACHED_YTDLP_VERIFICATION is not None:
        return _CACHED_YTDLP_VERIFICATION

    ytdlp_path = get_ytdlp_path()

    if not os.path.isfile(ytdlp_path):
        error_msg = (
            f"yt-dlp binary not found at:\n{ytdlp_path}\n\n"
            "YouCut requires yt-dlp.exe to be bundled in the assets folder.\n"
            "Please download from https://github.com/yt-dlp/yt-dlp/releases\n"
            "and place yt-dlp.exe in the assets/ directory."
        )
        logger.error("yt-dlp not found: %s", ytdlp_path)
        _CACHED_YTDLP_VERIFICATION = (False, error_msg)
        return False, error_msg

    try:
        result = subprocess.run(
            [ytdlp_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 0:
            error_msg = f"yt-dlp returned non-zero exit code: {result.returncode}"
            logger.error(error_msg)
            _CACHED_YTDLP_VERIFICATION = (False, error_msg)
            return False, error_msg

        version = result.stdout.strip()
        logger.info("yt-dlp verified: %s", version)
        _CACHED_YTDLP_VERIFICATION = (True, version)
        return True, version

    except FileNotFoundError:
        error_msg = f"yt-dlp binary is not executable: {ytdlp_path}"
        logger.error(error_msg)
        _CACHED_YTDLP_VERIFICATION = (False, error_msg)
        return False, error_msg
    except subprocess.TimeoutExpired:
        error_msg = "yt-dlp verification timed out (10s)"
        logger.error(error_msg)
        _CACHED_YTDLP_VERIFICATION = (False, error_msg)
        return False, error_msg
    except OSError as e:
        error_msg = f"yt-dlp execution failed: {e}"
        logger.error(error_msg)
        _CACHED_YTDLP_VERIFICATION = (False, error_msg)
        return False, error_msg


def get_ffprobe_path() -> Optional[str]:
    """
    Resolve the path to ffprobe.exe if it exists alongside ffmpeg.

    Returns:
        Absolute path to ffprobe.exe, or None if not found.
    """
    ffprobe_path = os.path.join(_get_assets_dir(), "ffprobe.exe")
    if os.path.isfile(ffprobe_path):
        return ffprobe_path
    return None
