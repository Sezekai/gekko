"""The library database (SQLite in the data folder).

What's here is Gekko's own knowledge about the songs; the song files stay where they are. The database is the
truth for what the library shows: a lookup or an edit changes it straight away, and Tidy up later writes it into
the files (names, tags, folders) once you've approved that."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS excluded (
    path TEXT PRIMARY KEY, added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS removed (
    path TEXT PRIMARY KEY, title TEXT, artist TEXT, removed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS songs (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL DEFAULT 0, mtime REAL NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0, bitrate INTEGER, sample_rate INTEGER, codec TEXT,
    title TEXT, artist TEXT, album TEXT, album_artist TEXT, track INTEGER, disc INTEGER,
    genre TEXT, year TEXT,
    has_art INTEGER NOT NULL DEFAULT 0,      -- 1 = picture in the tags, 2 = cover file in the folder
    folder_art TEXT,                         -- cover file beside the song (folder.jpg …)
    cover_url TEXT,
    lyrics_plain TEXT, lyrics_synced TEXT,
    shazam_key TEXT, isrc TEXT, mbid TEXT,
    id_status TEXT NOT NULL DEFAULT 'tagged',   -- tagged | pending | found | not_found | error | manual
    id_source TEXT,
    info_status TEXT NOT NULL DEFAULT 'pending', -- extras (lyrics, cover, genre): pending | done
    file_synced INTEGER NOT NULL DEFAULT 1,  -- 0: the info here differs from the file's own tags
    tags_json TEXT,                          -- the tags as they were read from the file
    rating INTEGER NOT NULL DEFAULT 0,       -- 0-10, in half stars
    favourite INTEGER NOT NULL DEFAULT 0,
    plays INTEGER NOT NULL DEFAULT 0, skips INTEGER NOT NULL DEFAULT 0, last_played REAL,
    lufs REAL, peak REAL,
    missing INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS songs_artist ON songs(artist);
CREATE INDEX IF NOT EXISTS songs_album ON songs(album);
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, color TEXT, rules TEXT, pos INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_songs (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    pos INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS pl_songs ON playlist_songs(playlist_id, pos);
CREATE TABLE IF NOT EXISTS artists (
    key TEXT PRIMARY KEY, name TEXT, mbid TEXT, bio TEXT, bio_source TEXT, bio_url TEXT, image TEXT,
    kind TEXT, country TEXT, years TEXT, status TEXT NOT NULL DEFAULT 'pending', fetched_at REAL
);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY, at REAL NOT NULL, action TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL,
    song_id INTEGER, note TEXT, tags_before TEXT, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS listens (
    id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL,
    at REAL NOT NULL,                        -- when it started playing
    played REAL NOT NULL DEFAULT 0,          -- seconds actually heard
    length REAL,                             -- the song's length then
    counted INTEGER NOT NULL DEFAULT 0,      -- 1 = heard enough to count as a listen (else a skip)
    context TEXT,                            -- what it was played from ("Album: Blue")
    title TEXT, artist TEXT                  -- kept in case the song later leaves the library
);
CREATE INDEX IF NOT EXISTS listens_at ON listens(at);
CREATE INDEX IF NOT EXISTS listens_song ON listens(song_id, at);
"""

# Columns every listing needs (the long ones - lyrics, tags - are read only for one song at a time).
LIST_COLS = ("id, path, duration, title, artist, album, album_artist, track, disc, genre, year, has_art, folder_art, "
             "cover_url, rating, favourite, plays, skips, last_played, added_at, missing, id_status, file_synced, "
             "bitrate, codec, lufs, (lyrics_plain IS NOT NULL OR lyrics_synced IS NOT NULL) AS has_lyrics")

EDITABLE = ("title", "artist", "album", "album_artist", "track", "disc", "genre", "year")


def norm(p: str | Path) -> str:
    """The form a path is stored and compared in (Windows paths ignore letter case)."""
    s = os.path.normpath(str(p))
    return s


def key(p: str | Path) -> str:
    return os.path.normcase(norm(p))


def artist_key(name: str | None) -> str:
    return (name or "").strip().casefold()


def main_artist(name: str | None) -> str:
    """The first artist of 'A feat. B', 'A & B', 'A, B' - the one an artist profile is about."""
    import re
    n = (name or "").strip()
    if not n:
        return ""
    parts = re.split(r"\s+(?:feat\.?|ft\.?|featuring|with|x|vs\.?)\s+|\s*[;,/]\s*|\s+&\s+", n, maxsplit=1,
                     flags=re.I)
    return parts[0].strip() or n


