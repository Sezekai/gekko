"""The player along the bottom of every screen: the song, its controls, the sound wave to seek with, volume, and
buttons for the side panel, the full-screen player, the mini player and the sleep timer."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QVBoxLayout, QWidget

from . import actions, theme
from .covers import get_covers, placeholder
from .hub import hub
from .library import fmt_len, get_library
from .playback import get_playback
from .theme import C, icon
from .waveform import Waveform
from .widgets import ElidedLabel, label


def round_button(size: int, ic: str) -> QToolButton:
    b = QToolButton()
    b.setObjectName("playRound")
    b.setFixedSize(size, size)
    b.setIconSize(QSize(int(size * 0.42), int(size * 0.42)))
    b.setCursor(Qt.PointingHandCursor)
    b._ic = ic
    style_round(b)
    return b


def style_round(b: QToolButton, playing: bool | None = None) -> None:
    s = b.width()
    b.setStyleSheet(f"QToolButton#playRound {{ border-radius: {s // 2}px; background: {theme.grad()}; padding: 0; }}"
                    f"QToolButton#playRound:hover {{ background: {C['text']}; }}")
    ic = b._ic if playing is None else ("pause" if playing else "play")
    b._ic = ic
    b.setIcon(icon(ic, C["on_acc"], int(s * 0.42)))


def flat_tool(ic: str, tip: str, size: int = 16, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(icon(ic, C["sec"], size, checked_color=C["acc"]))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedSize(size + 14, size + 14)
    b._ic = ic
    return b


class CoverLabel(QLabel):
    """A song's cover, kept up to date as it loads."""

    clicked = Signal()

    def __init__(self, size: int, radius: float = 6, parent=None):
        super().__init__(parent)
        self.s = size
        self.radius = radius
        self.sid: int | None = None
        self.seed = ""
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        get_covers().ready.connect(self._ready)

    def set_song(self, sid: int | None, seed: str = "") -> None:
        self.sid, self.seed = sid, seed
        self._paint()

    def set_size(self, size: int) -> None:
        if size != self.s:
            self.s = size
            self.setFixedSize(size, size)
            self._paint()

    def _ready(self, key: str) -> None:
        if self.sid is not None and key == f"song:{self.sid}":
            self._paint()

    def _paint(self) -> None:
        s = self.s
        pm = get_covers().get(f"song:{self.sid}", s) if self.sid is not None else None
        if pm is None:
            pm = placeholder(s, self.seed or "♪")
        out = QPixmap(int(s * 2), int(s * 2))
        out.setDevicePixelRatio(2)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(0, 0, s, s, self.radius, self.radius)
        p.setClipPath(path)
        from PySide6.QtCore import QRectF
        p.drawPixmap(QRectF(0, 0, s, s), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.end()
        self.setPixmap(out)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class LinkLabel(ElidedLabel):
    """Text that takes you somewhere when clicked (artist, album)."""

    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, e):
        f = self.font()
        f.setUnderline(True)
        self.setFont(f)

    def leaveEvent(self, e):
        f = self.font()
        f.setUnderline(False)
        self.setFont(f)


