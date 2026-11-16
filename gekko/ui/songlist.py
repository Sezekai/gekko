"""The song list used everywhere: a table with cover, favourite, title, artist, album, genre, year, length, rating,
plays and date added. Every column sorts; stars and hearts are clicked right in the list; the playing song is
marked; songs can be dragged onto playlists and the queue."""
from __future__ import annotations

import json
import time

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTableView

from . import actions, stars, theme
from .covers import get_covers, placeholder
from .library import fmt_len, get_library
from .playback import get_playback
from .theme import C

MIME = "application/x-gekko-songs"

# key, header, width (0 = takes the spare room), alignment
COLUMNS = {
    "cover": ("", 34, Qt.AlignCenter),
    "fav": ("", 28, Qt.AlignCenter),
    "track": ("#", 40, Qt.AlignRight | Qt.AlignVCenter),
    "title": ("Title", 0, Qt.AlignLeft | Qt.AlignVCenter),
    "artist": ("Artist", 190, Qt.AlignLeft | Qt.AlignVCenter),
    "album": ("Album", 190, Qt.AlignLeft | Qt.AlignVCenter),
    "genre": ("Genre", 110, Qt.AlignLeft | Qt.AlignVCenter),
    "year": ("Year", 52, Qt.AlignRight | Qt.AlignVCenter),
    "length": ("Length", 58, Qt.AlignRight | Qt.AlignVCenter),
    "rating": ("Rating", 90, Qt.AlignLeft | Qt.AlignVCenter),
    "plays": ("Plays", 50, Qt.AlignRight | Qt.AlignVCenter),
    "last": ("Last played", 100, Qt.AlignRight | Qt.AlignVCenter),
    "added": ("Added", 92, Qt.AlignRight | Qt.AlignVCenter),
}
DEFAULT_COLS = ["cover", "fav", "title", "artist", "album", "genre", "year", "length", "rating", "plays", "last",
                "added"]
ALBUM_COLS = ["track", "fav", "title", "artist", "length", "rating", "plays"]


def _sort_key(col: str):
    return {
        "cover": lambda r: r["has_art"],
        "fav": lambda r: r["favourite"],
        "track": lambda r: ((r["disc"] or 0), (r["track"] or 0)),
        "title": lambda r: r["name"].casefold(),
        "artist": lambda r: ((r["artist"] or "~").casefold(), (r["album"] or "").casefold(), r["track"] or 0),
        "album": lambda r: ((r["album"] or "~").casefold(), r["disc"] or 0, r["track"] or 0),
        "genre": lambda r: (r["genre"] or "~").casefold(),
        "year": lambda r: r["year_n"] or 0,
        "length": lambda r: r["duration"] or 0,
        "rating": lambda r: r["rating"] or 0,
        "plays": lambda r: r["plays"] or 0,
        "last": lambda r: r["last_played"] or 0,
        "added": lambda r: r["added_at"] or 0,
    }[col]


def _ago(t: float | None) -> str:
    if not t:
        return ""
    d = time.time() - t
    if d < 86400:
        return "Today"
    if d < 2 * 86400:
        return "Yesterday"
    if d < 30 * 86400:
        return f"{int(d // 86400)} days ago"
    return time.strftime("%d %b %Y", time.localtime(t))


