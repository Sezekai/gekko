"""Filters for any list of songs, each with its opposite, combinable. The same rules make smart playlists.

A filter is a plain dict (saved as JSON for smart playlists); match() checks a song against it and describe() lists
the active parts for the pills above a list."""
from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .theme import C
from .widgets import Segmented, button, label

EMPTY: dict[str, Any] = {
    "text": "", "fav": None, "rating_min": None, "rating_max": None, "unrated": None, "played": None,
    "lyrics": None, "info": None, "genres": [], "genres_not": False, "len_min": None, "len_max": None,
    "year_min": None, "year_max": None, "added_days": None, "added_old": False, "plays_min": None,
    "artist": "", "album": "", "missing": None, "tidy": None,
}


def new(**kw) -> dict:
    f = dict(EMPTY)
    f["genres"] = []
    f.update(kw)
    return f


def clean(f: dict | None) -> dict:
    out = new()
    for k, v in (f or {}).items():
        if k in out:
            out[k] = v
    return out


def _mmss(s: float) -> str:
    s = int(s)
    return f"{s // 60}:{s % 60:02d}"


def is_active(f: dict) -> bool:
    return any(f.get(k) not in (None, "", [], False) for k in EMPTY if k != "genres_not")


def match(f: dict, r: dict) -> bool:
    t = (f.get("text") or "").strip().casefold()
    if t:
        hay = " ".join(str(x) for x in (r["name"], r["artist"], r["album"], r["album_artist"], r["genre"], r["year"],
                                        r["path"]) if x).casefold()
        if not all(w in hay for w in t.split()):
            return False
    if f.get("fav") is not None and bool(r["favourite"]) != f["fav"]:
        return False
    rt = r["rating"] or 0
    if f.get("unrated") is True and rt:
        return False
    if f.get("unrated") is False and not rt:
        return False
    if f.get("rating_min") is not None and rt < f["rating_min"]:
        return False
    if f.get("rating_max") is not None and rt > f["rating_max"]:
        return False
    if f.get("played") is not None and bool(r["plays"]) != f["played"]:
        return False
    if f.get("plays_min") and (r["plays"] or 0) < f["plays_min"]:
        return False
    if f.get("lyrics") is not None and bool(r["has_lyrics"]) != f["lyrics"]:
        return False
    if f.get("info") is not None:
        known = bool(r["title"] and r["artist"])
        if (f["info"] == "known") != known:
            return False
    if f.get("tidy") is not None and (not r["file_synced"]) != f["tidy"]:
        return False
    if f.get("missing") is not None and bool(r["missing"]) != f["missing"]:
        return False
    gs = f.get("genres") or []
    if gs:
        mine = {g.casefold() for g in r["genres"]} or {"no genre"}
        hit = any(g.casefold() in mine for g in gs)
        if hit == bool(f.get("genres_not")):
            return False
    d = r["duration"] or 0
    if f.get("len_min") is not None and d < f["len_min"]:
        return False
    if f.get("len_max") is not None and d > f["len_max"]:
        return False
    y = r["year_n"] or 0
    if f.get("year_min") is not None and (not y or y < f["year_min"]):
        return False
    if f.get("year_max") is not None and (not y or y > f["year_max"]):
        return False
    if f.get("added_days"):
        recent = (r["added_at"] or 0) >= time.time() - f["added_days"] * 86400
        if recent == bool(f.get("added_old")):
            return False
    a = (f.get("artist") or "").strip().casefold()
    if a and a not in (r["artist"] or "").casefold() and a not in (r["album_artist"] or "").casefold():
        return False
    al = (f.get("album") or "").strip().casefold()
    if al and al not in (r["album"] or "").casefold():
        return False
    return True


