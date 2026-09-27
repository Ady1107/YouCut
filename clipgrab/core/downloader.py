import json
import logging
import os
import re
import traceback
import time
from typing import Optional, Any
from datetime import datetime

from PySide6.QtCore import QObject, Signal, Slot, QProcess, QProcessEnvironment, QTimer

from clipgrab.core.ffmpeg_utils import get_ytdlp_path, get_ffmpeg_directory
from clipgrab.core.formats import VideoInfo, parse_video_info
from clipgrab.core.validator import sanitize_filename

logger = logging.getLogger("downloader")

_CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0
_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+\.\d+)%")
_FFMPEG_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")


class DownloadRequest:
    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        video_format: Optional[Any] = None,
        audio_format: Optional[Any] = None,
        merge_format: str = "mp4",
        audio_only: bool = False,
        audio_output_format: Optional[Any] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        force_keyframes: bool = False,
        frame_accurate_clipping: bool = False,
        concurrent_fragments: int = 16,
    ):
        self.url = url
        self.output_dir = output_dir
        self.filename = filename
        self.video_format = video_format
        self.audio_format = audio_format
        self.merge_format = merge_format
        self.audio_only = audio_only
        self.audio_output_format = audio_output_format
        self.start_time = start_time
        self.end_time = end_time
        self.force_keyframes = force_keyframes
        self.frame_accurate_clipping = frame_accurate_clipping
        self.concurrent_fragments = concurrent_fragments


def kill_process_tree(pid: int) -> None:
    """Kill a process and all its child processes on Windows using taskkill /F /T."""
    if not pid or pid <= 0:
        return
    logger.info("Terminating process tree for PID: %d", pid)
    if os.name == "nt":
        try:
            import subprocess
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                creationflags=_CREATION_FLAGS,
                timeout=5,
            )
        except Exception as e:
            logger.warning("taskkill failed for PID %d: %s", pid, e)
    else:
        try:
            import signal
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


