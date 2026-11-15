"""What you can do with songs, shared by every list, the side panel and the full-screen player: play, queue, rate,
favourite, add to playlists, edit info, look up again, show in Explorer, remove or send to the Recycle Bin."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QComboBox, QGridLayout, QLineEdit, QMenu, QSpinBox, QWidget

from ..core.db import get_db, main_artist
from ..jobs import tidy
from . import dialogs
from .hub import hub
from .jobqueue import get_queue
from .library import display_title, get_library
from .playback import get_playback
from .theme import CATEGORY_COLOURS, C, icon
from .widgets import open_in_explorer


def _ids(rows) -> list[int]:
    return [r["id"] for r in rows]


def toggle_favourite(ids: list[int]) -> None:
    rows = get_db().songs_by_ids(list(ids))      # the database, not the list's copy: quick presses stay right
    if not rows:
        return
    on = not all(r["favourite"] for r in rows)
    get_db().set_favourite(ids, on)
    for i in ids:
        hub.songChanged.emit(i)
    if len(ids) > 1:
        hub.toast.emit(f"{len(ids):,} songs {'added to' if on else 'taken out of'} Favourites.", "ok")


def set_rating(ids: list[int], rating: int) -> None:
    get_db().set_rating(ids, rating)
    for i in ids:
        hub.songChanged.emit(i)


def nudge_rating(ids: list[int], delta: int) -> None:
    db = get_db()
    v = None
    for r in db.songs_by_ids(list(ids)):
        nv = max(0, min(10, (r["rating"] or 0) + delta))
        db.set_rating([r["id"]], nv)
        hub.songChanged.emit(r["id"])
        v = nv if v is None else v
    if v is not None:
        hub.toast.emit(f"Rated {v / 2:g} star{'s' if v != 2 else ''}" if v else "Rating cleared", "ok")


def go_artist(r) -> None:
    name = main_artist(r["artist"]) if r and r["artist"] else None
    if name:
        hub.goto.emit("artist", name)


def go_album(r) -> None:
    if r and r["album"]:
        a = get_library().album_of(r)
        if a is not None:
            hub.goto.emit("album", a["key"])


def remove_from_library(parent: QWidget, ids: list[int]) -> None:
    lib = get_library()
    rows = lib.rows_for(ids)
    if not rows:
        return
    what = f"“{display_title(rows[0])}”" if len(rows) == 1 else f"{len(rows):,} songs"
    if not dialogs.ask(parent, f"Remove {what} from the library?",
                       "They leave Gekko and won't be added again by later scans (Settings > Music folders can bring "
                       "them back). The files stay where they are on disk.", "Remove from library", danger=True):
        return
    get_db().remove_songs(ids)
    lib.remove(ids)
    pb = get_playback()
    pb.remove_at([i for i, sid in enumerate(pb.queue) if sid in set(ids) and i != pb.index])
    hub.toast.emit(f"Removed {what} from the library.", "ok")


def recycle(parent: QWidget, ids: list[int]) -> None:
    lib = get_library()
    rows = lib.rows_for(ids)
    if not rows:
        return
    if len(rows) == 1:
        title = f"Send “{Path(rows[0]['path']).name}” to the Recycle Bin?"
    else:
        title = f"Send {len(rows):,} song files to the Recycle Bin?"
    if not dialogs.ask(parent, title, "The files go to the Recycle Bin (nothing is deleted for good) and leave the "
                       "library. You can put them back from the Recycle Bin; the next scan then adds them again.",
                       "Send to Recycle Bin", danger=True):
        return
    pb = get_playback()
    if pb.current_id() in set(ids):
        pb.next() if len(pb.queue) > 1 else pb.clear()

    def done(res):
        lib.remove(ids)
        if res["failed"]:
            dialogs.info(parent, "Some files stayed", f"{res['failed']} couldn't go to the Recycle Bin (maybe on a "
                         "network drive, or open in another program). They weren't deleted; they're still in the "
                         "library.")
            hub.songsReset.emit()

    get_queue().add(f"Sending {len(ids):,} file{'s' if len(ids) != 1 else ''} to the Recycle Bin", tidy.recycle,
                    list(ids), on_done=done, done_text=lambda r: f"{r['done']} sent to the Recycle Bin")


def identify_again(ids: list[int], deep: bool = False) -> None:
    w = _window()
    db = get_db()
    for i in ids:
        db.update_song(i, id_status="pending" if not deep else "error", info_status="pending")
        if w is not None and hasattr(w, "identify"):
            w.identify.first("deep" if deep else "song", i)
    hub.toast.emit(f"Looking up {len(ids):,} song{'s' if len(ids) != 1 else ''} again"
                   + (" (slower and more thorough)" if deep else "") + ".", "info")
    for i in ids:
        hub.songChanged.emit(i)


def _window():
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if w.objectName() == "mainWindow":
            return w
    return None


# ---- playlists ---------------------------------------------------------------------------------------
def new_playlist(parent: QWidget, ids: list[int] | None = None) -> int | None:
    name = dialogs.ask_text(parent, "New playlist", "What should it be called?", "", "Make the playlist",
                            "My playlist")
    if not name:
        return None
    n = len(get_db().playlists())
    pid = get_db().add_playlist(name, CATEGORY_COLOURS[n % len(CATEGORY_COLOURS)])
    if ids:
        get_db().add_to_playlist(pid, ids)
        hub.toast.emit(f"Made “{name}” with {len(ids):,} song{'s' if len(ids) != 1 else ''}.", "ok")
    hub.playlistsChanged.emit()
    return pid


def add_to_playlist(pid: int, ids: list[int]) -> None:
    db = get_db()
    p = db.playlist(pid)
    if p is None:
        return
    have = set(db.playlist_song_ids(pid))
    new = [i for i in ids if i not in have]
    if not new:
        hub.toast.emit(f"Already in “{p['name']}”.", "info")
        return
    db.add_to_playlist(pid, new)
    skipped = len(ids) - len(new)
    hub.toast.emit(f"Added {len(new):,} to “{p['name']}”" + (f" ({skipped} already there)" if skipped else "") + ".",
                   "ok")
    hub.playlistsChanged.emit()


def playlist_menu(parent: QWidget, ids_fn) -> QMenu:
    m = QMenu("Add to playlist", parent)
    m.setIcon(icon("playlist", C["text"], 16))

    def fill():
        m.clear()
        a = m.addAction(icon("plus", C["text"], 16), "New playlist…")
        a.triggered.connect(lambda: new_playlist(parent, ids_fn()))
        pls = [p for p in get_db().playlists() if not p["rules"]]
        if pls:
            m.addSeparator()
        for p in pls:
            act = m.addAction(p["name"])
            act.setIcon(_dot(p["color"]))
            act.triggered.connect(lambda _=False, pid=p["id"]: add_to_playlist(pid, ids_fn()))

    m.aboutToShow.connect(fill)
    return m


def _dot(col: str | None):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    pm = QPixmap(24, 24)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(col or C["acc"]))
    p.setPen(Qt.NoPen)
    p.drawEllipse(3, 3, 6, 6)
    p.end()
    return QIcon(pm)


# ---- edit info ---------------------------------------------------------------------------------------
def edit_info(parent: QWidget, ids: list[int]) -> None:
    lib = get_library()
    rows = lib.rows_for(ids)
    if not rows:
        return
    many = len(rows) > 1
    d = dialogs.Dialog(parent, f"Edit {len(rows):,} songs" if many else "Edit song info",
                       ("Fields you change apply to every chosen song; empty ones stay as they are. " if many else "")
                       + "The library changes now; the files change when you apply Tidy up.", 520)
    g = QGridLayout()
    g.setHorizontalSpacing(10)
    g.setVerticalSpacing(7)
    fields = {}
    names = [("title", "Title"), ("artist", "Artist"), ("album", "Album"), ("album_artist", "Album artist"),
             ("genre", "Genre"), ("year", "Year"), ("track", "Track"), ("disc", "Disc")]
    if many:
        names = [n for n in names if n[0] not in ("title", "track")]
    from .widgets import label
    for i, (k, t) in enumerate(names):
        lb = label(t, "cap")
        lb.setFixedWidth(88)
        g.addWidget(lb, i, 0)
        if k in ("track", "disc"):
            w = QSpinBox()
            w.setRange(0, 999)
            w.setSpecialValueText("–")
            w.setFixedWidth(80)
            if not many:
                w.setValue(int(rows[0][k] or 0))
        else:
            w = QLineEdit()
            vals = {str(r[k] or "") for r in rows}
            if len(vals) == 1:
                w.setText(vals.pop())
            else:
                w.setPlaceholderText("Mixed: leave empty to keep each song's own")
        g.addWidget(w, i, 1)
        fields[k] = w
    d.body.addLayout(g)
    d.add_button("Cancel", "cancel")
    d.add_button("Save", "primary")
    if not d.exec():
        return
    out = {}
    for k, w in fields.items():
        if isinstance(w, QSpinBox):
            v = w.value() or None
            if many and v is None:
                continue
            out[k] = v
        else:
            v = w.text().strip()
            if many and not v:
                continue
            if not many or v:
                out[k] = v or None
    if not many:
        r = rows[0]
        out = {k: v for k, v in out.items() if (v or None) != (r[k] or None)}
    if not out:
        return
    tidy.edit_info(ids, out)
    for i in ids:
        hub.songChanged.emit(i)
    hub.toast.emit("Saved. Tidy up writes it into the files when you're ready.", "ok")


# ---- the menu -----------------------------------------------------------------------------------------
def song_menu(parent: QWidget, ids: list[int], context_rows=None, playlist_id: int | None = None,
              queue_idx: list[int] | None = None) -> QMenu:
    """Right-click menu for chosen songs. context_rows: the whole list shown (Play plays it from the song)."""
    lib = get_library()
    pb = get_playback()
    rows = lib.rows_for(ids)
    m = QMenu(parent)
    if not rows:
        return m
    first = rows[0]

    def act(text, ic, fn, enabled=True, shortcut: str = ""):
        t = text + (f"\t{shortcut}" if shortcut else "")
        a = QAction(icon(ic, C["text"], 16), t, m) if ic else QAction(t, m)
        a.triggered.connect(fn)
        a.setEnabled(enabled)
        m.addAction(a)
        return a

    if context_rows is not None and len(ids) == 1:
        act("Play", "play", lambda: pb.play_rows(context_rows, ids[0], "in_order", ""), shortcut="Enter")
    else:
        act("Play", "play", lambda: pb.play_ids(ids), shortcut="Enter")
    act("Play next", "next", lambda: pb.play_next(ids), shortcut="N")
    act("Add to queue", "queue", lambda: pb.add(ids), shortcut="Q")
    if queue_idx:
        act("Take out of the queue", "x", lambda: pb.remove_at(queue_idx), shortcut="Delete")
    m.addMenu(playlist_menu(m, lambda: list(ids)))
    if playlist_id is not None:
        def drop():
            db = get_db()
            cur = db.playlist_song_ids(playlist_id)
            gone = set(ids)
            db.set_playlist_songs(playlist_id, [i for i in cur if i not in gone])
            hub.playlistsChanged.emit()
        act("Take out of this playlist", "minus", drop)
    m.addSeparator()
    fav = all(r["favourite"] for r in rows)
    act("Unfavourite" if fav else "Favourite", "heart", lambda: toggle_favourite(ids), shortcut="V")
    rm = m.addMenu(icon("star", C["text"], 16), "Rate")
    for v in range(10, -1, -1):
        a = rm.addAction(f"{v / 2:g} star{'s' if v != 2 else ''}" if v else "No rating")
        a.triggered.connect(lambda _=False, v=v: set_rating(ids, v))
    m.addSeparator()
    if len(rows) == 1:
        act("Go to artist", "user", lambda: go_artist(first), bool(first["artist"]))
        act("Go to album", "disc", lambda: go_album(first), bool(first["album"]))
    act("Edit info…", "edit", lambda: edit_info(parent, ids), shortcut="E")
    lm = m.addMenu(icon("search", C["text"], 16), "Look up again")
    lm.addAction("Identify from the sound").triggered.connect(lambda: identify_again(ids))
    lm.addAction("Try harder (sped-up or slowed-down edits)").triggered.connect(lambda: identify_again(ids, True))
    if len(rows) == 1:
        act("Show in Explorer", "folder", lambda: open_in_explorer(first["path"]))
    m.addSeparator()
    act("Remove from library…", "minus", lambda: remove_from_library(parent, ids), shortcut="Delete")
    act("Send to Recycle Bin…", "trash", lambda: recycle(parent, ids), shortcut="Shift+Delete")
    return m
