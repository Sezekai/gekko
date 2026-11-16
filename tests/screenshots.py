"""Screenshots of every screen at 1100, 1440 and 1920 wide, with a generated library (no internet needed).

    python tests/screenshots.py <out folder>

Uses the real window (QT_QPA_PLATFORM=windows on CI, offscreen elsewhere) with GEKKO_ROOT / GEKKO_DATA pointed at
a temporary folder, so nothing real is touched."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

TMP = Path(tempfile.mkdtemp(prefix="gekko-shots-"))
os.environ["GEKKO_ROOT"] = str(TMP / "root")
os.environ["GEKKO_DATA"] = str(TMP / "data")
os.environ["GEKKO_NO_PLAYBACK"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["GEKKO_NO_SAFE_MODE"] = "1"

LYRICS = """[00:00.50]Under the harbour lights
[00:01.60]we counted every boat
[00:02.70]the night bus hummed a song
[00:03.80]that no one ever wrote
[00:04.90]and every glassy wave
[00:06.00]was carrying us home
[00:07.10]under the harbour lights
[00:08.20]we were never alone"""


def main(out: str) -> int:
    from make_library import make

    outd = Path(out)
    outd.mkdir(parents=True, exist_ok=True)
    lib_root = make(TMP / "lib")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from gekko.ui import theme
    theme.apply(app)
    from gekko.core.db import get_db
    from gekko.jobs import scan as scanjob
    from gekko.jobs.context import JobContext

    db = get_db()
    db.add_folder(str(lib_root / "Music"))
    db.add_folder(str(lib_root / "Downloads"))
    db.exclude(str(lib_root / "Downloads" / "random songs" / "voice notes"))
    res = scanjob.scan(JobContext())
    print("scan:", scanjob.describe(res))
    # what lookups would have found, so every screen has something to show
    rows = db.songs()
    import random
    rnd = random.Random(3)
    for r in rows:
        db.update_song(r["id"], rating=rnd.choice([0, 0, 6, 7, 8, 9, 10]), plays=rnd.choice([0, 0, 1, 3, 8, 15]),
                       favourite=1 if rnd.random() > 0.7 else 0, info_status="done", lufs=-14.0, peak=-2.0)
    first = db.one("SELECT id FROM songs WHERE title='Glass Harbour'")
    db.update_song(first["id"], lyrics_synced=LYRICS, lyrics_plain=LYRICS)
    hoshi = db.one("SELECT id FROM songs WHERE title='Hoshi'")
    if hoshi:
        db.update_song(hoshi["id"], album="Starlight", genre="J-Pop", year="2023", file_synced=0, id_status="found",
                       id_source="Shazam")
    from gekko.core.settings import get_settings
    get_settings().set("tidy.sort_mode", "all")
    for r in db.q("SELECT id FROM songs WHERE title IS NULL AND path LIKE '%track01%'"):
        db.update_song(r["id"], id_status="not_found")
    db.save_artist("Aurora Lane", status="done", kind="Group", country="GB", years="2014–",
                   bio="Aurora Lane is a synth-pop band formed in 2014. Their songs mix glassy synthesisers with "
                       "night-time stories about trains, harbours and radio signals. Their debut album Northern "
                       "Static was released in 2019, followed by Paper Comets in 2022.", bio_source="Wikipedia",
                   bio_url="https://en.wikipedia.org/wiki/Example")
    # some listening history over the last few weeks, with repeats in a row
    now = time.time()
    hist_rows = db.songs()
    t = now - 21 * 86400
    k = 0
    while t < now - 600:
        r = hist_rows[k % len(hist_rows)] if rnd.random() > 0.25 else hist_rows[(k - 1) % len(hist_rows)]
        counted = rnd.random() > 0.15
        dur = r["duration"] or 200
        db.add_listen(r["id"], t, dur if counted else 12.0, dur, rnd.choice(["Album: Northern Static", "All songs",
                      "Playlist: Late night drive", "History"]), r["title"], r["artist"])
        if counted:
            db.x("UPDATE listens SET counted=1 WHERE id=(SELECT MAX(id) FROM listens)")
        k += 1
        t += rnd.choice([240, 300, 3600, 5 * 3600, 20 * 3600])
    pid = db.add_playlist("Late night drive", "#9b86ff")
    db.add_to_playlist(pid, [r["id"] for r in db.songs()][:9])
    db.add_playlist("Short favourites", "#2fe0c0", {"filter": {"fav": True, "len_max": 600}, "order": "shortest"})
    from gekko.ui.main_window import MainWindow
    MainWindow._start_scan = lambda self: None
    w = MainWindow()
    w.identify.stop()
    w.show()
    pb = w.pb
    from gekko.ui.library import get_library
    get_library().reload()
    rows = sorted(get_library().rows, key=lambda r: (r["artist"] or "", r["album"] or "", r["track"] or 0))
    pb.play_rows(rows, first["id"], "in_order", "Album: Northern Static")
    pb.seek(3.0)
    pb.pause()

    def settle(n: float = 0.6):
        end = time.time() + n
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    def shot(name: str, width: int):
        h = {1100: 680, 1440: 900, 1920: 1080}[width]
        w.resize(width, h)
        settle(0.5)
        w.grab().save(str(outd / f"{name}-{width}.png"))

    ok = True
    try:
        for width in (1100, 1440, 1920):
            w.go("history")
            w.history_page.view.set_value("list")
            w.history_page._view("list")
            shot("history", width)
            w.history_page.view.set_value("grid")
            w.history_page._view("grid")
            shot("history-grid", width)
            w.history_page.view.set_value("list")
            w.history_page._view("list")
            w.history_page.range.set_preset("custom")
            shot("history-dates", width)
            w.history_page.range.set_preset("all")
            for key in ("songs", "albums", "artists", "genres", "playlists", "folders", "tidy", "look", "settings"):
                w.go(key)
                settle(0.3)
                shot(key, width)
            w.go("tidy")
            tp = w.pages["tidy"]
            tp.recompute()
            for _ in range(60):
                settle(0.1)
                if tp.plan and not tp._busy:
                    break
            if tp.table.rowCount():
                tp.table.selectRow(0)
                settle(0.3)
            shot("tidy-detail", width)
            tp.tabs.set_value("dupes")
            tp._tab("dupes")
            shot("tidy-dupes", width)
            tp.tabs.set_value("ready")
            tp._tab("ready")
            w.goto("album", get_library().albums()[0]["key"])
            shot("album", width)
            w.goto("artist", "Aurora Lane")
            shot("artist", width)
            w.artists_page.back()
            w.go("songs")
            w.songs_page.b.view.set_value("grid")
            w.songs_page.b._view("grid", save=False)
            shot("songs-grid", width)
            w.songs_page.b._view("list", save=False)
            w.songs_page.b.view.set_value("list")
            w.show_full(True)
            shot("fullplayer", width)
            w.show_full(False)
            w.toggle_keys()
            shot("keys", width)
            w.toggle_keys()
            w.toggle_log(True, "moves")
            shot("log", width)
            w.toggle_log(False)
        w.toggle_mini()
        settle(0.5)
        w.grab().save(str(outd / "mini.png"))
        w.toggle_mini()
        settle(0.3)
    except Exception:
        import traceback
        traceback.print_exc()
        ok = False
    w.close()
    print("screenshots in", outd)
    return 0 if ok else 1


if __name__ == "__main__":
    code = main(sys.argv[1] if len(sys.argv) > 1 else "shots")
    os._exit(code)
