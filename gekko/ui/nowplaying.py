"""The Now playing panel pinned on the right while you browse: cover, the song's details, lyrics that follow along,
a bit about the artist, and the queue. Sections fold away; the panel can be resized or hidden."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from ..core.db import get_db, main_artist
from . import actions, theme
from .covers import get_covers, placeholder
from .hub import hub
from .library import fmt_len, get_library
from .lyrics import LyricsView
from .playback import get_playback
from .playerbar import CoverLabel, LinkLabel, flat_tool
from .queue_view import QueueList
from .stars import StarRating
from .theme import C, icon
from .widgets import ElidedLabel, button, label


def _window():
    return actions._window()


def song_details(r) -> str:
    parts = []
    if r["genre"]:
        parts.append(r["genre"])
    parts.append(fmt_len(r["duration"]))
    if r["plays"]:
        parts.append(f"played {r['plays']}×")
    if r["bitrate"]:
        parts.append(f"{int(r['bitrate']) // 1000} kbps")
    ext = (r["path"].rsplit(".", 1)[-1] if "." in r["path"] else "").upper()
    if ext:
        parts.append(ext)
    return " · ".join(parts)


class Section(QWidget):
    """A heading you click to fold the content away (remembered)."""

    def __init__(self, key: str, title: str, content: QWidget, open_: bool = True, parent=None):
        super().__init__(parent)
        from ..core.settings import get_settings
        self.key = key
        self.s = get_settings()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        self.btn = QToolButton()
        self.btn.setCheckable(True)
        self.btn.setChecked(bool(self.s.get(f"ui.sections.{key}", open_)))
        self.btn.setText(title)
        self.btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn.setStyleSheet(f"QToolButton {{ font-weight: 600; color: {C['sec']}; padding: 2px 4px; }}"
                               f"QToolButton:checked {{ background: transparent; }}")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.toggled.connect(self._toggle)
        head.addWidget(self.btn)
        head.addStretch(1)
        self.extra = QHBoxLayout()
        self.extra.setSpacing(4)
        head.addLayout(self.extra)
        lay.addLayout(head)
        self.content = content
        lay.addWidget(content)
        self._toggle(self.btn.isChecked(), save=False)

    def _toggle(self, on: bool, save: bool = True) -> None:
        self.content.setVisible(on)
        self.btn.setIcon(icon("chev" if on else "back", C["sec"], 12))
        if save:
            self.s.set(f"ui.sections.{self.key}", on)

    def set_title(self, t: str) -> None:
        self.btn.setText(t)


class ArtistSnippet(QWidget):
    """Photo, name and the start of the bio, with a button to the full profile."""

    def __init__(self, big: bool = False, parent=None):
        super().__init__(parent)
        self.name = ""
        self.big = big
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.photo = QLabel()
        self.ps = 72 if big else 48
        self.photo.setFixedSize(self.ps, self.ps)
        top.addWidget(self.photo)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = ElidedLabel("")
        self.title.setObjectName("h2")
        self.meta = ElidedLabel("")
        self.meta.setObjectName("faint")
        col.addWidget(self.title)
        col.addWidget(self.meta)
        col.addStretch(1)
        top.addLayout(col, 1)
        lay.addLayout(top)
        self.bio = QLabel("")
        self.bio.setWordWrap(True)
        self.bio.setObjectName("muted")
        self.bio.setTextFormat(Qt.PlainText)
        self.bio.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        lay.addWidget(self.bio)
        row = QHBoxLayout()
        self.open_btn = button("Open profile", "user")
        self.open_btn.clicked.connect(lambda: hub.goto.emit("artist", self.name) if self.name else None)
        row.addWidget(self.open_btn)
        row.addStretch(1)
        lay.addLayout(row)
        hub.artistChanged.connect(lambda n: self.set_artist(self.name) if n.casefold() == self.name.casefold() else None)
        get_covers().ready.connect(lambda k: self._photo() if k == f"artist:{self.name}" else None)

    def set_artist(self, name: str) -> None:
        self.name = name or ""
        if not self.name:
            self.title.setText("")
            self.meta.setText("")
            self.bio.setText("")
            self.open_btn.hide()
            self.photo.clear()
            return
        self.open_btn.show()
        a = get_db().artist(self.name)
        lib = get_library()
        n = len(lib.artist_songs(self.name))
        self.title.setText(self.name)
        bits = []
        if a is not None:
            if a["kind"]:
                bits.append(a["kind"])
            if a["country"]:
                bits.append(a["country"])
            if a["years"]:
                bits.append(a["years"])
        bits.append(f"{n} song{'s' if n != 1 else ''} in your library")
        self.meta.setText(" · ".join(bits))
        if a is not None and a["bio"]:
            bio = a["bio"].strip()
            limit = 900 if self.big else 420
            if len(bio) > limit:
                bio = bio[:limit].rsplit(" ", 1)[0] + "…"
            self.bio.setText(bio + f"\n\nFrom {a['bio_source']}.")
        elif a is None or a["status"] == "pending":
            self.bio.setText("Looking this artist up…")
            w = _window()
            if w is not None and hasattr(w, "identify"):
                w.identify.first("artist", self.name)
        else:
            self.bio.setText("No biography was found for this artist." +
                             ("" if (get_db().kv_get("lastfm_hint") or False) else
                              " A free Last.fm key in Settings > Lookups can fill gaps Wikipedia leaves."))
        self._photo()

    def _photo(self) -> None:
        s = self.ps
        pm = get_covers().get(f"artist:{self.name}", s)
        if pm is None:
            pm = placeholder(s, self.name, "initial")
        out = QPixmap(int(s * 2), int(s * 2))
        out.setDevicePixelRatio(2)
        out.fill(Qt.transparent)
        p = QPainter(out)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        path = QPainterPath()
        path.addEllipse(QRectF(0, 0, s, s))
        p.setClipPath(path)
        p.drawPixmap(QRectF(0, 0, s, s), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.end()
        self.photo.setPixmap(out)


class LyricsBox(QWidget):
    """Lyrics with a button to look for them when there are none."""

    def __init__(self, big: bool = False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.view = LyricsView(big)
        self.view.seekRequested.connect(get_playback().seek)
        lay.addWidget(self.view, 1)
        self.find_btn = button("Look for lyrics", "search")
        self.find_btn.clicked.connect(self._find)
        row = QHBoxLayout()
        row.addWidget(self.find_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.sid: int | None = None
        pb = get_playback()
        pb.positionChanged.connect(self.view.set_position)

    def set_song(self, sid: int | None) -> None:
        self.sid = sid
        r = get_db().song(sid) if sid is not None else None
        if r is None:
            self.view.set_lyrics(None, None, "Nothing playing.")
            self.find_btn.hide()
            return
        msg = ""
        if r["info_status"] == "pending" or r["id_status"] == "pending":
            msg = "Looking for lyrics…"
        self.view.set_lyrics(r["lyrics_synced"], r["lyrics_plain"], msg or "No lyrics found for this song.")
        self.find_btn.setVisible(not self.view.has_lyrics() and not msg)
        if self.view.has_lyrics():
            self.view.set_position(get_playback().position())

    def _find(self) -> None:
        if self.sid is None:
            return
        get_db().update_song(self.sid, info_status="pending")
        w = _window()
        if w is not None and hasattr(w, "identify"):
            w.identify.first("info", self.sid)
        self.view.set_lyrics(None, None, "Looking for lyrics…")
        self.find_btn.hide()


class NowPlayingPanel(QFrame):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidepanel")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setMinimumWidth(260)
        self.pb = get_playback()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 8, 6, 4)
        head.setSpacing(8)
        head.addWidget(label("Now playing", "h2"))
        self.context = ElidedLabel("")
        self.context.setObjectName("faint")
        head.addWidget(self.context, 1)
        close = flat_tool("x", "Hide this panel", 14)
        close.clicked.connect(self.closed.emit)
        head.addWidget(close)
        outer.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transparent")
        self.scroll.setWidget(inner)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(12, 4, 12, 12)
        lay.setSpacing(10)
        self.cover = CoverLabel(280, theme.T.get("rp", 10))
        self.cover.setToolTip("Full-screen player (P)")
        self.cover.clicked.connect(lambda: _window().show_full(True) if _window() else None)
        lay.addWidget(self.cover, 0, Qt.AlignHCenter)
        self.title = QLabel("Nothing playing")
        self.title.setObjectName("h1")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.artist = LinkLabel("")
        self.artist.setToolTip("Go to the artist")
        self.artist.clicked.connect(lambda: actions.go_artist(self.pb.current_row()))
        lay.addWidget(self.artist)
        self.album = LinkLabel("")
        self.album.setObjectName("muted")
        self.album.setToolTip("Go to the album")
        self.album.clicked.connect(lambda: actions.go_album(self.pb.current_row()))
        lay.addWidget(self.album)
        rr = QHBoxLayout()
        self.stars = StarRating(15)
        self.stars.rated.connect(lambda v: actions.set_rating([self.pb.current_id()], v)
                                 if self.pb.current_id() is not None else None)
        rr.addWidget(self.stars)
        rr.addStretch(1)
        self.fav = flat_tool("heart", "Favourite (V)", 16)
        self.fav.clicked.connect(lambda: actions.toggle_favourite([self.pb.current_id()])
                                 if self.pb.current_id() is not None else None)
        rr.addWidget(self.fav)
        self.more = flat_tool("more", "More", 16)
        self.more.clicked.connect(self._more)
        rr.addWidget(self.more)
        lay.addLayout(rr)
        self.details = QLabel("")
        self.details.setObjectName("faint")
        self.details.setWordWrap(True)
        lay.addWidget(self.details)
        self.lyrics = LyricsBox()
        self.lyrics.view.setFixedHeight(240)
        self.sec_lyrics = Section("np_lyrics", "Lyrics", self.lyrics)
        lay.addWidget(self.sec_lyrics)
        self.artist_box = ArtistSnippet()
        self.sec_artist = Section("np_artist", "About the artist", self.artist_box)
        lay.addWidget(self.sec_artist)
        qw = QWidget()
        ql = QVBoxLayout(qw)
        ql.setContentsMargins(0, 0, 0, 0)
        self.queue = QueueList(show_current=False)
        self.queue.setMinimumHeight(160)
        ql.addWidget(self.queue)
        self.sec_queue = Section("np_queue", "Up next", qw)
        clear = button("Clear", None, "ghost")
        clear.setToolTip("Take everything after this song out of the queue")
        clear.clicked.connect(self.pb.clear_upcoming)
        self.sec_queue.extra.addWidget(clear)
        lay.addWidget(self.sec_queue)
        lay.addStretch(1)
        outer.addWidget(self.scroll, 1)
        self.pb.currentChanged.connect(self._current)
        self.pb.queueChanged.connect(self._queue_title)
        hub.songChanged.connect(self._song_changed)
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.cover.set_size(max(120, min(self.width() - 24 - 12, 420)))
        self._fit_queue()

    def _fit_queue(self) -> None:
        n = self.queue.count()
        self.queue.setFixedHeight(max(120, min(n, 12) * self.queue.row_h + 6))

    def _queue_title(self) -> None:
        n = len(self.pb.upcoming())
        self.sec_queue.set_title(f"Up next · {n:,}" if n else "Up next")
        self.context.setText(f"from {self.pb.context}" if self.pb.context else "")
        self._fit_queue()

    def _current(self, sid: int) -> None:
        r = get_library().get(sid) if sid is not None and sid >= 0 else None
        self._queue_title()
        if r is None:
            self.title.setText("Nothing playing")
            self.artist.setText("")
            self.album.setText("")
            self.details.setText("Double-click a song, or press Play on an album, artist or playlist.")
            self.cover.set_song(None)
            self.stars.set_rating(0)
            self.lyrics.set_song(None)
            self.artist_box.set_artist("")
            return
        self.title.setText(r["name"])
        self.artist.setText(r["artist"] or "Unknown artist")
        self.album.setText(" · ".join(x for x in (r["album"], str(r["year_n"]) if r["year_n"] else "") if x))
        self.cover.set_song(sid, r["album"] or r["name"])
        self.stars.set_rating(r["rating"] or 0)
        self._fav(r)
        self.details.setText(song_details(r))
        self.lyrics.set_song(sid)
        self.artist_box.set_artist(main_artist(r["artist"]))
        w = _window()
        if w is not None and hasattr(w, "identify"):
            row = get_db().song(sid)
            if row is not None and row["info_status"] == "pending":
                w.identify.first("info" if row["id_status"] != "pending" else "song", sid)

    def _fav(self, r) -> None:
        on = bool(r["favourite"])
        self.fav.setIcon(icon("heart_on" if on else "heart", C["pink"] if on else C["sec"], 16))

    def _song_changed(self, sid: int) -> None:
        if sid != self.pb.current_id():
            return
        QTimer.singleShot(320, lambda: self._refresh(sid))

    def _refresh(self, sid: int) -> None:
        if sid != self.pb.current_id():
            return
        r = get_library().get(sid)
        if r is None:
            return
        self.title.setText(r["name"])
        self.artist.setText(r["artist"] or "Unknown artist")
        self.album.setText(" · ".join(x for x in (r["album"], str(r["year_n"]) if r["year_n"] else "") if x))
        self.stars.set_rating(r["rating"] or 0)
        self._fav(r)
        self.details.setText(song_details(r))
        self.cover.set_song(sid, r["album"] or r["name"])
        if not self.lyrics.view.has_lyrics():
            self.lyrics.set_song(sid)
        if main_artist(r["artist"]) != self.artist_box.name:
            self.artist_box.set_artist(main_artist(r["artist"]))

    def _more(self) -> None:
        sid = self.pb.current_id()
        if sid is None:
            return
        m = actions.song_menu(self, [sid])
        m.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def theme_changed(self) -> None:
        self.cover.radius = theme.T.get("rp", 10)
        self._current(self.pb.current_id() if self.pb.current_id() is not None else -1)
