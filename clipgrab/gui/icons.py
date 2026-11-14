"""
YouCut — Vector SVG Icons and Rendering Helper.

Pure SVG vector icons sourced from:
- Lucide (ISC License) for UI icons
- Simple Icons (CC0 1.0) for platform brand marks

Supports dynamic recoloring via currentColor replacement and rendering directly
to QIcon or QPixmap via QtSvg.QSvgRenderer. Zero emoji are used.
"""

from __future__ import annotations

from typing import Optional
from PySide6.QtCore import QByteArray, Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# ── Raw SVG Templates ────────────────────────────────────────────────────────
# viewBox 0 0 24 24, stroke currentColor, fill none unless specified.

_STROKE_BASE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
)

_FILL_BASE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
)

ICONS: dict[str, str] = {
    # Brand / Logo
    "scissors": (
        f'{_STROKE_BASE}'
        '<circle cx="6" cy="6" r="3"/>'
        '<circle cx="6" cy="18" r="3"/>'
        '<line x1="20" y1="4" x2="8.12" y2="15.88"/>'
        '<line x1="14.47" y1="14.48" x2="20" y2="20"/>'
        '<line x1="8.12" y1="8.12" x2="12" y2="12"/>'
        '</svg>'
    ),
    # Navigation
    "home": (
        f'{_STROKE_BASE}'
        '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
        '<polyline points="9 22 9 12 15 12 15 22"/>'
        '</svg>'
    ),
    "queue": (
        f'{_STROKE_BASE}'
        '<line x1="8" y1="6" x2="21" y2="6"/>'
        '<line x1="8" y1="12" x2="21" y2="12"/>'
        '<line x1="8" y1="18" x2="21" y2="18"/>'
        '<line x1="3" y1="6" x2="3.01" y2="6"/>'
        '<line x1="3" y1="12" x2="3.01" y2="12"/>'
        '<line x1="3" y1="18" x2="3.01" y2="18"/>'
        '</svg>'
    ),
    "history": (
        f'{_STROKE_BASE}'
        '<circle cx="12" cy="12" r="10"/>'
        '<polyline points="12 6 12 12 16 14"/>'
        '</svg>'
    ),
    "settings": (
        f'{_STROKE_BASE}'
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
        '</svg>'
    ),
    # Platform Marks (Simple Icons - CC0)
    "youtube": (
        f'{_FILL_BASE}'
        '<path fill="currentColor" d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>'
        '</svg>'
    ),
    "instagram": (
        f'{_FILL_BASE}'
        '<path fill="currentColor" d="M12 2.163c3.204 0 3.584.012 4.85.07 3.252.148 4.771 1.691 4.919 4.919.058 1.265.069 1.645.069 4.849 0 3.205-.012 3.584-.069 4.849-.149 3.225-1.664 4.771-4.919 4.919-1.266.058-1.644.07-4.85.07-3.204 0-3.584-.012-4.849-.07-3.26-.149-4.771-1.699-4.919-4.92-.058-1.265-.07-1.644-.07-4.849 0-3.204.013-3.583.07-4.849.149-3.227 1.664-4.771 4.919-4.919 1.266-.057 1.645-.069 4.849-.069zm0-2.163c-3.259 0-3.667.014-4.947.072-4.358.2-6.78 2.618-6.98 6.98-.059 1.281-.073 1.689-.073 4.948 0 3.259.014 3.668.072 4.948.2 4.358 2.618 6.78 6.98 6.98 1.281.058 1.689.072 4.948.072 3.259 0 3.668-.014 4.948-.072 4.354-.2 6.782-2.618 6.979-6.98.059-1.28.073-1.689.073-4.948 0-3.259-.014-3.667-.072-4.947-.196-4.354-2.617-6.78-6.979-6.98-1.281-.059-1.69-.073-4.949-.073zm0 5.838c-3.403 0-6.162 2.759-6.162 6.162s2.759 6.163 6.162 6.163 6.162-2.759 6.162-6.163c0-3.403-2.759-6.162-6.162-6.162zm0 10.162c-2.209 0-4-1.79-4-4 0-2.209 1.791-4 4-4s4 1.791 4 4c0 2.21-1.791 4-4 4zm6.406-11.845c-.796 0-1.441.645-1.441 1.44s.645 1.44 1.441 1.44c.795 0 1.439-.645 1.439-1.44s-.644-1.44-1.439-1.44z"/>'
        '</svg>'
    ),
    "facebook": (
        f'{_FILL_BASE}'
        '<path fill="currentColor" d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/>'
        '</svg>'
    ),
    # UI Actions & Media Controls
    "link": (
        f'{_STROKE_BASE}'
        '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
        '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>'
        '</svg>'
    ),
    "check": (
        f'{_STROKE_BASE}'
        '<polyline points="20 6 9 17 4 12"/>'
        '</svg>'
    ),
    "play": (
        f'{_FILL_BASE}'
        '<polygon points="6 3 20 12 6 21 6 3" fill="currentColor"/>'
        '</svg>'
    ),
    "pause": (
        f'{_FILL_BASE}'
        '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor"/>'
        '<rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor"/>'
        '</svg>'
    ),
    "rotate-ccw": (
        f'{_STROKE_BASE}'
        '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>'
        '<path d="M3 3v5h5"/>'
        '</svg>'
    ),
    "rotate-cw": (
        f'{_STROKE_BASE}'
        '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.85.83 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '</svg>'
    ),
    "volume": (
        f'{_STROKE_BASE}'
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>'
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>'
        '</svg>'
    ),
    "volume-x": (
        f'{_STROKE_BASE}'
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<line x1="23" y1="9" x2="17" y2="15"/>'
        '<line x1="17" y1="9" x2="23" y2="15"/>'
        '</svg>'
    ),
    "folder": (
        f'{_STROKE_BASE}'
        '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>'
        '</svg>'
    ),
    "copy": (
        f'{_STROKE_BASE}'
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
        '</svg>'
    ),
    "download": (
        f'{_STROKE_BASE}'
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/>'
        '<line x1="12" y1="15" x2="12" y2="3"/>'
        '</svg>'
    ),
    "arrow-right": (
        f'{_STROKE_BASE}'
        '<path d="M5 12h14"/>'
        '<path d="m12 5 7 7-7 7"/>'
        '</svg>'
    ),
    "maximize": (
        f'{_STROKE_BASE}'
        '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>'
        '</svg>'
    ),
    "refresh": (
        f'{_STROKE_BASE}'
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>'
        '</svg>'
    ),
    "chevron-down": (
        f'{_STROKE_BASE}'
        '<path d="m6 9 6 6 6-6"/>'
        '</svg>'
    ),
    "more-horizontal": (
        f'{_STROKE_BASE}'
        '<circle cx="12" cy="12" r="1"/>'
        '<circle cx="19" cy="12" r="1"/>'
        '<circle cx="5" cy="12" r="1"/>'
        '</svg>'
    ),
    "chevron-left": (
        f'{_STROKE_BASE}'
        '<path d="m15 18-6-6 6-6"/>'
        '</svg>'
    ),
    "chevron-right": (
        f'{_STROKE_BASE}'
        '<path d="m9 18 6-6-6-6"/>'
        '</svg>'
    ),
    "x": (
        f'{_STROKE_BASE}'
        '<path d="M18 6 6 18M6 6l12 12"/>'
        '</svg>'
    ),
    "alert-triangle": (
        f'{_STROKE_BASE}'
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>'
        '<line x1="12" y1="9" x2="12" y2="13"/>'
        '<line x1="12" y1="17" x2="12.01" y2="17"/>'
        '</svg>'
    ),
    "film": (
        f'{_STROKE_BASE}'
        '<rect width="20" height="20" x="2" y="2" rx="2.18" ry="2.18"/>'
        '<line x1="7" y1="2" x2="7" y2="22"/>'
        '<line x1="17" y1="2" x2="17" y2="22"/>'
        '<line x1="2" y1="12" x2="22" y2="12"/>'
        '<line x1="2" y1="7" x2="7" y2="7"/>'
        '<line x1="2" y1="17" x2="7" y2="17"/>'
        '<line x1="17" y1="17" x2="22" y2="17"/>'
        '<line x1="17" y1="7" x2="22" y2="7"/>'
        '</svg>'
    ),
    "search": (
        f'{_STROKE_BASE}'
        '<circle cx="11" cy="11" r="8"/>'
        '<line x1="21" y1="21" x2="16.65" y2="16.65"/>'
        '</svg>'
    ),
    "trash": (
        f'{_STROKE_BASE}'
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'
        '</svg>'
    ),
    "square": (
        f'{_FILL_BASE}'
        '<rect x="5" y="5" width="14" height="14" rx="2" fill="currentColor"/>'
        '</svg>'
    ),
    "music": (
        f'{_STROKE_BASE}'
        '<path d="M9 18V5l12-2v13"/>'
        '<circle cx="6" cy="18" r="3"/>'
        '<circle cx="18" cy="16" r="3"/>'
        '</svg>'
    ),
    "skip-back": (
        f'{_STROKE_BASE}'
        '<polygon points="19 20 9 12 19 4 19 20"/>'
        '<line x1="5" y1="19" x2="5" y2="5"/>'
        '</svg>'
    ),
    "skip-forward": (
        f'{_STROKE_BASE}'
        '<polygon points="5 4 15 12 5 20 5 4"/>'
        '<line x1="19" y1="5" x2="19" y2="19"/>'
        '</svg>'
    ),
}


_pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}


def get_svg_pixmap(name: str, color: str = "#FFFFFF", size: int = 16) -> QPixmap:
    """
    Render a named vector SVG icon recolored to `color` at `size` x `size` pixels.
    Cached for high performance.
    """
    key = (name, color, size)
    if key in _pixmap_cache:
        return _pixmap_cache[key]

    raw_svg = ICONS.get(name)
    if not raw_svg:
        # Fallback empty transparent pixmap
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        return pix

    colored_svg = raw_svg.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(colored_svg.encode("utf-8")))

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()

    _pixmap_cache[key] = pixmap
    return pixmap


def get_svg_icon(name: str, color: str = "#FFFFFF", size: int = 16) -> QIcon:
    """Return a QIcon for a named vector SVG icon."""
    return QIcon(get_svg_pixmap(name, color=color, size=size))
