"""The queue as a list: the song playing, then what comes next. Drag to reorder, drop songs from any list onto it,
double-click to play one, Delete to take songs out."""
from __future__ import annotations

import json

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from . import actions, theme
from .covers import get_covers, placeholder
from .library import fmt_len, get_library
from .playback import get_playback
from .songlist import MIME
from .theme import C

LIMIT = 400


class _Delegate(QStyledItemDelegate):
    def __init__(self, view: "QueueList"):
        super().__init__(view)
        self.v = view

    def sizeHint(self, opt, idx):
        return QSize(100, self.v.row_h)

    def paint(self, p: QPainter, opt, idx):
        qi = idx.data(Qt.UserRole)
        if qi is None:
            return
        pb = get_playback()
        sid = pb.queue[qi] if 0 <= qi < len(pb.queue) else None
        r = get_library().get(sid)
        o = type(opt)(opt)
        self.initStyleOption(o, idx)
        o.text = ""
        self.v.style().drawControl(QStyle.CE_ItemViewItem, o, p, self.v)
        if r is None:
            return
        rect = opt.rect
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        s = self.v.row_h - 8
        cr = QRectF(rect.x() + 4, rect.y() + 4, s, s)
        clip = QPainterPath()
        clip.addRoundedRect(cr, 3, 3)
        p.save()
        p.setClipPath(clip)
        pm = get_covers().get(f"song:{sid}", s) or placeholder(s, r["album"] or r["name"])
        p.drawPixmap(cr, pm, QRectF(0, 0, pm.width(), pm.height()))
        current = qi == pb.index
        if current:
            p.fillRect(cr, QColor(0, 0, 0, 120))
            ic = theme.pixmap("pause" if pb.is_playing() else "play", 11, "#ffffff")
            p.drawPixmap(int(cr.x() + (s - 11) / 2), int(cr.y() + (s - 11) / 2), ic)
        p.restore()
        x = int(cr.right()) + 8
        tw = rect.right() - x - 46
        f = p.font()
        if current:
            f.setWeight(f.Weight.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        p.setPen(QColor(C["acc"] if current else C["text"]))
        p.drawText(QRect(x, rect.y() + 3, tw, rect.height() // 2 - 2), Qt.AlignLeft | Qt.AlignBottom,
                   fm.elidedText(r["name"], Qt.ElideRight, tw))
        f.setWeight(f.Weight.Normal)
        f.setPixelSize(max(10, theme.T["fs"] - 1))
        p.setFont(f)
        p.setPen(QColor(C["sec"]))
        p.drawText(QRect(x, rect.y() + rect.height() // 2 + 1, tw, rect.height() // 2 - 4), Qt.AlignLeft | Qt.AlignTop,
                   p.fontMetrics().elidedText(r["artist"] or "Unknown artist", Qt.ElideRight, tw))
        p.drawText(QRect(rect.right() - 44, rect.y(), 40, rect.height()), Qt.AlignRight | Qt.AlignVCenter,
                   fmt_len(r["duration"]))
        p.restore()


class QueueList(QListWidget):
    def __init__(self, parent=None, show_current: bool = True):
        super().__init__(parent)
        self.show_current = show_current
        self.row_h = 40
        self.setItemDelegate(_Delegate(self))
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(lambda it: get_playback().jump(it.data(Qt.UserRole)))
        pb = get_playback()
        pb.queueChanged.connect(self.refill)
        pb.currentChanged.connect(lambda _s: self.refill())
        pb.playingChanged.connect(lambda _on: self.viewport().update())
        get_covers().ready.connect(lambda k: self.viewport().update() if k.startswith("song:") and self.isVisible()
                                   else None)
        get_library().songUpdated.connect(lambda _s: self.viewport().update())
        self.refill()

    def refill(self) -> None:
        pb = get_playback()
        start = max(0, pb.index) if self.show_current else pb.index + 1
        idxs = list(range(start, min(len(pb.queue), start + LIMIT)))
        if self.count() == len(idxs) and all(self.item(i).data(Qt.UserRole) == q for i, q in enumerate(idxs)):
            self.viewport().update()
            return
        self.setUpdatesEnabled(False)
        self.clear()
        for q in idxs:
            it = QListWidgetItem()
            it.setData(Qt.UserRole, q)
            it.setSizeHint(QSize(100, self.row_h))
            self.addItem(it)
        self.setUpdatesEnabled(True)

    def queue_indexes(self) -> list[int]:
        return [it.data(Qt.UserRole) for it in self.selectedItems()]

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            get_playback().remove_at(self.queue_indexes())
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem() is not None:
            get_playback().jump(self.currentItem().data(Qt.UserRole))
            return
        super().keyPressEvent(e)

    def _menu(self, pos) -> None:
        it = self.itemAt(pos)
        if it is None:
            return
        if not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        pb = get_playback()
        qidx = self.queue_indexes()
        ids = [pb.queue[i] for i in qidx if 0 <= i < len(pb.queue)]
        m = actions.song_menu(self, ids, queue_idx=[i for i in qidx if i != pb.index])
        m.exec(self.viewport().mapToGlobal(pos))

    # ---- drag and drop ---------------------------------------------------------------------------------
    def mimeTypes(self):  # noqa: N802
        return [MIME, "application/x-gekko-queue"]

    def mimeData(self, items):  # noqa: N802
        from PySide6.QtCore import QMimeData
        md = QMimeData()
        pb = get_playback()
        idxs = [it.data(Qt.UserRole) for it in items]
        md.setData("application/x-gekko-queue", json.dumps(idxs).encode())
        md.setData(MIME, json.dumps({"ids": [pb.queue[i] for i in idxs if 0 <= i < len(pb.queue)]}).encode())
        return md

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        md = e.mimeData()
        pb = get_playback()
        it = self.itemAt(e.position().toPoint())
        if it is not None:
            rect = self.visualItemRect(it)
            dest = it.data(Qt.UserRole) + (1 if e.position().y() > rect.center().y() else 0)
        else:
            dest = len(pb.queue)
        if md.hasFormat("application/x-gekko-queue") and e.source() is self:
            src = json.loads(bytes(md.data("application/x-gekko-queue")).decode())
            pb.move(src, dest)
        elif md.hasFormat(MIME):
            ids = json.loads(bytes(md.data(MIME)).decode()).get("ids") or []
            if ids:
                if pb.index < 0 and not pb.queue:
                    pb.add(ids)
                else:
                    dest = max(dest, pb.index + 1)
                    pb.queue[dest:dest] = ids
                    pb._changed()
        e.setDropAction(Qt.CopyAction)
        e.accept()
        self.refill()
