"""What plays: the queue, the play orders, shuffle and repeat, listens, the sleep timer, and picking up where you
left off. One controller for the whole app, so music keeps playing whichever screen is showing."""
from __future__ import annotations

import random
import time

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import log as _log
from ..core.db import get_db
from ..core.settings import get_settings
from ..jobs import level
from .engine import AudioEngine
from .hub import hub

log = _log.get("playback")

ORDERS = [
    ("in_order", "In order"),
    ("shuffle", "Shuffled"),
    ("reverse", "Reversed"),
    ("shortest", "Shortest first"),
    ("longest", "Longest first"),
    ("newest", "Newest released first"),
    ("oldest", "Oldest released first"),
    ("rating", "Highest rated first"),
    ("plays", "Most played first"),
    ("least", "Least played first"),
    ("added", "Newest added first"),
]
ORDER_NAMES = dict(ORDERS)


def _year(r) -> int:
    try:
        return int(str(r["year"])[:4])
    except (TypeError, ValueError):
        return 0


def order_ids(rows: list, order: str) -> list[int]:
    """Song ids from rows (already in the view's order) in a play order."""
    if order == "shuffle":
        ids = [r["id"] for r in rows]
        random.shuffle(ids)
        return ids
    if order == "reverse":
        return [r["id"] for r in reversed(rows)]
    keyf = {
        "shortest": lambda r: r["duration"] or 0,
        "longest": lambda r: -(r["duration"] or 0),
        "newest": lambda r: -_year(r),
        "oldest": lambda r: _year(r) or 99999,
        "rating": lambda r: -(r["rating"] or 0),
        "plays": lambda r: -(r["plays"] or 0),
        "least": lambda r: r["plays"] or 0,
        "added": lambda r: -(r["added_at"] or 0),
    }.get(order)
    if keyf is None:
        return [r["id"] for r in rows]
    return [r["id"] for r in sorted(rows, key=keyf)]


