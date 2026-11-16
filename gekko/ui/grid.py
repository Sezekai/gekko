"""Cover-art grid for albums, artists, genres, playlists and songs: a picture, a name and a line under it.
Tile size has a slider and Ctrl + mouse wheel; a play button shows on the tile under the mouse."""
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import theme
from .covers import get_covers, placeholder
from .theme import C


class GridModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []

    def set_items(self, items: list[dict]) -> None:
        self.beginResetModel()
        self.items = items
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        it = self.items[idx.row()]
        if role == Qt.DisplayRole:
            return it["title"]
        if role == Qt.ToolTipRole:
            return it.get("tip") or f"{it['title']}\n{it.get('sub', '')}"
        if role == Qt.UserRole:
            return it
        return None


class GridDelegate(QStyledItemDelegate):
    def __init__(self, view: "CoverGrid"):
        super().__init__(view)
        self.v = view

    def sizeHint(self, opt, idx):
        s = self.v.tile
        return QSize(s + 16, s + 56)

    def paint(self, p: QPainter, opt, idx):
        it = self.v.model_.items[idx.row()]
        r = opt.rect.adjusted(6, 6, -6, -6)
        s = self.v.tile
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        sel = bool(opt.state & QStyle.State_Selected)
        hov = bool(opt.state & QStyle.State_MouseOver)
        if sel or hov:
            bg = QColor(C["hl"] if sel else C["text"])
            bg.setAlphaF(0.12 if sel else 0.04)
            path = QPainterPath()
            path.addRoundedRect(QRectF(opt.rect.adjusted(1, 1, -1, -1)), theme.T["rc"] + 2, theme.T["rc"] + 2)
            p.fillPath(path, bg)
            if sel:
                from PySide6.QtGui import QPen
                p.setPen(QPen(QColor(C["outline"]), theme.T.get("ow", 2)))
                p.drawPath(path)
        cr = QRectF(r.x() + (r.width() - s) / 2, r.y(), s, s)
        circle = it.get("kind") == "artist"
        clip = QPainterPath()
        if circle:
            clip.addEllipse(cr)
        else:
            clip.addRoundedRect(cr, max(2, theme.T["rc"]), max(2, theme.T["rc"]))
        p.save()
        p.setClipPath(clip)
        pm = None
        for k in it.get("covers") or []:
            pm = get_covers().get(k, s)
            if pm is not None:
                break
        if pm is None and it.get("colour"):
            g = QColor(it["colour"])
            p.fillRect(cr, QColor(C["raised"]))
            dot = QColor(g)
            dot.setAlpha(60)
            p.fillRect(cr, dot)
            ic = theme.pixmap(it.get("icon") or "playlist", int(s * 0.36), it["colour"])
            p.drawPixmap(int(cr.x() + s * 0.32), int(cr.y() + s * 0.32), ic)
        else:
            if pm is None:
                pm = placeholder(s, it.get("seed") or it["title"], "initial" if circle else "note")
            p.drawPixmap(cr, pm, QRectF(0, 0, pm.width(), pm.height()))
        p.restore()
        if hov and it.get("playable", True):
            b = 34 if s >= 120 else 26
            br = QRectF(cr.right() - b - 8, cr.bottom() - b - 8, b, b) if not circle else \
                QRectF(cr.center().x() - b / 2, cr.center().y() - b / 2, b, b)
            g = QPainterPath()
            g.addEllipse(br)
            from PySide6.QtGui import QLinearGradient
            lg = QLinearGradient(br.topLeft(), br.bottomRight())
            st = theme.T["stops"]
            for i, c in enumerate(st):
                lg.setColorAt(i / max(1, len(st) - 1), QColor(c))
            p.fillPath(g, lg)
            ic = theme.pixmap("play", int(b * 0.42), C["on_acc"])
            p.drawPixmap(int(br.x() + b * 0.31), int(br.y() + b * 0.29), ic)
            self.v.play_rect = (idx.row(), br.toRect())
        tr = QRect(r.x(), int(cr.bottom()) + 6, r.width(), 18)
        f = p.font()
        f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        al = Qt.AlignHCenter if circle else Qt.AlignLeft
        x0 = tr if circle else QRect(int(cr.x()), tr.y(), int(cr.width()), 18)
        p.drawText(x0, al | Qt.AlignVCenter, p.fontMetrics().elidedText(it["title"], Qt.ElideRight, x0.width()))
        f.setWeight(f.Weight.Normal)
        f.setPixelSize(max(10, theme.T["fs"] - 1))
        p.setFont(f)
        p.setPen(QColor(C["sec"]))
        x1 = QRect(x0.x(), x0.y() + 18, x0.width(), 16)
        p.drawText(x1, al | Qt.AlignVCenter, p.fontMetrics().elidedText(it.get("sub", ""), Qt.ElideRight, x1.width()))
        p.restore()


class CoverGrid(QListView):
    opened = Signal(dict)             # a tile was clicked
    playRequested = Signal(dict)      # its play button was clicked
    menuRequested = Signal(dict, QPoint)
    sizeChanged = Signal(int)

    def __init__(self, tile: int = 170, parent=None):
        super().__init__(parent)
        self.model_ = GridModel(self)
        self.setModel(self.model_)
        self.tile = tile
        self.play_rect: tuple[int, QRect] | None = None
        self.setItemDelegate(GridDelegate(self))
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(2)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.setObjectName("grid")
        # a method, not a lambda: Qt drops the connection when the grid is deleted (album and artist pages
        # build new grids each time)
        get_covers().ready.connect(self._cover_ready)

    def _cover_ready(self, _key: str) -> None:
        if self.isVisible():
            self.viewport().update()

    def set_items(self, items: list[dict]) -> None:
        self.model_.set_items(items)

    def set_tile(self, s: int) -> None:
        s = max(96, min(320, int(s)))
        if s != self.tile:
            self.tile = s
            self.model_.layoutChanged.emit()
            self.sizeChanged.emit(s)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.set_tile(self.tile + (16 if e.angleDelta().y() > 0 else -16))
            return
        super().wheelEvent(e)

    def mouseReleaseEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if e.button() == Qt.LeftButton and idx.isValid():
            it = self.model_.items[idx.row()]
            pr = self.play_rect
            if pr and pr[0] == idx.row() and pr[1].contains(e.position().toPoint()) and it.get("playable", True):
                self.playRequested.emit(it)
            else:
                self.opened.emit(it)
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            idx = self.currentIndex()
            if idx.isValid():
                self.opened.emit(self.model_.items[idx.row()])
            return
        super().keyPressEvent(e)

    def mouseMoveEvent(self, e):
        self.play_rect = None
        super().mouseMoveEvent(e)
        self.viewport().update()

    def _menu(self, pos) -> None:
        idx = self.indexAt(pos)
        if idx.isValid():
            self.setCurrentIndex(idx)
            self.menuRequested.emit(self.model_.items[idx.row()], self.viewport().mapToGlobal(pos))
