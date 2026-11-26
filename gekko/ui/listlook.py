"""How every list looks and remembers itself: alternating row shades, a faint line between columns, and column
widths (and order, and sort) kept between runs, per list."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate

from ..core.settings import get_settings
from .theme import C


def column_line(p: QPainter, rect, last: bool) -> None:
    """The faint line on the right of a cell, so columns can be told apart."""
    if last:
        return
    c = QColor(C["line"])
    c.setAlphaF(0.55)
    p.save()
    p.setPen(c)
    p.drawLine(rect.right(), rect.top(), rect.right(), rect.bottom())   # full height, so the lines join up
    p.restore()


class LinedDelegate(QStyledItemDelegate):
    """The normal cell, plus the column line."""

    def paint(self, p, opt, idx):
        super().paint(p, opt, idx)
        model = idx.model()
        if model.index(0, 0, idx).isValid():  # a group heading across the whole row
            return
        view = self.parent()
        hh = getattr(view, "header", None) or getattr(view, "horizontalHeader", None)
        last = idx.column() >= model.columnCount(idx.parent()) - 1
        if hh is not None:
            h = hh()
            vis = [h.visualIndex(i) for i in range(h.count()) if not h.isSectionHidden(i)]
            last = h.visualIndex(idx.column()) == max(vis) if vis else True
        column_line(p, opt.rect, last)


def style_list(view: QAbstractItemView, lined: bool = True) -> None:
    view.setAlternatingRowColors(True)
    if lined and not isinstance(view.itemDelegate(), LinedDelegate):
        view.setItemDelegate(LinedDelegate(view))


def remember_columns(header: QHeaderView, key: str, all_interactive: bool = True) -> None:
    """Columns can be resized (and dragged into another order); the layout is saved as `ui.cols.<key>`.
    all_interactive: every column takes a width you drag (the last one fills the rest); otherwise a column set to
    stretch keeps filling the spare room and the others are dragged."""
    s = get_settings()
    key = _flat(key)
    header.setSectionsMovable(True)
    if all_interactive:
        header.setStretchLastSection(True)
        for i in range(header.count()):
            if header.sectionResizeMode(i) == QHeaderView.Stretch:
                header.setSectionResizeMode(i, QHeaderView.Interactive)
    saved = s.get(f"ui.cols.{key}")
    if saved:
        try:
            header.restoreState(QByteArray.fromBase64(saved.encode()))
        except Exception:
            pass
    timer = QTimer(header, singleShot=True, interval=400)

    def save():
        s.set(f"ui.cols.{key}", bytes(header.saveState().toBase64()).decode())

    timer.timeout.connect(save)
    header.sectionResized.connect(lambda *_: timer.start())
    header.sectionMoved.connect(lambda *_: timer.start())
    header.sortIndicatorChanged.connect(lambda *_: timer.start())


def _flat(key: str) -> str:
    """'tidy' and 'tidy.unknown' must not nest inside each other in the settings file."""
    return key.replace(".", "_")


def has_saved_columns(key: str) -> bool:
    return bool(get_settings().get(f"ui.cols.{_flat(key)}"))
