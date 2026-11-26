"""Tidy up: review what Gekko would do to your song files, change any of it, then apply it - all of it, the ones
the filters show, or one song at a time.

Nothing changes on disk until Apply. Songs already in an album folder stay there, file names that start with a
track number keep it, and names that only differ in punctuation are left alone. Click a song to see everything
that would change in the panel on the right, and change any part: another name, another folder, other tag values,
tags left out, the cover or not. Double-click New name or Moves to to edit them in the list."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QScrollArea, QSplitter, QStackedWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.db import get_db, key
from ..core.settings import get_settings
from ..jobs import tidy
from . import actions, dialogs, workers
from .covers import get_covers, placeholder
from .hub import hub
from .jobqueue import get_queue
from .library import get_library
from .listlook import LinedDelegate, remember_columns, style_list
from .theme import C
from .widgets import ElidedLabel, Segmented, button, clear_layout, flow_row, hline, label, page_header, panel

TAG_NAMES = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
             ("genre", "Genre"), ("year", "Year"), ("track", "Track"), ("disc", "Disc")]
FILTERS = [("rename", "Renamed"), ("move", "Moved"), ("tags", "Tags"), ("cover", "Cover art"),
           ("edited", "Changed by you")]
C_DO, C_SONG, C_NOW, C_NAME, C_TO, C_TAGS, C_COVER = range(7)


def _rel(p: str, root: str) -> str:
    try:
        return str(Path(p).relative_to(root))
    except ValueError:
        return p


class _Tri(QComboBox):
    """One filter: either way, only songs where it happens, or only songs where it doesn't."""

    def __init__(self, name: str):
        super().__init__()
        for k, t in (("any", f"{name}: any"), ("yes", f"{name}: yes"), ("no", f"{name}: no")):
            self.addItem(t, k)
        self.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.currentIndexChanged.connect(self._restyle)

    @property
    def changed(self):
        return self.currentIndexChanged

    @property
    def value(self) -> str:
        return self.currentData()

    def set_value(self, v: str) -> None:
        self.setCurrentIndex(max(0, self.findData(v)))

    def get(self):
        return None if self.value == "any" else self.value == "yes"

    def _restyle(self) -> None:
        on = self.value != "any"
        self.setProperty("active", on)
        self.setStyleSheet(f"QComboBox {{ border: 1px solid {C['acc']}; color: {C['acc']}; }}" if on else "")


class _EditDelegate(LinedDelegate):
    """New name and Moves to are edited with the full value (the list shows a shorter one)."""

    def __init__(self, page: "TidyPage"):
        super().__init__(page.table)
        self.page = page

    def createEditor(self, parent, opt, idx):  # noqa: N802
        return QLineEdit(parent)

    def setEditorData(self, ed, idx):  # noqa: N802
        ed.setText(idx.data(Qt.UserRole + 1) or "")
        ed.selectAll()

    def setModelData(self, ed, model, idx):  # noqa: N802
        sid = model.index(idx.row(), C_DO).data(Qt.UserRole)
        col, text = idx.column(), ed.text()
        QTimer.singleShot(0, lambda: self.page.edited_cell(sid, col, text))


