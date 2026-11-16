"""Listening history: every time a song played, in order and when.

The History screen lists them newest first, grouped by day, week, month or year, as a list (each play its own row,
repeats in a row marked) or a cover grid (each song once per group, with how often). A span of dates can be picked
with a calendar. Each song also has its own history (right-click > Listening history, and the full-screen player)."""
from __future__ import annotations

import datetime as dt
import math
import time
from collections import Counter, OrderedDict

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QScrollArea, QSlider, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.db import get_db
from ..core.settings import get_settings
from . import actions, dialogs, filters, theme
from .daterange import DateRange, times
from .grid import CoverGrid
from .hub import hub
from .library import fmt_len, fmt_total, get_library
from .playback import get_playback
from .theme import C, icon
from .widgets import ElidedLabel, Segmented, button, clear_layout, label, page_header

LIST_LIMIT = 5000
GROUPS = [("day", "Day"), ("week", "Week"), ("month", "Month"), ("year", "Year")]


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _day(d: dt.date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B %Y')}"


def group_of(ts: float, mode: str, today: dt.date | None = None) -> tuple[str, str]:
    """(key, heading) of the day, week, month or year a time falls in."""
    d = dt.date.fromtimestamp(ts)
    t = today or dt.date.today()
    if mode == "week":
        start = d - dt.timedelta(days=d.weekday())
        this = t - dt.timedelta(days=t.weekday())
        iso = d.isocalendar()
        name = ("This week" if start == this else "Last week" if start == this - dt.timedelta(days=7) else
                f"Week of {start.day} {start.strftime('%B %Y')}")
        return f"{iso[0]}-W{iso[1]:02d}", f"{name} · week {iso[1]}"
    if mode == "month":
        return d.strftime("%Y-%m"), d.strftime("%B %Y") + (" (this month)" if (d.year, d.month) == (t.year, t.month)
                                                           else "")
    if mode == "year":
        return str(d.year), str(d.year) + (" (this year)" if d.year == t.year else "")
    name = "Today" if d == t else "Yesterday" if d == t - dt.timedelta(days=1) else ""
    return d.isoformat(), (f"{name} · " if name else "") + _day(d)


def heard_text(r) -> str:
    played = r["played"] or 0
    if r["counted"]:
        if r["length"] and played >= (r["length"] or 0) - 2:
            return "Whole song"
        return f"{fmt_len(played)} of {fmt_len(r['length'])}" if r["length"] else fmt_len(played)
    return f"Skipped at {fmt_len(played)}"


def when_text(ts: float, with_date: bool = True) -> str:
    d = dt.datetime.fromtimestamp(ts)
    return (f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}, " if with_date else "") + d.strftime("%H:%M")


def mark_repeats(entries: list) -> dict[int, int]:
    """For each listen id: how many times in a row its song had played by then (1 = not a repeat).
    entries are newest first."""
    out: dict[int, int] = {}
    prev, n = None, 0
    for r in reversed(entries):
        n = n + 1 if r["song_id"] == prev else 1
        prev = r["song_id"]
        out[r["id"]] = n
    return out


def summary(entries: list) -> str:
    counted = [r for r in entries if r["counted"]]
    skips = len(entries) - len(counted)
    songs = len({r["song_id"] for r in counted})
    heard = sum(r["played"] or 0 for r in entries)
    parts = [f"{len(counted):,} listen{'s' if len(counted) != 1 else ''}", f"{songs:,} different song{'s' if songs != 1 else ''}",
             f"{fmt_total(heard)} listened"]
    if skips:
        parts.append(f"{skips:,} skipped")
    return " · ".join(parts)


class HistoryTree(QTreeWidget):
    """Plays as rows (newest first) under group headings."""

    COLS = ["When", "Song", "Artist", "Album", "Heard", "Played from"]

    def __init__(self, show_song: bool = True, parent=None):
        super().__init__(parent)
        self.show_song = show_song
        cols = self.COLS if show_song else ["When", "Heard", "Played from"]
        self.setColumnCount(len(cols))
        self.setHeaderLabels(cols)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._double)
        hh = self.header()
        hh.setStretchLastSection(False)
        widths = {"When": 150, "Heard": 120, "Played from": 190, "Artist": 180, "Album": 180}
        for i, c in enumerate(cols):
            if c == "Song" or (not show_song and c == "Played from"):
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Interactive)
                self.setColumnWidth(i, widths.get(c, 120))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.show_song:          # narrow windows drop Album, then Played from
            w = self.viewport().width()
            self.setColumnHidden(3, w < 900)
            self.setColumnHidden(5, w < 760)

    def fill(self, entries: list, mode: str | None) -> None:
        """entries newest first; mode None = no group headings."""
        lib = get_library()
        reps = mark_repeats(entries)
        self.setUpdatesEnabled(False)
        self.clear()
        groups: OrderedDict[str, tuple[str, list]] = OrderedDict()
        for r in entries[:LIST_LIMIT]:
            k, head = group_of(r["at"], mode) if mode else ("", "")
            groups.setdefault(k, (head, []))[1].append(r)
        bold = QFont(self.font())
        bold.setWeight(QFont.Weight.DemiBold)
        for k, (head, rs) in groups.items():
            parent = None
            if mode:
                parent = QTreeWidgetItem([f"{head}   ·   {summary(rs)}"])
                parent.setFont(0, bold)
                parent.setForeground(0, QColor(C["acc"]))
                parent.setFlags(Qt.ItemIsEnabled)
                self.addTopLevelItem(parent)
                parent.setFirstColumnSpanned(True)      # only works once the row is in the list
            for r in rs:
                song = lib.get(r["song_id"])
                title = song["name"] if song else (r["title"] or "A song no longer in the library")
                n = reps.get(r["id"], 1)
                if n > 1:
                    title += f"   ↻ {_ord(n)} in a row"
                when = when_text(r["at"], with_date=mode not in ("day",))
                if self.show_song:
                    vals = [when, title, (song["artist"] if song else r["artist"]) or "",
                            (song["album"] if song else "") or "", heard_text(r), r["context"] or ""]
                else:
                    vals = [when_text(r["at"]), heard_text(r) + (f"   ↻ {_ord(n)} in a row" if n > 1 else ""),
                            r["context"] or ""]
                it = QTreeWidgetItem(vals)
                it.setData(0, Qt.UserRole, (r["id"], r["song_id"]))
                if not r["counted"] or song is None:
                    for c in range(self.columnCount()):
                        it.setForeground(c, QColor(C["faint"]))
                elif self.show_song:
                    for c in (0, 2, 3, 4, 5):
                        it.setForeground(c, QColor(C["sec"]))
                if parent is not None:
                    parent.addChild(it)
                else:
                    self.addTopLevelItem(it)
            if parent is not None:
                parent.setExpanded(True)
        self.setUpdatesEnabled(True)

    def chosen(self) -> list[tuple[int, int]]:
        return [it.data(0, Qt.UserRole) for it in self.selectedItems() if it.data(0, Qt.UserRole)]

    def _double(self, it) -> None:
        d = it.data(0, Qt.UserRole)
        if d and get_library().get(d[1]) is not None:
            get_playback().play_ids([d[1]], context="History")

    def _menu(self, pos) -> None:
        it = self.itemAt(pos)
        if it is None or not it.data(0, Qt.UserRole):
            return
        if not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        ch = self.chosen()
        sids = list(dict.fromkeys(s for _l, s in ch if get_library().get(s) is not None))
        m = actions.song_menu(self, sids) if sids else QMenu(self)
        rm = m.addAction(icon("trash", C["text"], 16), f"Remove {'this play' if len(ch) == 1 else f'{len(ch)} plays'} "
                                                       "from the history")
        rm.triggered.connect(lambda: remove_listens(self, [l for l, _s in ch]))
        first = m.actions()[0] if m.actions() else None
        if first is not None and first is not rm:
            m.removeAction(rm)
            m.insertAction(first, rm)
            m.insertSeparator(first)
        m.exec(self.viewport().mapToGlobal(pos))


