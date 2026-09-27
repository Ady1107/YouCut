"""
YouCut — Entry point.

Launches the PySide6 GUI application with logging, theme, and clipboard detection.
Verifies bundled binaries (ffmpeg.exe, yt-dlp.exe) on startup.
"""

import sys
import os

# Add the parent directory to sys.path so absolute imports work when running 'python main.py'
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QIcon  # type: ignore  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # type: ignore  # noqa: E402

from clipgrab.core.logger import setup_logging, get_logger  # noqa: E402
from clipgrab.core.ffmpeg_utils import verify_ffmpeg, verify_ytdlp  # noqa: E402
from clipgrab.config.settings import AppSettings  # noqa: E402
from clipgrab.data.history_db import HistoryDB  # noqa: E402
from clipgrab.gui.theme import apply_theme  # noqa: E402
from clipgrab.gui.main_window import MainWindow  # noqa: E402

logger = get_logger("main")


def main() -> int:
    """Application entry point."""
    # Initialize logging
    setup_logging()
    logger.info("YouCut starting...")

    # Set explicit AppUserModelID on Windows so the taskbar displays the app icon
    # instead of the generic/default paper icon or python icon
    if sys.platform == "win32":
        try:
            import ctypes
            app_id = "shubh.youcut.clipper.app"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
            logger.debug("Explicit AppUserModelID set: %s", app_id)
        except Exception as e:
            logger.warning("Could not set AppUserModelID: %s", e)

    # Load settings
    settings = AppSettings.load()
    logger.info("Settings loaded (theme=%s)", settings.theme)

    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("YouCut")
    app.setOrganizationName("YouCut")

    # Set application icon with all available sizes and formats
    from clipgrab.core.ffmpeg_utils import _get_assets_dir  # noqa: E402
    assets_dir = _get_assets_dir()
    icon_candidates = [
        os.path.join(assets_dir, "icons", "youcut.ico"),
        os.path.join(assets_dir, "icons", "youclip.ico"),
        os.path.join(assets_dir, "icons", "youcut.png"),
        os.path.join(assets_dir, "icons", "youclip.png"),
        os.path.join(assets_dir, "assets", "icons", "youcut.ico"),
        os.path.join(assets_dir, "assets", "icons", "youclip.ico"),
        os.path.join(assets_dir, "assets", "icons", "youcut.png"),
        os.path.join(assets_dir, "assets", "icons", "youclip.png"),
    ]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        icon_candidates.extend([
            os.path.join(exe_dir, "icons", "youcut.ico"),
            os.path.join(exe_dir, "icons", "youclip.ico"),
            os.path.join(exe_dir, "icons", "youcut.png"),
            os.path.join(exe_dir, "icons", "youclip.png"),
            os.path.join(exe_dir, "assets", "icons", "youcut.ico"),
            os.path.join(exe_dir, "assets", "icons", "youclip.ico"),
        ])

    app_icon = QIcon()
    for ic in icon_candidates:
        if os.path.isfile(ic):
            app_icon.addFile(ic)
            logger.debug("Added icon source: %s", ic)

    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
        logger.info("Application icon configured successfully")

    # Apply theme
    apply_theme(app, settings.theme)

    # Verify bundled binaries
    ffmpeg_ok, ffmpeg_msg = verify_ffmpeg()
    if not ffmpeg_ok:
        logger.error("FFmpeg verification failed: %s", ffmpeg_msg)
        QMessageBox.critical(
            None,
            "YouCut — FFmpeg Error",
            f"FFmpeg is required but could not be found or is not working.\n\n{ffmpeg_msg}",
        )
        return 1

    ytdlp_ok, ytdlp_msg = verify_ytdlp()
    if not ytdlp_ok:
        logger.error("yt-dlp verification failed: %s", ytdlp_msg)
        QMessageBox.critical(
            None,
            "YouCut — yt-dlp Error",
            f"yt-dlp is required but could not be found or is not working.\n\n{ytdlp_msg}",
        )
        return 1

    logger.info("Binaries verified — FFmpeg: %s, yt-dlp: %s", ffmpeg_msg, ytdlp_msg)

    # Initialize history database
    history_db = HistoryDB()

    # Create and show main window
    window = MainWindow(settings=settings, history_db=history_db)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()

    logger.info("YouCut ready")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
