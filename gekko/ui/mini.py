"""The mini player (Ctrl+M): the whole window shrinks to the cover, the song and its controls. Drag it anywhere;
it can stay on top of other windows. The expand button (or Ctrl+M, or Esc) brings the full window back."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ..core.settings import get_settings
from . import actions, theme
from .hub import hub
from .library import get_library
from .playback import get_playback
from .playerbar import CoverLabel, flat_tool, round_button, style_round
from .theme import C, icon
from .widgets import ElidedLabel


class _Progress(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(3)
        self.frac = 0.0

    def set(self, f: float) -> None:
        self.frac = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(C["raised2"]))
        g = QLinearGradient(0, 0, self.width(), 0)
        st = theme.T["stops"]
        for i, c in enumerate(st):
            g.setColorAt(i / max(1, len(st) - 1), QColor(c))
        p.fillRect(QRectF(0, 0, self.width() * self.frac, self.height()), g)
        p.end()

    def mousePressEvent(self, e):
        pb = get_playback()
        if pb.duration():
            pb.seek(e.position().x() / max(1, self.width()) * pb.duration())


class MiniPlayer(QFrame):
    restore = Signal()
    pinToggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mini")
        self.setAttribute(Qt.WA_StyledBackground)
        self.pb = get_playback()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(8, 8, 8, 6)
        row.setSpacing(8)
        self.cover = CoverLabel(64, 6)
        self.cover.setToolTip("Back to the full window")
        self.cover.clicked.connect(self.restore.emit)
        row.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = ElidedLabel("Nothing playing")
        self.title.setObjectName("h2")
        self.artist = ElidedLabel("")
        self.artist.setObjectName("muted")
        col.addWidget(self.title)
        col.addWidget(self.artist)
        ctl = QHBoxLayout()
        ctl.setSpacing(2)
        prev = flat_tool("prev", "Previous", 14)
        prev.clicked.connect(self.pb.previous)
        self.play = round_button(28, "play")
        self.play.clicked.connect(self.pb.toggle)
        nxt = flat_tool("next", "Next", 14)
        nxt.clicked.connect(self.pb.next)
        self.fav = flat_tool("heart", "Favourite", 14)
        self.fav.clicked.connect(lambda: actions.toggle_favourite([self.pb.current_id()])
                                 if self.pb.current_id() is not None else None)
        for w in (prev, self.play, nxt, self.fav):
            ctl.addWidget(w)
        ctl.addStretch(1)
        col.addLayout(ctl)
        row.addLayout(col, 1)
        side = QVBoxLayout()
        side.setSpacing(2)
        self.pin = flat_tool("pin", "Stay on top of other windows", 14, True)
        self.pin.setChecked(bool(get_settings().get("ui.mini_on_top", True)))
        self.pin.toggled.connect(self._pin)
        expand = flat_tool("expand", "Back to the full window (Ctrl+M)", 14)
        expand.clicked.connect(self.restore.emit)
        side.addWidget(expand)
        side.addWidget(self.pin)
        side.addStretch(1)
        row.addLayout(side)
        outer.addLayout(row, 1)
        self.bar = _Progress()
        outer.addWidget(self.bar)
        self._tools = [prev, nxt, self.fav, self.pin, expand]
        self.pb.currentChanged.connect(self._current)
        self.pb.playingChanged.connect(lambda on: style_round(self.play, on))
        self.pb.positionChanged.connect(lambda s: self.bar.set(s / self.pb.duration()) if self.pb.duration() else None)
        hub.songChanged.connect(lambda sid: self._current(sid) if sid == self.pb.current_id() else None)
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)

    def _pin(self, on: bool) -> None:
        get_settings().set("ui.mini_on_top", on)
        self.pinToggled.emit(on)

    def _current(self, sid: int) -> None:
        r = get_library().get(sid) if sid is not None and sid >= 0 else None
        if r is None:
            self.title.setText("Nothing playing")
            self.artist.setText("")
            self.cover.set_song(None)
            return
        self.title.setText(r["name"])
        self.artist.setText(r["artist"] or "Unknown artist")
        self.cover.set_song(sid, r["album"] or r["name"])
        on = bool(r["favourite"])
        self.fav.setIcon(icon("heart_on" if on else "heart", C["pink"] if on else C["sec"], 14))

    def showEvent(self, e):
        super().showEvent(e)
        style_round(self.play, self.pb.is_playing())

    # drag the little window from anywhere that isn't a button
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()

    def theme_changed(self) -> None:
        style_round(self.play, self.pb.is_playing())
        for b in self._tools:
            b.setIcon(icon(b._ic, C["sec"], 14, checked_color=C["acc"]))
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)
