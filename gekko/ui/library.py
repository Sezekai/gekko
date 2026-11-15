"""The library in memory: every song's list details, grouped into albums, artists and genres, kept up to date as
songs change. All the screens read from this, so browsing never waits on the database."""
from __future__ import annotations

import os
import re
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.db import get_db, main_artist
from .hub import hub

_SPLIT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with|x|vs\.?)\s+|\s*[;,/]\s*|\s+&\s+", re.I)
_GENRE_SPLIT = re.compile(r"\s*[;,/|]\s*")
UNKNOWN_ARTIST = "Unknown artist"


def all_artists(name: str | None) -> list[str]:
    return [p.strip() for p in _SPLIT.split(name or "") if p and p.strip()]


def genres_of(g: str | None) -> list[str]:
    out = []
    for p in _GENRE_SPLIT.split(g or ""):
        p = p.strip()
        if p:
            out.append(p[:1].upper() + p[1:])
    return out


def display_title(r) -> str:
    return r["title"] or Path(r["path"]).stem


def fmt_len(s: float | None) -> str:
    s = int(round(s or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_total(s: float) -> str:
    s = int(s)
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


def stars_text(r: int) -> str:
    return f"{r / 2:g}★" if r else "Unrated"


class Library(QObject):
    changed = Signal()            # many things changed: views refresh
    songUpdated = Signal(int)     # one song's details changed in place

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self._albums: list[dict] | None = None
        self._artists: list[dict] | None = None
        self._genres: list[dict] | None = None
        self._pending: set[int] = set()
        self._batch = QTimer(self, singleShot=True, interval=250, timeout=self._apply_batch)
        hub.songChanged.connect(self.update)
        hub.songsReset.connect(self.reload)
        self.reload()

    # ---- loading ------------------------------------------------------------------------------------
    @staticmethod
    def _dict(r) -> dict:
        d = dict(r)
        d["name"] = display_title(d)
        d["main_artist"] = main_artist(d["artist"]) or ""
        d["genres"] = genres_of(d["genre"])
        try:
            d["year_n"] = int(str(d["year"])[:4]) if d["year"] else 0
        except ValueError:
            d["year_n"] = 0
        d["folder"] = os.path.dirname(d["path"])
        return d

    def reload(self) -> None:
        rows = get_db().songs()
        self.rows = [self._dict(r) for r in rows]
        self.by_id = {r["id"]: r for r in self.rows}
        self._albums = self._artists = self._genres = None
        self.changed.emit()

    def update(self, sid: int) -> None:
        """A song changed; many changes in a row are folded into one refresh."""
        self._pending.add(int(sid))
        if not self._batch.isActive():
            self._batch.start()

    def _apply_batch(self) -> None:
        ids = list(self._pending)
        self._pending.clear()
        if not ids:
            return
        db = get_db()
        fresh = {r["id"]: self._dict(r) for r in db.songs_by_ids(ids)}
        structural = False
        for sid in ids:
            old = self.by_id.get(sid)
            new = fresh.get(sid)
            if new is None:
                if old is not None:
                    self.rows = [r for r in self.rows if r["id"] != sid]
                    del self.by_id[sid]
                    structural = True
                continue
            if old is None:
                self.rows.append(new)
                self.by_id[sid] = new
                structural = True
                continue
            if any(old.get(k) != new.get(k) for k in ("title", "artist", "album", "album_artist", "genre", "year",
                                                       "missing", "has_art", "path")):
                structural = True
            old.update(new)
        if structural:
            self._albums = self._artists = self._genres = None
            self.changed.emit()
        else:
            for sid in ids:
                self.songUpdated.emit(sid)

    def remove(self, ids: list[int]) -> None:
        drop = set(ids)
        self.rows = [r for r in self.rows if r["id"] not in drop]
        for i in drop:
            self.by_id.pop(i, None)
        self._albums = self._artists = self._genres = None
        self.changed.emit()

    def get(self, sid: int | None) -> dict | None:
        return self.by_id.get(sid) if sid is not None else None

    def rows_for(self, ids: list[int]) -> list[dict]:
        return [self.by_id[i] for i in ids if i in self.by_id]

    # ---- groupings -------------------------------------------------------------------------------------
    @staticmethod
    def album_key(r) -> tuple[str, str] | None:
        if not r["album"]:
            return None
        if r["album_artist"]:
            return (r["album"].casefold(), r["album_artist"].casefold())
        return (r["album"].casefold(), os.path.normcase(r["folder"]))

    def albums(self) -> list[dict]:
        if self._albums is None:
            groups: dict[tuple, list[dict]] = {}
            for r in self.rows:
                k = self.album_key(r)
                if k is not None:
                    groups.setdefault(k, []).append(r)
            out = []
            for k, rs in groups.items():
                arts = Counter(r["album_artist"] or r["main_artist"] or UNKNOWN_ARTIST for r in rs)
                top, n = arts.most_common(1)[0]
                artist = top if n * 2 > len(rs) or len(arts) == 1 else "Various artists"
                years = [r["year_n"] for r in rs if r["year_n"]]
                cover = next((r["id"] for r in sorted(rs, key=lambda x: (x["disc"] or 0, x["track"] or 0))
                              if r["has_art"]), rs[0]["id"])
                out.append({"key": k, "album": rs[0]["album"], "artist": artist, "year": max(years) if years else 0,
                            "n": len(rs), "duration": sum(r["duration"] or 0 for r in rs), "cover": cover,
                            "genres": Counter(g for r in rs for g in r["genres"]).most_common(1),
                            "added": max(r["added_at"] or 0 for r in rs),
                            "plays": sum(r["plays"] or 0 for r in rs)})
            out.sort(key=lambda a: (a["artist"].casefold(), a["year"], a["album"].casefold()))
            self._albums = out
        return self._albums

    def album_songs(self, key: tuple) -> list[dict]:
        rs = [r for r in self.rows if self.album_key(r) == tuple(key)]
        rs.sort(key=lambda r: (r["disc"] or 0, r["track"] or 0, r["name"].casefold()))
        return rs

    def find_album(self, album: str, artist: str | None = None) -> dict | None:
        a = (album or "").casefold()
        cands = [x for x in self.albums() if x["album"].casefold() == a]
        if artist:
            exact = [x for x in cands if x["artist"].casefold() == artist.casefold()
                     or main_artist(x["artist"]).casefold() == main_artist(artist).casefold()]
            cands = exact or cands
        return cands[0] if cands else None

    def album_of(self, r) -> dict | None:
        k = self.album_key(r)
        if k is None:
            return None
        return next((a for a in self.albums() if a["key"] == k), None)

    def artists(self) -> list[dict]:
        if self._artists is None:
            groups: dict[str, list[dict]] = {}
            names: dict[str, Counter] = {}
            for r in self.rows:
                seen = set()
                for n in all_artists(r["artist"]) or [UNKNOWN_ARTIST]:
                    k = n.casefold()
                    if k in seen:
                        continue
                    seen.add(k)
                    groups.setdefault(k, []).append(r)
                    names.setdefault(k, Counter())[n] += 1
            out = []
            for k, rs in groups.items():
                name = names[k].most_common(1)[0][0]
                own = [r for r in rs if r["main_artist"].casefold() == k]
                albums = {self.album_key(r) for r in own if r["album"]}
                cover = next((r["id"] for r in own if r["has_art"]), (own or rs)[0]["id"])
                out.append({"key": k, "name": name, "n": len(rs), "own": len(own), "albums": len(albums),
                            "duration": sum(r["duration"] or 0 for r in rs), "cover": cover,
                            "plays": sum(r["plays"] or 0 for r in rs)})
            # people who only appear as a guest are listed too, after the main ones in A-Z
            out.sort(key=lambda a: a["name"].casefold())
            self._artists = out
        return self._artists

    def artist_songs(self, name: str) -> list[dict]:
        k = name.casefold()
        out = [r for r in self.rows if k in {n.casefold() for n in (all_artists(r["artist"]) or [UNKNOWN_ARTIST])}
               or (r["album_artist"] or "").casefold() == k]
        out.sort(key=lambda r: (-(r["year_n"] or 0), (r["album"] or "").casefold(), r["disc"] or 0, r["track"] or 0))
        return out

    def artist_albums(self, name: str) -> list[dict]:
        k = name.casefold()
        keys = {self.album_key(r) for r in self.artist_songs(name) if r["album"]}
        out = [a for a in self.albums() if a["key"] in keys]
        # their own albums first, then ones they're on
        out.sort(key=lambda a: (main_artist(a["artist"]).casefold() != k, -(a["year"] or 0)))
        return out

    def genres(self) -> list[dict]:
        if self._genres is None:
            groups: dict[str, list[dict]] = {}
            names: dict[str, str] = {}
            for r in self.rows:
                for g in r["genres"] or ["No genre"]:
                    groups.setdefault(g.casefold(), []).append(r)
                    names.setdefault(g.casefold(), g)
            out = [{"key": k, "name": names[k], "n": len(rs), "duration": sum(r["duration"] or 0 for r in rs),
                    "cover": next((r["id"] for r in rs if r["has_art"]), rs[0]["id"])}
                   for k, rs in groups.items()]
            out.sort(key=lambda g: (-g["n"], g["name"].casefold()))
            self._genres = out
        return self._genres

    def genre_songs(self, name: str) -> list[dict]:
        k = name.casefold()
        if k == "no genre":
            return [r for r in self.rows if not r["genres"]]
        return [r for r in self.rows if k in {g.casefold() for g in r["genres"]}]

    def folder_songs(self, folder: str) -> list[dict]:
        k = os.path.normcase(folder.rstrip("\\/"))
        return [r for r in self.rows if os.path.normcase(r["path"]).startswith(k + os.sep)]

    # ---- built-in lists -------------------------------------------------------------------------------
    AUTO = [("favourites", "Favourites"), ("top", "Top rated (4★ and up)"), ("recent", "Recently added"),
            ("most", "Most played"), ("lately", "Played lately"), ("never", "Never played"),
            ("missing", "Can't be found")]

    def auto_list(self, key: str) -> list[dict]:
        rs = self.rows
        if key == "favourites":
            return [r for r in rs if r["favourite"]]
        if key == "top":
            return sorted([r for r in rs if (r["rating"] or 0) >= 8], key=lambda r: -r["rating"])
        if key == "recent":
            cut = time.time() - 60 * 86400
            return sorted([r for r in rs if (r["added_at"] or 0) >= cut], key=lambda r: -(r["added_at"] or 0))[:1000]
        if key == "most":
            return sorted([r for r in rs if r["plays"]], key=lambda r: -r["plays"])[:200]
        if key == "lately":
            return sorted([r for r in rs if r["last_played"]], key=lambda r: -r["last_played"])[:200]
        if key == "never":
            return [r for r in rs if not r["plays"]]
        if key == "missing":
            return [r for r in rs if r["missing"]]
        return []


_lib: Library | None = None


def get_library() -> Library:
    global _lib
    if _lib is None:
        _lib = Library()
    return _lib
