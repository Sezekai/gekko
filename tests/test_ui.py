"""The real window, offscreen: every screen opens, playing carries on across screens, the keys work, the queue,
the full-screen and mini players, ratings and playlists."""
from __future__ import annotations

import time

import pytest

from make_library import make


def settle(app, s: float = 0.3):
    end = time.time() + s
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture()
def win(fresh, qapp, monkeypatch):
    monkeypatch.setenv("GEKKO_FAKE_SPEED", "20")
    root = make(fresh / "lib")
    from gekko.core.db import get_db
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    get_db().add_folder(str(root / "Music"))
    scan.scan(JobContext())
    # fresh singletons for this window
    from gekko.ui import covers, jobqueue, library, playback
    library._lib = None
    playback._pb = None
    jobqueue._queue = None
    from gekko.ui.main_window import MainWindow
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self: None)
    w = MainWindow()
    w.identify.stop()
    w.resize(1440, 900)
    w.show()
    settle(qapp)
    yield w
    w.close()
    settle(qapp, 0.1)


def key(w, seq: str):
    from PySide6.QtGui import QKeySequence
    from PySide6.QtGui import QShortcut
    for sc in w.findChildren(QShortcut):
        if sc.key() == QKeySequence(seq):
            sc.activated.emit()
            return
    raise AssertionError(f"no shortcut {seq}")


def test_every_screen_opens(win, qapp):
    for k in ("songs", "albums", "artists", "genres", "playlists", "folders", "tidy", "look", "settings"):
        win.go(k)
        settle(qapp, 0.15)
        assert win.stack.currentWidget() is win.pages[k]
    assert win.songs_page.b.table.model_.rowCount() == 18


def test_music_keeps_playing_across_screens_and_keys(win, qapp):
    pb = win.pb
    t = win.songs_page.b.table
    win.go("songs")
    settle(qapp)
    first = t.model_.rows[0]["id"]
    t.play_from(first)
    settle(qapp)
    assert pb.queue[0] == first and pb.is_playing()
    for k in ("albums", "artists", "settings", "tidy"):
        win.go(k)
        settle(qapp, 0.1)
        assert pb.is_playing()
    key(win, "Space")
    assert not pb.is_playing()
    key(win, "Space")
    assert pb.is_playing()
    i = pb.index
    key(win, "Right")
    assert pb.index == i + 1
    v = pb.volume
    key(win, "-")
    assert pb.volume == v - 5
    key(win, "0")
    assert pb.muted
    key(win, "V")                      # nothing chosen on this screen: the playing song
    from gekko.core.db import get_db
    assert get_db().song(pb.current_id())["favourite"] == 1
    key(win, "]")
    key(win, "]")
    key(win, "]")
    assert get_db().song(pb.current_id())["rating"] == 3
    key(win, "R")
    assert pb.repeat == "all"
    key(win, "X")
    assert pb.shuffle


def test_songs_carry_on_to_the_next(win, qapp):
    pb = win.pb
    rows = win.lib.album_songs(win.lib.albums()[0]["key"])
    pb.play_rows(rows, None, "in_order", "Album")
    settle(qapp, 1.2)                 # 20x speed: the first 7 s song is over
    assert pb.index >= 1
    from gekko.core.db import get_db
    assert get_db().song(rows[0]["id"])["plays"] == 1


def test_queue_play_next_and_reorder(win, qapp):
    pb = win.pb
    ids = [r["id"] for r in win.lib.rows][:6]
    pb.play_ids(ids[:3])
    pb.play_next([ids[5]])
    assert pb.queue[1] == ids[5]
    pb.add([ids[4]])
    assert pb.queue[-1] == ids[4]
    pb.move([4], 1)
    assert pb.queue[1] == ids[4]
    pb.remove_at([1])
    assert ids[4] not in pb.queue
    cur = pb.current_id()
    pb.set_shuffle(True)
    assert pb.current_id() == cur and pb.queue[0] == cur
    pb.set_shuffle(False)
    assert pb.queue[:len(ids[:3])] == pb.queue[:3]


def test_full_and_mini_players(win, qapp):
    pb = win.pb
    pb.play_ids([r["id"] for r in win.lib.rows][:3])
    key(win, "P")
    settle(qapp)
    assert win.layers.currentIndex() == 1
    key(win, "Esc")
    assert win.layers.currentIndex() == 0
    key(win, "Ctrl+M")
    settle(qapp)
    assert win.property("mini") and win.modes.currentIndex() == 1 and win.width() < 500
    assert pb.is_playing()
    key(win, "Ctrl+M")
    settle(qapp)
    assert not win.property("mini") and win.width() >= win.min_size[0]


def test_artist_album_pages_and_back(win, qapp):
    from gekko.ui.hub import hub
    hub.goto.emit("artist", "Aurora Lane")
    settle(qapp)
    assert win.stack.currentWidget() is win.artists_page and win.artists_page.stack.currentIndex() == 1
    assert win.artists_page.songs_b.table.model_.rowCount() == 7
    key(win, "Esc")
    assert win.artists_page.stack.currentIndex() == 0
    a = win.lib.albums()[0]
    hub.goto.emit("album", a["key"])
    settle(qapp)
    assert win.albums_page.stack.currentIndex() == 1


def test_playlists(win, qapp):
    from gekko.core.db import get_db
    from gekko.ui import actions
    ids = [r["id"] for r in win.lib.rows][:4]
    pid = get_db().add_playlist("Mine", "#9b86ff")
    actions.add_to_playlist(pid, ids)
    actions.add_to_playlist(pid, ids[:2])               # already there: not added twice
    assert get_db().playlist_song_ids(pid) == ids
    win.goto("playlist", pid)
    settle(qapp)
    assert win.playlists_page.b.table.model_.rowCount() == 4
    win.playlists_page._reordered(list(reversed(ids)))
    assert get_db().playlist_song_ids(pid) == list(reversed(ids))


def test_remove_from_library(win, qapp):
    from gekko.core.db import get_db
    from gekko.ui import actions
    r = win.lib.rows[0]
    actions.remove_from_library(win, [r["id"]])          # dialogs answer "accept" in tests
    assert get_db().song(r["id"]) is None
    assert any(x["path"] == r["path"] for x in get_db().removed())


def test_pages_built_again_leave_nothing_behind(win, qapp, monkeypatch):
    """Album, artist and genre pages make new lists and grids each time; the old ones must stop listening."""
    import sys
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    from gekko.ui.covers import get_covers
    from gekko.ui.hub import hub
    for _ in range(3):
        hub.goto.emit("artist", "Aurora Lane")
        settle(qapp, 0.1)
        hub.goto.emit("genre", "Synthpop")
        settle(qapp, 0.1)
        win.genres_page.b._view("grid", save=False)
        hub.goto.emit("album", win.lib.albums()[0]["key"])
        settle(qapp, 0.1)
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)    # the old pages really go
    get_covers().ready.emit("song:1")
    get_covers().ready.emit("artist:Aurora Lane")
    hub.artistChanged.emit("Aurora Lane")
    win.pb.play_ids([r["id"] for r in win.lib.rows][:2])
    win.pb.toggle()
    settle(qapp, 0.3)
    assert not errors, errors
