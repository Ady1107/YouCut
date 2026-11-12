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
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # type: ignore  # noqa: E402

from clipgrab.core.logger import setup_logging, get_logger  # noqa: E402
from clipgrab.core.ffmpeg_utils import verify_ffmpeg, verify_ytdlp  # noqa: E402
from clipgrab.config.settings import AppSettings  # noqa: E402
from clipgrab.data.history_db import HistoryDB  # noqa: E402
from clipgrab.gui.theme import apply_theme  # noqa: E402
from clipgrab.gui.main_window import MainWindow  # noqa: E402

logger = get_logger("main")


IPC_SERVER_NAME = "YouCut_SingleInstance_IPC"

def main() -> int:
    """Application entry point."""
    # Single-instance guard (QLockFile in ~/.youcut or YOUCUT_HOME)
    env_home = os.environ.get("YOUCUT_HOME")
    youcut_dir = os.path.abspath(env_home) if env_home else os.path.expanduser("~/.youcut")
    os.makedirs(youcut_dir, exist_ok=True)
    lock_path = os.path.join(youcut_dir, "youcut.lock")
    lock_file = QLockFile(lock_path)
    lock_file.setStaleLockTime(30000)

    if not lock_file.tryLock(150):
        # Second instance: notify existing instance to focus its window and exit
        socket = QLocalSocket()
        socket.connectToServer(IPC_SERVER_NAME)
        if socket.waitForConnected(1000):
            socket.write(b"ACTIVATE\n")
            socket.flush()
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
        print("Another instance of YouCut is already running. Focused existing window.")
        return 0

    # Initialize logging
    setup_logging()
    logger.info("YouCut starting (single-instance lock acquired)...")

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

    # Local IPC server to focus window when second instance is launched
    ipc_server = QLocalServer()
    ipc_server.removeServer(IPC_SERVER_NAME)
    ipc_server.listen(IPC_SERVER_NAME)

    def _handle_ipc_connection():
        client = ipc_server.nextPendingConnection()
        if client:
            def _on_ready_read():
                data = bytes(client.readAll()).decode("utf-8", errors="ignore")
                if "ACTIVATE" in data:
                    logger.info("Second instance launched — focusing main window")
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()
                    if sys.platform == "win32":
                        try:
                            import ctypes
                            ctypes.windll.user32.SetForegroundWindow(int(window.winId()))
                        except Exception:
                            pass
            client.readyRead.connect(_on_ready_read)

    ipc_server.newConnection.connect(_handle_ipc_connection)

    logger.info("YouCut ready")
    try:
        ret = app.exec()
    finally:
        ipc_server.close()
        lock_file.unlock()
    return ret


if __name__ == "__main__":
    sys.exit(main())