class Detail(QFrame):
    """Everything that would change for one song, each part changeable, and Apply for just this song."""

    def __init__(self, page: "TidyPage"):
        super().__init__()
        self.page = page
        self.setObjectName("panel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setMinimumWidth(300)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transparent")
        sc.setWidget(inner)
        outer.addWidget(sc)
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(12, 10, 12, 12)
        self.lay.setSpacing(8)
        self.sid: int | None = None
        self.cover_lbl: QLabel | None = None
        self.empty()
        get_covers().ready.connect(self._cover_ready)

    def _cover_ready(self, k: str) -> None:
        if self.sid is not None and k == f"song:{self.sid}" and self.cover_lbl is not None:
            self._paint_cover()

    def empty(self) -> None:
        self.sid = None
        self.cover_lbl = None
        clear_layout(self.lay)
        self.lay.addWidget(label("Click a song to see everything that would change for it, and change any of it.",
                                 "faint", wrap=True))
        self.lay.addStretch(1)

    def show_plan(self, p: dict | None) -> None:
        if p is None:
            self.empty()
            return
        r = get_db().song(p["id"])
        if r is None:
            self.empty()
            return
        clear_layout(self.lay)
        self.cover_lbl = None
        self.sid = p["id"]
        ov = self.page.ov.get(self.sid, {})
        src, dst = Path(p["src"]), Path(p["dst"])
        t = QLabel(f"{r['artist'] or 'Unknown artist'} - {r['title']}")
        t.setObjectName("h2")
        t.setWordWrap(True)
        self.lay.addWidget(t)
        if p.get("notes"):
            self.lay.addWidget(label("Left as it is: " + "; ".join(p["notes"]) + ".", "faint", wrap=True))
        # cover
        if p["can_cover"] or r["has_art"]:
            row = QHBoxLayout()
            self.cover_lbl = QLabel()
            self.cover_lbl.setFixedSize(84, 84)
            row.addWidget(self.cover_lbl)
            col = QVBoxLayout()
            if r["has_art"] == 1:
                col.addWidget(label("This file already has cover art.", "muted", wrap=True))
            else:
                cb = QCheckBox("Put this cover into the file")
                cb.setChecked(bool(p["cover"]))
                cb.toggled.connect(lambda on: self.page.set_ov(self.sid, cover=on))
                col.addWidget(cb)
                src_txt = "from its folder" if r["folder_art"] else "found by a lookup"
                col.addWidget(label(f"The picture is {src_txt}; the file has none yet.", "faint", wrap=True))
            col.addStretch(1)
            row.addLayout(col, 1)
            self.lay.addLayout(row)
            self._paint_cover()
        self.lay.addWidget(hline())
        # file name
        self.lay.addWidget(label("File name", "cap"))
        self.lay.addWidget(self._now(src.name))
        nr = QHBoxLayout()
        self.name_edit = QLineEdit(dst.stem)
        self.name_edit.setToolTip("The new name (the extension stays). Press Enter to use it.")
        self.name_edit.returnPressed.connect(lambda: self._name(self.name_edit.text()))
        nr.addWidget(self.name_edit, 1)
        nr.addWidget(label(src.suffix, "faint"))
        self.lay.addLayout(nr)
        br = QHBoxLayout()
        b1 = button("Artist - Title", None, "ghost")
        b1.setToolTip("Name it after the song")
        b1.clicked.connect(lambda: self._name(tidy.nice_name(tidy.stem_for(r) or src.stem)))
        b2 = button("Keep the current name", None, "ghost")
        b2.clicked.connect(lambda: self._name(""))
        br.addWidget(b1)
        br.addWidget(b2)
        br.addStretch(1)
        self.lay.addLayout(br)
        # folder
        self.lay.addWidget(label("Folder", "cap"))
        self.lay.addWidget(self._now(str(src.parent)))
        fr = QHBoxLayout()
        self.folder_edit = QLineEdit(str(dst.parent))
        self.folder_edit.setToolTip("Where it goes. Press Enter to use it.")
        self.folder_edit.returnPressed.connect(lambda: self._folder(self.folder_edit.text()))
        fr.addWidget(self.folder_edit, 1)
        brw = button("", "folder", "ghost")
        brw.setToolTip("Choose a folder")
        brw.clicked.connect(self._browse)
        fr.addWidget(brw)
        self.lay.addLayout(fr)
        br = QHBoxLayout()
        b3 = button("Don't move it", None, "ghost")
        b3.clicked.connect(lambda: self._folder(""))
        b4 = button("Artist\\Album", None, "ghost")
        b4.setToolTip("Into the sort folder, by artist and album")
        b4.clicked.connect(lambda: self._folder(str(tidy.folder_for(r, tidy.options()["root"]) or src.parent)))
        br.addWidget(b3)
        br.addWidget(b4)
        br.addStretch(1)
        self.lay.addLayout(br)
        self.lay.addWidget(hline())
        # tags
        self.lay.addWidget(label("Tags (the ticked ones are written)", "cap"))
        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(2)
        cur = tidy.file_tags(r)
        custom = ov.get("tags") or {}
        skip = set(ov.get("skip") or ())
        for i, (k, name) in enumerate(TAG_NAMES):
            want = custom.get(k, r[k])
            have = cur.get(k)
            changes = k in p["tags"]
            cb = QCheckBox()
            cb.setChecked(changes)
            cb.setEnabled(changes or k in skip)
            cb.setToolTip("Written" if changes else ("Left out by you" if k in skip else "Already the same"))
            cb.toggled.connect(lambda on, k=k: self._skip(k, not on))
            g.addWidget(cb, i * 2 + 1, 0)
            g.addWidget(label(name, "cap"), i * 2, 1)
            now = ElidedLabel(f"now: {have}" if have not in (None, "") else "now: (empty)")
            now.setObjectName("faint")
            g.addWidget(now, i * 2, 2)
            e = QLineEdit("" if want is None else str(want))
            e.setPlaceholderText("(empty)")
            e.setToolTip("Change it and press Enter")
            e.returnPressed.connect(lambda k=k, e=e: self._tag(k, e.text()))
            g.addWidget(e, i * 2 + 1, 1, 1, 2)
        g.setColumnStretch(2, 1)
        self.lay.addLayout(g)
        self.lay.addWidget(hline())
        # just this song
        row = QHBoxLayout()
        ap = button("Apply to this song", "check", "primary")
        ap.clicked.connect(lambda: self.page.apply([self.sid]))
        row.addWidget(ap)
        if ov:
            un = button("Undo my changes", "undo", "ghost")
            un.clicked.connect(lambda: self.page.clear_ov(self.sid))
            row.addWidget(un)
        row.addStretch(1)
        self.lay.addLayout(row)
        sk = button("Put it back in" if self.sid in self.page.skip else "Leave it out", None, "ghost")
        sk.clicked.connect(lambda: self.page.toggle_skip([self.sid]))
        self.lay.addWidget(sk, 0, Qt.AlignLeft)
        self.lay.addStretch(1)

    def _now(self, text: str) -> QLabel:
        lb = ElidedLabel(f"now: {text}", mode=Qt.ElideMiddle)
        lb.setObjectName("faint")
        lb.setToolTip(text)
        return lb

    def _paint_cover(self) -> None:
        pm = get_covers().get(f"song:{self.sid}", 84) or placeholder(84, str(self.sid))
        self.cover_lbl.setPixmap(pm)

    def _name(self, v: str) -> None:
        self.page.set_ov(self.sid, name=v.strip())

    def _folder(self, v: str) -> None:
        v = v.strip()
        if v and not os.path.isabs(v):
            v = str(Path(tidy.options()["root"]) / v)
        self.page.set_ov(self.sid, folder=v)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Move it to…", self.folder_edit.text())
        if d:
            self._folder(str(Path(d)))

    def _tag(self, k: str, v: str) -> None:
        r = get_db().song(self.sid)
        ov = dict(self.page.ov.get(self.sid, {}))
        tg = dict(ov.get("tags") or {})
        cur = "" if r[k] is None else str(r[k])
        if v.strip() == cur:
            tg.pop(k, None)
        else:
            tg[k] = v.strip()
        skip = set(ov.get("skip") or ())
        skip.discard(k)
        self.page.set_ov(self.sid, tags=tg, skip=sorted(skip))

    def _skip(self, k: str, on: bool) -> None:
        ov = self.page.ov.get(self.sid, {})
        skip = set(ov.get("skip") or ())
        (skip.add if on else skip.discard)(k)
        self.page.set_ov(self.sid, skip=sorted(skip))


class TidyPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.s = get_settings()
        self.plan: list[dict] = []
        self.shown: list[dict] = []
        self.skip: set[int] = set(get_db().kv_get("tidy_skip") or [])
        self.ov: dict[int, dict] = {int(k): v for k, v in (get_db().kv_get("tidy_overrides") or {}).items()}
        self.album_dirs: set[str] = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        head, _ = page_header("Tidy up", "Write looked-up info into your files. Nothing changes until you apply it; "
                                         "click a song to change what happens to it")
        lay.addLayout(head)
        tb = panel()
        tl = QVBoxLayout(tb)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(6)
        r1 = QHBoxLayout()
        r1.setSpacing(12)
        r1.addWidget(label("Do", "cap"))
        self.o_rename = self._opt(r1, "Rename to “Artist - Title”", "tidy.rename")
        self.o_tags = self._opt(r1, "Write the tags", "tidy.tags")
        self.o_cover = self._opt(r1, "Add cover art", "tidy.cover")
        r1.addStretch(1)
        self.refresh_btn = button("Check again", "refresh", "ghost")
        self.refresh_btn.clicked.connect(self.recompute)
        r1.addWidget(self.refresh_btn)
        self.apply_btn = button("Apply", "check", "primary")
        self.apply_btn.clicked.connect(lambda: self.apply(None))
        r1.addWidget(self.apply_btn)
        tl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        r2.addWidget(label("Move", "cap"))
        self.sort = QComboBox()
        for k, t in tidy.SORT_MODES:
            self.sort.addItem(t, k)
        self.sort.setCurrentIndex(max(0, self.sort.findData(tidy.options()["sort"])))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        r2.addWidget(self.sort)
        r2.addWidget(label("into", "faint"))
        self.root = QLineEdit(self.s.get("library.sort_root") or "E:\\Music")
        self.root.setMinimumWidth(200)
        self.root.editingFinished.connect(self._root_typed)
        r2.addWidget(self.root, 1)
        br = button("Browse…", "folder")
        br.clicked.connect(self._browse)
        r2.addWidget(br)
        r2.addWidget(label("\\Artist\\Album (no album: Artist\\Singles)", "faint"))
        tl.addLayout(r2)
        lay.addWidget(tb)
        # filters: they choose what's listed (and what Apply does); the Do ticks still decide what happens
        self.filter_row, fr = flow_row(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(180)
        self._st = QTimer(self, singleShot=True, interval=200, timeout=self._fill)
        self.search.textChanged.connect(lambda _t: self._st.start())
        fr.addWidget(self.search)
        fr.addWidget(label("Show", "cap"))
        self.tri: dict[str, _Tri] = {}
        for k, t in FILTERS:
            w = _Tri(t)
            w.setToolTip("Any: either way. Yes: only songs where this happens. No: only songs where it doesn't.\n"
                         "This chooses what's listed (and what Apply does); the Do ticks above still decide what "
                         "happens to each song.")
            w.changed.connect(lambda _v: self._fill())
            self.tri[k] = w
            fr.addWidget(w)
        clr = button("Clear", "x", "ghost")
        clr.clicked.connect(self._clear_filters)
        fr.addWidget(clr)
        lay.addWidget(self.filter_row)
        sumrow = QHBoxLayout()
        self.tabs = Segmented([("ready", "Ready"), ("unknown", "Not identified"), ("pending", "Looking up"),
                               ("dupes", "Duplicates")], "ready")
        self.tabs.changed.connect(self._tab)
        sumrow.addWidget(self.tabs)
        sumrow.addStretch(1)
        self.summary = ElidedLabel("")
        self.summary.setObjectName("faint")
        self.skip_btn = button("Leave out", "minus", "ghost")
        self.skip_btn.setToolTip("Untick the chosen songs")
        self.skip_btn.clicked.connect(lambda: self._set_skip(True))
        sumrow.addWidget(self.skip_btn)
        self.inc_btn = button("Put back", "plus", "ghost")
        self.inc_btn.setToolTip("Tick the chosen songs again")
        self.inc_btn.clicked.connect(lambda: self._set_skip(False))
        sumrow.addWidget(self.inc_btn)
        self.edit_btn = button("Edit info…", "edit")
        self.edit_btn.clicked.connect(self._edit)
        sumrow.addWidget(self.edit_btn)
        self.again_btn = button("Look up again", "search")
        self.again_btn.clicked.connect(lambda: actions.identify_again(self._chosen()))
        sumrow.addWidget(self.again_btn)
        self.harder_btn = button("Try harder", "search", "ghost")
        self.harder_btn.setToolTip("Also tries the song sped up and slowed down (edits often are). Slower.")
        self.harder_btn.clicked.connect(lambda: actions.identify_again(self._chosen(), deep=True))
        sumrow.addWidget(self.harder_btn)
        lay.addLayout(sumrow)
        lay.addWidget(self.summary)
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.table = self._table(["Do", "Song", "Now", "New name", "Moves to", "Tags", "Cover"], "tidy")
        self.table.setItemDelegate(_EditDelegate(self))
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.horizontalHeader().setSectionResizeMode(C_DO, QHeaderView.Fixed)
        self.table.setColumnWidth(C_DO, 40)
        self.table.itemChanged.connect(self._ticked)
        self.table.itemSelectionChanged.connect(self._selected)
        self.stack.addWidget(self.table)
        self.unknown = self._table(["File", "Folder", "What happened"], "tidy.unknown")
        self.unknown.doubleClicked.connect(lambda _i: self._edit())
        self.stack.addWidget(self.unknown)
        self.pending = self._table(["File", "Folder"], "tidy.pending")
        self.stack.addWidget(self.pending)
        from .duplicates import DuplicatesView
        self.dupes = DuplicatesView()
        self.stack.addWidget(self.dupes)
        self.split.addWidget(self.stack)
        self.detail = Detail(self)
        self.split.addWidget(self.detail)
        self.split.setStretchFactor(0, 1)
        self.split.setSizes([900, int(self.s.get("ui.tidy_detail_w", 340))])
        self.split.splitterMoved.connect(lambda *_: self.s.set("ui.tidy_detail_w", self.split.sizes()[1]))
        lay.addWidget(self.split, 1)
        self.empty = label("", "faint", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        self._dirty = True
        self._busy = False
        self._timer = QTimer(self, singleShot=True, interval=2500, timeout=self._bg_recompute)
        get_library().changed.connect(self._lib_changed)
        get_library().songUpdated.connect(lambda _s: self._lib_changed())
        self._tab("ready")
        QTimer.singleShot(5000, self._bg_recompute)

    def _table(self, cols: list[str], key_: str) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(28)
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        t.setWordWrap(False)
        widths = {"Do": 40, "Song": 230, "Now": 230, "New name": 230, "Moves to": 200, "Tags": 170, "Cover": 90,
                  "File": 300, "Folder": 300}
        for i, c in enumerate(cols):
            t.setColumnWidth(i, widths.get(c, 200))
        style_list(t)
        remember_columns(t.horizontalHeader(), key_)
        return t

    def _opt(self, row: QHBoxLayout, text: str, k: str) -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(bool(self.s.get(k, True)))
        c.toggled.connect(lambda v: (self.s.set(k, v), self.recompute()))
        row.addWidget(c)
        return c

    def _sort_changed(self) -> None:
        self.s.set("tidy.sort_mode", self.sort.currentData())
        self.recompute()

    def _root_typed(self) -> None:
        t = self.root.text().strip()
        if t and t != self.s.get("library.sort_root"):
            self.s.set("library.sort_root", t)
            self.recompute()

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Sort songs into…", self.root.text() or str(Path.home()))
        if d:
            self.root.setText(str(Path(d)))
            self._root_typed()

    def _clear_filters(self) -> None:
        for w in self.tri.values():
            w.set_value("any")
        self.search.clear()
        self._fill()

    def _lib_changed(self) -> None:
        self._dirty = True
        self._timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.recompute()

    # ---- the plan -------------------------------------------------------------------------------------------
    def recompute(self) -> None:
        self._dirty = False
        self.summary.setText("Working out what would change…")
        self._bg_recompute()

    def _bg_recompute(self) -> None:
        if self._busy:
            self._timer.start()
            return
        self._busy = True
        opt = tidy.options()
        ov = dict(self.ov)

        def work():
            db = get_db()
            dirs = tidy.album_folders(db.q("SELECT path, album, folder_art FROM songs WHERE missing=0"))
            return tidy.plans(None, opt, ov), dirs

        def done(res):
            self._busy = False
            self.plan, self.album_dirs = res
            hub.tidyCount.emit(len([p for p in self.plan if p["id"] not in self.skip]))
            if self.isVisible():
                self._fill()

        def failed(msg):
            self._busy = False
            self.summary.setText(f"Couldn't work it out: {msg}")

        workers.run_bg(work, done=done, failed=failed)

    def _replan(self, sid: int) -> None:
        """One song again, after you changed something about it (no need to work everything out)."""
        r = get_db().song(sid)
        taken = {key(p["dst"]) for p in self.plan if p["id"] != sid}
        new = tidy.plan_for(r, tidy.options(), taken, self.album_dirs, self.ov.get(sid)) if r is not None else None
        idx = next((i for i, p in enumerate(self.plan) if p["id"] == sid), None)
        if new is None and idx is not None:
            del self.plan[idx]
        elif new is not None and idx is not None:
            self.plan[idx] = new
        elif new is not None:
            self.plan.append(new)
        self._fill(keep=sid)

    def set_ov(self, sid: int | None, **kw) -> None:
        if sid is None:
            return
        ov = dict(self.ov.get(sid, {}))
        ov.update(kw)
        ov = {k: v for k, v in ov.items() if v not in ([], {}) and not (k == "cover" and v is None)}
        if ov:
            self.ov[sid] = ov
        else:
            self.ov.pop(sid, None)
        self._save_ov()
        self._replan(sid)

    def clear_ov(self, sid: int) -> None:
        self.ov.pop(sid, None)
        self._save_ov()
        self._replan(sid)

    def _save_ov(self) -> None:
        get_db().kv_set("tidy_overrides", {str(k): v for k, v in self.ov.items()})

    def edited_cell(self, sid: int, col: int, text: str) -> None:
        if col == C_NAME:
            self.set_ov(sid, name=text.strip())
        elif col == C_TO:
            v = text.strip()
            if v and not os.path.isabs(v):
                v = str(Path(self.root.text()) / v)
            self.set_ov(sid, folder=v)

    # ---- showing it -----------------------------------------------------------------------------------------
    def _tab(self, k: str) -> None:
        self.stack.setCurrentIndex({"ready": 0, "unknown": 1, "pending": 2, "dupes": 3}[k])
        ready = k == "ready"
        for w in (self.skip_btn, self.inc_btn, self.filter_row, self.detail):
            w.setVisible(ready)
        self.edit_btn.setVisible(k != "dupes")
        self.again_btn.setVisible(k in ("unknown", "pending"))
        self.harder_btn.setVisible(k == "unknown")
        self._fill()

    def _passes(self, p: dict) -> bool:
        vals = {"rename": p["rename"], "move": p["move"], "tags": bool(p["tags"]), "cover": p["cover"],
                "edited": p["edited"]}
        for k, w in self.tri.items():
            want = w.get()
            if want is not None and vals[k] != want:
                return False
        t = self.search.text().strip().casefold()
        if t:
            hay = f"{p['src']} {p['dst']}".casefold()
            r = get_library().get(p["id"])
            if r is not None:
                hay += f" {r['name']} {r['artist']} {r['album']}".casefold()
            if not all(w in hay for w in t.split()):
                return False
        return True

    def _fill(self, keep: int | None = None) -> None:
        k = self.tabs.value
        lib = get_library()
        unknown = [r for r in lib.rows if r["id_status"] in ("not_found", "error") and not r["title"]
                   and not r["missing"]]
        pending = [r for r in lib.rows if r["id_status"] == "pending" and not r["missing"]]
        self.shown = [p for p in self.plan if self._passes(p)]
        do = [p for p in self.shown if p["id"] not in self.skip]
        self.tabs.set_count("ready", len(self.plan))
        self.tabs.set_count("unknown", len(unknown))
        self.tabs.set_count("pending", len(pending))
        filtered = len(self.shown) != len(self.plan)
        self.apply_btn.setText(f"Apply to {len(do):,} song{'s' if len(do) != 1 else ''}" +
                               (" shown" if filtered else ""))
        self.apply_btn.setEnabled(bool(do) and k == "ready")
        root = self.root.text()
        if k == "ready":
            t = self.table
            sel = keep if keep is not None else (self._chosen()[:1] or [None])[0]
            t.blockSignals(True)
            t.setRowCount(len(self.shown))
            faint = QColor(C["faint"])
            for i, p in enumerate(self.shown):
                r = lib.get(p["id"])
                on = p["id"] not in self.skip
                inc = QTableWidgetItem()
                inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                inc.setCheckState(Qt.Checked if on else Qt.Unchecked)
                inc.setData(Qt.UserRole, p["id"])
                inc.setToolTip("Will be done" if on else "Left out")
                t.setItem(i, C_DO, inc)
                song = f"{r['artist']} - {r['name']}" if r and r["artist"] else (r["name"] if r else "")
                cells = {
                    C_SONG: (song + ("   ✎" if p["edited"] else ""), None),
                    C_NOW: (Path(p["src"]).name, p["src"]),
                    C_NAME: (Path(p["dst"]).name if p["rename"] else "(same name)", Path(p["dst"]).stem),
                    C_TO: (_rel(str(Path(p["dst"]).parent), root) if p["move"] else "(stays)",
                           str(Path(p["dst"]).parent)),
                    C_TAGS: (", ".join(p["tags"]) or "–", None),
                    C_COVER: ("Adds cover" if p["cover"] else ("Could add" if p["can_cover"] else "–"), None),
                }
                for c, (txt, full) in cells.items():
                    it = QTableWidgetItem(txt)
                    fl = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    if c in (C_NAME, C_TO):
                        fl |= Qt.ItemIsEditable
                        it.setData(Qt.UserRole + 1, full)
                        it.setToolTip(f"{full}\nDouble-click to change")
                    elif c == C_SONG and p["edited"]:
                        it.setToolTip("You changed what happens to this song")
                    elif full:
                        it.setToolTip(full)
                    if c == C_TAGS and p["tags"]:
                        it.setToolTip("\n".join(f"{kk}: {v}" for kk, v in p["tags"].items()))
                    it.setFlags(fl)
                    if not on:
                        it.setForeground(faint)
                        f = it.font()
                        f.setItalic(True)
                        if c in (C_NAME, C_TO, C_TAGS, C_COVER):
                            f.setStrikeOut(True)
                        it.setFont(f)
                    elif c in (C_NAME, C_TO, C_TAGS, C_COVER) and txt not in ("–", "(stays)", "(same name)",
                                                                          "Could add"):
                        it.setForeground(QColor(C["text"]))
                    elif c in (C_NOW, C_NAME, C_TO, C_TAGS, C_COVER):
                        it.setForeground(QColor(C["sec"]))
                    t.setItem(i, c, it)
            t.blockSignals(False)
            if sel is not None:
                for i, p in enumerate(self.shown):
                    if p["id"] == sel:
                        t.selectRow(i)
                        break
            self._selected()
            bits = [f"{len(do):,} of {len(self.shown):,} " + ("shown " if filtered else "") + "would change"]
            n_kept = sum(1 for p in self.plan if p["notes"])
            if n_kept:
                bits.append(f"{n_kept:,} keep their folder or name")
            self.summary.setText(" · ".join(bits) + ". The audio is never touched, nothing is overwritten, every "
                                 "change can be undone (Log > File moves).")
            self.empty.setVisible(not self.shown)
            self.empty.setText("Nothing matches these filters." if self.plan else
                               ("Everything is tidy: every song's file already matches its info." if not self._busy
                                else "Working it out…"))
        elif k == "unknown":
            t = self.unknown
            t.setRowCount(len(unknown))
            for i, r in enumerate(unknown):
                a = QTableWidgetItem(Path(r["path"]).name)
                a.setData(Qt.UserRole, r["id"])
                a.setToolTip(r["path"])
                t.setItem(i, 0, a)
                t.setItem(i, 1, QTableWidgetItem(r["folder"]))
                t.setItem(i, 2, QTableWidgetItem("Not recognised. Edit info, or try harder."
                                                 if r["id_status"] == "not_found" else
                                                 "The lookup failed (no internet?). Look up again."))
            self.summary.setText("Songs nothing could recognise. Type their info in (double-click), or look them "
                                 "up again.")
            self.empty.setVisible(not unknown)
            self.empty.setText("Every song has been identified.")
        elif k == "pending":
            t = self.pending
            t.setRowCount(len(pending))
            for i, r in enumerate(pending):
                a = QTableWidgetItem(Path(r["path"]).name)
                a.setData(Qt.UserRole, r["id"])
                t.setItem(i, 0, a)
                t.setItem(i, 1, QTableWidgetItem(r["folder"]))
            self.summary.setText("Songs with no title or artist, waiting to be identified from their sound. "
                                 "Progress is in the title bar.")
            self.empty.setVisible(not pending)
            self.empty.setText("Nothing waiting.")
        else:
            self.summary.setText("Songs that are the same: by file name, by title and artist, or by their sound.")
            self.empty.hide()

    def _selected(self) -> None:
        if self.tabs.value != "ready":
            return
        ids = self._chosen()
        p = next((x for x in self.plan if ids and x["id"] == ids[0]), None)
        if p is None or self.detail.sid != p["id"] or getattr(self, "_shown_plan", None) is not p:
            self._shown_plan = p
            self.detail.show_plan(p)

    def _ticked(self, it: QTableWidgetItem) -> None:
        if it.column() != C_DO:
            return
        sid = it.data(Qt.UserRole)
        (self.skip.discard if it.checkState() == Qt.Checked else self.skip.add)(sid)
        self._save_skip()
        QTimer.singleShot(0, lambda: self._fill(keep=sid))

    def _save_skip(self) -> None:
        get_db().kv_set("tidy_skip", sorted(self.skip))

    def toggle_skip(self, ids: list[int]) -> None:
        for sid in ids:
            (self.skip.discard if sid in self.skip else self.skip.add)(sid)
        self._save_skip()
        self._shown_plan = None
        self._fill(keep=ids[0] if ids else None)

    def _chosen(self) -> list[int]:
        t = self.stack.currentWidget()
        if not isinstance(t, QTableWidget):
            return []
        rows = sorted({i.row() for i in t.selectionModel().selectedRows()})
        return [t.item(r, 0).data(Qt.UserRole) for r in rows if t.item(r, 0) is not None]

    def _set_skip(self, on: bool) -> None:
        for sid in self._chosen():
            (self.skip.add if on else self.skip.discard)(sid)
        self._save_skip()
        self._fill()

    def _edit(self) -> None:
        ids = self._chosen()
        if ids:
            actions.edit_info(self, ids)

    def target_ids(self) -> list[int]:
        return self._chosen()

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---- doing it -------------------------------------------------------------------------------------------
    def apply(self, ids: list[int] | None) -> None:
        """ids None: every ticked song that's shown; otherwise just those."""
        from .playback import get_playback
        cur = get_playback().current_id()
        if ids is None:
            todo = [p for p in self.shown if p["id"] not in self.skip]
        else:
            todo = [p for p in self.plan if p["id"] in set(ids)]
        held = [p for p in todo if p["id"] == cur and (p["move"] or p["rename"])]
        todo = [p for p in todo if p not in held]
        if held:
            hub.toast.emit("The song that's playing waits (Windows can't rename a file that's open). It's still in "
                           "the list for later.", "info")
        if not todo:
            return
        if ids is None:
            moves = sum(1 for p in todo if p["move"])
            renames = sum(1 for p in todo if p["rename"] and not p["move"])
            tagged = sum(1 for p in todo if p["tags"])
            covered = sum(1 for p in todo if p["cover"])
            bits = []
            if moves:
                bits.append(f"{moves:,} moved")
            if renames:
                bits.append(f"{renames:,} renamed")
            if tagged:
                bits.append(f"{tagged:,} with new tags")
            if covered:
                bits.append(f"{covered:,} with cover art added")
            if not dialogs.ask(self, f"Tidy up {len(todo):,} song files?", "; ".join(bits) + ".\n\nThe audio isn't "
                               "touched and nothing is overwritten. Every change goes into the move log (L, then File "
                               "moves), where Undo puts the file back with its old tags. Ctrl+Z undoes the whole "
                               "batch.", f"Apply to {len(todo):,} songs"):
                return
        done_ids = [p["id"] for p in todo]

        def done(res):
            for sid in done_ids:
                self.ov.pop(sid, None)
            self._save_ov()
            hub.movesChanged.emit()
            hub.songsReset.emit()
            hub.foldersChanged.emit()
            if res["failed"]:
                dialogs.info(self, f"{res['failed']} song{'s' if res['failed'] != 1 else ''} couldn't be changed",
                             "\n".join(res["errors"]) + "\n\nThey were left as they were. The log has the details.")
            self.recompute()

        get_queue().add(f"Tidying {len(todo):,} song{'s' if len(todo) != 1 else ''}", tidy.apply, todo,
                        on_event=lambda k, p: hub.songChanged.emit(int(p)) if k == "song_changed" else None,
                        on_done=done, done_text=lambda r: f"{r['done']:,} tidied, {r['failed']} left as they were")
