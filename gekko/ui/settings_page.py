"""Settings: music folders, scanning, playback, lookups, Tidy up, Storage and Reset. Changes apply straight away."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QScrollArea, QVBoxLayout, QWidget)

from ..core.db import get_db
from ..core.paths import drive_ok
from ..core.settings import get_settings
from ..jobs import level, storage
from . import dialogs, workers
from .hub import hub
from .jobqueue import get_queue
from .theme import C
from .widgets import ElidedLabel, button, clear_layout, hline, label, open_in_explorer, page_header, panel


class SettingsPage(QWidget):
    def __init__(self, scan_fn=None):
        super().__init__()
        self.setObjectName("page")
        self.s = get_settings()
        self.queue = get_queue()
        self.scan_fn = scan_fn
        self.sizes: dict[str, tuple[int, int] | None] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)
        head, _ = page_header("Settings", "Changes apply straight away. Where Gekko keeps things is under Storage")
        outer.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transparent")
        self.scroll.setWidget(inner)
        self.root = QVBoxLayout(inner)
        self.root.setContentsMargins(0, 0, 4, 0)
        self.root.setSpacing(8)
        outer.addWidget(self.scroll, 1)
        self.storage_panel: QFrame | None = None
        self._built = False
        hub.foldersChanged.connect(lambda: self.build() if self.isVisible() else setattr(self, "_built", False))

    def showEvent(self, e):
        super().showEvent(e)
        if not self._built:
            self.build()

    def build(self) -> None:
        self._built = True
        clear_layout(self.root)
        from PySide6.QtWidgets import QBoxLayout
        cols = QBoxLayout(QBoxLayout.LeftToRight)
        cols.setSpacing(8)
        self.cols = cols
        left = QVBoxLayout()
        left.setSpacing(8)
        right = QVBoxLayout()
        right.setSpacing(8)
        left.addWidget(self._folders_card())
        left.addWidget(self._playback_card())
        left.addWidget(self._tidy_card())
        left.addStretch(1)
        right.addWidget(self._identify_card())
        self.storage_panel = self._storage_card()
        right.addWidget(self.storage_panel)
        right.addWidget(self._reset_card())
        right.addStretch(1)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        self.root.addLayout(cols)
        self.root.addStretch(1)
        self._fit_columns()
        self._work_out_sizes()

    def _fit_columns(self) -> None:
        if getattr(self, "cols", None) is not None:
            from PySide6.QtWidgets import QBoxLayout
            self.cols.setDirection(QBoxLayout.LeftToRight if self.width() >= 1180 else QBoxLayout.TopToBottom)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._fit_columns()

    def focus_storage(self) -> None:
        if self.storage_panel is not None:
            self.scroll.ensureWidgetVisible(self.storage_panel, 0, 40)

    # ---- helpers -------------------------------------------------------------------
    def _section(self, title: str, sub: str = "") -> tuple[QFrame, QVBoxLayout]:
        c = panel()
        l = QVBoxLayout(c)
        l.setContentsMargins(12, 10, 12, 12)
        l.setSpacing(8)
        h = QHBoxLayout()
        h.setSpacing(10)
        h.addWidget(label(title, "h2"))
        if sub:
            s = ElidedLabel(sub)
            s.setObjectName("faint")
            h.addWidget(s, 1)
        else:
            h.addStretch(1)
        l.addLayout(h)
        return c, l

    def _toggle_row(self, l: QVBoxLayout, text: str, key: str, tip: str = "") -> QCheckBox:
        t = QCheckBox(text)
        t.setChecked(bool(self.s.get(key)))
        t.setToolTip(tip)
        t.toggled.connect(lambda v: self.s.set(key, v))
        l.addWidget(t)
        return t

    def _spin(self, key: str, lo: float, hi: float, step: float, suffix: str, dec: int = 1) -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(dec)
        sp.setSuffix(suffix)
        sp.setValue(float(self.s.get(key)))
        sp.setFixedWidth(96)
        sp.valueChanged.connect(lambda v: self.s.set(key, v))
        return sp

    def _field_row(self, l, name: str) -> QHBoxLayout:
        r = QHBoxLayout()
        r.setSpacing(8)
        lb = label(name, "cap")
        lb.setFixedWidth(120)
        r.addWidget(lb)
        l.addLayout(r)
        return r

    # ---- identification ----------------------------------------------------------------
    def _identify_card(self) -> QFrame:
        c, l = self._section("Lookups", "Runs in the background; what's found shows in the library straight away")
        self._toggle_row(l, "Look songs up automatically after each scan", "identify.auto",
                         "Off: only songs you choose (right-click > Look up again) are looked up")
        self._toggle_row(l, "Identify songs with no title or artist with Shazam (no key needed, about 18 a minute)",
                         "identify.shazam")
        r = self._field_row(l, "AcoustID key")
        key = QLineEdit(self.s.get("identify.acoustid_key", ""))
        key.setPlaceholderText("Optional backup - free from acoustid.org")
        key.editingFinished.connect(lambda: self.s.set("identify.acoustid_key", key.text().strip()))
        r.addWidget(key, 1)
        l.addWidget(label("AcoustID matches whole songs, so it rarely finds short clips. Get a free key: sign in at "
                          "acoustid.org and register an application.", "faint", wrap=True))
        r = self._field_row(l, "AudioTag key")
        atk = QLineEdit(self.s.get("identify.audiotag_key", ""))
        atk.setPlaceholderText("Optional third backup - free from audiotag.info")
        atk.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.at_status = label("", "muted", wrap=True)

        def save_atk():
            self.s.set("identify.audiotag_key", atk.text().strip())
            from ..jobs.identify import reset_audiotag
            reset_audiotag()

        atk.editingFinished.connect(save_atk)
        r.addWidget(atk, 1)
        chk = button("Check", "refresh")
        chk.setToolTip("Check the key works and how much free time is left this month")
        chk.clicked.connect(lambda: (save_atk(), self._check_audiotag()))
        r.addWidget(chk)
        l.addWidget(self.at_status)
        l.addWidget(label("AudioTag gives 3 hours of audio a month free. Gekko sends 15 s per song, so that's about "
                          "700 songs a month. Get a key: sign up at audiotag.info, then API → API keys.", "faint",
                          wrap=True))
        self._toggle_row(l, "Album, genre, year and cover art from iTunes", "identify.itunes")
        self._toggle_row(l, "Genres and years from MusicBrainz", "identify.musicbrainz")
        self._toggle_row(l, "Lyrics from LRCLIB (timed lyrics where there are some)", "identify.lyrics")
        self._toggle_row(l, "Cover art for songs without", "identify.covers")
        self._toggle_row(l, "Artist bios (Wikipedia) and photos (Deezer)", "identify.artists")
        r = self._field_row(l, "Last.fm key")
        lfk = QLineEdit(self.s.get("identify.lastfm_key", ""))
        lfk.setPlaceholderText("Optional - bios for artists Wikipedia doesn't have")
        lfk.editingFinished.connect(lambda: self.s.set("identify.lastfm_key", lfk.text().strip()))
        r.addWidget(lfk, 1)
        l.addWidget(label("Get a free key at last.fm/api/account/create (any app name works).", "faint", wrap=True))
        row = QHBoxLayout()
        retry = button("Retry failed lookups", "refresh")
        retry.clicked.connect(lambda: self._retry("error"))
        row.addWidget(retry)
        retry2 = button("Retry songs not found", "refresh")
        retry2.clicked.connect(lambda: self._retry("not_found"))
        row.addWidget(retry2)
        row.addStretch(1)
        l.addLayout(row)
        return c

    def _check_audiotag(self) -> None:
        key = (self.s.get("identify.audiotag_key") or "").strip()
        if not key:
            self.at_status.setText("Enter a key first.")
            return
        self.at_status.setText("Checking…")
        from ..lookup import services

        def done(st):
            secs = int(st.get("identification_free_sec_remainder") or 0)
            self.at_status.setText(f"Key works. Free time left: {secs // 60} min {secs % 60} s "
                                   f"(about {secs // 15} songs). Lookups so far: {st.get('queries_count', 0)}.")

        workers.run_bg(services.audiotag_stat, key, done=done,
                       failed=lambda msg: self.at_status.setText(f"Couldn't check: {msg}"))

    def _retry(self, status: str) -> None:
        n = get_db().x("UPDATE songs SET id_status='pending', info_status='pending' WHERE id_status=?", (status,))
        hub.songsReset.emit()
        w = self.window()
        if hasattr(w, "identify"):
            w.identify.poke()
        hub.toast.emit(f"Queued {n} song{'s' if n != 1 else ''} for another lookup.", "ok")

    # ---- storage ----------------------------------------------------------------------------
    def _storage_card(self) -> QFrame:
        c, l = self._section("Storage", "Every place Gekko keeps things. Moving copies, checks, switches, then "
                                        "removes the old copy")
        self.size_lbls: dict[str, QLabel] = {}
        for i, (key, what, movable, clearable) in enumerate(storage.PLACES):
            if i:
                l.addWidget(hline())
            p = storage.place_path(key)
            top = QHBoxLayout()
            top.setSpacing(8)
            name = ElidedLabel(what)
            name.setObjectName("h2")
            top.addWidget(name, 1)
            sz = label("Working out…", "muted")
            self.size_lbls[key] = sz
            top.addWidget(sz)
            l.addLayout(top)
            bottom = QHBoxLayout()
            bottom.setSpacing(6)
            path = ElidedLabel(str(p), mode=Qt.ElideMiddle)
            path.setObjectName("mono")
            path.setToolTip(str(p))
            if not drive_ok(p):
                path.setText(f"{p}  ·  this drive isn't connected")
                path.setStyleSheet(f"color: {C['warn']};")
            bottom.addWidget(path, 1)
            op = button("Open", "folder", "ghost")
            op.setToolTip("Open this folder")
            op.clicked.connect(lambda _=False, pp=str(p): open_in_explorer(pp))
            bottom.addWidget(op)
            if movable:
                mv = button("Move…", "move")
                mv.clicked.connect(lambda _=False, k=key: self.move_place(k))
                bottom.addWidget(mv)
            if clearable:
                cl = button("Clear", "trash", "dangerGhost")
                cl.clicked.connect(self.clear_cache)
                bottom.addWidget(cl)
            l.addLayout(bottom)
        return c

    def _work_out_sizes(self) -> None:
        def calc():
            return {k: storage.folder_size(storage.place_path(k)) if drive_ok(storage.place_path(k)) else None
                    for k, *_ in storage.PLACES}

        def show(res):
            self.sizes = res
            for k, v in res.items():
                lbl = self.size_lbls.get(k)
                if lbl is None:
                    continue
                try:
                    lbl.setText("Can't be reached" if v is None else
                                f"{storage.human(v[0])} · {v[1]:,} file{'s' if v[1] != 1 else ''}")
                except RuntimeError:
                    pass

        workers.run_bg(calc, done=show)

    def move_place(self, key: str) -> None:
        if self.queue.busy():
            dialogs.info(self, "Can't move it right now", f"{self.queue.current.name} is running. Moving is only "
                         "possible when nothing else is running, so nothing is half-written.")
            return
        what = dict((k, w) for k, w, *_ in storage.PLACES)[key]
        src = storage.place_path(key)
        d = QFileDialog.getExistingDirectory(self, f"Move {what.split(':')[0].lower()} to…", str(src.parent))
        if not d:
            return
        dst = Path(d) / (src.name if key != "data" else "Gekko data")
        size = self.sizes.get(key)
        sz = f" ({storage.human(size[0])})" if size else ""
        extra = ("\n\nThe database and logs are in use, so Gekko switches to the new place when it restarts, "
                 "then removes the old copy.") if key == "data" else ""
        if not dialogs.ask(self, f"Move {what.split(':')[0].lower()}?",
                           f"From {src}\nTo {dst}{sz}\n\nEverything is copied, then checked file by file. Only then "
                           f"does Gekko switch to the new place and remove the old copy.{extra}", "Move it"):
            return
        w = self.window()

        def done(res):
            if res.get("restart"):
                if dialogs.ask(self, "Copied", "The copy is checked. Restart Gekko now to switch to it?",
                               "Restart Gekko", "Later"):
                    if hasattr(w, "restart"):
                        w.restart()
            else:
                hub.toast.emit(f"Moved to {res['to']}.", "ok")
                if key == "cache":
                    from .covers import get_covers
                    get_covers().clear()
            self.build()

        self.queue.add(f"Moving {what.split(':')[0].lower()}", storage.move_place, key, d,
                       detail=f"To {dst}", on_done=done,
                       on_failed=lambda e: dialogs.info(self, "The move stopped", f"{e}\n\nNothing was switched; "
                                                        "everything is still in its old place."),
                       done_text=lambda r: f"now in {r['to']}")

    def clear_cache(self) -> None:
        size = self.sizes.get("cache")
        sz = storage.human(size[0]) if size else "the space it uses"
        if dialogs.ask(self, "Clear the cache?", f"Covers, artist photos and sound waves are deleted; {sz} comes "
                       "back. Covers inside your song files are copied out again when needed; downloaded covers and "
                       "photos are looked up again.", f"Clear {sz}", danger=True):
            workers.run_bg(storage.clear_cache, done=lambda n: (hub.toast.emit(f"Cleared {storage.human(n)}.", "ok"),
                                                                self._work_out_sizes()))

    # ---- music folders -------------------------------------------------------------------
    def _folders_card(self) -> QFrame:
        c, l = self._section("Music folders", "Any folders, on any drive. Removing one here never touches the disk")
        db = get_db()
        fl = db.folders()
        if not fl:
            l.addWidget(label("No folders yet.", "faint"))
        for f in fl:
            r = QHBoxLayout()
            r.setSpacing(6)
            p = ElidedLabel(f, mode=Qt.ElideMiddle)
            p.setObjectName("mono")
            if not drive_ok(Path(f)) or not Path(f).exists():
                p.setText(f"{f}  ·  can't be reached right now")
                p.setStyleSheet(f"color: {C['warn']};")
            r.addWidget(p, 1)
            op = button("Open", "folder", "ghost")
            op.clicked.connect(lambda _=False, f=f: open_in_explorer(f))
            r.addWidget(op)
            rm = button("Take off the list", "x", "ghost")
            rm.clicked.connect(lambda _=False, f=f: self._remove_folder(f))
            r.addWidget(rm)
            l.addLayout(r)
        r = QHBoxLayout()
        add = button("Add folder…", "plus")
        add.clicked.connect(self._add_folder)
        r.addWidget(add)
        scan = button("Check for new songs now", "refresh")
        scan.clicked.connect(lambda: self.scan_fn(None) if self.scan_fn else None)
        r.addWidget(scan)
        r.addStretch(1)
        l.addLayout(r)
        self._toggle_row(l, "Check the folders for new and removed songs every time Gekko opens",
                         "library.scan_on_start")
        ex = db.excluded()
        if ex:
            l.addWidget(hline())
            l.addWidget(label(f"Folders not added ({len(ex)})", "h2"))
            for f in ex:
                r = QHBoxLayout()
                p = ElidedLabel(f, mode=Qt.ElideMiddle)
                p.setObjectName("mono")
                r.addWidget(p, 1)
                b = button("Add again", "plus", "ghost")
                b.clicked.connect(lambda _=False, f=f: self._unexclude(f))
                r.addWidget(b)
                l.addLayout(r)
        rem = db.removed()
        if rem:
            l.addWidget(hline())
            r = QHBoxLayout()
            r.addWidget(label(f"Songs removed from the library ({len(rem):,})", "h2"))
            r.addStretch(1)
            b = button("Bring them all back", "undo", "ghost")
            b.clicked.connect(self._unremove_all)
            r.addWidget(b)
            l.addLayout(r)
            for row in rem[:8]:
                rr = QHBoxLayout()
                name = f"{row['artist']} - {row['title']}" if row["title"] and row["artist"] else Path(row["path"]).name
                t = ElidedLabel(name)
                t.setToolTip(row["path"])
                rr.addWidget(t, 1)
                b = button("Bring back", "undo", "ghost")
                b.clicked.connect(lambda _=False, p=row["path"]: self._unremove([p]))
                rr.addWidget(b)
                l.addLayout(rr)
            if len(rem) > 8:
                l.addWidget(label(f"…and {len(rem) - 8:,} more.", "faint"))
        return c

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Add a music folder", str(Path.home()))
        if not d:
            return
        if get_db().add_folder(d):
            hub.foldersChanged.emit()
            if self.scan_fn:
                self.scan_fn([d])
        else:
            hub.toast.emit("That folder is already in the list (or inside one that is).", "info")

    def _remove_folder(self, f: str) -> None:
        if dialogs.ask(self, "Take this folder off the list?", f"{f}\n\nIts songs leave the library (their ratings "
                       "and plays are forgotten). Nothing on disk changes.", "Take it off the list", danger=True):
            get_db().remove_folder(f)
            hub.songsReset.emit()
            hub.foldersChanged.emit()

    def _unexclude(self, f: str) -> None:
        get_db().unexclude(f)
        hub.foldersChanged.emit()
        if self.scan_fn:
            self.scan_fn(None)

    def _unremove(self, paths_: list[str]) -> None:
        get_db().unremove(paths_)
        self.build()
        if self.scan_fn:
            self.scan_fn(None)

    def _unremove_all(self) -> None:
        n = len(get_db().removed())
        if dialogs.ask(self, f"Bring back {n:,} removed songs?", "The next scan adds them to the library again (if "
                       "their files are still there).", "Bring them back"):
            self._unremove([r["path"] for r in get_db().removed()])

    # ---- playback ----------------------------------------------------------------------------------
    def _playback_card(self) -> QFrame:
        c, l = self._section("Playback")
        self._toggle_row(l, "No gaps between songs (gapless; takes effect after a restart)", "playback.gapless")
        self._toggle_row(l, "Play every song at a similar volume (the files aren't changed)", "playback.level",
                         "Each song's loudness is measured once in the background; loud songs are turned down and "
                         "quiet ones up while they play.")
        r = self._field_row(l, "Target loudness")
        tgt = self._spin("playback.target", -24, -8, 0.5, " LUFS")
        tgt.setToolTip("-14 LUFS is what most music apps play at. Lower is quieter.")
        r.addWidget(tgt)
        r.addStretch(1)
        n = len(level.to_measure())
        r = QHBoxLayout()
        r.addWidget(label(f"{n:,} songs still to be measured (in the background, after lookups)." if n else
                          "Every song has been measured.", "faint"))
        r.addStretch(1)
        l.addLayout(r)
        self._toggle_row(l, "Pick up where I left off (the queue and the song) when Gekko opens", "playback.resume")
        return c

    def _tidy_card(self) -> QFrame:
        c, l = self._section("Tidy up", "Defaults; you review every change on the Tidy up screen first")
        r = self._field_row(l, "Sort into")
        root = QLineEdit(self.s.get("library.sort_root") or "E:\\Music")
        root.editingFinished.connect(lambda: self.s.set("library.sort_root", root.text().strip() or "E:\\Music"))
        r.addWidget(root, 1)
        br = button("Browse…", "folder")

        def pick():
            d = QFileDialog.getExistingDirectory(self, "Sort songs into…", root.text())
            if d:
                root.setText(str(Path(d)))
                self.s.set("library.sort_root", str(Path(d)))

        br.clicked.connect(pick)
        r.addWidget(br)
        self._toggle_row(l, "Rename files to “Artist - Title”", "tidy.rename")
        self._toggle_row(l, "Write the info into the files' tags", "tidy.tags")
        self._toggle_row(l, "Add cover art to files without", "tidy.cover")
        self._toggle_row(l, "Sort into Artist\\Album folders", "tidy.sort")
        return c

    # ---- resets ---------------------------------------------------------------------------------
    def _reset_card(self) -> QFrame:
        c, l = self._section("Reset", "One part at a time; each asks first and says what stays")
        db = get_db()
        nm = len(db.moves(100000))
        nskip = len(db.excluded()) + len(db.removed())
        rows = [
            ("Settings", "Back to the defaults (folders, the look and themes stay)", self._reset_settings),
            ("Library", "Forget songs, ratings, plays and playlists; the song files and folders stay", self._reset_library),
            (f"Skips ({nskip})", "Folders not added and songs removed can come back with the next scan", self._reset_skips),
            ("Artist profiles", "Bios and photos are looked up again", self._reset_artists),
            ("Lookups", "Look every song up again (lyrics, covers, genres)", self._reset_lookups),
            (f"Move log ({nm})", "Forget the list of renames and moves (Undo stops working for them)", self._reset_moves),
            ("Cache", "Covers, photos and sound waves; made again when needed", self.clear_cache),
            ("Logs", "Empty the log files", self._clear_logs),
        ]
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(6)
        for i, (t, why, fn) in enumerate(rows):
            b = button(t, "refresh", "dangerGhost")
            b.clicked.connect(fn)
            g.addWidget(b, i, 0)
            g.addWidget(label(why, "faint"), i, 1)
        g.setColumnStretch(1, 1)
        l.addLayout(g)
        return c

    def _reset_settings(self) -> None:
        if dialogs.ask(self, "Reset settings?", "Every setting goes back to its default. Your music folders, the "
                       "look, saved themes, the library and your files stay.", "Reset settings", danger=True):
            self.s.reset()
            self.build()

    def _reset_library(self) -> None:
        if dialogs.ask(self, "Reset the library?", "Every song's ratings, favourites, plays and looked-up info, and "
                       "your playlists, are forgotten. Your music folders stay, the files aren't touched, and the next "
                       "scan reads the songs in again.", "Reset the library", danger=True):
            w = self.window()
            if hasattr(w, "pb"):
                w.pb.clear()
            get_db().reset_library()
            hub.songsReset.emit()
            hub.playlistsChanged.emit()
            if self.scan_fn:
                self.scan_fn(None)

    def _reset_skips(self) -> None:
        if dialogs.ask(self, "Forget the skips?", "Folders you chose not to add and songs you removed from the library "
                       "are added again by the next scan.", "Forget them", danger=True):
            db = get_db()
            for f in db.excluded():
                db.unexclude(f)
            db.unremove(None)
            self.build()
            if self.scan_fn:
                self.scan_fn(None)

    def _reset_artists(self) -> None:
        if dialogs.ask(self, "Look every artist up again?", "Bios and photos are forgotten and looked up again in the "
                       "background.", "Look them up again", danger=True):
            get_db().reset_artists()
            w = self.window()
            if hasattr(w, "identify"):
                w.identify.poke()

    def _reset_lookups(self) -> None:
        if dialogs.ask(self, "Look every song up again?", "Lyrics, covers, genres and years are looked up again for "
                       "every song (songs you identified by hand keep their names). It runs in the background.",
                       "Look them up again", danger=True):
            get_db().x("UPDATE songs SET info_status='pending'")
            get_db().x("UPDATE songs SET id_status='pending' WHERE id_status IN ('not_found', 'error')")
            w = self.window()
            if hasattr(w, "identify"):
                w.identify.poke()

    def _reset_moves(self) -> None:
        if dialogs.ask(self, "Forget the move log?", "The list of renames and moves is cleared, so they can't be "
                       "undone any more. The files stay as they are.", "Forget the move log", danger=True):
            get_db().clear_moves()
            hub.movesChanged.emit()
            self.build()

    def _clear_logs(self) -> None:
        if dialogs.ask(self, "Empty the logs?", "The log files list what Gekko did. Crash logs are emptied too.",
                       "Empty the logs", danger=True):
            from ..core import log as _log
            _log.clear()
            w = self.window()
            if hasattr(w, "drawer"):
                w.drawer.log.clear()
            hub.toast.emit("Logs cleared.", "ok")