class Playback(QObject):
    currentChanged = Signal(int)        # song id, or -1 when nothing is loaded
    queueChanged = Signal()
    playingChanged = Signal(bool)
    positionChanged = Signal(float)
    durationChanged = Signal(float)
    modesChanged = Signal()             # shuffle, repeat, sleep timer
    volumeChanged = Signal(int, bool)   # volume, muted

    def __init__(self, parent=None):
        super().__init__(parent)
        s = get_settings()
        self.engine = AudioEngine(self, gapless=bool(s.get("playback.gapless", True)))
        self.queue: list[int] = []        # song ids, in play order
        self.index = -1                   # position of the current song in the queue
        self.context = ""                 # where the queue came from ("Album: Blue", "Favourites")
        self.shuffle = False
        self.repeat = "off"               # off | all | one
        self._unshuffled: list[int] | None = None
        self.volume = int(s.get("ui.volume", 80))
        self.muted = False
        self.sleep_at: float | None = None
        self.sleep_after_song = False
        self._counted = False
        self._row = None
        self._played_s = 0.0
        self._last_pos = 0.0
        self._hist_id: int | None = None
        self._hist_saved = 0.0
        self.engine.set_volume(self.volume)
        self.engine.positionChanged.connect(self._on_pos)
        self.engine.durationChanged.connect(self.durationChanged.emit)
        self.engine.playingChanged.connect(self._on_playing)
        self.engine.advanced.connect(self._on_advanced)
        self.engine.finished.connect(self._on_finished)
        self.engine.failed.connect(self._on_failed)
        self._save_timer = QTimer(self, singleShot=True, interval=1500, timeout=self.save_state)
        self._sleep_timer = QTimer(self, interval=1000, timeout=self._sleep_tick)
        hub.songChanged.connect(self._song_changed)

    # ---- what's playing --------------------------------------------------------------------------
    def current_id(self) -> int | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    def current_row(self):
        return self._row

    def upcoming(self) -> list[int]:
        return self.queue[self.index + 1:] if self.index >= 0 else list(self.queue)

    def is_playing(self) -> bool:
        return self.engine.is_playing()

    def position(self) -> float:
        return self.engine.position()

    def duration(self) -> float:
        return self.engine.duration() or (self._row["duration"] if self._row else 0.0)

    # ---- starting things --------------------------------------------------------------------------
    def play_rows(self, rows: list, start_id: int | None = None, order: str = "in_order", context: str = "") -> None:
        """Play a list (an album, a playlist, the songs shown) in a play order, starting at start_id."""
        rows = [r for r in rows if not r["missing"]]
        if not rows:
            hub.toast.emit("There's nothing here to play.", "info")
            return
        ids = order_ids(rows, order)
        self.shuffle = order == "shuffle"
        self._unshuffled = [r["id"] for r in rows] if self.shuffle else None
        if start_id is not None and start_id in ids:
            if self.shuffle:
                ids.remove(start_id)
                ids.insert(0, start_id)
                start = 0
            else:
                start = ids.index(start_id)
        else:
            start = 0
        self.queue = ids
        self.context = context
        self._start(start)
        self.queueChanged.emit()
        self.modesChanged.emit()

    def play_ids(self, ids: list[int], order: str = "in_order", context: str = "") -> None:
        self.play_rows(get_db().songs_by_ids(ids), None, order, context)

    def play_next(self, ids: list[int]) -> None:
        ids = [i for i in ids if i is not None]
        if not ids:
            return
        if self.index < 0:
            self.play_ids(ids, context="Played next")
            return
        self.queue[self.index + 1:self.index + 1] = ids
        if self._unshuffled is not None:
            self._unshuffled.extend(ids)
        self._changed()
        hub.toast.emit(f"{self._n(ids)} will play next.", "ok")

    def add(self, ids: list[int]) -> None:
        ids = [i for i in ids if i is not None]
        if not ids:
            return
        start = self.index < 0 and not self.queue
        self.queue.extend(ids)
        if self._unshuffled is not None:
            self._unshuffled.extend(ids)
        if start:
            self._start(0)
        self._changed()
        hub.toast.emit(f"Added {self._n(ids)} to the queue.", "ok")

    @staticmethod
    def _n(ids: list[int]) -> str:
        if len(ids) == 1:
            r = get_db().song(ids[0])
            return f"“{(r['title'] or 'That song') if r else 'That song'}”"
        return f"{len(ids):,} songs"

    def jump(self, queue_index: int) -> None:
        if 0 <= queue_index < len(self.queue):
            self._count_skip()
            self._start(queue_index)
            self.queueChanged.emit()

    def remove_at(self, idxs: list[int]) -> None:
        cur = self.current_id()
        for i in sorted(set(idxs), reverse=True):
            if 0 <= i < len(self.queue) and i != self.index:
                del self.queue[i]
                if i < self.index:
                    self.index -= 1
        if cur is not None and self.current_id() != cur:
            self.index = self.queue.index(cur) if cur in self.queue else -1
        self._changed()

    def move(self, src: list[int], dest: int) -> None:
        """Move queue entries (indexes) so they sit before `dest`."""
        cur = self.current_id()
        cur_i = self.index
        src = sorted(i for i in set(src) if 0 <= i < len(self.queue))
        items = [self.queue[i] for i in src]
        marks = [(i == cur_i) for i in src]
        for i in reversed(src):
            del self.queue[i]
        dest -= sum(1 for i in src if i < dest)
        dest = max(0, min(dest, len(self.queue)))
        self.queue[dest:dest] = items
        if any(marks):
            self.index = dest + marks.index(True)
        elif cur is not None:
            # the current song stays the same entry
            before = [i for i in range(len(self.queue)) if self.queue[i] == cur]
            if before:
                self.index = min(before, key=lambda i: abs(i - cur_i))
        self._changed()

    def clear_upcoming(self) -> None:
        if self.index >= 0:
            self.queue = self.queue[:self.index + 1]
        else:
            self.queue = []
        self._unshuffled = None
        self._changed()

    def clear(self) -> None:
        self._hist_close()
        self.engine.unload()
        self.queue, self.index, self._row = [], -1, None
        self.context = ""
        self.currentChanged.emit(-1)
        self._changed()

    # ---- controls ------------------------------------------------------------------------------------
    def toggle(self) -> None:
        if self.index < 0:
            if self.queue:
                self._start(0)
            else:
                hub.toast.emit("Pick something to play: double-click a song, or press Play on an album.", "info")
            return
        if self.engine.current_path() is None:
            self._start(self.index, play=True)
            return
        self.engine.toggle()

    def play(self) -> None:
        if not self.is_playing():
            self.toggle()

    def pause(self) -> None:
        self.engine.pause()

    def next(self) -> None:
        if not self.queue:
            return
        self._count_skip()
        if self.index + 1 < len(self.queue):
            self._start(self.index + 1)
        elif self.repeat in ("all", "one"):
            self._start(0)
        else:
            hub.toast.emit("That was the last song in the queue.", "info")
            return
        self.queueChanged.emit()

    def previous(self) -> None:
        if not self.queue:
            return
        if self.engine.position() > 3 or self.index <= 0:
            if self.index <= 0 and self.repeat == "all" and self.engine.position() <= 3 and len(self.queue) > 1:
                self._start(len(self.queue) - 1)
                self.queueChanged.emit()
                return
            self.engine.seek(0)
            if not self.is_playing():
                self.engine.play()
            return
        self._start(self.index - 1)
        self.queueChanged.emit()

    def seek(self, s: float) -> None:
        self.engine.seek(s)

    def skip_by(self, delta: float) -> None:
        self.engine.seek(max(0.0, self.engine.position() + delta))

    def set_volume(self, v: int) -> None:
        self.volume = max(0, min(100, int(v)))
        if self.muted and v > 0:
            self.muted = False
            self.engine.set_muted(False)
        self.engine.set_volume(self.volume)
        get_settings().set("ui.volume", self.volume)
        self.volumeChanged.emit(self.volume, self.muted)

    def nudge_volume(self, d: int) -> None:
        self.set_volume(self.volume + d)

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        self.engine.set_muted(self.muted)
        self.volumeChanged.emit(self.volume, self.muted)

    def set_shuffle(self, on: bool) -> None:
        """Shuffle what's still to come (the song playing stays); off puts it back in its first order."""
        if on == self.shuffle:
            return
        self.shuffle = on
        cur = self.current_id()
        if on:
            self._unshuffled = list(self.queue)
            rest = self.upcoming()
            random.shuffle(rest)
            self.queue = self.queue[:self.index + 1] + rest if self.index >= 0 else rest
        elif self._unshuffled is not None:
            base = [i for i in self._unshuffled if i in set(self.queue)]
            extra = [i for i in self.queue if i not in set(base)]
            self.queue = base + extra
            self.index = self.queue.index(cur) if cur in self.queue else -1
            self._unshuffled = None
        self._changed()
        self.modesChanged.emit()

    def cycle_repeat(self) -> None:
        self.repeat = {"off": "all", "all": "one", "one": "off"}[self.repeat]
        self._preload()
        self.modesChanged.emit()
        self._save_timer.start()

    def set_sleep(self, minutes: int | None, after_song: bool = False) -> None:
        self.sleep_after_song = after_song
        self.sleep_at = (time.time() + minutes * 60) if minutes else None
        if self.sleep_at:
            self._sleep_timer.start()
        else:
            self._sleep_timer.stop()
        self.modesChanged.emit()

    def sleep_text(self) -> str:
        if self.sleep_after_song:
            return "Stops after this song"
        if self.sleep_at:
            left = max(0, int(self.sleep_at - time.time()))
            return f"Stops in {left // 60}:{left % 60:02d}"
        return ""

    def _sleep_tick(self) -> None:
        if self.sleep_at and time.time() >= self.sleep_at:
            self.sleep_at = None
            self._sleep_timer.stop()
            self.engine.pause()
            hub.toast.emit("Sleep timer: paused.", "info")
            self.modesChanged.emit()

    # ---- inside ---------------------------------------------------------------------------------------
    def _start(self, i: int, play: bool = True, at: float = 0.0) -> None:
        self._hist_close()
        db = get_db()
        tried = 0
        while 0 <= i < len(self.queue) and tried < len(self.queue):
            r = db.song(self.queue[i])
            if r is not None and not r["missing"]:
                break
            i += 1
            tried += 1
        if not (0 <= i < len(self.queue)):
            self.index = -1
            self._row = None
            self.engine.unload()
            self.currentChanged.emit(-1)
            return
        self.index = i
        self._row = r
        self._counted = False
        self._played_s = 0.0
        self._last_pos = at
        g = level.gain_for(r["lufs"], r["peak"])
        self.engine.load(r["path"], play=play, start=at, gain=g, duration_hint=r["duration"] or 0.0)
        self._preload()
        self.currentChanged.emit(r["id"])
        self._save_timer.start()

    def _next_index(self) -> int | None:
        if self.repeat == "one":
            return self.index
        if self.index + 1 < len(self.queue):
            return self.index + 1
        if self.repeat == "all" and self.queue:
            return 0
        return None

    def _preload(self) -> None:
        if self.sleep_after_song or self.repeat == "one":
            self.engine.set_next(None)
            return
        ni = self._next_index()
        if ni is None:
            self.engine.set_next(None)
            return
        r = get_db().song(self.queue[ni])
        if r is None or r["missing"]:
            self.engine.set_next(None)
            return
        self.engine.set_next(r["path"], level.gain_for(r["lufs"], r["peak"]), r["duration"] or 0.0)

    def _on_advanced(self) -> None:
        """The preloaded song started (gapless)."""
        self._count_play()
        self._hist_close()
        ni = self._next_index()
        if ni is None:
            return
        self.index = ni
        self._row = get_db().song(self.queue[ni])
        self._counted = False
        self._played_s = 0.0
        self._last_pos = 0.0
        self._preload()
        self.currentChanged.emit(self.queue[ni])
        self.queueChanged.emit()
        self._save_timer.start()

    def _on_finished(self) -> None:
        self._count_play()
        self._hist_close()
        if self.sleep_after_song:
            self.sleep_after_song = False
            self.modesChanged.emit()
            hub.toast.emit("Sleep timer: stopped after the song.", "info")
            return
        ni = self._next_index()
        if ni is not None:
            self._start(ni)
            self.queueChanged.emit()
        else:
            self.playingChanged.emit(False)

    def _on_failed(self, msg: str) -> None:
        r = self._row
        name = (r["title"] or "that song") if r is not None else "that song"
        log.warning("couldn't play %s: %s", r["path"] if r is not None else "?", msg)
        hub.toast.emit(f"Couldn't play {name}; skipping it.", "warn")
        QTimer.singleShot(300, self.next)

    def _on_pos(self, p: float) -> None:
        if 0 < p - self._last_pos < 2:
            self._played_s += p - self._last_pos
        self._last_pos = p
        self._hist_track()
        d = self.duration()
        if not self._counted and d and self._played_s >= d * float(get_settings().get("playback.count_after", 0.5)):
            self._count_play()
        self.positionChanged.emit(p)

    def _on_playing(self, on: bool) -> None:
        self.playingChanged.emit(on)
        if not on:
            self.save_state()

    def _count_play(self) -> None:
        if self._row is not None and not self._counted:
            self._counted = True
            get_db().add_play(self._row["id"])
            if self._hist_id is None:
                self._hist_open()
            if self._hist_id is not None:
                get_db().update_listen(self._hist_id, self._heard(), True)
                hub.historyChanged.emit()
            hub.songChanged.emit(self._row["id"])

    # ---- listening history ----------------------------------------------------------------------------
    # Every time a song plays for at least a second it gets an entry: when, how long was heard, and whether that
    # counted as a listen (the same rule as the play count) or a skip. Playing it again - straight away or later -
    # makes another entry.
    def _heard(self) -> float:
        d = self.duration()
        return min(self._played_s, d) if d else self._played_s

    def _hist_open(self) -> None:
        r = self._row
        if r is None:
            return
        import time as _t
        self._hist_id = get_db().add_listen(r["id"], _t.time() - self._played_s, self._heard(),
                                            r["duration"] or None, self.context, r["title"], r["artist"])
        self._hist_saved = self._played_s
        hub.historyChanged.emit()

    def _hist_track(self) -> None:
        if self._row is None or self._played_s < 1.0:
            return
        if self._hist_id is None:
            self._hist_open()
        elif self._played_s - self._hist_saved >= 10:
            self._hist_saved = self._played_s
            get_db().update_listen(self._hist_id, self._heard())

    def _hist_close(self) -> None:
        if self._hist_id is not None:
            try:
                get_db().update_listen(self._hist_id, self._heard(), self._counted)
            except Exception as e:
                log.info("couldn't save a listen: %s", e)
            self._hist_id = None
            hub.historyChanged.emit()

    def _count_skip(self) -> None:
        if self._row is not None and not self._counted and self.engine.position() < 30 and self.is_playing():
            get_db().add_skip(self._row["id"])

    def _changed(self) -> None:
        if self.current_id() is not None:
            self._preload()
        self.queueChanged.emit()
        self._save_timer.start()

    def _song_changed(self, sid: int) -> None:
        if self._row is not None and sid == self._row["id"]:
            self._row = get_db().song(sid)

    # ---- remembering -------------------------------------------------------------------------------------
    def save_state(self) -> None:
        try:
            get_db().kv_set("queue", {"ids": self.queue[:5000], "index": self.index, "pos": self.engine.position(),
                                      "context": self.context, "shuffle": self.shuffle, "repeat": self.repeat})
        except Exception as e:
            log.info("couldn't save the queue: %s", e)

    def restore(self) -> None:
        if not get_settings().get("playback.resume", True):
            return
        st = get_db().kv_get("queue") or {}
        ids = [int(i) for i in st.get("ids") or []]
        if not ids:
            return
        self.queue = ids
        self.context = st.get("context") or ""
        self.shuffle = bool(st.get("shuffle"))
        self.repeat = st.get("repeat") if st.get("repeat") in ("off", "all", "one") else "off"
        i = int(st.get("index", -1))
        if 0 <= i < len(ids):
            self._start(i, play=False, at=float(st.get("pos") or 0.0))
        self.queueChanged.emit()
        self.modesChanged.emit()

    def shutdown(self) -> None:
        self._hist_close()
        self.save_state()
        self.engine.shutdown()


_pb: Playback | None = None


def get_playback() -> Playback:
    global _pb
    if _pb is None:
        _pb = Playback()
    return _pb
