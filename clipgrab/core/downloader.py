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
from clipgrab.core.validator import clean_media_url, sanitize_filename

logger = logging.getLogger("downloader")

_CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0
_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+\.\d+)%")
_FFMPEG_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")

_last_executed_command: list[str] = []

def get_last_executed_command() -> str:
    """Return the last executed yt-dlp or ffmpeg command string."""
    return " ".join(_last_executed_command)


def get_js_runtime_args() -> list[str]:
    """
    Detect available JavaScript runtimes (node, deno, bun, quickjs) for yt-dlp.
    Recent yt-dlp releases require a JS runtime to solve YouTube's n-sig / JS challenges.
    Node is installed on standard systems but disabled by default in yt-dlp unless passed explicitly.
    """
    import shutil
    for runtime in ("node", "deno", "bun", "quickjs"):
        if shutil.which(runtime):
            return ["--js-runtimes", runtime]
    return []


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
        convert_to_gif: bool = False,
        platform_id: str = "youtube",
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
        self.convert_to_gif = convert_to_gif
        self.platform_id = platform_id


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
    download_finished_with_warning = Signal(str, str)
    download_failed = Signal(str, bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.fetch_url: str = ""
        self.fetch_platform_id: str = "youtube"  # set before starting thread
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
            self.fetch_info(self.fetch_url, platform_id=self.fetch_platform_id)

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
        logger.info("request_cancel() invoked: setting _cancel_requested=True")
        self._cancel_requested = True
        self.kill_process_tree()

    def is_cancel_requested(self) -> bool:
        return self._cancel_requested

    def fetch_info(self, url: str, platform_id: str = "youtube") -> None:
        """
        Fetch video/media info via yt-dlp -J.

        Args:
            url: The media URL to fetch.
            platform_id: The platform ID from PlatformConfig.id.
                YouTube keeps --no-playlist so playlist URLs aren't auto-expanded.
                Non-YouTube platforms (Instagram carousels, etc.) omit it so that
                multi-item posts return their full entries list.
        """
        self._cancel_requested = False
        url = clean_media_url(url)
        ytdlp_path = get_ytdlp_path()
        cmd = [
            ytdlp_path,
            "-J",
            "--ignore-errors",
            "--force-ipv4",
            "--socket-timeout", "20",
            "--retries", "5",
            "--extractor-retries", "3",
        ]
        cmd.extend(get_js_runtime_args())

        # --no-playlist: only needed for YouTube to prevent playlist expansion.
        # Instagram carousels ARE represented as a "playlist" in yt-dlp, so we
        # must NOT pass --no-playlist for non-YouTube yt-dlp platforms.
        if platform_id in ("youtube",):
            cmd.append("--no-playlist")

        cmd += ["--ffmpeg-location", get_ffmpeg_directory(), url]

        try:
            import subprocess
            self._fetch_process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
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

        except subprocess.TimeoutExpired:
            if self._cancel_requested:
                logger.info("fetch_info cancelled during timeout.")
                return
            logger.warning("Primary fetch timed out for URL '%s'. Attempting fallback retry with android/web client...", url)
            if self._process_pid > 0:
                kill_process_tree(self._process_pid)

            # Fast fallback attempt with android client
            fallback_cmd = [
                ytdlp_path,
                "-J",
                "--ignore-errors",
                "--force-ipv4",
                "--socket-timeout", "15",
                "--retries", "5",
                "--extractor-retries", "2",
            ]
            if platform_id in ("youtube",):
                fallback_cmd.extend(["--no-playlist", "--extractor-args", "youtube:player_client=android,web"])
            fallback_cmd.extend(get_js_runtime_args())
            fallback_cmd += ["--ffmpeg-location", get_ffmpeg_directory(), url]

            try:
                import subprocess
                retry_proc = subprocess.Popen(
                    fallback_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=_CREATION_FLAGS,
                    encoding="utf-8",
                    errors="replace",
                )
                self._fetch_process = retry_proc
                self._process_pid = retry_proc.pid
                stdout_retry, stderr_retry = retry_proc.communicate(timeout=25)
                if retry_proc.returncode == 0 and stdout_retry.strip():
                    info_dict = json.loads(stdout_retry.strip())
                    video_info = parse_video_info(info_dict)
                    self.info_fetched.emit(video_info)
                    return
            except Exception as fb_err:
                logger.error("Fallback fetch retry failed: %s", fb_err)

            self.info_fetch_failed.emit(
                "Fetching video details timed out. The connection was slow or YouTube temporarily throttled requests. Please click 'Retry Fetch'.",
                False,
            )

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
                speed = re.sub(r"\x1b\[[0-9;]*m", "", speed).strip()
                eta = re.sub(r"\x1b\[[0-9;]*m", "", eta).strip()
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
                        speed_str = ""
                        br_match = re.search(r"bitrate=\s*([0-9.]+)\s*(\w+)/s", line)
                        if br_match:
                            br_val = float(br_match.group(1))
                            br_unit = br_match.group(2).lower()
                            if "m" in br_unit:
                                speed_str = f"{br_val / 8:.1f} MB/s"
                            else:
                                speed_str = f"{br_val / 8:.0f} KB/s"
                        else:
                            sp_match = re.search(r"speed=\s*([0-9.]+)x", line)
                            if sp_match:
                                speed_str = f"{sp_match.group(1)}x"
                        self.progress_updated.emit(percent, speed_str or "Processing...", "", 0, 0)

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
        if not output_path or not os.path.isfile(output_path):
            logger.warning("Download completed but output file not found in %s", request.output_dir)
            self.download_finished_with_warning.emit("", "Output file not found after download completed")
            self._process = None
            return

        file_size = os.path.getsize(output_path)
        if file_size == 0:
            logger.warning("Output file is empty (0 bytes): %s", output_path)
            self.download_finished_with_warning.emit(output_path, "Output file is empty (0 bytes)")
            self._process = None
            return

        # Handle GIF conversion if requested
        if getattr(request, "convert_to_gif", False):
            self.status_changed.emit("Converting to GIF with high-quality palette...")
            gif_path = os.path.splitext(output_path)[0] + ".gif"
            ffmpeg_exe = os.path.join(get_ffmpeg_directory(), "ffmpeg.exe")
            conv_cmd = [
                ffmpeg_exe, "-y",
                "-i", output_path,
                "-vf", "fps=15,scale=flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                gif_path
            ]
            global _last_executed_command
            _last_executed_command = list(conv_cmd)
            logger.info("Executing GIF conversion: %s", " ".join(conv_cmd))
            try:
                import subprocess
                conv_res = subprocess.run(conv_cmd, capture_output=True, text=True, creationflags=_CREATION_FLAGS)
                if conv_res.returncode == 0 and os.path.isfile(gif_path) and os.path.getsize(gif_path) > 0:
                    output_path = gif_path
                    file_size = os.path.getsize(output_path)
                    # Check GIF size warning (> 10MB)
                    if file_size > 10 * 1024 * 1024:
                        size_mb = file_size / (1024 * 1024)
                        reason = f"Large GIF size ({size_mb:.1f} MB)"
                        logger.warning("GIF file size is large: %.1f MB", size_mb)
                        self.download_finished_with_warning.emit(output_path, reason)
                        self._process = None
                        return
                else:
                    logger.error("GIF conversion failed: %s", conv_res.stderr)
                    self.download_finished_with_warning.emit(output_path, "GIF conversion failed; kept MP4")
                    self._process = None
                    return
            except Exception as e:
                logger.error("GIF conversion exception: %s", e)

        # Post-download validation: video stream & duration check
        ffmpeg_exe = os.path.join(get_ffmpeg_directory(), "ffmpeg.exe")
        try:
            import subprocess
            probe_res = subprocess.run(
                [ffmpeg_exe, "-i", output_path],
                capture_output=True,
                text=True,
                errors="replace",
                creationflags=_CREATION_FLAGS,
            )
            probe_text = probe_res.stderr
            has_video = "Stream #" in probe_text and "Video:" in probe_text
            has_audio = "Stream #" in probe_text and "Audio:" in probe_text

            if not request.audio_only and not getattr(request, "convert_to_gif", False):
                if not has_video:
                    reason = "No video stream detected in output file"
                    logger.warning("Post-download validation failed: %s", reason)
                    self.download_finished_with_warning.emit(output_path, reason)
                    self._process = None
                    return
            elif request.audio_only:
                if not has_audio:
                    reason = "No audio stream detected in output file"
                    logger.warning("Post-download validation failed: %s", reason)
                    self.download_finished_with_warning.emit(output_path, reason)
                    self._process = None
                    return

            # Duration tolerance check for clipped downloads
            if request.start_time is not None and request.end_time is not None:
                requested_duration = request.end_time - request.start_time
                dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe_text)
                if dur_match and requested_duration > 0:
                    h, m, s = float(dur_match.group(1)), float(dur_match.group(2)), float(dur_match.group(3))
                    actual_duration = h * 3600 + m * 60 + s
                    delta = abs(actual_duration - requested_duration)
                    # For stream-copy (no re-encode), cuts land on nearest keyframes (GOP).
                    # Keyframe intervals can be 5-10s. If actual_duration is within 15s or 35% of requested,
                    # this is completely normal stream-copy behavior and a valid download!
                    max_tol = 2.0 if (request.frame_accurate_clipping or request.force_keyframes) else max(15.0, requested_duration * 0.35)
                    if actual_duration < requested_duration * 0.5 or delta > max_tol:
                        reason = f"Duration mismatch: {actual_duration:.1f}s vs {requested_duration:.1f}s requested (delta {delta:.2f}s)"
                        logger.warning("Post-download validation warning: %s", reason)
                        self.download_finished_with_warning.emit(output_path, reason)
                        self._process = None
                        return
        except Exception as e:
            logger.warning("Post-download validation probe exception: %s", e)

        logger.info("Download completed & validated: %s", output_path)
        self.download_finished.emit(output_path)
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

        cf = getattr(request, "concurrent_fragments", None)
        if not cf:
            try:
                from clipgrab.config.settings import AppSettings
                cf = AppSettings.load().concurrent_fragments
            except Exception:
                cf = 16

        cmd = [
            ytdlp_path,
            "--ffmpeg-location", get_ffmpeg_directory(),
            "-o", output_template,
            "--newline",
            "--progress",
            "--progress-template", "%(progress)j",
            "--no-mtime",
            "--continue",
            "--force-ipv4",
            "--socket-timeout", "20",
            "--retries", "10",
            "--fragment-retries", "10",
            "--file-access-retries", "3",
            "--buffer-size", "1024K",
            "--http-chunk-size", "10M",
            "--concurrent-fragments", str(cf),
            "--downloader-args", "ffmpeg_i:-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        ]
        cmd.extend(get_js_runtime_args())

        if getattr(request, "platform_id", "youtube") in ("youtube",):
            cmd.append("--no-playlist")

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
            cmd.append("--force-overwrites")

            if request.frame_accurate_clipping or request.force_keyframes:
                cmd.append("--force-keyframes-at-cuts")
                logger.info("Clip: using --force-keyframes-at-cuts (re-encode, frame-accurate)")
            else:
                logger.info("Clip: using stream-copy (default, no re-encode)")

        cmd.append(request.url)
        global _last_executed_command
        _last_executed_command = list(cmd)
        return cmd

    def _handle_json_progress(self, data: dict) -> None:
        status = data.get("status", "")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total > 0 else 0.0

            speed_val = data.get("speed")
            speed_str = data.get("_speed_str", "")
            if not speed_str and speed_val:
                if speed_val >= 1024 * 1024:
                    speed_str = f"{speed_val / (1024 * 1024):.1f} MB/s"
                elif speed_val >= 1024:
                    speed_str = f"{speed_val / 1024:.0f} KB/s"
                else:
                    speed_str = f"{speed_val:.0f} B/s"
            elif speed_str:
                speed_str = re.sub(r"\x1b\[[0-9;]*m", "", speed_str)

            eta_val = data.get("eta")
            eta_str = data.get("_eta_str", "")
            if not eta_str and eta_val is not None:
                m, s = divmod(int(eta_val), 60)
                h, m = divmod(m, 60)
                eta_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            elif eta_str:
                eta_str = re.sub(r"\x1b\[[0-9;]*m", "", eta_str)

            self.progress_updated.emit(percent, speed_str.strip(), eta_str.strip(), downloaded, total)
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

    # 1. Network & Connection Timeouts / Drops (Must check BEFORE content restrictions!)
    if any(k in stderr_lower for k in (
        "error number -138",
        "timed out",
        "connect timeout",
        "connection timed out",
        "fragment 1 not found",
        "unable to download video data",
        "connection refused",
        "connection reset",
        "network is unreachable",
        "name resolution",
        "urlopen error",
        "temporary failure in name resolution",
        "failed to connect",
    )):
        return (
            "Connection to the media server timed out or failed. "
            "Please check your internet connection and retry."
        )

    # 2. Bot verification / Captcha check
    if "bot" in stderr_lower and any(w in stderr_lower for w in ("sign in", "confirm", "robot", "captcha", "checkpoint")):
        return "YouTube requested verification or sign-in. This content requires login and can't be downloaded by YouCut."

    # 3. Age restriction
    if re.search(r"\b(age[- ]restricted|confirm your age|inappropriate for some users|sign in to confirm your age)\b", stderr_lower):
        return "This content requires login and can't be downloaded by YouCut."

    # 4. Private / Sign-in required
    if any(k in stderr_lower for k in ("private video", "this video is private", "sign in to view this video")):
        return "This content requires login and can't be downloaded by YouCut."

    if any(k in stderr_lower for k in ("members-only", "join this channel")):
        return "This video is available to channel members only and can't be downloaded by YouCut."

    if any(k in stderr_lower for k in ("registered users", "login", "log in", "checkpoint", "not granting access", "empty media response", "authentication")):
        return "This content requires login and can't be downloaded by YouCut."

    # 5. Content status & availability
    if "unavailable" in stderr_lower or "removed" in stderr_lower or "video has been deleted" in stderr_lower:
        return "This media is unavailable. It may have been removed or made private."
    if "copyright" in stderr_lower:
        return "This media is unavailable due to a copyright claim."
    if "region" in stderr_lower or "country" in stderr_lower:
        return "This media is not available in your region."
    if "premiere" in stderr_lower:
        return "This video is a scheduled premiere and is not yet available for download."
    if "live" in stderr_lower and ("not started" in stderr_lower or "offline" in stderr_lower):
        return "This live stream has not started yet or is no longer available."

    # 6. Formats & Encoding
    if "no video formats" in stderr_lower:
        if is_extraction:
            return (
                "No downloadable video found in this post. "
                "(Note: Photo-only posts cannot be downloaded as videos)."
            )
        return "The selected format is not available. Try selecting a different quality."
    if "format" in stderr_lower and "not available" in stderr_lower:
        if is_extraction:
            return (
                "No video stream available for this post. "
                "The post may be photo-only or restricted."
            )
        return "The selected format is not available. Try selecting a different quality."

    # 8. FFmpeg binary errors
    if "ffmpeg" in stderr_lower and ("not found" in stderr_lower or "cannot find" in stderr_lower or "does not exist" in stderr_lower):
        return "FFmpeg error during video processing. The bundled FFmpeg may be corrupted or missing."

    # 9. Fallback
    if is_extraction:
        return (
            f"Failed to extract video information.\n\n"
            f"This might be caused by a platform change. Try updating yt-dlp.\n\n"
            f"Details: {stderr[:300]}"
        )

    if stderr:
        return f"Download failed:\n{stderr[:500]}"
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