class SongModel(QAbstractTableModel):
    def __init__(self, cols: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.cols = cols or list(DEFAULT_COLS)
        self.rows: list[dict] = []
        self.playing: int | None = None
        self.sort_col: str | None = None
        self.sort_desc = False
        self.manual = False                 # playlist order: rows can be dragged into a new order
        self.dropped = None                 # callback(list[int] new order) for manual lists

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        if self.sort_col:
            self._sort()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal:
            return None
        c = self.cols[section]
        if role == Qt.DisplayRole:
            return COLUMNS[c][0]
        if role == Qt.TextAlignmentRole:
            return int(COLUMNS[c][2])
        if role == Qt.ToolTipRole:
            return {"fav": "Favourite", "cover": "Cover art"}.get(c)
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        r = self.rows[idx.row()]
        c = self.cols[idx.column()]
        if role == Qt.DisplayRole:
            if c == "title":
                return r["name"]
            if c == "artist":
                return r["artist"] or ""
            if c == "album":
                return r["album"] or ""
            if c == "genre":
                return r["genre"] or ""
            if c == "year":
                return str(r["year_n"]) if r["year_n"] else ""
            if c == "length":
                return fmt_len(r["duration"])
            if c == "plays":
                return str(r["plays"]) if r["plays"] else ""
            if c == "added":
                return _ago(r["added_at"])
            if c == "last":
                return _ago(r["last_played"]) if r["last_played"] else "Never"
            if c == "track":
                return f"{r['track']}" if r["track"] else ""
            return None
        if role == Qt.TextAlignmentRole:
            return int(COLUMNS[c][2])
        if role == Qt.ForegroundRole:
            if r["missing"]:
                return QColor(C["faint"])
            if c in ("artist", "album", "genre", "year", "length", "plays", "added", "track", "last"):
                return QColor(C["sec"])
            return None
        if role == Qt.ToolTipRole:
            if r["missing"]:
                return f"Can't be found at {r['path']}"
            if c == "title":
                return r["path"]
            return None
        if role == Qt.UserRole:
            return r
        return None

    def flags(self, idx):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if self.manual:
            f |= Qt.ItemIsDropEnabled
        return f

    def sort(self, column, order=Qt.AscendingOrder):
        if column < 0:
            return
        self.layoutAboutToBeChanged.emit()
        self.sort_col = self.cols[column]
        self.sort_desc = order == Qt.DescendingOrder
        self._sort()
        self.layoutChanged.emit()

    def _sort(self) -> None:
        self.rows.sort(key=_sort_key(self.sort_col), reverse=self.sort_desc)

    def clear_sort(self) -> None:
        self.sort_col = None

    # ---- drag and drop -----------------------------------------------------------------------------
    def mimeTypes(self):  # noqa: N802
        return [MIME]

    def mimeData(self, idxs):  # noqa: N802
        rows = sorted({i.row() for i in idxs})
        md = QMimeData()
        md.setData(MIME, json.dumps({"ids": [self.rows[i]["id"] for i in rows], "rows": rows}).encode())
        return md

    def supportedDropActions(self):  # noqa: N802
        return Qt.MoveAction | Qt.CopyAction

    def supportedDragActions(self):  # noqa: N802
        return Qt.MoveAction | Qt.CopyAction

    def canDropMimeData(self, data, action, row, column, parent):  # noqa: N802
        return self.manual and data.hasFormat(MIME)

    def dropMimeData(self, data, action, row, column, parent):  # noqa: N802
        if not self.manual or not data.hasFormat(MIME):
            return False
        d = json.loads(bytes(data.data(MIME)).decode())
        if row < 0:
            row = parent.row() if parent.isValid() else len(self.rows)
        src = d.get("rows") or []
        ids = d.get("ids") or []
        if not src or [self.rows[i]["id"] for i in src if i < len(self.rows)] != ids:
            # songs from elsewhere: add them at that place
            new_ids = [r["id"] for r in self.rows]
            new_ids[row:row] = [i for i in ids if i not in set(new_ids)]
        else:
            moving = [self.rows[i] for i in src]
            keep = [r for i, r in enumerate(self.rows) if i not in set(src)]
            row -= sum(1 for i in src if i < row)
            keep[row:row] = moving
            new_ids = [r["id"] for r in keep]
        if self.dropped:
            self.dropped(new_ids)
        return False          # the list is rebuilt from the new order


class SongDelegate(QStyledItemDelegate):
    def __init__(self, table: "SongTable"):
        super().__init__(table)
        self.t = table

    def paint(self, p: QPainter, opt, idx):
        self._paint(p, opt, idx)
        from .listlook import column_line
        hh = self.t.horizontalHeader()
        last = hh.visualIndex(idx.column()) == max(hh.visualIndex(i) for i in range(hh.count())
                                                    if not hh.isSectionHidden(i))
        column_line(p, opt.rect, last)

    def _paint(self, p: QPainter, opt, idx):
        m = self.t.model_
        c = m.cols[idx.column()]
        r = m.rows[idx.row()]
        playing = m.playing == r["id"]
        if c in ("cover", "fav", "rating") or (c == "title" and playing):
            # background (selection / hover) from the style, then our own drawing
            o = type(opt)(opt)
            self.initStyleOption(o, idx)
            o.text = ""
            self.t.style().drawControl(QStyle.CE_ItemViewItem, o, p, self.t)
            rect = opt.rect
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            if c == "cover":
                s = min(rect.height() - 4, 26)
                x = rect.x() + (rect.width() - s) / 2
                y = rect.y() + (rect.height() - s) / 2
                pm = get_covers().get(f"song:{r['id']}", s) if r["has_art"] or True else None
                path = QPainterPath()
                path.addRoundedRect(QRectF(x, y, s, s), 3, 3)
                p.setClipPath(path)
                if pm is not None:
                    p.drawPixmap(QRectF(x, y, s, s), pm, QRectF(0, 0, pm.width(), pm.height()))
                else:
                    ph = placeholder(s, r["album"] or r["name"])
                    p.drawPixmap(QRectF(x, y, s, s), ph, QRectF(0, 0, ph.width(), ph.height()))
                if playing:
                    p.fillRect(QRectF(x, y, s, s), QColor(0, 0, 0, 120))
                    ic = theme.pixmap("pause" if get_playback().is_playing() else "play", 12, "#ffffff")
                    p.drawPixmap(int(x + (s - 12) / 2), int(y + (s - 12) / 2), ic)
            elif c == "fav":
                on = bool(r["favourite"])
                col = C["pink"] if on else C["line2"]
                ic = theme.pixmap("heart_on" if on else "heart", 14, col)
                p.drawPixmap(rect.x() + (rect.width() - 14) // 2, rect.y() + (rect.height() - 14) // 2, ic)
            elif c == "rating":
                hov = self.t.hover_rating if self.t.hover_cell == (idx.row(), idx.column()) else None
                stars.paint(p, QRectF(rect.x() + 6, rect.y(), rect.width() - 6, rect.height()), r["rating"] or 0,
                            11, 3, hover=hov)
            elif c == "title":
                acc = QColor(C["acc"])
                p.setPen(acc)
                f = p.font()
                f.setWeight(f.Weight.DemiBold)
                p.setFont(f)
                tr = rect.adjusted(6, 0, -6, 0)
                txt = p.fontMetrics().elidedText(r["name"], Qt.ElideRight, tr.width())
                p.drawText(tr, Qt.AlignLeft | Qt.AlignVCenter, txt)
            p.restore()
            return
        super().paint(p, opt, idx)

    def sizeHint(self, opt, idx):
        return QSize(40, self.t.row_h)


class SongTable(QTableView):
    """A song list. Signals: activated(song id) on double-click / Enter."""

    played = Signal(int)

    def __init__(self, cols: list[str] | None = None, parent=None, context: str = "", state_key: str | None = None):
        super().__init__(parent)
        self.model_ = SongModel(cols)
        self.setModel(self.model_)
        self.context = context
        self.playlist_id: int | None = None
        self.row_h = max(30, theme.T.get("rh", 24) + 6)
        self.hover_cell: tuple[int, int] | None = None
        self.hover_rating: int | None = None
        self.setItemDelegate(SongDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(self.row_h)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.setSortingEnabled(True)
        self.horizontalHeader().setSortIndicatorShown(False)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.doubleClicked.connect(self._double)
        self._apply_cols()
        if state_key:
            from .listlook import remember_columns
            remember_columns(self.horizontalHeader(), state_key, all_interactive=False)
            for i, c in enumerate(self.model_.cols):
                if c in ("cover", "fav"):
                    self.setColumnWidth(i, COLUMNS[c][1])
        pb = get_playback()
        pb.currentChanged.connect(self._playing_changed)
        pb.playingChanged.connect(self._playing_toggled)
        self.model_.playing = pb.current_id()
        get_covers().ready.connect(self._cover_ready)
        get_library().songUpdated.connect(self._song_updated)

    def _apply_cols(self) -> None:
        hh = self.horizontalHeader()
        hh.setMinimumSectionSize(24)
        for i, c in enumerate(self.model_.cols):
            w = COLUMNS[c][1]
            if w:
                hh.setSectionResizeMode(i, QHeaderView.Interactive)
                self.setColumnWidth(i, w)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
        for i, c in enumerate(self.model_.cols):
            if c in ("cover", "fav"):
                hh.setSectionResizeMode(i, QHeaderView.Fixed)

    DROP_ORDER = ["added", "last", "plays", "genre", "year", "album", "cover", "track"]

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Narrow windows drop the less important columns instead of scrolling sideways."""
        cols = self.model_.cols
        avail = self.viewport().width()
        hidden: set[str] = set()
        def need():
            return sum(COLUMNS[c][1] for c in cols if c not in hidden) + 170
        for c in self.DROP_ORDER:
            if need() <= avail:
                break
            if c in cols:
                hidden.add(c)
        for i, c in enumerate(cols):
            self.setColumnHidden(i, c in hidden)

    def set_manual(self, on: bool, dropped=None) -> None:
        """Playlist order: drag rows to reorder (sorting by a column turns it off until you reset it)."""
        self.model_.manual = on
        self.model_.dropped = dropped
        self.setDragDropMode(QAbstractItemView.DragDrop if on else QAbstractItemView.DragOnly)
        self.setDropIndicatorShown(on)
        self.setDefaultDropAction(Qt.MoveAction if on else Qt.CopyAction)

    def set_rows(self, rows: list[dict], keep_sort: bool = True) -> None:
        sel = set(self.selected_ids())
        if not keep_sort:
            self.model_.clear_sort()
            self.horizontalHeader().setSortIndicatorShown(False)
        self.model_.set_rows(rows)
        if sel:
            sm = self.selectionModel()
            for i, r in enumerate(self.model_.rows):
                if r["id"] in sel:
                    sm.select(self.model_.index(i, 0), sm.Select | sm.Rows)

    def rows(self) -> list[dict]:
        return self.model_.rows

    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()}) if self.selectionModel() else []
        return [self.model_.rows[i]["id"] for i in rows if i < len(self.model_.rows)]

    def target_ids(self) -> list[int]:
        """What a key acts on: the chosen songs."""
        return self.selected_ids()

    def _header_clicked(self, i: int) -> None:
        self.horizontalHeader().setSortIndicatorShown(True)

    def _double(self, idx) -> None:
        r = self.model_.rows[idx.row()]
        self.play_from(r["id"])

    def play_from(self, sid: int) -> None:
        get_playback().play_rows(self.model_.rows, sid, "in_order", self.context)
        self.played.emit(sid)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            ids = self.selected_ids()
            if ids:
                self.play_from(ids[0])
            return
        super().keyPressEvent(e)

    def _menu(self, pos) -> None:
        ids = self.selected_ids()
        idx = self.indexAt(pos)
        if idx.isValid() and self.model_.rows[idx.row()]["id"] not in ids:
            self.selectRow(idx.row())
            ids = self.selected_ids()
        if not ids:
            return
        m = actions.song_menu(self, ids, self.model_.rows, self.playlist_id)
        m.exec(self.viewport().mapToGlobal(pos))

    def mouseMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        old = self.hover_cell
        if idx.isValid() and self.model_.cols[idx.column()] == "rating":
            rect = self.visualRect(idx)
            self.hover_cell = (idx.row(), idx.column())
            self.hover_rating = stars.hit(e.position().x(), rect.x() + 6, 11, 3)
            self.viewport().setCursor(Qt.PointingHandCursor)
        elif idx.isValid() and self.model_.cols[idx.column()] in ("fav",):
            self.hover_cell = None
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.hover_cell = None
            self.viewport().unsetCursor()
        if old != self.hover_cell or self.hover_cell:
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_cell = None
        self.viewport().update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if e.button() == Qt.LeftButton and idx.isValid() and not (e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
            c = self.model_.cols[idx.column()]
            r = self.model_.rows[idx.row()]
            if c == "fav":
                actions.toggle_favourite([r["id"]])
                return
            if c == "rating":
                rect = self.visualRect(idx)
                v = stars.hit(e.position().x(), rect.x() + 6, 11, 3)
                actions.set_rating([r["id"]], 0 if v == (r["rating"] or 0) else v)
                return
            if c == "cover" and self.model_.playing == r["id"]:
                get_playback().toggle()
                return
        super().mousePressEvent(e)

    def _playing_toggled(self, _on: bool) -> None:
        self.viewport().update()

    def _playing_changed(self, sid: int) -> None:
        self.model_.playing = sid if sid >= 0 else None
        self.viewport().update()

    def _cover_ready(self, key: str) -> None:
        if key.startswith("song:") and self.isVisible():
            self.viewport().update()

    def _song_updated(self, sid: int) -> None:
        if self.isVisible() and any(r["id"] == sid for r in self.model_.rows):
            self.viewport().update()

    def scroll_to_id(self, sid: int) -> None:
        for i, r in enumerate(self.model_.rows):
            if r["id"] == sid:
                self.selectRow(i)
                self.scrollTo(self.model_.index(i, 0), QAbstractItemView.PositionAtCenter)
                return

    def go_first(self) -> None:
        if self.model_.rows:
            self.selectRow(0)
            self.scrollToTop()

    def go_last(self) -> None:
        if self.model_.rows:
            self.selectRow(len(self.model_.rows) - 1)
            self.scrollToBottom()

    def summary(self) -> str:
        rows = self.model_.rows
        tot = sum(r["duration"] or 0 for r in rows)
        from .library import fmt_total
        return f"{len(rows):,} song{'s' if len(rows) != 1 else ''} · {fmt_total(tot)}"
