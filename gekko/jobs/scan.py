"""Looking through the music folders: new songs are added, changed ones re-read, missing ones marked.

Songs taken out of the library and folders marked "Don't add" are never added again. Songs on a drive that isn't
connected stay in the library as they are (nothing is marked missing because a drive is unplugged)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..core import log as _log
from ..core import paths
from ..core.db import get_db, key, norm
from ..media import tags
from .context import JobContext

log = _log.get("scan")

SKIP_DIRS = {"$recycle.bin", "system volume information", "recycler", ".trash", ".git", "__macosx"}


def _excluded(k: str, excl: list[str]) -> bool:
    return any(k == e or k.startswith(e + os.sep) for e in excl)


def walk(ctx: JobContext | None, folders: list[str], excl_keys: list[str], removed: set[str],
         found: list[tuple[str, int, float]], unreachable: list[str]) -> None:
    for f in folders:
        if not paths.drive_ok(Path(f)) or not Path(f).exists():
            unreachable.append(f)
            continue
        for root, dirs, files in os.walk(f):
            if ctx is not None:
                ctx.check()
            rk = key(root)
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")
                       and not _excluded(key(os.path.join(root, d)), excl_keys)]
            if _excluded(rk, excl_keys):
                dirs[:] = []
                continue
            for name in files:
                if Path(name).suffix.lower() not in tags.AUDIO_EXTS:
                    continue
                full = norm(os.path.join(root, name))
                if key(full) in removed:
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if st.st_size < 1024:
                    continue
                found.append((full, st.st_size, st.st_mtime))
            if ctx is not None and len(found) % 200 == 0:
                ctx.progress(0.0, f"Looking through folders: {len(found):,} songs found so far")


def song_fields(path: str, t: dict, art_cache: dict) -> dict:
    """Database fields for a song file from its tags."""
    folder_art = tags.folder_cover(Path(path).parent, art_cache)
    has_art = 1 if t.get("has_pic") else (2 if folder_art else 0)
    titled = bool(t.get("title")) and bool(t.get("artist"))
    f = {k: t.get(k) for k in tags.EASY}
    f.update(duration=t.get("duration") or 0.0, bitrate=t.get("bitrate"), sample_rate=t.get("sample_rate"),
             codec=t.get("codec"), has_art=has_art, folder_art=folder_art,
             tags_json=__import__("json").dumps({k: t.get(k) for k in tags.EASY}),
             id_status="tagged" if titled else "pending", info_status="pending", file_synced=1)
    if t.get("lyrics"):
        f["lyrics_plain"] = t["lyrics"]
    return f


def scan(ctx: JobContext, only: list[str] | None = None) -> dict:
    db = get_db()
    folders = only or db.folders()
    excl = [key(e).rstrip("\\/") for e in db.excluded()]
    removed = db.removed_keys()
    ctx.progress(0.0, "Looking through folders")
    found: list[tuple[str, int, float]] = []
    unreachable: list[str] = []
    walk(ctx, folders, excl, removed, found, unreachable)
    existing = {key(r["path"]): r for r in db.q("SELECT id, path, size, mtime, missing, file_synced, duration, "
                                                  "title FROM songs")}
    seen: set[str] = set()
    todo_new: list[tuple[str, int, float]] = []
    todo_changed: list[tuple[int, str, int, float, int]] = []
    back: list[int] = []
    for full, size, mtime in found:
        k = key(full)
        seen.add(k)
        r = existing.get(k)
        if r is None:
            todo_new.append((full, size, mtime))
            continue
        if r["missing"]:
            back.append(r["id"])
        # changed on disk, or read badly before (no length: older versions couldn't read some MP3s)
        if r["size"] != size or abs((r["mtime"] or 0) - mtime) > 1 or not r["duration"]:
            todo_changed.append((r["id"], full, size, mtime, r["file_synced"]))
    if back:
        db.update_songs(back, missing=0)
    # Songs not found where they should be (only in folders that can be reached)
    reach = [key(f).rstrip("\\/") for f in folders if f not in unreachable]
    gone = [r for k, r in existing.items() if k not in seen and not r["missing"]
            and any(k.startswith(f + os.sep) for f in reach) and not _excluded(k, excl)]
    gone_by_size: dict[int, list] = {}
    for r in gone:
        gone_by_size.setdefault(int(r["size"] or 0), []).append(r)

    art_cache: dict = {}
    total = len(todo_new) + len(todo_changed)
    added = updated = moved = 0
    t0 = time.time()
    for i, (full, size, mtime) in enumerate(todo_new):
        ctx.check()
        if i % 10 == 0:
            ctx.progress(0.02 + 0.96 * i / max(1, total), f"Adding {Path(full).name}")
        t = tags.read(full)
        # A file moved or renamed outside Gekko: same size and length as a song that went missing
        twin = None
        for r in gone_by_size.get(size, []):
            if abs((r["duration"] or 0) - (t.get("duration") or 0)) < 0.6:
                twin = r
                break
        if twin is not None:
            gone_by_size[size].remove(twin)
            gone.remove(twin)
            db.update_song(twin["id"], path=full, mtime=mtime, missing=0)
            moved += 1
            continue
        f = song_fields(full, t, art_cache)
        db.add_song(path=full, size=size, mtime=mtime, **f)
        added += 1
    for j, (sid, full, size, mtime, synced) in enumerate(todo_changed):
        ctx.check()
        ctx.progress(0.02 + 0.96 * (len(todo_new) + j) / max(1, total), f"Reading {Path(full).name} again")
        t = tags.read(full)
        f = song_fields(full, t, art_cache)
        if not synced:
            # info waiting to be written by Tidy up stays; only the file's own details change
            f = {k: f[k] for k in ("duration", "bitrate", "sample_rate", "codec", "has_art", "folder_art")}
        else:
            f.pop("info_status", None)
            row = db.song(sid)
            if row is not None and row["id_status"] not in ("pending",) and f["id_status"] == "pending":
                f.pop("id_status")          # a looked-up song keeps that state
        db.update_song(sid, size=size, mtime=mtime, **f)
        updated += 1
    missing_now = [r["id"] for r in gone if not Path(r["path"]).exists()]
    if missing_now:
        db.update_songs(missing_now, missing=1)
    log.info("scan: %d added, %d re-read, %d moved, %d missing, %d back, %d folders unreachable (%.1fs)",
             added, updated, moved, len(missing_now), len(back), len(unreachable), time.time() - t0)
    return {"added": added, "updated": updated, "moved": moved, "missing": len(missing_now), "back": len(back),
            "unreachable": unreachable, "total": len(found)}


def describe(res: dict) -> str:
    parts = []
    if res.get("added"):
        parts.append(f"{res['added']:,} new song{'s' if res['added'] != 1 else ''}")
    if res.get("updated"):
        parts.append(f"{res['updated']:,} changed")
    if res.get("moved"):
        parts.append(f"{res['moved']:,} moved")
    if res.get("missing"):
        parts.append(f"{res['missing']:,} missing")
    if res.get("back"):
        parts.append(f"{res['back']:,} found again")
    if not parts:
        parts.append("nothing new")
    return ", ".join(parts) + f" · {res.get('total', 0):,} songs in your folders"