class DownloadWorker(QObject):
    info_fetched = Signal(VideoInfo)
    info_fetch_failed = Signal(str, bool)

    progress_updated = Signal(float, str, str, int, int)
    status_changed = Signal(str)
    download_finished = Signal(str)
    download_failed = Signal(str, bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.fetch_url: str = ""
        self.download_request: Optional[DownloadRequest] = None
        
        self._process: Optional[QProcess] = None
        self._fetch_process: Optional[Any] = None
        self._process_pid: int = 0
        self._current_request: Optional[DownloadRequest] = None
        self._watchdog_timer: Optional[QTimer] = None
        self._buffer: str = ""
        self._cancel_requested: bool = False
        self._stalled: bool = False
        self._last_progress_time: float = 0.0
        self._received_first_progress: bool = False
        self._download_start_time: float = 0.0
        self._output_log: list[str] = []

    def execute_fetch(self) -> None:
        if self.fetch_url:
            self.fetch_info(self.fetch_url)

    def execute_download(self) -> None:
        if self.download_request:
            self.start_download(self.download_request)

    def kill_process_tree(self, clean_partial: bool = False) -> None:
        """Forcefully terminate active download/fetch processes and their entire process trees."""
        self._cancel_requested = True

        # Stop watchdog timer immediately
        if self._watchdog_timer:
            try:
                self._watchdog_timer.stop()
                self._watchdog_timer.deleteLater()
            except Exception:
                pass
            self._watchdog_timer = None

        # Kill QProcess tree if running
        pid = self._process_pid
        if self._process:
            try:
                qpid = self._process.processId()
                if qpid > 0:
                    pid = qpid
            except Exception:
                pass

        if pid > 0:
            logger.info("Killing active worker process tree (PID: %d)", pid)
            kill_process_tree(pid)

        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            try:
                self._process.kill()
            except Exception:
                pass

        # Kill fetch subprocess if active
        if self._fetch_process and self._fetch_process.poll() is None:
            fpid = self._fetch_process.pid
            logger.info("Killing active fetch worker process tree (PID: %d)", fpid)
            kill_process_tree(fpid)
            try:
                self._fetch_process.kill()
            except Exception:
                pass

        # Clean up partial / stub files if requested (e.g. on stall, not on normal stop/pause)
        if self._current_request:
            time.sleep(0.15)  # Allow OS to release file handles after taskkill
            self._cleanup_stub_files(self._current_request, preserve_part=not clean_partial)

    @Slot()
    def request_cancel(self) -> None:
        logger.info("[DEBUG] request_cancel() invoked")
        self.kill_process_tree()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def fetch_info(self, url: str) -> None:
        self._cancel_requested = False
        ytdlp_path = get_ytdlp_path()
        cmd = [
            ytdlp_path,
            "-J",
            "--no-playlist",
            "--ffmpeg-location", get_ffmpeg_directory(),
            url,
        ]

        try:
            import subprocess
            self._fetch_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=_CREATION_FLAGS,
                encoding="utf-8",
                errors="replace",
            )
            self._process_pid = self._fetch_process.pid
            logger.info("fetch_info started with PID: %d", self._process_pid)

            stdout, stderr = self._fetch_process.communicate(timeout=60)
            returncode = self._fetch_process.returncode

            if self._cancel_requested:
                logger.info("fetch_info was cancelled, aborting.")
                return

            if returncode != 0:
                stderr = stderr.strip()
                is_extraction = _is_extraction_related(stderr)
                error_msg = _classify_error(stderr, is_extraction)
                logger.error("yt-dlp info fetch failed (rc=%d): %s", returncode, stderr)
                self.info_fetch_failed.emit(error_msg, is_extraction)
                return

            stdout = stdout.strip()
            if not stdout:
                self.info_fetch_failed.emit("No information returned for this URL.", False)
                return

            info_dict = json.loads(stdout)
            video_info = parse_video_info(info_dict)
            self.info_fetched.emit(video_info)

        except Exception as e:
            if self._cancel_requested:
                logger.info("fetch_info exception during cancel: %s", e)
                return
            logger.error("Failed to fetch info: %s", e)
            self.info_fetch_failed.emit(f"Failed to fetch info: {e}", False)
        finally:
            self._fetch_process = None

    def start_download(self, request: DownloadRequest) -> None:
        self._cancel_requested = False
        self._stalled = False
        self._output_log.clear()
        self._buffer = ""
        self._current_request = request
        self._received_first_progress = False
        self._download_start_time = time.time()
        self.status_changed.emit("Fetching video info...")
        logger.info("Starting download: %s -> %s", request.url, request.filename)

        cmd = self._build_command(request)
        # Log the full command for debugging stream-copy vs re-encode
        logger.info("=== FULL yt-dlp COMMAND ===")
        logger.info("Argument list: %s", cmd)
        logger.info("Command string: %s", " ".join(cmd))
        is_clip = request.start_time is not None and request.end_time is not None
        if is_clip:
            mode = "FRAME-ACCURATE (re-encode)" if request.frame_accurate_clipping else "STREAM-COPY (fast)"
            logger.info("Clip mode: %s  |  Range: %.1f - %.1f", mode, request.start_time, request.end_time)
        else:
            logger.info("Mode: FULL VIDEO (no clipping)")
        logger.info("==========================")

        self._process = QProcess()
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self._process.setProcessEnvironment(env)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        
        self._process.readyReadStandardOutput.connect(self._on_ready_read)
        self._process.finished.connect(self._on_process_finished)
        self._process.errorOccurred.connect(self._on_process_error)
        self._process.started.connect(self._on_process_started)
        
        self._last_progress_time = time.time()
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(30000)  # Check every 30s for better responsiveness
        self._watchdog_timer.timeout.connect(self._on_watchdog_timeout)
        self._watchdog_timer.start()
        logger.info("Watchdog started at %s (90s stall threshold)", datetime.now().strftime("%H:%M:%S"))
        
        self._process.start(cmd[0], cmd[1:])
        if self._process.processId() > 0:
            self._process_pid = self._process.processId()
            logger.info("Download process started with PID: %d", self._process_pid)

    def _on_process_started(self) -> None:
        if self._process:
            self._process_pid = self._process.processId()
            logger.info("yt-dlp QProcess started with PID: %d", self._process_pid)

    def _on_ready_read(self) -> None:
        if not self._process:
            return
            
        now = time.time()
        elapsed_since_last = now - self._last_progress_time
        self._last_progress_time = now
        logger.debug("Watchdog timer reset at %s (%.1fs since last output)",
                     datetime.now().strftime("%H:%M:%S.%f")[:-3], elapsed_since_last)
        
        byte_data = self._process.readAllStandardOutput().data()
        if isinstance(byte_data, memoryview):
            byte_data = byte_data.tobytes()
        elif isinstance(byte_data, str):
            byte_data = byte_data.encode("utf-8", errors="replace")
            
        data = byte_data.decode("utf-8", errors="replace")

        self._buffer += data
        
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._process_line(line.strip())

    def _process_line(self, line: str) -> None:
        if not line:
            return
            
        self._output_log.append(line)
        
        # Log yt-dlp phase transitions for diagnosability
        if line.startswith("[") and not line.startswith("{"):
            logger.info("yt-dlp: %s", line[:200])
        
        # Transition from "Fetching video info..." to "Starting download..." 
        # once we see actual download/progress activity
        if not self._received_first_progress:
            if "[download]" in line or line.startswith("{") or "[Merger]" in line:
                self._received_first_progress = True
                pre_progress_elapsed = time.time() - self._download_start_time
                logger.info("First progress data received after %.1fs", pre_progress_elapsed)
                self.status_changed.emit("Starting download...")
        
        if line.startswith("{"):
            try:
                data = json.loads(line)
                self._handle_json_progress(data)
                return
            except json.JSONDecodeError:
                pass

        if "[download]" in line:
            match = _PROGRESS_RE.search(line)
            if match:
                percent = float(match.group(1))
                speed = self._extract_field(line, "at", "ETA") or ""
                eta = self._extract_field(line, "ETA", None) or ""
                self.progress_updated.emit(percent, speed, eta, 0, 0)
                
        elif "time=" in line and "bitrate=" in line:
            match = _FFMPEG_RE.search(line)
            if match and self._current_request:
                req = self._current_request
                if req.start_time is not None and req.end_time is not None:
                    duration = req.end_time - req.start_time
                    if duration > 0:
                        h, m, s = map(float, match.groups())
                        elapsed = h * 3600 + m * 60 + s
                        percent = min((elapsed / duration) * 100, 99.9)
                        self.progress_updated.emit(percent, "ffmpeg", "", 0, 0)

        if "[Merger]" in line or "[ExtractAudio]" in line or "[ffmpeg]" in line:
            self.status_changed.emit("Processing with FFmpeg...")

    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        if self._watchdog_timer:
            self._watchdog_timer.stop()
            self._watchdog_timer.deleteLater()
            self._watchdog_timer = None
            
        request = self._current_request
        if not request:
            return

        if self._cancel_requested:
            # Preserve partial .part files so resuming picks up from existing fragments
            self._cleanup_stub_files(request, preserve_part=True)
            self.status_changed.emit("Download cancelled")
            self.download_failed.emit("Download was cancelled by user.", False)
            self._process = None
            return

        if self._stalled:
            self._cleanup_stub_files(request)
            self.status_changed.emit("Download stalled")
            self.download_failed.emit("Download stalled for over 90 seconds.", False)
            self._process = None
            return

        if exit_code != 0:
            self._cleanup_stub_files(request)
            stderr = "\n".join(self._output_log)
            is_extraction = _is_extraction_related(stderr)
            error_msg = _classify_error(stderr, is_extraction)
            logger.error("yt-dlp download failed (rc=%d): %s", exit_code, stderr)
            self.download_failed.emit(error_msg, is_extraction)
            self._process = None
            return

        output_path = self._find_output_file(request)
        if output_path:
            logger.info("Download completed: %s", output_path)
            self.download_finished.emit(output_path)
        else:
            logger.warning("Download completed but output file not found in %s", request.output_dir)
            self.download_finished.emit("")
            
        self._process = None

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        logger.error("QProcess error occurred: %s", error)
        if error == QProcess.ProcessError.FailedToStart:
            ytdlp_path = get_ytdlp_path()
            logger.error("yt-dlp.exe failed to start: %s", ytdlp_path)
            self.download_failed.emit(f"yt-dlp.exe failed to start at:\n{ytdlp_path}", False)

    def _on_watchdog_timeout(self) -> None:
        if self._process and self._process.state() == QProcess.ProcessState.Running:
            elapsed = time.time() - self._last_progress_time
            logger.info("Watchdog check at %s: %.1fs since last output (threshold: 90s)",
                        datetime.now().strftime("%H:%M:%S"), elapsed)
            if elapsed > 90:
                logger.error("Download stalled for >90s (last output %.1fs ago). Killing yt-dlp process tree.", elapsed)
                self._stalled = True
                self.kill_process_tree(clean_partial=True)
            else:
                logger.debug("Watchdog: process still alive, last output %.1fs ago — OK", elapsed)

    def _cleanup_stub_files(self, request: DownloadRequest, preserve_part: bool = False) -> None:
        import glob
        from pathlib import Path
        safe_filename = sanitize_filename(request.filename)
        stem = Path(safe_filename).stem
        patterns = [
            os.path.join(request.output_dir, f"{safe_filename}.*"),
            os.path.join(request.output_dir, f"{stem}.*"),
        ]
        seen = set()
        for pattern in patterns:
            for path in glob.glob(pattern):
                if path not in seen and os.path.isfile(path):
                    seen.add(path)
                    try:
                        # If preserving partial fragments for resume, do not remove .part or .ytdl
                        if preserve_part and path.endswith((".part", ".ytdl")):
                            continue
                        if path.endswith((".part", ".ytdl", ".temp", ".tmp")):
                            os.remove(path)
                            logger.info("Cleaned up partial file: %s", path)
                    except OSError as e:
                        logger.debug("Could not remove stub file %s: %s", path, e)

    def _build_command(self, request: DownloadRequest) -> list[str]:
        ytdlp_path = get_ytdlp_path()
        safe_filename = sanitize_filename(request.filename)
        output_template = os.path.join(request.output_dir, f"{safe_filename}.%(ext)s")

        cmd = [
            ytdlp_path,
            "--ffmpeg-location", get_ffmpeg_directory(),
            "-o", output_template,
            "--newline",
            "--progress",
            "--progress-template", "%(progress)j",
            "--no-playlist",
            "--no-mtime",
            "--continue",
            "--concurrent-fragments", str(getattr(request, "concurrent_fragments", 16)),
        ]

        if request.audio_only:
            cmd.extend([
                "-f", "bestaudio/best",
                "-x",
                "--audio-format", request.audio_output_format.value if request.audio_output_format else "mp3",
                "--audio-quality", "0",
            ])
        else:
            fmt_parts: list[str] = []
            if request.video_format:
                fmt_parts.append(request.video_format.format_id)
            else:
                fmt_parts.append("bestvideo")
            if request.audio_format:
                fmt_parts.append(request.audio_format.format_id)
            else:
                fmt_parts.append("bestaudio")

            fmt_str = "+".join(fmt_parts) + "/best"
            cmd.extend(["-f", fmt_str])
            cmd.extend(["--merge-output-format", request.merge_format])

        if request.start_time is not None and request.end_time is not None:
            section = f"*{request.start_time}-{request.end_time}"
            cmd.extend(["--download-sections", section])

            if request.frame_accurate_clipping or request.force_keyframes:
                cmd.append("--force-keyframes-at-cuts")
                logger.info("Clip: using --force-keyframes-at-cuts (re-encode, frame-accurate)")
            else:
                # Stream-copy is the default behavior of --download-sections.
                # yt-dlp + ffmpeg will mux/copy streams without re-encoding
                # unless --force-keyframes-at-cuts is explicitly passed.
                # No extra args needed — this is the fast path.
                logger.info("Clip: using stream-copy (default, no re-encode)")

        cmd.append(request.url)
        return cmd

    def _handle_json_progress(self, data: dict) -> None:
        status = data.get("status", "")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total > 0 else 0.0
            speed_str = data.get("_speed_str", "")
            eta_str = data.get("_eta_str", "")
            if speed_str:
                speed_str = re.sub(r"\[\d+m", "", speed_str)
            if eta_str:
                eta_str = re.sub(r"\[\d+m", "", eta_str)
            self.progress_updated.emit(percent, speed_str.strip(), eta_str.strip(), 0, 0)
        elif status == "finished":
            self.progress_updated.emit(100.0, "Processing...", "", 0, 0)
            self.status_changed.emit("Download complete. Processing...")

    @staticmethod
    def _extract_field(line: str, start_marker: str, end_marker: Optional[str]) -> Optional[str]:
        try:
            idx = line.index(start_marker)
            value_start = idx + len(start_marker)
            if end_marker:
                end_idx = line.index(end_marker, value_start)
                return line[value_start:end_idx].strip()
            return line[value_start:].strip()
        except ValueError:
            return None

    def _find_output_file(self, request: DownloadRequest) -> Optional[str]:
        safe_filename = sanitize_filename(request.filename)
        output_dir = request.output_dir

        if request.audio_only:
            ext_val = request.audio_output_format.value if request.audio_output_format else "mp3"
            extensions = [ext_val, "m4a", "opus", "webm", "ogg", "mp3"]
        else:
            extensions = [request.merge_format, "mp4", "mkv", "webm"]

        for ext in extensions:
            path = os.path.join(output_dir, f"{safe_filename}.{ext}")
            if os.path.isfile(path):
                return path

        try:
            for entry in os.scandir(output_dir):
                if entry.is_file() and entry.name.startswith(safe_filename):
                    return entry.path
        except OSError:
            pass

        return None


