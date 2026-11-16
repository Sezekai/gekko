"""Finding songs that are the same: by file name, by title and artist, or by their sound (Chromaprint).

No Qt here, so it can run in the background and be tested on its own."""
from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np

from ..core.db import get_db, main_artist
from .context import JobContext
from .tidy import letters

MODES = [("name", "Same file name"), ("tags", "Same title and artist"), ("sound", "Same sound")]
_LEAD = re.compile(r"^\s*(?:\d{1,2}[-.])?\d{1,3}\s*[-._)\]]?\s*(?=\D)")
_COPY = re.compile(r"(?:\s*[-_]?\s*(?:copy|kopie|copie)(?:\s*\(\d+\))?|\s*\(\d{1,2}\)|\s*\[\d{1,2}\])+\s*$", re.I)
FP_SECONDS = 120      # how much of each song is fingerprinted
SAME_SOUND = 0.86     # share of equal fingerprint bits that counts as the same recording


def name_key(path: str) -> str:
    """'03 - Night Bus (2).mp3' and 'Night Bus - Copy.flac' -> 'nightbus'."""
    stem = Path(path).stem
    stem = _COPY.sub("", stem)
    stem = _LEAD.sub("", stem) or stem
    return letters(stem)


def tag_key(r) -> str | None:
    t, a = letters(r["title"]), letters(main_artist(r["artist"]))
    return f"{a}|{t}" if t and a else None


def _group(rows, keyfn) -> list[list[int]]:
    by: dict[str, list[int]] = {}
    for r in rows:
        k = keyfn(r)
        if k:
            by.setdefault(k, []).append(r["id"])
    return [ids for ids in by.values() if len(ids) > 1]


def by_name(rows) -> list[list[int]]:
    return _group(rows, lambda r: name_key(r["path"]))


def by_tags(rows) -> list[list[int]]:
    return _group(rows, tag_key)


def best_of(rows) -> int:
    """The copy to keep: lossless or the highest bitrate, then rated higher, played more, with cover art, and the
    shortest path (usually the one already in its proper folder)."""
    lossless = {"flac", "alac", "wav", "aiff", "ape", "wv"}

    def score(r):
        codec = (r.get("codec") or Path(r["path"]).suffix.lstrip(".")).lower()
        return (codec in lossless, r.get("bitrate") or 0, r.get("rating") or 0, r.get("plays") or 0,
                bool(r.get("has_art")), -len(r["path"]))
    return max(rows, key=score)["id"]


# ---- by sound ---------------------------------------------------------------------------------------------------
def _cached_fp(sid: int, path: str) -> np.ndarray | None:
    db = get_db()
    try:
        st = os.stat(path)
    except OSError:
        return None
    row = db.q("SELECT size, mtime, fp FROM fingerprints WHERE song_id=?", (sid,))
    if row and row[0]["size"] == st.st_size and abs(row[0]["mtime"] - st.st_mtime) < 1:
        return np.frombuffer(row[0]["fp"], dtype=np.uint32)
    return None


def _store_fp(sid: int, path: str, fp: np.ndarray) -> None:
    st = os.stat(path)
    get_db().x("INSERT OR REPLACE INTO fingerprints (song_id, size, mtime, fp) VALUES (?,?,?,?)",
               (sid, st.st_size, st.st_mtime, fp.astype(np.uint32).tobytes()))


def similarity(a: np.ndarray, b: np.ndarray, max_shift: int = 10) -> float:
    """Best share of equal bits with b shifted up to max_shift items (about 1.2 s) either way."""
    from ..analysis.fingerprint import bit_similarity
    best = 0.0
    need = max(40, int(min(len(a), len(b)) * 0.6))
    for off in range(-max_shift, max_shift + 1):
        sim, n = bit_similarity(a, b, off)
        if n >= need and sim > best:
            best = sim
    return best


def _close_length(a: float, b: float) -> bool:
    return abs(a - b) <= max(4.0, 0.04 * max(a, b))


def by_sound(ctx: JobContext, rows: list[dict], fp_fn=None) -> dict:
    """Fingerprints every song (kept for next time) and groups the ones that sound the same."""
    from ..analysis.fingerprint import fingerprint
    fp_fn = fp_fn or (lambda p: fingerprint(p, FP_SECONDS))
    rows = [r for r in rows if not r.get("missing")]
    fps: dict[int, np.ndarray] = {}
    n = len(rows)
    made = 0
    for i, r in enumerate(rows):
        ctx.check()
        ctx.progress(0.9 * i / max(1, n), f"Listening to {Path(r['path']).name}")
        fp = _cached_fp(r["id"], r["path"])
        if fp is None:
            fp = fp_fn(r["path"])
            if fp is not None and len(fp):
                try:
                    _store_fp(r["id"], r["path"], fp)
                except OSError:
                    pass
                made += 1
        if fp is not None and len(fp) >= 40:
            fps[r["id"]] = fp
    ctx.progress(0.9, "Comparing")
    have = sorted((r for r in rows if r["id"] in fps), key=lambda r: r["duration"] or 0)
    parent = {r["id"]: r["id"] for r in have}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(have):
        if i % 50 == 0:
            ctx.check()
            ctx.progress(0.9 + 0.1 * i / max(1, len(have)), "Comparing")
        for b in have[i + 1:]:
            if not _close_length(a["duration"] or 0, b["duration"] or 0):
                break
            if find(a["id"]) == find(b["id"]):
                continue
            if similarity(fps[a["id"]], fps[b["id"]]) >= SAME_SOUND:
                parent[find(b["id"])] = find(a["id"])
    groups: dict[int, list[int]] = {}
    for r in have:
        groups.setdefault(find(r["id"]), []).append(r["id"])
    return {"groups": [g for g in groups.values() if len(g) > 1], "checked": len(have), "new": made,
            "failed": n - len(have)}
