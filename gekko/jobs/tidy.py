"""Tidy up: write what the library knows into the song files, after you've reviewed it.

For each song this can: rename the file to "Artist - Title", write the title, artist, album, genre and year into
its tags (adding the cover art if the file has none), and move it into <sort folder>\\Artist\\Album. The audio is
never touched, nothing is overwritten, and every change goes into the move log, where Undo puts the file back and
its old tags too."""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from ..core import log as _log
from ..core import paths
from ..core.db import get_db, key, main_artist, norm
from ..core.settings import get_settings
from ..media import covers, tags
from .context import JobContext
from .library_files import safe_name, to_recycle_bin, unique_path

log = _log.get("tidy")
TEXT = ("title", "artist", "album", "album_artist", "track", "disc", "genre", "year")


class FileBusy(Exception):
    """The file is open in another program and can't be renamed or changed."""


def options() -> dict:
    s = get_settings()
    return {"rename": bool(s.get("tidy.rename", True)), "tags": bool(s.get("tidy.tags", True)),
            "sort": bool(s.get("tidy.sort", True)), "cover": bool(s.get("tidy.cover", True)),
            "root": s.get("library.sort_root") or "E:\\Music"}


def stem_for(r) -> str | None:
    if r["title"] and r["artist"]:
        return f"{r['artist']} - {r['title']}"
    return r["title"] or None


def folder_for(r, root: str) -> Path | None:
    artist = r["album_artist"] or main_artist(r["artist"])
    if not artist:
        return None
    album = r["album"] or "Singles"
    return Path(root) / safe_name(artist, 80) / safe_name(album, 100)


def file_tags(r) -> dict:
    try:
        return json.loads(r["tags_json"] or "{}")
    except Exception:
        return {}


def tag_changes(r) -> dict:
    """Tags whose value in the library differs from the file's."""
    cur = file_tags(r)
    out = {}
    for k in TEXT:
        want = r[k]
        have = cur.get(k)
        if k in ("track", "disc"):
            want = int(want) if want else None
            have = int(have) if have else None
        else:
            want = (str(want).strip() if want is not None else None) or None
            have = (str(have).strip() if have is not None else None) or None
        if want is not None and want != have:
            out[k] = want
    return out


def plan_for(r, opt: dict, taken: set[str]) -> dict | None:
    """What Tidy up would do to one song, or None when nothing needs doing."""
    if not r["title"] or r["missing"]:
        return None
    src = Path(r["path"])
    folder = src.parent
    if opt["sort"]:
        f = folder_for(r, opt["root"])
        if f is not None:
            folder = f
    stem = src.stem
    if opt["rename"]:
        stem = safe_name(stem_for(r) or src.stem, 150)
    dst = folder / (stem + src.suffix)
    if key(dst) != key(src):
        # never onto another file, nor onto where another song in this plan is going
        i = 2
        base = dst
        while (dst.exists() and key(dst) != key(src)) or key(dst) in taken:
            dst = base.with_name(f"{base.stem} ({i}){base.suffix}")
            i += 1
    if key(dst) == key(src) and str(dst) != str(src):
        pass                                   # only the letter case changes: still a rename
    changes = tag_changes(r) if opt["tags"] else {}
    cover = bool(opt["cover"] and r["has_art"] != 1 and covers.picture_bytes(r) is not None)
    moves = str(dst) != str(src)
    if not moves and not changes and not cover:
        return None
    taken.add(key(dst))
    return {"id": r["id"], "src": str(src), "dst": str(dst), "tags": changes, "cover": cover,
            "rename": src.name != dst.name, "move": key(src.parent) != key(dst.parent)}


def plans(ids: list[int] | None = None, opt: dict | None = None) -> list[dict]:
    db = get_db()
    opt = opt or options()
    where = "missing=0 AND title IS NOT NULL"
    rows = db.q(f"SELECT * FROM songs WHERE {where} ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, disc, track, "
                "title COLLATE NOCASE")
    if ids is not None:
        want = set(ids)
        rows = [r for r in rows if r["id"] in want]
    taken: set[str] = set()
    out = []
    for r in rows:
        p = plan_for(r, opt, taken)
        if p is not None:
            out.append(p)
    return out


def _move_file(src: Path, dst: Path, tries: int = 12) -> None:
    paths.ensure(dst.parent)
    if not dst.parent.exists():
        raise RuntimeError(f"{dst.parent} can't be reached (is the drive connected?)")
    for i in range(tries):
        try:
            if key(src) == key(dst):
                os.rename(src, dst)               # letter case only
            elif dst.exists():
                raise RuntimeError(f"{dst.name} already exists there, and Gekko never overwrites files.")
            elif Path(src).anchor.casefold() == Path(dst).anchor.casefold():
                os.rename(src, dst)
            else:
                shutil.move(str(src), str(dst))   # another drive: copied, then the original removed
            return
        except PermissionError:
            if i == tries - 1:
                raise FileBusy(src.name)
            time.sleep(0.25)


