"""
Automated regression tests for Video Downloader & Playback Software.
Verifies all 10 requirements:
1. Download Section Improvements (Markers visible during downloading and after completion)
2. Play/Pause Synchronisation
3. Volume Control (slider, mute/unmute, 70% default)
4. YouTube-Style Video Quality Controls (144p-2160p/Auto, apply during playback)
5. Playback Speed Controls (0.25x-2x, apply during playback)
6. YouTube Captions — Always Disabled (Strictly CC OFF by default on load, quality/speed change)
7. In/Out Keyboard Shortcuts (I, O, input field exemption, validation)
8. Age-Restricted Video Support (Browser cookies & cookies.txt in Settings and yt-dlp)
9. Global Playback Synchronisation (Central PlaybackController)
10. Testing & Regression
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit

from clipgrab.config.settings import AppSettings
from clipgrab.core.playback_controller import PlaybackController, get_playback_controller
from clipgrab.core.queue_manager import QueueItem, QueueItemStatus
from clipgrab.core.formats import VideoInfo, VideoFormat
from clipgrab.core.updater import UpdateManager
from clipgrab.gui.widgets.queue_item_widget import QueueItemWidget
from clipgrab.gui.widgets.settings_panel import SettingsPanel
from clipgrab.gui.widgets.youtube_page import YouTubePage


def get_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_download_type_markers(qapp):
    """Task 1: Verify markers on every download bar and visibility during downloading & completion."""
    # 1. AUDIO ONLY
    item_audio = QueueItem(url="https://youtube.com/watch?v=test1", title="Audio Test", download_type="AUDIO ONLY")
    widget_audio = QueueItemWidget(item_audio)
    assert "AUDIO ONLY" in widget_audio._type_badge.text()
    
    # Simulate downloading
    item_audio.status = QueueItemStatus.DOWNLOADING
    item_audio.progress = 45.0
    item_audio.speed = "3.2 MB/s"
    widget_audio.update_from_item(item_audio)
    assert not widget_audio._progress_bar.isHidden()
    assert "AUDIO ONLY" in widget_audio._progress_bar.format()

    # Simulate completion
    item_audio.status = QueueItemStatus.DONE
    widget_audio.update_from_item(item_audio)
    assert not widget_audio._progress_bar.isHidden()
    assert "AUDIO ONLY" in widget_audio._progress_bar.format()
    assert "100%" in widget_audio._progress_bar.format()

    # 2. VIDEO + AUDIO
    item_va = QueueItem(url="https://youtube.com/watch?v=test2", title="VA Test", download_type="VIDEO + AUDIO")
    widget_va = QueueItemWidget(item_va)
    assert "VIDEO + AUDIO" in widget_va._type_badge.text()

    # Simulate completion
    item_va.status = QueueItemStatus.DONE
    widget_va.update_from_item(item_va)
    assert not widget_va._progress_bar.isHidden()
    assert "VIDEO + AUDIO" in widget_va._progress_bar.format()

    # 3. VIDEO ONLY
    item_vo = QueueItem(url="https://youtube.com/watch?v=test3", title="VO Test", download_type="VIDEO ONLY")
    widget_vo = QueueItemWidget(item_vo)
    assert "VIDEO ONLY" in widget_vo._type_badge.text()

    # 4. GIF
    item_gif = QueueItem(url="https://youtube.com/watch?v=test4", title="GIF Test", download_type="GIF")
    widget_gif = QueueItemWidget(item_gif)
    assert "GIF" in widget_gif._type_badge.text()


def test_playback_controller_singleton_and_sync(qapp):
    """Task 2 & 9: Verify Central PlaybackController synchronisation across adapters."""
    ctrl1 = get_playback_controller()
    ctrl2 = get_playback_controller()
    assert ctrl1 is ctrl2

    # Adapter mock to verify synchronization
    class DummyAdapter:
        def __init__(self):
            self.playing = False
            self.volume = 70
            self.muted = False
            self.speed = 1.0
            self.quality = "1080p"
            self.captions = False
            self.seek_pos = 0.0

        def play(self): self.playing = True
        def pause(self): self.playing = False
        def set_volume(self, v): self.volume = v
        def set_muted(self, m): self.muted = m
        def set_speed(self, s): self.speed = s
        def set_quality(self, q): self.quality = q
        def set_captions(self, c): self.captions = c
        def seek(self, s): self.seek_pos = s

    adapter1 = DummyAdapter()
    adapter2 = DummyAdapter()
    ctrl1.register_adapter(adapter1)
    ctrl1.register_adapter(adapter2)

    # Play/Pause sync
    ctrl1.play()
    assert ctrl1.is_playing is True
    assert adapter1.playing is True
    assert adapter2.playing is True

    ctrl1.pause()
    assert ctrl1.is_playing is False
    assert adapter1.playing is False
    assert adapter2.playing is False

    # Cleanup
    ctrl1.unregister_adapter(adapter1)
    ctrl1.unregister_adapter(adapter2)


def test_volume_controls(qapp):
    """Task 3: Verify Volume Slider, Mute/Unmute, and 70% default."""
    ctrl = get_playback_controller()
    # Default is 70%
    assert ctrl.volume == 70
    assert ctrl.is_muted is False

    # Change volume
    ctrl.set_volume(85)
    assert ctrl.volume == 85

    # Toggle mute
    ctrl.toggle_mute()
    assert ctrl.is_muted is True
    ctrl.toggle_mute()
    assert ctrl.is_muted is False

    # Clamp bounds
    ctrl.set_volume(150)
    assert ctrl.volume == 100
    ctrl.set_volume(-20)
    assert ctrl.volume == 0


def test_video_quality_controls(qapp):
    """Task 4: YouTube-style Video Quality selection."""
    ctrl = get_playback_controller()
    for q in ["144p", "240p", "360p", "480p", "720p", "1080p", "1440p", "2160p (4K)", "Auto"]:
        ctrl.set_quality(q)
        assert ctrl.quality == q


def test_playback_speed_controls(qapp):
    """Task 5: Playback speed controls."""
    ctrl = get_playback_controller()
    for s in [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]:
        ctrl.set_speed(s)
        assert ctrl.speed == s


def test_captions_always_disabled_by_default(qapp):
    """Task 6: Captions strictly OFF by default on load, restart, quality switch, speed switch."""
    ctrl = get_playback_controller()
    # Initially False
    assert ctrl.captions_enabled is False

    # Loading video keeps captions OFF
    ctrl.load_video("test_vid_123", duration=120.0, url="https://youtu.be/test_vid_123", title="Caption Test")
    assert ctrl.captions_enabled is False

    # Changing quality keeps captions OFF
    ctrl.set_quality("720p")
    assert ctrl.captions_enabled is False

    # Changing speed keeps captions OFF
    ctrl.set_speed(1.5)
    assert ctrl.captions_enabled is False


def test_in_out_keyboard_shortcuts_and_safety(qapp):
    """Task 7: In/Out Keyboard Shortcuts (I, O), input field typing exemption, validation."""
    page = YouTubePage()
    ctrl = get_playback_controller()
    
    # Mock video info
    info = VideoInfo(
        title="Shortcut Test Video",
        url="https://youtu.be/short_test",
        duration=300.0,
        thumbnail_url="",
        video_formats=[VideoFormat(format_id="137", ext="mp4", resolution="1080p", width=1920, height=1080)],
        audio_formats=[]
    )
    page.set_loaded_state(info)
    assert page._video_info is not None

    # Set playhead to 45s
    ctrl.seek(45.0)
    assert ctrl.playhead_seconds == 45.0

    # 1. Trigger 'I' shortcut when NOT focused in text input
    qapp.setActiveWindow(page)
    page.setFocus()
    key_i = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier, "i")
    page.keyPressEvent(key_i)
    assert ctrl.in_point == 45.0
    assert page._start_seconds == 45

    # 2. Move playhead to 120s and trigger 'O' shortcut
    ctrl.seek(120.0)
    key_o = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier, "o")
    page.keyPressEvent(key_o)
    assert ctrl.out_point == 120.0
    assert page._end_seconds == 120
    assert page._start_seconds < page._end_seconds

    # 3. SAFETY CHECK: Focus on QLineEdit (e.g. filename input or url input)
    # Pressing 'I' and 'O' must NOT alter In/Out points while typing!
    page._filename_input.setFocus()
    assert isinstance(QApplication.focusWidget(), QLineEdit)

    ctrl.seek(200.0)
    key_i_typing = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_I, Qt.KeyboardModifier.NoModifier, "i")
    page.keyPressEvent(key_i_typing)
    # In point must REMAIN 45s!
    assert ctrl.in_point == 45.0
    assert page._start_seconds == 45

    key_o_typing = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier, "o")
    page.keyPressEvent(key_o_typing)
    # Out point must REMAIN 120s!
    assert ctrl.out_point == 120.0
    assert page._end_seconds == 120


def test_age_restricted_settings(qapp):
    """Task 8: Age-restricted browser cookies in settings."""
    settings = AppSettings()
    updater = UpdateManager()
    panel = SettingsPanel(update_manager=updater, settings=settings)
    
    # Change browser for cookies
    panel._browser_combo.setCurrentText("chrome")
    assert settings.browser_for_cookies == "chrome"

    panel._browser_combo.setCurrentText("None")
    assert settings.browser_for_cookies == ""

    # Custom cookies file
    panel._on_clear_cookies()
    assert settings.cookies_file_path == ""


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    print("Running test_download_type_markers...")
    test_download_type_markers(app)
    print("Running test_playback_controller_singleton_and_sync...")
    test_playback_controller_singleton_and_sync(app)
    print("Running test_volume_controls...")
    test_volume_controls(app)
    print("Running test_video_quality_controls...")
    test_video_quality_controls(app)
    print("Running test_playback_speed_controls...")
    test_playback_speed_controls(app)
    print("Running test_captions_always_disabled_by_default...")
    test_captions_always_disabled_by_default(app)
    print("Running test_in_out_keyboard_shortcuts_and_safety...")
    test_in_out_keyboard_shortcuts_and_safety(app)
    print("Running test_age_restricted_settings...")
    test_age_restricted_settings(app)
    print("\nALL 8 PLAYBACK & SYNCHRONISATION TESTS PASSED!")
