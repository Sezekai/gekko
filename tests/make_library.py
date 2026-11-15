"""Make a small music library of generated songs (tones) for tests and screenshots: album folders with tags and
cover pictures, loose songs with only file names, and songs with no info at all."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ALBUMS = [
    ("Aurora Lane", "Northern Static", "2019", "Synthpop", ["Glass Harbour", "Night Bus", "Satellite Hearts", "Low Tide"]),
    ("Aurora Lane", "Paper Comets", "2022", "Synthpop", ["Afterglow", "Comet Season", "Signal Fire"]),
    ("The Velvet Arcade", "Neon Weather", "2016", "Indie Rock", ["Weathervane", "Coin Op", "Last Arcade", "Static Bloom", "Overtime"]),
    ("Mika Sora", "Hanabi", "2021", "J-Pop", ["Hanabi", "Natsu no Owari", "Kaze"]),
    ("DJ Kestrel", "Midnight Circuit", "2020", "Electronic", ["Circuit One", "Pulse Width", "Afterhours"]),
]
LOOSE = [("Aurora Lane", "Echo Chamber"), ("Lumen", "Driftwood"), ("Mika Sora feat. Lumen", "Hoshi"),
         ("Old Lantern", "River Song")]
UNKNOWN = ["track01", "voice memo 3", "song (1)"]
COLOURS = ["#2fd6b4", "#9b86ff", "#ff6fb1", "#ffbf5a", "#5aa8ff"]


def tone(out: Path, seconds: float, freq: float, meta: dict | None = None, fmt: str = "mp3",
         cover: Path | None = None) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
            f"sine=frequency={freq}:duration={seconds},volume=0.4"]
    if cover is not None and fmt in ("mp3",):
        args += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    for k, v in (meta or {}).items():
        args += ["-metadata", f"{k}={v}"]
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], "flac": ["-c:a", "flac"], "m4a": ["-c:a", "aac", "-b:a", "128k"],
             "ogg": ["-c:a", "libvorbis"]}[fmt]
    args += codec + [str(out)]
    subprocess.run(args, check=True)


def cover_jpg(out: Path, colour: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s=300x300",
                    "-vf", "drawbox=x=60:y=60:w=180:h=180:color=white@0.35:t=fill", "-frames:v", "1", str(out)],
                   check=True)


def make(root: Path, short: bool = True) -> Path:
    root = Path(root)
    music = root / "Music"
    sec = 6.0 if short else 30.0
    for i, (artist, album, year, genre, songs) in enumerate(ALBUMS):
        folder = music / artist / album
        cov = folder / "folder.jpg"
        cover_jpg(cov, COLOURS[i % len(COLOURS)])
        fmt = ["mp3", "flac", "m4a", "mp3", "ogg"][i]
        for n, t in enumerate(songs, 1):
            tone(folder / f"{n:02d} {t}.{fmt}", sec + n, 220 + 40 * n + 15 * i,
                 {"title": t, "artist": artist, "album": album, "album_artist": artist, "track": n, "date": year,
                  "genre": genre}, fmt=fmt)
    loose = root / "Downloads" / "random songs"
    for j, (a, t) in enumerate(LOOSE):
        tone(loose / f"{a} - {t}.mp3", sec + j, 330 + 30 * j, {"title": t, "artist": a} if j % 2 == 0 else None)
    for j, n in enumerate(UNKNOWN):
        tone(loose / f"{n}.mp3", sec + 2 + j, 500 + 20 * j)
    skip = root / "Downloads" / "random songs" / "voice notes"
    tone(skip / "memo.mp3", 3.0, 600)
    return root


if __name__ == "__main__":
    print(make(Path(sys.argv[1] if len(sys.argv) > 1 else "sample")))
