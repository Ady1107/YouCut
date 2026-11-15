"""
Unit & Integration Tests for YouCut History Thumbnails and Clear All Feature.

Verifies:
1. Safe DB migration adding 'thumbnail_path' while keeping legacy rows intact.
2. Backfill priority order: (a) cache -> (b) file frame -> (c) YouTube ID -> (d) placeholder.
3. 'Clear all' removes DB rows + thumbnails, while preserving downloaded files by default.
4. 'Clear all' with delete_files=True deletes existing downloaded files (ignoring missing ones and preserving active downloads).
5. Home page Recent Downloads refreshes to empty state after clearing history.
6. Deleting a single history entry removes its cached thumbnail file.
7. Missing files ('File not found') do not remove or hide the thumbnail.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from clipgrab.core.thumbnail_service import (
    ThumbnailService,
    extract_youtube_id,
    generate_placeholder_pixmap,
    get_thumbnail_dir,
    is_audio_entry,
    make_rounded_pixmap,
)
from clipgrab.data.history_db import HistoryDB, HistoryEntry
from clipgrab.gui.widgets.history_item_widget import HistoryItemWidget
from clipgrab.gui.widgets.history_panel import ClearHistoryDialog, HistoryPanel
from clipgrab.gui.widgets.home_page import HomePage, RecentDownloadCard


class TestHistoryThumbnailsAndClear(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="youcut_thumb_test_")
        os.environ["YOUCUT_HOME"] = self.temp_dir
        self.db_path = os.path.join(self.temp_dir, "history.db")
        self.thumb_dir = Path(self.temp_dir) / "thumbnails"
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        ThumbnailService.get_instance().clear_cache()

    def tearDown(self) -> None:
        ThumbnailService.get_instance().clear_cache()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_db_migration_keeps_old_rows(self) -> None:
        """Verify that opening a legacy DB without 'thumbnail_path' migrates safely and keeps all rows."""
        # 1. Create a legacy table without 'thumbnail_path'
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                start_time TEXT DEFAULT '',
                end_time TEXT DEFAULT '',
                video_quality TEXT DEFAULT '',
                audio_quality TEXT DEFAULT '',
                output_path TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                downloaded_at TEXT DEFAULT (datetime('now', 'localtime')),
                thumbnail_url TEXT DEFAULT ''
            )
        """)
        # Insert 3 legacy rows
        for i in range(1, 4):
            conn.execute(
                "INSERT INTO downloads (title, url, output_path, thumbnail_url) VALUES (?, ?, ?, ?)",
                (f"Legacy Video {i}", f"https://youtube.com/watch?v=legacy{i}", f"C:/fake/path{i}.mp4", f"https://img.com/{i}.jpg"),
            )
        conn.commit()
        conn.close()

        # 2. Initialize HistoryDB which runs _init_schema migration
        db = HistoryDB(self.db_path)
        self.assertEqual(db.get_count(), 3)

        # 3. Verify columns now include 'thumbnail_path'
        conn2 = sqlite3.connect(self.db_path)
        cols = [r[1] for r in conn2.execute("PRAGMA table_info(downloads)").fetchall()]
        conn2.close()
        self.assertIn("thumbnail_path", cols)

        # 4. Verify existing entries are intact with empty thumbnail_path string
        entries = db.get_all()
        self.assertEqual(len(entries), 3)
        for e in entries:
            self.assertTrue(e.title.startswith("Legacy Video"))
            self.assertEqual(e.thumbnail_path, "")

    def test_thumbnail_backfill_order(self) -> None:
        """
        Verify backfill resolution priority:
        a. cache exists on disk -> use it
        b. else if file exists -> extract frame via ffmpeg
        c. else if YouTube URL -> derive https://i.ytimg.com/vi/<id>/mqdefault.jpg
        d. else -> show placeholder
        """
        db = HistoryDB(self.db_path)
        service = ThumbnailService.get_instance()

        # Create dummy image to use as test source
        dummy_img = QImage(320, 180, QImage.Format.Format_RGB32)
        dummy_img.fill(QColor(255, 0, 0))

        # Case A: thumbnail_path exists on disk
        thumb_a = str(self.thumb_dir / "existing_a.jpg")
        dummy_img.save(thumb_a, "JPEG")
        entry_a = HistoryEntry(id=101, title="Cached A", url="https://example.com/a", thumbnail_path=thumb_a)
        pix_a = service.load_thumbnail(entry_a, QSize(128, 72), lambda *_: None, is_dark=True, db=db)
        self.assertFalse(pix_a.isNull())
        self.assertEqual(pix_a.size(), QSize(128, 72))

        # Case B: output_path exists on disk -> frame extraction
        video_b = os.path.join(self.temp_dir, "video_b.mp4")
        with open(video_b, "wb") as f:
            f.write(b"fake mp4 video bytes")

        entry_b = HistoryEntry(id=102, title="File B", url="https://example.com/b", output_path=video_b)
        with patch("clipgrab.core.thumbnail_service.extract_frame_ffmpeg") as mock_extract:
            # Simulate successful frame extraction
            def fake_extract(src, dst, *args, **kwargs):
                dummy_img.save(dst, "JPEG")
                return True
            mock_extract.side_effect = fake_extract

            # Run worker synchronously
            worker_b = service._thread_pool
            cb_mock = MagicMock()
            service.load_thumbnail(entry_b, QSize(128, 72), cb_mock, is_dark=True, db=db)
            service._thread_pool.waitForDone(2000)
            mock_extract.assert_called_once()

        # Case C: YouTube URL -> derived mqdefault.jpg
        entry_c = HistoryEntry(id=103, title="YouTube C", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        with patch("clipgrab.core.thumbnail_service.download_image_urllib") as mock_dl:
            def fake_dl(url, dst):
                self.assertIn("dQw4w9WgXcQ", url)
                self.assertIn("mqdefault.jpg", url)
                dummy_img.save(dst, "JPEG")
                return True
            mock_dl.side_effect = fake_dl

            cb_mock_c = MagicMock()
            service.load_thumbnail(entry_c, QSize(128, 72), cb_mock_c, is_dark=True, db=db)
            service._thread_pool.waitForDone(2000)
            mock_dl.assert_called_once()

        # Case D: Non-YouTube, no file, no cache -> placeholder
        entry_d = HistoryEntry(id=104, title="Unknown D", url="https://unknown-site.org/media/99")
        pix_d = service.load_thumbnail(entry_d, QSize(128, 72), lambda *_: None, is_dark=True, db=db)
        self.assertFalse(pix_d.isNull())
        self.assertEqual(pix_d.size(), QSize(128, 72))

        # Attempted tracking check: second call does not re-queue worker
        self.assertIn(104, service._attempted_entry_ids)

    def test_clear_all_removes_rows_and_thumbnails_keeps_files_by_default(self) -> None:
        """Verify 'Clear all' deletes rows and cached thumbnails, but keeps downloaded files by default."""
        db = HistoryDB(self.db_path)

        # Create dummy downloaded file
        media_file = os.path.join(self.temp_dir, "downloaded_vid.mp4")
        with open(media_file, "w") as f:
            f.write("content")

        # Create dummy thumbnail file
        thumb_file = str(self.thumb_dir / "thumb_1.jpg")
        with open(thumb_file, "w") as f:
            f.write("image")

        eid = db.add_entry(
            title="Video to Clear",
            url="https://youtube.com/watch?v=clear1",
            output_path=media_file,
            thumbnail_path=thumb_file,
        )

        self.assertEqual(db.get_count(), 1)
        self.assertTrue(os.path.isfile(media_file))
        self.assertTrue(os.path.isfile(thumb_file))

        # Clear all without deleting files
        entries_del, files_del = db.clear_all(delete_files=False)
        self.assertEqual(entries_del, 1)
        self.assertEqual(files_del, 0)

        # Database is empty
        self.assertEqual(db.get_count(), 0)
        # Thumbnail file is deleted
        self.assertFalse(os.path.isfile(thumb_file))
        # Downloaded media file is KEPT
        self.assertTrue(os.path.isfile(media_file))

    def test_clear_all_with_checkbox_deletes_existing_files(self) -> None:
        """Verify 'Clear all' with delete_files=True deletes existing downloaded files while protecting active queue paths."""
        db = HistoryDB(self.db_path)

        # Create 2 media files
        file1 = os.path.join(self.temp_dir, "file1.mp4")
        file2 = os.path.join(self.temp_dir, "file2.mp4")
        with open(file1, "w") as f:
            f.write("1")
        with open(file2, "w") as f:
            f.write("2")

        db.add_entry("File 1", "https://youtube.com/watch?v=f1", output_path=file1)
        db.add_entry("File 2", "https://youtube.com/watch?v=f2", output_path=file2)
        # Entry with already missing file
        db.add_entry("File 3 Missing", "https://youtube.com/watch?v=f3", output_path="C:/nonexistent/file3.mp4")

        # Protect file2 as actively downloading
        active_paths = {os.path.abspath(file2)}

        entries_del, files_del = db.clear_all(delete_files=True, active_paths=active_paths)
        self.assertEqual(entries_del, 3)
        self.assertEqual(files_del, 1)  # Only file1 deleted

        self.assertFalse(os.path.isfile(file1))
        self.assertTrue(os.path.isfile(file2))  # Preserved!
        self.assertEqual(db.get_count(), 0)

    def test_delete_entry_removes_thumbnail(self) -> None:
        """Verify removing a single entry also deletes its cached thumbnail file."""
        db = HistoryDB(self.db_path)
        thumb_file = str(self.thumb_dir / "single_thumb.jpg")
        with open(thumb_file, "w") as f:
            f.write("data")

        eid = db.add_entry("Single", "https://youtube.com/watch?v=s1", "C:/out.mp4", thumbnail_path=thumb_file)
        self.assertTrue(os.path.isfile(thumb_file))

        db.delete_entry(eid)
        self.assertEqual(db.get_count(), 0)
        self.assertFalse(os.path.isfile(thumb_file))

    def test_home_list_refreshes_after_clearing(self) -> None:
        """Verify HomePage Recent Downloads updates to empty state when history is cleared."""
        db = HistoryDB(self.db_path)
        db.add_entry("Recent 1", "https://youtube.com/watch?v=r1", "C:/out1.mp4")
        db.add_entry("Recent 2", "https://youtube.com/watch?v=r2", "C:/out2.mp4")

        home = HomePage(db)
        home.resize(800, 600)
        home.show()
        home.refresh_recent_downloads()

        # Should have 2 recent download cards
        self.assertEqual(home._recent_layout.count(), 2)

        # Clear history
        db.clear_all()
        home.refresh_recent_downloads()

        # Should now show empty label (1 widget)
        self.assertEqual(home._recent_layout.count(), 1)
        self.assertIn("No downloads yet", home._recent_layout.itemAt(0).widget().text())
        home.close()

    def test_missing_file_does_not_remove_thumbnail(self) -> None:
        """Verify an entry with a missing downloaded file still displays its thumbnail."""
        thumb_file = str(self.thumb_dir / "missing_file_thumb.jpg")
        dummy_img = QImage(320, 180, QImage.Format.Format_RGB32)
        dummy_img.fill(QColor(0, 255, 0))
        dummy_img.save(thumb_file, "JPEG")

        entry = HistoryEntry(
            id=201,
            title="Missing Video",
            url="https://youtube.com/watch?v=miss1",
            output_path="C:/nonexistent/deleted.mp4",
            thumbnail_path=thumb_file,
            file_exists=False,
        )

        widget = HistoryItemWidget(entry)
        widget.resize(700, 90)
        widget.show()

        # Status text says File not found
        self.assertIn("File not found", widget._file_status_label.text())
        # Open location button is disabled
        self.assertFalse(widget._open_btn.isEnabled())
        # Thumbnail label is populated
        self.assertIsNotNone(widget._thumb_label.pixmap())
        self.assertFalse(widget._thumb_label.pixmap().isNull())
        widget.close()

    def test_clear_history_dialog_checkbox_default(self) -> None:
        """Verify ClearHistoryDialog checkbox defaults to unchecked."""
        dialog = ClearHistoryDialog(count=15, is_dark=True)
        self.assertFalse(dialog.should_delete_files())
        self.assertEqual(dialog._delete_files_cb.isChecked(), False)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
