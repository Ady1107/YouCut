"""
YouCut — Download queue manager.

Manages a list of download jobs and processes them sequentially.
Coordinates with DownloadWorker instances running in background QThreads.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PySide6.QtCore import QMutexLocker, QObject, QRecursiveMutex, QThread, Signal, Qt

from clipgrab.core.downloader import DownloadRequest, DownloadWorker
from clipgrab.core.logger import get_logger

logger = get_logger("queue_manager")


class QueueItemStatus(Enum):
    """Status of a queue item."""
    PENDING = "Pending"
    DOWNLOADING = "Downloading"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    STOPPED = "Stopped"


@dataclass
class QueueItem:
    """Represents a single download job in the queue."""

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    url: str = ""
    title: str = ""
    thumbnail_url: str = ""
    thumbnail_path: Optional[str] = None  # Local cached path
    start_time: Optional[str] = None  # HH:MM:SS
    end_time: Optional[str] = None  # HH:MM:SS
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    duration: float = 0.0
    video_format_label: str = "Best available"
    audio_format_label: str = "Best available"
    output_path: str = ""
    file_size: int = 0
    status: QueueItemStatus = QueueItemStatus.PENDING
    error_msg: str = ""
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    status_text: str = ""

    # The actual download request (set when building the job)
    download_request: Optional[DownloadRequest] = None


class QueueManager(QObject):
    """
    Manages the download queue and processes items sequentially (one at a time).

    Signals:
        item_added: (index,) — emitted when a new item is added
        item_removed: (index,) — emitted when an item is removed
        item_updated: (index,) — emitted when an item's state changes
        queue_started: () — emitted when processing begins
        queue_finished: () — emitted when all items are processed
        queue_empty: () — emitted when the queue becomes empty
        download_started: () — emitted when a download begins
        download_ended: () — emitted when a download ends (success, fail, or cancel)
    """

    item_added = Signal(int)
    item_removed = Signal(int)
    item_updated = Signal(int)
    queue_started = Signal()
    queue_finished = Signal()
    queue_empty = Signal()
    download_started = Signal()
    download_ended = Signal()
    cancel_worker_requested = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._items: list[QueueItem] = []
        self._mutex = QRecursiveMutex()
        self._is_processing: bool = False
        self._active_thread: Optional[QThread] = None
        self._active_worker: Optional[DownloadWorker] = None
        self._active_item_id: Optional[str] = None

    @property
    def is_processing(self) -> bool:
        """Whether the queue is currently processing items."""
        return self._is_processing

    @property
    def has_active_download(self) -> bool:
        """Whether a download is currently in progress."""
        return self._active_thread is not None

    @property
    def has_pending_items(self) -> bool:
        """Whether there are pending items waiting to be processed."""
        with QMutexLocker(self._mutex):
            return any(item.status == QueueItemStatus.PENDING for item in self._items)

    @property
    def is_busy(self) -> bool:
        """Whether the queue has active downloads or pending items."""
        return self.has_active_download or self.has_pending_items

    def get_items(self) -> list[QueueItem]:
        """Get a copy of all queue items."""
        with QMutexLocker(self._mutex):
            return list(self._items)

    def get_item(self, index: int) -> Optional[QueueItem]:
        """Get a queue item by index."""
        with QMutexLocker(self._mutex):
            if 0 <= index < len(self._items):
                return self._items[index]
            return None

    def get_item_by_id(self, item_id: str) -> Optional[QueueItem]:
        """Get a queue item by its unique ID."""
        with QMutexLocker(self._mutex):
            for item in self._items:
                if item.id == item_id:
                    return item
            return None

    def _index_of(self, item_id: str) -> int:
        """Get the index of an item by its ID. Must be called with mutex held."""
        for i, item in enumerate(self._items):
            if item.id == item_id:
                return i
        return -1

    def item_count(self) -> int:
        """Total number of items in the queue."""
        with QMutexLocker(self._mutex):
            return len(self._items)

    def add_item(self, item: QueueItem) -> int:
        """
        Add a new item to the queue.

        Args:
            item: The QueueItem to add.

        Returns:
            The index of the newly added item.
        """
        with QMutexLocker(self._mutex):
            self._items.append(item)
            index = len(self._items) - 1

        logger.info("Queue item added [%s]: %s", item.id, item.title)
        self.item_added.emit(index)

        # If processing is active, try to start this item
        if self._is_processing and not self.has_active_download:
            self._try_start_next()

        return index

    def remove_item(self, index: int) -> bool:
        """
        Remove an item from the queue (only if not currently downloading).

        Args:
            index: The index of the item to remove.

        Returns:
            True if the item was removed, False otherwise.
        """
        with QMutexLocker(self._mutex):
            if index < 0 or index >= len(self._items):
                return False
            item = self._items[index]
            if item.status == QueueItemStatus.DOWNLOADING:
                logger.warning("Cannot remove item [%s] while downloading", item.id)
                return False
            self._items.pop(index)

        logger.info("Queue item removed at index %d", index)
        self.item_removed.emit(index)
        return True

    def reorder_item(self, from_index: int, to_index: int) -> bool:
        """
        Move an item from one position to another (only pending items).

        Args:
            from_index: Current position of the item.
            to_index: Desired new position.

        Returns:
            True if the item was moved, False otherwise.
        """
        with QMutexLocker(self._mutex):
            if (
                from_index < 0 or from_index >= len(self._items)
                or to_index < 0 or to_index >= len(self._items)
            ):
                return False
            item = self._items[from_index]
            if item.status != QueueItemStatus.PENDING:
                return False
            self._items.pop(from_index)
            self._items.insert(to_index, item)

        self.item_updated.emit(from_index)
        self.item_updated.emit(to_index)
        return True

    def start_processing(self) -> None:
        """Start processing the queue (downloads the next pending item)."""
        if self._is_processing:
            return

        self._is_processing = True
        logger.info("Queue processing started")
        self.queue_started.emit()
        self._try_start_next()

    def stop_processing(self) -> None:
        """Stop processing and cancel the currently active download."""
        self._is_processing = False
        logger.info("Queue processing stopped")
        
        if self._active_worker:
            logger.info("Cancelling active download due to queue stop")
            self.cancel_worker_requested.emit()

    def cancel_item(self, index: int) -> None:
        """Cancel a specific queue item."""
        with QMutexLocker(self._mutex):
            if index < 0 or index >= len(self._items):
                return
            item = self._items[index]
            item_id = item.id

        if item.status == QueueItemStatus.DOWNLOADING:
            if self._active_worker and self._active_item_id == item_id:
                # Tell the worker to cancel, it will stop the subprocess
                # and _on_failed will handle changing the status to STOPPED
                self.cancel_worker_requested.emit()
        elif item.status == QueueItemStatus.PENDING:
            item.status = QueueItemStatus.CANCELLED
            self.item_updated.emit(index)

    def cancel_all(self) -> None:
        """Cancel all pending and downloading items."""
        self._is_processing = False
        updated_indices = []

        with QMutexLocker(self._mutex):
            for i, item in enumerate(self._items):
                if item.status == QueueItemStatus.PENDING:
                    item.status = QueueItemStatus.CANCELLED
                    updated_indices.append(i)

        for i in updated_indices:
            self.item_updated.emit(i)

        if self._active_worker:
            self._active_worker.request_cancel()

    def shutdown(self) -> None:
        """Forcefully terminate active downloads, kill process tree, and wait for worker thread."""
        logger.info("Shutdown: Terminating queue manager and all active workers...")
        self._is_processing = False
        updated_indices = []

        # Cancel all items
        with QMutexLocker(self._mutex):
            for i, item in enumerate(self._items):
                if item.status in (QueueItemStatus.PENDING, QueueItemStatus.DOWNLOADING):
                    item.status = QueueItemStatus.CANCELLED
                    updated_indices.append(i)

        for i in updated_indices:
            self.item_updated.emit(i)

        # Forcefully terminate active worker process tree
        if self._active_worker:
            try:
                logger.info("Shutdown: Killing active download process tree...")
                self._active_worker.request_cancel()
            except Exception as e:
                logger.warning("Error cancelling active worker during shutdown: %s", e)

        # Wait for thread to quit cleanly
        if self._active_thread and self._active_thread.isRunning():
            logger.info("Shutdown: Waiting for download worker thread to exit...")
            self._active_thread.quit()
            if not self._active_thread.wait(3000):
                logger.warning("Shutdown: Download thread did not finish within 3s, terminating...")
                self._active_thread.terminate()
                self._active_thread.wait(1000)

        self._active_thread = None
        self._active_worker = None
        self._active_item_id = None
        logger.info("Shutdown: Queue manager shutdown complete.")

    def retry_item(self, index: int) -> None:
        """Reset a failed/cancelled item to pending and restart if processing."""
        with QMutexLocker(self._mutex):
            if index < 0 or index >= len(self._items):
                return
            item = self._items[index]
            if item.status not in (QueueItemStatus.FAILED, QueueItemStatus.CANCELLED):
                return
            item.status = QueueItemStatus.PENDING
            item.error_msg = ""
            item.progress = 0.0
            item.speed = ""
            item.eta = ""

        self.item_updated.emit(index)

        if self._is_processing and not self.has_active_download:
            self._try_start_next()

    def resume_item(self, index: int) -> None:
        """Resume a stopped item."""
        with QMutexLocker(self._mutex):
            if index < 0 or index >= len(self._items):
                return
            item = self._items[index]
            if item.status != QueueItemStatus.STOPPED:
                return
            item.status = QueueItemStatus.PENDING
            item.error_msg = ""
            
        self.item_updated.emit(index)
        
        # If queue is not processing, start it
        if not self._is_processing:
            self.start_processing()
        elif not self.has_active_download:
            self._try_start_next()

    def clear_completed(self) -> None:
        """Remove all completed (Done/Failed/Cancelled) items from the queue."""
        indices_to_remove = []
        with QMutexLocker(self._mutex):
            for i in range(len(self._items) - 1, -1, -1):
                if self._items[i].status in (
                    QueueItemStatus.DONE,
                    QueueItemStatus.FAILED,
                    QueueItemStatus.CANCELLED,
                ):
                    indices_to_remove.append(i)

            for i in indices_to_remove:
                self._items.pop(i)
                
        # Emit signals outside the mutex to prevent deadlocks
        for i in indices_to_remove:
            self.item_removed.emit(i)

    def _try_start_next(self) -> None:
        """Attempt to start the next pending item."""
        if not self._is_processing or self.has_active_download:
            return

        next_item = self._find_next_pending()
        if next_item is None:
            self._is_processing = False
            logger.info("Queue processing finished — no more pending items")
            self.queue_finished.emit()
            self.queue_empty.emit()
            return

        index, item = next_item
        self._start_download(index, item)

    def _find_next_pending(self) -> Optional[tuple[int, QueueItem]]:
        """Find the next pending item in the queue."""
        with QMutexLocker(self._mutex):
            for i, item in enumerate(self._items):
                if item.status == QueueItemStatus.PENDING:
                    return i, item
        return None

    def _start_download(self, index: int, item: QueueItem) -> None:
        """Start downloading a specific queue item in a new thread."""
        if item.download_request is None:
            item.status = QueueItemStatus.FAILED
            item.error_msg = "Download request not configured"
            self.item_updated.emit(index)
            self._try_start_next()
            return

        item.status = QueueItemStatus.DOWNLOADING
        item.progress = 0.0
        self.item_updated.emit(index)

        # Create worker and thread
        thread = QThread(self)
        worker = DownloadWorker()
        worker.moveToThread(thread)

        item_id = item.id

        # Connect signals without lambdas to ensure they execute on the main thread
        worker.progress_updated.connect(self._on_progress)
        worker.status_changed.connect(self._on_status)
        worker.download_finished.connect(self._on_finished)
        worker.download_failed.connect(self._on_failed)

        worker.download_request = item.download_request
        self.cancel_worker_requested.connect(worker.request_cancel, Qt.ConnectionType.QueuedConnection)
        thread.started.connect(worker.execute_download, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(self._on_thread_finished)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(worker.deleteLater)

        self._active_thread = thread
        self._active_worker = worker
        self._active_item_id = item_id

        self.download_started.emit()
        thread.start()
        logger.info("Download started for [%s]: %s", item_id, item.title)

    def _on_progress(self, percent: float, speed: str, eta: str, d: int, t: int) -> None:
        """Handle progress update from a download worker."""
        if not self._active_item_id:
            return
            
        actual_index = -1
        with QMutexLocker(self._mutex):
            actual_index = self._index_of(self._active_item_id)
            if actual_index >= 0:
                self._items[actual_index].progress = percent
                self._items[actual_index].speed = speed
                self._items[actual_index].eta = eta
                
        if actual_index >= 0:
            self.item_updated.emit(actual_index)

    def _on_status(self, status_text: str) -> None:
        """Handle status text update from a download worker."""
        if not self._active_item_id:
            return
            
        actual_index = -1
        with QMutexLocker(self._mutex):
            actual_index = self._index_of(self._active_item_id)
            if actual_index >= 0:
                self._items[actual_index].status_text = status_text
                
        if actual_index >= 0:
            self.item_updated.emit(actual_index)

    def _on_finished(self, output_path: str) -> None:
        """Handle download completion."""
        if not self._active_item_id:
            return
            
        actual_index = -1
        with QMutexLocker(self._mutex):
            actual_index = self._index_of(self._active_item_id)
            if actual_index >= 0:
                self._items[actual_index].status = QueueItemStatus.DONE
                self._items[actual_index].output_path = output_path
                self._items[actual_index].progress = 100.0
                if output_path and os.path.isfile(output_path):
                    self._items[actual_index].file_size = os.path.getsize(output_path)

        if actual_index >= 0:
            self.item_updated.emit(actual_index)
            
        self._cleanup_worker()

    def _on_failed(self, error_msg: str, is_extraction_error: bool) -> None:
        """Handle download failure."""
        if not self._active_item_id:
            return
            
        actual_index = -1
        with QMutexLocker(self._mutex):
            actual_index = self._index_of(self._active_item_id)
            if actual_index >= 0:
                if "cancelled" in error_msg.lower():
                    self._items[actual_index].status = QueueItemStatus.STOPPED
                else:
                    self._items[actual_index].status = QueueItemStatus.FAILED
                self._items[actual_index].error_msg = error_msg

        if actual_index >= 0:
            self.item_updated.emit(actual_index)
            
        self._cleanup_worker()

    def _cleanup_worker(self) -> None:
        """Tell the thread to quit. Cleanup happens in _on_thread_finished."""
        if self._active_thread:
            self._active_thread.quit()

    def _on_thread_finished(self) -> None:
        """Called when the QThread has fully exited its run loop."""
        self._active_thread = None
        self._active_worker = None
        self._active_item_id = None
        logger.debug("Worker fully cleaned up and thread finished")
        
        self.download_ended.emit()
        self._try_start_next()
        
        if not self._is_processing and not self.has_active_download:
            logger.info("Queue is idle/empty. All threads finished.")
