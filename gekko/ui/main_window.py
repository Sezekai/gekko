"""Main window: our own title bar, the rail, the screens, the Now playing panel, the player bar, the log drawer, the
status bar, the full-screen player and the mini player - and every key, on every screen."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QObject, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QLinearGradient, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
                               QTextEdit, QToolButton, QVBoxLayout, QWidget)

from .. import APP_NAME, VERSION
from ..core import log as _log
from ..core import paths, safemode
from ..core.db import get_db, main_artist
from ..core.settings import get_settings
from ..jobs import scan as scanjob
from ..jobs import storage
from ..jobs.identify import IdentifyQueue
from . import actions, dialogs, frameless, theme
from .drawer import Drawer, undo
from .fullplayer import FullPlayer
from .hub import hub
from .jobqueue import get_queue
from .keys_page import KeysPopup
from .library import get_library
from .look_page import LookPage
from .mini import MiniPlayer
from .nowplaying import NowPlayingPanel
from .pages import AlbumsPage, ArtistsPage, FoldersPage, GenresPage, PlaylistsPage, SongsPage
from .playback import get_playback
from .playerbar import PlayerBar
from .settings_page import SettingsPage
from .statusbar import StatusBar
from .theme import C, icon
from .tidy_page import TidyPage
from .widgets import button, label

log = _log.get("ui")


class Backdrop(QWidget):
    """The window background: the ground colour and, with the starfield scene, the stars behind the panels."""

    def __init__(self):
        super().__init__()
        self.setObjectName("backdrop")
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setClipRect(e.rect())
        theme.paint_backdrop(p, self.rect(), self.devicePixelRatioF())


class Toast(QFrame):
    """Bottom right, 4-7 s: an icon in the state colour and a short message."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("dialog")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(9)
        self.ic = QLabel()
        lay.addWidget(self.ic, 0, Qt.AlignTop)
        self.txt = QLabel()
        self.txt.setWordWrap(True)
        self.txt.setMaximumWidth(420)
        lay.addWidget(self.txt)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(24)
        sh.setOffset(0, 6)
        sh.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(sh)
        self.hide()
        self.timer = QTimer(self, singleShot=True, timeout=self.hide)

    def show_msg(self, text: str, kind: str = "info") -> None:
        if self.window().property("mini"):
            return
        col = {"ok": C["ok"], "warn": C["warn"], "error": C["danger"]}.get(kind, C["acc"])
        name = {"ok": "check", "warn": "warn", "error": "warn"}.get(kind, "info")
        self.ic.setPixmap(theme.pixmap(name, 16, col))
        self.txt.setText(text)
        self.adjustSize()
        self.place()
        self.raise_()
        self.show()
        self.timer.start(4500 if kind in ("info", "ok") else 7000)

    def place(self) -> None:
        p = self.parentWidget()
        self.move(p.width() - self.width() - 16, p.height() - self.height() - 116)

    def mousePressEvent(self, e):
        self.hide()