class PlayerBar(QFrame):
    sideToggled = Signal(bool)
    fullRequested = Signal()
    miniRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playerbar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedHeight(68)
        self.pb = get_playback()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(10)
        # the song
        self.cover = CoverLabel(48, 6)
        self.cover.setToolTip("Full-screen player (P)")
        self.cover.clicked.connect(self.fullRequested.emit)
        lay.addWidget(self.cover)
        info = QVBoxLayout()
        info.setSpacing(0)
        info.setContentsMargins(0, 2, 0, 2)
        self.title = LinkLabel("Nothing playing")
        self.title.setObjectName("h2")
        self.title.setToolTip("Go to the album")
        self.title.clicked.connect(lambda: actions.go_album(self.pb.current_row()))
        self.artist = LinkLabel("")
        self.artist.setObjectName("muted")
        self.artist.setToolTip("Go to the artist")
        self.artist.clicked.connect(lambda: actions.go_artist(self.pb.current_row()))
        info.addWidget(self.title)
        info.addWidget(self.artist)
        iw = QWidget()
        iw.setObjectName("transparent")
        iw.setLayout(info)
        iw.setFixedWidth(210)
        lay.addWidget(iw)
        self.fav = flat_tool("heart", "Favourite (V)", 16)
        self.fav.clicked.connect(self._fav)
        lay.addWidget(self.fav)
        lay.addSpacing(6)
        # controls
        self.shuffle = flat_tool("shuffle", "Shuffle what's still to come (X)", 16, True)
        self.shuffle.clicked.connect(lambda on: self.pb.set_shuffle(on))
        self.prev = flat_tool("prev", "Previous (←); back to the start after 3 s", 16)
        self.prev.clicked.connect(self.pb.previous)
        self.play = round_button(38, "play")
        self.play.setToolTip("Play / pause (Space)")
        self.play.clicked.connect(self.pb.toggle)
        self.next = flat_tool("next", "Next (→)", 16)
        self.next.clicked.connect(self.pb.next)
        self.repeat = flat_tool("repeat", "Repeat: off (R)", 16, True)
        self.repeat.clicked.connect(self.pb.cycle_repeat)
        for w in (self.shuffle, self.prev, self.play, self.next, self.repeat):
            lay.addWidget(w)
        lay.addSpacing(6)
        # the wave
        self.t_pos = label("0:00", "muted")
        self.t_pos.setMinimumWidth(40)
        self.t_pos.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.wave = Waveform(height=36)
        self.wave.setToolTip("Click or drag to move through the song (Shift + ← → skip 10 s)")
        self.wave.seekRequested.connect(self.pb.seek)
        self.t_dur = label("0:00", "muted")
        self.t_dur.setMinimumWidth(40)
        lay.addWidget(self.t_pos)
        lay.addWidget(self.wave, 1)
        lay.addWidget(self.t_dur)
        lay.addSpacing(4)
        # extras
        self.sleep = flat_tool("moon", "Sleep timer", 16, True)
        self.sleep.clicked.connect(self._sleep_menu)
        self.side = flat_tool("panel", "Now playing panel: cover, lyrics, artist and the queue (H hides every panel)",
                              16, True)
        self.side.clicked.connect(lambda on: self.sideToggled.emit(on))
        self.full = flat_tool("expand", "Full-screen player (P)", 16)
        self.full.clicked.connect(self.fullRequested.emit)
        self.mini = flat_tool("mini", "Mini player (Ctrl+M)", 16)
        self.mini.clicked.connect(self.miniRequested.emit)
        for w in (self.sleep, self.side, self.full, self.mini):
            lay.addWidget(w)
        self.vol_icon = flat_tool("volume", "Mute (0) · volume down - and up =", 16)
        self.vol_icon.clicked.connect(self.pb.toggle_mute)
        lay.addWidget(self.vol_icon)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(90)
        self.vol.setValue(self.pb.volume)
        self.vol.valueChanged.connect(self._vol_moved)
        lay.addWidget(self.vol)
        self.pb.currentChanged.connect(self._current)
        self.pb.playingChanged.connect(self._playing)
        self.pb.positionChanged.connect(self._pos)
        self.pb.durationChanged.connect(self._dur)
        self.pb.modesChanged.connect(self._modes)
        self.pb.volumeChanged.connect(self._volume)
        hub.songChanged.connect(self._song_changed)
        self._tick = QTimer(self, interval=1000, timeout=self._sleep_tip)
        self._current(self.pb.current_id() or -1)
        self._modes()
        self._volume(self.pb.volume, self.pb.muted)

    # ---- updates from the player --------------------------------------------------------------------
    def _current(self, sid: int) -> None:
        r = get_library().get(sid) if sid >= 0 else None
        if r is None:
            self.title.setText("Nothing playing")
            self.artist.setText("Double-click a song to play it")
            self.cover.set_song(None)
            self.wave.load(None, "", 0)
            self.t_pos.setText("0:00")
            self.t_dur.setText("0:00")
            self.fav.setIcon(icon("heart", C["sec"], 16))
            return
        self.title.setText(r["name"])
        self.artist.setText(r["artist"] or "Unknown artist")
        self.cover.set_song(sid, r["album"] or r["name"])
        self.t_dur.setText(fmt_len(r["duration"]))
        self.wave.load(r["path"], f"{sid}-{int(r.get('added_at') or 0)}", r["duration"] or 0)
        self._fav_icon(r)

    def _song_changed(self, sid: int) -> None:
        if sid == self.pb.current_id():
            r = get_library().get(sid) or self.pb.current_row()
            if r is not None:
                QTimer.singleShot(300, lambda: self._refresh_text(sid))

    def _refresh_text(self, sid: int) -> None:
        r = get_library().get(sid)
        if r is not None and sid == self.pb.current_id():
            self.title.setText(r["name"])
            self.artist.setText(r["artist"] or "Unknown artist")
            self._fav_icon(r)
            self.cover.set_song(sid, r["album"] or r["name"])

    def _fav_icon(self, r) -> None:
        on = bool(r["favourite"])
        self.fav.setIcon(icon("heart_on" if on else "heart", C["pink"] if on else C["sec"], 16))
        self.fav.setToolTip("Unfavourite (V)" if on else "Favourite (V)")

    def _fav(self) -> None:
        sid = self.pb.current_id()
        if sid is not None:
            actions.toggle_favourite([sid])

    def _playing(self, on: bool) -> None:
        style_round(self.play, on)

    def _pos(self, s: float) -> None:
        self.t_pos.setText(fmt_len(s))
        self.wave.set_position(s)

    def _dur(self, s: float) -> None:
        if s > 0:
            self.t_dur.setText(fmt_len(s))
            self.wave.duration = s

    def _modes(self) -> None:
        self.shuffle.setChecked(self.pb.shuffle)
        rep = self.pb.repeat
        self.repeat.setChecked(rep != "off")
        self.repeat.setIcon(icon("repeat_one" if rep == "one" else "repeat", C["sec"], 16, checked_color=C["acc"]))
        self.repeat.setToolTip({"off": "Repeat: off (R)", "all": "Repeat: the whole queue (R)",
                                "one": "Repeat: this song (R)"}[rep])
        on = bool(self.pb.sleep_at or self.pb.sleep_after_song)
        self.sleep.setChecked(on)
        self._sleep_tip()
        (self._tick.start if on else self._tick.stop)()

    def _sleep_tip(self) -> None:
        t = self.pb.sleep_text()
        self.sleep.setToolTip(f"Sleep timer: {t.lower()}" if t else "Sleep timer")

    def _sleep_menu(self) -> None:
        self.sleep.setChecked(bool(self.pb.sleep_at or self.pb.sleep_after_song))
        m = QMenu(self)
        for mins in (15, 30, 45, 60, 90):
            m.addAction(f"In {mins} minutes").triggered.connect(lambda _=False, v=mins: self.pb.set_sleep(v))
        m.addAction("After this song").triggered.connect(lambda: self.pb.set_sleep(None, True))
        if self.pb.sleep_at or self.pb.sleep_after_song:
            m.addSeparator()
            m.addAction(f"Turn off ({self.pb.sleep_text().lower()})").triggered.connect(lambda: self.pb.set_sleep(None))
        m.exec(self.sleep.mapToGlobal(QPoint(0, -m.sizeHint().height() - 4)))

    def _vol_moved(self, v: int) -> None:
        if v != self.pb.volume or self.pb.muted:
            self.pb.set_volume(v)

    def _volume(self, v: int, muted: bool) -> None:
        if self.vol.value() != v:
            self.vol.blockSignals(True)
            self.vol.setValue(v)
            self.vol.blockSignals(False)
        self.vol_icon.setIcon(icon("mute" if muted else "volume", C["warn"] if muted else C["sec"], 16))
        self.vol_icon.setToolTip("Unmute (0)" if muted else "Mute (0) · volume down - and up =")

    def show_volume_tip(self) -> None:
        from PySide6.QtWidgets import QToolTip
        if self.isVisible():
            txt = "Muted" if self.pb.muted else f"Volume {self.pb.volume}%"
            QToolTip.showText(self.vol.mapToGlobal(QPoint(0, -34)), txt, self.vol, self.vol.rect(), 1200)

    def theme_changed(self) -> None:
        style_round(self.play, self.pb.is_playing())
        for b in (self.shuffle, self.prev, self.next, self.sleep, self.side, self.full, self.mini):
            b.setIcon(icon(b._ic, C["sec"], 16, checked_color=C["acc"]))
        self._modes()
        self._volume(self.pb.volume, self.pb.muted)
        r = self.pb.current_row()
        if r is not None:
            self._fav_icon(get_library().get(r["id"]) or r)
