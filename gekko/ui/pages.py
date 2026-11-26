"""The library screens: Songs, Albums, Artists, Genres, Folders and Playlists, with album and artist pages."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QToolButton, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QScrollArea, QSizePolicy, QSlider, QSplitter, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.db import get_db, main_artist
from ..core.settings import get_settings
from . import actions, dialogs, filters
from .browser import PlayButtons, SongBrowser
from .covers import get_covers
from .grid import CoverGrid
from .hub import hub
from .library import UNKNOWN_ARTIST, fmt_total, get_library
from .nowplaying import ArtistSnippet
from .playback import get_playback
from .playerbar import CoverLabel, LinkLabel
from .songlist import ALBUM_COLS, MIME, SongTable
from .theme import CATEGORY_COLOURS, C, icon
from .widgets import ElidedLabel, Segmented, button, clear_layout, label, open_in_explorer, page_header


class Page(QWidget):
    """A screen with the one-line header."""

    title = ""
    desc = ""

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(12, 10, 12, 12)
        self.lay.setSpacing(8)
        head, self.desc_lbl = page_header(self.title, self.desc)
        self.head = head
        self.lay.addLayout(head)

    # keys the main window passes on
    def browser(self) -> SongBrowser | None:
        return None

    def focus_search(self) -> None:
        b = self.browser()
        if b is not None:
            b.focus_search()

    def target_ids(self) -> list[int]:
        b = self.browser()
        return b.target_ids() if b is not None else []

    def key_first(self) -> None:
        b = self.browser()
        if b is not None:
            b.key_first()

    def key_last(self) -> None:
        b = self.browser()
        if b is not None:
            b.key_last()

    def key_back(self) -> bool:
        return False


# ---------------------------------------------------------------------------------------------------------
class SongsPage(Page):
    title = "Songs"
    desc = "Every song in your music folders. Double-click to play; click the stars or heart to rate"

    def __init__(self):
        super().__init__()
        lib = get_library()
        self.b = SongBrowser(lambda: lib.rows, "All songs", "songs", sort="artist")
        self.lay.addWidget(self.b, 1)
        self.b.set_empty_text("No songs yet. Add your music folders in Folders.")

    def browser(self):
        return self.b

    def show_filter(self, f: dict) -> None:
        self.b.set_filter(f)


# ---------------------------------------------------------------------------------------------------------
class GridPage(Page):
    """A grid of things (albums, artists, genres) with sort and size, and a detail view for one of them."""

    SORTS: list[tuple[str, str, tuple[str, str]]] = []   # key, name, (words for its usual way, reversed)
    key = ""

    def __init__(self):
        super().__init__()
        self.s = get_settings()
        self.stack = QStackedWidget()
        gw = QWidget()
        gl = QVBoxLayout(gw)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText(f"Find {self.title.lower()}…  ( / )")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["faint"], 14), QLineEdit.LeadingPosition)
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(lambda _t: self.fill())
        bar.addWidget(self.search, 1)
        bar.addWidget(label("Sort", "cap"))
        self.sort = QComboBox()
        for k, t, _w in self.SORTS:
            self.sort.addItem(t, k)
        i = self.sort.findData(self.s.get(f"ui.sort.{self.key}", self.SORTS[0][0]))
        self.sort.setCurrentIndex(max(0, i))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        bar.addWidget(self.sort)
        self.rev = QToolButton()
        self.rev.setObjectName("menuBtn")
        self.rev.setCheckable(True)
        self.rev.setChecked(bool(self.s.get(f"ui.sort_rev.{self.key}", False)))
        self.rev.setCursor(Qt.PointingHandCursor)
        self.rev.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.rev.clicked.connect(self._rev_changed)
        bar.addWidget(self.rev)
        self._rev_look()
        self.extra_bar(bar)
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(96, 320)
        self.size.setFixedWidth(100)
        self.size.setToolTip("Tile size (Ctrl + mouse wheel)")
        bar.addWidget(self.size)
        bar.addStretch(1)
        self.count = label("", "faint")
        bar.addWidget(self.count)
        gl.addLayout(bar)
        self.grid = CoverGrid(int(self.s.get(f"ui.tile.{self.key}", 170)))
        self.size.setValue(self.grid.tile)
        self.size.valueChanged.connect(self.grid.set_tile)
        self.grid.sizeChanged.connect(self._tile)
        self.grid.opened.connect(lambda it: self.open(it["key"]))
        self.grid.playRequested.connect(self.play_item)
        self.grid.menuRequested.connect(self.item_menu)
        gl.addWidget(self.grid, 1)
        self.empty = label("", "faint", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        gl.addWidget(self.empty)
        self.stack.addWidget(gw)
        self.detail = QWidget()
        self.detail_lay = QVBoxLayout(self.detail)
        self.detail_lay.setContentsMargins(0, 0, 0, 0)
        self.detail_lay.setSpacing(8)
        self.stack.addWidget(self.detail)
        self.lay.addWidget(self.stack, 1)
        self._dirty = True
        self.current_key = None
        get_library().changed.connect(self._lib_changed)

    def extra_bar(self, bar: QHBoxLayout) -> None:
        pass

    def _tile(self, s: int) -> None:
        if self.size.value() != s:
            self.size.blockSignals(True)
            self.size.setValue(s)
            self.size.blockSignals(False)
        self.s.set(f"ui.tile.{self.key}", s)

    def _sort_changed(self) -> None:
        self.s.set(f"ui.sort.{self.key}", self.sort.currentData())
        self._rev_look()
        self.fill()

    def _rev_changed(self) -> None:
        self.s.set(f"ui.sort_rev.{self.key}", self.rev.isChecked())
        self._rev_look()
        self.fill()

    def _rev_words(self) -> tuple[str, str]:
        return next((w for k, _t, w in self.SORTS if k == self.sort.currentData()), ("Normal order", "Reversed"))

    def _rev_look(self) -> None:
        on = self.rev.isChecked()
        w = self._rev_words()
        self.rev.setText(w[1] if on else w[0])
        self.rev.setIcon(icon("sort_desc" if on else "sort_asc", C["sec"], 16))
        self.rev.setToolTip(f"Click for {w[0] if on else w[1]}")

    def _lib_changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(0, self._refresh_visible)

    def _refresh_visible(self) -> None:
        if self.stack.currentIndex() == 0:
            self.fill()
        elif self.current_key is not None:
            self.open(self.current_key, keep=True)

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self._refresh_visible()

    def items(self) -> list[dict]:
        return []

    def fill(self) -> None:
        self._dirty = False
        items = self.items()
        if self.rev.isChecked():
            items.reverse()
        t = self.search.text().strip().casefold()
        if t:
            items = [it for it in items if all(w in (it["title"] + " " + it.get("sub", "")).casefold()
                                               for w in t.split())]
        self.grid.set_items(items)
        self.count.setText(f"{len(items):,}")
        self.empty.setVisible(not items)
        self.empty.setText("Nothing matches." if t else self.empty_text())

    def empty_text(self) -> str:
        return "Nothing here yet. Add your music folders in Folders."

    def back(self) -> None:
        self.stack.setCurrentIndex(0)
        self.current_key = None
        self.desc_lbl.setText(self.desc)
        if self._dirty:
            self.fill()

    def key_back(self) -> bool:
        if self.stack.currentIndex() == 1:
            self.back()
            return True
        return False

    def focus_search(self) -> None:
        if self.stack.currentIndex() == 0:
            self.search.setFocus()
            self.search.selectAll()
        else:
            super().focus_search()

    def back_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        b = button(f"All {self.title.lower()}", "back", "ghost")
        b.setToolTip("Back (Esc)")
        b.clicked.connect(self.back)
        row.addWidget(b)
        row.addStretch(1)
        return row

    def open(self, key, keep: bool = False) -> None:
        pass

    def play_item(self, it: dict) -> None:
        pass

    def item_menu(self, it: dict, pos) -> None:
        pass


def _ids(rows) -> list[int]:
    return [r["id"] for r in rows]


def _group_menu(parent, rows_fn, context: str, extra=None) -> QMenu:
    pb = get_playback()
    m = QMenu(parent)
    m.addAction(icon("play", C["text"], 16), "Play").triggered.connect(
        lambda: pb.play_rows(rows_fn(), None, "in_order", context))
    m.addAction(icon("shuffle", C["text"], 16), "Shuffle").triggered.connect(
        lambda: pb.play_rows(rows_fn(), None, "shuffle", context))
    m.addAction(icon("next", C["text"], 16), "Play next").triggered.connect(lambda: pb.play_next(_ids(rows_fn())))
    m.addAction(icon("queue", C["text"], 16), "Add to queue").triggered.connect(lambda: pb.add(_ids(rows_fn())))
    m.addMenu(actions.playlist_menu(m, lambda: _ids(rows_fn())))
    m.addSeparator()
    m.addAction(icon("heart", C["text"], 16), "Favourite / unfavourite all").triggered.connect(
        lambda: actions.toggle_favourite(_ids(rows_fn())))
    m.addAction(icon("edit", C["text"], 16), "Edit info of all…").triggered.connect(
        lambda: actions.edit_info(parent, _ids(rows_fn())))
    if extra:
        extra(m)
    return m


# ---------------------------------------------------------------------------------------------------------
class AlbumsPage(GridPage):
    title = "Albums"
    desc = "Albums from your songs' tags. Click one to see its songs"
    key = "albums"
    SORTS = [("artist", "Artist", ("A to Z", "Z to A")), ("album", "Album name", ("A to Z", "Z to A")),
             ("newest", "Date released", ("Newest first", "Oldest first")),
             ("added", "Date added", ("Newest first", "Oldest first")),
             ("plays", "Plays", ("Most first", "Fewest first"))]

    def items(self) -> list[dict]:
        al = list(get_library().albums())
        k = self.sort.currentData()
        if k == "album":
            al.sort(key=lambda a: a["album"].casefold())
        elif k == "newest":
            al.sort(key=lambda a: -(a["year"] or 0))
        elif k == "added":
            al.sort(key=lambda a: -a["added"])
        elif k == "plays":
            al.sort(key=lambda a: -a["plays"])
        return [{"key": a["key"], "title": a["album"],
                 "sub": a["artist"] + (f" · {a['year']}" if a["year"] else ""),
                 "covers": [f"song:{a['cover']}"], "seed": a["album"], "kind": "album"} for a in al]

    def empty_text(self) -> str:
        return "No albums yet: songs with an album name in their tags show up here."

    def _rows(self, key) -> list:
        return get_library().album_songs(tuple(key))

    def play_item(self, it: dict) -> None:
        get_playback().play_rows(self._rows(it["key"]), None, "in_order", f"Album: {it['title']}")

    def item_menu(self, it: dict, pos) -> None:
        _group_menu(self, lambda: self._rows(it["key"]), f"Album: {it['title']}").exec(pos)

    def open(self, key, keep: bool = False) -> None:
        lib = get_library()
        a = next((x for x in lib.albums() if x["key"] == tuple(key)), None)
        if a is None:
            self.back()
            return
        self.current_key = tuple(key)
        clear_layout(self.detail_lay)
        self.detail_lay.addLayout(self.back_row())
        top = QHBoxLayout()
        top.setSpacing(16)
        cov = CoverLabel(176, 10)
        cov.set_song(a["cover"], a["album"])
        cov.setCursor(Qt.ArrowCursor)
        top.addWidget(cov, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(label("Album", "faint"))
        t = QLabel(a["album"])
        t.setWordWrap(True)
        t.setStyleSheet("font-size: 22px; font-weight: 700;")
        col.addWidget(t)
        art = LinkLabel(a["artist"])
        art.setStyleSheet("font-size: 14px;")
        art.clicked.connect(lambda: hub.goto.emit("artist", main_artist(a["artist"])) if a["artist"] not in
                            ("Various artists", UNKNOWN_ARTIST) else None)
        col.addWidget(art)
        rows = lib.album_songs(self.current_key)
        g = a["genres"][0][0] if a["genres"] else ""
        bits = [str(a["year"]) if a["year"] else "", g, f"{len(rows)} song{'s' if len(rows) != 1 else ''}",
                fmt_total(sum(r["duration"] or 0 for r in rows))]
        col.addWidget(label(" · ".join(b for b in bits if b), "muted"))
        col.addStretch(1)
        ctx = f"Album: {a['album']}"
        pbtn = PlayButtons(lambda: lib.album_songs(self.current_key), lambda: ctx)
        col.addWidget(pbtn, 0, Qt.AlignLeft)
        top.addLayout(col, 1)
        self.detail_lay.addLayout(top)
        table = SongTable(ALBUM_COLS, context=ctx, state_key="songs.album")
        table.set_rows(rows, keep_sort=False)
        self.table = table
        self.detail_lay.addWidget(table, 1)
        self.desc_lbl.setText(f"{a['album']} · {a['artist']}")
        self.stack.setCurrentIndex(1)

    def target_ids(self) -> list[int]:
        if self.stack.currentIndex() == 1 and hasattr(self, "table"):
            return self.table.selected_ids()
        it = self.grid.currentIndex()
        if it.isValid():
            return _ids(self._rows(self.grid.model_.items[it.row()]["key"]))
        return []

    def key_first(self) -> None:
        if self.stack.currentIndex() == 1:
            self.table.go_first()
        else:
            self.grid.scrollToTop()

    def key_last(self) -> None:
        if self.stack.currentIndex() == 1:
            self.table.go_last()
        else:
            self.grid.scrollToBottom()


# ---------------------------------------------------------------------------------------------------------
class ArtistsPage(GridPage):
    title = "Artists"
    desc = "Everyone in your library. Click an artist for their profile and all their songs"
    key = "artists"
    SORTS = [("name", "Name", ("A to Z", "Z to A")), ("songs", "Songs", ("Most first", "Fewest first")),
             ("plays", "Plays", ("Most first", "Fewest first"))]

    def extra_bar(self, bar: QHBoxLayout) -> None:
        self.who = Segmented([("main", "Main artists"), ("all", "Everyone")], self.s.get("ui.artists_who", "main"))
        self.who.setToolTip("Everyone also lists people who only appear as a guest (feat.)")
        self.who.changed.connect(lambda v: (self.s.set("ui.artists_who", v), self.fill()))
        bar.addWidget(self.who)

    def items(self) -> list[dict]:
        ar = list(get_library().artists())
        if self.who.value == "main":
            ar = [a for a in ar if a["own"]]
        k = self.sort.currentData()
        if k == "songs":
            ar.sort(key=lambda a: -a["n"])
        elif k == "plays":
            ar.sort(key=lambda a: -a["plays"])
        return [{"key": a["name"], "title": a["name"],
                 "sub": f"{a['n']} song{'s' if a['n'] != 1 else ''}" + (f" · {a['albums']} album{'s' if a['albums'] != 1 else ''}"
                                                                        if a["albums"] else ""),
                 "covers": [f"artist:{a['name']}", f"song:{a['cover']}"], "seed": a["name"], "kind": "artist"}
                for a in ar]

    def play_item(self, it: dict) -> None:
        get_playback().play_rows(get_library().artist_songs(it["key"]), None, "shuffle", f"Artist: {it['key']}")

    def item_menu(self, it: dict, pos) -> None:
        _group_menu(self, lambda: get_library().artist_songs(it["key"]), f"Artist: {it['key']}").exec(pos)

    def open(self, key, keep: bool = False) -> None:
        name = str(key)
        lib = get_library()
        rows = lib.artist_songs(name)
        if not rows:
            self.back()
            hub.toast.emit(f"No songs by {name} in your library.", "info")
            return
        if keep and self.current_key == name and hasattr(self, "songs_b"):
            self.songs_b.mark_dirty()
            self.prof.set_artist(name)
            return
        self.current_key = name
        clear_layout(self.detail_lay)
        self.detail_lay.addLayout(self.back_row())
        top = QHBoxLayout()
        top.setSpacing(16)
        self.prof = ArtistSnippet(big=True)
        self.prof.open_btn.hide()
        self.prof.ps = 132
        self.prof.photo.setFixedSize(132, 132)
        self.prof.title.setStyleSheet("font-size: 22px; font-weight: 700;")
        self.prof.set_artist(name)
        self.prof.open_btn.hide()
        self.prof.bio.setMaximumHeight(150)
        top.addWidget(self.prof, 1)
        side = QVBoxLayout()
        side.setSpacing(6)
        ctx = f"Artist: {name}"
        side.addWidget(PlayButtons(lambda: lib.artist_songs(name), lambda: ctx))
        more = QHBoxLayout()
        a = get_db().artist(name)
        self.read = button("Read the full bio", "open", "ghost")
        self.read.clicked.connect(lambda: self._full_bio(name))
        more.addWidget(self.read)
        refresh = button("Look up again", "refresh", "ghost")
        refresh.clicked.connect(lambda: self._refetch(name))
        more.addWidget(refresh)
        more.addStretch(1)
        side.addLayout(more)
        side.addStretch(1)
        top.addLayout(side)
        self.detail_lay.addLayout(top)
        albums = lib.artist_albums(name)
        tabs = Segmented([("songs", f"Songs  {len(rows):,}"), ("albums", f"Albums  {len(albums):,}")], "songs")
        self.detail_lay.addWidget(tabs, 0, Qt.AlignLeft)
        st = QStackedWidget()
        self.songs_b = SongBrowser(lambda: lib.artist_songs(name), ctx, "artist",
                                   ["cover", "fav", "title", "artist", "album", "year", "length", "rating", "plays"])
        self.songs_b.play.hide()
        st.addWidget(self.songs_b)
        ag = CoverGrid(int(self.s.get("ui.tile.artist_albums", 170)))
        ag.sizeChanged.connect(lambda v: self.s.set("ui.tile.artist_albums", v))
        ag.set_items([{"key": x["key"], "title": x["album"], "sub": (x["artist"] if main_artist(x["artist"]).casefold()
                       != name.casefold() else "") + (f" · {x['year']}" if x["year"] else ""),
                       "covers": [f"song:{x['cover']}"], "seed": x["album"], "kind": "album"} for x in albums])
        ag.opened.connect(lambda it: hub.goto.emit("album", it["key"]))
        ag.playRequested.connect(lambda it: get_playback().play_rows(lib.album_songs(it["key"]), None, "in_order",
                                                                    f"Album: {it['title']}"))
        st.addWidget(ag)
        tabs.changed.connect(lambda v: st.setCurrentIndex(0 if v == "songs" else 1))
        self.detail_lay.addWidget(st, 1)
        self.desc_lbl.setText(name)
        self.stack.setCurrentIndex(1)
        self.songs_b.refresh()
        w = actions._window()
        if w is not None and hasattr(w, "identify") and (a is None or a["status"] == "pending"):
            w.identify.first("artist", name)

    def _full_bio(self, name: str) -> None:
        a = get_db().artist(name)
        if a is None or not a["bio"]:
            dialogs.info(self, name, "No biography was found for this artist yet.")
            return
        d = dialogs.Dialog(self, name, "", 640)
        txt = QLabel(a["bio"])
        txt.setWordWrap(True)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setWidget(txt)
        sc.setMinimumHeight(320)
        d.body.addWidget(sc)
        d.body.addWidget(label(f"From {a['bio_source']}", "faint"))
        if a["bio_url"]:
            b = d.add_button("Open the page", "default", "open")
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(a["bio_url"])))
        d.add_button("Close", "primary")
        d.exec()

    def _refetch(self, name: str) -> None:
        get_db().save_artist(name, status="pending")
        w = actions._window()
        if w is not None:
            w.identify.first("artist", name)
        self.prof.set_artist(name)

    def browser(self):
        return self.songs_b if self.stack.currentIndex() == 1 and hasattr(self, "songs_b") else None


# ---------------------------------------------------------------------------------------------------------
class GenresPage(GridPage):
    title = "Genres"
    desc = "Your songs by genre. Click one to see its songs"
    key = "genres"
    SORTS = [("songs", "Songs", ("Most first", "Fewest first")), ("name", "Name", ("A to Z", "Z to A"))]

    def items(self) -> list[dict]:
        gs = list(get_library().genres())
        if self.sort.currentData() == "name":
            gs.sort(key=lambda g: g["name"].casefold())
        out = []
        for i, g in enumerate(gs):
            out.append({"key": g["name"], "title": g["name"],
                        "sub": f"{g['n']:,} song{'s' if g['n'] != 1 else ''} · {fmt_total(g['duration'])}",
                        "covers": [], "colour": CATEGORY_COLOURS[i % len(CATEGORY_COLOURS)], "icon": "genre",
                        "kind": "genre"})
        return out

    def play_item(self, it: dict) -> None:
        get_playback().play_rows(get_library().genre_songs(it["key"]), None, "shuffle", f"Genre: {it['key']}")

    def item_menu(self, it: dict, pos) -> None:
        _group_menu(self, lambda: get_library().genre_songs(it["key"]), f"Genre: {it['key']}").exec(pos)

    def open(self, key, keep: bool = False) -> None:
        name = str(key)
        if keep and self.current_key == name and hasattr(self, "b"):
            self.b.mark_dirty()
            return
        self.current_key = name
        clear_layout(self.detail_lay)
        row = self.back_row()
        t = label(name, "h1")
        row.insertWidget(1, t)
        self.detail_lay.addLayout(row)
        self.b = SongBrowser(lambda: get_library().genre_songs(name), f"Genre: {name}", "genre", sort="artist")
        self.detail_lay.addWidget(self.b, 1)
        self.desc_lbl.setText(name)
        self.stack.setCurrentIndex(1)
        self.b.refresh()

    def browser(self):
        return self.b if self.stack.currentIndex() == 1 and hasattr(self, "b") else None


# ---------------------------------------------------------------------------------------------------------
counts_names: dict[str, str] = {}


class FoldersPage(Page):
    title = "Folders"
    desc = "Your music folders, on any drive. Gekko checks them for new songs every time it opens"

    def __init__(self, scan_fn):
        super().__init__()
        self.scan_fn = scan_fn
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        btns = QHBoxLayout()
        add = button("Add folder…", "plus", "primary")
        add.clicked.connect(self.add_folder)
        btns.addWidget(add)
        rescan = button("Check now", "refresh")
        rescan.setToolTip("Check every folder for new, changed and missing songs")
        rescan.clicked.connect(lambda: self.scan_fn(None))
        btns.addWidget(rescan)
        btns.addStretch(1)
        ll.addLayout(btns)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda *_: self._picked())
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        ll.addWidget(self.tree, 1)
        self.note = label("", "faint", wrap=True)
        ll.addWidget(self.note)
        left.setMinimumWidth(250)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.sel_path: str | None = None
        self.b = SongBrowser(self._rows, self._ctx, "folders", sort="artist")
        rl.addWidget(self.b)
        split.addWidget(right)
        split.setSizes([int(get_settings().get("ui.folder_tree_w", 260)), 900])
        split.splitterMoved.connect(lambda *_: get_settings().set("ui.folder_tree_w", split.sizes()[0]))
        self.lay.addWidget(split, 1)
        self._dirty = True
        get_library().changed.connect(self._changed)
        hub.foldersChanged.connect(self._changed)

    def _ctx(self) -> str:
        return f"Folder: {Path(self.sel_path).name}" if self.sel_path else "All folders"

    def _rows(self) -> list:
        lib = get_library()
        return lib.folder_songs(self.sel_path) if self.sel_path else lib.rows

    def _changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(0, self.fill)

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.fill()

    def fill(self) -> None:
        self._dirty = False
        lib = get_library()
        db = get_db()
        counts: dict[str, int] = {}
        global counts_names
        counts_names = {}
        for r in lib.rows:
            d = os.path.normcase(r["folder"])
            counts[d] = counts.get(d, 0) + 1
            counts_names.setdefault(d, r["folder"])
        cur = self.sel_path
        self.tree.blockSignals(True)
        self.tree.clear()
        top = QTreeWidgetItem(["All folders", f"{len(lib.rows):,}"])
        top.setData(0, Qt.UserRole, None)
        top.setIcon(0, icon("library", C["sec"], 16))
        self.tree.addTopLevelItem(top)
        from ..core import paths
        for f in db.folders():
            fk = os.path.normcase(f.rstrip("\\/"))
            total = sum(n for d, n in counts.items() if d == fk or d.startswith(fk + os.sep))
            reach = paths.drive_ok(Path(f)) and Path(f).exists()
            fp = Path(f)
            it = QTreeWidgetItem([fp.name or str(fp), f"{total:,}"])
            it.setData(0, Qt.UserRole, f)
            it.setIcon(0, icon("folder" if reach else "drive", C["sec"] if reach else C["warn"], 16))
            it.setToolTip(0, f if reach else f"{f}\nCan't be reached right now (is the drive connected?). Its songs "
                          "stay in the library.")
            self.tree.addTopLevelItem(it)
            self._children(it, f, counts)
            it.setExpanded(False)
        self.tree.blockSignals(False)
        self._select(cur)
        nx = len(db.excluded())
        nr = len(db.removed())
        bits = []
        if nx:
            bits.append(f"{nx} folder{'s' if nx != 1 else ''} not added")
        if nr:
            bits.append(f"{nr} song{'s' if nr != 1 else ''} removed")
        self.note.setText(("Skipped: " + ", ".join(bits) + ". Settings > Music folders can bring them back.")
                          if bits else "Right-click a subfolder to stop adding it.")
        if not db.folders():
            self.b.set_empty_text("Add the folders your music is in.")
        self.b.mark_dirty()

    def _children(self, parent: QTreeWidgetItem, folder: str, counts: dict[str, int]) -> None:
        """Subfolders that hold songs, with counts (built from the songs, so it never touches the disk)."""
        base = folder.rstrip("\\/")
        bk = os.path.normcase(base)
        tree: dict = {"kids": {}}
        for d, n in counts.items():
            if not d.startswith(bk + os.sep):
                continue
            node = tree
            for part in counts_names.get(d, d)[len(base) + 1:].split(os.sep):
                node = node["kids"].setdefault(part.casefold(), {"name": part, "n": 0, "kids": {}})
                node["n"] += n

        def add(item: QTreeWidgetItem, node: dict, path: str) -> None:
            for k in sorted(node["kids"]):
                ch = node["kids"][k]
                p = os.path.join(path, ch["name"])
                it = QTreeWidgetItem([ch["name"], f"{ch['n']:,}"])
                it.setData(0, Qt.UserRole, p)
                it.setIcon(0, icon("folder", C["sec"], 16))
                item.addChild(it)
                add(it, ch, p)

        add(parent, tree, base)

    def _select(self, path: str | None) -> None:
        def walk(it: QTreeWidgetItem):
            if it.data(0, Qt.UserRole) == path:
                return it
            for i in range(it.childCount()):
                f = walk(it.child(i))
                if f is not None:
                    return f
            return None
        found = None
        for i in range(self.tree.topLevelItemCount()):
            found = walk(self.tree.topLevelItem(i))
            if found is not None:
                break
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(found or self.tree.topLevelItem(0))
        self.tree.blockSignals(False)
        if found is None:
            self.sel_path = None

    def _picked(self) -> None:
        it = self.tree.currentItem()
        self.sel_path = it.data(0, Qt.UserRole) if it is not None else None
        self.b.mark_dirty()
        self.b.refresh()

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add a music folder", str(Path.home()))
        if not d:
            return
        if get_db().add_folder(d):
            hub.foldersChanged.emit()
            self.scan_fn([d])
        else:
            hub.toast.emit("That folder is already in the list (or inside one that is).", "info")

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        path = it.data(0, Qt.UserRole)
        m = QMenu(self)
        lib = get_library()
        rows = (lambda p=path: lib.folder_songs(p) if p else lib.rows)
        ctx = f"Folder: {Path(path).name}" if path else "All folders"
        pb = get_playback()
        m.addAction(icon("play", C["text"], 16), "Play").triggered.connect(lambda: pb.play_rows(rows(), None, "in_order", ctx))
        m.addAction(icon("shuffle", C["text"], 16), "Shuffle").triggered.connect(lambda: pb.play_rows(rows(), None, "shuffle", ctx))
        m.addAction(icon("queue", C["text"], 16), "Add to queue").triggered.connect(lambda: pb.add(_ids(rows())))
        if path:
            m.addSeparator()
            m.addAction(icon("folder", C["text"], 16), "Show in Explorer").triggered.connect(lambda: open_in_explorer(path))
            m.addAction(icon("refresh", C["text"], 16), "Check this folder for new songs").triggered.connect(
                lambda: self.scan_fn([path]))
            top = it.parent() is None
            if top:
                m.addAction(icon("x", C["text"], 16), "Take off the list…").triggered.connect(lambda: self._remove(path))
            else:
                m.addAction(icon("minus", C["text"], 16), "Don't add this folder…").triggered.connect(
                    lambda: self._exclude(path))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _remove(self, path: str) -> None:
        n = len(get_library().folder_songs(path))
        if dialogs.ask(self, "Take this folder off the list?", f"{path}\n\nIts {n:,} songs leave the library (their "
                       "ratings and plays are forgotten). Nothing on disk changes, and you can add it again any time.",
                       "Take it off the list", danger=True):
            get_db().remove_folder(path)
            hub.songsReset.emit()
            hub.foldersChanged.emit()

    def _exclude(self, path: str) -> None:
        n = len(get_library().folder_songs(path))
        if dialogs.ask(self, f"Don't add “{Path(path).name}”?", f"{path}\n\nIts {n:,} songs leave the library and "
                       "later scans skip this folder and everything in it. Nothing on disk changes. Settings > Music "
                       "folders can add it again.", "Don't add this folder", danger=True):
            get_db().exclude(path)
            hub.songsReset.emit()
            hub.foldersChanged.emit()

    def browser(self):
        return self.b


# ---------------------------------------------------------------------------------------------------------
class PlaylistList(QListWidget):
    """The playlists on the left; songs dropped onto one are added to it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        ok = it is not None and isinstance(it.data(Qt.UserRole), int) and not it.data(Qt.UserRole + 1)
        e.setAccepted(ok)

    def dropEvent(self, e):
        import json
        it = self.itemAt(e.position().toPoint())
        if it is None or not isinstance(it.data(Qt.UserRole), int):
            return
        ids = json.loads(bytes(e.mimeData().data(MIME)).decode()).get("ids") or []
        actions.add_to_playlist(it.data(Qt.UserRole), ids)
        e.acceptProposedAction()