def _classify_error(stderr: str, is_extraction: bool) -> str:
    stderr_lower = stderr.lower()
    if "private video" in stderr_lower:
        return "This video is private. You need the owner's permission to access it."
    if "sign in" in stderr_lower or "age" in stderr_lower:
        return "This video is age-restricted and requires sign-in. Age-restricted videos are not currently supported."
    if "unavailable" in stderr_lower or "removed" in stderr_lower:
        return "This video is unavailable. It may have been removed or made private."
    if "copyright" in stderr_lower:
        return "This video is unavailable due to a copyright claim."
    if "region" in stderr_lower or "country" in stderr_lower:
        return "This video is not available in your region."
    if "premiere" in stderr_lower:
        return "This video is a scheduled premiere and is not yet available for download."
    if "live" in stderr_lower and ("not started" in stderr_lower or "offline" in stderr_lower):
        return "This live stream has not started yet or is no longer available."
    if "urlopen error" in stderr_lower or "connection" in stderr_lower or "network" in stderr_lower:
        return "No internet connection. Please check your network and try again."
    if "ffmpeg" in stderr_lower and ("not found" in stderr_lower or "error" in stderr_lower):
        return "FFmpeg error during video processing. The bundled FFmpeg may be corrupted or missing."
    if "no video formats" in stderr_lower or "format" in stderr_lower and "not available" in stderr_lower:
        return "The selected format is not available. Try selecting a different quality."

    if is_extraction:
        return (
            f"Failed to extract video information.\\n\\n"
            f"This might be caused by a YouTube change. Try updating yt-dlp.\\n\\n"
            f"Details: {stderr[:300]}"
        )

    if stderr:
        return f"Download failed:\\n{stderr[:500]}"
    return "Download failed with an unknown error."


def _is_extraction_related(stderr: str) -> bool:
    patterns = [
        "unable to extract",
        "extractorerror",
        "unsupportederror",
        "signature",
        "cipher",
        "this video is unavailable",
        "unable to download webpage",
        "no video formats found",
        "regex",
        "json",
        "keyerror",
        "indexerror",
    ]
    stderr_lower = stderr.lower()
    return any(p in stderr_lower for p in patterns)
