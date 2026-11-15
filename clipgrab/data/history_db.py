"""
YouCut — Download history SQLite database.

Stores all completed downloads with metadata for search, filter, and re-download.
Database file is stored at ~/.youcut/history.db.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from clipgrab.core.logger import get_logger

logger = get_logger("history_db")


def _get_db_path() -> str:
    """Get the path to the history database file."""
    env_home = os.environ.get("YOUCUT_HOME")
    if env_home:
        db_dir = Path(env_home).resolve()
    else:
        db_dir = Path.home() / ".youcut"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "history.db")


@dataclass
class HistoryEntry:
    """Represents a single download history record."""

    id: int = 0
    title: str = ""
    url: str = ""
    start_time: str = ""  # HH:MM:SS or empty
    end_time: str = ""  # HH:MM:SS or empty
    video_quality: str = ""
    audio_quality: str = ""
    output_path: str = ""
    file_size: int = 0
    downloaded_at: str = ""  # ISO timestamp
    thumbnail_url: str = ""
    thumbnail_path: str = ""
    file_exists: bool = True  # Computed, not stored


class HistoryDB:
    """
    SQLite database for download history.

    Provides CRUD operations, search, and date-based filtering.
    Thread-safe via per-call connection pattern.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Initialize the history database.

        Args:
            db_path: Path to the SQLite database file. Defaults to ~/.youcut/history.db.
        """
        self._db_path = db_path or _get_db_path()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Create a database connection context manager.

        Yields:
            A sqlite3.Connection with row_factory set.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Create the downloads table if it doesn't exist, and safely migrate new columns."""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
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
                    thumbnail_url TEXT DEFAULT '',
                    thumbnail_path TEXT DEFAULT ''
                )
            """)

            # Safe migration: inspect existing columns and add thumbnail_path if missing
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(downloads)").fetchall()]
            if "thumbnail_path" not in cols:
                logger.info("Migrating history schema: adding thumbnail_path column")
                conn.execute("ALTER TABLE downloads ADD COLUMN thumbnail_path TEXT DEFAULT ''")

            # Create indexes for search/filter performance
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_downloads_title
                ON downloads(title)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_downloads_date
                ON downloads(downloaded_at)
            """)

        logger.info("History database initialized: %s", self._db_path)

    def add_entry(
        self,
        title: str,
        url: str,
        output_path: str,
        start_time: str = "",
        end_time: str = "",
        video_quality: str = "",
        audio_quality: str = "",
        file_size: int = 0,
        thumbnail_url: str = "",
        thumbnail_path: str = "",
    ) -> int:
        """
        Add a new download to the history.

        Args:
            title: Video title.
            url: Original YouTube URL.
            output_path: Path to the downloaded file.
            start_time: Clip start time (HH:MM:SS).
            end_time: Clip end time (HH:MM:SS).
            video_quality: Selected video quality label.
            audio_quality: Selected audio quality label.
            file_size: File size in bytes.
            thumbnail_url: URL of the video thumbnail.
            thumbnail_path: Local path to cached thumbnail image.

        Returns:
            The ID of the newly inserted record.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO downloads
                    (title, url, start_time, end_time, video_quality, audio_quality,
                     output_path, file_size, thumbnail_url, thumbnail_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title,
                    url,
                    start_time,
                    end_time,
                    video_quality,
                    audio_quality,
                    output_path,
                    file_size,
                    thumbnail_url,
                    thumbnail_path,
                ),
            )
            entry_id = cursor.lastrowid or 0

        logger.info("History entry added [%d]: %s", entry_id, title)
        return entry_id

    def get_all(self, limit: int = 500, offset: int = 0) -> list[HistoryEntry]:
        """
        Get all history entries, most recent first.

        Args:
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.

        Returns:
            List of HistoryEntry objects.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM downloads
                ORDER BY downloaded_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def get_entry(self, entry_id: int) -> Optional[HistoryEntry]:
        """
        Get a single history entry by ID.

        Args:
            entry_id: The database ID of the entry.

        Returns:
            The HistoryEntry, or None if not found.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM downloads WHERE id = ?", (entry_id,)
            ).fetchone()

        if row:
            return self._row_to_entry(row)
        return None

    def search(self, query: str, limit: int = 100) -> list[HistoryEntry]:
        """
        Search history entries by title (case-insensitive partial match).

        Args:
            query: Search query string.
            limit: Maximum number of results.

        Returns:
            List of matching HistoryEntry objects.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM downloads
                WHERE title LIKE ?
                ORDER BY downloaded_at DESC
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def filter_by_date(
        self,
        start_date: str,
        end_date: str,
        limit: int = 500,
    ) -> list[HistoryEntry]:
        """
        Filter history entries by date range.

        Args:
            start_date: Start date as ISO string (YYYY-MM-DD).
            end_date: End date as ISO string (YYYY-MM-DD).
            limit: Maximum number of results.

        Returns:
            List of matching HistoryEntry objects.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM downloads
                WHERE date(downloaded_at) BETWEEN date(?) AND date(?)
                ORDER BY downloaded_at DESC
                LIMIT ?
                """,
                (start_date, end_date, limit),
            ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def update_thumbnail_path(self, entry_id: int, thumbnail_path: str) -> bool:
        """
        Update the cached thumbnail path for a specific entry.

        Args:
            entry_id: The database ID of the entry.
            thumbnail_path: Local filesystem path to the cached thumbnail JPEG.

        Returns:
            True if updated, False otherwise.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE downloads SET thumbnail_path = ? WHERE id = ?",
                (thumbnail_path, entry_id),
            )
            return cursor.rowcount > 0

    def delete_entry(self, entry_id: int) -> bool:
        """
        Remove a history entry and delete its cached thumbnail file if present.
        (Does NOT delete the downloaded video/audio file).

        Args:
            entry_id: The database ID of the entry to remove.

        Returns:
            True if the entry was deleted, False if not found.
        """
        entry = self.get_entry(entry_id)
        if entry and entry.thumbnail_path and os.path.isfile(entry.thumbnail_path):
            try:
                os.remove(entry.thumbnail_path)
                logger.info("Deleted cached thumbnail file for entry %d: %s", entry_id, entry.thumbnail_path)
            except Exception as e:
                logger.warning("Could not delete thumbnail %s: %s", entry.thumbnail_path, e)

        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM downloads WHERE id = ?", (entry_id,)
            )
            deleted = cursor.rowcount > 0

        if deleted:
            logger.info("History entry deleted: %d", entry_id)
        return deleted

    def clear_all(
        self,
        delete_files: bool = False,
        active_paths: Optional[set[str]] = None,
    ) -> tuple[int, int]:
        """
        Remove all history records, delete cached thumbnail files, and optionally
        delete downloaded media files from disk. VACUUM the database.

        Args:
            delete_files: When True, deletes existing downloaded files from disk.
            active_paths: Set of file paths actively in the download queue to preserve.

        Returns:
            Tuple of (entries_deleted_count, files_deleted_count).
        """
        active_normalized = {os.path.normpath(p).lower() for p in (active_paths or set()) if p}
        entries = self.get_all(limit=100000)
        entries_count = len(entries)
        files_deleted = 0

        # 1. Optionally delete downloaded files from disk (ignoring missing ones)
        if delete_files:
            for entry in entries:
                if entry.output_path:
                    norm = os.path.normpath(entry.output_path).lower()
                    if norm not in active_normalized and os.path.isfile(entry.output_path):
                        try:
                            os.remove(entry.output_path)
                            files_deleted += 1
                        except Exception as e:
                            logger.warning("Failed to delete downloaded file %s: %s", entry.output_path, e)

        # 2. Delete all cached thumbnails
        for entry in entries:
            if entry.thumbnail_path and os.path.isfile(entry.thumbnail_path):
                try:
                    os.remove(entry.thumbnail_path)
                except Exception as e:
                    logger.warning("Failed to delete thumbnail %s: %s", entry.thumbnail_path, e)

        # 3. Clear database rows
        with self._connect() as conn:
            conn.execute("DELETE FROM downloads")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='downloads'")

        # 4. Run VACUUM outside active transaction in autocommit mode
        try:
            conn_vac = sqlite3.connect(self._db_path, isolation_level=None)
            conn_vac.execute("VACUUM")
            conn_vac.close()
        except Exception as e:
            logger.warning("VACUUM failed: %s", e)

        logger.info(
            "History cleared: %d entries removed, %d files deleted from disk",
            entries_count,
            files_deleted,
        )
        return entries_count, files_deleted

    def get_count(self) -> int:
        """Get the total number of history entries."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM downloads").fetchone()
            return row["cnt"] if row else 0

    def _row_to_entry(self, row: sqlite3.Row) -> HistoryEntry:
        """Convert a database row to a HistoryEntry, checking if file still exists."""
        keys = row.keys()
        entry = HistoryEntry(
            id=row["id"],
            title=row["title"],
            url=row["url"],
            start_time=row["start_time"] or "",
            end_time=row["end_time"] or "",
            video_quality=row["video_quality"] or "",
            audio_quality=row["audio_quality"] or "",
            output_path=row["output_path"] or "",
            file_size=row["file_size"] or 0,
            downloaded_at=row["downloaded_at"] or "",
            thumbnail_url=row["thumbnail_url"] or "",
            thumbnail_path=row["thumbnail_path"] if "thumbnail_path" in keys and row["thumbnail_path"] else "",
        )

        # Check if the downloaded file still exists
        entry.file_exists = bool(
            entry.output_path and os.path.isfile(entry.output_path)
        )

        return entry