class PlaylistsPage(Page):
    title = "Playlists"
    desc = "Your own playlists (drag songs onto them) and smart ones that fill themselves from filters"

    def __init__(self):
        super().__init__()
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        btns = QHBoxLayout()
        new = button("Playlist", "plus", "primary")
        new.setToolTip("Make a new playlist (or right-click songs > Add to playlist > New playlist)")
        new.clicked.connect(lambda: self._new())
        btns.addWidget(new)
        smart = button("Smart playlist", "smart")
        smart.setToolTip("A playlist that fills itself from filters: genre, rating, length, plays…")
        smart.clicked.connect(lambda: self._edit_smart(None))
        btns.addWidget(smart)
        btns.addStretch(1)
        ll.addLayout(btns)
        self.list = PlaylistList()
        self.list.currentItemChanged.connect(lambda *_: self._picked())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        ll.addWidget(self.list, 1)
        left.setMinimumWidth(250)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        head = QHBoxLayout()
        self.name = label("", "h1")
        head.addWidget(self.name)
        self.kind = label("", "faint")
        head.addWidget(self.kind, 1)
        self.rules_btn = button("Edit rules…", "smart")
        self.rules_btn.clicked.connect(lambda: self._edit_smart(self.sel))
        head.addWidget(self.rules_btn)
        self.order_btn = button("Back to my order", "undo", "ghost")
        self.order_btn.setToolTip("Sorting by a column shows a different order; this goes back to the playlist's own")
        self.order_btn.clicked.connect(self._my_order)
        head.addWidget(self.order_btn)
        self.more_btn = button("", "more", "ghost")
        self.more_btn.setToolTip("Rename, colour, delete")
        self.more_btn.clicked.connect(lambda: self._menu_for(self.list.currentItem(), self.more_btn.mapToGlobal(
            QPoint(0, self.more_btn.height()))))
        head.addWidget(self.more_btn)
        rl.addLayout(head)
        self.sel = None
        self.b = SongBrowser(self._rows, self._ctx, "playlist")
        self.b.table.horizontalHeader().sectionClicked.connect(
            lambda *_: self.order_btn.setVisible(self.b.table.model_.manual))
        rl.addWidget(self.b, 1)
        split.addWidget(right)
        split.setSizes([int(get_settings().get("ui.playlist_list_w", 260)), 900])
        split.splitterMoved.connect(lambda *_: get_settings().set("ui.playlist_list_w", split.sizes()[0]))
        self.lay.addWidget(split, 1)
        hub.playlistsChanged.connect(self.fill)
        get_library().changed.connect(lambda: self.b.mark_dirty())
        self.fill()

    def _ctx(self) -> str:
        it = self.list.currentItem()
        return f"Playlist: {it.text()}" if it is not None else ""

    def _rows(self) -> list:
        lib = get_library()
        s = self.sel
        if s is None:
            return []
        if isinstance(s, str):
            return lib.auto_list(s)
        p = get_db().playlist(s)
        if p is None:
            return []
        if p["rules"]:
            import json
            try:
                spec = json.loads(p["rules"])
            except Exception:
                spec = {}
            f = filters.clean(spec.get("filter"))
            pf = filters.prepare(f)
            rows = [r for r in lib.rows if filters.match(pf, r)]
            from .playback import order_ids
            order = spec.get("order") or "in_order"
            if order != "in_order":
                by = {r["id"]: r for r in rows}
                rows = [by[i] for i in order_ids(rows, order)]
            lim = int(spec.get("limit") or 0)
            return rows[:lim] if lim else rows
        return lib.rows_for(get_db().playlist_song_ids(s))

    def fill(self) -> None:
        cur = self.sel
        self.list.blockSignals(True)
        self.list.clear()

        def head(t):
            it = QListWidgetItem(t)
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(C["faint"]))
            f = it.font()
            f.setWeight(f.Weight.DemiBold)
            it.setFont(f)
            self.list.addItem(it)

        head("Built in")
        lib = get_library()
        for k, t in lib.AUTO:
            if k == "missing" and not any(r["missing"] for r in lib.rows):
                continue
            it = QListWidgetItem(icon({"favourites": "heart", "top": "star", "recent": "clock", "most": "refresh",
                                       "lately": "clock", "never": "note", "missing": "warn"}.get(k, "note"),
                                      C["sec"], 16), t)
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        pls = get_db().playlists()
        head("Your playlists")
        if not pls:
            it = QListWidgetItem("None yet: New playlist, or right-click songs")
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor(C["faint"]))
            self.list.addItem(it)
        for p in pls:
            it = QListWidgetItem(icon("smart" if p["rules"] else "playlist", p["color"] or C["acc"], 16),
                                 f"{p['name']}")
            it.setData(Qt.UserRole, int(p["id"]))
            it.setData(Qt.UserRole + 1, bool(p["rules"]))
            it.setToolTip("Smart playlist: fills itself from its rules" if p["rules"] else
                          f"{p['n']} song{'s' if p['n'] != 1 else ''} · drop songs here to add them")
            self.list.addItem(it)
        self.list.blockSignals(False)
        target = cur if cur is not None else "favourites"
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == target:
                self.list.setCurrentRow(i)
                break
        else:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) == "favourites":
                    self.list.setCurrentRow(i)
        self._picked()

    def _picked(self) -> None:
        it = self.list.currentItem()
        self.sel = it.data(Qt.UserRole) if it is not None else None
        user = isinstance(self.sel, int)
        smart = bool(it.data(Qt.UserRole + 1)) if it is not None and user else False
        self.name.setText(it.text() if it is not None else "")
        self.kind.setText("Smart playlist · fills itself from its rules" if smart else
                          ("Your playlist · drag rows to reorder, drop songs to add" if user else "Built in"))
        self.rules_btn.setVisible(smart)
        self.more_btn.setVisible(user)
        manual = user and not smart
        self.b.table.playlist_id = self.sel if manual else None
        self.b.table.set_manual(manual, self._reordered if manual else None)
        self.b.table.model_.clear_sort()
        self.b.table.horizontalHeader().setSortIndicatorShown(False)
        self.order_btn.hide()
        self.b.mark_dirty()
        self.b.refresh()

    def _my_order(self) -> None:
        self.b.table.model_.clear_sort()
        self.b.table.horizontalHeader().setSortIndicatorShown(False)
        self.order_btn.hide()
        self.b.refresh()

    def _reordered(self, ids: list[int]) -> None:
        if isinstance(self.sel, int):
            get_db().set_playlist_songs(self.sel, ids)
            self.b.refresh()

    def _new(self) -> None:
        pid = actions.new_playlist(self)
        if pid:
            self.sel = pid
            self.fill()

    def _menu(self, pos) -> None:
        self._menu_for(self.list.itemAt(pos), self.list.viewport().mapToGlobal(pos))

    def _menu_for(self, it, gpos) -> None:
        if it is None:
            return
        key = it.data(Qt.UserRole)
        pb = get_playback()
        m = QMenu(self)
        self.list.setCurrentItem(it)
        rows = self._rows
        m.addAction(icon("play", C["text"], 16), "Play").triggered.connect(lambda: pb.play_rows(rows(), None, "in_order", self._ctx()))
        m.addAction(icon("shuffle", C["text"], 16), "Shuffle").triggered.connect(lambda: pb.play_rows(rows(), None, "shuffle", self._ctx()))
        m.addAction(icon("queue", C["text"], 16), "Add to queue").triggered.connect(lambda: pb.add(_ids(rows())))
        if isinstance(key, int):
            m.addSeparator()
            m.addAction(icon("edit", C["text"], 16), "Rename…").triggered.connect(lambda: self._rename(key))
            cm = m.addMenu("Colour")
            for c in CATEGORY_COLOURS:
                cm.addAction(actions._dot(c), c).triggered.connect(lambda _=False, c=c: (
                    get_db().update_playlist(key, color=c), hub.playlistsChanged.emit()))
            if it.data(Qt.UserRole + 1):
                m.addAction(icon("smart", C["text"], 16), "Edit rules…").triggered.connect(lambda: self._edit_smart(key))
                m.addAction(icon("copy", C["text"], 16), "Save as a normal playlist").triggered.connect(
                    lambda: self._freeze(key))
            m.addSeparator()
            m.addAction(icon("trash", C["danger"], 16), "Delete playlist…").triggered.connect(lambda: self._delete(key))
        m.exec(gpos)

    def _rename(self, pid: int) -> None:
        p = get_db().playlist(pid)
        n = dialogs.ask_text(self, "Rename playlist", "New name", p["name"], "Rename")
        if n:
            get_db().update_playlist(pid, name=n)
            hub.playlistsChanged.emit()

    def _delete(self, pid: int) -> None:
        p = get_db().playlist(pid)
        if dialogs.ask(self, f"Delete “{p['name']}”?", "Only the playlist goes; the songs stay in your library.",
                       "Delete playlist", danger=True):
            get_db().delete_playlist(pid)
            self.sel = "favourites"
            hub.playlistsChanged.emit()

    def _freeze(self, pid: int) -> None:
        p = get_db().playlist(pid)
        ids = _ids(self._rows())
        nid = get_db().add_playlist(f"{p['name']} (saved)", p["color"])
        get_db().add_to_playlist(nid, ids)
        self.sel = nid
        hub.playlistsChanged.emit()

    def _edit_smart(self, pid) -> None:
        import json
        p = get_db().playlist(pid) if isinstance(pid, int) else None
        spec = json.loads(p["rules"]) if p is not None and p["rules"] else {}
        d = dialogs.Dialog(self, "Smart playlist" if p is None else f"Rules for “{p['name']}”",
                           "Songs that match every rule are in it, and it keeps itself up to date.", 640)
        name = QLineEdit(p["name"] if p else "")
        name.setPlaceholderText("Name, e.g. Short favourites")
        d.body.addWidget(name)
        form = filters.FilterForm([g["name"] for g in get_library().genres()], smart=True)
        form.put(spec.get("filter") or {})
        d.body.addWidget(form)
        row = QHBoxLayout()
        row.addWidget(label("Order", "cap"))
        order = QComboBox()
        from .playback import ORDERS
        for k, t in ORDERS:
            order.addItem(t, k)
        order.setCurrentIndex(max(0, order.findData(spec.get("order") or "in_order")))
        row.addWidget(order)
        row.addWidget(label("At most", "cap"))
        from PySide6.QtWidgets import QSpinBox
        lim = QSpinBox()
        lim.setRange(0, 100000)
        lim.setSpecialValueText("No limit")
        lim.setValue(int(spec.get("limit") or 0))
        lim.setFixedWidth(100)
        row.addWidget(lim)
        row.addWidget(label("songs", "faint"))
        row.addStretch(1)
        d.body.addLayout(row)
        count = label("", "muted")
        d.body.addWidget(count)

        def upd():
            f = form.get()
            pf = filters.prepare(f)
            n = sum(1 for r in get_library().rows if filters.match(pf, r))
            if lim.value():
                n = min(n, lim.value())
            count.setText(f"{n:,} songs match right now.")

        form.changed.connect(upd)
        lim.valueChanged.connect(upd)
        upd()
        d.add_button("Cancel", "cancel")
        ok = d.add_button("Save smart playlist", "primary")
        ok.setEnabled(bool(name.text().strip()))
        name.textChanged.connect(lambda t: ok.setEnabled(bool(t.strip())))
        if not d.exec():
            return
        rules = {"filter": form.get(), "order": order.currentData(), "limit": lim.value() or None}
        if p is None:
            n = len(get_db().playlists())
            self.sel = get_db().add_playlist(name.text().strip(), CATEGORY_COLOURS[n % len(CATEGORY_COLOURS)], rules)
        else:
            get_db().update_playlist(p["id"], name=name.text().strip(), rules=rules)
        hub.playlistsChanged.emit()

    def browser(self):
        return self.b

    def open_key(self, key) -> None:
        self.sel = key
        self.fill()
