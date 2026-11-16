"""SongBrowser: search, filters, play buttons and a list (or cover grid) of songs. Every screen that shows songs
uses it: Songs, a genre, a folder, an artist, a playlist."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLineEdit, QMenu, QSlider, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.settings import get_settings
from . import actions, filters
from .grid import CoverGrid
from .library import fmt_len, get_library
from .playback import ORDERS, get_playback
from .songlist import DEFAULT_COLS, SongTable
from .theme import C, icon
from .widgets import ElidedLabel, Segmented, button, label


class PlayButtons(QWidget):
    """Play (in order) plus a menu of every play order, Play next and Add to queue."""

    def __init__(self, rows_fn: Callable[[], list], context_fn: Callable[[], str], parent=None):
        super().__init__(parent)
        self.rows_fn = rows_fn
        self.context_fn = context_fn
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.play = button("Play", "play", "primary")
        self.play.setToolTip("Play these in order")
        self.play.clicked.connect(lambda: self.go("in_order"))
        lay.addWidget(self.play)
        self.shuffle = button("Shuffle", "shuffle")
        self.shuffle.clicked.connect(lambda: self.go("shuffle"))
        lay.addWidget(self.shuffle)
        self.more = QToolButton()
        self.more.setObjectName("menuBtn")
        self.more.setText("Play order")
        self.more.setIcon(icon("chev", C["sec"], 14))
        self.more.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.more.setLayoutDirection(Qt.RightToLeft)
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setToolTip("Play these reversed, by length, by release date, by rating… or queue them")
        self.more.clicked.connect(self._menu)
        lay.addWidget(self.more)

    def go(self, order: str) -> None:
        get_playback().play_rows(self.rows_fn(), None, order, self.context_fn())

    def _menu(self) -> None:
        m = QMenu(self)
        for k, t in ORDERS:
            if k in ("in_order", "shuffle"):
                continue
            m.addAction(f"Play {t.lower()}").triggered.connect(lambda _=False, k=k: self.go(k))
        m.addSeparator()
        m.addAction(icon("next", C["text"], 16), "Play next").triggered.connect(
            lambda: get_playback().play_next([r["id"] for r in self.rows_fn() if not r["missing"]]))
        m.addAction(icon("queue", C["text"], 16), "Add to queue").triggered.connect(
            lambda: get_playback().add([r["id"] for r in self.rows_fn() if not r["missing"]]))
        m.addMenu(actions.playlist_menu(m, lambda: [r["id"] for r in self.rows_fn()]))
        m.exec(self.more.mapToGlobal(QPoint(0, self.more.height() + 2)))


class SongBrowser(QWidget):
    """rows_fn gives the songs to show; filters and search narrow them down."""

    countChanged = Signal(str)

    def __init__(self, rows_fn: Callable[[], list], context: str | Callable[[], str] = "", key: str = "songs",
                 cols: list[str] | None = None, grid: bool = True, search: bool = True, sort: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.rows_fn = rows_fn
        self._context = context
        self.key = key
        self.s = get_settings()
        self.f = filters.new()
        self.shown: list[dict] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search titles, artists, albums, genres…  ( / )")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["faint"], 14), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(380)
        self._search_timer = QTimer(self, singleShot=True, interval=180, timeout=self._search_now)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        bar.addWidget(self.search, 1)
        self.search.setVisible(search)
        self.filter_btn = button("Filters", "filter")
        self.filter_btn.setToolTip("Genre, length, release date, rating, favourites, played… each with its opposite")
        self.filter_btn.clicked.connect(self._filters)
        bar.addWidget(self.filter_btn)
        self.view = Segmented([("list", "List"), ("grid", "Grid")], self.s.get(f"ui.view.{key}", "list"))
        self.view.changed.connect(self._view)
        self.view.setVisible(grid)
        bar.addWidget(self.view)
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(96, 320)
        self.size.setFixedWidth(90)
        self.size.setToolTip("Tile size (Ctrl + mouse wheel)")
        bar.addWidget(self.size)
        bar.addStretch(1)
        self.play = PlayButtons(lambda: self.shown, self.context)
        bar.addWidget(self.play)
        lay.addLayout(bar)
        self.pills = filters.Pills()
        self.pills.cleared.connect(self._clear_keys)
        self.pills.hide()
        lay.addWidget(self.pills)
        self.summary = ElidedLabel("")
        self.summary.setObjectName("faint")
        lay.addWidget(self.summary)
        self.stack = QStackedWidget()
        self.table = SongTable(cols or list(DEFAULT_COLS), context=self.context())
        if sort and sort in self.table.model_.cols:
            self.table.model_.sort_col = sort
            self.table.horizontalHeader().setSortIndicator(self.table.model_.cols.index(sort), Qt.AscendingOrder)
            self.table.horizontalHeader().setSortIndicatorShown(True)
        self.stack.addWidget(self.table)
        self.grid = CoverGrid(int(self.s.get(f"ui.tile.{key}", 150)))
        self.grid.opened.connect(lambda it: self.table.play_from(it["id"]))
        self.grid.playRequested.connect(lambda it: self.table.play_from(it["id"]))
        self.grid.menuRequested.connect(self._grid_menu)
        self.grid.sizeChanged.connect(self._tile_changed)
        self.size.setValue(self.grid.tile)
        self.size.valueChanged.connect(self.grid.set_tile)
        self.stack.addWidget(self.grid)
        lay.addWidget(self.stack, 1)
        self.empty = label("", "faint", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.hide()
        lay.addWidget(self.empty)
        self._view(self.view.value, save=False)
        self._dirty = True
        get_library().changed.connect(self.mark_dirty)

    def context(self) -> str:
        return self._context() if callable(self._context) else self._context

    def mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(0, self.refresh)

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        rows = self.rows_fn()
        f = self.f
        if filters.is_active(f):
            pf = filters.prepare(f)
            rows = [r for r in rows if filters.match(pf, r)]
        self.shown = rows
        self.table.context = self.context()
        self.table.set_rows(rows)
        if self.stack.currentIndex() == 1:
            self._fill_grid()
        n = len(filters.describe(f))
        self.filter_btn.setText(f"Filters · {n}" if n else "Filters")
        self.pills.set_filter(f)
        tot = sum(r["duration"] or 0 for r in rows)
        from .library import fmt_total
        txt = f"{len(rows):,} song{'s' if len(rows) != 1 else ''} · {fmt_total(tot)}"
        if filters.is_active(f):
            txt += " (filtered)"
        self.summary.setText(txt)
        self.countChanged.emit(txt)
        self.play.setEnabled(bool(rows))

    def set_empty_text(self, t: str) -> None:
        self.empty.setText(t)

    def _fill_grid(self) -> None:
        items = [{"id": r["id"], "title": r["name"], "sub": r["artist"] or "Unknown artist",
                  "covers": [f"song:{r['id']}"], "seed": r["album"] or r["name"], "kind": "song"}
                 for r in self.table.rows()]
        self.grid.set_items(items)

    def _view(self, v: str, save: bool = True) -> None:
        self.stack.setCurrentIndex(1 if v == "grid" else 0)
        self.size.setVisible(v == "grid")
        if v == "grid":
            self._fill_grid()
        if save:
            self.s.set(f"ui.view.{self.key}", v)

    def _tile_changed(self, s: int) -> None:
        if self.size.value() != s:
            self.size.blockSignals(True)
            self.size.setValue(s)
            self.size.blockSignals(False)
        self.s.set(f"ui.tile.{self.key}", s)

    def _grid_menu(self, it: dict, pos) -> None:
        actions.song_menu(self, [it["id"]], self.table.rows(), self.table.playlist_id).exec(pos)

    def _search_now(self) -> None:
        self.f["text"] = self.search.text()
        self.refresh()

    def _filters(self) -> None:
        lib = get_library()
        genres = [g["name"] for g in lib.genres()]
        pop = filters.FilterPopup(genres, self.f, self)
        pop.changed.connect(self._set_filter)
        pop.adjustSize()
        p = self.filter_btn.mapToGlobal(QPoint(0, self.filter_btn.height() + 4))
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr is not None and p.y() + pop.height() > scr.bottom():
            p.setY(max(scr.top(), scr.bottom() - pop.height()))
        pop.move(p)
        pop.show()

    def _set_filter(self, f: dict) -> None:
        f["text"] = self.search.text()
        self.f = f
        self.refresh()

    def _clear_keys(self, keys: list) -> None:
        if not keys:
            self.f = filters.new(text=self.search.text())
        else:
            base = filters.new()
            for k in keys:
                self.f[k] = base[k]
        self.refresh()

    def set_filter(self, f: dict) -> None:
        self.f = filters.clean(f)
        self.search.setText(self.f.get("text") or "")
        self.refresh()

    # ---- keys ----------------------------------------------------------------------------------------
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def target_ids(self) -> list[int]:
        if self.stack.currentIndex() == 1:
            idx = self.grid.currentIndex()
            return [self.grid.model_.items[idx.row()]["id"]] if idx.isValid() else []
        return self.table.selected_ids()

    def key_first(self) -> None:
        if self.stack.currentIndex() == 0:
            self.table.go_first()
        else:
            self.grid.scrollToTop()

    def key_last(self) -> None:
        if self.stack.currentIndex() == 0:
            self.table.go_last()
        else:
            self.grid.scrollToBottom()

    def show_song(self, sid: int) -> None:
        if self.stack.currentIndex() == 0:
            self.table.scroll_to_id(sid)
