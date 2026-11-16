"""Background lookups, one at a time so the window never waits:

1. Songs with no title or artist are identified from their sound: Shazam first, AcoustID and AudioTag as backups,
   then album, year and genre (iTunes, MusicBrainz) and lyrics (LRCLIB).
2. Songs that already have a title and artist get what they're missing by name: lyrics, cover art, genre, year.
3. Artists get their profile: bio and photo.

Whatever is found goes into the library straight away. The files only change through Tidy up, after review."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..analysis import audio_features as af
from ..analysis.fingerprint import fingerprint_compressed
from ..core import log as _log
from ..core.db import get_db
from ..core.settings import get_settings
from ..lookup import services
from ..media import ff

log = _log.get("identify")

SR = 16000
SEG = 12.0


def pick_segments(y: np.ndarray, sr: int, n: int = 3) -> list[float]:
    """Start times of up to n loud 12 s segments, spread across the clip."""
    dur = len(y) / sr
    if dur <= SEG + 1:
        return [0.0]
    env = af.loudness_envelope(y, sr, 1.0)
    win = int(SEG)
    c = np.convolve(env, np.ones(win) / win, mode="valid")
    out: list[float] = []
    zones = np.array_split(np.arange(len(c)), n)
    for z in zones:
        if len(z):
            out.append(float(z[int(np.argmax(c[z]))]))
    # Loudest first.
    out.sort(key=lambda t: -c[int(t)])
    return out


def _merge(dst: dict, src: dict | None, keys: tuple[str, ...], overwrite: bool = False) -> None:
    if not src:
        return
    for k in keys:
        v = src.get(k)
        if v and (overwrite or not dst.get(k)):
            dst[k] = v


METHODS = ("shazam", "deep", "acoustid", "audiotag")
TEXT_KEYS = ("title", "artist", "album", "genre", "year")
# Speeds to play a clip at when it may be a sped-up or slowed-down edit: 0.8 undoes a 1.25x speed-up.
SPEEDS = (0.8, 0.87, 0.92, 1.1, 1.2)
_audiotag_off = threading.Event()      # set when AudioTag says the key is bad or the free time is used up


def reset_audiotag() -> None:
    """Try AudioTag again (after a new key, or a new month)."""
    _audiotag_off.clear()


def respeed(y: np.ndarray, sr: int, start: float, speed: float, seconds: float = SEG) -> np.ndarray | None:
    """`seconds` of audio from `start`, played at `speed` (pitch moves too, like a sped-up edit)."""
    n_in = int(seconds * speed * sr)
    seg = y[int(start * sr): int(start * sr) + n_in]
    if len(seg) < 4 * sr:
        return None
    n_out = int(len(seg) / speed)
    return np.interp(np.linspace(0, len(seg) - 1, n_out), np.arange(len(seg)), seg).astype(np.float32)


def methods_for(deep: bool = False) -> list[str]:
    """Lookups to try, in order."""
    s = get_settings()
    out = []
    if s.get("identify.shazam", True):
        out.append("shazam")
        if deep:
            out.append("deep")
    if (s.get("identify.acoustid_key") or "").strip():
        out.append("acoustid")
    if (s.get("identify.audiotag_key") or "").strip():
        out.append("audiotag")
    return out


def _guess_from_name(stem: str) -> dict:
    """'Artist - Title' in a file name, used only when nothing else knows the song."""
    import re
    stem = re.sub(r"^\s*\d{1,3}[\s._-]+", "", stem)              # "01 - ", "07. "
    stem = re.sub(r"\s*[\[(](official|lyrics?|audio|video|hd|hq|mv)[^\])]*[\])]", "", stem, flags=re.I)
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        if a.strip() and t.strip() and not re.fullmatch(r"[\d\s._-]+", a):
            return {"artist": a.strip(), "title": t.strip()}
    return {}


def identify_song(song_id: int, cancel: threading.Event | None = None, deep: bool = False) -> dict:
    db = get_db()
    s = get_settings()
    row = db.song(song_id)
    if row is None:
        return {}
    path = Path(row["path"])
    methods = methods_for(deep)
    found_by = None
    try:
        y = ff.decode_audio(path, SR, duration=600)
    except Exception as e:
        db.update_song(song_id, id_status="error")
        log.warning("could not read %s: %s", path, e)
        return {}
    info: dict[str, Any] = {}
    tries = errors = 0

    def stop_check():
        if cancel is not None and cancel.is_set():
            raise InterruptedError()

    def try_shazam(seg, how: str) -> bool:
        nonlocal tries, errors, info, found_by
        stop_check()
        tries += 1
        try:
            r = services.shazam(seg, SR, cancel)
        except InterruptedError:
            raise
        except Exception as e:
            log.info("Shazam failed for %s: %s", path.name, e)
            errors += 1
            return False
        if r and r.get("title"):
            info = r
            found_by = how
            return True
        return False

    if "shazam" in methods:
        starts = pick_segments(y, SR, 5 if deep else 3)
        for start in starts:
            if try_shazam(y[int(start * SR): int((start + SEG) * SR)], "Shazam"):
                break
        if not info and "deep" in methods:
            for start in starts[:1]:
                for sp in SPEEDS:
                    seg = respeed(y, SR, start, sp)
                    if seg is not None and try_shazam(seg, f"Shazam (song is {1 / sp:.2f}× its usual speed)"):
                        break
                if info:
                    break
    key = (s.get("identify.acoustid_key") or "").strip()
    if not info and "acoustid" in methods and key:
        stop_check()
        fpc = fingerprint_compressed(str(path))
        if fpc:
            tries += 1
            try:
                r = services.acoustid(fpc[0], fpc[1], key)
            except Exception as e:
                log.info("AcoustID failed for %s: %s", path.name, e)
                errors += 1
                r = None
            if r and r.get("title"):
                info = r
                found_by = "AcoustID"
    atkey = (s.get("identify.audiotag_key") or "").strip()
    if not info and "audiotag" in methods and atkey and not _audiotag_off.is_set():
        start = pick_segments(y, SR, 1)[0]
        stop_check()
        tries += 1
        try:
            r = services.audiotag(y[int(start * SR): int((start + services.AUDIOTAG_SECONDS) * SR)], SR, atkey, cancel)
        except InterruptedError:
            raise
        except services.AudioTagError as e:
            log.warning("%s", e)
            errors += 1
            if e.stop:
                _audiotag_off.set()
            r = None
        except Exception as e:
            log.info("AudioTag failed for %s: %s", path.name, e)
            errors += 1
            r = None
        if r and r.get("title"):
            info = r
            found_by = "AudioTag"
    if not info:
        upd: dict[str, Any] = {"id_status": "error" if tries and errors == tries else "not_found"}
        # Nothing recognised it: the file name may still say who it is
        if upd["id_status"] == "not_found" and not row["title"]:
            g = _guess_from_name(path.stem)
            if g:
                upd.update(g)
                upd["file_synced"] = 0
                upd["id_source"] = "file name"
        db.update_song(song_id, **upd)
        return {}
    # Keep what the file already says for anything the lookup didn't find
    for k in TEXT_KEYS:
        if not info.get(k) and row[k]:
            info[k] = row[k]
    _extras(info, row)
    upd = {k: info[k] for k in ("title", "artist", "album", "genre", "year", "cover_url", "shazam_key", "isrc",
                                "mbid", "lyrics_plain", "lyrics_synced") if info.get(k)}
    upd["id_status"] = "found"
    upd["id_source"] = found_by
    upd["info_status"] = "done"
    if any((upd.get(k) or None) != (row[k] or None) for k in TEXT_KEYS if k in upd):
        upd["file_synced"] = 0
    db.update_song(song_id, **upd)
    _save_cover(song_id, row, upd.get("cover_url"))
    log.info("identified %s as %s - %s (%s)", path.name, upd.get("artist"), upd.get("title"), found_by)
    return upd


def _extras(info: dict, row) -> tuple[int, int]:
    """Album, year, genre, cover and lyrics for a song whose title and artist are known. Returns (tried, failed)."""
    s = get_settings()
    title, artist = info.get("title"), info.get("artist")
    tried = failed = 0
    if not title:
        return 0, 0
    need_meta = not (info.get("album") and info.get("genre") and info.get("year")) or \
        (not row["has_art"] and not info.get("cover_url") and s.get("identify.covers", True))
    if s.get("identify.itunes", True) and need_meta:
        tried += 1
        try:
            _merge(info, services.itunes(title, artist), ("genre", "album", "year", "cover_url"))
        except Exception as e:
            failed += 1
            log.info("iTunes lookup failed: %s", e)
    if s.get("identify.musicbrainz", True) and not (info.get("genre") and info.get("year")):
        tried += 1
        try:
            mb = services.musicbrainz(info.get("mbid"), title, artist)
            _merge(info, mb, ("mbid", "album", "year", "genre"))
        except Exception as e:
            failed += 1
            log.info("MusicBrainz lookup failed: %s", e)
    if s.get("identify.lyrics", True) and not (row["lyrics_synced"] or "").strip():
        tried += 1
        try:
            ly = services.lrclib(title, artist, info.get("album"), row["duration"] or None)
            if ly:
                if ly.get("lyrics_synced") or not (row["lyrics_plain"] or "").strip():
                    info["lyrics_plain"] = ly.get("lyrics_plain") or row["lyrics_plain"]
                    info["lyrics_synced"] = ly.get("lyrics_synced")
        except Exception as e:
            failed += 1
            log.info("LRCLIB lookup failed: %s", e)
    return tried, failed


def _save_cover(song_id: int, row, url: str | None) -> None:
    if not url or row["has_art"] == 1 or not get_settings().get("identify.covers", True):
        return
    dest = get_settings().cache("covers") / f"{song_id}.jpg"
    if services.download(url, dest):
        from ..media import covers
        covers.forget(song_id)


def info_song(song_id: int) -> dict:
    """Extras for a song that already has a title and artist: missing lyrics, cover, genre and year."""
    db = get_db()
    row = db.song(song_id)
    if row is None:
        return {}
    info: dict[str, Any] = {k: row[k] for k in TEXT_KEYS}
    tried, failed = _extras(info, row)
    if tried and failed == tried:
        raise ConnectionError("every lookup failed")
    upd: dict[str, Any] = {"info_status": "done"}
    for k in ("album", "genre", "year"):
        if info.get(k) and not row[k]:
            upd[k] = info[k]
            upd["file_synced"] = 0
    for k in ("lyrics_plain", "lyrics_synced", "cover_url", "mbid"):
        if info.get(k) and info.get(k) != row[k]:
            upd[k] = info[k]
    db.update_song(song_id, **upd)
    _save_cover(song_id, row, upd.get("cover_url"))
    return upd


class IdentifyQueue:
    """One background worker for every lookup: identifying, extras, then artists.

    Starts after `start_delay` seconds so it never competes with the app opening. on_status gets a dict:
    pending, current, done (this run), started (time), busy, paused. on_change(song_id) or on_artist(name) report
    what changed."""

    def __init__(self, on_change: Callable[[int], None] | None = None,
                 on_status: Callable[[dict], None] | None = None,
                 on_artist: Callable[[str], None] | None = None, start_delay: float = 4.0):
        self.on_change = on_change
        self.on_status = on_status
        self.on_artist = on_artist
        self.start_delay = start_delay
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._paused = False
        self._first: list[tuple[str, Any]] = []     # asked for now: ("song", id) / ("artist", name) / ("deep", id)
        self._lock = threading.Lock()
        self.current = ""
        self.done = 0
        self.started = 0.0
        self._t = threading.Thread(target=self._run, daemon=True, name="lookups")
        self._t.start()

    def poke(self) -> None:
        self._wake.set()

    def first(self, kind: str, what: Any) -> None:
        """Do this next (the song that's playing, an artist page that's open, Identify again)."""
        with self._lock:
            if (kind, what) not in self._first:
                self._first.insert(0, (kind, what))
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._wake.set()
        self._status(self.current)

    def stop(self) -> None:
        self._stop.set()
        self._cancel.set()
        self._wake.set()

    @staticmethod
    def _auto() -> bool:
        return bool(get_settings().get("identify.auto", True))

    def pending(self) -> int:
        db = get_db()
        n = len(self._first)
        if self._auto():
            r = db.one("SELECT (SELECT COUNT(*) FROM songs WHERE id_status='pending' AND missing=0) + "
                       "(SELECT COUNT(*) FROM songs WHERE info_status='pending' AND id_status!='pending' AND missing=0)")
            n += int(r[0]) if r else 0
        if get_settings().get("playback.level", True):
            r = db.one("SELECT COUNT(*) FROM songs WHERE lufs IS NULL AND missing=0")
            n += int(r[0]) if r else 0
        return n

    def _status(self, msg: str) -> None:
        self.current = msg
        if self.on_status and not self._stop.is_set():
            try:
                self.on_status({"pending": self.pending(), "current": msg, "done": self.done,
                                "started": self.started, "busy": bool(msg), "paused": self._paused,
                                "phase": "Measuring" if msg.startswith("Measuring") else "Looking up"})
            except RuntimeError:
                pass

    def _next(self) -> tuple[str, Any] | None:
        with self._lock:
            if self._first:
                return self._first.pop(0)
        db = get_db()
        s = get_settings()
        auto = self._auto()
        if auto:
            r = db.one("SELECT id FROM songs WHERE id_status='pending' AND missing=0 ORDER BY added_at LIMIT 1")
            if r is not None:
                return ("song", r["id"])
            r = db.one("SELECT id FROM songs WHERE info_status='pending' AND id_status!='pending' AND missing=0 "
                       "ORDER BY has_art, added_at LIMIT 1")
            if r is not None:
                return ("info", r["id"])
        if s.get("playback.level", True):
            r = db.one("SELECT id FROM songs WHERE lufs IS NULL AND missing=0 ORDER BY last_played DESC, added_at "
                       "LIMIT 1")
            if r is not None:
                return ("level", r["id"])
        if auto and s.get("identify.artists", True):
            from ..core.db import artist_key, main_artist
            known = {r["key"]: r for r in db.q("SELECT key, status, fetched_at FROM artists")}
            now = time.time()
            for a in db.q("SELECT artist, COUNT(*) AS n FROM songs WHERE artist IS NOT NULL AND missing=0 "
                          "GROUP BY artist COLLATE NOCASE ORDER BY n DESC"):
                name = main_artist(a["artist"])
                r = known.get(artist_key(name))
                if name and (r is None or r["status"] == "pending" or
                             (r["status"] == "error" and now - (r["fetched_at"] or 0) > 7 * 86400)):
                    return ("artist", name)
        return None

    def _run(self) -> None:
        if self._stop.wait(self.start_delay):
            return
        while not self._stop.is_set():
            item = None if self._paused else self._next()
            if item is None:
                self.done = 0
                self.started = 0.0
                self._status("")
                self._wake.wait(30)
                self._wake.clear()
                continue
            if not self.started:
                self.started = time.time()
            kind, what = item
            try:
                if kind in ("song", "deep"):
                    row = get_db().song(int(what))
                    if row is not None:
                        self._status(f"Identifying {Path(row['path']).name}")
                        identify_song(int(what), self._cancel, deep=(kind == "deep"))
                elif kind == "info":
                    row = get_db().song(int(what))
                    if row is not None:
                        self._status(f"Lyrics and cover for {row['artist']} - {row['title']}")
                        info_song(int(what))
                elif kind == "artist":
                    from ..lookup import artists
                    self._status(f"About {what}")
                    artists.fetch(str(what))
                elif kind == "level":
                    from . import level
                    row = get_db().song(int(what))
                    if row is not None:
                        self._status(f"Measuring loudness: {Path(row['path']).name}")
                        res = None
                        try:
                            res = level.measure(row["path"])
                        except Exception as e:
                            log.info("couldn't measure %s: %s", row["path"], e)
                        get_db().update_song(int(what), lufs=res[0] if res else -70.0, peak=res[1] if res else 0.0)
            except InterruptedError:
                return
            except ConnectionError:
                log.info("lookups can't reach the internet; trying again in a minute")
                self._status("No internet? Trying again in a minute")
                if self._stop.wait(60):
                    return
                continue
            except Exception:
                log.exception("lookup failed")
                if kind in ("song", "deep"):
                    get_db().update_song(int(what), id_status="error")
                elif kind == "info":
                    get_db().update_song(int(what), info_status="done")
                elif kind == "artist":
                    get_db().save_artist(str(what), status="error", fetched_at=time.time())
                elif kind == "level":
                    get_db().update_song(int(what), lufs=-70.0, peak=0.0)
            self.done += 1
            try:
                if kind == "artist":
                    if self.on_artist and not self._stop.is_set():
                        self.on_artist(str(what))
                elif kind == "level":
                    pass
                elif self.on_change and not self._stop.is_set():
                    self.on_change(int(what))
            except RuntimeError:      # the window closed while this lookup ran
                return