def remove_listens(parent, ids: list[int]) -> None:
    if not ids:
        return
    if dialogs.ask(parent, f"Remove {len(ids)} play{'s' if len(ids) != 1 else ''} from the history?",
                   "Only the history entries go. Play counts stay as they are.", "Remove from history", danger=True):
        get_db().delete_listens(ids)
        hub.historyChanged.emit()


# ---------------------------------------------------------------------------------------------------------
class SongHistory(QWidget):
    """One song's listening history: totals, then every time it played."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sid: int | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(4)
        lay.addLayout(self.grid)
        self.tree = HistoryTree(show_song=False)
        lay.addWidget(self.tree, 1)
        self.empty = label("", "faint", wrap=True)
        lay.addWidget(self.empty)
        self._timer = QTimer(self, singleShot=True, interval=600, timeout=self.refresh)
        hub.historyChanged.connect(self._changed)

    def _changed(self) -> None:
        if self.isVisible() and self.sid is not None:
            self._timer.start()

    def set_song(self, sid: int | None) -> None:
        self.sid = sid
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.grid)
        if self.sid is None:
            self.tree.clear()
            return
        entries = get_db().listens(song_id=self.sid)
        counted = [r for r in entries if r["counted"]]
        row = get_db().song(self.sid)
        now = time.time()
        items = [
            ("Listens", f"{len(counted):,}" + (f" (the play count says {row['plays']})" if row and row["plays"] !=
                                                len(counted) else "")),
            ("Skipped", f"{len(entries) - len(counted):,}"),
            ("First listened", when_text(entries[-1]["at"]) if entries else "Never"),
            ("Last listened", when_text(entries[0]["at"]) if entries else "Never"),
            ("Time listened", fmt_total(sum(r["played"] or 0 for r in entries))),
            ("Last 30 days", f"{sum(1 for r in counted if r['at'] >= now - 30 * 86400):,} listens"),
        ]
        days = Counter(dt.date.fromtimestamp(r["at"]) for r in counted)
        if days:
            d, n = days.most_common(1)[0]
            if n > 1:
                items.append(("Most in a day", f"{n} on {_day(d)}"))
        for i, (k, v) in enumerate(items):
            a = label(k, "cap")
            self.grid.addWidget(a, i // 2, (i % 2) * 2)
            self.grid.addWidget(QLabel(v), i // 2, (i % 2) * 2 + 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnStretch(3, 1)
        self.tree.fill(entries, None)
        self.empty.setVisible(not entries)
        self.empty.setText("No plays recorded yet. Each time this song plays it's listed here (history started "
                           "with Gekko 1.1).")


def song_history_dialog(parent, sid: int) -> None:
    r = get_library().get(sid)
    if r is None:
        return
    d = dialogs.Dialog(parent, f"Listening history: {r['name']}", r["artist"] or "", 720)
    w = SongHistory()
    w.setMinimumHeight(380)
    w.set_song(sid)
    d.body.addWidget(w)
    d.add_button("Close", "primary")
    d.exec()


# ---------------------------------------------------------------------------------------------------------
class HistoryGrid(QScrollArea):
    """The grid view: a heading per day/week/month/year and each song once under it, with how often. Groups are
    added as you scroll down."""

    STEP = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transparent")
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(0, 0, 4, 0)
        self.lay.setSpacing(6)
        self.setWidget(inner)
        self.tile = int(get_settings().get("ui.tile.history", 140))
        self.groups: list[tuple[str, list]] = []
        self.shown = 0
        self.grids: list[CoverGrid] = []
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    def fill(self, entries: list, mode: str) -> None:
        clear_layout(self.lay)
        self.grids = []
        groups: OrderedDict[str, tuple[str, list]] = OrderedDict()
        for r in entries:
            k, head = group_of(r["at"], mode)
            groups.setdefault(k, (head, []))[1].append(r)
        self.groups = list(groups.values())
        self.shown = 0
        self.lay.addStretch(1)
        self._more()

    def _more(self) -> None:
        lib = get_library()
        end = min(len(self.groups), self.shown + self.STEP)
        for head, rs in self.groups[self.shown:end]:
            hl = QHBoxLayout()
            hl.addWidget(label(head, "h2"))
            hl.addWidget(label(summary(rs), "faint"), 1)
            self.lay.insertLayout(self.lay.count() - 1, hl)
            counts = Counter(r["song_id"] for r in rs if r["counted"])
            last: dict[int, float] = {}
            for r in rs:
                last.setdefault(r["song_id"], r["at"])
            items = []
            for sid in sorted(counts, key=lambda s: (-counts[s], -last[s])):
                song = lib.get(sid)
                if song is None:
                    continue
                n = counts[sid]
                items.append({"id": sid, "key": sid, "title": song["name"],
                              "sub": f"{n}× · {song['artist'] or 'Unknown artist'}",
                              "tip": f"{song['name']}\n{song['artist'] or ''}\nPlayed {n} time{'s' if n != 1 else ''}, "
                                     f"last at {when_text(last[sid])}",
                              "covers": [f"song:{sid}"], "seed": song["album"] or song["name"], "kind": "song"})
            if not items:
                self.lay.insertWidget(self.lay.count() - 1, label("Only skips here.", "faint"))
                continue
            g = CoverGrid(self.tile)
            g.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            g.set_items(items)
            g.opened.connect(lambda it: get_playback().play_ids([it["id"]], context="History"))
            g.playRequested.connect(lambda it: get_playback().play_ids([it["id"]], context="History"))
            g.menuRequested.connect(lambda it, pos, g=g: actions.song_menu(g, [it["id"]]).exec(pos))
            g.sizeChanged.connect(self._tile_changed)
            self.lay.insertWidget(self.lay.count() - 1, g)
            self.grids.append(g)
        self.shown = end
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        w = max(100, self.viewport().width() - 8)
        for g in self.grids:
            cell_w = g.tile + 16 + 2 * g.spacing()
            cell_h = g.tile + 56 + 2 * g.spacing()
            per = max(1, w // cell_w)
            rows = math.ceil(g.model_.rowCount() / per)
            g.setFixedHeight(rows * cell_h + 8)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit()

    def _scrolled(self, v: int) -> None:
        sb = self.verticalScrollBar()
        if self.shown < len(self.groups) and v >= sb.maximum() - 300:
            self._more()

    def _tile_changed(self, s: int) -> None:
        self.set_tile(s)

    def set_tile(self, s: int) -> None:
        self.tile = s
        get_settings().set("ui.tile.history", s)
        for g in self.grids:
            if g.tile != s:
                g.blockSignals(True)
                g.set_tile(s)
                g.blockSignals(False)
        self._fit()


class HistoryPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.s = get_settings()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        head, _ = page_header("History", "Every song you've played, in order and when. Pick days with the calendar")
        lay.addLayout(head)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the history…  ( / )")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon("search", C["faint"], 14), QLineEdit.LeadingPosition)
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(300)
        self._t = QTimer(self, singleShot=True, interval=200, timeout=self.refresh)
        self.search.textChanged.connect(lambda _t: self._t.start())
        bar.addWidget(self.search, 1)
        bar.addWidget(label("When", "cap"))
        self.range = DateRange(self.s.get("ui.history.range", "all"))
        self.range.changed.connect(self._range_changed)
        bar.addWidget(self.range)
        bar.addStretch(1)
        self.play_btn = button("Play these", "play", "primary")
        self.play_btn.setToolTip("Play the songs listened to in these dates, most played first")
        self.play_btn.clicked.connect(self._play)
        bar.addWidget(self.play_btn)
        lay.addLayout(bar)
        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        bar2.addWidget(label("Group by", "cap"))
        self.group = Segmented(GROUPS, self.s.get("ui.history.group", "day"))
        self.group.changed.connect(lambda v: (self.s.set("ui.history.group", v), self.refresh()))
        bar2.addWidget(self.group)
        self.view = Segmented([("list", "List"), ("grid", "Grid")], self.s.get("ui.view.history", "list"))
        self.view.changed.connect(self._view)
        bar2.addWidget(self.view)
        self.size = QSlider(Qt.Horizontal)
        self.size.setRange(96, 320)
        self.size.setFixedWidth(90)
        self.size.setToolTip("Tile size (Ctrl + mouse wheel)")
        bar2.addWidget(self.size)
        self.skips = QCheckBox("Show skips")
        self.skips.setToolTip("Also list songs that were skipped before they counted as a listen")
        self.skips.setChecked(bool(self.s.get("ui.history.skips", True)))
        self.skips.toggled.connect(lambda v: (self.s.set("ui.history.skips", v), self.refresh()))
        bar2.addWidget(self.skips)
        bar2.addStretch(1)
        self.not_heard = button("Songs not listened to in this time", "filter", "ghost")
        self.not_heard.setToolTip("Opens Songs with a filter for everything you didn't play in these dates")
        self.not_heard.clicked.connect(self._not_heard)
        bar2.addWidget(self.not_heard)
        lay.addLayout(bar2)
        self.summary = ElidedLabel("")
        self.summary.setObjectName("muted")
        lay.addWidget(self.summary)
        self.stack = QStackedWidget()
        self.tree = HistoryTree()
        self.stack.addWidget(self.tree)
        self.grid = HistoryGrid()
        self.size.setValue(self.grid.tile)
        self.size.valueChanged.connect(self.grid.set_tile)
        self.stack.addWidget(self.grid)
        lay.addWidget(self.stack, 1)
        self.note = label("", "faint", wrap=True)
        self.note.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.note)
        self.entries: list = []
        self._dirty = True
        self._later = QTimer(self, singleShot=True, interval=1500, timeout=self._refresh_if_visible)
        hub.historyChanged.connect(self._history_changed)
        get_library().changed.connect(self._history_changed)
        self._view(self.view.value, save=False)

    def _history_changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._later.start()

    def _refresh_if_visible(self) -> None:
        if self.isVisible():
            self.refresh()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def _range_changed(self) -> None:
        self.s.set("ui.history.range", self.range.key())
        self.refresh()

    def _view(self, v: str, save: bool = True) -> None:
        self.stack.setCurrentIndex(1 if v == "grid" else 0)
        self.size.setVisible(v == "grid")
        if save:
            self.s.set("ui.view.history", v)
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        a, b = times(*self.range.dates())
        entries = list(get_db().listens(a, b))
        if not self.skips.isChecked():
            entries = [r for r in entries if r["counted"]]
        t = self.search.text().strip().casefold()
        if t:
            lib = get_library()

            def hay(r):
                s = lib.get(r["song_id"])
                return " ".join(str(x) for x in ((s["name"], s["artist"], s["album"], s["genre"]) if s else
                                                  (r["title"], r["artist"])) + (r["context"],) if x).casefold()
            entries = [r for r in entries if all(w in hay(r) for w in t.split())]
        self.entries = entries
        mode = self.group.value
        if self.stack.currentIndex() == 1:
            self.grid.fill(entries, mode)
        else:
            self.tree.fill(entries, mode)
        txt = summary(entries) if entries else ""
        if len(entries) > LIST_LIMIT and self.stack.currentIndex() == 0:
            txt += f" · showing the newest {LIST_LIMIT:,}; pick dates to see older ones"
        self.summary.setText(txt)
        self.play_btn.setEnabled(any(r["counted"] for r in entries))
        if not entries:
            total = get_db().listen_count()
            self.note.setText("Nothing played in these dates." if total else
                              "Nothing played yet. Every song you play shows up here, in order, with when.")
        self.note.setVisible(not entries)

    def _not_heard(self) -> None:
        a, b = self.range.dates()
        key = self.range.key()
        hub.goto.emit("songs", filters.new(listen=False, listen_preset=key,
                                           listen_from=a if key == "custom" else None,
                                           listen_to=b if key == "custom" else None))

    def _play(self) -> None:
        counts = Counter(r["song_id"] for r in self.entries if r["counted"])
        ids = [s for s, _n in counts.most_common() if get_library().get(s) is not None]
        if ids:
            get_playback().play_ids(ids, "in_order", f"History: {self.range.preset.currentText()}")

    # keys from the window
    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def target_ids(self) -> list[int]:
        if self.stack.currentIndex() == 0:
            return list(dict.fromkeys(s for _l, s in self.tree.chosen() if get_library().get(s) is not None))
        return []

    def key_first(self) -> None:
        if self.stack.currentIndex() == 0:
            self.tree.scrollToTop()
        else:
            self.grid.verticalScrollBar().setValue(0)

    def key_last(self) -> None:
        if self.stack.currentIndex() == 0:
            self.tree.scrollToBottom()
        else:
            self.grid.verticalScrollBar().setValue(self.grid.verticalScrollBar().maximum())
