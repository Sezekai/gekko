"""Real playback through the bundled libmpv with no sound card (GEKKO_MPV_AO=null): three short songs play one
after another without gaps, the controller follows along, repeat-one and seeking work. Exit code 0 = fine."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(tempfile.mkdtemp(prefix="gekko-mpv-"))
os.environ["GEKKO_DATA"] = str(TMP / "data")
os.environ["GEKKO_ROOT"] = str(TMP / "root")
os.environ.setdefault("GEKKO_MPV_AO", "null")


def main() -> int:
    from make_library import tone
    for i in range(3):
        tone(TMP / "music" / f"s{i}.mp3", 2.0, 300 + 100 * i, {"title": f"S{i}", "artist": "T"})
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from gekko.core.db import get_db
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    db = get_db()
    db.add_folder(str(TMP / "music"))
    scan.scan(JobContext())
    from gekko.ui.playback import get_playback
    pb = get_playback()
    print("backend:", pb.engine.backend)
    if pb.engine.backend != "mpv":
        return 1
    seen = []
    pb.currentChanged.connect(lambda sid: seen.append(sid))
    ids = [r["id"] for r in db.q("SELECT id FROM songs ORDER BY path")]

    def run(s):
        end = time.time() + s
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    pb.play_ids(ids)
    run(8)
    print("played in order:", seen, "plays:", [db.song(i)["plays"] for i in ids])
    ok = seen[:3] == ids and all(db.song(i)["plays"] == 1 for i in ids)
    print("in order:", ok)
    pb.play_ids(ids)
    run(0.5)
    if os.environ.get("GEKKO_DEBUG"):
        m = pb.engine._mpv
        print("after replay: pause", m.pause, "idle", m.idle_active, "pos", m.time_pos, "path", m.path, m.playlist,
              "playing", pb.is_playing())
    pb.seek(1.0)
    run(0.3)
    print("ids:", ids, "position after seeking to 1.0:", round(pb.position(), 2))
    ok = ok and pb.position() >= 1.0
    pb.cycle_repeat()
    pb.cycle_repeat()        # repeat this song
    if os.environ.get("GEKKO_DEBUG"):
        e = pb.engine
        for name in ("advanced", "finished", "playingChanged"):
            getattr(e, name).connect(lambda *a, n=name: print(n, a, e._next, e._ended_sent))
        e._b.eof.connect(lambda: print("eof", e._next, e._ended_sent))
        e._b.pos.connect(lambda v: print("pos", round(v, 2)) if int(v * 10) % 5 == 0 else None)
    before = len(seen)
    run(5)
    print("repeat one: index", pb.index, "starts", len(seen) - before, "repeat", pb.repeat)
    ok = ok and pb.index == 0 and len(seen) > before
    pb.shutdown()
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    code = main()
    os._exit(code)
