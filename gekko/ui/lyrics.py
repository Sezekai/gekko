"""Lyrics that follow the song: with timed (synced) lyrics the current line lights up and stays in the middle;
click a line to jump there. Plain lyrics scroll on their own. Scrolling by hand pauses the following for a moment."""
from __future__ import annotations

import re
import time

from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, Signal, Property
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from . import theme
from .theme import C

_TS = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")


def parse_lrc(text: str | None) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for line in (text or "").splitlines():
        stamps = list(_TS.finditer(line))
        if not stamps:
            continue
        words = _TS.sub("", line).strip()
        for m in stamps:
            t = int(m.group(1)) * 60 + float(m.group(2).replace(":", "."))
            out.append((t, words))
    out.sort(key=lambda x: x[0])
    return out


class LyricsView(QWidget):
    seekRequested = Signal(float)

    def __init__(self, big: bool = False, parent=None):
        super().__init__(parent)
        self.big = big
        self.lines: list[tuple[float | None, str]] = []
        self.synced = False
        self.cur = -1
        self._offset = 0.0
        self._manual_until = 0.0
        self._layout: list[tuple[float, float, list[str]]] = []   # y, height, wrapped lines
        self._w = 0
        self.message = ""
        self.anim = QPropertyAnimation(self, b"offset", self)
        self.anim.setDuration(350)
        self.setMouseTracking(True)
        self.setMinimumHeight(80)

    # animated scroll position
    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, v: float) -> None:
        self._offset = v
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def set_lyrics(self, synced: str | None, plain: str | None, message: str = "") -> None:
        lrc = parse_lrc(synced)
        if lrc:
            self.lines = [(t, w) for t, w in lrc]
            self.synced = True
        elif plain and plain.strip():
            self.lines = [(None, l.strip()) for l in plain.strip().splitlines()]
            self.synced = False
        else:
            self.lines = []
            self.synced = False
        self.message = message
        self.cur = -1
        self._offset = 0.0
        self._w = 0
        self.update()

    def has_lyrics(self) -> bool:
        return bool(self.lines)

    def _font(self, current: bool = False) -> QFont:
        f = QFont(self.font())
        base = theme.T.get("fs", 13)
        f.setPixelSize(int(base * (1.55 if self.big else 1.08)))
        f.setWeight(QFont.Weight.Bold if current and self.big else (QFont.Weight.DemiBold if current else
                                                                    QFont.Weight.Normal))
        return f

    def _relayout(self) -> None:
        w = max(40, self.width() - 16)
        if w == self._w and self._layout and len(self._layout) == len(self.lines):
            return
        self._w = w
        fm = QFontMetrics(self._font(True))
        lh = fm.height() * 1.18
        gap = lh * (0.55 if self.big else 0.35)
        y = 0.0
        out = []
        for _t, words in self.lines:
            wrapped = self._wrap(words or "♪", fm, w) if words else [""]
            h = lh * len(wrapped)
            out.append((y, h, wrapped))
            y += h + (gap if words else gap * 0.6)
        self._layout = out
        self._total = y

    @staticmethod
    def _wrap(text: str, fm: QFontMetrics, w: int) -> list[str]:
        words = text.split(" ")
        lines, cur = [], ""
        for wd in words:
            t = (cur + " " + wd).strip()
            if fm.horizontalAdvance(t) <= w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = wd
        lines.append(cur)
        return lines

    def set_position(self, t: float) -> None:
        if not self.synced or not self.lines:
            return
        i = -1
        for k, (lt, _w) in enumerate(self.lines):
            if lt is not None and lt <= t + 0.15:
                i = k
            else:
                break
        if i != self.cur:
            self.cur = i
            self._follow()
            self.update()

    def _follow(self) -> None:
        if time.monotonic() < self._manual_until or not self._layout or self.cur < 0:
            return
        y, h, _ = self._layout[self.cur]
        target = y + h / 2 - self.height() * (0.42 if self.big else 0.38)
        target = max(0.0, min(target, max(0.0, self._total - self.height() * 0.6)))
        self.anim.stop()
        self.anim.setStartValue(self._offset)
        self.anim.setEndValue(target)
        self.anim.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._w = 0
        self._relayout()
        self._follow()

    def wheelEvent(self, e):
        if not self.lines:
            return
        self._relayout()
        self.anim.stop()
        self._manual_until = time.monotonic() + 4.0
        self._offset = max(0.0, min(self._offset - e.angleDelta().y() / 2.0, max(0.0, self._total - 40)))
        self.update()

    def mousePressEvent(self, e):
        if not self.synced or not self._layout:
            return
        y = e.position().y() + self._offset - 8
        for k, (ly, h, _w) in enumerate(self._layout):
            if ly <= y <= ly + h and self.lines[k][0] is not None:
                self._manual_until = 0
                self.seekRequested.emit(float(self.lines[k][0]))
                return

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.PointingHandCursor if self.synced and self.lines else Qt.ArrowCursor)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        if not self.lines:
            p.setPen(QColor(C["faint"]))
            p.setFont(self._font())
            p.drawText(self.rect().adjusted(8, 8, -8, -8), Qt.AlignCenter | Qt.TextWordWrap,
                       self.message or "No lyrics for this song yet.")
            p.end()
            return
        self._relayout()
        fm_cur = QFontMetrics(self._font(True))
        lh = fm_cur.height() * 1.18
        for k, (y, h, wrapped) in enumerate(self._layout):
            top = y - self._offset + 8
            if top + h < 0 or top > self.height():
                continue
            current = self.synced and k == self.cur
            if not self.synced:
                col = QColor(C["text"])
            elif current:
                col = QColor(C["text"])
            else:
                col = QColor(C["sec"] if k > self.cur else C["faint"])
            p.setPen(col)
            p.setFont(self._font(current))
            for j, line in enumerate(wrapped):
                r = QRectF(8, top + j * lh, self.width() - 16, lh)
                p.drawText(r, (Qt.AlignHCenter if self.big else Qt.AlignLeft) | Qt.AlignVCenter, line)
        p.end()
