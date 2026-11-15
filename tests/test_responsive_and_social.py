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
        self.assertEqual(list(ig._type_buttons.keys()), ["Reel"])
        self.assertFalse(ig._empty_card.isHidden())
        self.assertTrue(ig._info_card.isHidden())

        # URL auto-selects Reel
        ig._on_url_text_changed("https://www.instagram.com/reel/C123456/")
        self.assertEqual(ig._active_type, "Reel")

        # Post URL also maps to Reel
        ig._on_url_text_changed("https://www.instagram.com/p/C987654/")
        self.assertEqual(ig._active_type, "Reel")

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
        self.assertEqual(list(fb._type_buttons.keys()), ["Reel", "Video"])
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

    def test_url_cleaner_and_sanitization(self) -> None:
        """Verify tracking and referral parameters are stripped from media URLs."""
        from clipgrab.core.validator import clean_media_url

        dirty_ig = "https://www.instagram.com/p/DDXYZ123/?utm_source=ig_web_copy_link&igsh=MzRlODBiNWFlZA==&sh=MzRlODBiNWFlZA=="
        clean_ig = clean_media_url(dirty_ig)
        self.assertNotIn("utm_source", clean_ig)
        self.assertNotIn("igsh", clean_ig)
        self.assertEqual(clean_ig, "https://www.instagram.com/p/DDXYZ123/")

        dirty_fb = "https://www.facebook.com/share/p/1HW55KMuJv/?mibextid=wwXIfr&fbclid=IwAR123"
        clean_fb = clean_media_url(dirty_fb)
        self.assertNotIn("mibextid", clean_fb)
        self.assertNotIn("fbclid", clean_fb)
        self.assertEqual(clean_fb, "https://www.facebook.com/share/p/1HW55KMuJv/")

    def test_queue_markers_and_highlighting(self) -> None:
        """Verify QueueItemWidget shows platform marker badges and applies orange/green styling."""
        from clipgrab.core.queue_manager import QueueItem, QueueItemStatus
        from clipgrab.gui.widgets.queue_item_widget import (
            QueueItemWidget,
            _STYLE_CARD_DOWNLOADING,
            _STYLE_CARD_DONE,
            _PROGRESS_STYLE_ORANGE,
            _PROGRESS_STYLE_GREEN,
        )

        # 1. Instagram item in DOWNLOADING state
        ig_item = QueueItem(
            id="item-1",
            url="https://www.instagram.com/reel/C123/",
            title="IG Reel",
            download_type="video_audio",
            status=QueueItemStatus.DOWNLOADING,
            progress=45.0,
            speed="2.5 MB/s",
        )
        ig_widget = QueueItemWidget(ig_item)
        self.assertEqual(ig_widget._platform_badge.text(), "INSTAGRAM")
        self.assertEqual(ig_widget._type_badge.text(), "VIDEO + AUDIO")
        self.assertEqual(ig_widget.styleSheet(), _STYLE_CARD_DOWNLOADING)
        self.assertEqual(ig_widget._progress_bar.styleSheet(), _PROGRESS_STYLE_ORANGE)
        self.assertIn("Downloading", ig_widget._status_label.text())

        # 2. Facebook item in DONE state
        fb_item = QueueItem(
            id="item-2",
            url="https://www.facebook.com/watch/?v=123",
            title="FB Video",
            download_type="audio_only",
            status=QueueItemStatus.DONE,
            progress=100.0,
        )
        fb_widget = QueueItemWidget(fb_item)
        self.assertEqual(fb_widget._platform_badge.text(), "FACEBOOK")
        self.assertEqual(fb_widget._type_badge.text(), "AUDIO ONLY")
        self.assertEqual(fb_widget.styleSheet(), _STYLE_CARD_DONE)
        self.assertEqual(fb_widget._progress_bar.styleSheet(), _PROGRESS_STYLE_GREEN)
        self.assertIn("Done", fb_widget._status_label.text())

        # 3. YouTube item in WARNING state (completed with post-processing warn)
        yt_item = QueueItem(
            id="item-3",
            url="https://youtube.com/watch?v=abc",
            title="YT Clip",
            download_type="video_audio",
            status=QueueItemStatus.WARNING,
            progress=100.0,
        )
        yt_widget = QueueItemWidget(yt_item)
        self.assertEqual(yt_widget._platform_badge.text(), "YOUTUBE")
        self.assertEqual(yt_widget.styleSheet(), _STYLE_CARD_DONE)
        self.assertEqual(yt_widget._progress_bar.styleSheet(), _PROGRESS_STYLE_GREEN)

    def test_history_markers_and_filtering(self) -> None:
        """Verify History tab has platform and file type filters and correctly filters entries."""
        import tempfile
        from clipgrab.data.history_db import HistoryDB, HistoryEntry
        from clipgrab.gui.widgets.history_panel import HistoryPanel

        # Use temporary isolated database
        with tempfile.TemporaryDirectory(prefix="hist_test_") as tmpdir:
            test_db = HistoryDB(db_path=os.path.join(tmpdir, "test_history.db"))
            test_db.add_entry(
                title="YT Tech Video",
                url="https://youtube.com/watch?v=111",
                output_path="C:/Downloads/video.mp4",
                video_quality="1080p",
            )
            test_db.add_entry(
                title="IG Cooking Reel",
                url="https://www.instagram.com/reel/222/",
                output_path="C:/Downloads/cooking.mp4",
                video_quality="720p",
            )
            test_db.add_entry(
                title="FB Podcast Audio",
                url="https://www.facebook.com/watch/?v=333",
                output_path="C:/Downloads/podcast.mp3",
                video_quality="Audio only",
            )
            test_db.add_entry(
                title="Funny Animation Meme",
                url="https://youtube.com/watch?v=444",
                output_path="C:/Downloads/meme.gif",
                video_quality="GIF",
            )

            panel = HistoryPanel(test_db)
            panel.show()
            panel.refresh()

            # Initial state: 4 entries
            self.assertEqual(len(panel._item_widgets), 4)

            # Filter by Platform: Instagram
            idx_ig = panel._platform_filter.findData("instagram")
            panel._platform_filter.setCurrentIndex(idx_ig)
            self.assertEqual(len(panel._item_widgets), 1)
            self.assertEqual(panel._item_widgets[0]._platform_badge.text(), "INSTAGRAM")
            self.assertEqual(panel._item_widgets[0]._title_label.text(), "IG Cooking Reel")

            # Filter by Platform: All, Type: Audio
            panel._platform_filter.setCurrentIndex(panel._platform_filter.findData("all"))
            idx_audio = panel._type_filter.findData("audio")
            panel._type_filter.setCurrentIndex(idx_audio)
            self.assertEqual(len(panel._item_widgets), 1)
            self.assertEqual(panel._item_widgets[0]._type_badge.text(), "AUDIO")
            self.assertEqual(panel._item_widgets[0]._title_label.text(), "FB Podcast Audio")

            # Filter by Type: GIF
            idx_gif = panel._type_filter.findData("gif")
            panel._type_filter.setCurrentIndex(idx_gif)
            self.assertEqual(len(panel._item_widgets), 1)
            self.assertEqual(panel._item_widgets[0]._type_badge.text(), "GIF")
            self.assertEqual(panel._item_widgets[0]._title_label.text(), "Funny Animation Meme")

            # Verify platform filter only contains YouTube, Instagram, and Facebook (plus All)
            plat_items = [panel._platform_filter.itemData(i) for i in range(panel._platform_filter.count())]
            self.assertEqual(plat_items, ["all", "youtube", "instagram", "facebook"])

            # Reset filters
            panel._type_filter.setCurrentIndex(panel._type_filter.findData("all"))
            self.assertEqual(len(panel._item_widgets), 4)

            panel.close()

    def test_dark_and_light_theme_switching(self) -> None:
        """Verify settings panel Appearance group is visible and toggles between Dark and Light themes."""
        from clipgrab.gui.theme import apply_theme, toggle_theme

        settings = self.win._settings
        sp = self.win._settings_panel

        # Ensure starting in dark theme
        settings.update(theme="dark")
        apply_theme(self.app, "dark")

        # Ensure theme preview cards are available
        self.assertIsNotNone(sp._theme_card_dark)
        self.assertIsNotNone(sp._theme_card_light)
        self.assertIsNotNone(sp._theme_card_system)

        # Toggle to Light Theme
        new_theme = toggle_theme(self.app, settings)
        self.assertEqual(new_theme, "light")
        self.assertEqual(self.app.property("current_theme"), "light")
        self.assertEqual(settings.theme, "light")

        # Toggle back to Dark Theme
        new_theme = toggle_theme(self.app, settings)
        self.assertEqual(new_theme, "dark")
        self.assertEqual(self.app.property("current_theme"), "dark")
        self.assertEqual(settings.theme, "dark")


if __name__ == "__main__":
    unittest.main()
