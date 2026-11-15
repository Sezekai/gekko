"""Where a song's cover art comes from (no Qt here): the picture inside the file, a cover file in its folder, or
one downloaded by a lookup. Pictures inside files are copied out once into the cache so lists stay fast."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from ..core.settings import get_settings
from . import tags

_listeners: list[Callable[[int], None]] = []
_lock = threading.Lock()


def on_forget(fn: Callable[[int], None]) -> None:
    _listeners.append(fn)


def forget(song_id: int) -> None:
    """A song's cover changed: drop what's cached for it."""
    for d in ("embedded",):
        try:
            (get_settings().cache(d) / f"{song_id}.jpg").unlink(missing_ok=True)
            (get_settings().cache(d) / f"{song_id}.none").unlink(missing_ok=True)
        except OSError:
            pass
    for fn in list(_listeners):
        try:
            fn(song_id)
        except Exception:
            pass


def downloaded(song_id: int) -> Path:
    return get_settings().cache("covers") / f"{song_id}.jpg"


def source(row) -> str | None:
    """A picture file for the song, or None. May read the song file (call off the UI thread)."""
    sid = row["id"]
    if row["has_art"] == 1:
        d = get_settings().cache("embedded")
        out = d / f"{sid}.jpg"
        if out.exists():
            return str(out)
        if not (d / f"{sid}.none").exists():
            data = None
            try:
                data = tags.picture(row["path"])
            except Exception:
                data = None
            with _lock:
                if data:
                    tmp = out.with_suffix(".tmp")
                    tmp.write_bytes(data)
                    tmp.replace(out)
                    return str(out)
                try:
                    (d / f"{sid}.none").write_text("")
                except OSError:
                    pass
    fa = row["folder_art"]
    if fa and Path(fa).exists():
        return fa
    dl = downloaded(sid)
    if dl.exists():
        return str(dl)
    return None


def picture_bytes(row) -> bytes | None:
    """The cover as bytes, for writing into a file that has none (Tidy up)."""
    for p in (row["folder_art"], str(downloaded(row["id"]))):
        if p and Path(p).exists():
            try:
                b = Path(p).read_bytes()
                if b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n":
                    return b
            except OSError:
                pass
    return None
