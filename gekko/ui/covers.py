"""Cover art and artist photos as small pictures, loaded off the UI thread and kept in memory.

get() returns a picture straight away when it's ready (or None) and loads it in the background otherwise; the
`ready` signal says when to repaint."""
from __future__ import annotations

import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap

from ..core.db import get_db
from ..media import covers as mcovers
from . import theme


class _Sig(QObject):
    loaded = Signal(str, int, QImage)


class CoverCache(QObject):
    ready = Signal(str)                        # key ("song:12", "artist:name")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mem: OrderedDict[tuple[str, int], QPixmap | None] = OrderedDict()
        self._busy: set[tuple[str, int]] = set()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="covers")
        self._sig = _Sig()
        self._sig.loaded.connect(self._loaded)
        self._lock = threading.Lock()
        self.max_items = 1500
        mcovers.on_forget(lambda sid: self.forget(f"song:{sid}"))

    def forget(self, key: str) -> None:
        for k in [k for k in self._mem if k[0] == key]:
            self._mem.pop(k, None)
        self.ready.emit(key)

    def clear(self) -> None:
        self._mem.clear()

    def get(self, key: str, size: int, source=None) -> QPixmap | None:
        """key 'song:<id>' or 'artist:<name>' (or anything with `source`, a function giving an image path)."""
        k = (key, int(size))
        if k in self._mem:
            self._mem.move_to_end(k)
            return self._mem[k]
        if k not in self._busy:
            self._busy.add(k)
            self._pool.submit(self._load, key, int(size), source)
        return None

    def _load(self, key: str, size: int, source) -> None:
        path = None
        try:
            if source is not None:
                path = source()
            elif key.startswith("song:"):
                r = get_db().one("SELECT id, path, has_art, folder_art FROM songs WHERE id=?", (int(key[5:]),))
                path = mcovers.source(r) if r is not None else None
            elif key.startswith("artist:"):
                a = get_db().artist(key[7:])
                path = a["image"] if a is not None and a["image"] else None
        except Exception:
            path = None
        img = QImage()
        if path:
            if img.load(path) and not img.isNull():
                px = int(size * 2)
                img = img.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                if img.width() != img.height():
                    s = min(img.width(), img.height())
                    img = img.copy((img.width() - s) // 2, (img.height() - s) // 2, s, s)
        self._sig.loaded.emit(key, size, img)

    def _loaded(self, key: str, size: int, img: QImage) -> None:
        k = (key, size)
        self._busy.discard(k)
        if img.isNull():
            self._mem[k] = None
        else:
            pm = QPixmap.fromImage(img)
            pm.setDevicePixelRatio(2.0)
            self._mem[k] = pm
        while len(self._mem) > self.max_items:
            self._mem.popitem(last=False)
        self.ready.emit(key)


_cc: CoverCache | None = None


def get_covers() -> CoverCache:
    global _cc
    if _cc is None:
        _cc = CoverCache()
    return _cc


def placeholder(size: int, seed: str, kind: str = "note") -> QPixmap:
    """A colourful tile for things without a picture (a note for songs, the initial for artists)."""
    import zlib
    dpr = 2.0
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    h = zlib.crc32((seed or "?").encode()) % 360
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor.fromHsl(h, 120, 62))
    g.setColorAt(1, QColor.fromHsl((h + 50) % 360, 140, 34))
    p.fillRect(QRectF(0, 0, size, size), g)
    if kind == "initial":
        f = p.font()
        f.setPixelSize(max(8, int(size * 0.42)))
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 220))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, (seed or "?").strip()[:1].upper())
    else:
        note = theme.pixmap("note", max(6, int(size * 0.42)), "#ffffff")
        p.setOpacity(0.55)
        p.drawPixmap(int(size * 0.29), int(size * 0.29), note)
    p.end()
    return pm


def rounded(pm: QPixmap, size: int, radius: float, circle: bool = False) -> QPixmap:
    dpr = 2.0
    out = QPixmap(int(size * dpr), int(size * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    if circle:
        path.addEllipse(QRectF(0, 0, size, size))
    else:
        path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(QRectF(0, 0, size, size), pm, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    return out


def cover_or_placeholder(key: str, size: int, seed: str, radius: float = 6, circle: bool = False,
                         kind: str = "note") -> QPixmap:
    pm = get_covers().get(key, size)
    if pm is None:
        pm = placeholder(size, seed, kind)
    return rounded(pm, size, radius, circle)
