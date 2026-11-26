"""Library, scanning, skips, Tidy up (rename, tags, sort, undo), filters, play orders and lyrics."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from make_library import make, tone


@pytest.fixture()
def library(fresh):
    root = make(fresh / "lib")
    from gekko.core.db import get_db
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    db = get_db()
    db.add_folder(str(root / "Music"))
    db.add_folder(str(root / "Downloads"))
    res = scan.scan(JobContext())
    return root, db, res


def test_scan_adds_every_song(library):
    root, db, res = library
    assert res["added"] == 26
    hanabi = db.one("SELECT * FROM songs WHERE title='Hanabi'")
    assert hanabi["artist"] == "Mika Sora" and hanabi["album"] == "Hanabi" and hanabi["year"] == "2021"
    assert hanabi["has_art"] == 2 and hanabi["folder_art"].endswith("folder.jpg")
    assert hanabi["id_status"] == "tagged"
    unknown = db.one("SELECT * FROM songs WHERE path LIKE '%track01%'")
    assert unknown["title"] is None and unknown["id_status"] == "pending"


def test_excluded_folder_and_removed_songs_never_come_back(library):
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    root, db, _ = library
    n = db.exclude(str(root / "Downloads" / "random songs" / "voice notes"))
    assert n == 1
    kaze = db.one("SELECT id FROM songs WHERE title='Kaze'")
    db.remove_songs([kaze["id"]])
    res = scan.scan(JobContext())
    assert res["added"] == 0
    assert db.one("SELECT 1 FROM songs WHERE title='Kaze'") is None
    assert db.one("SELECT 1 FROM songs WHERE path LIKE '%memo.mp3'") is None
    assert (root / "Music" / "Mika Sora" / "Hanabi" / "03 Kaze.mp3").exists()      # the file itself stays
    db.unremove(None)
    db.unexclude(str(root / "Downloads" / "random songs" / "voice notes"))
    res = scan.scan(JobContext())
    assert res["added"] == 2


def test_new_songs_are_found_and_missing_ones_marked(library):
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    root, db, _ = library
    tone(root / "Music" / "New Artist" / "fresh.mp3", 5, 300, {"title": "Fresh", "artist": "New Artist"})
    (root / "Music" / "Mika Sora" / "Hanabi" / "01 Hanabi.mp3").unlink()
    res = scan.scan(JobContext())
    assert res["added"] == 1 and res["missing"] == 1
    assert db.one("SELECT missing FROM songs WHERE title='Hanabi'")["missing"] == 1


def test_a_file_moved_outside_gekko_keeps_its_ratings(library):
    from gekko.jobs import scan
    from gekko.jobs.context import JobContext
    root, db, _ = library
    r = db.one("SELECT * FROM songs WHERE title='Low Tide'")
    db.update_song(r["id"], rating=9, plays=4)
    dst = root / "Music" / "moved" / "Low Tide.mp3"
    dst.parent.mkdir()
    shutil.move(r["path"], dst)
    res = scan.scan(JobContext())
    assert res["moved"] == 1 and res["added"] == 0
    r2 = db.song(r["id"])
    assert Path(r2["path"]) == dst and r2["rating"] == 9 and r2["plays"] == 4


def test_tidy_renames_tags_sorts_and_undoes(library):
    from gekko.core.settings import get_settings
    from gekko.jobs import tidy
    from gekko.jobs.context import JobContext
    from gekko.media import tags
    root, db, _ = library
    get_settings().set("library.sort_root", str(root / "Sorted"))
    r = db.one("SELECT * FROM songs WHERE path LIKE '%track01%'")
    tidy.edit_info([r["id"]], {"title": "Glass Rain", "artist": "Aurora Lane", "album": "Northern Static",
                               "genre": "Synthpop", "year": "2019"})
    plan = tidy.plans([r["id"]])
    assert len(plan) == 1
    p = plan[0]
    assert Path(p["dst"]) == root / "Sorted" / "Aurora Lane" / "Northern Static" / "Aurora Lane - Glass Rain.mp3"
    assert p["tags"]["title"] == "Glass Rain"
    res = tidy.apply(JobContext(), plan)
    assert res["done"] == 1, res
    new = Path(p["dst"])
    assert new.exists() and not Path(r["path"]).exists()
    t = tags.read(new)
    assert t["title"] == "Glass Rain" and t["artist"] == "Aurora Lane" and t["year"] == "2019"
    row = db.song(r["id"])
    assert row["path"] == str(new) and row["file_synced"] == 1
    assert str(root / "Sorted") in db.folders()
    # undo puts it back with no tags, as it was
    m = db.last_move()
    msg = tidy.undo_move(m["id"])
    assert msg.startswith("Put "), msg
    assert Path(r["path"]).exists() and not new.exists()
    assert tags.read(r["path"])["title"] is None
    assert db.song(r["id"])["file_synced"] == 0


def _loose(db):
    return db.one("SELECT * FROM songs WHERE path LIKE '%random songs%' AND title IS NOT NULL AND artist IS NOT NULL "
                  "AND path NOT LIKE '%voice notes%' ORDER BY id")


def test_tidy_never_overwrites(library):
    from gekko.core.settings import get_settings
    from gekko.jobs import tidy
    root, db, _ = library
    get_settings().set("tidy.sort_mode", "off")
    r = _loose(db)
    tidy.edit_info([r["id"]], {"title": "Night Drive"})
    taken = Path(r["path"]).with_name(f"{r['artist']} - Night Drive.mp3")
    taken.write_bytes(b"someone else's file")
    p = tidy.plans([r["id"]])[0]
    assert Path(p["dst"]).name == f"{r['artist']} - Night Drive (2).mp3"
    # the same name in another folder is fine
    p2 = tidy.plan_for(db.song(r["id"]), tidy.options(), set(), set(),
                       {"folder": str(root / "elsewhere")})
    assert Path(p2["dst"]) == root / "elsewhere" / f"{r['artist']} - Night Drive.mp3"
    # two songs planned into the same folder under the same name don't land on each other
    taken_keys = {tidy.key(str(root / "elsewhere" / f"{r['artist']} - Night Drive.mp3"))}
    p3 = tidy.plan_for(db.song(r["id"]), tidy.options(), taken_keys, set(), {"folder": str(root / "elsewhere")})
    assert Path(p3["dst"]).name.endswith("(2).mp3")


def test_tidy_leaves_album_folders_track_numbers_and_punctuation(library):
    from gekko.core.settings import get_settings
    from gekko.jobs import tidy
    root, db, _ = library
    get_settings().set("library.sort_root", str(root / "Sorted"))
    get_settings().set("tidy.sort_mode", "loose")
    album_song = db.one("SELECT * FROM songs WHERE title='Night Bus'")
    plans = {p["id"]: p for p in tidy.plans()}
    p = plans.get(album_song["id"])
    if p is not None:                                  # only tags may change: no move, no rename
        assert not p["move"] and not p["rename"]
        assert "already in an album folder" in p["notes"] and "keeps its track number" in p["notes"]
    assert tidy.has_track_number("02 Night Bus") and tidy.has_track_number("1-03 - Song")
    assert not tidy.has_track_number("5 Centimeters Per Second") and not tidy.has_track_number("10cc - Song")
    # a name that only differs by punctuation stays
    r = _loose(db)
    src = Path(r["path"])
    odd = src.with_name(f'{r["artist"]} - {r["title"]}!!.mp3')
    src.rename(odd)
    db.x("UPDATE songs SET path=? WHERE id=?", (str(odd), r["id"]))
    get_settings().set("tidy.sort_mode", "off")
    p = tidy.plan_for(db.song(r["id"]), tidy.options(), set(), set())
    assert p is None or not p["rename"]
    assert tidy.nice_name('He said "hi": ok?') == "He said 'hi' - ok"
    # every song sorted when asked
    get_settings().set("tidy.sort_mode", "all")
    p = tidy.plan_for(db.song(album_song["id"]), tidy.options(), set(), set())
    assert p["move"]


def test_tidy_overrides(library):
    from gekko.core.settings import get_settings
    from gekko.jobs import tidy
    root, db, _ = library
    get_settings().set("tidy.sort_mode", "off")
    r = _loose(db)
    tidy.edit_info([r["id"]], {"title": "Night Drive", "genre": "Jazz"})
    r = db.song(r["id"])
    p = tidy.plan_for(r, tidy.options(), set(), set(), {"name": "", "skip": ["genre"], "tags": {"year": "1999"}})
    assert not p["rename"] and "genre" not in p["tags"] and p["tags"]["year"] == "1999" and p["edited"]
    p = tidy.plan_for(r, tidy.options(), set(), set(), {"name": "My own name"})
    assert Path(p["dst"]).name == "My own name.mp3"


def test_duplicates(library):
    import numpy as np
    from gekko.jobs import duplicates as dup
    from gekko.jobs.context import JobContext
    root, db, _ = library
    assert dup.name_key("03 - Night Bus (2).mp3") == dup.name_key("Night Bus - Copy.flac") == "nightbus"
    assert dup.name_key("1979.mp3") == "1979"
    rows = [dict(r) for r in db.q("SELECT * FROM songs WHERE missing=0")]
    a, b = rows[0], rows[1]
    copy = Path(a["path"]).with_name(Path(a["path"]).stem + " - Copy" + Path(a["path"]).suffix)
    shutil.copy(a["path"], copy)
    rows2 = rows + [dict(a, id=9999, path=str(copy))]
    assert any(set(g) == {a["id"], 9999} for g in dup.by_name(rows2))
    assert any(set(g) >= {a["id"], 9999} for g in dup.by_tags(rows2)) or not a["title"]
    # by sound: made-up fingerprints, two the same with a small shift, one different
    rng = np.random.default_rng(1)
    base = rng.integers(0, 2**32, 600, dtype=np.uint64).astype(np.uint32)
    fake = {a["path"]: base, b["path"]: np.roll(base, 3), str(copy): rng.integers(0, 2**32, 600, dtype=np.uint64).astype(np.uint32)}
    rs = [dict(a, duration=100), dict(b, duration=101), dict(a, id=9999, path=str(copy), duration=100)]
    res = dup.by_sound(JobContext(), rs, fp_fn=lambda p: fake[p])
    assert res["groups"] == [[a["id"], b["id"]]] or res["groups"] == [[b["id"], a["id"]]]
    # fingerprints are kept for next time
    res2 = dup.by_sound(JobContext(), rs, fp_fn=lambda p: None)
    assert res2["new"] == 0 and len(res2["groups"]) == 1
    keep = dup.best_of([dict(a, bitrate=128000, codec="mp3"), dict(b, bitrate=320000, codec="mp3")])
    assert keep == b["id"]


def test_cover_goes_into_files_without_one(library):
    from gekko.core.settings import get_settings
    from gekko.jobs import tidy
    from gekko.jobs.context import JobContext
    from gekko.media import tags
    root, db, _ = library
    get_settings().set("tidy.sort_mode", "off")
    get_settings().set("tidy.rename", False)
    r = db.one("SELECT * FROM songs WHERE title='Hanabi'")
    plan = tidy.plans([r["id"]])
    assert plan and plan[0]["cover"]
    tidy.apply(JobContext(), plan)
    assert tags.read(r["path"])["has_pic"]
    assert tags.picture(r["path"])[:3] == b"\xff\xd8\xff"
    assert db.song(r["id"])["has_art"] == 1


def test_tags_round_trip_every_format(fresh):
    from gekko.media import tags
    for fmt in ("mp3", "flac", "m4a", "ogg"):
        p = fresh / f"t.{fmt}"
        tone(p, 2, 440, fmt=fmt)
        assert tags.write(p, {"title": "Ü title", "artist": "A & B", "album": "X", "track": 3, "year": "1999",
                              "genre": "Rock"})
        t = tags.read(p)
        assert (t["title"], t["artist"], t["album"], t["track"], t["year"]) == ("Ü title", "A & B", "X", 3, "1999"), fmt


def test_filters_with_opposites():
    from gekko.ui import filters
    r = {"name": "Kaze", "artist": "Mika Sora", "album": "Hanabi", "album_artist": None, "genre": "J-Pop",
         "year": "2021", "path": "x/kaze.mp3", "favourite": 1, "rating": 7, "plays": 0, "has_lyrics": 1,
         "title": "Kaze", "file_synced": 1, "missing": 0, "genres": ["J-Pop"], "duration": 200, "year_n": 2021,
         "added_at": 0}
    assert filters.match(filters.new(fav=True), r)
    assert not filters.match(filters.new(fav=False), r)
    assert filters.match(filters.new(rating_min=7), r) and not filters.match(filters.new(rating_min=8), r)
    assert filters.match(filters.new(played=False), r)
    assert filters.match(filters.new(genres=["j-pop"]), r)
    assert not filters.match(filters.new(genres=["J-Pop"], genres_not=True), r)
    assert filters.match(filters.new(len_min=180, len_max=240), r)
    assert not filters.match(filters.new(year_max=2020), r)
    assert filters.match(filters.new(text="mika kaze"), r)
    assert filters.describe(filters.new(fav=False))[0][0] == "Not favourites"


def test_play_orders():
    from gekko.ui.playback import order_ids
    rows = [{"id": 1, "duration": 300, "year": "2001", "rating": 2, "plays": 5, "added_at": 1},
            {"id": 2, "duration": 100, "year": "2020", "rating": 10, "plays": 0, "added_at": 3},
            {"id": 3, "duration": 200, "year": None, "rating": 6, "plays": 9, "added_at": 2}]
    assert order_ids(rows, "in_order") == [1, 2, 3]
    assert order_ids(rows, "reverse") == [3, 2, 1]
    assert order_ids(rows, "shortest") == [2, 3, 1]
    assert order_ids(rows, "longest") == [1, 3, 2]
    assert order_ids(rows, "newest")[0] == 2
    assert order_ids(rows, "oldest")[:2] == [1, 2]
    assert order_ids(rows, "rating") == [2, 3, 1]
    assert sorted(order_ids(rows, "shuffle")) == [1, 2, 3]


def test_lyrics_and_names():
    from gekko.core.db import main_artist
    from gekko.jobs.identify import _guess_from_name
    from gekko.ui.lyrics import parse_lrc
    lrc = parse_lrc("[00:01.50]one\n[01:02.00][01:30.25]two\nno stamp")
    assert lrc == [(1.5, "one"), (62.0, "two"), (90.25, "two")]
    assert main_artist("Mika Sora feat. Lumen") == "Mika Sora"
    assert main_artist("A & B") == "A"
    assert _guess_from_name("03 - Aurora Lane - Night Bus (Official Video)") == {"artist": "Aurora Lane",
                                                                                 "title": "Night Bus"}


def test_level_gain():
    from gekko.jobs import level
    assert level.gain_for(-20.0, -6.0) == pytest.approx(5.0)
    assert level.gain_for(-20.0, -2.0) == pytest.approx(1.0)      # never past -1 dBTP
    assert level.gain_for(-8.0, 0.0) == pytest.approx(-6.0)
    assert level.gain_for(None, None) == 0.0


def test_measure_loudness(fresh):
    from gekko.jobs import level
    p = fresh / "x.flac"
    tone(p, 4, 440, fmt="flac")
    lufs, peak = level.measure(p)
    assert -40 < lufs < 0 and peak < 0


def test_odd_mp3_still_gets_its_tags_and_length(fresh, monkeypatch):
    """MP3s the tag reader can't follow ("can't sync to MPEG frame") keep their ID3 tags; ffprobe gives the length."""
    import mutagen
    from mutagen.mp3 import HeaderNotFoundError
    from gekko.media import tags
    p = fresh / "odd.mp3"
    tone(p, 4, 440, {"title": "Liberi Fatali", "artist": "Nobuo Uematsu", "album": "Distant Worlds"})

    def broken(*a, **k):
        raise HeaderNotFoundError("can't sync to MPEG frame")

    monkeypatch.setattr(mutagen, "File", broken)
    t = tags.read(p)
    assert (t["title"], t["artist"], t["album"]) == ("Liberi Fatali", "Nobuo Uematsu", "Distant Worlds")
    assert 3.5 < t["duration"] < 5


def test_every_colour_name_used_exists():
    """C["name"] with a name the theme doesn't have crashes a screen when it's drawn (1.2.0's 'accent')."""
    import re
    from gekko.ui.theme import C
    root = Path(__file__).resolve().parents[1] / "gekko"
    bad = []
    for f in root.rglob("*.py"):
        for m in re.finditer(r"""\bC\[["']([a-z_0-9]+)["']\]""", f.read_text("utf-8")):
            if m.group(1) not in C:
                bad.append(f"{f.name}: {m.group(1)}")
    assert not bad, bad
