"""
YouCut — Toast notification widget.

Non-blocking slide-in notification that auto-dismisses after a configurable
duration. Used for update notifications, download completion, and errors.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    Qt,
    QTimer,
    Property,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget


class ToastNotification(QWidget):
    """
    A non-blocking toast notification that slides in from the top-right
    corner and auto-dismisses after a set duration.
    """

    # Class-level tracking for stacking multiple toasts
    _active_toasts: list[ToastNotification] = []

    def __init__(
        self,
        parent: QWidget,
        message: str,
        duration_ms: int = 5000,
        toast_type: str = "info",
    ) -> None:
        """
        Create a toast notification.

        Args:
            parent: The parent widget (usually MainWindow).
            message: The notification message text.
            duration_ms: Auto-dismiss duration in milliseconds (0 = no auto-dismiss).
            toast_type: One of "info", "success", "warning", "error".
        """
        super().__init__(parent)
        self._message = message
        self._duration_ms = duration_ms
        self._toast_type = toast_type

        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(360)

        self._setup_ui()
        self._setup_animations()

    def _setup_ui(self) -> None:
        """Set up the toast UI elements."""
        # Background colors by type — matching YouCut brand palette
        colors = {
            "info": "#E63946",     # Brand red
            "success": "#27AE60",  # Rich green
            "warning": "#F77F00",  # Brand orange
            "error": "#C0392B",    # Deep red
        }
        bg_color = colors.get(self._toast_type, colors["info"])

        # Icons by type
        icons = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }
        icon = icons.get(self._toast_type, "ℹ️")

        self._label = QLabel(f"  {icon}  {self._message}", self)
        self._label.setWordWrap(True)
        self._label.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self._label.setStyleSheet(f"""
            QLabel {{
                background-color: {bg_color};
                color: white;
                padding: 14px 22px;
                border-radius: 12px;
            }}
        """)
        self._label.adjustSize()
        self.setFixedHeight(self._label.height() + 8)
        self._label.setFixedWidth(self.width())
        self._label.setFixedHeight(self.height())

        # Close on click
        self._label.mousePressEvent = lambda _: self.dismiss()

        # Opacity effect for fade out
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

    def _setup_animations(self) -> None:
        """Set up slide-in and fade-out animations."""
        # Slide-in animation
        self._slide_anim = QPropertyAnimation(self, b"pos")
        self._slide_anim.setDuration(300)
        self._slide_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Fade-out animation
        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(500)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_anim.finished.connect(self._on_fade_finished)

        # Auto-dismiss timer
        if self._duration_ms > 0:
            self._dismiss_timer = QTimer(self)
            self._dismiss_timer.setSingleShot(True)
            self._dismiss_timer.setInterval(self._duration_ms)
            self._dismiss_timer.timeout.connect(self.dismiss)

    def show_toast(self) -> None:
        """Show the toast with a slide-in animation."""
        parent = self.parentWidget()
        if not parent:
            return

        # Calculate position (top-right, below any existing toasts)
        margin = 16
        stack_offset = sum(t.height() + 8 for t in ToastNotification._active_toasts)

        target_x = parent.width() - self.width() - margin
        target_y = margin + stack_offset

        # Start off-screen to the right
        start_pos = QPoint(parent.width() + 10, target_y)
        end_pos = QPoint(target_x, target_y)

        self.move(start_pos)
        self.show()
        self.raise_()

        self._slide_anim.setStartValue(start_pos)
        self._slide_anim.setEndValue(end_pos)
        self._slide_anim.start()

        # Track active toasts
        ToastNotification._active_toasts.append(self)

        # Start auto-dismiss timer
        if self._duration_ms > 0:
            self._dismiss_timer.start()

    def dismiss(self) -> None:
        """Dismiss the toast with a fade-out animation."""
        if hasattr(self, '_dismiss_timer'):
            self._dismiss_timer.stop()
        self._fade_anim.start()

    def _on_fade_finished(self) -> None:
        """Clean up after fade-out animation completes."""
        if self in ToastNotification._active_toasts:
            ToastNotification._active_toasts.remove(self)
        self.hide()
        self.deleteLater()

    @classmethod
    def dismiss_all(cls) -> None:
        """Immediately stop and dismiss all active toast notifications."""
        for toast in list(cls._active_toasts):
            try:
                if hasattr(toast, '_dismiss_timer'):
                    toast._dismiss_timer.stop()
                toast.hide()
                toast.deleteLater()
            except Exception:
                pass
        cls._active_toasts.clear()


def show_toast(
    parent: QWidget,
    message: str,
    duration_ms: int = 5000,
    toast_type: str = "info",
) -> ToastNotification:
    """
    Convenience function to create and show a toast notification.

    Args:
        parent: The parent widget.
        message: The notification message.
        duration_ms: Auto-dismiss duration in milliseconds.
        toast_type: One of "info", "success", "warning", "error".

    Returns:
        The created ToastNotification instance.
    """
    toast = ToastNotification(parent, message, duration_ms, toast_type)
    toast.show_toast()
    return toast
