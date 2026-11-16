"""The full-screen player (P): big cover art, the song's details and controls, lyrics that follow along, and the queue,
the artist and the song's full details beside them. Esc or P goes back to where you were."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSlider, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.db import get_db, main_artist
from . import actions, theme
from .hub import hub
from .library import fmt_len, get_library
from .nowplaying import ArtistSnippet, LyricsBox, song_details
from .playback import get_playback
from .playerbar import CoverLabel, LinkLabel, flat_tool, round_button, style_round
from .queue_view import QueueList
from .stars import StarRating
from .theme import C, icon
from .waveform import Waveform
from .widgets import ElidedLabel, Segmented, button, label


class FullPlayer(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fullplayer")
        self.setAttribute(Qt.WA_StyledBackground)
        self.pb = get_playback()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 12, 18, 16)
        outer.setSpacing(10)
        top = QHBoxLayout()
        back = button("Back to the library", "back", "ghost")
        back.setToolTip("Esc or P")
        back.clicked.connect(self.closed.emit)
        top.addWidget(back)
        self.context = ElidedLabel("")
        self.context.setObjectName("faint")
        top.addWidget(self.context, 1)
        mini = flat_tool("mini", "Mini player (Ctrl+M)", 16)
        mini.clicked.connect(lambda: actions._window().toggle_mini() if actions._window() else None)
        top.addWidget(mini)
        outer.addLayout(top)
        body = QHBoxLayout()
        body.setSpacing(28)
        # left: cover, details, controls
        left = QVBoxLayout()
        left.setSpacing(8)
        self.cover = CoverLabel(380, 12)
        left.addWidget(self.cover, 0, Qt.AlignHCenter)
        self.title = QLabel("Nothing playing")
        self.title.setWordWrap(True)
        self.title.setStyleSheet(f"font-size: {theme.T['fs'] + 9}px; font-weight: 700;")
        left.addWidget(self.title)
        self.artist = LinkLabel("")
        self.artist.setStyleSheet(f"font-size: {theme.T['fs'] + 2}px;")
        self.artist.clicked.connect(lambda: (self.closed.emit(), actions.go_artist(self.pb.current_row())))
        left.addWidget(self.artist)
        self.album = LinkLabel("")
        self.album.setObjectName("muted")
        self.album.clicked.connect(lambda: (self.closed.emit(), actions.go_album(self.pb.current_row())))
        left.addWidget(self.album)
        rr = QHBoxLayout()
        self.stars = StarRating(18)
        self.stars.rated.connect(lambda v: actions.set_rating([self.pb.current_id()], v)
                                 if self.pb.current_id() is not None else None)
        rr.addWidget(self.stars)
        rr.addStretch(1)
        self.fav = flat_tool("heart", "Favourite (V)", 18)
        self.fav.clicked.connect(lambda: actions.toggle_favourite([self.pb.current_id()])
                                 if self.pb.current_id() is not None else None)
        rr.addWidget(self.fav)
        left.addLayout(rr)
        self.details = QLabel("")
        self.details.setObjectName("faint")
        left.addWidget(self.details)
        left.addSpacing(6)
        self.wave = Waveform(height=54)
        self.wave.seekRequested.connect(self.pb.seek)
        left.addWidget(self.wave)
        times = QHBoxLayout()
        self.t_pos = label("0:00", "muted")
        self.t_dur = label("0:00", "muted")
        times.addWidget(self.t_pos)
        times.addStretch(1)
        times.addWidget(self.t_dur)
        left.addLayout(times)
        ctl = QHBoxLayout()
        ctl.setSpacing(12)
        ctl.addStretch(1)
        self.shuffle = flat_tool("shuffle", "Shuffle (X)", 20, True)
        self.shuffle.clicked.connect(lambda on: self.pb.set_shuffle(on))
        prev = flat_tool("prev", "Previous (←)", 20)
        prev.clicked.connect(self.pb.previous)
        self.play = round_button(56, "play")
        self.play.clicked.connect(self.pb.toggle)
        nxt = flat_tool("next", "Next (→)", 20)
        nxt.clicked.connect(self.pb.next)
        self.repeat = flat_tool("repeat", "Repeat (R)", 20, True)
        self.repeat.clicked.connect(self.pb.cycle_repeat)
        self._tools = [self.shuffle, prev, nxt, self.repeat, self.fav, mini]
        for w in (self.shuffle, prev, self.play, nxt, self.repeat):
            ctl.addWidget(w)
        ctl.addStretch(1)
        left.addLayout(ctl)
        vr = QHBoxLayout()
        vr.addStretch(1)
        self.vol_icon = flat_tool("volume", "Mute (0)", 16)
        self.vol_icon.clicked.connect(self.pb.toggle_mute)
        vr.addWidget(self.vol_icon)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(160)
        self.vol.setValue(self.pb.volume)
        self.vol.valueChanged.connect(lambda v: self.pb.set_volume(v) if v != self.pb.volume else None)
        vr.addWidget(self.vol)
        vr.addStretch(1)
        left.addLayout(vr)
        left.addStretch(1)
        lw = QWidget()
        lw.setObjectName("transparent")
        lw.setLayout(left)
        lw.setMinimumWidth(320)
        lw.setMaximumWidth(460)
        body.addWidget(lw, 4)
        # middle: lyrics
        mid = QVBoxLayout()
        mid.addWidget(label("Lyrics", "h2"))
        self.lyrics = LyricsBox(big=True)
        mid.addWidget(self.lyrics, 1)
        mw = QWidget()
        mw.setObjectName("transparent")
        mw.setLayout(mid)
        body.addWidget(mw, 5)
        # right: queue / artist / details
        right = QVBoxLayout()
        right.setSpacing(8)
        self.tabs = Segmented([("queue", "Up next"), ("artist", "Artist"), ("details", "Details"), ("history", "History")],
                              "queue")
        self.tabs.changed.connect(self._tab)
        right.addWidget(self.tabs)
        self.stack = QStackedWidget()
        self.queue = QueueList(show_current=True)
        self.stack.addWidget(self.queue)
        asc = QScrollArea()
        asc.setWidgetResizable(True)
        asc.setFrameShape(QFrame.NoFrame)
        asc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.artist_box = ArtistSnippet(big=True)
        aw = QWidget()
        aw.setObjectName("transparent")
        al = QVBoxLayout(aw)
        al.setContentsMargins(0, 4, 6, 0)
        al.addWidget(self.artist_box)
        al.addStretch(1)
        asc.setWidget(aw)
        self.stack.addWidget(asc)
        dsc = QScrollArea()
        dsc.setWidgetResizable(True)
        dsc.setFrameShape(QFrame.NoFrame)
        dsc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dw = QWidget()
        dw.setObjectName("transparent")
        self.grid = QGridLayout(dw)
        self.grid.setContentsMargins(0, 4, 6, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(6)
        dsc.setWidget(dw)
        self.stack.addWidget(dsc)
        from .history import SongHistory
        self.song_history = SongHistory()
        self.stack.addWidget(self.song_history)
        right.addWidget(self.stack, 1)
        rw = QWidget()
        rw.setObjectName("transparent")
        rw.setLayout(right)
        rw.setMinimumWidth(300)
        rw.setMaximumWidth(420)
        body.addWidget(rw, 3)
        outer.addLayout(body, 1)
        self.pb.currentChanged.connect(self._current)
        self.pb.positionChanged.connect(self._pos)
        self.pb.durationChanged.connect(self._dur)
        self.pb.playingChanged.connect(lambda on: style_round(self.play, on))
        self.pb.modesChanged.connect(self._modes)
        self.pb.queueChanged.connect(self._context)
        self.pb.volumeChanged.connect(self._volume)
        hub.songChanged.connect(lambda sid: QTimer.singleShot(320, lambda: self._current(sid))
                                if sid == self.pb.current_id() and self.isVisible() else None)
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)
        self._modes()

    def showEvent(self, e):
        super().showEvent(e)
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)
        style_round(self.play, self.pb.is_playing())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        s = max(200, min(int(self.height() * 0.42), 440, int(self.width() * 0.30)))
        self.cover.set_size(s)

    def _tab(self, k: str) -> None:
        self.stack.setCurrentIndex({"queue": 0, "artist": 1, "details": 2, "history": 3}[k])

    def _context(self) -> None:
        self.context.setText(f"Playing from {self.pb.context}" if self.pb.context else "")

    def _current(self, sid: int) -> None:
        self._context()
        r = get_library().get(sid) if sid is not None and sid >= 0 else None
        if r is None:
            self.title.setText("Nothing playing")
            for w in (self.artist, self.album, self.details):
                w.setText("")
            self.cover.set_song(None)
            self.lyrics.set_song(None)
            self.artist_box.set_artist("")
            self.wave.load(None, "", 0)
            self._fill_details(None)
            return
        if not self.isVisible():
            return
        self.title.setText(r["name"])
        self.artist.setText(r["artist"] or "Unknown artist")
        self.album.setText(" · ".join(x for x in (r["album"], str(r["year_n"]) if r["year_n"] else "") if x))
        self.cover.set_song(sid, r["album"] or r["name"])
        self.stars.set_rating(r["rating"] or 0)
        on = bool(r["favourite"])
        self.fav.setIcon(icon("heart_on" if on else "heart", C["pink"] if on else C["sec"], 18))
        self.details.setText(song_details(r))
        self.t_dur.setText(fmt_len(r["duration"]))
        if getattr(self, "_wave_sid", None) != sid:
            self._wave_sid = sid
            self.wave.load(r["path"], f"{sid}-{int(r.get('added_at') or 0)}", r["duration"] or 0)
        self.lyrics.set_song(sid)
        self.artist_box.set_artist(main_artist(r["artist"]))
        self._fill_details(sid)
        self.song_history.set_song(sid)

    def _fill_details(self, sid: int | None) -> None:
        from .widgets import clear_layout
        clear_layout(self.grid)
        row = get_db().song(sid) if sid is not None else None
        if row is None:
            return
        def when(t):
            return time.strftime("%d %b %Y, %H:%M", time.localtime(t)) if t else "Never"
        src = {"tagged": "The file's own tags", "found": f"Looked up ({row['id_source']})",
               "manual": "You", "not_found": "Not recognised", "pending": "Waiting to be looked up",
               "error": "Lookup failed (no internet?)"}.get(row["id_status"], row["id_status"])
        items = [("Title", row["title"]), ("Artist", row["artist"]), ("Album", row["album"]),
                 ("Album artist", row["album_artist"]), ("Genre", row["genre"]), ("Released", row["year"]),
                 ("Track", f"{row['track']}" + (f" (disc {row['disc']})" if row["disc"] else "") if row["track"] else ""),
                 ("Length", fmt_len(row["duration"])),
                 ("Format", f"{row['codec'] or ''} · {int(row['bitrate'] or 0) // 1000} kbps · "
                            f"{(row['sample_rate'] or 0) / 1000:g} kHz" if row["bitrate"] else row["codec"]),
                 ("Plays", f"{row['plays']} (skipped {row['skips']})"), ("Last played", when(row["last_played"])),
                 ("Added", when(row["added_at"])), ("Info from", src),
                 ("Loudness", f"{row['lufs']:.1f} LUFS" if row["lufs"] is not None and row["lufs"] > -69 else "Not measured"),
                 ("File", row["path"])]
        for i, (k, v) in enumerate(items):
            a = label(k, "cap")
            a.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            self.grid.addWidget(a, i, 0)
            b = QLabel(str(v) if v not in (None, "") else "–")
            b.setWordWrap(True)
            b.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if k == "File":
                b.setObjectName("mono")
            self.grid.addWidget(b, i, 1)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(len(items), 1)

    def _pos(self, s: float) -> None:
        if self.isVisible():
            self.t_pos.setText(fmt_len(s))
            self.wave.set_position(s)

    def _dur(self, s: float) -> None:
        if s > 0:
            self.t_dur.setText(fmt_len(s))
            self.wave.duration = s

    def _modes(self) -> None:
        self.shuffle.setChecked(self.pb.shuffle)
        self.repeat.setChecked(self.pb.repeat != "off")
        self.repeat.setIcon(icon("repeat_one" if self.pb.repeat == "one" else "repeat", C["sec"], 20,
                                 checked_color=C["acc"]))

    def _volume(self, v: int, muted: bool) -> None:
        if self.vol.value() != v:
            self.vol.blockSignals(True)
            self.vol.setValue(v)
            self.vol.blockSignals(False)
        self.vol_icon.setIcon(icon("mute" if muted else "volume", C["warn"] if muted else C["sec"], 16))

    def theme_changed(self) -> None:
        style_round(self.play, self.pb.is_playing())
        for b in self._tools:
            b.setIcon(icon(b._ic, C["sec"], b.iconSize().width(), checked_color=C["acc"]))
        self.title.setStyleSheet(f"font-size: {theme.T['fs'] + 9}px; font-weight: 700;")
        self._modes()