def describe(f: dict) -> list[tuple[str, list[str]]]:
    """[(pill text, keys it clears)] for the active parts."""
    out = []
    yn = lambda v, a, b: a if v else b  # noqa: E731
    if f.get("fav") is not None:
        out.append((yn(f["fav"], "Favourites", "Not favourites"), ["fav"]))
    if f.get("unrated") is not None:
        out.append((yn(f["unrated"], "Unrated", "Rated"), ["unrated"]))
    if f.get("rating_min") is not None:
        out.append((f"{f['rating_min'] / 2:g}★ or more", ["rating_min"]))
    if f.get("rating_max") is not None:
        out.append((f"{f['rating_max'] / 2:g}★ or less", ["rating_max"]))
    if f.get("played") is not None:
        out.append((yn(f["played"], "Played", "Never played"), ["played"]))
    if f.get("plays_min"):
        out.append((f"Played {f['plays_min']}+ times", ["plays_min"]))
    if f.get("lyrics") is not None:
        out.append((yn(f["lyrics"], "Has lyrics", "No lyrics"), ["lyrics"]))
    if f.get("info") is not None:
        out.append(("Title and artist known" if f["info"] == "known" else "Not identified", ["info"]))
    if f.get("tidy") is not None:
        out.append((yn(f["tidy"], "Waiting for Tidy up", "Files up to date"), ["tidy"]))
    if f.get("missing") is not None:
        out.append((yn(f["missing"], "Can't be found", "Can be found"), ["missing"]))
    if f.get("genres"):
        g = ", ".join(f["genres"][:3]) + ("…" if len(f["genres"]) > 3 else "")
        out.append((("Not " if f.get("genres_not") else "") + g, ["genres", "genres_not"]))
    if f.get("len_min") is not None or f.get("len_max") is not None:
        a, b = f.get("len_min"), f.get("len_max")
        txt = (f"{_mmss(a)}–{_mmss(b)}" if a is not None and b is not None else
               f"Longer than {_mmss(a)}" if a is not None else f"Shorter than {_mmss(b)}")
        out.append((txt, ["len_min", "len_max"]))
    if f.get("year_min") is not None or f.get("year_max") is not None:
        a, b = f.get("year_min"), f.get("year_max")
        txt = (f"Released {a}–{b}" if a and b else f"Released {a} or later" if a else f"Released {b} or earlier")
        out.append((txt, ["year_min", "year_max"]))
    if f.get("added_days"):
        out.append(((f"Added over {f['added_days']} days ago" if f.get("added_old") else
                     f"Added in the last {f['added_days']} days"), ["added_days", "added_old"]))
    if (f.get("artist") or "").strip():
        out.append((f"Artist: {f['artist']}", ["artist"]))
    if (f.get("album") or "").strip():
        out.append((f"Album: {f['album']}", ["album"]))
    return out


class _Tri(Segmented):
    """Any / yes / no."""

    def __init__(self, yes: str, no: str):
        super().__init__([("any", "Any"), ("yes", yes), ("no", no)], "any")

    def get(self):
        return None if self.value == "any" else self.value == "yes"

    def put(self, v) -> None:
        self.set_value("any" if v is None else ("yes" if v else "no"))


