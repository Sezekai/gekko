"""The sound itself: libmpv (bundled) for gapless playback of every format, with Qt Multimedia as a fallback and a
silent stand-in when there's no screen (tests, safe mode).

The engine only knows files. It plays one and, for gapless playback, has the next one loaded behind it; the
playback controller (playback.py) decides what comes next."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from ..core import log as _log
from .player import _load_mpv

log = _log.get("engine")


class _Bridge(QObject):
    failed = Signal(str)
    pos = Signal(float)
    dur = Signal(float)
    pause = Signal(bool)
    eof = Signal()
    path = Signal(str)
    idle = Signal(bool)


class AudioEngine(QObject):
    positionChanged = Signal(float)
    durationChanged = Signal(float)
    playingChanged = Signal(bool)
    advanced = Signal()           # the preloaded next song has started (gapless)
    finished = Signal()           # the song ended and nothing was loaded after it
    failed = Signal(str)          # the file couldn't be played

    def __init__(self, parent=None, gapless: bool = True):
        super().__init__(parent)
        self.gapless = gapless
        self._b = _Bridge()
        self._b.pos.connect(self._on_pos)
        self._b.dur.connect(self._on_dur)
        self._b.pause.connect(self._on_pause)
        self._b.eof.connect(self._on_eof)
        self._b.path.connect(self._on_path)
        self._b.failed.connect(self.failed.emit)
        self._b_failed = self._b.failed
        self._pos = 0.0
        self._dur = 0.0
        self._paused = True
        self._volume = 80
        self._muted = False
        self._gain = 0.0
        self._cur: str | None = None
        self._next: str | None = None
        self._next_gain = 0.0
        self._last_emit = 0.0
        self._ended_sent = False
        self._mpv = None
        self._qt = None
        from PySide6.QtGui import QGuiApplication
        headless = bool(os.environ.get("GEKKO_NO_PLAYBACK")) or QGuiApplication.platformName() == "offscreen"
        self.backend = "none"
        if not headless and _load_mpv() is not None:
            try:
                self._init_mpv()
                self.backend = "mpv"
            except Exception as e:
                log.warning("mpv failed to start (%s); using Qt playback", e)
                self._mpv = None
        if self._mpv is None and not headless:
            if self._init_qt():
                self.backend = "qt"
        if self.backend == "none":
            self._fake = QTimer(self, interval=250, timeout=self._fake_tick)
            self._fake_speed = float(os.environ.get("GEKKO_FAKE_SPEED", "1") or 1)
        log.info("playback: %s", self.backend)

    # ---- mpv --------------------------------------------------------------------------------------
    def _init_mpv(self) -> None:
        mpv = _load_mpv()
        opts = dict(idle="yes", keep_open="yes", input_default_bindings=False, input_vo_keyboard=False, osc=False,
                    loglevel="warn", volume=self._volume, pause=True, video="no", audio_display="no", vo="null",
                    force_window="no", gapless_audio="yes" if self.gapless else "no",
                    prefetch_playlist="yes" if self.gapless else "no")
        if os.environ.get("GEKKO_MPV_AO"):           # tests: no sound card
            opts["ao"] = os.environ["GEKKO_MPV_AO"]
        extra = dict(audio_fallback_to_null=True, load_scripts=False, ytdl=False, config=False, terminal=False,
                     input_terminal=False, load_stats_overlay=False, load_osd_console=False,
                     load_auto_profiles=False, load_select=False, load_commands=False, load_positioning=False,
                     load_context_menu=False)
        m = None
        for _ in range(len(extra) + 2):
            try:
                m = mpv.MPV(**opts, **extra)
                break
            except Exception as e:
                msg = str(e).replace("-", "_")
                bad = [k for k in list(extra) + list(opts) if k in msg and k not in ("volume", "idle")]
                if not bad:
                    extra = {}
                    continue
                for k in bad:
                    extra.pop(k, None)
                    opts.pop(k, None)
        if m is None:
            m = mpv.MPV(idle="yes", keep_open="yes", video="no")
        m.observe_property("time-pos", lambda _n, v: self._b.pos.emit(float(v or 0.0)))
        m.observe_property("duration", lambda _n, v: self._b.dur.emit(float(v or 0.0)))
        m.observe_property("pause", lambda _n, v: self._b.pause.emit(bool(v)))
        m.observe_property("eof-reached", lambda _n, v: self._b.eof.emit() if v else None)
        m.observe_property("path", lambda _n, v: self._b.path.emit(str(v or "")))

        @m.event_callback("end-file")
        def _end(ev):
            try:
                d = ev.as_dict() if hasattr(ev, "as_dict") else {}
                ed = d if not isinstance(d.get("event"), dict) else d["event"]
                reason = ed.get("reason")
                if reason in (b"error", "error", 4):
                    err = ed.get("file_error") or ed.get("error") or "the file can't be played"
                    self._b_failed.emit(err.decode() if isinstance(err, bytes) else str(err))
            except Exception:
                pass

        self._mpv = m
        self._has_gain_prop = True

    def _mpv_gain(self, db: float) -> None:
        m = self._mpv
        if m is None:
            return
        if self._has_gain_prop:
            try:
                m["volume-gain"] = float(db)
                return
            except Exception:
                self._has_gain_prop = False
        try:
            m["af"] = f"volume={db:.2f}dB" if abs(db) >= 0.05 else ""
        except Exception:
            pass

    # ---- Qt fallback -----------------------------------------------------------------------------
    def _init_qt(self) -> bool:
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError as e:
            log.error("no playback available: mpv failed and Qt Multimedia isn't included (%s)", e)
            return False
        self._qt = QMediaPlayer(self)
        self._qt_out = QAudioOutput(self)
        self._qt.setAudioOutput(self._qt_out)
        self._qt.positionChanged.connect(lambda ms: self._b.pos.emit(ms / 1000.0))
        self._qt.durationChanged.connect(lambda ms: self._b.dur.emit(ms / 1000.0))
        self._qt.playbackStateChanged.connect(
            lambda st: self._b.pause.emit(st != QMediaPlayer.PlaybackState.PlayingState))
        self._qt.mediaStatusChanged.connect(
            lambda st: self._b.eof.emit() if st == QMediaPlayer.MediaStatus.EndOfMedia else None)
        self._qt.errorOccurred.connect(lambda _e, msg: self.failed.emit(msg))
        self._apply_volume()
        return True

    # ---- the stand-in -----------------------------------------------------------------------------
    def _fake_tick(self) -> None:
        if self._paused or self._cur is None:
            return
        self._b.pos.emit(min(self._dur, self._pos + 0.25 * self._fake_speed))
        if self._dur and self._pos >= self._dur - 1e-3:
            self._b.eof.emit()

    # ---- API ------------------------------------------------------------------------------------------
    def load(self, path: str, play: bool = True, start: float = 0.0, gain: float = 0.0,
             duration_hint: float = 0.0) -> None:
        """Play a file now (whatever was playing or preloaded stops)."""
        self._cur, self._next = path, None
        self._pos, self._dur = start, duration_hint
        self._gain = gain
        self._ended_sent = False
        if self._mpv is not None:
            m = self._mpv
            self._mpv_gain(gain)
            m["start"] = f"{start:.3f}" if start else "none"
            try:
                m.command("loadfile", path, "replace")
            except Exception as e:
                self.failed.emit(str(e))
                return
            # after loadfile: at the end of the last song mpv has paused itself (keep-open), and setting this
            # first would be undone by that
            m["pause"] = not play
        elif self._qt is not None:
            self._apply_volume()
            self._qt.setSource(QUrl.fromLocalFile(path))
            if start:
                self._qt.setPosition(int(start * 1000))
            if play:
                self._qt.play()
            else:
                self._qt.pause()
        else:
            self._b.dur.emit(duration_hint)
            self._b.pos.emit(start)
            self._b.pause.emit(not play)
            if play:
                self._fake.start()

    def set_next(self, path: str | None, gain: float = 0.0, duration_hint: float = 0.0) -> None:
        """What plays straight after the current song (gapless where the engine can)."""
        if path == self._next and abs(gain - self._next_gain) < 0.01:
            return
        self._next, self._next_gain, self._next_dur = path, gain, duration_hint
        m = self._mpv
        if m is None or not self.gapless:
            return
        try:
            pl = m.playlist or []
            cur = os.path.normcase(self._cur or "")
            idx = next((i for i, e in enumerate(pl) if e.get("current") or e.get("playing")), None)
            if idx is None:
                idx = next((i for i, e in enumerate(pl) if os.path.normcase(e.get("filename", "")) == cur), 0)
            for i in range(len(pl) - 1, idx, -1):
                m.command("playlist-remove", i)
            if path:
                m.command("loadfile", path, "append")
        except Exception as e:
            log.info("couldn't preload the next song: %s", e)

    def _on_path(self, p: str) -> None:
        if not p or self._mpv is None:
            return
        if self._cur and os.path.normcase(p) == os.path.normcase(self._cur):
            try:
                self._mpv["start"] = "none"     # a start position is for this song only, not the next
            except Exception:
                pass
        if self._next and os.path.normcase(p) == os.path.normcase(self._next) and \
                os.path.normcase(p) != os.path.normcase(self._cur or ""):
            # the preloaded song has started: it's now the current one
            self._cur, self._next = self._next, None
            self._gain = self._next_gain
            self._mpv_gain(self._gain)
            self._ended_sent = False
            try:
                pl = self._mpv.playlist or []
                pos = next((i for i, e in enumerate(pl) if e.get("current") or e.get("playing")), 0)
                for i in range(pos - 1, -1, -1):
                    self._mpv.command("playlist-remove", i)
            except Exception:
                pass
            self.advanced.emit()

    def _on_eof(self) -> None:
        if self._ended_sent:
            return
        if self._mpv is not None and self._next and self.gapless:
            return                         # mpv moves on to the preloaded song by itself
        self._ended_sent = True
        if self._next:
            nxt, g, d = self._next, self._next_gain, getattr(self, "_next_dur", 0.0)
            self.load(nxt, True, 0.0, g, d)
            self.advanced.emit()
        else:
            if self._qt is None and self._mpv is None:
                self._fake.stop()
                self._b.pause.emit(True)
            self.finished.emit()

    def unload(self) -> None:
        self._cur = self._next = None
        if self._mpv is not None:
            try:
                self._mpv.command("stop")
            except Exception:
                pass
        elif self._qt is not None:
            self._qt.stop()
            self._qt.setSource(QUrl())
        else:
            self._fake.stop()
        self._b.pos.emit(0.0)
        self._b.dur.emit(0.0)
        self._b.pause.emit(True)

    def play(self) -> None:
        if self._cur is None:
            return
        if self._dur and self._pos >= self._dur - 0.3:
            self.seek(0)
        if self._mpv is not None:
            self._mpv["pause"] = False
        elif self._qt is not None:
            self._qt.play()
        else:
            self._b.pause.emit(False)
            self._fake.start()

    def pause(self) -> None:
        if self._mpv is not None:
            self._mpv["pause"] = True
        elif self._qt is not None:
            self._qt.pause()
        else:
            self._b.pause.emit(True)

    def toggle(self) -> None:
        self.pause() if self.is_playing() else self.play()

    def is_playing(self) -> bool:
        return not self._paused and self._cur is not None

    def seek(self, s: float) -> None:
        s = max(0.0, min(s, self._dur - 0.05 if self._dur else s))
        self._ended_sent = False
        if self._mpv is not None:
            try:
                self._mpv.command("seek", f"{s:.3f}", "absolute+exact")
            except Exception:
                pass
        elif self._qt is not None:
            self._qt.setPosition(int(s * 1000))
        else:
            self._b.pos.emit(s)

    def set_volume(self, v: int) -> None:
        self._volume = max(0, min(100, int(v)))
        self._apply_volume()

    def set_muted(self, on: bool) -> None:
        self._muted = on
        self._apply_volume()

    def set_gain(self, db: float) -> None:
        self._gain = db
        if self._mpv is not None:
            self._mpv_gain(db)
        else:
            self._apply_volume()

    def _apply_volume(self) -> None:
        v = 0 if self._muted else self._volume
        if self._mpv is not None:
            try:
                self._mpv["volume"] = v
            except Exception:
                pass
        elif self._qt is not None:
            lin = (v / 100) * (10 ** (self._gain / 20))
            self._qt_out.setVolume(max(0.0, min(1.0, lin)))

    def position(self) -> float:
        return self._pos

    def duration(self) -> float:
        return self._dur

    def current_path(self) -> str | None:
        return self._cur

    # ---- reports from the backend ---------------------------------------------------------------
    def _on_pos(self, v: float) -> None:
        self._pos = v
        now = time.monotonic()
        if now - self._last_emit >= 0.05:
            self._last_emit = now
            self.positionChanged.emit(v)

    def _on_dur(self, v: float) -> None:
        if v > 0 or self.backend != "none":
            self._dur = v
            self.durationChanged.emit(v)

    def _on_pause(self, paused: bool) -> None:
        if paused != self._paused:
            self._paused = paused
            self.playingChanged.emit(not paused)

    def shutdown(self) -> None:
        if self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception:
                pass
            self._mpv = None
        if self._qt is not None:
            self._qt.stop()
