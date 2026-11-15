"""Reading and writing the tags inside song files (mutagen). Only the tags change: the audio is never touched."""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from ..core import log as _log

log = _log.get("tags")

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aif", ".aiff",
              ".ape", ".wv", ".mka", ".alac", ".dsf", ".mpc", ".m4b", ".webm"}
COVER_NAMES = ("cover", "folder", "front", "albumart", "album", "albumartlarge", "albumartsmall", "artwork")
IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
# the text tags Gekko reads and writes, as mutagen's "easy" names
EASY = {"title": "title", "artist": "artist", "album": "album", "album_artist": "albumartist",
        "track": "tracknumber", "disc": "discnumber", "genre": "genre", "year": "date"}


def is_audio(p: Path) -> bool:
    return p.suffix.lower() in AUDIO_EXTS


def _first(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    if v is None:
        return None
    s = str(v).strip().strip("\x00")
    return s or None


def _num(v) -> int | None:
    s = _first(v)
    if not s:
        return None
    m = re.match(r"\s*(\d+)", s)
    return int(m.group(1)) if m else None


def _year(v) -> str | None:
    s = _first(v)
    if not s:
        return None
    m = re.search(r"(\d{4})", s)
    return m.group(1) if m else None


def _codec_name(raw: str, path) -> str:
    r = (raw or "").lower()
    if r.startswith("mp4a") or r == "aac":
        return "AAC"
    for k, v in (("alac", "ALAC"), ("flac", "FLAC"), ("vorbis", "Vorbis"), ("opus", "Opus"), ("mp3", "MP3"),
                 ("wave", "WAV"), ("wav", "WAV"), ("asf", "WMA"), ("aiff", "AIFF"), ("monkeysaudio", "APE"),
                 ("wavpack", "WavPack"), ("dsf", "DSD"), ("musepack", "Musepack")):
        if k in r:
            return v
    return Path(str(path)).suffix.lstrip(".").upper() or raw


def folder_cover(folder: Path, cache: dict | None = None) -> str | None:
    """A cover picture beside the songs (folder.jpg, cover.png, …)."""
    k = str(folder)
    if cache is not None and k in cache:
        return cache[k]
    found = None
    try:
        imgs = [p for p in folder.iterdir() if p.suffix.lower() in IMG_EXTS and p.is_file()]
    except OSError:
        imgs = []
    by_stem = {p.stem.lower().replace(" ", "").replace("_", ""): p for p in imgs}
    for n in COVER_NAMES:
        if n in by_stem:
            found = str(by_stem[n])
            break
    if found is None:
        for p in imgs:
            if any(w in p.stem.lower() for w in ("cover", "folder", "front")):
                found = str(p)
                break
    if found is None and len(imgs) == 1:
        found = str(imgs[0])
    if cache is not None:
        cache[k] = found
    return found


def read(path: str | Path) -> dict[str, Any]:
    """Text tags, length and format details. Missing values are None. Never raises for a readable file."""
    import mutagen

    out: dict[str, Any] = {k: None for k in EASY}
    out.update(duration=0.0, bitrate=None, sample_rate=None, codec=None, has_pic=False, lyrics=None)
    try:
        f = mutagen.File(str(path), easy=True)
    except Exception as e:
        log.info("can't read tags of %s: %s", path, e)
        f = None
    if f is None:
        return out
    info = getattr(f, "info", None)
    if info is not None:
        out["duration"] = float(getattr(info, "length", 0.0) or 0.0)
        br = getattr(info, "bitrate", None)
        out["bitrate"] = int(br) if br else None
        sr = getattr(info, "sample_rate", None)
        out["sample_rate"] = int(sr) if sr else None
        out["codec"] = _codec_name(getattr(info, "codec", None) or type(info).__module__.split(".")[-1], path)
    tags = f.tags or {}
    for k, ek in EASY.items():
        try:
            v = tags.get(ek)
        except Exception:
            v = None
        if k in ("track", "disc"):
            out[k] = _num(v)
        elif k == "year":
            out[k] = _year(v)
        else:
            out[k] = _first(v)
    # pictures and lyrics live outside the easy names
    try:
        raw = mutagen.File(str(path))
        out["has_pic"], out["lyrics"] = _pic_and_lyrics(raw)
    except Exception:
        pass
    return out


def _pic_and_lyrics(raw) -> tuple[bool, str | None]:
    if raw is None:
        return False, None
    t = raw.tags
    pic = bool(getattr(raw, "pictures", None))
    lyr = None
    if t is not None:
        try:
            keys = list(t.keys())
        except Exception:
            keys = []
        for k in keys:
            ks = str(k)
            if ks.startswith("APIC") or ks == "covr" or ks.lower() == "metadata_block_picture" or ks.startswith("WM/Picture"):
                pic = True
            if ks.startswith("USLT") or ks == "\xa9lyr" or ks.lower() in ("lyrics", "unsyncedlyrics"):
                try:
                    v = t[k]
                    lyr = str(getattr(v, "text", None) or _first(v) or "") or lyr
                except Exception:
                    pass
    return pic, (lyr.strip() if lyr and lyr.strip() else None)


def picture(path: str | Path) -> bytes | None:
    """The picture inside the file's tags (front cover first)."""
    import mutagen
    try:
        raw = mutagen.File(str(path))
    except Exception:
        return None
    if raw is None:
        return None
    pics = getattr(raw, "pictures", None)
    if pics:
        front = [p for p in pics if getattr(p, "type", 0) == 3]
        return (front or pics)[0].data
    t = raw.tags
    if t is None:
        return None
    try:
        for k in list(t.keys()):
            ks = str(k)
            if ks.startswith("APIC"):
                return t[k].data
            if ks == "covr":
                v = t[k]
                return bytes(v[0]) if v else None
            if ks.lower() == "metadata_block_picture":
                from mutagen.flac import Picture
                v = t[k]
                return Picture(base64.b64decode(_first(v) or "")).data
            if ks.startswith("WM/Picture"):
                v = t[k][0].value
                # ASF picture: type(1) size(4) mime(utf16z) desc(utf16z) data
                i = 5
                for _ in range(2):
                    while v[i:i + 2] != b"\x00\x00":
                        i += 2
                    i += 2
                return v[i:]
    except Exception:
        return None
    return None


def snapshot(path: str | Path) -> dict[str, Any]:
    """The text tags as they are, so a later write can be undone."""
    r = read(path)
    return {k: r.get(k) for k in EASY} | {"has_pic": bool(r.get("has_pic"))}


def write(path: str | Path, fields: dict[str, Any], cover: bytes | None = None, remove_cover: bool = False) -> bool:
    """Write text tags (None clears one). cover is added only when the file has no picture yet.
    Returns False when this kind of file can't hold tags."""
    import mutagen
    p = str(path)
    try:
        f = mutagen.File(p, easy=True)
    except Exception as e:
        raise RuntimeError(f"The tags of {Path(p).name} can't be read: {e}")
    if f is None:
        return False
    if f.tags is None:
        try:
            f.add_tags()
        except Exception:
            return False
    for k, v in fields.items():
        ek = EASY.get(k)
        if not ek:
            continue
        try:
            if v in (None, ""):
                if ek in f.tags:
                    del f.tags[ek]
            else:
                f.tags[ek] = [str(v)]
        except Exception as e:                     # some formats don't take every tag (e.g. WAV and discs)
            log.info("%s: couldn't set %s: %s", Path(p).name, k, e)
    f.save()
    if cover or remove_cover:
        try:
            _set_cover(p, cover, remove_cover)
        except Exception as e:
            log.info("%s: couldn't change the cover: %s", Path(p).name, e)
    return True


def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def _set_cover(p: str, data: bytes | None, remove: bool) -> None:
    import mutagen
    raw = mutagen.File(p)
    if raw is None:
        return
    has, _ = _pic_and_lyrics(raw)
    if data and has and not remove:
        return                               # never replace a picture that's already there
    ext = Path(p).suffix.lower()
    if ext == ".mp3":
        from mutagen.id3 import APIC, ID3
        try:
            t = ID3(p)
        except Exception:
            t = ID3()
        if remove:
            t.delall("APIC")
        if data:
            t.add(APIC(encoding=3, mime=_mime(data), type=3, desc="Cover", data=data))
        t.save(p)
    elif ext in (".m4a", ".mp4", ".m4b", ".alac", ".aac"):
        from mutagen.mp4 import MP4, MP4Cover
        m = MP4(p)
        if m.tags is None:
            m.add_tags()
        if remove:
            m.tags.pop("covr", None)
        if data:
            fmt = MP4Cover.FORMAT_PNG if _mime(data) == "image/png" else MP4Cover.FORMAT_JPEG
            m.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        m.save()
    elif ext == ".flac":
        from mutagen.flac import FLAC, Picture
        fl = FLAC(p)
        if remove:
            fl.clear_pictures()
        if data:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, _mime(data), "Cover", data
            fl.add_picture(pic)
        fl.save()
    elif ext in (".ogg", ".oga", ".opus"):
        from mutagen.flac import Picture
        if raw.tags is None:
            raw.add_tags()
        if remove:
            raw.tags.pop("metadata_block_picture", None)
        if data:
            pic = Picture()
            pic.type, pic.mime, pic.desc, pic.data = 3, _mime(data), "Cover", data
            raw.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
        raw.save()
