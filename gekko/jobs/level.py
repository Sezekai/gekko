"""Even volume: each song's loudness is measured once (EBU R128, with ffmpeg) and the player turns loud songs down
and quiet ones up while they play. The song files are never changed."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core import log as _log
from ..core.db import get_db
from ..core.settings import get_settings
from ..media import ff
from .context import JobContext

log = _log.get("level")
_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def measure(path: str | Path) -> tuple[float, float] | None:
    """(integrated loudness in LUFS, true peak in dBTP), or None for silence or an unreadable file."""
    args = [ff.ffmpeg(), "-hide_banner", "-nostdin", "-v", "info", "-i", str(path), "-map", "0:a:0", "-vn",
            "-af", "loudnorm=print_format=json", "-f", "null", "-"]
    p = ff.run(args, timeout=900)
    m = _JSON.search(p.stderr.decode("utf-8", "replace"))
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        lufs, peak = float(d["input_i"]), float(d["input_tp"])
    except (ValueError, KeyError):
        return None
    if lufs < -69:
        return None
    return lufs, peak


def gain_for(lufs: float | None, peak: float | None) -> float:
    """How many dB to change a song by while it plays."""
    s = get_settings()
    if lufs is None or lufs <= -69 or not s.get("playback.level", True):
        return 0.0                            # not measured, silent or unreadable: played as it is
    g = float(s.get("playback.target", -14.0)) - lufs
    if peak is not None:
        g = min(g, -1.0 - peak)               # never so loud that it clips
    return max(-12.0, min(12.0, g))


def to_measure() -> list[int]:
    return [r[0] for r in get_db().q("SELECT id FROM songs WHERE lufs IS NULL AND missing=0 ORDER BY added_at")]


def measure_songs(ctx: JobContext, ids: list[int]) -> dict:
    db = get_db()
    done = failed = 0
    for i, sid in enumerate(ids):
        ctx.check()
        r = db.song(sid)
        if r is None or r["lufs"] is not None:
            continue
        ctx.progress(i / max(1, len(ids)), Path(r["path"]).name)
        try:
            res = measure(r["path"])
        except Exception as e:
            log.info("couldn't measure %s: %s", r["path"], e)
            res = None
        if res is None:
            db.update_song(sid, lufs=-70.0, peak=0.0)      # silence or unreadable: played as it is
            failed += 1
        else:
            db.update_song(sid, lufs=res[0], peak=res[1])
            done += 1
    return {"done": done, "failed": failed}