class TitleBar(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("titlebar")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedHeight(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(8)
        name = QLabel(APP_NAME)
        name.setStyleSheet("font-size: 12px; font-weight: 600;")
        lay.addWidget(name)
        self.screen = label("", "faint")
        self.screen.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.screen)
        lay.addStretch(1)
        self.pills = QHBoxLayout()
        self.pills.setSpacing(6)
        lay.addLayout(self.pills)
        lay.addSpacing(8)
        self.btns = []
        for name_, tip, fn in (("win_min", "Minimise", win.showMinimized), ("win_max", "Maximise", self._max),
                               ("win_close", "Close", win.close)):
            b = QToolButton()
            b.setObjectName("winClose" if name_ == "win_close" else "winBtn")
            b.setIcon(icon(name_, C["sec"], 14))
            b.setIconSize(QSize(14, 14))
            b.setFixedSize(46, 32)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            lay.addWidget(b)
            self.btns.append(b)
        self.native = False

    def _max(self) -> None:
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    def sync(self) -> None:
        self.btns[1].setIcon(icon("win_restore" if self.win.isMaximized() else "win_max", C["sec"], 14))
        self.btns[1].setToolTip("Restore" if self.win.isMaximized() else "Maximise")
        for b, n in zip(self.btns, ("win_min", None, "win_close")):
            if n:
                b.setIcon(icon(n, C["sec"], 14))

    def mousePressEvent(self, e):
        if not self.native and e.button() == Qt.LeftButton and self.win.windowHandle():
            self.win.windowHandle().startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if not self.native:
            self._max()


class Rail(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.setObjectName("rail")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setFixedWidth(64)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(2)
        self.logo = QLabel()
        self.logo.setFixedSize(36, 36)
        self.logo.setToolTip(f"{APP_NAME} {VERSION}")
        lay.addWidget(self.logo, 0, Qt.AlignHCenter)
        lay.addSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.btns: dict[str, QToolButton] = {}
        self.icons: dict[str, str] = {}
        self.badges: dict[str, QLabel] = {}
        for key, text, ic in win.PAGES:
            lay.addWidget(self._btn(key, text, ic), 0, Qt.AlignHCenter)
        lay.addStretch(1)
        for key, text, ic in win.BOTTOM:
            lay.addWidget(self._btn(key, text, ic, page=key not in ("log", "keys")), 0, Qt.AlignHCenter)
        self.restyle()

    def _btn(self, key: str, text: str, ic: str, page: bool = True) -> QToolButton:
        b = QToolButton()
        b.setObjectName("railBtn")
        b.setCheckable(True)
        b.setText(text)
        b.setIconSize(QSize(19, 19))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setFixedSize(60, 48)
        b.setCursor(Qt.PointingHandCursor)
        if page:
            self.group.addButton(b)
            b.clicked.connect(lambda _=False, k=key: self.win.go(k))
        elif key == "keys":
            b.clicked.connect(lambda _=False: self.win.toggle_keys())
        else:
            b.clicked.connect(lambda on: self.win.toggle_log(on))
        badge = QLabel(b)
        badge.setObjectName("badge")
        badge.setAlignment(Qt.AlignCenter)
        badge.hide()
        self.badges[key] = badge
        self.btns[key] = b
        self.icons[key] = ic
        return b

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        n = len(self.btns)
        h = max(38, min(48, (self.height() - 64) // max(1, n)))
        for b in self.btns.values():
            if b.height() != h:
                b.setFixedSize(60, h)

    def set_badge(self, key: str, n: int) -> None:
        bd = self.badges.get(key)
        if bd is None:
            return
        if n <= 0:
            bd.hide()
            return
        bd.setText(f"{n:,}" if n < 1000 else "999+")
        bd.setStyleSheet(f"QLabel#badge {{ background: {C['acc']}; color: {C['on_acc']}; border-radius: 7px;"
                         f" font-size: 9px; font-weight: 700; padding: 0; }}")
        f = bd.font()
        f.setPixelSize(9)
        f.setBold(True)
        from PySide6.QtGui import QFontMetrics
        w = max(14, QFontMetrics(f).horizontalAdvance(bd.text()) + 8)
        bd.setFixedSize(w, 14)
        bd.move(min(60 - w - 2, 38), 1)
        bd.setToolTip(f"{n:,} songs waiting")
        bd.show()
        bd.raise_()

    def restyle(self) -> None:
        for k, b in self.btns.items():
            b.setIcon(icon(self.icons[k], C["sec"], 19, checked_color=C["acc"]))
        pm = theme.pixmap("note", 18, C["on_acc"])
        dpr = 2.0
        tile = QPixmap(int(36 * dpr), int(36 * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.transparent)
        p = QPainter(tile)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(0, 0, 36, 36)
        st = theme.T["stops"]
        for i, s in enumerate(st):
            g.setColorAt(i / max(1, len(st) - 1), QColor(s))
        p.setBrush(g)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 36, 36), 9, 9)
        p.drawPixmap(9, 9, pm)
        p.end()
        self.logo.setPixmap(tile)


class _GlowFilter(QObject):
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QPushButton) and obj.objectName() == "primary":
            apply_glow(obj)
        return False


def apply_glow(b: QPushButton) -> None:
    if theme.T.get("glow"):
        eff = b.graphicsEffect()
        if not isinstance(eff, QGraphicsDropShadowEffect):
            eff = QGraphicsDropShadowEffect(b)
            b.setGraphicsEffect(eff)
        c = QColor(C["acc"])
        c.setAlpha(150)
        eff.setColor(c)
        eff.setBlurRadius(18)
        eff.setOffset(0, 3)
    elif isinstance(b.graphicsEffect(), QGraphicsDropShadowEffect):
        b.setGraphicsEffect(None)


def set_topmost(win: QWidget, on: bool) -> None:
    """Keep the window above others (the mini player), without re-creating it."""
    if sys.platform == "win32":
        try:
            import ctypes
            HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
            ctypes.windll.user32.SetWindowPos(int(win.winId()), HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                                              0x0002 | 0x0001 | 0x0010)
            return
        except Exception:
            pass
    win.setWindowFlag(Qt.WindowStaysOnTopHint, on)
    win.show()


class MainWindow(QMainWindow):
    PAGES = [("songs", "Songs", "note"), ("albums", "Albums", "disc"), ("artists", "Artists", "users"),
             ("genres", "Genres", "genre"), ("playlists", "Playlists", "playlist"), ("folders", "Folders", "folder"),
             ("tidy", "Tidy up", "broom")]
    BOTTOM = [("look", "Look", "look"), ("settings", "Settings", "gear"), ("keys", "Keys", "keys"), ("log", "Log", "log")]
    TITLES = {"songs": "Songs", "albums": "Albums", "artists": "Artists", "genres": "Genres", "playlists": "Playlists",
              "folders": "Folders", "tidy": "Tidy up", "look": "Appearance", "settings": "Settings"}

    def __init__(self):
        super().__init__()
        self.setObjectName("mainWindow")
        self.s = get_settings()
        self.setWindowTitle(APP_NAME)
        self.resize(1520, 920)
        scr = QGuiApplication.primaryScreen()
        av = scr.availableGeometry() if scr else None
        self.min_size = (min(1100, av.width()) if av else 1100, min(680, av.height()) if av else 680)
        self.setMinimumSize(*self.min_size)
        self.native_frame = frameless.enable(self)
        geo = self.s.get("ui.geometry")
        if geo:
            try:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
            except Exception:
                pass
        self.queue = get_queue()
        self.lib = get_library()
        self.pb = get_playback()

        central = Backdrop()
        self.setCentralWidget(central)
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.modes = QStackedWidget()
        cl.addWidget(self.modes)
        full = QWidget()
        full.setObjectName("transparent")
        self.modes.addWidget(full)
        self.mini = MiniPlayer()
        self.mini.restore.connect(self.toggle_mini)
        self.mini.pinToggled.connect(lambda on: set_topmost(self, on) if self.property("mini") else None)
        self.modes.addWidget(self.mini)

        outer = QVBoxLayout(full)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title_bar = TitleBar(self)
        self.title_bar.native = self.native_frame
        outer.addWidget(self.title_bar)
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.rail = Rail(self)
        mid.addWidget(self.rail)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(12, 6, 8, 6)
        bl.setSpacing(10)
        self.banner_ic = QLabel()
        bl.addWidget(self.banner_ic)
        self.banner_txt = label("", wrap=True)
        bl.addWidget(self.banner_txt, 1)
        self.banner_btns = QHBoxLayout()
        bl.addLayout(self.banner_btns)
        bw = QWidget()
        bw.setObjectName("transparent")
        bwl = QVBoxLayout(bw)
        bwl.setContentsMargins(12, 10, 12, 0)
        bwl.addWidget(self.banner)
        self.banner_wrap = bw
        bw.hide()
        right.addWidget(bw)
        # the screens, with the Now playing panel beside them; the full-screen player covers both
        self.layers = QStackedWidget()
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(5)
        self.stack = QStackedWidget()
        self.songs_page = SongsPage()
        self.albums_page = AlbumsPage()
        self.artists_page = ArtistsPage()
        self.genres_page = GenresPage()
        self.playlists_page = PlaylistsPage()
        self.folders_page = FoldersPage(self.scan)
        self.tidy_page = TidyPage()
        self.look_page = LookPage()
        self.settings_page = SettingsPage(self.scan)
        self.pages = {"songs": self.songs_page, "albums": self.albums_page, "artists": self.artists_page,
                      "genres": self.genres_page, "playlists": self.playlists_page, "folders": self.folders_page,
                      "tidy": self.tidy_page, "look": self.look_page, "settings": self.settings_page}
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.split.addWidget(self.stack)
        self.side = NowPlayingPanel()
        self.side.closed.connect(lambda: self.set_side(False))
        self.split.addWidget(self.side)
        self.split.setStretchFactor(0, 1)
        self.split.setStretchFactor(1, 0)
        self.split.splitterMoved.connect(lambda *_: self.s.set("ui.side_width", self.split.sizes()[1])
                                         if self.side.isVisible() else None)
        self.layers.addWidget(self.split)
        self.full = FullPlayer()
        self.full.closed.connect(lambda: self.show_full(False))
        self.layers.addWidget(self.full)
        right.addWidget(self.layers, 1)
        self.drawer = Drawer()
        self.drawer.closed.connect(lambda: self.toggle_log(False))
        self.drawer.setVisible(False)
        right.addWidget(self.drawer)
        mid.addLayout(right, 1)
        outer.addLayout(mid, 1)
        self.player_bar = PlayerBar()
        self.player_bar.sideToggled.connect(self.set_side)
        self.player_bar.fullRequested.connect(lambda: self.show_full(not self.full.isVisible()))
        self.player_bar.miniRequested.connect(self.toggle_mini)
        outer.addWidget(self.player_bar)
        self.status = StatusBar(self.queue)
        self.status.openLog.connect(lambda: self.toggle_log(True))
        outer.addWidget(self.status)
        self.title_bar.pills.addWidget(self.status.job_pill)
        self.title_bar.pills.addWidget(self.status.id_pill)
        for pl in (self.status.job_pill, self.status.id_pill):
            pl.setProperty("interactive", True)

        self.toast = Toast(central)
        hub.toast.connect(self.toast.show_msg)
        hub.logLine.connect(self._log_line)
        _log.add_listener(lambda lvl, msg: hub.logLine.emit(lvl, msg))
        hub.identifyStatus.connect(self.status.set_identify)
        hub.themeChanged.connect(self._theme_changed)
        hub.goto.connect(self.goto)
        hub.tidyCount.connect(lambda n: self.rail.set_badge("tidy", n))
        self._glow = _GlowFilter(self)
        self._glow_on = False
        self._sync_glow_filter()

        self.identify = IdentifyQueue(on_change=lambda sid: hub.songChanged.emit(sid),
                                      on_status=lambda st: hub.identifyStatus.emit(st),
                                      on_artist=lambda name: hub.artistChanged.emit(name))
        self.status.identify = self.identify
        self._hidden_panels = False
        self._unreachable: list[str] = []
        self.rail.setVisible(not self.s.get("ui.rail_hidden", False))
        self.keys_popup = KeysPopup(self.centralWidget())
        self.keys_popup.closed.connect(lambda: self.rail.btns["keys"].setChecked(False))
        self._keys()
        self.go(self.s.get("ui.page", "songs") if self.s.get("ui.page", "songs") in self.PAGES_KEYS() else "songs")
        self.set_side(bool(self.s.get("ui.side_open", True)), save=False)
        if self.s.get("ui.log_open", False):
            self.toggle_log(True)
        self.pb.restore()
        QTimer.singleShot(300, self._check_banners)
        QTimer.singleShot(700, self._start_scan)

    def PAGES_KEYS(self) -> list[str]:  # noqa: N802
        return [k for k, *_ in self.PAGES] + ["look", "settings"]

    # ---- window frame -------------------------------------------------------------------------------
    def nativeEvent(self, event_type, message):
        r = frameless.native_event(self, event_type, message, self.title_bar)
        if r is not None:
            return r
        return super().nativeEvent(event_type, message)

    def showEvent(self, e):
        super().showEvent(e)
        if self.native_frame and not getattr(self, "_styled", False):
            self._styled = True
            frameless.after_show(self)
        QTimer.singleShot(0, self._fit_side)

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self.title_bar.sync()
            self.title_bar.setVisible(not self.isFullScreen())
        super().changeEvent(e)

    # ---- navigation ------------------------------------------------------------------------------------
    def go(self, key: str) -> None:
        if key not in self.pages:
            return
        if self.full.isVisible():
            self.show_full(False)
        self.stack.setCurrentWidget(self.pages[key])
        if key in self.rail.btns:
            self.rail.btns[key].setChecked(True)
        self.title_bar.screen.setText(f"· {self.TITLES.get(key, key)}")
        if key in [k for k, *_ in self.PAGES]:
            self.s.set("ui.page", key)

    def current_page(self):
        return self.stack.currentWidget()

    def goto(self, kind: str, key) -> None:
        if kind == "artist" and key:
            self.go("artists")
            self.artists_page.open(key)
        elif kind == "album":
            self.go("albums")
            self.albums_page.open(key)
        elif kind == "genre":
            self.go("genres")
            self.genres_page.open(key)
        elif kind == "playlist":
            self.go("playlists")
            self.playlists_page.open_key(key)
        elif kind == "songs":
            self.go("songs")
            if isinstance(key, dict):
                self.songs_page.show_filter(key)
        elif kind in self.pages:
            self.go(kind)

    def set_side(self, on: bool, save: bool = True) -> None:
        self.side.setVisible(on)
        self.player_bar.side.setChecked(on)
        if save:
            self.s.set("ui.side_open", on)
        self._fit_side()

    def _fit_side(self) -> None:
        if self.side.isVisible():
            w = int(self.s.get("ui.side_width", 320))
            total = self.split.width()
            w = max(260, min(w, max(260, total - 640)))
            self.split.setSizes([max(200, total - w), w])

    def show_full(self, on: bool) -> None:
        self.layers.setCurrentIndex(1 if on else 0)
        self.title_bar.screen.setText("· Now playing" if on else
                                      f"· {self.TITLES.get(self._page_key(), '')}")
        self.player_bar.full.setToolTip("Back to the library (P)" if on else "Full-screen player (P)")

    def _page_key(self) -> str:
        cur = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is cur), "")

    def toggle_log(self, on: bool | None = None, tab: str | None = None) -> None:
        on = (not self.drawer.isVisible()) if on is None else on
        self.drawer.setVisible(on)
        if tab:
            self.drawer.show_tab(tab)
        self.rail.btns["log"].setChecked(on)
        self.s.set("ui.log_open", on)

    def _log_line(self, level: str, msg: str) -> None:
        self.drawer.append(msg)
        if level in ("ERROR", "CRITICAL") and not self.drawer.isVisible() and not self.property("mini"):
            self.toggle_log(True, "log")

    # ---- the mini player ------------------------------------------------------------------------------------
    def toggle_mini(self) -> None:
        if not self.property("mini"):
            self._full_geo = self.saveGeometry()
            self._was_max = self.isMaximized()
            self._was_fs = self.isFullScreen()
            if self.isFullScreen() or self.isMaximized():
                self.showNormal()
            self.setProperty("mini", True)
            self.modes.setCurrentIndex(1)
            self.setMinimumSize(300, 88)
            self.resize(370, 92)
            pos = self.s.get("ui.mini_pos") or []
            scr = self.screen().availableGeometry() if self.screen() else None
            if len(pos) == 2 and any(s.availableGeometry().contains(pos[0] + 20, pos[1] + 20)
                                     for s in QGuiApplication.screens()):
                self.move(pos[0], pos[1])
            elif scr is not None:
                self.move(scr.right() - 370 - 24, scr.bottom() - 92 - 24)
            if self.s.get("ui.mini_on_top", True):
                set_topmost(self, True)
            self.toast.hide()
        else:
            self.s.set("ui.mini_pos", [self.x(), self.y()])
            set_topmost(self, False)
            self.setProperty("mini", False)
            self.modes.setCurrentIndex(0)
            self.setMinimumSize(*self.min_size)
            if getattr(self, "_full_geo", None) is not None:
                self.restoreGeometry(self._full_geo)
            if getattr(self, "_was_fs", False):
                self.showFullScreen()
            elif getattr(self, "_was_max", False):
                self.showMaximized()

    # ---- keys ---------------------------------------------------------------------------------------------
    def _keys(self) -> None:
        def sc(seq, fn, always: bool = False, typing_ok: bool = False):
            s = QShortcut(QKeySequence(seq), self)
            s.setContext(Qt.WindowShortcut)

            def run():
                if self.keys_popup.isVisible() and not always:
                    return
                fw = QApplication.focusWidget()
                if not typing_ok and isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)):
                    return
                fn()
            s.activated.connect(run)
            return s

        sc("K", self.toggle_keys, always=True)
        sc("/", self._search)
        sc("Ctrl+F", self._search, typing_ok=True)
        sc("F", self.toggle_full_screen)
        sc("F11", self.toggle_full_screen, typing_ok=True)
        sc("H", self.toggle_panels)
        sc("L", lambda: self.toggle_log())
        sc("M", self.toggle_rail)
        sc("S", lambda: self.go("settings"))
        sc("Ctrl+Z", lambda: undo(None))
        sc("Esc", self._escape, always=True, typing_ok=True)
        pb = self.pb
        sc("Space", pb.toggle)
        sc("Media Play", pb.toggle, typing_ok=True)
        for k in ("Left", "PgUp", "Media Previous"):
            sc(k, pb.previous)
        for k in ("Right", "PgDown", "Media Next"):
            sc(k, pb.next)
        sc("Shift+Left", lambda: pb.skip_by(-10))
        sc("Shift+Right", lambda: pb.skip_by(10))
        sc("-", lambda: (pb.nudge_volume(-5), self.player_bar.show_volume_tip()))
        for k in ("=", "+"):
            sc(k, lambda: (pb.nudge_volume(5), self.player_bar.show_volume_tip()))
        sc("0", lambda: (pb.toggle_mute(), self.player_bar.show_volume_tip()))
        sc("X", lambda: pb.set_shuffle(not pb.shuffle))
        sc("R", pb.cycle_repeat)
        sc("P", lambda: self.show_full(not self.full.isVisible()))
        sc("Ctrl+M", self.toggle_mini, typing_ok=True)
        sc("J", self._jump)
        sc("Q", lambda: pb.add(self._targets(False)))
        sc("N", lambda: pb.play_next(self._targets(False)))
        sc("V", lambda: actions.toggle_favourite(self._targets()))
        sc("[", lambda: actions.nudge_rating(self._targets(), -1))
        sc("]", lambda: actions.nudge_rating(self._targets(), 1))
        sc("E", lambda: actions.edit_info(self, self._targets()))
        sc("Delete", self._delete)
        sc("Shift+Delete", lambda: actions.recycle(self, self._targets(False)))
        sc("Home", lambda: self._page_call("key_first"))
        sc("End", lambda: self._page_call("key_last"))

    def _targets(self, playing_too: bool = True) -> list[int]:
        """The songs a key acts on: the chosen ones on this screen, else (for some keys) the song playing."""
        ids: list[int] = []
        if not self.full.isVisible():
            p = self.current_page()
            fw = QApplication.focusWidget()
            from .queue_view import QueueList
            if isinstance(fw, QueueList):
                ids = [self.pb.queue[i] for i in fw.queue_indexes() if 0 <= i < len(self.pb.queue)]
            elif hasattr(p, "target_ids"):
                ids = p.target_ids()
        if not ids and playing_too and self.pb.current_id() is not None:
            ids = [self.pb.current_id()]
        return ids

    def _delete(self) -> None:
        from .queue_view import QueueList
        fw = QApplication.focusWidget()
        if isinstance(fw, QueueList):
            self.pb.remove_at(fw.queue_indexes())
            return
        ids = self._targets(False)
        if ids:
            actions.remove_from_library(self, ids)

    def _page_call(self, name: str) -> None:
        p = self.current_page()
        fn = getattr(p, name, None)
        if fn:
            fn()

    def _jump(self) -> None:
        r = self.pb.current_row()
        if r is None:
            return
        if r["album"]:
            actions.go_album(r)
        elif r["artist"]:
            actions.go_artist(r)

    def _search(self) -> None:
        if self.full.isVisible():
            self.show_full(False)
        p = self.current_page()
        if hasattr(p, "focus_search") and p not in (self.look_page, self.settings_page, self.tidy_page):
            p.focus_search()
        else:
            self.go("songs")
            self.songs_page.focus_search()

    def toggle_full_screen(self) -> None:
        if self.property("mini"):
            return
        if self.isFullScreen():
            self.showMaximized() if getattr(self, "_fs_was_max", False) else self.showNormal()
        else:
            self._fs_was_max = self.isMaximized()
            self.showFullScreen()

    def toggle_panels(self) -> None:
        self._hidden_panels = not self._hidden_panels
        hide = self._hidden_panels
        self.rail.setVisible(not hide and not self.s.get("ui.rail_hidden", False))
        self.side.setVisible(not hide and bool(self.s.get("ui.side_open", True)))
        if hide and self.drawer.isVisible():
            self.drawer.setVisible(False)
        if not hide:
            self._fit_side()

    def toggle_rail(self) -> None:
        on = not self.rail.isVisible()
        self.rail.setVisible(on)
        self.s.set("ui.rail_hidden", not on)

    def toggle_keys(self) -> None:
        if self.property("mini"):
            return
        self.keys_popup.toggle()
        self.rail.btns["keys"].setChecked(self.keys_popup.isVisible())

    def _escape(self) -> None:
        fw = QApplication.focusWidget()
        if isinstance(fw, QLineEdit) and fw.text():
            fw.clear()
            return
        if isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit)):
            fw.clearFocus()
            return
        if self.keys_popup.isVisible():
            self.toggle_keys()
        elif self.property("mini"):
            self.toggle_mini()
        elif self.full.isVisible():
            self.show_full(False)
        elif hasattr(self.current_page(), "key_back") and self.current_page().key_back():
            pass
        elif self._hidden_panels:
            self.toggle_panels()
        elif self.isFullScreen():
            self.toggle_full_screen()
        elif self.drawer.isVisible():
            self.toggle_log(False)

    # ---- theme -----------------------------------------------------------------------------------------------
    def _theme_changed(self) -> None:
        self.rail.restyle()
        self.title_bar.sync()
        for w in (self.player_bar, self.side, self.full, self.mini):
            w.theme_changed()
        for p in self.pages.values():
            if hasattr(p, "theme_changed"):
                p.theme_changed()
        for b in self.findChildren(QPushButton, "primary"):
            apply_glow(b)
        self._sync_glow_filter()
        self.status.refresh()
        self.centralWidget().update()

    def _sync_glow_filter(self) -> None:
        on = bool(theme.T.get("glow"))
        if on != self._glow_on:
            app = QApplication.instance()
            (app.installEventFilter if on else app.removeEventFilter)(self._glow)
            self._glow_on = on

    # ---- scanning ------------------------------------------------------------------------------------------
    def _start_scan(self) -> None:
        db = get_db()
        if not db.folders():
            self.show_banner("folder", "Add the folders your music is in: on any drive, as many as you like. Gekko "
                             "finds the songs, and checks for new ones every time it opens.",
                             [("Add a music folder…", self.folders_page.add_folder)])
            return
        if self.s.get("library.scan_on_start", True):
            self.scan(None, quiet=True)

    def scan(self, folders: list[str] | None = None, quiet: bool = False) -> None:
        if any(j.name.startswith("Checking") for j in ([self.queue.current] + self.queue.waiting) if j):
            if not quiet:
                hub.toast.emit("Already checking your folders.", "info")
            return
        what = (f"Checking {Path(folders[0]).name}" if folders and len(folders) == 1 else
                "Checking your music folders")
        if not folders and not get_db().folders():
            hub.toast.emit("Add a music folder first (Folders > Add folder).", "info")
            return
        self.hide_banner()

        def done(res):
            hub.songsReset.emit()
            hub.foldersChanged.emit()
            self.identify.poke()
            self._unreachable = res.get("unreachable") or []
            self._check_banners()
            if res.get("added") or res.get("missing") or not quiet:
                hub.toast.emit(scanjob.describe(res), "ok")

        self.queue.add(what, scanjob.scan, folders, pausable=True, on_done=done, sized=False,
                       done_text=scanjob.describe)

    # ---- banners --------------------------------------------------------------------------------------------
    def show_banner(self, icon_name: str, text: str, actions_: list[tuple[str, object]]) -> None:
        from .widgets import clear_layout
        clear_layout(self.banner_btns)
        self.banner_ic.setPixmap(theme.pixmap(icon_name, 16, C["acc"]))
        self.banner_txt.setText(text)
        for i, (txt, fn) in enumerate(actions_):
            b = button(txt, None, "primary" if i == len(actions_) - 1 else None)
            b.clicked.connect(fn)
            self.banner_btns.addWidget(b)
        self.banner.show()
        self.banner_wrap.show()

    def hide_banner(self) -> None:
        self.banner.hide()
        self.banner_wrap.hide()

    def _check_banners(self) -> None:
        if safemode.active():
            self.show_banner("warn", "Gekko closed unexpectedly twice in a row, so it started in safe mode: playback "
                             "is off. The crash log is in the logs folder.",
                             [("Open logs", lambda: __import__("gekko.ui.widgets", fromlist=["x"]).open_in_explorer(
                                 str(_log.log_dir()))), ("Turn playback back on", self._leave_safe_mode)])
            return
        miss = storage.missing_places()
        unreach = [f for f in self._unreachable if f in get_db().folders()]
        if miss or unreach:
            parts = []
            drives = sorted({str(Path(p).anchor) for _k, _l, p in miss} | {str(Path(f).anchor) for f in unreach})
            if unreach:
                n = sum(len(self.lib.folder_songs(f)) for f in unreach)
                parts.append(f"{n:,} songs in {', '.join(unreach)} can't be played right now; they stay in the "
                             "library")
            if miss:
                parts.append("these can't be reached: " + ", ".join(f"{lab.split(':')[0]} ({p})"
                                                                   for _k, lab, p in miss))
            self.show_banner("drive", f"{' and '.join(d for d in drives if d) or 'A drive'} isn't connected, so "
                             + "; ".join(parts) + ".", [("Choose a new place", lambda: (
                                 self.go("settings"), self.settings_page.focus_storage())) if miss else
                                 ("Open Folders", lambda: self.go("folders")), ("Retry", self._retry_drives)])
        elif get_db().folders():
            self.hide_banner()

    def _retry_drives(self) -> None:
        self._unreachable = []
        self.hide_banner()
        self.scan(None, quiet=True)

    def _leave_safe_mode(self) -> None:
        if dialogs.ask(self, "Leave safe mode", "Turn playback back on? Gekko restarts to do that.", "Restart Gekko"):
            safemode.turn_off()
            self.restart()

    def restart(self) -> None:
        from PySide6.QtCore import QProcess
        args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
        QProcess.startDetached(sys.executable, args)
        self.close()

    # ---- closing ----------------------------------------------------------------------------------------------
    def closeEvent(self, e):
        if self.queue.busy() and not self.queue.current.name.startswith("Checking"):
            if not dialogs.ask(self, "Stop and close?", f"{self.queue.current.name} is still running"
                               + (f", with {len(self.queue.waiting)} more queued" if self.queue.waiting else "")
                               + ". Finished work is kept.", "Stop and close", "Keep working"):
                e.ignore()
                return
        if self.queue.busy():
            self.queue.stop_all()
        if self.property("mini"):
            self.s.set("ui.mini_pos", [self.x(), self.y()])
            if getattr(self, "_full_geo", None) is not None:
                self.s.set("ui.geometry", bytes(self._full_geo.toBase64()).decode())
        else:
            self.s.set("ui.geometry", bytes(self.saveGeometry().toBase64()).decode())
        self.identify.stop()
        try:
            self.pb.shutdown()
        except Exception:
            pass
        safemode.clean_exit()
        super().closeEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.place()
