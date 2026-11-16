"""Duplicates: songs that are the same by file name, by title and artist, or by their sound. Each group shows which
copy would be kept (lossless or the best bitrate, then rating, plays and cover art); the rest can go to the Recycle
Bin in one go, or pick them yourself."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..core.db import get_db
from ..jobs import duplicates as dup
from . import actions
from .jobqueue import get_queue
from .library import get_library
from .listlook import remember_columns, style_list
from .playback import get_playback
from .theme import C
from .widgets import ElidedLabel, Segmented, button, fmt_dur, label

COLS = ["File", "Folder", "Length", "Quality", "Rating", "Plays"]


class DuplicatesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.groups: list[list[int]] = []
        self.keep: dict[int, int] = {}          # group index -> song kept
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.mode = Segmented(dup.MODES, get_db().kv_get("dupes_mode") or "name")
        self.mode.changed.connect(self._mode)
        top.addWidget(self.mode)
        self.find_btn = button("Listen for duplicates", "search")
        self.find_btn.setToolTip("Fingerprints every song (the first two minutes) and compares them. The first time "
                                 "takes a while; after that only new or changed songs are listened to.")
        self.find_btn.clicked.connect(self.find_sound)
        top.addWidget(self.find_btn)
        self.info = ElidedLabel("")
        self.info.setObjectName("faint")
        top.addWidget(self.info, 1)
        self.chosen_btn = button("Recycle chosen", "trash", "ghost")
        self.chosen_btn.setToolTip("Send the chosen copies to the Recycle Bin")
        self.chosen_btn.clicked.connect(lambda: self._recycle(self._chosen()))
        top.addWidget(self.chosen_btn)
        self.best_btn = button("Keep the best, recycle the rest", "check", "primary")
        self.best_btn.setToolTip("In every group, keep the copy marked “keep” and send the others to the Recycle Bin")
        self.best_btn.clicked.connect(lambda: self._recycle(self._extras()))
        top.addWidget(self.best_btn)
        lay.addLayout(top)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemDoubleClicked.connect(self._play)
        for i, w in enumerate((320, 320, 70, 110, 70, 60)):
            self.tree.setColumnWidth(i, w)
        style_list(self.tree)
        remember_columns(self.tree.header(), "dupes")
        lay.addWidget(self.tree, 1)
        self.empty = label("", "faint", wrap=True)
        self.empty.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty)
        get_library().changed.connect(self._refresh)
        self._mode(self.mode.value)

    # ---- working out the groups ------------------------------------------------------------------------------
    def _mode(self, m: str) -> None:
        get_db().kv_set("dupes_mode", m)
        self.find_btn.setVisible(m == "sound")
        self._refresh()

    def _refresh(self, *_a) -> None:
        if not self.isVisible() and self.groups:
            return
        rows = [r for r in get_library().rows if not r["missing"]]
        m = self.mode.value
        if m == "name":
            self.groups = dup.by_name(rows)
            self.info.setText("File names that match once track numbers, “(2)” and “- Copy” are left off.")
        elif m == "tags":
            self.groups = dup.by_tags(rows)
            self.info.setText("Songs with the same title and main artist.")
        else:
            saved = get_db().kv_get("dupes_sound") or {}
            alive = {r["id"] for r in rows}
            self.groups = [[i for i in g if i in alive] for g in saved.get("groups", [])]
            self.groups = [g for g in self.groups if len(g) > 1]
            when = saved.get("at")
            self.info.setText(("Last listened " + time.strftime("%d %b %Y, %H:%M", time.localtime(when))
                               + f" ({saved.get('checked', 0):,} songs).") if when else
                              "Listens to every song to find the same recording under different names or tags.")
        self._fill()

    def showEvent(self, e):
        super().showEvent(e)
        self._refresh()

    def find_sound(self) -> None:
        rows = [dict(r) for r in get_library().rows if not r["missing"]]
        self.find_btn.setEnabled(False)

        def done(res):
            get_db().kv_set("dupes_sound", {"groups": res["groups"], "at": time.time(), "checked": res["checked"]})
            self.find_btn.setEnabled(True)
            if self.mode.value == "sound":
                self._refresh()

        get_queue().add("Listening for duplicates", dup.by_sound, rows, pausable=True, on_done=done,
                        on_finished=lambda: self.find_btn.setEnabled(True),
                        done_text=lambda r: f"{len(r['groups']):,} group{'s' if len(r['groups']) != 1 else ''} of "
                                            f"the same song in {r['checked']:,} songs")

    # ---- showing them ------------------------------------------------------------------------------------------
    def _fill(self) -> None:
        lib = get_library()
        self.tree.clear()
        self.keep = {}
        bold = QFont(self.tree.font())
        bold.setBold(True)
        n_extra = 0
        for gi, ids in enumerate(sorted(self.groups, key=lambda g: (lib.get(g[0]) or {}).get("name", "").casefold())):
            rows = [r for r in lib.rows_for(ids)]
            if len(rows) < 2:
                continue
            keep = dup.best_of(rows)
            self.keep[gi] = keep
            n_extra += len(rows) - 1
            first = next(r for r in rows if r["id"] == keep)
            head = QTreeWidgetItem([f"{first['artist'] + ' - ' if first['artist'] else ''}{first['name']}"
                                    f"   ·   {len(rows)} copies"])
            head.setFont(0, bold)
            head.setFlags(Qt.ItemIsEnabled)
            head.setData(0, Qt.UserRole, None)
            self.tree.addTopLevelItem(head)
            head.setFirstColumnSpanned(True)
            for r in sorted(rows, key=lambda r: r["id"] != keep):
                q = []
                if r.get("codec"):
                    q.append(r["codec"].upper())
                if r.get("bitrate"):
                    q.append(f"{int(r['bitrate']) // 1000 if r['bitrate'] > 5000 else r['bitrate']} kbps")
                it = QTreeWidgetItem([Path(r["path"]).name + ("   ✓ keep" if r["id"] == keep else ""),
                                      str(Path(r["path"]).parent), fmt_dur(r["duration"] or 0), " ".join(q),
                                      f"{(r['rating'] or 0) / 2:g}★" if r["rating"] else "–", str(r["plays"] or 0)])
                it.setData(0, Qt.UserRole, r["id"])
                it.setToolTip(0, r["path"])
                it.setToolTip(1, str(Path(r["path"]).parent))
                if r["id"] == keep:
                    it.setForeground(0, QColor(C["accent"]))
                head.addChild(it)
            head.setExpanded(True)
        shown = self.tree.topLevelItemCount()
        self.best_btn.setText(f"Keep the best, recycle {n_extra:,}" if n_extra else "Keep the best, recycle the rest")
        self.best_btn.setEnabled(bool(n_extra))
        self.empty.setVisible(not shown)
        if self.mode.value == "sound" and not get_db().kv_get("dupes_sound"):
            self.empty.setText("Press “Listen for duplicates” to compare how your songs sound.")
        else:
            self.empty.setText("No duplicates found this way.")

    def _chosen(self) -> list[int]:
        return [i for i in (it.data(0, Qt.UserRole) for it in self.tree.selectedItems()) if i is not None]

    def _extras(self) -> list[int]:
        out = []
        for gi in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(gi)
            for c in range(head.childCount()):
                sid = head.child(c).data(0, Qt.UserRole)
                if sid not in self.keep.values():
                    out.append(sid)
        return out

    def _recycle(self, ids: list[int]) -> None:
        if ids:
            actions.recycle(self, ids)

    def _play(self, it: QTreeWidgetItem, _col: int) -> None:
        sid = it.data(0, Qt.UserRole)
        if sid is not None:
            get_playback().play_ids([sid], context="Duplicates")

    def _menu(self, pos) -> None:
        ids = self._chosen()
        if not ids:
            return
        m = actions.song_menu(self, ids) or QMenu(self)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def target_ids(self) -> list[int]:
        return self._chosen()