class DB:
    def __init__(self, path: Path | None = None):
        self.path = path or (paths.data_dir() / "gekko.db")
        self._local = threading.local()
        self._lock = threading.RLock()
        c = self.conn()
        c.executescript(SCHEMA)
        c.commit()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.c = c
        return c

    def q(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.conn().execute(sql, tuple(args)).fetchall()

    def one(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.conn().execute(sql, tuple(args)).fetchone()

    def x(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self._lock:
            c = self.conn()
            cur = c.execute(sql, tuple(args))
            c.commit()
            return cur.lastrowid or cur.rowcount

    def many(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            c = self.conn()
            c.executemany(sql, [tuple(r) for r in rows])
            c.commit()

    # ---- key/value ------------------------------------------------------------------------
    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        if r is None:
            return default
        try:
            return json.loads(r[0])
        except Exception:
            return r[0]

    def kv_set(self, k: str, v: Any) -> None:
        self.x("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    # ---- folders --------------------------------------------------------------------------
    def folders(self) -> list[str]:
        return [r["path"] for r in self.q("SELECT path FROM folders ORDER BY path COLLATE NOCASE")]

    def add_folder(self, path: str) -> bool:
        p = norm(path)
        for f in self.folders():                 # already covered by a folder that's in the list
            if key(p) == key(f) or key(p).startswith(key(f).rstrip("\\/") + os.sep):
                return False
        # a parent being added takes over its subfolders already in the list
        for f in self.folders():
            if key(f).startswith(key(p).rstrip("\\/") + os.sep):
                self.x("DELETE FROM folders WHERE path=?", (f,))
        self.x("INSERT OR IGNORE INTO folders(path, added_at) VALUES(?, ?)", (p, time.time()))
        self.unexclude(p)
        return True

    def remove_folder(self, path: str) -> int:
        """Take a folder off the list (nothing on disk changes). Its songs leave the library. Returns how many."""
        k = key(path).rstrip("\\/")
        self.x("DELETE FROM folders WHERE path=?", (path,))
        ids = [r["id"] for r in self.q("SELECT id, path FROM songs") if key(r["path"]).startswith(k + os.sep)]
        self.delete_songs(ids)
        return len(ids)

    def excluded(self) -> list[str]:
        return [r["path"] for r in self.q("SELECT path FROM excluded ORDER BY path COLLATE NOCASE")]

    def exclude(self, path: str) -> int:
        """Never add this folder (and its subfolders) again. Its songs leave the library now. Returns how many."""
        p = norm(path)
        self.x("INSERT OR IGNORE INTO excluded(path, added_at) VALUES(?, ?)", (p, time.time()))
        k = key(p).rstrip("\\/")
        ids = [r["id"] for r in self.q("SELECT id, path FROM songs") if key(r["path"]).startswith(k + os.sep)]
        self.delete_songs(ids)
        return len(ids)

    def unexclude(self, path: str) -> None:
        self.x("DELETE FROM excluded WHERE path=?", (norm(path),))

    # ---- removed songs ----------------------------------------------------------------------
    def remove_songs(self, ids: list[int]) -> int:
        """Take songs out of the library for good: later scans skip them. The files stay on disk."""
        rows = [self.song(i) for i in ids]
        rows = [r for r in rows if r is not None]
        now = time.time()
        self.many("INSERT OR REPLACE INTO removed(path, title, artist, removed_at) VALUES(?, ?, ?, ?)",
                  [(r["path"], r["title"], r["artist"], now) for r in rows])
        self.delete_songs([r["id"] for r in rows])
        return len(rows)

    def removed(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM removed ORDER BY removed_at DESC")

    def unremove(self, paths_: list[str] | None = None) -> None:
        if paths_ is None:
            self.x("DELETE FROM removed")
        else:
            self.many("DELETE FROM removed WHERE path=?", [(p,) for p in paths_])

    def removed_keys(self) -> set[str]:
        return {key(r["path"]) for r in self.q("SELECT path FROM removed")}

    # ---- songs ------------------------------------------------------------------------------
    def add_song(self, **f: Any) -> int:
        f.setdefault("added_at", time.time())
        cols = ", ".join(f)
        qs = ", ".join("?" for _ in f)
        return int(self.x(f"INSERT INTO songs({cols}) VALUES({qs})", f.values()))

    def update_song(self, song_id: int, **f: Any) -> None:
        if not f:
            return
        sets = ", ".join(f"{k}=?" for k in f)
        self.x(f"UPDATE songs SET {sets} WHERE id=?", [*f.values(), song_id])

    def update_songs(self, ids: Iterable[int], **f: Any) -> None:
        ids = list(ids)
        if not ids or not f:
            return
        sets = ", ".join(f"{k}=?" for k in f)
        self.many(f"UPDATE songs SET {sets} WHERE id=?", [[*f.values(), i] for i in ids])

    def song(self, song_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM songs WHERE id=?", (song_id,))

    def song_by_path(self, path: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM songs WHERE path=?", (norm(path),))

    def songs(self, where: str = "", args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self.q(f"SELECT {LIST_COLS} FROM songs {('WHERE ' + where) if where else ''}", args)

    def songs_by_ids(self, ids: list[int]) -> list[sqlite3.Row]:
        """Rows in the order of ids (missing ones skipped)."""
        if not ids:
            return []
        out: dict[int, sqlite3.Row] = {}
        for i in range(0, len(ids), 900):
            chunk = ids[i:i + 900]
            for r in self.q(f"SELECT {LIST_COLS} FROM songs WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                out[r["id"]] = r
        return [out[i] for i in ids if i in out]

    def delete_songs(self, ids: list[int]) -> None:
        self.many("DELETE FROM songs WHERE id=?", [(i,) for i in ids])

    def count(self) -> int:
        r = self.one("SELECT COUNT(*) FROM songs")
        return int(r[0]) if r else 0

    def set_rating(self, ids: Iterable[int], rating: int) -> None:
        self.update_songs(ids, rating=max(0, min(10, int(rating))))

    def set_favourite(self, ids: Iterable[int], on: bool) -> None:
        self.update_songs(ids, favourite=1 if on else 0)

    def add_play(self, song_id: int) -> None:
        self.x("UPDATE songs SET plays=plays+1, last_played=? WHERE id=?", (time.time(), song_id))

    def add_skip(self, song_id: int) -> None:
        self.x("UPDATE songs SET skips=skips+1 WHERE id=?", (song_id,))

    # ---- playlists ----------------------------------------------------------------------------
    def playlists(self) -> list[sqlite3.Row]:
        return self.q("SELECT p.*, (SELECT COUNT(*) FROM playlist_songs s WHERE s.playlist_id=p.id) AS n "
                      "FROM playlists p ORDER BY pos, name COLLATE NOCASE")

    def playlist(self, pid: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM playlists WHERE id=?", (pid,))

    def add_playlist(self, name: str, color: str | None, rules: dict | None = None) -> int:
        r = self.one("SELECT COALESCE(MAX(pos), 0) + 1 FROM playlists")
        return int(self.x("INSERT INTO playlists(name, color, rules, pos, created_at) VALUES(?, ?, ?, ?, ?)",
                          (name, color, json.dumps(rules) if rules is not None else None, int(r[0]), time.time())))

    def update_playlist(self, pid: int, **f: Any) -> None:
        if "rules" in f and f["rules"] is not None and not isinstance(f["rules"], str):
            f["rules"] = json.dumps(f["rules"])
        sets = ", ".join(f"{k}=?" for k in f)
        self.x(f"UPDATE playlists SET {sets} WHERE id=?", [*f.values(), pid])

    def delete_playlist(self, pid: int) -> None:
        self.x("DELETE FROM playlists WHERE id=?", (pid,))

    def playlist_song_ids(self, pid: int) -> list[int]:
        return [r[0] for r in self.q("SELECT song_id FROM playlist_songs WHERE playlist_id=? ORDER BY pos", (pid,))]

    def add_to_playlist(self, pid: int, ids: list[int]) -> int:
        r = self.one("SELECT COALESCE(MAX(pos), -1) FROM playlist_songs WHERE playlist_id=?", (pid,))
        start = int(r[0]) + 1
        self.many("INSERT INTO playlist_songs(playlist_id, song_id, pos) VALUES(?, ?, ?)",
                  [(pid, sid, start + i) for i, sid in enumerate(ids)])
        return len(ids)

    def set_playlist_songs(self, pid: int, ids: list[int]) -> None:
        with self._lock:
            c = self.conn()
            c.execute("DELETE FROM playlist_songs WHERE playlist_id=?", (pid,))
            c.executemany("INSERT INTO playlist_songs(playlist_id, song_id, pos) VALUES(?, ?, ?)",
                          [(pid, sid, i) for i, sid in enumerate(ids)])
            c.commit()

    # ---- artists -------------------------------------------------------------------------------
    def artist(self, name: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM artists WHERE key=?", (artist_key(name),))

    def save_artist(self, name: str, **f: Any) -> None:
        k = artist_key(name)
        if self.one("SELECT 1 FROM artists WHERE key=?", (k,)) is None:
            self.x("INSERT INTO artists(key, name) VALUES(?, ?)", (k, name))
        if f:
            sets = ", ".join(f"{c}=?" for c in f)
            self.x(f"UPDATE artists SET {sets} WHERE key=?", [*f.values(), k])

    def artist_images(self) -> dict[str, str]:
        return {r["key"]: r["image"] for r in self.q("SELECT key, image FROM artists WHERE image IS NOT NULL")}

    # ---- the move log ----------------------------------------------------------------------------
    def log_move(self, action: str, src: str, dst: str, song_id: int | None = None, note: str = "",
                 tags_before: dict | None = None) -> int:
        return int(self.x("INSERT INTO moves(at, action, src, dst, song_id, note, tags_before) "
                          "VALUES(?, ?, ?, ?, ?, ?, ?)",
                          (time.time(), action, src, dst, song_id, note,
                           json.dumps(tags_before) if tags_before is not None else None)))

    def moves(self, limit: int = 300) -> list:
        return self.q("SELECT * FROM moves ORDER BY id DESC LIMIT ?", (limit,))

    def move(self, mid: int):
        return self.one("SELECT * FROM moves WHERE id=?", (mid,))

    def last_move(self):
        return self.one("SELECT * FROM moves WHERE undone=0 ORDER BY id DESC LIMIT 1")

    def mark_undone(self, mid: int) -> None:
        self.x("UPDATE moves SET undone=1 WHERE id=?", (mid,))

    def clear_moves(self) -> None:
        self.x("DELETE FROM moves")

    # ---- listening history ------------------------------------------------------------------------
    def add_listen(self, song_id: int, at: float, played: float, length: float | None, context: str,
                   title: str | None, artist: str | None) -> int:
        return int(self.x("INSERT INTO listens(song_id, at, played, length, counted, context, title, artist) "
                          "VALUES(?, ?, ?, ?, 0, ?, ?, ?)", (song_id, at, played, length, context, title, artist)))

    def update_listen(self, lid: int, played: float, counted: bool | None = None) -> None:
        if counted is None:
            self.x("UPDATE listens SET played=? WHERE id=?", (played, lid))
        else:
            self.x("UPDATE listens SET played=?, counted=? WHERE id=?", (played, 1 if counted else 0, lid))

    def listens(self, start: float | None = None, end: float | None = None, song_id: int | None = None,
                limit: int | None = None) -> list[sqlite3.Row]:
        """Newest first. start/end are times (end not included)."""
        where, args = [], []
        if start is not None:
            where.append("at >= ?")
            args.append(start)
        if end is not None:
            where.append("at < ?")
            args.append(end)
        if song_id is not None:
            where.append("song_id = ?")
            args.append(song_id)
        sql = "SELECT * FROM listens" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY at DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.q(sql, args)

    def listened_ids(self, start: float | None, end: float | None, counted_only: bool = True) -> set[int]:
        where, args = (["counted=1"] if counted_only else []), []
        if start is not None:
            where.append("at >= ?")
            args.append(start)
        if end is not None:
            where.append("at < ?")
            args.append(end)
        sql = "SELECT DISTINCT song_id FROM listens" + (" WHERE " + " AND ".join(where) if where else "")
        return {r[0] for r in self.q(sql, args)}

    def delete_listens(self, ids: list[int]) -> None:
        self.many("DELETE FROM listens WHERE id=?", [(i,) for i in ids])

    def clear_listens(self) -> None:
        self.x("DELETE FROM listens")

    def listen_count(self) -> int:
        r = self.one("SELECT COUNT(*) FROM listens")
        return int(r[0]) if r else 0

    # ---- resets ----------------------------------------------------------------------------------
    def reset_library(self) -> None:
        """Forget every song, playlist and artist (the files stay). Folders, skips and the move log stay."""
        with self._lock:
            c = self.conn()
            for t in ("playlist_songs", "playlists", "songs", "artists"):
                c.execute(f"DELETE FROM {t}")
            c.execute("DELETE FROM kv WHERE k LIKE 'queue%'")
            c.commit()

    def reset_artists(self) -> None:
        self.x("DELETE FROM artists")


_db: DB | None = None
_db_lock = threading.Lock()


def get_db() -> DB:
    global _db
    with _db_lock:
        if _db is None:
            _db = DB()
        return _db


def reset_db_cache() -> None:
    global _db
    _db = None