def apply_one(p: dict, batch: str) -> str:
    """Do one plan. Returns the song's new path."""
    db = get_db()
    r = db.song(p["id"])
    if r is None:
        raise RuntimeError("That song is no longer in the library.")
    src = Path(r["path"])
    if not src.exists():
        raise RuntimeError(f"{src.name} can't be found any more.")
    before = None
    wrote = False
    if p["tags"] or p["cover"]:
        before = tags.snapshot(src)
        data = covers.picture_bytes(r) if p["cover"] else None
        try:
            ok = tags.write(src, p["tags"], cover=data)
        except PermissionError:
            raise FileBusy(src.name)
        if ok:
            wrote = True
        elif p["tags"]:
            log.info("%s can't hold tags; only its name changes", src.name)
    dst = Path(p["dst"])
    moved = str(dst) != str(src)
    if moved:
        if dst.exists() and key(dst) != key(src):
            dst = unique_path(dst)
        _move_file(src, dst)
    new = norm(dst if moved else src)
    t = tags.read(new)
    upd = {"path": new, "tags_json": json.dumps({k: t.get(k) for k in tags.EASY}), "file_synced": 1}
    if t.get("has_pic"):
        upd["has_art"] = 1
        covers.forget(p["id"])
    try:
        upd["mtime"] = os.stat(new).st_mtime
        upd["size"] = os.stat(new).st_size
    except OSError:
        pass
    db.update_song(p["id"], **upd)
    if moved or wrote:
        what = []
        if moved:
            what.append("Moved" if key(src.parent) != key(Path(new).parent) else "Renamed")
        if wrote:
            what.append("tags written")
        db.log_move(" · ".join(what) if moved else "Tags written", str(src), new, p["id"], batch, before)
    return new


def apply(ctx: JobContext, plan: list[dict]) -> dict:
    batch = "tidy-" + uuid.uuid4().hex[:8]
    done = failed = 0
    errors: list[str] = []
    for i, p in enumerate(plan):
        ctx.check()
        ctx.progress(i / max(1, len(plan)), Path(p["src"]).name)
        try:
            apply_one(p, batch)
            done += 1
            ctx.event("song_changed", p["id"])
        except Exception as e:
            failed += 1
            errors.append(f"{Path(p['src']).name}: {e}")
            log.warning("tidy up: %s: %s", Path(p["src"]).name, e)
    root = options()["root"]
    if any(Path(p["dst"]).parent != Path(p["src"]).parent for p in plan) and Path(root).exists():
        get_db().add_folder(root)
    return {"done": done, "failed": failed, "errors": errors[:20], "batch": batch}


# ---------------------------------------------------------------------------
# Undo
def undo_move(mid: int) -> str:
    """Put a file back where it was, with the tags it had. Returns what happened, in words."""
    db = get_db()
    m = db.move(mid)
    if m is None or m["undone"]:
        return "That change was already undone."
    src, dst = Path(m["src"]), Path(m["dst"])
    if not dst.exists():
        return f"{dst.name} was moved or deleted outside Gekko, so it can't be undone."
    if str(src) != str(dst) and src.exists() and key(src) != key(dst):
        return f"There's already a file called {src.name} there, and Gekko never overwrites files."
    try:
        if m["tags_before"]:
            before = json.loads(m["tags_before"])
            fields = {k: before.get(k) for k in TEXT}
            tags.write(dst, fields, remove_cover=not before.get("has_pic", True))
        if str(src) != str(dst):
            _move_file(dst, src)
    except FileBusy as e:
        return f"{e} is open in another program. Close it and try again."
    except Exception as e:
        return f"Couldn't undo: {e}"
    if m["song_id"] is not None and db.song(m["song_id"]) is not None:
        t = tags.read(src)
        upd = {"path": norm(src), "tags_json": json.dumps({k: t.get(k) for k in tags.EASY})}
        r = db.song(m["song_id"])
        upd["file_synced"] = 0 if tag_changes({**dict(r), **upd}) else 1
        upd["has_art"] = 1 if t.get("has_pic") else (2 if r["folder_art"] else 0)
        db.update_song(m["song_id"], **upd)
        covers.forget(m["song_id"])
    db.mark_undone(mid)
    return f"Put {src.name} back" + (" with its old tags." if m["tags_before"] else ".")


def batch_of(mid: int) -> list[int]:
    m = get_db().move(mid)
    if m is None or not (m["note"] or "").startswith("tidy-"):
        return [mid]
    return [r["id"] for r in get_db().q("SELECT id FROM moves WHERE note=? AND undone=0 ORDER BY id DESC",
                                        (m["note"],))]


# ---------------------------------------------------------------------------
# Editing and removing
def edit_info(ids: list[int], fields: dict) -> None:
    """Change the library's info for songs (typed in by you). Files change only with Tidy up."""
    db = get_db()
    f = {k: (v if v not in ("",) else None) for k, v in fields.items() if k in TEXT}
    if not f:
        return
    for i in ids:
        r = db.song(i)
        if r is None:
            continue
        upd = dict(f)
        if "title" in upd or "artist" in upd:
            upd["id_status"] = "manual"
            upd["id_source"] = "you"
        merged = {**dict(r), **upd}
        upd["file_synced"] = 0 if tag_changes(merged) else 1
        db.update_song(i, **upd)


def recycle(ctx: JobContext, ids: list[int]) -> dict:
    """Send song files to the Recycle Bin and take them out of the library."""
    db = get_db()
    done = failed = 0
    for i, sid in enumerate(ids):
        ctx.check()
        r = db.song(sid)
        if r is None:
            continue
        ctx.progress(i / max(1, len(ids)), Path(r["path"]).name)
        p = Path(r["path"])
        if p.exists() and not to_recycle_bin(p):
            failed += 1
            continue
        db.delete_songs([sid])
        done += 1
        ctx.event("song_removed", sid)
    return {"done": done, "failed": failed}
