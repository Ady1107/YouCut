"""
Test suite for UI Responsive Breakpoints, Screen Clamping, FlowLayout,
and SocialDownloadPage (Instagram and Facebook).
"""

import os
import sys
import unittest

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from clipgrab.config.settings import AppSettings
from clipgrab.core.formats import VideoInfo
from clipgrab.data.history_db import HistoryDB
from clipgrab.gui.main_window import MainWindow
from clipgrab.gui.widgets.flow_layout import FlowLayout
from clipgrab.gui.widgets.social_page import SocialDownloadPage
from clipgrab.gui.widgets.youtube_page import YouTubePage


class TestResponsiveAndSocial(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self) -> None:
        self.settings = AppSettings.load()
        self.history_db = HistoryDB()
        self.win = MainWindow(self.settings, self.history_db)
        self.win.show()

    def tearDown(self) -> None:
        self.win.close()

    def test_main_window_min_size_and_clamping(self) -> None:
        """Verify window has sensible minimum size 900x600 and no maximum size."""
        self.assertGreaterEqual(self.win.minimumWidth(), 900)
        self.assertGreaterEqual(self.win.minimumHeight(), 600)
        self.assertEqual(self.win.maximumWidth(), 16777215)
        self.assertEqual(self.win.maximumHeight(), 16777215)

    def test_breakpoints_sidebar_and_layout_reflow(self) -> None:
        """Verify wide, medium, and narrow breakpoints reflow correctly."""
        # 1. Wide breakpoint (>= 1280)
        self.win.resize(1366, 768)
        self.win._on_debounced_resize()
        self.assertFalse(self.win._sidebar.is_collapsed())
        self.assertEqual(self.win._sidebar.width(), 190)

        yt = self.win._youtube_page
        self.assertEqual(yt._current_bp, "wide")
        self.assertEqual(yt._workspace_stack.currentIndex(), 0)  # Two-column layout
        self.assertFalse(yt._tc_is_vertical)  # Start/End side-by-side
        self.assertTrue(yt._more_btn.isHidden())
        self.assertFalse(yt._quality_btn.isHidden())

        # 2. Medium breakpoint (960 - 1279)
        self.win.resize(1024, 768)
        self.win._on_debounced_resize()
        self.assertTrue(self.win._sidebar.is_collapsed())
        self.assertEqual(self.win._sidebar.width(), 64)
        self.assertEqual(yt._current_bp, "medium")
        self.assertEqual(yt._workspace_stack.currentIndex(), 0)  # Two-column layout
        self.assertFalse(yt._tc_is_vertical)

        # 3. Narrow breakpoint (< 960)
        self.win.resize(900, 600)
        self.win._on_debounced_resize()
        self.assertTrue(self.win._sidebar.is_collapsed())
        self.assertEqual(self.win._sidebar.width(), 64)
        self.assertEqual(yt._current_bp, "narrow")
        self.assertEqual(yt._workspace_stack.currentIndex(), 1)  # One-column stacked layout
        self.assertTrue(yt._tc_is_vertical)  # Start/End stacked vertically
        self.assertFalse(yt._more_btn.isHidden())
        self.assertTrue(yt._quality_btn.isHidden())

    def test_youtube_text_elision(self) -> None:
        """Verify long titles and folder paths are elided with tooltips."""
        yt = self.win._youtube_page
        long_title = "This is an extremely long title for a video that should definitely wrap or truncate with ellipsis without breaking UI " * 2
        yt.set_loaded_state(VideoInfo(
            url="https://youtube.com/watch?v=123",
            title=long_title,
            duration=120,
        ))
        self.assertEqual(yt._video_title.toolTip(), long_title)
        self.assertTrue("…" in yt._video_title.text() or "..." in yt._video_title.text())
        self.assertEqual(yt._folder_input.toolTip(), yt._full_folder_path)

    def test_social_page_instagram(self) -> None:
        """Verify Instagram SocialDownloadPage architecture, pills, and states."""
        self.win.show_page("instagram")
        ig = self.win._instagram_page
        self.assertIsInstance(ig, SocialDownloadPage)
        self.assertEqual(ig._platform_id, "instagram")
        self.assertEqual(list(ig._type_buttons.keys()), ["Reel", "Post"])
        self.assertFalse(ig._empty_card.isHidden())
        self.assertTrue(ig._info_card.isHidden())

        # URL auto-selects Reel
        ig._on_url_text_changed("https://www.instagram.com/reel/C123456/")
        self.assertEqual(ig._active_type, "Reel")

        # URL auto-selects Post
        ig._on_url_text_changed("https://www.instagram.com/p/C987654/")
        self.assertEqual(ig._active_type, "Post")

        # Test loaded state
        info = VideoInfo(
            url="https://www.instagram.com/reel/C123456/",
            title="Mountain sunset reel #nature",
            duration=45,
            uploader="wanderer",
        )
        ig.set_loaded_state(info)
        self.assertFalse(ig._info_card.isHidden())
        self.assertFalse(ig._options_card.isHidden())
        self.assertFalse(ig._actions_box.isHidden())
        self.assertTrue(ig._empty_card.isHidden())
        self.assertIn("Mountain sunset", ig._video_title.text())
        self.assertEqual(ig._dur_pill.text(), "00:45")

    def test_social_page_facebook(self) -> None:
        """Verify Facebook SocialDownloadPage architecture, pills, and states."""
        self.win.show_page("facebook")
        fb = self.win._facebook_page
        self.assertIsInstance(fb, SocialDownloadPage)
        self.assertEqual(fb._platform_id, "facebook")
        self.assertEqual(list(fb._type_buttons.keys()), ["Reel", "Video", "Post"])
        self.assertFalse(fb._empty_card.isHidden())

        # URL auto-selects Video
        fb._on_url_text_changed("https://www.facebook.com/watch/?v=123456789")
        self.assertEqual(fb._active_type, "Video")

        # Test audio toggle
        fb.set_loaded_state(VideoInfo(
            url="https://www.facebook.com/watch/?v=123456789",
            title="Podcasts on Tech",
            duration=300,
        ))
        fb._on_mode_toggled("audio")
        self.assertTrue(fb._is_audio_only)
        self.assertIn("MP3", fb._format_chips)
        self.assertIn("audio", fb._download_btn.text().lower())

    def test_geometry_clamping_and_maximized_persistence(self) -> None:
        """Verify geometry restore clamps to screen bounds and preserves maximized flag."""
        # Simulated offscreen coordinates
        self.settings.update(window_x=-9999, window_y=-9999, window_width=20000, window_height=20000, window_maximized=False)
        self.win._restore_geometry()
        geo = self.win.geometry()
        screen = QApplication.primaryScreen().availableGeometry()
        self.assertGreaterEqual(geo.x(), screen.left())
        self.assertGreaterEqual(geo.y(), screen.top())
        self.assertLessEqual(geo.width(), screen.width())
        self.assertLessEqual(geo.height(), screen.height())

    def test_social_fetch_url_detection_and_worker_setup(self) -> None:
        """Verify Instagram and Facebook URL variants are recognized and fetch worker initializes properly."""
        from clipgrab.core.platforms import detect_platform

        ig_urls = [
            "https://www.instagram.com/reel/C7Xxyz/",
            "https://www.instagram.com/reels/C7Xxyz/",
            "https://www.instagram.com/p/C7Xxyz/?igsh=MWQ1",
            "https://instagr.am/p/C7Xxyz",
            "https://instagr.am/reel/C7Xxyz",
            "https://www.instagram.com/share/p/C7Xxyz/",
            "https://www.instagram.com/share/r/C7Xxyz/",
            "https://m.instagram.com/reels/C7Xxyz/",
        ]
        for u in ig_urls:
            p = detect_platform(u)
            self.assertEqual(p.id, "instagram", f"Failed detecting {u}")

        fb_urls = [
            "https://www.facebook.com/share/r/17qWxyz/",
            "https://www.facebook.com/share/v/17qWxyz/",
            "https://www.facebook.com/share/17qWxyz/",
            "https://facebook.com/reel/123456789012345",
            "https://facebook.com/reel/DA123456789",
            "https://m.facebook.com/reel/1234567890",
            "https://web.facebook.com/watch/?v=123456789",
            "https://fb.com/watch/?v=123456789",
            "https://fb.watch/xyz123/",
        ]
        for u in fb_urls:
            p = detect_platform(u)
            self.assertEqual(p.id, "facebook", f"Failed detecting {u}")

        # Test fetch worker initialization on Instagram page
        from unittest.mock import patch
        from clipgrab.core.downloader import DownloadWorker

        ig = self.win._instagram_page
        ig._url_input.setText("https://www.instagram.com/reel/C7Xxyz/")
        with patch.object(DownloadWorker, "execute_fetch"):
            ig._on_fetch_clicked()
            self.assertIsNotNone(ig._fetch_worker)
            self.assertIsNotNone(ig._fetch_thread)
            self.assertEqual(ig._fetch_worker.fetch_url, "https://www.instagram.com/reel/C7Xxyz/")
            self.assertEqual(ig._fetch_worker.fetch_platform_id, "instagram")
            self.assertTrue(ig._watchdog_timer.isActive())
            ig.shutdown()

        # Test fetch worker initialization on Facebook page
        fb = self.win._facebook_page
        fb._url_input.setText("https://www.facebook.com/reel/123456789012345")
        with patch.object(DownloadWorker, "execute_fetch"):
            fb._on_fetch_clicked()
            self.assertIsNotNone(fb._fetch_worker)
            self.assertIsNotNone(fb._fetch_thread)
            self.assertEqual(fb._fetch_worker.fetch_url, "https://www.facebook.com/reel/123456789012345")
            self.assertEqual(fb._fetch_worker.fetch_platform_id, "facebook")
            self.assertTrue(fb._watchdog_timer.isActive())
            fb.shutdown()


if __name__ == "__main__":
    unittest.main()
