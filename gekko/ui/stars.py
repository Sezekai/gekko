"""Five stars with halves: drawing them and turning a click into a rating (0-10 half stars)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from .theme import C

STAR_GOLD = "#ffbf5a"        # the category amber; ratings read as stars at a glance


def _star_path(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    path.closeSubpath()
    return path


def paint(p: QPainter, rect: QRectF, rating: int, size: float = 12.0, gap: float = 3.0,
          dim: str | None = None, hover: int | None = None) -> None:
    """Draw five stars left-aligned in rect, centred vertically."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    show = hover if hover is not None else rating
    on = QColor(STAR_GOLD)
    if hover is not None:
        on.setAlpha(170)
    off = QColor(dim or C["line2"])
    cy = rect.center().y()
    for i in range(5):
        cx = rect.left() + size / 2 + i * (size + gap)
        path = _star_path(cx, cy, size / 2)
        p.fillPath(path, off)
        v = show - i * 2
        if v >= 2:
            p.fillPath(path, on)
        elif v == 1:
            p.save()
            p.setClipRect(QRectF(cx - size / 2, cy - size / 2, size / 2, size))
            p.fillPath(path, on)
            p.restore()
    p.restore()


def width(size: float = 12.0, gap: float = 3.0) -> float:
    return 5 * size + 4 * gap


def hit(x: float, left: float, size: float = 12.0, gap: float = 3.0) -> int:
    """The rating (1-10) for a click at x; past the stars gives 10, before them 0."""
    rel = x - left
    if rel < 0:
        return 0
    step = size + gap
    i = int(rel // step)
    if i >= 5:
        return 10
    within = rel - i * step
    return i * 2 + (1 if within < size / 2 else 2)


class StarRating(QWidget):
    """Clickable stars (the side panel, the full-screen player). Clicking the current rating clears it."""

    rated = Signal(int)

    def __init__(self, size: float = 14.0, parent=None):
        super().__init__(parent)
        self.size_ = size
        self.gap = max(2.0, size / 4)
        self.rating = 0
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(QSize(int(width(size, self.gap)) + 2, int(size) + 6))
        self.setToolTip("Rate: click a star (the left half gives a half star); [ and ] change it by half a star")

    def set_rating(self, r: int) -> None:
        self.rating = int(r or 0)
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = hit(e.position().x(), 1, self.size_, self.gap)
        self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            v = hit(e.position().x(), 1, self.size_, self.gap)
            if v == self.rating:
                v = 0
            self.rating = v
            self.rated.emit(v)
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        paint(p, QRectF(1, 0, self.width() - 2, self.height()), self.rating, self.size_, self.gap, hover=self._hover)
        p.end()