class FilterForm(QWidget):
    """Every filter, as a form (the Filters pop-up and the smart playlist editor)."""

    changed = Signal()

    def __init__(self, genres: list[str], smart: bool = False, parent=None):
        super().__init__(parent)
        self._quiet = False
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(7)
        row = 0

        def name(t):
            lb = label(t, "cap")
            lb.setFixedWidth(88)
            return lb

        def add(t, w):
            nonlocal row
            g.addWidget(name(t), row, 0, Qt.AlignTop if isinstance(w, QListWidget) else Qt.AlignVCenter)
            g.addWidget(w, row, 1)
            row += 1

        self.fav = _Tri("Favourites", "Not favourites")
        add("Favourite", self.fav)
        rw = QWidget()
        rl = QHBoxLayout(rw)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.rate_mode = QComboBox()
        for k, t in (("any", "Any rating"), ("min", "At least"), ("max", "At most"), ("unrated", "Unrated"),
                     ("rated", "Rated")):
            self.rate_mode.addItem(t, k)
        self.rate_val = QComboBox()
        for v in range(1, 11):
            self.rate_val.addItem(f"{v / 2:g} star{'s' if v != 2 else ''}", v)
        self.rate_val.setCurrentIndex(7)
        rl.addWidget(self.rate_mode)
        rl.addWidget(self.rate_val)
        rl.addStretch(1)
        add("Rating", rw)
        self.played = _Tri("Played", "Never played")
        add("Played", self.played)
        self.lyrics = _Tri("Has lyrics", "No lyrics")
        add("Lyrics", self.lyrics)
        self.info = _Tri("Known", "Not identified")
        add("Song info", self.info)
        # length
        lw = QWidget()
        ll = QHBoxLayout(lw)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        self.len_min = self._mins()
        self.len_max = self._mins()
        ll.addWidget(label("from", "faint"))
        ll.addWidget(self.len_min)
        ll.addWidget(label("to", "faint"))
        ll.addWidget(self.len_max)
        ll.addWidget(label("minutes (0 = no limit)", "faint"))
        ll.addStretch(1)
        add("Length", lw)
        yw = QWidget()
        yl = QHBoxLayout(yw)
        yl.setContentsMargins(0, 0, 0, 0)
        yl.setSpacing(6)
        self.year_min = self._year()
        self.year_max = self._year()
        yl.addWidget(label("from", "faint"))
        yl.addWidget(self.year_min)
        yl.addWidget(label("to", "faint"))
        yl.addWidget(self.year_max)
        yl.addStretch(1)
        add("Released", yw)
        aw = QWidget()
        al = QHBoxLayout(aw)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(6)
        self.added = QComboBox()
        for d, t in ((0, "Any time"), (7, "7 days"), (30, "30 days"), (90, "3 months"), (365, "A year")):
            self.added.addItem(t, d)
        self.added_old = Segmented([("new", "Within"), ("old", "Longer ago than")], "new")
        al.addWidget(self.added_old)
        al.addWidget(self.added)
        al.addStretch(1)
        add("Added", aw)
        if smart:
            self.plays_min = QSpinBox()
            self.plays_min.setRange(0, 9999)
            self.plays_min.setSpecialValueText("Any")
            self.plays_min.setFixedWidth(90)
            add("Played at least", self.plays_min)
            self.artist = QLineEdit()
            self.artist.setPlaceholderText("Artist name contains…")
            add("Artist", self.artist)
            self.album = QLineEdit()
            self.album.setPlaceholderText("Album name contains…")
            add("Album", self.album)
        gw = QWidget()
        gl = QVBoxLayout(gw)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(5)
        self.genres_not = Segmented([("in", "Any of these"), ("not", "None of these")], "in")
        gl.addWidget(self.genres_not)
        self.genre_list = QListWidget()
        self.genre_list.setFixedHeight(150)
        for gname in genres:
            it = QListWidgetItem(gname)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.genre_list.addItem(it)
        gl.addWidget(self.genre_list)
        add("Genre", gw)
        for w in (self.fav, self.played, self.lyrics, self.info, self.added_old, self.genres_not):
            w.changed.connect(self._emit)
        for w in (self.rate_mode, self.rate_val, self.added):
            w.currentIndexChanged.connect(self._emit)
        for w in (self.len_min, self.len_max, self.year_min, self.year_max):
            w.valueChanged.connect(self._emit)
        self.genre_list.itemChanged.connect(self._emit)
        if smart:
            self.plays_min.valueChanged.connect(self._emit)
            self.artist.textChanged.connect(self._emit)
            self.album.textChanged.connect(self._emit)
        self.smart = smart

    @staticmethod
    def _mins() -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, 600)
        s.setSpecialValueText("–")
        s.setFixedWidth(64)
        return s

    @staticmethod
    def _year() -> QSpinBox:
        s = QSpinBox()
        s.setRange(0, 2100)
        s.setSpecialValueText("–")
        s.setFixedWidth(76)
        return s

    def _emit(self, *_):
        if not self._quiet:
            self.changed.emit()

    def get(self, base: dict | None = None) -> dict:
        f = clean(base)
        f["fav"] = self.fav.get()
        m = self.rate_mode.currentData()
        f["rating_min"] = self.rate_val.currentData() if m == "min" else None
        f["rating_max"] = self.rate_val.currentData() if m == "max" else None
        f["unrated"] = True if m == "unrated" else (False if m == "rated" else None)
        f["played"] = self.played.get()
        f["lyrics"] = self.lyrics.get()
        v = self.info.get()
        f["info"] = None if v is None else ("known" if v else "unknown")
        f["len_min"] = self.len_min.value() * 60 if self.len_min.value() else None
        f["len_max"] = self.len_max.value() * 60 if self.len_max.value() else None
        f["year_min"] = self.year_min.value() or None
        f["year_max"] = self.year_max.value() or None
        f["added_days"] = self.added.currentData() or None
        f["added_old"] = self.added_old.value == "old"
        f["genres"] = [self.genre_list.item(i).text() for i in range(self.genre_list.count())
                       if self.genre_list.item(i).checkState() == Qt.Checked]
        f["genres_not"] = self.genres_not.value == "not"
        if self.smart:
            f["plays_min"] = self.plays_min.value() or None
            f["artist"] = self.artist.text().strip()
            f["album"] = self.album.text().strip()
        return f

    def put(self, f: dict) -> None:
        self._quiet = True
        try:
            f = clean(f)
            self.fav.put(f["fav"])
            m = ("min" if f["rating_min"] is not None else "max" if f["rating_max"] is not None else
                 "unrated" if f["unrated"] is True else "rated" if f["unrated"] is False else "any")
            self.rate_mode.setCurrentIndex(max(0, self.rate_mode.findData(m)))
            v = f["rating_min"] if f["rating_min"] is not None else f["rating_max"]
            if v is not None:
                self.rate_val.setCurrentIndex(max(0, self.rate_val.findData(int(v))))
            self.played.put(f["played"])
            self.lyrics.put(f["lyrics"])
            self.info.put(None if f["info"] is None else f["info"] == "known")
            self.len_min.setValue(int((f["len_min"] or 0) // 60))
            self.len_max.setValue(int((f["len_max"] or 0) // 60))
            self.year_min.setValue(int(f["year_min"] or 0))
            self.year_max.setValue(int(f["year_max"] or 0))
            self.added.setCurrentIndex(max(0, self.added.findData(int(f["added_days"] or 0))))
            self.added_old.set_value("old" if f["added_old"] else "new")
            want = {g.casefold() for g in f["genres"]}
            for i in range(self.genre_list.count()):
                it = self.genre_list.item(i)
                it.setCheckState(Qt.Checked if it.text().casefold() in want else Qt.Unchecked)
            self.genres_not.set_value("not" if f["genres_not"] else "in")
            if self.smart:
                self.plays_min.setValue(int(f["plays_min"] or 0))
                self.artist.setText(f["artist"] or "")
                self.album.setText(f["album"] or "")
        finally:
            self._quiet = False


class FilterPopup(QFrame):
    """The Filters pop-up over a list: the form, Clear all and Close."""

    changed = Signal(dict)

    def __init__(self, genres: list[str], f: dict, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("dialog")
        self.setAttribute(Qt.WA_StyledBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("Filters", "h2"))
        head.addWidget(label("Each has its opposite; they combine", "faint"), 1)
        lay.addLayout(head)
        self.form = FilterForm(genres)
        self.form.put(f)
        self.base = f
        self.form.changed.connect(lambda: self.changed.emit(self.form.get(self.base)))
        lay.addWidget(self.form)
        row = QHBoxLayout()
        row.addStretch(1)
        clear = button("Clear all", "x", "ghost")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        close = button("Done", None, "primary")
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)
        self.setMinimumWidth(520)

    def _clear(self) -> None:
        text = self.base.get("text", "")
        self.base = new(text=text)
        self.form.put(self.base)
        self.changed.emit(self.base)


class Pills(QWidget):
    """The active filters as pills with ×, and Clear all."""

    cleared = Signal(list)          # keys to clear ([] = everything)

    def __init__(self, parent=None):
        super().__init__(parent)
        from .widgets import FlowLayout
        self.lay = FlowLayout(self, 6)

    def set_filter(self, f: dict) -> None:
        from .widgets import clear_layout
        clear_layout(self.lay)
        parts = describe(f)
        for txt, keys in parts:
            b = QPushButton(txt + "  ×")
            b.setObjectName("chip")
            b.setCheckable(True)
            b.setChecked(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip("Remove this filter")
            b.clicked.connect(lambda _=False, k=keys: self.cleared.emit(k))
            self.lay.addWidget(b)
        if len(parts) > 1:
            b = button("Clear all", None, "ghost")
            b.clicked.connect(lambda: self.cleared.emit([]))
            self.lay.addWidget(b)
        self.setVisible(bool(parts))
