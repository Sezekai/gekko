"""Tidy up: review what Gekko would do to your song files, change single songs, then apply it.

Nothing changes on disk until Apply. Each song can be left out; double-click one to edit its info first. Songs
that couldn't be identified are listed separately, with Edit info and Look up again."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.db import get_db, key
from ..core.settings import get_settings
from ..jobs import tidy
from . import actions, dialogs, workers
from .hub import hub
from .jobqueue import get_queue
from .library import get_library
from .theme import C
from .widgets import ElidedLabel, Segmented, button, label, page_header, panel


def _rel(p: str, root: str) -> str:
    try:
        return str(Path(p).relative_to(root))
    except ValueError:
        return p


class TidyPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.s = get_settings()
        self.plan: list[dict] = []
        self.skip: set[int] = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        head, _ = page_header("Tidy up", "Write looked-up info into your files: names, tags, cover art and "
                                         "Artist\\Album folders. Nothing changes until you apply it")
        lay.addLayout(head)
        tb = panel()
        tl = QVBoxLayout(tb)
        tl.setContentsMargins(10, 8, 10, 8)
        tl.setSpacing(6)
        r1 = QHBoxLayout()
        r1.setSpacing(12)
        self.o_rename = self._opt(r1, "Rename files to “Artist - Title”", "tidy.rename")
        self.o_tags = self._opt(r1, "Write the info into the tags", "tidy.tags")
        self.o_cover = self._opt(r1, "Add cover art to files without", "tidy.cover")
        r1.addStretch(1)
        self.refresh_btn = button("Check again", "refresh", "ghost")
        self.refresh_btn.clicked.connect(self.recompute)
        r1.addWidget(self.refresh_btn)
        self.apply_btn = button("Apply", "check", "primary")
        self.apply_btn.clicked.connect(self.apply)
        r1.addWidget(self.apply_btn)
        tl.addLayout(r1)
        r2 = QHBoxLayout()
        r2.setSpacing(8)
        self.o_sort = self._opt(r2, "Sort into folders", "tidy.sort")
        self.root = QLineEdit(self.s.get("library.sort_root") or "E:\\Music")
        self.root.setMinimumWidth(240)
        self.root.editingFinished.connect(self._root_typed)
        r2.addWidget(self.root, 1)
        br = button("Browse…", "folder")
        br.clicked.connect(self._browse)
        r2.addWidget(br)
        r2.addWidget(label("\\Artist\\Album\\  (songs with no album go in Artist\\Singles)", "faint"))
        r2.addStretch(1)
        tl.addLayout(r2)
        lay.addWidget(tb)
        sumrow = QHBoxLayout()
        self.tabs = Segmented([("ready", "Ready"), ("unknown", "Not identified"), ("pending", "Being looked up")],
                              "ready")
        self.tabs.changed.connect(self._tab)
        sumrow.addWidget(self.tabs)
        self.summary = ElidedLabel("")
        self.summary.setObjectName("faint")
        sumrow.addWidget(self.summary, 1)
        self.skip_btn = button("Leave out chosen", "minus", "ghost")
        self.skip_btn.clicked.connect(lambda: self._set_skip(True))
        sumrow.addWidget(self.skip_btn)
        self.inc_btn = button("Put back chosen", "plus", "ghost")
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
        self.stack = QStackedWidget()
        self.table = self._table(["Do", "Song", "Now", "New name", "Moves to", "Changes"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.itemChanged.connect(self._ticked)
        self.stack.addWidget(self.table)
        self.unknown = self._table(["File", "Folder", "What happened"])
        self.stack.addWidget(self.unknown)
        self.pending = self._table(["File", "Folder"])
        self.stack.addWidget(self.pending)
        lay.addWidget(self.stack, 1)
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

    def _table(self, cols: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(cols))
        t.setHorizontalHeaderLabels(cols)
        t.verticalHeader().hide()
        t.verticalHeader().setDefaultSectionSize(max(26, self.s.get("ui.rh", 26)))
        t.setShowGrid(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        t.setWordWrap(False)
        hh = t.horizontalHeader()
        for i in range(len(cols)):
            hh.setSectionResizeMode(i, QHeaderView.Stretch)
        t.doubleClicked.connect(lambda _i: self._edit())
        return t

    def _opt(self, row: QHBoxLayout, text: str, k: str) -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(bool(self.s.get(k, True)))
        c.toggled.connect(lambda v: (self.s.set(k, v), self.recompute()))
        row.addWidget(c)
        return c

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

    def _lib_changed(self) -> None:
        self._dirty = True
        if self.isVisible():
            self._timer.start()
        elif not self._timer.isActive():
            self._timer.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.recompute()

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

        def done(plan):
            self._busy = False
            self.plan = plan or []
            hub.tidyCount.emit(len(self.plan))
            if self.isVisible():
                self._fill()

        def failed(msg):
            self._busy = False
            self.summary.setText(f"Couldn't work it out: {msg}")

        workers.run_bg(tidy.plans, None, opt, done=done, failed=failed)

    def _tab(self, k: str) -> None:
        self.stack.setCurrentIndex({"ready": 0, "unknown": 1, "pending": 2}[k])
        ready = k == "ready"
        self.skip_btn.setVisible(ready)
        self.inc_btn.setVisible(ready)
        self.again_btn.setVisible(not ready)
        self.harder_btn.setVisible(k == "unknown")
        self._fill()

    def _fill(self) -> None:
        db = get_db()
        k = self.tabs.value
        lib = get_library()
        unknown = [r for r in lib.rows if r["id_status"] in ("not_found", "error") and not r["title"]
                   and not r["missing"]]
        pending = [r for r in lib.rows if r["id_status"] == "pending" and not r["missing"]]
        n_do = len([p for p in self.plan if p["id"] not in self.skip])
        self.tabs.set_count("ready", len(self.plan))
        self.tabs.set_count("unknown", len(unknown))
        self.tabs.set_count("pending", len(pending))
        self.apply_btn.setText(f"Apply to {n_do:,} song{'s' if n_do != 1 else ''}")
        self.apply_btn.setEnabled(n_do > 0)
        root = self.root.text()
        if k == "ready":
            t = self.table
            t.blockSignals(True)
            t.setRowCount(len(self.plan))
            for i, p in enumerate(self.plan):
                r = lib.get(p["id"])
                inc = QTableWidgetItem()
                inc.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                inc.setCheckState(Qt.Unchecked if p["id"] in self.skip else Qt.Checked)
                inc.setData(Qt.UserRole, p["id"])
                t.setItem(i, 0, inc)
                song = f"{r['artist']} - {r['name']}" if r and r["artist"] else (r["name"] if r else "")
                t.setItem(i, 1, QTableWidgetItem(song))
                now = QTableWidgetItem(Path(p["src"]).name)
                now.setToolTip(p["src"])
                t.setItem(i, 2, now)
                b = QTableWidgetItem(Path(p["dst"]).name if p["rename"] else "(same name)")
                b.setToolTip(p["dst"])
                t.setItem(i, 3, b)
                mv = QTableWidgetItem(_rel(str(Path(p["dst"]).parent), root) if p["move"] else "(stays)")
                mv.setToolTip(str(Path(p["dst"]).parent))
                t.setItem(i, 4, mv)
                ch = []
                if p["tags"]:
                    ch.append("tags: " + ", ".join(p["tags"]))
                if p["cover"]:
                    ch.append("cover art")
                c = QTableWidgetItem("; ".join(ch) or "–")
                c.setToolTip("\n".join(f"{k}: {v}" for k, v in p["tags"].items()) or "")
                t.setItem(i, 5, c)
                if p["id"] in self.skip:
                    for j in range(1, 6):
                        t.item(i, j).setForeground(Qt.gray)
            t.blockSignals(False)
            self.summary.setText(f"{n_do:,} of {len(self.plan):,} songs would change. The audio is never touched, "
                                 "nothing is overwritten, and every change can be undone (Log > File moves).")
            self.empty.setVisible(not self.plan)
            self.empty.setText("Everything is tidy: every song's file already matches its info." if not self._busy
                               else "Working it out…")
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
        else:
            t = self.pending
            t.setRowCount(len(pending))
            for i, r in enumerate(pending):
                a = QTableWidgetItem(Path(r["path"]).name)
                a.setData(Qt.UserRole, r["id"])
                t.setItem(i, 0, a)
                t.setItem(i, 1, QTableWidgetItem(r["folder"]))
            self.summary.setText("Songs with no title or artist, waiting to be identified from their sound "
                                 "(about 18 a minute). Progress is in the title bar.")
            self.empty.setVisible(not pending)
            self.empty.setText("Nothing waiting.")

    def _ticked(self, it: QTableWidgetItem) -> None:
        if it.column() != 0:
            return
        sid = it.data(Qt.UserRole)
        if it.checkState() == Qt.Checked:
            self.skip.discard(sid)
        else:
            self.skip.add(sid)
        self._fill()

    def _chosen(self) -> list[int]:
        t = self.stack.currentWidget()
        rows = sorted({i.row() for i in t.selectionModel().selectedRows()})
        return [t.item(r, 0).data(Qt.UserRole) for r in rows if t.item(r, 0) is not None]

    def _set_skip(self, on: bool) -> None:
        for sid in self._chosen():
            (self.skip.add if on else self.skip.discard)(sid)
        self._fill()

    def _edit(self) -> None:
        ids = self._chosen()
        if ids:
            actions.edit_info(self, ids)

    def target_ids(self) -> list[int]:
        return self._chosen()

    def apply(self) -> None:
        from .playback import get_playback
        cur = get_playback().current_id()
        todo = [p for p in self.plan if p["id"] not in self.skip]
        held = [p for p in todo if p["id"] == cur and (p["move"] or p["rename"])]
        todo = [p for p in todo if p not in held]
        if held:
            hub.toast.emit("The song that's playing is left for now (Windows can't rename a file that's open). "
                           "It's still in the list for next time.", "info")
        if not todo:
            return
        moves = sum(1 for p in todo if p["move"])
        renames = sum(1 for p in todo if p["rename"] and not p["move"])
        tagged = sum(1 for p in todo if p["tags"] or p["cover"])
        bits = []
        if moves:
            bits.append(f"{moves:,} moved into {self.root.text()}")
        if renames:
            bits.append(f"{renames:,} renamed")
        if tagged:
            bits.append(f"{tagged:,} with new tags")
        if not dialogs.ask(self, f"Tidy up {len(todo):,} song files?", "; ".join(bits) + ".\n\nThe audio isn't "
                           "touched and nothing is overwritten. Every change goes into the move log (L, then File "
                           "moves), where Undo puts the file back with its old tags. Ctrl+Z undoes the whole batch.",
                           f"Apply to {len(todo):,} songs"):
            return
        def done(res):
            self.skip.clear()
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
